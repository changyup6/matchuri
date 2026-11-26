# -*- coding: utf-8 -*-
"""관측 — 요청 한 줄 JSON 로그 · 요청 id · 도메인 이벤트 · 프로세스 지표.

**왜 JSON 한 줄인가.** 로그를 읽는 것은 사람이 아니라 `docker logs | jq` 이고, 사고가
났을 때 알아야 하는 것은 '몇 시에 누가 무엇을 불러서 몇 ms 에 몇 번으로 끝났나' 다.
사람이 읽기 좋은 문장은 그 질문에 답하려면 정규식을 요구하고, 정규식은 필드가 하나
늘 때마다 깨진다. 그래서 필드 이름을 계약으로 두고 값만 바뀌게 한다.

    {"ts":"2026-09-10T15:04:05+09:00","level":"INFO","event":"http",
     "request_id":"9f2c…","method":"GET","path":"/cards","route":"/cards","status":200,
     "ms":12.4,"client_ip":"1.2.3.4","user_id":"u_3f2a…","ua":"okhttp/4.12","bytes_out":18432}

**요청 id 는 밖에서 온 것을 먼저 믿는다.** nginx·앱·클라이언트가 같은 id 를 쓰면 한 사고를
세 로그에서 이어 볼 수 있다. 다만 남이 준 문자열이므로 길이와 문자를 자른다(로그 주입 방지) —
`X-Request-ID` 가 없거나 이상하면 uuid4 를 만든다. 응답 헤더로 되돌려 주므로 프론트가
버그 리포트에 그 값을 적을 수 있다.

**컨텍스트 변수 하나로 요청 전체를 묶는다.** 핸들러가 남기는 도메인 이벤트
(`user.create`·`pref.record`·`cards.serve` …)에도 같은 `request_id` 가 붙는다. 스레드풀에서
도는 동기 핸들러도 `contextvars` 를 물려받으므로 따로 넘길 필요가 없다.

**지표는 프로세스 안에서만 센다.** 의존성을 하나도 더하지 않는 것이 조건이었고(이미지가
커지면 배포가 느려진다), 재시작하면 0 이 된다 — 그래도 Prometheus 는 `counter` 의 리셋을
알아서 처리한다. 히스토그램 버킷은 스와이프 API 의 현실(중앙값 10ms 대)에 맞춰 5ms 부터
잡았다.

로그 수준은 `MATCHURI_LOG_LEVEL`(기본 INFO). `/healthz`·`/readyz`·`/metrics` 는 DEBUG 로
남긴다 — 헬스체크가 1초에 한 번 오면 나머지 로그가 그 안에 묻힌다.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

#: 로거 이름 하나로 모은다 — 앱이 남기는 모든 줄이 같은 형식이어야 `jq` 가 통한다.
LOGGER_NAME = "matchuri"
LEVEL_ENV = "MATCHURI_LOG_LEVEL"
CORS_ENV = "MATCHURI_CORS_ORIGINS"

#: 이보다 느린 요청은 경고 한 줄을 더 남긴다(`event: "http.slow"`).
SLOW_MS = 500.0

#: 헬스체크·스크레이프는 DEBUG. 초당 한 번 오는 줄이 사고 로그를 덮지 않게.
QUIET_PATHS = {"/healthz", "/readyz", "/metrics"}

#: 남이 준 요청 id 에 허용하는 것 — 길이와 문자를 자른다(로그 주입·거대 헤더 방지).
REQUEST_ID_MAX = 64
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
REQUEST_ID_HEADER = "x-request-id"

#: 히스토그램 버킷(초). `/cards` 가 10ms 대라 아래쪽을 촘촘히 둔다.
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "matchuri_request_id", default=None)

_lock = threading.Lock()
_ready = False


# ────────────────────────────────────────────────────────── 로깅
def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class JsonFormatter(logging.Formatter):
    """레코드 하나 -> JSON 한 줄. `log()` 가 실은 필드를 그대로 펼친다."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict = {"ts": _now(), "level": record.levelname}
        fields = getattr(record, "matchuri", None)
        if isinstance(fields, dict):
            payload.update(fields)
        else:                                   # 남의 로거(uvicorn 등)가 흘려보낸 줄
            payload["event"] = "log"
            payload["logger"] = record.name
            payload["msg"] = record.getMessage()
        payload.setdefault("request_id", _request_id.get())
        if record.exc_info:
            payload["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup(level: str | None = None) -> logging.Logger:
    """stdout 에 JSON 한 줄씩. 두 번 불러도 핸들러가 겹치지 않는다.

    uvicorn 의 access 로그는 WARNING 으로 낮춘다 — 같은 요청을 두 줄로 남기면 지표와
    로그가 어긋나 보이고, 우리 줄에는 `route`·`user_id`·`request_id` 가 들어 있다.
    """
    global _ready
    logger = logging.getLogger(LOGGER_NAME)
    want = (level or os.environ.get(LEVEL_ENV) or "INFO").strip().upper()
    logger.setLevel(getattr(logging, want, logging.INFO))
    with _lock:
        if not _ready:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)
            logger.propagate = False            # 루트로 새어 나가 두 번 찍히지 않게
            logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
            _ready = True
    return logger


