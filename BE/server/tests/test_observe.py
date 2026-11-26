# -*- coding: utf-8 -*-
"""관측·보안 — 로그 한 줄의 계약, 요청 id, 500 처리, 헬스·지표, 입력 검증, CORS.

로그는 사고가 났을 때만 읽는다. 그때 필드가 하나 없거나 이름이 바뀌어 있으면 그 사고는
로그로 풀 수 없다 — 그래서 **필드 이름을 테스트로 잠근다.** 지표도 같은 이유다(대시보드
쿼리가 이름에 붙어 있다).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time

import pytest
from fastapi.testclient import TestClient

from app import observe, state

#: `event: "http"` 줄이 반드시 담는 것. 하나라도 빠지면 대시보드·grep 이 깨진다.
HTTP_FIELDS = {"ts", "level", "event", "request_id", "method", "path", "route",
               "status", "ms", "client_ip", "user_id", "ua", "bytes_out"}


@contextlib.contextmanager
def capture(level: int = logging.DEBUG):
    """이 블록 안에서 남은 로그 줄을 **형식까지 그대로** 모은다(JSON 으로 다시 읽는다)."""
    logger = logging.getLogger(observe.LOGGER_NAME)
    got: list[dict] = []
    formatter = observe.JsonFormatter()

    class Cap(logging.Handler):
        def emit(self, record):
            got.append(json.loads(formatter.format(record)))

    handler = Cap()
    handler.setLevel(logging.DEBUG)
    old = logger.level
    logger.setLevel(level)
    logger.addHandler(handler)
    try:
        yield got
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)


def only(lines: list[dict], event: str) -> list[dict]:
    return [x for x in lines if x.get("event") == event]


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


@pytest.fixture(scope="module")
def uid(client):
    return client.post("/users", json={"name": "관측"}).json()["user_id"]


# ────────────────────────────────────────────────── 요청 로그
def test_요청_한_줄은_JSON_이고_필드가_다_있다(client, uid):
    with capture() as lines:
        r = client.get(f"/cards?user_id={uid}&limit=2",
                       headers={"User-Agent": "pytest-ua"})
    assert r.status_code == 200
    http = only(lines, "http")
    assert len(http) == 1
    line = http[0]
    assert HTTP_FIELDS <= set(line)
    assert line["method"] == "GET" and line["path"] == "/cards"
    assert line["route"] == "/cards"                    # 경로 템플릿이다
    assert line["status"] == 200 and isinstance(line["ms"], float)
    assert line["user_id"] == uid and line["ua"] == "pytest-ua"
    assert line["bytes_out"] > 0 and line["level"] == "INFO"
    assert line["request_id"] == r.headers["x-request-id"]


def test_경로_템플릿으로_묶는다(client):
    """`/cards/{card_id}` 는 카드마다 다른 줄이 아니다 — 지표 라벨이 폭발하지 않게."""
    cid = client.get("/cards?limit=1").json()[0]["card_id"]
    with capture() as lines:
        client.get(f"/cards/{cid}")
    line = only(lines, "http")[0]
    assert line["path"] == f"/cards/{cid}" and line["route"] == "/cards/{card_id}"


def test_도메인_이벤트가_요청과_같은_id_로_묶인다(client):
    with capture() as lines:
        r = client.post("/users", json={"name": "이벤트"})
    rid = r.headers["x-request-id"]
    created = only(lines, "user.create")
    assert len(created) == 1 and created[0]["user_id"] == r.json()["user_id"]
    assert {x["request_id"] for x in lines} == {rid}     # 한 요청 = 하나의 id


def test_설정과_선호_이벤트(client):
    uid = client.post("/users").json()["user_id"]
    with capture() as lines:
        client.put(f"/users/{uid}/location", json={"lat": 37.5006, "lon": 127.0366})
        client.put(f"/users/{uid}/filter", json={"cat": ["일식"], "within_m": 500})
        cards = client.get(f"/cards?user_id={uid}&limit=2").json()
        client.post("/preferences", json={"user_id": uid,
                                          "card_id": cards[0]["card_id"],
                                          "prefer": "like"})
        client.delete(f"/users/{uid}/filter")
    assert only(lines, "user.location.set")[0]["lat"] == 37.501    # 좌표는 반올림해 남긴다
    assert only(lines, "user.filter.set")[0]["cat"] == ["일식"]
    assert only(lines, "user.filter.clear")[0]["user_id"] == uid

    serve = only(lines, "cards.serve")[0]
    assert serve["n_requested"] == 2 and serve["n_returned"] == len(cards)
    assert serve["filter"] == {"cat": ["일식"], "tag": [], "within_m": 500}
    assert serve["has_location"] is True and sum(serve["policies"].values()) == len(cards)

    pref = only(lines, "pref.record")[0]
    assert pref["user_id"] == uid and pref["prefer"] == "like"
    assert pref["store_id"] and pref["learned"] == ["legacy", "taste"]
    # 본문에서 확인한 user_id 가 그 요청의 http 줄에도 붙는다
    assert [x for x in only(lines, "http") if x["route"] == "/preferences"][0]["user_id"] \
        == uid


def test_요청_id_는_받아_쓰고_이상하면_새로_만든다(client):
    r = client.get("/healthz", headers={"X-Request-ID": "trace-abc_123"})
    assert r.headers["x-request-id"] == "trace-abc_123"

    for bad in ("has space", "x" * 65, "semi;colon"):
        r = client.get("/healthz", headers={"X-Request-ID": bad})
        got = r.headers["x-request-id"]
        assert got != bad and len(got) == 32 and got.isalnum()

    # HTTP 헤더로는 보낼 수조차 없는 값들(제어문자·한글)은 함수로 직접 확인한다 —
    # 로그 한 줄이 여러 줄이 되는 것을 막는 자리다.
    for bad in ("줄\n바꿈", "탭\t", "한글", "", "  ", None, "x" * 200):
        got = observe.clean_request_id(bad)
        assert got != bad and len(got) == 32 and got.isalnum()
    assert observe.clean_request_id("  keep-me.1  ") == "keep-me.1"


def test_느린_요청은_경고를_한_줄_더_남긴다(client, monkeypatch):
    monkeypatch.setattr(observe, "SLOW_MS", 0.0)        # 무엇이든 느리다고 보게
    with capture() as lines:
        client.get("/cards?limit=1")
    slow = only(lines, "http.slow")
    assert len(slow) == 1 and slow[0]["level"] == "WARNING"
    assert slow[0]["limit_ms"] == 0.0 and slow[0]["route"] == "/cards"


def test_헬스체크는_DEBUG_로만_남는다(client):
    """초당 한 번 오는 줄이 사고 로그를 덮지 않게 — 기본(INFO)에서는 안 보인다."""
    with capture(logging.INFO) as quiet:
        client.get("/healthz")
        client.get("/metrics")
    assert only(quiet, "http") == []
    with capture(logging.DEBUG) as loud:
        client.get("/healthz")
    assert len(only(loud, "http")) == 1


# ────────────────────────────────────────────────── 예외
@pytest.fixture
def boom(client):
    """예외를 던지는 임시 경로. 테스트가 끝나면 걷어낸다(문서에도 안 나온다)."""
    import main

    def _boom():
        raise ZeroDivisionError("일부러 터뜨린다")

    main.app.add_api_route("/__boom", _boom, include_in_schema=False)
    yield TestClient(main.app, raise_server_exceptions=False)
    main.app.router.routes = [r for r in main.app.router.routes
                              if getattr(r, "path", None) != "/__boom"]


def test_예상_못_한_예외는_요청_id_만_돌려준다(boom):
    with capture() as lines:
        r = boom.get("/__boom")
    assert r.status_code == 500
    body = r.json()
    assert body == {"detail": "internal error", "request_id": r.headers["x-request-id"]}
    assert "ZeroDivisionError" not in r.text and "Traceback" not in r.text

    err = only(lines, "error")
    assert len(err) == 1                                # traceback 은 로그에만 있다
    assert err[0]["error"] == "ZeroDivisionError" and "Traceback" in err[0]["traceback"]
    assert err[0]["request_id"] == body["request_id"]
    assert only(lines, "http")[0]["status"] == 500      # 지표에도 500 으로 센다


def test_4xx_는_사유와_함께_INFO_로_남는다(client):
    with capture() as lines:
        assert client.get("/cards/999999999").status_code == 404
        assert client.post("/preferences", json={"user_id": "u", "card_id": 0,
                                                 "prefer": "좋아요"}).status_code == 400
        assert client.put("/users/u_test/filter",
                          json={"cat": ["초밥"]}).status_code == 422
    events = only(lines, "http.client_error")
    assert [e["status"] for e in events] == [404, 400, 422]
    assert all(e["level"] == "INFO" for e in events)
    assert "card not found" in events[0]["detail"]
    assert "초밥" in events[2]["detail"]


# ────────────────────────────────────────────────── 운영 엔드포인트
def test_healthz_는_의존성을_보지_않는다(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_readyz(client):
    body = client.get("/readyz").json()
    assert body["ok"] is True and body["reasons"] == []
    assert body["data"] and body["pool"] and body["state_writable"]
    assert isinstance(body["artifacts"], bool)
    assert body["policy"] in ("legacy", "taste", "interleave")
    assert body["cards"] > 0 and body["recommendable"] > 0


def test_state_에_쓸_수_없으면_readyz_는_503(client, monkeypatch):
    monkeypatch.setattr(state, "writable", lambda: False)
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False and body["state_writable"] is False
    assert any("state" in reason for reason in body["reasons"])


def test_state_writable_은_실제로_써_본다(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DIR", tmp_path / "새디렉터리")
    assert state.writable() is True                     # 없으면 만들어 본다
    monkeypatch.setattr(state, "DIR", tmp_path / "없는파일" / "밑")
    (tmp_path / "없는파일").write_text("x", encoding="utf-8")
    assert state.writable() is False                    # 파일 밑에는 못 만든다


def test_metrics(client, uid):
    before = client.get("/metrics").text
    client.get(f"/cards?user_id={uid}&limit=1")
    body = client.get("/metrics").text
    for name in ("matchuri_http_requests_total", "matchuri_http_request_seconds_bucket",
                 "matchuri_http_request_seconds_sum", "matchuri_preferences_total",
                 "matchuri_cards_served_total", "matchuri_users_total",
                 "matchuri_sessions", "matchuri_state_prefs_total",
                 "matchuri_build_info"):
        assert name in body, name
    assert 'matchuri_build_info{version="1.0.0"' in body
    assert 'route="/cards",method="GET",status="200"' in body
    assert f'le="{observe.BUCKETS[0]}"' in body and 'le="+Inf"' in body

    def count(text: str) -> int:
        for line in text.splitlines():
            if line.startswith('matchuri_http_requests_total{route="/cards",'
                               'method="GET",status="200"}'):
                return int(line.rsplit(" ", 1)[1])
        return 0

    assert count(body) == count(before) + 1             # 요청 한 번이 한 번 센다
    # 카드 id 는 라벨에 들어가지 않는다(시계열 폭발 방지)
    assert "route=\"/cards/{card_id}\"" in body or "/cards/{card_id}" not in body


# ────────────────────────────────────────────────── 입력 검증
#: 경로·쿼리·본문 어디에 넣어도 거절돼야 하는 값들(HTTP 로 보낼 수 있는 것만 골랐다).
BAD_IDS = ["u id", "u;drop", "u<script>", "x" * 65, "사용자"]


def test_user_id_는_모든_자리에서_검증된다(client):
    for bad in BAD_IDS:
        assert client.get(f"/users/{bad}").status_code == 422, bad
        assert client.get("/cards", params={"user_id": bad}).status_code == 422, bad
        assert client.get("/cards/0", params={"user_id": bad}).status_code == 422, bad
        assert client.put(f"/users/{bad}/location",
                          json={"lat": 37.5, "lon": 127.0}).status_code == 422, bad
        assert client.delete(f"/users/{bad}/filter").status_code == 422, bad
        assert client.post("/preferences", json={"user_id": bad, "card_id": 0,
                                                 "prefer": "like"}).status_code == 422
        assert client.get(f"/preferences/{bad}").status_code == 422, bad
        assert client.get(f"/users/{bad}/taste").status_code == 422, bad
    r = client.get("/cards", params={"user_id": "u id"})
    assert "user_id" in r.json()["detail"]              # 사람이 읽을 한국어 메시지

    # 빈 값은 '누구도 아님' 이다 — 예전부터 `user_id` 없는 호출과 같게 다뤘다
    assert client.get("/cards", params={"user_id": "", "limit": 1}).status_code == 200


def test_예전부터_쓰던_id_는_그대로_통한다(client):
    """`u_test`·`u9`·`anon.filter:2` 같은 값은 계속 유효해야 한다(기존 테스트·기록)."""
    from app import users as registry
    for good in ("u", "u_test", "u9", "u_3f2a1b4c5d6e", "anon.filter:2", "a-b.c_d",
                 "x" * 64):
        assert registry.validate_id(good) == good
        assert client.get("/cards", params={"user_id": good,
                                           "limit": 1}).status_code == 200


def test_이름은_50자_이하이고_제어문자가_지워진다(client):
    r = client.post("/users", json={"name": "가" * 51})
    assert r.status_code == 422 and "50자" in json.dumps(r.json(), ensure_ascii=False)
    created = client.post("/users", json={"name": " 김철수\n\r\t "}).json()
    assert created["name"] == "김철수"                   # 줄바꿈이 로그를 쪼개지 못한다
    assert client.post("/users", json={"name": "\n\n"}).json()["name"] is None


# ────────────────────────────────────────────────── CORS
def _cors_app():
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_CORS_는_환경변수가_있을_때만_켜진다(monkeypatch):
    monkeypatch.delenv(observe.CORS_ENV, raising=False)
    app = _cors_app()
    assert observe.add_cors(app) == []
    r = TestClient(app).get("/ping", headers={"Origin": "https://app.example"})
    assert "access-control-allow-origin" not in r.headers

    monkeypatch.setenv(observe.CORS_ENV, "https://app.example, https://admin.example")
    app = _cors_app()
    assert observe.add_cors(app) == ["https://app.example", "https://admin.example"]
    r = TestClient(app).get("/ping", headers={"Origin": "https://app.example"})
    assert r.headers["access-control-allow-origin"] == "https://app.example"
    assert r.headers.get("access-control-allow-credentials") == "true"
    # 목록에 없는 오리진은 허용하지 않는다
    r = TestClient(app).get("/ping", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_와일드카드에는_자격증명을_허용하지_않는다(monkeypatch):
    monkeypatch.setenv(observe.CORS_ENV, "*")
    app = _cors_app()
    observe.add_cors(app)
    r = TestClient(app).get("/ping", headers={"Origin": "https://any.example"})
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers


# ────────────────────────────────────────────────── 지표 (Prometheus)
#: 노출 형식의 한 줄. 이름[{라벨}] 값 — 여기서 어긋나면 스크레이프가 통째로 실패한다.
#: 라벨 **값** 안에는 `{`·`}` 가 들어갈 수 있으므로(우리 `route` 가 `/cards/{card_id}` 다)
#: `[^}]*` 로는 못 읽는다. 값은 따옴표 안의 이스케이프된 문자열로 읽어야 맞다.
LABEL_VALUE = r'"(?:[^"\\]|\\.)*"'
SAMPLE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*"
                    r"(\{[a-zA-Z_][a-zA-Z0-9_]*=" + LABEL_VALUE +
                    r"(,[a-zA-Z_][a-zA-Z0-9_]*=" + LABEL_VALUE + r")*\})?"
                    r" -?[0-9.e+-]+$")

#: 히스토그램은 계열 이름 하나에 세 종류의 줄이 붙는다.
HIST_SUFFIX = ("_bucket", "_sum", "_count")


def parse_exposition(text: str) -> dict[str, str]:
    """작은 엄격 파서 — 모든 줄이 형식에 맞고 모든 계열에 HELP·TYPE 이 있는지 본다.

    Prometheus 는 형식이 어긋난 줄 하나로 스크레이프 전체를 버린다. 우리는 이 텍스트를
    손으로 쓰므로(의존성을 안 더하려고) 그 대가를 테스트가 치러야 한다.
    """
    helps: dict[str, str] = {}
    types: dict[str, str] = {}
    seen: dict[str, str] = {}
    for line in text.splitlines():
        assert line == line.rstrip(), f"줄 끝 공백: {line!r}"
        if not line:
            continue
        if line.startswith("# HELP "):
            name, _, rest = line[len("# HELP "):].partition(" ")
            assert rest.strip(), f"HELP 내용이 비었다: {line}"
            assert name not in helps, f"HELP 가 두 번: {name}"
            helps[name] = rest
            continue
        if line.startswith("# TYPE "):
            name, _, kind = line[len("# TYPE "):].partition(" ")
            assert kind in ("counter", "gauge", "histogram", "summary"), line
            assert name not in types, f"TYPE 이 두 번: {name}"
            types[name] = kind
            continue
        assert not line.startswith("#"), f"모르는 주석: {line}"
        assert SAMPLE.match(line), f"형식이 어긋난 샘플: {line!r}"
        metric = line.split("{", 1)[0].split(" ", 1)[0]

        family = metric
        if types.get(metric) is None:            # 히스토그램의 _bucket/_sum/_count
            for suffix in HIST_SUFFIX:
                if metric.endswith(suffix) and types.get(metric[: -len(suffix)]):
                    family = metric[: -len(suffix)]
                    break
        assert family in helps, f"HELP 없는 계열: {family}"
        assert family in types, f"TYPE 없는 계열: {family}"
        seen[metric] = family
    return seen


def test_노출_형식이_엄격하게_맞는다(client, uid):
    """`/metrics` 는 손으로 쓴 텍스트다 — 한 줄만 어긋나도 스크레이프가 통째로 버려진다."""
    client.get(f"/cards?user_id={uid}&limit=2")
    client.get("/healthz")
    seen = parse_exposition(client.get("/metrics").text)
    assert "matchuri_http_requests_total" in seen
    assert "matchuri_http_request_seconds_bucket" in seen
    assert seen["matchuri_http_request_seconds_bucket"] == "matchuri_http_request_seconds"


def test_지표_이름이_다_있다(client, uid):
    body = client.get("/metrics").text
    for name in ("matchuri_http_requests_total", "matchuri_http_request_seconds",
                 "matchuri_http_slow_total", "matchuri_errors_total",
                 "matchuri_http_inflight", "matchuri_preferences_total",
                 "matchuri_cards_served_total", "matchuri_cards_requests_total",
                 "matchuri_cards_short_total", "matchuri_bookmarks_total",
                 "matchuri_taste_views_total", "matchuri_filter_sets_total",
                 "matchuri_location_sets_total", "matchuri_users_created_total",
                 "matchuri_users_total", "matchuri_state_users_total",
                 "matchuri_sessions", "matchuri_state_prefs_total",
                 "matchuri_state_bookmarks_total", "matchuri_restore_seconds",
                 "matchuri_artifacts_available", "matchuri_build_info"):
        assert f"# TYPE {name} " in body, name


@pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="리눅스에서만")
def test_프로세스_지표는_proc_에서_읽는다(client):
    body = client.get("/metrics").text
    for name in ("matchuri_process_resident_bytes", "matchuri_process_cpu_seconds_total",
                 "matchuri_process_open_fds", "matchuri_process_start_time_seconds",
                 "matchuri_process_threads"):
        assert f"# TYPE {name} " in body, name
    proc = observe._proc()
    assert proc["resident_bytes"] > 1e6 and proc["threads"] >= 1
    assert proc["start_time_seconds"] < time.time()


def test_proc_이_없으면_조용히_건너뛴다(client, monkeypatch):
    """개발 노트북(맥)에서 `/metrics` 가 500 이 되면 안 된다 — 지표는 서비스의 부속이다."""
    monkeypatch.setattr(observe, "_proc", lambda: {})
    body = client.get("/metrics").text
    assert "matchuri_process_resident_bytes" not in body
    parse_exposition(body)                       # 나머지 형식은 그대로다


def _metric(text: str, prefix: str) -> int:
    for line in text.splitlines():
        if line.startswith(prefix):
            return int(float(line.rsplit(" ", 1)[1]))
    return 0


def test_선호_지표에_노출_정책_라벨이_붙는다(client):
    """팀-드래프트 인터리빙의 온라인 결과 — 그 카드를 **보여준** 정책이 라벨이다."""
    uid = client.post("/users").json()["user_id"]
    cards = client.get(f"/cards?user_id={uid}&limit=4").json()
    by_policy = {c["card_id"]: c["policy"] for c in cards}
    before = client.get("/metrics").text
    for card in cards[:2]:
        client.post("/preferences", json={"user_id": uid, "card_id": card["card_id"],
                                          "prefer": "like"})
    after = client.get("/metrics").text
    for card in cards[:2]:
        name = by_policy[card["card_id"]]
        prefix = f'matchuri_preferences_total{{prefer="like",policy="{name}"}}'
        assert _metric(after, prefix) > _metric(before, prefix), name

    # 노출된 적 없는 카드(찜 목록에서 바로 평가 등)는 `none` 이다
    from app import loader
    from recsys import policy as pol
    other = client.get("/cards?limit=30").json()          # user_id 없이 = 노출 기록 없음
    unseen = next(c["card_id"] for c in other
                  if pol.exposed_by(uid, loader.to_store_id(c["card_id"])) is None)
    prefix = 'matchuri_preferences_total{prefer="dislike",policy="none"}'
    before_none = _metric(client.get("/metrics").text, prefix)
    client.post("/preferences", json={"user_id": uid, "card_id": unseen,
                                      "prefer": "dislike"})
    assert _metric(client.get("/metrics").text, prefix) == before_none + 1


def test_노출_지도는_첫_노출이_이기고_상한이_있다(monkeypatch):
    from recsys import policy as pol
    pol.remember_exposure("u_map", "s1", "legacy")
    pol.remember_exposure("u_map", "s1", "taste")         # 두 번째는 무시된다
    assert pol.exposed_by("u_map", "s1") == "legacy"
    assert pol.exposed_by("u_map", "없는가게") is None

    monkeypatch.setattr(pol, "MAX_EXPOSED", 3)
    for i in range(5):
        pol.remember_exposure("u_cap", f"s{i}", "taste")
    kept = [k for k in pol._exposed if k[0] == "u_cap"]
    assert len(kept) == 3 and kept[0] == ("u_cap", "s2")  # 오래된 것부터 버린다


def test_노출_지도는_기동_때_파일에서_되살아난다(client, tmp_path, monkeypatch):
    """재시작 뒤의 선호가 전부 `none` 으로 세어지면 인터리빙 비교가 배포마다 끊긴다."""
    from app import loader, state
    from recsys import policy as pol

    monkeypatch.setattr(state, "EXPOSURES", tmp_path / "exposures.jsonl")
    cid = client.get("/cards?limit=1").json()[0]["card_id"]
    state.record_exposure("u_boot", cid, "taste", 0, 0)
    state.record_exposure("u_boot", cid, "legacy", 1, 0)      # 재노출은 무시된다
    state.record_exposure("u_boot", 10 ** 9, "taste", 0, 1)   # 없는 카드는 건너뛴다

    monkeypatch.setattr(pol, "_exposed", {})
    n = pol.restore_exposures(loader.to_store_id)
    assert n == 2                                            # 읽은 줄 수(없는 카드 제외)
    assert pol.exposed_by("u_boot", loader.to_store_id(cid)) == "taste"


def test_카드_요청_지표(client, uid):
    """요청 수와 카드 수는 다른 계열이다 — '몇 번 불렀나' 와 '몇 장 나갔나'."""
    before = client.get("/metrics").text
    client.get("/cards?limit=3")                              # 개인화 없음·필터 없음
    client.put(f"/users/{uid}/filter", json={"cat": ["중식"], "within_m": 1})
    client.put(f"/users/{uid}/location", json={"lat": 37.5006, "lon": 127.0366})
    short = client.get(f"/cards?user_id={uid}&limit=10").json()   # 필터가 좁아 못 채운다
    after = client.get("/metrics").text
    client.delete(f"/users/{uid}/filter")

    assert len(short) < 10
    plain = 'matchuri_cards_requests_total{filtered="false",personalized="false"}'
    tight = 'matchuri_cards_requests_total{filtered="true",personalized="true"}'
    assert _metric(after, plain) == _metric(before, plain) + 1
    assert _metric(after, tight) == _metric(before, tight) + 1
    assert _metric(after, "matchuri_cards_short_total") >= \
        _metric(before, "matchuri_cards_short_total") + 1
    assert _metric(after, "matchuri_cards_served_total") >= 1   # 카드 수는 따로 센다


def test_게이지와_카운터가_움직인다(client):
    before = client.get("/metrics").text
    uid = client.post("/users").json()["user_id"]
    cid = client.get("/cards?limit=1").json()[0]["card_id"]
    client.put(f"/users/{uid}/bookmarks/{cid}")
    client.put(f"/users/{uid}/location", json={"lat": 37.5, "lon": 127.0})
    client.put(f"/users/{uid}/filter", json={"cat": ["한식"]})
    after = client.get("/metrics").text

    for name in ("matchuri_users_created_total", "matchuri_location_sets_total",
                 "matchuri_filter_sets_total"):
        assert _metric(after, name) == _metric(before, name) + 1
    assert _metric(after, "matchuri_state_bookmarks_total") >= \
        _metric(before, "matchuri_state_bookmarks_total") + 1
    assert _metric(after, "matchuri_state_users_total") == \
        _metric(after, "matchuri_users_total")            # 같은 값의 다른 이름이다
    assert _metric(after, "matchuri_artifacts_available") in (0, 1)
    # 스크레이프 요청 자신도 '처리 중' 이라 1 이 기준선이다
    assert _metric(after, "matchuri_http_inflight") == 1
    assert "matchuri_restore_seconds " in after


def test_느린_요청과_5xx_도_지표로_센다(boom, monkeypatch):
    errors = 'matchuri_errors_total{route="/__boom"}'
    slow = 'matchuri_http_slow_total{route="/healthz"}'
    before = boom.get("/metrics").text
    boom.get("/__boom")
    monkeypatch.setattr(observe, "SLOW_MS", 0.0)
    boom.get("/healthz")
    after = boom.get("/metrics").text
    assert _metric(after, errors) == _metric(before, errors) + 1
    assert _metric(after, slow) == _metric(before, slow) + 1
