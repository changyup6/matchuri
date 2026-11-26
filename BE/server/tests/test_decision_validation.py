"""검증 도구 자체의 검증: 앱 선조회 순서, 고정 반응, HTTP와 라우터의 일치."""
import importlib.util
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from recsys import policy, swipe, taste

spec = importlib.util.spec_from_file_location(
    "decision_validation", Path(__file__).resolve().parents[2] / "analysis/decision_validation.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def test_frozen_response_bank_is_order_independent():
    ids = ["first", "second", "third"]
    first = {s: validation.uniform("korean", 10, s) for s in ids}
    again = {s: validation.uniform("korean", 10, s) for s in reversed(ids)}
    assert first == again
    assert first != {s: validation.uniform("korean", 11, s) for s in ids}
    assert all(0 < x < 1 for x in first.values())


def test_actual_app_prefetch_is_sixty_not_ten():
    assert validation.protocol_batches("app_10_plus_50") == [10, 50]
    assert sum(validation.protocol_batches("app_10_plus_50")) == 60
    assert validation.protocol_batches("batch_10") == [10]


def test_summary_does_not_drop_nontriggers_or_use_future_good_proposals():
    rows = []
    for at, fit in [(None, None), (20, 0), (30, 4)]:
        rows.append({"family": "korean", "flow": "app_10_plus_50",
                     "first_at": {v: at for v in validation.CONFIGS},
                     "first_quality": {v: {"fit_count": fit} if at else None for v in validation.CONFIGS}})
    for row in validation.summary(rows):
        assert row["n"] == 3 and row["at20"] == 1 and row["at30"] == 2
        assert row["all4_fit_at30"] == 1 and row["none_fit_at30"] == 1


def _reset(uid, seed):
    policy._sessions.pop(uid, None)
    swipe._sessions.pop(uid, None)
    taste._sessions.pop(uid, None)
    sess = policy.session_for(uid)
    for name in ("legacy", "taste"):
        sess[name]["rng"] = np.random.default_rng(seed)
    return sess


def test_router_protocol_matches_real_http_prefetch_and_feedback():
    import main
    client = TestClient(main.app)
    uid, seed = "validation-protocol-parity", 761
    sess = _reset(uid, seed)
    direct = []
    for size in [10, 50]:
        direct.extend(policy.pick_many(sess, size))
    for i, sid in enumerate(direct[:20]):
        policy.record(sess, sid, i % 3 != 0)
    expected = {name: (sess[name]["m"].copy(), sess[name]["v"].copy(), sess[name]["shown"].copy())
                for name in ("legacy", "taste")}
    hist = sess["taste"]["hist"].copy()

    sess = _reset(uid, seed)
    http = []
    for size in [10, 50]:
        response = client.get(f"/cards?user_id={uid}&limit={size}")
        assert response.status_code == 200
        http.extend(response.json())
    assert [c["store"]["id"] for c in http] == direct
    for i, card in enumerate(http[:20]):
        response = client.post("/preferences?include_decision=true", json={
            "user_id": uid, "card_id": card["card_id"], "prefer": "like" if i % 3 != 0 else "dislike"})
        assert response.status_code == 200
    for name in ("legacy", "taste"):
        np.testing.assert_array_equal(sess[name]["m"], expected[name][0])
        np.testing.assert_array_equal(sess[name]["v"], expected[name][1])
        assert sess[name]["shown"] == expected[name][2]
    assert sess["taste"]["hist"] == hist