def log(event: str, level: int = logging.INFO, **fields) -> None:
    """도메인 이벤트 한 줄. `event` 가 곧 그 줄의 종류다(`jq 'select(.event=="…")'`)."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.isEnabledFor(level):
        return
    logger.log(level, event, extra={"matchuri": {"event": event, **fields}})


def log_exc(event: str, exc: BaseException, **fields) -> None:
    """예외 한 건 — traceback 은 로그에만 남고 클라이언트에는 가지 않는다."""
    logging.getLogger(LOGGER_NAME).error(
        event, exc_info=(type(exc), exc, exc.__traceback__),
        extra={"matchuri": {"event": event, "error": type(exc).__name__,
                            "detail": str(exc)[:500], **fields}})


# ────────────────────────────────────────────────────────── 요청 id
def clean_request_id(raw: str | None) -> str:
    """밖에서 온 id 를 검사하고, 못 쓰면 새로 만든다."""
    if raw:
        raw = raw.strip()
        if len(raw) <= REQUEST_ID_MAX and REQUEST_ID_RE.match(raw):
            return raw
    return uuid.uuid4().hex


def request_id() -> str | None:
    """지금 처리 중인 요청의 id(요청 밖에서는 None)."""
    return _request_id.get()


def bind_request_id(value: str):
    return _request_id.set(value)


def unbind_request_id(token) -> None:
    _request_id.reset(token)


# ────────────────────────────────────────────────────────── 지표
#: 지표는 전부 이 프로세스 안에서 센다. 대시보드가 묻는 질문 순서대로 이름을 정했다 —
#: '얼마나 오나(requests)' · '얼마나 실패하나(errors)' · '얼마나 걸리나(seconds)' ·
#: '무엇이 일어났나(preferences·cards·bookmarks…)' · '지금 상태가 어떤가(gauge)'.
_requests: dict[tuple[str, str, int], int] = {}
_hist: dict[str, list] = {}                      # route -> [버킷 카운트…, 합, 개수]
_slow: dict[str, int] = {}
_errors: dict[str, int] = {}
_inflight = 0
_prefs: dict[tuple[str, str], int] = {}          # (prefer, 노출한 정책) -> 수
_cards: dict[str, int] = {}                      # 정책 -> 내보낸 카드 수
_cards_requests: dict[tuple[bool, bool], int] = {}   # (필터 걸림, 개인화됨) -> 요청 수
_cards_short = 0                                 # limit 을 못 채운 요청 수
_cards_ranked: dict[str, int] = {}               # 무엇이 순서를 정했나 (tier/distance/none)
_bookmarks: dict[str, int] = {}
_directions: dict[tuple[str, str], int] = {}     # (지도 앱, 이동 수단) -> 길찾기 수
_surveys: dict[tuple[str, str], int] = {}        # (방문 여부, 만족도) -> 설문 응답 수
_taste_views = 0
_filter_sets = 0
_location_sets = 0
_users_created = 0
_restore_seconds = 0.0
_ready = 0.0

#: `/proc/self` 를 읽는 값들. 리눅스가 아니면 조용히 건너뛴다(개발 노트북).
_CLK_TCK = 100.0
try:
    _CLK_TCK = float(os.sysconf("SC_CLK_TCK")) or 100.0
except (AttributeError, ValueError, OSError):     # noqa: S110 — 없으면 기본값
    pass


def record_request(route: str, method: str, status: int, seconds: float) -> None:
    with _lock:
        key = (route, method, status)
        _requests[key] = _requests.get(key, 0) + 1
        row = _hist.get(route)
        if row is None:
            row = [[0] * (len(BUCKETS) + 1), 0.0, 0]
            _hist[route] = row
        for i, edge in enumerate(BUCKETS):
            if seconds <= edge:
                row[0][i] += 1
        row[0][len(BUCKETS)] += 1               # +Inf
        row[1] += seconds
        row[2] += 1
        if status >= 500:
            _errors[route] = _errors.get(route, 0) + 1


def inc_slow(route: str) -> None:
    with _lock:
        _slow[route] = _slow.get(route, 0) + 1


def inflight(delta: int) -> None:
    """지금 처리 중인 요청 수. 큐가 밀리는지는 지연 분포보다 이 값이 먼저 말해 준다."""
    global _inflight
    with _lock:
        _inflight = max(0, _inflight + delta)


def inc_pref(prefer: str, policy: str = "none") -> None:
    """선호 표현 하나. `policy` 는 **그 카드를 보여준 정책**이다(`recsys/policy.py`).

    이 라벨이 팀-드래프트 인터리빙의 온라인 결과다 — 대시보드에서 정책별 like 율이
    `rate(…{prefer="like"}) / rate(…)` 한 줄로 나온다. 노출 기록이 없는 카드
    (찜 목록에서 바로 평가한 경우 등)는 `none` 이고, 그것도 알아야 하는 값이다.
    """
    with _lock:
        key = (prefer, policy)
        _prefs[key] = _prefs.get(key, 0) + 1


def inc_cards(policy: str, n: int = 1) -> None:
    """내보낸 **카드 수**(정책별). 요청 수는 `inc_cards_request` 가 따로 센다."""
    with _lock:
        _cards[policy] = _cards.get(policy, 0) + n


def inc_cards_request(*, filtered: bool, personalized: bool, short: bool) -> None:
    """`GET /cards` **요청** 하나. 필터가 걸렸나·개인화됐나로 나눠 센다.

    `short` 는 `limit` 을 못 채운 요청이다 — '필터가 너무 좁다' 의 신호이고, 제품 결정
    (기본 반경을 넓힐까)이 여기서 나온다.
    """
    global _cards_short
    with _lock:
        key = (filtered, personalized)
        _cards_requests[key] = _cards_requests.get(key, 0) + 1
        if short:
            _cards_short += 1


def inc_cards_ranked(by: str) -> None:
    """`GET /cards` 요청 하나의 **정렬 근거** — 태그 계층 · 거리 · 아무것도 아님.

    필터를 걸어 두면 순서가 왜 그런지가 달라진다. 대시보드에서 '요즘 사람들의 카드 순서는
    무엇이 정하나' 를 보면 다음에 무엇을 고쳐야 하는지가 나온다(`app/ranking.py`).
    """
    with _lock:
        _cards_ranked[by] = _cards_ranked.get(by, 0) + 1


def inc_bookmark(action: str) -> None:
    """찜이 **실제로 바뀐** 횟수만 센다(같은 카드를 다시 담는 멱등 호출은 세지 않는다)."""
    with _lock:
        _bookmarks[action] = _bookmarks.get(action, 0) + 1


def inc_direction(map_app: str, mode: str) -> None:
    """길찾기를 누른 횟수. 같은 카드를 다시 눌러도 센다 — '몇 번 길을 찾았나' 가 지표다."""
    with _lock:
        key = (map_app, mode)
        _directions[key] = _directions.get(key, 0) + 1


def inc_survey(visited: bool, satisfaction: str | None) -> None:
    """설문 응답 수. **새로 기록된 것만** 센다(두 번 답한 것은 세지 않는다).

    `satisfaction` 이 없으면(방문 안 함 등) `none` 으로 라벨한다 — 라벨을 비우면
    Prometheus 에서 시계열이 갈라져 합계가 안 맞는다.
    """
    with _lock:
        key = ("true" if visited else "false", satisfaction or "none")
        _surveys[key] = _surveys.get(key, 0) + 1


def inc_taste_view() -> None:
    global _taste_views
    with _lock:
        _taste_views += 1


def inc_filter_set() -> None:
    global _filter_sets
    with _lock:
        _filter_sets += 1


def inc_location_set() -> None:
    global _location_sets
    with _lock:
        _location_sets += 1


def inc_user_created() -> None:
    global _users_created
    with _lock:
        _users_created += 1


def set_ready(flag: bool) -> None:
    """기동 복원이 끝났나. 배포 직후 이 값이 0 인 구간이 곧 '준비 중' 이다 —
    `/healthz` 는 그 동안에도 200 이므로 살아 있음과 준비됨은 이 게이지로 갈린다."""
    global _ready
    with _lock:
        _ready = 1.0 if flag else 0.0


def set_restore_seconds(seconds: float) -> None:
    """기동 복원에 걸린 시간. 배포 뒤 '왜 늦게 떴나' 를 그래프로 답한다."""
    global _restore_seconds
    with _lock:
        _restore_seconds = float(seconds)


def _label(value: object) -> str:
    """라벨 값 이스케이프 — 경로에 `"` 나 `\\` 가 들어와도 스크레이프가 깨지지 않게."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _proc() -> dict[str, float]:
    """`/proc/self` 에서 읽는 프로세스 지표. 의존성(psutil)을 더하지 않으려고 직접 읽는다.

    리눅스가 아니거나 `/proc` 이 없으면 **빈 dict** 를 준다 — 개발 노트북에서 `/metrics`
    가 500 이 되면 안 된다(지표는 서비스의 부속이지 서비스가 아니다).
    """
    out: dict[str, float] = {}
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    out["resident_bytes"] = float(line.split()[1]) * 1024.0
                    break
        with open("/proc/self/stat", encoding="utf-8") as f:
            raw = f.read()
        # comm 필드에 괄호·공백이 들어갈 수 있어 **마지막 ')' 뒤**부터 자른다.
        fields = raw[raw.rindex(")") + 2:].split()
        out["cpu_seconds_total"] = (float(fields[11]) + float(fields[12])) / _CLK_TCK
        out["threads"] = float(fields[17])
        starttime = float(fields[19]) / _CLK_TCK
        out["open_fds"] = float(len(os.listdir("/proc/self/fd")))
        with open("/proc/stat", encoding="utf-8") as f:
            for line in f:
                if line.startswith("btime "):
                    out["start_time_seconds"] = float(line.split()[1]) + starttime
                    break
    except (OSError, ValueError, IndexError):
        return out
    return out


