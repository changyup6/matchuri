# -*- coding: utf-8 -*-
"""찜 — 담기·빼기·목록, 그리고 **추천을 건드리지 않는다**는 성질.

찜은 좋아요가 아니다. 좋아요는 취향 모델의 입력이고 답한 카드는 다시 안 나오지만, 찜은
'나중에 볼 목록' 일 뿐이다. 그 경계가 무너지면(모델이 움직이거나 `shown` 에 들어가면)
사용자는 담기만 했는데 추천이 달라지고, 팀-드래프트 인터리빙의 like 율도 오염된다 —
`test_찜은_추천을_하나도_바꾸지_않는다` 가 호출 전후를 통째로 비교해 그것을 잠근다
(`test_taste_api.py` 의 읽기 전용 검사와 같은 방식).
"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import bookmarks, state
from recsys import policy, swipe, taste


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


@pytest.fixture
def uid(client):
    return client.post("/users", json={"name": "찜"}).json()["user_id"]


@pytest.fixture
def cards(client):
    """실데이터 카드 몇 장(개인화 없는 인기순이라 세션을 만들지 않는다)."""
    return [c["card_id"] for c in client.get("/cards?limit=5").json()]


# ────────────────────────────────────────────────── 담기·빼기
def test_찜은_멱등이다(client, uid, cards):
    first = client.put(f"/users/{uid}/bookmarks/{cards[0]}").json()
    assert first == {"card_id": cards[0], "bookmarked": True,
                     "at": first["at"], "count": 1}
    again = client.put(f"/users/{uid}/bookmarks/{cards[0]}").json()
    assert again == first                       # 시각도 개수도 그대로다
    assert client.get(f"/users/{uid}/bookmarks").json()[0]["card_id"] == cards[0]


def test_없는_카드는_404(client, uid):
    assert client.put(f"/users/{uid}/bookmarks/999999999").status_code == 404
    # 뺄 때는 404 가 아니다 — 이미 원하는 상태다
    r = client.delete(f"/users/{uid}/bookmarks/999999999")
    assert r.status_code == 200 and r.json()["bookmarked"] is False


def test_빼기는_없어도_200(client, uid, cards):
    r = client.delete(f"/users/{uid}/bookmarks/{cards[0]}").json()
    assert r == {"card_id": cards[0], "bookmarked": False, "at": None, "count": 0}
    client.put(f"/users/{uid}/bookmarks/{cards[0]}")
    r = client.delete(f"/users/{uid}/bookmarks/{cards[0]}").json()
    assert r["bookmarked"] is False and r["count"] == 0
    assert client.get(f"/users/{uid}/bookmarks").json() == []


def test_목록은_최신_순이고_페이징된다(client, uid, cards):
    for cid in cards[:4]:
        client.put(f"/users/{uid}/bookmarks/{cid}")
    got = client.get(f"/users/{uid}/bookmarks").json()
    assert [c["card_id"] for c in got] == list(reversed(cards[:4]))
    assert [c["bookmarked_at"] for c in got] == sorted(
        (c["bookmarked_at"] for c in got), reverse=True)

    page1 = client.get(f"/users/{uid}/bookmarks?limit=2").json()
    page2 = client.get(f"/users/{uid}/bookmarks?limit=2&offset=2").json()
    assert [c["card_id"] for c in page1 + page2] == list(reversed(cards[:4]))
    assert client.get(f"/users/{uid}/bookmarks?limit=2&offset=99").json() == []

    # 뺐다 다시 담으면 맨 위로 온다
    client.delete(f"/users/{uid}/bookmarks/{cards[0]}")
    client.put(f"/users/{uid}/bookmarks/{cards[0]}")
    assert client.get(f"/users/{uid}/bookmarks").json()[0]["card_id"] == cards[0]


def test_목록의_카드는_카드_모양_그대로다(client, uid, cards):
    client.put(f"/users/{uid}/bookmarks/{cards[0]}")
    one = client.get(f"/users/{uid}/bookmarks").json()[0]
    assert {"card_id", "store", "image", "images", "bookmarked",
            "open_now", "bookmarked_at", "distance_m"} == set(one)
    assert one["bookmarked"] is True and one["distance_m"] is None
    assert one["store"]["category_group"] and "reviews" in one["store"]


def test_거리도_저장된_위치에서_잰다(client, uid, cards):
    client.put(f"/users/{uid}/location", json={"lat": 37.5006, "lon": 127.0366})
    client.put(f"/users/{uid}/bookmarks/{cards[0]}")
    one = client.get(f"/users/{uid}/bookmarks").json()[0]
    assert isinstance(one["distance_m"], int) and one["distance_m"] >= 0


def test_찜_수와_플래그가_다른_응답에도_붙는다(client, uid, cards):
    assert client.get(f"/users/{uid}").json()["bookmarks"] == 0
    client.put(f"/users/{uid}/bookmarks/{cards[0]}")
    assert client.get(f"/users/{uid}").json()["bookmarks"] == 1

    detail = client.get(f"/cards/{cards[0]}?user_id={uid}").json()
    assert detail["bookmarked"] is True
    assert client.get(f"/cards/{cards[0]}").json()["bookmarked"] is False   # user 없음

    # 추천 목록과 선호 목록에도 같은 플래그가 있다
    listed = client.get(f"/cards?user_id={uid}&limit=5").json()
    assert all(isinstance(c["bookmarked"], bool) for c in listed)
    client.post("/preferences", json={"user_id": uid, "card_id": cards[0],
                                      "prefer": "like"})
    liked = client.get(f"/preferences/{uid}").json()
    assert liked and liked[0]["card_id"] == cards[0] and liked[0]["bookmarked"] is True


def test_없는_사용자도_빈_목록이다(client):
    """찜한 적 없는 id 는 404 가 아니라 빈 배열이다(설정과 같은 규칙)."""
    assert client.get("/users/anon_bookmark_test/bookmarks").json() == []
    assert client.get("/users/anon_bookmark_test/bookmarks").status_code == 200


def test_user_id_는_여기서도_검증된다(client, cards):
    for bad in ("u id", "x" * 65, "사용자"):
        assert client.put(f"/users/{bad}/bookmarks/{cards[0]}").status_code == 422
        assert client.delete(f"/users/{bad}/bookmarks/{cards[0]}").status_code == 422
        assert client.get(f"/users/{bad}/bookmarks").status_code == 422


# ────────────────────────────────────────────────── 추천과의 경계
def _snapshot(user_id: str) -> dict:
    """살아 있는 두 세션과 기록 파일의 지문. 찜은 이걸 하나도 바꾸면 안 된다."""
    sess = policy.session_for(user_id)
    legacy, tst = sess["legacy"], sess["taste"]
    return {
        "legacy_m": legacy["m"].tolist(), "legacy_v": legacy["v"].tolist(),
        "legacy_shown": set(legacy["shown"]), "legacy_n": legacy["n"],
        "legacy_hist": copy.deepcopy(legacy["hist"]),
        "taste_m": tst["m"].tolist(), "taste_v": tst["v"].tolist(),
        "taste_shown": set(tst["shown"]), "taste_n": tst["n"],
        "taste_hist": copy.deepcopy(tst["hist"]),
        "like_ids": list(tst["like_ids"]), "dislike_ids": list(tst["dislike_ids"]),
        "served": tst["served"], "batch": sess["batch"],
        "exposures": (state.EXPOSURES.read_bytes() if state.EXPOSURES.exists() else b""),
        "prefs": (state.PREFS.read_bytes() if state.PREFS.exists() else b""),
    }


def test_찜은_추천을_하나도_바꾸지_않는다(client, uid, cards):
    """담고 빼고 목록을 열어도 모델·`shown`·노출 로그·선호 기록이 그대로여야 한다."""
    client.get(f"/cards?user_id={uid}&limit=3")          # 세션을 만들어 둔다
    client.post("/preferences", json={"user_id": uid, "card_id": cards[0],
                                      "prefer": "like"})
    before = _snapshot(uid)
    for cid in cards:
        client.put(f"/users/{uid}/bookmarks/{cid}")
    client.get(f"/users/{uid}/bookmarks")
    client.get(f"/cards/{cards[1]}?user_id={uid}")
    client.delete(f"/users/{uid}/bookmarks/{cards[1]}")
    after = _snapshot(uid)

    assert after["taste_shown"] == before["taste_shown"], "찜이 카드를 '봤다' 고 표시했다"
    assert after["legacy_shown"] == before["legacy_shown"]
    assert after["exposures"] == before["exposures"], "찜이 노출 로그를 남겼다"
    assert after["prefs"] == before["prefs"], "찜이 선호 기록을 남겼다"
    assert after == before


def test_찜한_카드도_추천에_다시_나올_수_있다(client, uid):
    """'찜 = 이미 본 것' 이 아니다 — 담았다고 후보에서 빠지지 않는다."""
    swipe.sessions().pop(uid, None)
    taste.sessions().pop(uid, None)
    policy.sessions().pop(uid, None)
    first = [c["card_id"] for c in client.get(f"/cards?user_id={uid}&limit=3").json()]
    for cid in first:
        client.put(f"/users/{uid}/bookmarks/{cid}")
    # 세션을 새로 시작하면(=새 사용자처럼) 같은 카드가 다시 후보다
    other = client.post("/users").json()["user_id"]
    again = [c["card_id"] for c in client.get(f"/cards?user_id={other}&limit=3").json()]
    for cid in first:
        assert client.put(f"/users/{other}/bookmarks/{cid}").status_code == 200
    assert again                                        # 추천은 평소대로 돌았다


# ────────────────────────────────────────────────── 저장소
def test_상한을_넘으면_오래된_것부터_빠진다(monkeypatch):
    monkeypatch.setattr(bookmarks, "MAX_PER_USER", 3)
    user = "u_cap_test"
    bookmarks.all_marks().pop(user, None)
    for cid in (1, 2, 3, 4):
        bookmarks.add(user, cid)
    assert [c for c, _ in bookmarks.page(user, 10)] == [4, 3, 2]   # 1 이 빠졌다
    assert bookmarks.count(user) == 3
    rows = [json.loads(x) for x in
            state.BOOKMARKS.read_text(encoding="utf-8").splitlines() if x.strip()]
    mine = [r for r in rows if r["user_id"] == user]
    # 버린 것도 파일에 남는다 — 안 남기면 재시작 때 되살아난다
    assert mine[-1] == {"user_id": user, "card_id": 1, "action": "remove",
                        "at": mine[-1]["at"]}


def test_파일에_남고_재시작하면_복원된다(client, uid, cards):
    client.put(f"/users/{uid}/bookmarks/{cards[0]}")
    client.put(f"/users/{uid}/bookmarks/{cards[1]}")
    client.delete(f"/users/{uid}/bookmarks/{cards[0]}")

    rows = [json.loads(x) for x in
            state.BOOKMARKS.read_text(encoding="utf-8").splitlines() if x.strip()]
    mine = [r for r in rows if r["user_id"] == uid]
    assert [r["action"] for r in mine] == ["add", "add", "remove"]
    assert all(r["at"] for r in mine)

    # 재시작 — 메모리를 비우고 파일만으로 되살린다
    kept = dict(bookmarks.all_marks())
    bookmarks.all_marks().clear()
    n = bookmarks.restore()
    assert n >= len(mine)
    assert [c for c, _ in bookmarks.page(uid, 10)] == [cards[1]]   # 뺀 것은 안 살아난다
    assert bookmarks.has(uid, cards[1]) and not bookmarks.has(uid, cards[0])
    bookmarks.all_marks().clear()
    bookmarks.all_marks().update(kept)                  # 다른 테스트를 위해 되돌린다


def test_복원은_순서를_지킨다():
    """같은 카드를 담았다 뺐다 다시 담으면 **마지막이 이긴다**(그리고 맨 위로 온다)."""
    user = "u_replay_test"
    bookmarks.all_marks().pop(user, None)
    for card_id, action, at in [(10, "add", "2026-09-10T10:00:00+09:00"),
                                (11, "add", "2026-09-10T11:00:00+09:00"),
                                (10, "remove", "2026-09-10T12:00:00+09:00"),
                                (10, "add", "2026-09-10T13:00:00+09:00")]:
        bookmarks.apply(user, card_id, action, at)
    assert [c for c, _ in bookmarks.page(user, 10)] == [10, 11]
    assert dict(bookmarks.page(user, 10))[10] == "2026-09-10T13:00:00+09:00"
    assert bookmarks.apply(user, 12, "이상한값", None) is False
    bookmarks.all_marks().pop(user, None)


def test_지표와_이벤트(client, uid, cards):
    from app import observe

    def total(text: str, action: str) -> int:
        for line in text.splitlines():
            if line.startswith(f'matchuri_bookmarks_total{{action="{action}"}}'):
                return int(line.rsplit(" ", 1)[1])
        return 0

    before = client.get("/metrics").text
    client.put(f"/users/{uid}/bookmarks/{cards[0]}")
    client.put(f"/users/{uid}/bookmarks/{cards[0]}")     # 멱등 호출은 세지 않는다
    client.delete(f"/users/{uid}/bookmarks/{cards[0]}")
    after = client.get("/metrics").text
    assert total(after, "add") == total(before, "add") + 1
    assert total(after, "remove") == total(before, "remove") + 1
    assert observe.inc_bookmark  # 지표 이름은 `matchuri_bookmarks_total{action}` 하나다
