# -*- coding: utf-8 -*-
"""`attribution` — 카드가 뽑힌 이유를 **숫자**로 준다.

잠그는 것: ① 세 정책 모두 자기 틀로 채운다 ② `dominant` 는 `parts` 안의 항 하나(또는 explore·cold)
③ `user_id` 없으면 `null` ④ 설명을 붙여도 **뽑히는 카드는 바뀌지 않는다**(재료만 꺼낸다).
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from recsys import content, policy, swipe, taste, trend


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


def test_익명이면_null(client):
    for c in client.get("/cards?limit=3&reviews=0").json():
        assert c["attribution"] is None


def test_정책마다_자기_틀로_채운다(client, monkeypatch):
    monkeypatch.setenv(trend.SLOT_ENV, "5")           # 슬롯 카드도 한 장 보고 싶다
    uid = client.post("/users", json={"name": "attr"}).json()["user_id"]
    cards = client.get(f"/cards?user_id={uid}&limit=10&reviews=0").json()
    assert len(cards) == 10
    seen = set()
    for c in cards:
        a = c["attribution"]
        assert a is not None and a["policy"] == c["policy"]
        seen.add(a["policy"])
        parts = a["parts"]
        if a["policy"] == "trend":
            assert set(parts) == {"trend", "menu", "context"}
            assert a["dominant"] in set(parts["trend"]) | {"season"}
        else:
            assert {"personal", "reputation", "context", "explore", "cold"} <= set(parts)
            assert set(parts["context"]) == {"distance", "trend", "tag_tier"}
            assert parts["context"]["trend"] == 0.0        # 무게 0 — 슬롯 아니면 트렌드는 안 밀었다
            keys = set(parts["personal"]) | set(parts["reputation"]) | {"distance", "trend", "explore", "cold"}
            assert a["dominant"] in keys
            for v in list(parts["personal"].values()) + list(parts["reputation"].values()):
                assert v != 0                              # 0 인 항은 싣지 않는다
    assert {"legacy", "taste", "trend"} <= seen


def test_콜드스타트는_평판이_지배한다(client):
    """스와이프 0 인 taste 카드는 평판(`pop` 인기 · `quality` 평점)이 dominant 여야 한다 — 진단 문서가 말한 그대로.

    좋아요가 생기기 전에는 개인화 성분(공방문·메뉴·요약문·리뷰·음식사진·기피)이 0 이라 평판만 남는다. Step 3 사전값은
    인기에 1.0(눈금의 기준)을 줘서 인기 하나가 이겼고, 실제 스와이프로 배운 Step 4 사전값은 인기 0.07 · 평점 0.06 이라
    둘이 나눠 이긴다(`analysis/backtest/results/step4-real-swipes.md`).
    """
    uid = client.post("/users", json={"name": "cold"}).json()["user_id"]
    cards = [c for c in client.get(f"/cards?user_id={uid}&limit=10&reviews=0").json()
             if c["policy"] == "taste" and not c["attribution"]["parts"]["explore"]]
    assert cards
    doms = [c["attribution"]["dominant"] for c in cards]
    assert sum(d in ("pop", "quality") for d in doms) >= len(doms) * 0.6
    assert all(c["attribution"]["parts"]["cold"] for c in cards)


def test_legacy_기여는_항별로_갈린다(client):
    """실제 풀(합성 아님) 위에서 — 특성 배치(절편·평점·리뷰수·업종·가격)를 그대로 읽어야 한다."""
    sess = swipe.session_for("u_attr_legacy")
    sid = swipe.pool()["ids"][0]
    got = swipe.contributions(sess, sid)
    assert set(got) == {"quality", "reviews", "category", "price", "cold_weight"}
    assert got["cold_weight"] == pytest.approx(1.0)      # 스와이프 0 → 평점이 다 정한다


def test_trend_breakdown_은_scores_의_분해다(monkeypatch):
    ids = ["a", "b", "c"]
    fake = {"a": {"season": 1.0, "fad": 0.2}, "b": {"season": -0.5}, "c": {}}
    monkeypatch.setattr(trend, "breakdown", lambda ids_: {s: dict(fake[s]) for s in ids_})
    z = trend.scores(ids)
    assert z == {"a": 1.2, "b": -0.5, "c": 0.0}


def test_설명을_붙여도_뽑히는_카드는_같다(art, monkeypatch):
    """`explain` 은 재료를 꺼내기만 한다 — 난수 소비도 순서도 그대로여야 한다."""
    taste._sessions.clear()
    s1 = taste.session_for("u_same_1"); s2 = taste.session_for("u_same_2")
    s2["rng"] = np.random.default_rng(1); s1["rng"] = np.random.default_rng(1)
    ex: dict = {}
    a = taste.pick_many(s1, 3)
    b = taste.pick_many(s2, 3, explain=ex)
    assert a == b and set(ex) == set(b)
    for sid in b:
        assert set(ex[sid]["components"]) == set(content.COMPONENTS)
