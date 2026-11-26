# -*- coding: utf-8 -*-
"""`/admin/api/trend/slot` — 트렌드 슬롯이 내보낸 카드를 **카드 단위**로 본다.

`/admin/api/policies` 의 `slot` 은 집계다. 실험 초반에 "실제로 무엇이 나갔나" 를 눈으로 보려면
카드 목록이 필요하다. 잠그는 것: ① `trend` 줄만 고른다 ② 선호와 (사용자·세션·카드)로 조인해
`prefer` 가 붙는다 ③ 재노출은 한 번만 ④ 기록이 없으면 빈 목록이고 오류가 아니다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import state as app_state
    monkeypatch.setattr(app_state, "DIR", tmp_path)
    monkeypatch.setattr(app_state, "EXPOSURES", tmp_path / "exposures.jsonl")
    monkeypatch.setattr(app_state, "PREFS", tmp_path / "preferences.jsonl")
    import main
    return TestClient(main.app), app_state


def test_기록이_없으면_빈_목록(client):
    c, _ = client
    body = c.get("/admin/api/trend/slot").json()
    assert body["shown"] == 0 and body["cards"] == [] and body["by_store"] == []
    assert "slot_every" in body


def test_트렌드_줄만_골라_선호와_조인한다(client):
    c, st = client
    st.record_exposure("u1", 1, "taste", 0, 0)          # 트렌드 아님 — 빠져야 한다
    st.record_exposure("u1", 2, "trend", 0, 3)
    st.record_exposure("u1", 2, "trend", 1, 5)          # 같은 카드 재노출 — 한 번만
    st.record_exposure("u2", 3, "trend", 0, 7)
    st.record_preference("u1", 2, "like")
    st.record_preference("u1", 1, "like")               # 트렌드 카드가 아니라 집계에 안 든다

    body = c.get("/admin/api/trend/slot").json()
    assert body["shown"] == 2 and body["liked"] == 1 and body["disliked"] == 0 and body["unrated"] == 1
    assert body["users"] == 2
    cards = {r["card_id"]: r for r in body["cards"]}
    assert set(cards) == {2, 3}
    assert cards[2]["prefer"] == "like" and cards[3]["prefer"] is None
    assert cards[2]["rank"] == 3                          # 첫 노출의 자리
    for r in body["cards"]:
        assert {"at", "user_id", "store_id", "name", "category", "menu", "trend_raw"} <= set(r)
    assert sum(a["shown"] for a in body["by_store"]) == 2


def test_창과_개수를_자를_수_있다(client):
    c, st = client
    for i in range(5):
        st.record_exposure("u1", 10 + i, "trend", 0, i)
    body = c.get("/admin/api/trend/slot?limit=2").json()
    assert body["shown"] == 5 and len(body["cards"]) == 2   # 집계는 전체, 목록만 잘린다
    assert c.get("/admin/api/trend/slot?days=0").json()["days"] == 0
    assert c.get("/admin/api/trend/slot?days=-1").status_code == 422
