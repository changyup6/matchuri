# -*- coding: utf-8 -*-
"""사람 id 로 취향을 물으면 그 사람의 세션을 이어 붙여 한 번에 준다.

왜 필요한가. 앱은 "내 취향" 한 장을 그리려고 `/users/{id}/taste` 를 먼저 부르고, 404 가 오면
**세션 수만큼** 세션별 취향을 하나씩 받아다 직접 합친다(FE/app/lib/services/taste_api.dart).
앱을 켤 때마다 세션이 하나씩 늘어 92개가 된 사용자가 있었고, 그 요청 묶음이 요청 제한에 걸려
화면이 실패했다(2026-09-20 21:38·23:57). 여기서 합쳐 주면 그 되받기가 일어나지 않는다.

여기서 잠그는 것 셋.
  ① 세션 id 로 물으면 **예전 그대로** 그 세션 하나만 본다.
  ② 사람 id 로 물으면 세션들을 `created` 순으로 이어 붙인다 — 스와이프·좋아요는 합, 궤적은
     시간순, `n` 은 누적.
  ③ 나중 세션의 평가가 이긴다 — 옛 세션에서 좋아요한 가게를 나중에 싫어요하면 앵커에서 빠진다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import identities, loader, taste_api, taste_hierarchy
from recsys import taste
from conftest import IDS, NAMES
from test_taste_api import FAKE_CARDS, FAKE_LAYOUT

PERSON = "usr_" + "a" * 24


@pytest.fixture
def client() -> TestClient:
    import main
    return TestClient(main.app)


@pytest.fixture
def lay(monkeypatch, art):
    folded = taste_api._fold(FAKE_LAYOUT)
    monkeypatch.setattr(taste_api, "_load_layout", lambda: folded)
    monkeypatch.setattr(taste_hierarchy, "read_level", lambda detail=1: {
        "level": detail, "nodes": [
            {**region, "key": taste_hierarchy._key("cat2", region["name"]), "card_id": None}
            for region in folded["regions"]]})
    monkeypatch.setattr(taste_api, "REGION_K", 3)
    monkeypatch.setattr(loader, "card_id_by_store", dict(FAKE_CARDS))
    return folded


def _session(created: float, feedback: list[tuple[str, bool]]) -> dict:
    sess = taste.replay(feedback)          # 등록하지 않고 만들어 먹인다
    sess["created"] = created
    return sess


@pytest.fixture
def person(monkeypatch):
    """세션 둘을 가진 사람. 옛 세션이 먼저다(created 가 작다)."""
    old = _session(100.0, [("s0", True), ("s1", True)])
    new = _session(200.0, [("s2", True), ("s0", False)])
    live = {"s_old": old, "s_new": new}
    monkeypatch.setattr(taste, "sessions", lambda: live)
    monkeypatch.setattr(identities, "owned", lambda uid: (
        [{"user_id": PERSON, "session_id": "s_old"}, {"user_id": PERSON, "session_id": "s_new"}]
        if uid == PERSON else []))
    return live


def test_세션_id_는_예전처럼_그_세션만_본다(client, lay, person):
    d = client.get("/users/s_old/taste").json()
    assert (d["swipes"], d["likes"]) == (2, 2)
    assert [p["store"] for p in d["trail"]] == [NAMES["s0"], NAMES["s1"]]


def test_사람_id_는_세션을_이어_붙인다(client, lay, person):
    d = client.get(f"/users/{PERSON}/taste").json()
    assert (d["swipes"], d["likes"]) == (4, 3)          # 2+2 스와이프 · 2+1 좋아요
    assert [p["store"] for p in d["trail"]] == [NAMES["s0"], NAMES["s1"], NAMES["s2"]]
    assert [p["n"] for p in d["trail"]] == [1, 2, 3]     # 누적 번호 — 뒤로만 간다
    assert d["user_id"] == PERSON


def test_나중_세션의_평가가_이긴다(client, lay, person):
    """옛 세션에서 좋아요한 s0 를 나중 세션에서 싫어요했다 — 현재 점의 앵커에서 빠진다."""
    d = client.get(f"/users/{PERSON}/taste").json()
    near = [n["name"] for n in d["now"]["near"]]
    assert NAMES["s0"] not in near
    assert set(near) <= {NAMES["s1"], NAMES["s2"]}


def test_세션이_하나도_없으면_404(client, lay, monkeypatch):
    monkeypatch.setattr(identities, "owned", lambda uid: [])
    monkeypatch.setattr(taste, "sessions", lambda: {})
    assert client.get(f"/users/{PERSON}/taste").status_code == 404


def test_합치기는_아무것도_바꾸지_않는다(client, lay, person):
    """읽기 전용이다 — 세션 상태(학습·이력)가 호출 전후로 같아야 한다."""
    before = {k: (v["n"], v["likes"], len(v["hist"]), list(v["like_ids"])) for k, v in person.items()}
    client.get(f"/users/{PERSON}/taste")
    after = {k: (v["n"], v["likes"], len(v["hist"]), list(v["like_ids"])) for k, v in person.items()}
    assert before == after