def _family(out: list[str], name: str, kind: str, help_text: str) -> None:
    out.append(f"# HELP {name} {help_text}")
    out.append(f"# TYPE {name} {kind}")


def render(*, users: int, sessions: int, prefs: int, version: str, policy: str,
           bookmarks: int = 0, artifacts: bool = False) -> str:
    """Prometheus 텍스트 형식. 의존성 없이 손으로 쓴다(형식이 단순해서 그럴 수 있다).

    모든 계열에 `HELP`·`TYPE` 을 붙인다 — 그게 있어야 Grafana 의 지표 탐색기에서 이름만
    보고도 무엇인지 알 수 있고, 형식 검사(`tests/test_observe.py`)도 그것을 잠근다.
    """
    out: list[str] = []
    with _lock:
        requests = dict(_requests)
        hist = {r: (list(v[0]), v[1], v[2]) for r, v in _hist.items()}
        slow_by = dict(_slow)
        errors_by = dict(_errors)
        inflight_now = _inflight
        prefs_by = dict(_prefs)
        cards_by = dict(_cards)
        card_reqs = dict(_cards_requests)
        cards_short = _cards_short
        ranked_by = dict(_cards_ranked)
        bookmarks_by = dict(_bookmarks)
        directions_by = dict(_directions)
        surveys_by = dict(_surveys)
        taste_views, filter_sets = _taste_views, _filter_sets
        location_sets, users_created = _location_sets, _users_created
        restore_seconds = _restore_seconds
        ready_flag = _ready

    _family(out, "matchuri_http_requests_total", "counter", "처리한 HTTP 요청 수")
    for (route, method, status), n in sorted(requests.items(), key=lambda kv: str(kv[0])):
        out.append(f'matchuri_http_requests_total{{route="{_label(route)}",'
                   f'method="{_label(method)}",status="{status}"}} {n}')

    _family(out, "matchuri_http_request_seconds", "histogram",
            "요청 처리 시간(초). 라벨은 경로 템플릿 하나뿐이다(카디널리티)")
    for route, (counts, total, n) in sorted(hist.items()):
        for i, edge in enumerate(BUCKETS):
            out.append(f'matchuri_http_request_seconds_bucket{{route="{_label(route)}",'
                       f'le="{edge}"}} {counts[i]}')
        out.append(f'matchuri_http_request_seconds_bucket{{route="{_label(route)}",'
                   f'le="+Inf"}} {counts[len(BUCKETS)]}')
        out.append(f'matchuri_http_request_seconds_sum{{route="{_label(route)}"}} '
                   f'{total:.6f}')
        out.append(f'matchuri_http_request_seconds_count{{route="{_label(route)}"}} {n}')

    _family(out, "matchuri_http_slow_total", "counter",
            f"{SLOW_MS:.0f}ms 를 넘은 요청 수")
    for route, n in sorted(slow_by.items()):
        out.append(f'matchuri_http_slow_total{{route="{_label(route)}"}} {n}')

    _family(out, "matchuri_errors_total", "counter", "5xx 로 끝난 요청 수")
    for route, n in sorted(errors_by.items()):
        out.append(f'matchuri_errors_total{{route="{_label(route)}"}} {n}')

    _family(out, "matchuri_http_inflight", "gauge", "지금 처리 중인 요청 수")
    out.append(f"matchuri_http_inflight {inflight_now}")

    _family(out, "matchuri_preferences_total", "counter",
            "선호 표현 수. policy 는 그 카드를 **보여준** 정책(인터리빙 결과)")
    for (prefer, name), n in sorted(prefs_by.items()):
        out.append(f'matchuri_preferences_total{{prefer="{_label(prefer)}",'
                   f'policy="{_label(name)}"}} {n}')

    _family(out, "matchuri_cards_served_total", "counter", "내보낸 카드 수 (뽑은 정책별)")
    for name, n in sorted(cards_by.items()):
        out.append(f'matchuri_cards_served_total{{policy="{_label(name)}"}} {n}')

    _family(out, "matchuri_cards_requests_total", "counter",
            "GET /cards 요청 수 (필터 걸림·개인화 여부별)")
    for (filtered, personalized), n in sorted(card_reqs.items()):
        out.append(f'matchuri_cards_requests_total{{filtered="{str(filtered).lower()}",'
                   f'personalized="{str(personalized).lower()}"}} {n}')

    _family(out, "matchuri_cards_short_total", "counter",
            "limit 을 못 채운 GET /cards 요청 수 (필터가 좁다는 신호)")
    out.append(f"matchuri_cards_short_total {cards_short}")

    _family(out, "matchuri_cards_ranked_total", "counter",
            "GET /cards 요청 수 (무엇이 순서를 정했나: 태그 계층·거리·보정 없음)")
    for by, n in sorted(ranked_by.items()):
        out.append(f'matchuri_cards_ranked_total{{by="{_label(by)}"}} {n}')

    _family(out, "matchuri_bookmarks_total", "counter", "찜이 바뀐 횟수 (담기·빼기)")
    for action, n in sorted(bookmarks_by.items()):
        out.append(f'matchuri_bookmarks_total{{action="{_label(action)}"}} {n}')

    _family(out, "matchuri_directions_total", "counter",
            "길찾기를 누른 횟수 = 방문 의사 (지도 앱·이동 수단별)")
    for (app_name, mode), n in sorted(directions_by.items()):
        out.append(f'matchuri_directions_total{{map_app="{_label(app_name)}",'
                   f'mode="{_label(mode)}"}} {n}')

    _family(out, "matchuri_surveys_total", "counter",
            "설문 응답 수 (방문 여부·만족도별). 추천의 결과 지표다")
    for (visited, sat), n in sorted(surveys_by.items()):
        out.append(f'matchuri_surveys_total{{visited="{_label(visited)}",'
                   f'satisfaction="{_label(sat)}"}} {n}')

    _family(out, "matchuri_taste_views_total", "counter", "취향 화면(GET /users/{id}/taste) 조회 수")
    out.append(f"matchuri_taste_views_total {taste_views}")
    _family(out, "matchuri_filter_sets_total", "counter", "필터를 저장한 횟수")
    out.append(f"matchuri_filter_sets_total {filter_sets}")
    _family(out, "matchuri_location_sets_total", "counter", "위치를 저장한 횟수")
    out.append(f"matchuri_location_sets_total {location_sets}")
    _family(out, "matchuri_users_created_total", "counter", "발급한 user_id 수")
    out.append(f"matchuri_users_created_total {users_created}")

    _family(out, "matchuri_users_total", "gauge", "등록부에 있는 사용자 수")
    out.append(f"matchuri_users_total {users}")
    _family(out, "matchuri_state_users_total", "gauge",
            "등록부 사용자 수 (matchuri_users_total 과 같은 값 — 이름 호환)")
    out.append(f"matchuri_state_users_total {users}")
    _family(out, "matchuri_sessions", "gauge", "학습 중인 세션 수")
    out.append(f"matchuri_sessions {sessions}")
    _family(out, "matchuri_state_prefs_total", "gauge",
            "메모리에 올라온 선호 기록 수(복원 포함)")
    out.append(f"matchuri_state_prefs_total {prefs}")
    _family(out, "matchuri_state_bookmarks_total", "gauge", "담겨 있는 찜의 총 개수")
    out.append(f"matchuri_state_bookmarks_total {bookmarks}")
    _family(out, "matchuri_restore_seconds", "gauge", "마지막 기동에서 복원에 걸린 시간(초)")
    out.append(f"matchuri_restore_seconds {restore_seconds:.6f}")
    _family(out, "matchuri_ready", "gauge",
            "기동 복원이 끝났나 (0 이면 /healthz 는 200 이어도 카드 요청은 503 이다)")
    out.append(f"matchuri_ready {ready_flag:.0f}")
    _family(out, "matchuri_artifacts_available", "gauge",
            "서빙 산출물이 있나 (0 이면 legacy 정책으로만 돈다)")
    out.append(f"matchuri_artifacts_available {1 if artifacts else 0}")

    proc = _proc()
    if proc:
        for key, kind, help_text in (
                ("resident_bytes", "gauge", "상주 메모리(RSS, 바이트)"),
                ("cpu_seconds_total", "counter", "사용한 CPU 시간(초, user+sys)"),
                ("open_fds", "gauge", "열려 있는 파일 디스크립터 수"),
                ("start_time_seconds", "gauge", "프로세스 시작 시각(유닉스 초)"),
                ("threads", "gauge", "스레드 수")):
            if key in proc:
                name = f"matchuri_process_{key}"
                _family(out, name, kind, help_text)
                out.append(f"{name} {proc[key]:.6f}".rstrip("0").rstrip("."))

    _family(out, "matchuri_build_info", "gauge", "버전과 도는 정책")
    out.append(f'matchuri_build_info{{version="{_label(version)}",'
               f'policy="{_label(policy)}"}} 1')
    return "\n".join(out) + "\n"


