# -*- coding: utf-8 -*-
"""HTTP 계약 — `/users → /cards → /preferences → /admin/api/policies`.

Step 4/5 는 **필드를 더하기만 한다.** 프론트가 쓰던 응답 모양이 그대로 남아 있는지를
여기서 잠근다. 진짜 데이터·산출물을 쓰는 통합 테스트다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import filters


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


def test_상태(client):
    body = client.get("/").json()
    for key in ("cards", "recommendable", "logs", "sessions"):     # 기존 계약
        assert key in body
    assert body["policy"] in ("legacy", "taste", "interleave")


def test_전체_흐름(client):
    created = client.post("/users", json={"name": "테스트"}).json()
    uid = created["user_id"]
    assert created["location"] is None and created["filter"] is None   # 새 필드

    cards = client.get(f"/cards?user_id={uid}&limit=10").json()
    assert len(cards) == 10
    for c in cards:
        assert {"card_id", "store", "image", "images",
                "recommendation_reason"} <= set(c)                 # 기존 계약
        assert c["recommendation_reason"]
        assert c["policy"] in ("legacy", "taste", "trend")         # 새 필드
        assert c["distance_m"] is None                             # 새 필드(위치 없음)
        assert {"category", "category_group"} <= set(c["store"])   # cat3 은 그대로 둔다
        assert c["store"]["category_group"] in filters.CAT_CHOICES  # 새 필드
    assert len({c["card_id"] for c in cards}) == 10                # 중복 없음

    body = client.post("/preferences", json={
        "user_id": uid, "card_id": cards[0]["card_id"], "prefer": "like"}).json()
    assert body["ok"] and body["count"] >= 1
    learned = body["learned"]
    for key in ("quality_weight", "top", "cats", "price", "base"):
        assert key in learned                                      # 기존 계약
    assert set(learned["policies"]) == {"legacy", "taste"}         # 새 필드
    # 무거운 둘(`hist`·`prototypes`)은 **사용자 응답에 담지 않는다**. `learn_state` 는
    # 이름대로 관리자 화면용이고, 앱은 이 값을 debugPrint 로 찍고 버린다
    # (FE/app/lib/screens/home_screen.dart — 유일한 소비처). 담았을 때 이 엔드포인트
    # 비용의 76~88% 였다(2026-09-22 실측: p50 367ms → 92ms, 응답 48KB → 3.3KB).
    # 이름 둘의 **부재만** 보면 부족하다 — 남아야 할 필드가 사라져도, 새 무거운 필드가
    # 들어와도 통과한다(2026-09-22 반박이 둘 다 실제로 보였다). 그래서 키 집합을 잠근다.
    assert set(learned["policies"]["taste"]) == {
        "policy", "available", "weights", "top", "likes", "dislikes", "swipes", "prior"}
    assert set(learned["policies"]["legacy"]) == {
        "quality_weight", "top", "cats", "price", "base"}
    assert "hist" not in learned

    for c in cards[1:6]:
        client.post("/preferences", json={"user_id": uid, "card_id": c["card_id"],
                                          "prefer": "dislike"})

    liked = client.get(f"/preferences/{uid}").json()
    assert [c["card_id"] for c in liked] == [cards[0]["card_id"]]

    # 두 번째 배치: 이미 답한 카드는 다시 나오지 않는다
    again = client.get(f"/cards?user_id={uid}&limit=10").json()
    assert not ({c["card_id"] for c in again} & {c["card_id"] for c in cards})

    user = client.get(f"/users/{uid}").json()
    assert user["swipes"] == 6 and user["likes"] == 1
    assert user["location"] is None and user["filter"] is None     # 새 필드
    assert user["learned"]["policies"]["taste"]["likes"] == 1
    # 이 경로도 무거운 둘을 안 싣는다. `POST /preferences` 와 **따로** 잠근다 —
    # 한쪽만 잠그면 다른 쪽을 되돌려도 스위트가 통과한다(2026-09-22 반박이 실제로 보였다).
    # 이 엔드포인트만으로 실측 p50 284.6ms → 15.8ms · 54,827B → 3,459B 다.
    assert set(user["learned"]["policies"]["taste"]) == {
        "policy", "available", "weights", "top", "likes", "dislikes", "swipes", "prior"}
    assert "hist" not in user["learned"]

    stats = client.get("/admin/api/policies").json()
    assert stats["exposures"] >= 20
    rows = {r["policy"]: r for r in stats["by_policy"]}
    assert rows["legacy"]["shown"] + rows["taste"]["shown"] >= 20
    assert sum(r["rated"] for r in rows.values()) >= 6
    assert stats["verdict"]

    detail = client.get(f"/admin/api/users/{uid}").json()
    for key in ("weights", "history", "swiped", "next"):           # 기존 계약
        assert key in detail
    assert detail["taste"]["available"] is True                    # 새 필드
    assert detail["taste"]["next"] and "parts" in detail["taste"]["next"][0]
    # 관리자 경로는 그 둘을 **그대로** 받는다(`learn_state` 의 detail 기본값이 True 다).
    # 사용자 응답에서 뺀 것이지 기능을 없앤 것이 아니다 — 이 줄이 그 구분을 잠근다.
    assert "prototypes" in detail["taste"] and "hist" in detail["taste"]


def test_user_id_없이도_이유가_온다(client):
    cards = client.get("/cards?limit=3").json()
    assert len(cards) == 3
    for c in cards:
        assert c["recommendation_reason"]
        assert c["policy"] is None            # 개인화가 없으면 정책도 없다


def test_filters_는_어휘를_9개_12개로_고정한다(client):
    """클라이언트가 필터 UI 를 이 응답으로 만든다 — 목록이 흔들리면 화면이 흔들린다."""
    body = client.get("/filters").json()
    assert [c["name"] for c in body["cats"]] == filters.CAT_CHOICES
    assert [t["name"] for t in body["tags"]] == list(filters.TAGS)
    assert body["within_m"] == filters.WITHIN_CHOICES
    # 어휘 검증은 **설정을 저장할 때** 한다. `GET /cards` 에는 필터 쿼리가 없으므로
    # 쿼리에 실린 값은 읽지도 않는다(422 가 아니라 그냥 무시된다).
    uid = client.post("/users").json()["user_id"]
    assert client.put(f"/users/{uid}/filter",
                      json={"cat": ["없는업종"]}).status_code == 422
    assert client.get(f"/cards?user_id={uid}&cat=없는업종&limit=2").status_code == 200


def test_카드_상세와_리뷰는_그대로다(client):
    cards = client.get("/cards?limit=1").json()
    cid = cards[0]["card_id"]
    detail = client.get(f"/cards/{cid}").json()
    assert "menus" in detail and "store" in detail
    assert detail["distance_m"] is None                            # 새 필드(위치 없음)
    assert isinstance(client.get(f"/cards/{cid}/reviews?limit=2").json(), list)
    assert client.get("/cards/999999").status_code == 404


def test_잘못된_선호는_400(client):
    assert client.post("/preferences", json={"user_id": "u", "card_id": 0,
                                             "prefer": "좋아요"}).status_code == 400
    assert client.post("/preferences", json={"user_id": "u", "card_id": 10 ** 9,
                                             "prefer": "like"}).status_code == 404
