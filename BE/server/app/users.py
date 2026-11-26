"""사용자 등록부.

로그인은 없다. `POST /users` 로 id 를 하나 받아 클라이언트가 들고 다니면 된다.
등록하지 않은 아무 문자열을 `user_id` 로 써도 동작은 한다(예전 방식). 다만
등록해 두면 이름이 붙고 관리자 화면에서 구분하기 쉽다.

등록부는 메모리에 살지만 `state/users.jsonl` 에 남아 재시작 때 되살아난다(`app/state.py`).
레코드를 고칠 때는 **병합한 전체 레코드**를 다시 append 한다 — 복원이 마지막 줄을 이기게
하는 방식이라(마지막 레코드가 그 사용자다) 부분 레코드를 쓰면 이름·발급시각이 사라진다.
"""
from __future__ import annotations

import re
import secrets
import threading
from datetime import datetime, timezone

from fastapi import HTTPException

from app import state

#: `user_id` 로 받아 줄 수 있는 것. 이 값은 URL·파일 이름 없는 로그 줄·상태 파일에 그대로
#: 들어가므로 좁게 잡는다 — 제어문자가 섞이면 로그 한 줄이 여러 줄이 되고(로그 주입),
#: 길이가 무제한이면 파일이 그만큼 부푼다. 발급하는 id(`u_` + hex 12)는 당연히 통과한다.
ID_MAX = 64
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,%d}$" % ID_MAX)

_lock = threading.Lock()
_users: dict[str, dict] = {}

MAX_USERS = 10_000


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def validate_id(user_id: str) -> str:
    """`user_id` 를 받는 모든 자리(경로·쿼리·본문)가 이걸 지난다. 아니면 **422**.

    로그인이 없어 클라이언트가 id 를 정하는 서버라, '아무 문자열' 과 '아무 바이트열' 은
    구분해야 한다. 거절 사유를 한국어로 적어 주는 이유는 이 메시지를 읽는 것이 사람이기
    때문이다(프론트 개발자).
    """
    if not isinstance(user_id, str) or not ID_RE.match(user_id):
        raise HTTPException(
            422, f"user_id 는 영문·숫자와 . _ : - 만 쓸 수 있고 1~{ID_MAX}자여야 합니다")
    return user_id


def _public(u: dict) -> dict:
    """응답에 나가는 모양. 예전 파일에서 복원한 레코드에는 `location`·`filter` 가 없다."""
    return {**u, "location": u.get("location"), "filter": u.get("filter")}


def _ensure(user_id: str) -> dict:
    """그 id 의 레코드를 보장한다(**락을 잡은 채** 부른다).

    등록하지 않은 id 도 받는다 — `user_id` 는 아무 문자열이나 되는 것이 이 서버의 계약이고,
    위치나 필터를 저장하려고 먼저 `POST /users` 를 하라고 요구하면 그 계약이 깨진다.
    그때는 이름 없는(`name: None`) 레코드를 새로 만든다.
    """
    u = _users.get(user_id)
    if u is None:
        # `location`·`filter` 를 **처음부터 넣어 둔다.** 빼 두면 위치만 저장한 미등록
        # 사용자의 응답에 `filter` 키가 아예 없어서, 같은 엔드포인트가 사용자에 따라
        # 다른 모양을 준다(타입을 붙이며 드러난 구멍이다 — `_public` 과 같은 규칙으로 맞춘다).
        u = {"user_id": user_id, "name": None, "created_at": _now(),
             "location": None, "filter": None}
        _users[user_id] = u
    return u