# ────────────────────────────────────────────────────────── 미들웨어
class RequestLog:
    """요청 한 건 = 로그 한 줄 + 지표 한 번. 순수 ASGI 라 응답 스트림을 그대로 흘린다.

    `BaseHTTPMiddleware` 를 쓰지 않는 이유: 그쪽은 응답을 큐로 옮겨 담아 스트리밍과
    예외 전파가 미묘해진다. 여기서 필요한 것은 상태 코드·바이트 수·경로 템플릿뿐이고
    그건 `send` 를 감싸면 얻는다.

    라우팅은 이 미들웨어 **안쪽**에서 일어나므로 `scope["route"]`·`path_params`·
    `state` 는 호출이 끝난 뒤에 읽는다(같은 dict 를 라우터가 채운다).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        started = time.perf_counter()
        rid = clean_request_id(_header(scope, REQUEST_ID_HEADER))
        # 컨텍스트 변수는 이 호출이 끝나면 풀린다. 예외가 밖(ServerErrorMiddleware)에서
        # 처리될 때도 id 를 알 수 있도록 요청 상태에도 적어 둔다.
        scope.setdefault("state", {})["request_id"] = rid
        token = bind_request_id(rid)
        inflight(1)
        status = 500
        sent = 0

        async def _send(message):
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", rid.encode("latin-1", "ignore")))
                message = {**message, "headers": headers}
            elif message["type"] == "http.response.body":
                sent += len(message.get("body") or b"")
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:                                   # noqa: BLE001
            # 로그는 여기서 남긴다 — 밖의 ServerErrorMiddleware 가 응답을 만들기 때문에
            # 이 자리를 놓치면 traceback 이 아무 줄에도 안 남는다.
            log_exc("error", exc, method=scope.get("method"),
                    path=scope.get("path"), route=_route(scope))
            self._finish(scope, started, 500, sent, error=True)
            raise
        else:
            self._finish(scope, started, status, sent)
        finally:
            inflight(-1)
            unbind_request_id(token)

    def _finish(self, scope, started: float, status: int, sent: int,
                error: bool = False) -> None:
        seconds = time.perf_counter() - started
        route = _route(scope)
        record_request(route, scope.get("method", "?"), status, seconds)
        ms = round(seconds * 1000, 1)
        path = scope.get("path", "")
        level = logging.DEBUG if (path in QUIET_PATHS and not error) else logging.INFO
        log("http", level=level,
            method=scope.get("method"), path=path, route=route, status=status,
            ms=ms, client_ip=client_ip(scope), user_id=_user_id(scope),
            ua=_header(scope, "user-agent"), bytes_out=sent)
        if ms > SLOW_MS:
            inc_slow(route)
            log("http.slow", level=logging.WARNING, method=scope.get("method"),
                route=route, path=path, status=status, ms=ms,
                user_id=_user_id(scope), limit_ms=SLOW_MS)


def _header(scope, name: str) -> str | None:
    want = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or []:
        if key.lower() == want:
            return value.decode("latin-1", "replace")[:512]
    return None


def client_ip(scope) -> str | None:
    """`X-Forwarded-For` 의 **첫 홉**(nginx 뒤에 있으므로), 없으면 소켓 주소.

    첫 홉은 클라이언트가 위조할 수 있다 — 인증에는 쓰지 않고 로그에만 쓴다.
    """
    xff = _header(scope, "x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:64]
    client = scope.get("client")
    return client[0] if client else None


def _route(scope) -> str:
    """경로 템플릿(`/cards/{card_id}`). 지표 라벨이 카드 id 만큼 늘어나면 안 된다."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    return scope.get("path") or "?"


def _user_id(scope) -> str | None:
    """이 요청이 누구의 것인가 — 경로 인자 · 쿼리 · 핸들러가 심어 준 값 순."""
    state = scope.get("state") or {}
    if isinstance(state, dict) and state.get("user_id"):
        return str(state["user_id"])[:64]
    params = scope.get("path_params") or {}
    if params.get("user_id"):
        return str(params["user_id"])[:64]
    query = scope.get("query_string") or b""
    if b"user_id=" in query:
        from urllib.parse import parse_qs
        got = parse_qs(query.decode("latin-1", "replace")).get("user_id")
        if got:
            return got[0][:64]
    return None


# ────────────────────────────────────────────────────────── CORS
def cors_origins() -> list[str]:
    """`MATCHURI_CORS_ORIGINS` 의 쉼표 목록. 없으면 빈 목록(= CORS 를 켜지 않는다)."""
    raw = os.environ.get(CORS_ENV) or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


def add_cors(app) -> list[str]:
    """환경변수가 있을 때만 CORS 를 켠다. 돌려주는 것은 실제로 허용한 오리진 목록.

    브라우저에서 직접 부르는 프론트가 생길 때까지 기본은 **꺼짐**이다 — 켜 두면
    '어디서 부를 수 있나' 가 코드에 안 적히고 환경변수에만 남는다.
    `*` 와 쿠키를 같이 허용하지 않는다(그 조합은 브라우저가 막고, 막지 않으면 위험하다).
    """
    origins = cors_origins()
    if not origins:
        return []
    from fastapi.middleware.cors import CORSMiddleware
    wildcard = "*" in origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not wildcard,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    return origins