def create(name: str | None = None) -> dict:
    """새 사용자. id 는 추측하기 어렵게 만든다(남의 취향을 넘겨보지 못하도록)."""
    with _lock:
        if len(_users) >= MAX_USERS:            # 가장 오래된 것부터 정리
            oldest = min(_users, key=lambda k: _users[k]["created_at"])
            del _users[oldest]
        uid = "u_" + secrets.token_hex(6)
        _users[uid] = {"user_id": uid, "name": name or None, "created_at": _now(),
                       "location": None, "filter": None}
        rec = dict(_users[uid])
    state.record_user(rec)          # 락 밖에서 — 파일 I/O 로 요청을 붙잡지 않는다
    return rec


#: 파일에 다시 적을 만큼 움직였다고 보는 거리(m).
#:
#: **앱이 위치를 계속 보낸다**(방문 판정 때문에). 그런데 `set_location` 은 부를 때마다
#: 레코드 전체를 `users.jsonl` 에 한 줄 추가한다 — 1분에 한 번이면 한 시간에 60줄이고,
#: 그 줄마다 위도·경도가 정확히 남는다. 그러면 등록부 파일이 **GPS 궤적**이 되고,
#: 기동 때 `state.restore` 가 그걸 한 줄씩 다 다시 먹는다.
#:
#: 저장된 위치를 쓰는 곳은 `distance_m` 과 `within_m` 필터뿐이고, 반경은 300m 이상이다.
#: 100m 안쪽의 차이는 그 답을 바꾸지 않으므로 적지 않는다. 메모리의 값은 매번 갱신되니
#: **응답과 필터는 항상 최신**이고, 파일만 거칠어진다(재시작 뒤 최대 100m 오차).
SAVE_MOVE_M = 100


def _older(stamp: str, than: str | None) -> bool:
    """`stamp` 가 `than` 보다 오래됐나.

    **문자열로 비교하면 안 된다.** 앱이 `+00:00` 으로 보내고 서버가 `+09:00` 으로
    저장해 두면 사전순 비교가 거꾸로 나온다(`2026-09-16T03:00:00+00:00` 은
    `2026-09-16T12:00:00+09:00` 과 같은 순간인데 문자열로는 더 작다).
    """
    if not than:
        return False
    try:
        a, b = datetime.fromisoformat(stamp), datetime.fromisoformat(than)
    except (TypeError, ValueError):
        return False              # 못 읽으면 덮는다 — 낡은 값을 붙들고 있을 이유가 없다
    if (a.tzinfo is None) != (b.tzinfo is None):
        return False              # 한쪽만 오프셋이 없으면 비교가 불가능하다
    return a < b


#: user_id -> 마지막으로 **파일에 적은** 좌표. 등록부 레코드에 넣지 않는다 —
#: 그 레코드는 그대로 응답과 파일로 나가므로 내부 살림을 섞으면 계약이 늘어난다.
_saved: dict[str, tuple[float, float]] = {}


def _moved_enough(user_id: str, lat: float, lon: float) -> bool:
    """파일에 다시 적을 만큼 움직였나. 처음이면 무조건 적는다.

    **마지막으로 '적은' 좌표와 비교한다.** 마지막으로 '받은' 좌표와 비교하면 90m 씩
    조금씩 움직이는 동안 한 번도 적히지 않아, 파일의 값이 실제 위치에서 얼마든지
    멀어질 수 있다(재시작하면 그 낡은 값이 살아난다). 기준을 고정해야 오차가
    `SAVE_MOVE_M` 안으로 묶인다.
    """
    last = _saved.get(user_id)
    if last is None:
        return True
    from app import geo             # 순환 import 를 피해 부를 때 가져온다
    return geo.haversine_m(last[0], last[1], lat, lon) >= SAVE_MOVE_M


def set_location(user_id: str, lat: float, lon: float,
                 at: str | None = None) -> dict:
    """그 사용자의 위치를 기억한다(`PUT /users/{id}/location`).

    `at` 을 함께 남긴다 — 위치는 낡는 값이라 '언제 받은 위치인가' 가 있어야 나중에
    '오래된 위치는 쓰지 않는다' 같은 판단을 할 수 있다. 앱이 잰 시각을 주면 그것을,
    안 주면 받은 시각을 쓴다.

    **더 오래된 위치는 설정을 덮지 않는다.** 앱이 지하에서 못 보낸 것을 모아 올려보내면
    순서가 뒤섞여 오는데, 그때 마지막에 도착한 줄이 이기면 현재 위치가 과거로 되돌아간다.
    (그래도 돌려주는 레코드는 늘 현재 설정이라 호출자가 분기할 일은 없다.)

    파일 쓰기는 `SAVE_MOVE_M` 만큼 움직였을 때만 한다(위 상수 설명).
    """
    now = _now()
    stamp = at or now
    with _lock:
        u = _ensure(user_id)
        cur = u.get("location") or None      # 아래 stale 판정에만 쓴다
        # 잰 시각이 더 오래됐으면 설정은 그대로 둔다(판정은 호출자가 이미 했다).
        stale = _older(stamp, (cur or {}).get("at"))
        save = False
        if not stale:
            save = _moved_enough(user_id, float(lat), float(lon))
            u["location"] = {"lat": float(lat), "lon": float(lon), "at": stamp}
            if save:
                _saved[user_id] = (float(lat), float(lon))
        rec = _public(u)            # 옛 파일에서 복원된 레코드에도 두 키가 다 있게
    if save:
        state.record_user(rec)      # 병합된 전체 레코드 — 복원이 이 줄만 봐도 되게
    return rec


def set_filter(user_id: str, cat: list[str], tag: list[str],
               within_m: int | None) -> dict:
    """그 사용자의 필터를 기억한다(`PUT /users/{id}/filter`).

    앱이 `cat`·`tag`·`within_m` 을 매 `GET /cards` 에 다시 실어 보내지 않게 하는 자리다.
    값의 어휘 검증은 라우터가 `filters.validate` 로 이미 했다 — 등록부는 어휘를 모른다.

    `at` 은 위치와 같은 이유로 남긴다('언제 정한 필터인가').
    """
    with _lock:
        u = _ensure(user_id)
        u["filter"] = {"cat": list(cat), "tag": list(tag), "within_m": within_m,
                       "at": _now()}
        rec = _public(u)
    state.record_user(rec)
    return rec


def clear_filter(user_id: str) -> dict:
    """필터를 지운다(`DELETE /users/{id}/filter`). 지운 것도 한 줄 쌓아야 복원이 안다."""
    with _lock:
        u = _ensure(user_id)
        u["filter"] = None
        rec = _public(u)
    state.record_user(rec)
    return rec


def filter_of(user_id: str) -> dict | None:
    """저장된 필터 `{cat, tag, within_m}`. 조건이 하나도 없으면 None(= 저장 안 된 것과 같다).

    빈 목록만 저장된 레코드를 '필터가 있다' 고 보면 `GET /cards` 가 아무 조건도 없는
    필터를 적용하려 들고, 로그와 400 메시지가 있지도 않은 조건을 가리킨다.
    """
    f = (_users.get(user_id) or {}).get("filter") or {}
    cat = [c for c in (f.get("cat") or []) if isinstance(c, str)]
    tag = [t for t in (f.get("tag") or []) if isinstance(t, str)]
    w = f.get("within_m")
    w = int(w) if isinstance(w, int) and not isinstance(w, bool) and w >= 1 else None
    if not cat and not tag and w is None:
        return None
    return {"cat": cat, "tag": tag, "within_m": w}


def location_of(user_id: str) -> tuple[float, float] | None:
    """저장된 위치를 (lat, lon) 으로. 없으면 None."""
    loc = (_users.get(user_id) or {}).get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return None


def get(user_id: str) -> dict | None:
    u = _users.get(user_id)
    return _public(u) if u else None


def name_of(user_id: str) -> str | None:
    u = _users.get(user_id)
    return u["name"] if u else None


def all_users() -> dict[str, dict]:
    return _users
