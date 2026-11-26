"""Inherited legacy evidence must affect the first card without faking progress."""
import copy

import numpy as np
import pytest

from recsys import swipe


class MeanSampler:
    def normal(self, mean, _scale):
        return mean.copy()


@pytest.fixture
def legacy_pool(monkeypatch):
    ids = ["quality_first", "prior_preferred", "other"]
    cats = ["한식", "일식", "중식"]
    qz = np.array([1.0, 0.8, 0.2])
    features = np.zeros((len(ids), 3 + len(swipe.CATS) + 1 + 5))
    features[:, 0] = 1
    features[:, 1] = qz
    for i, category in enumerate(cats):
        features[i, 3 + swipe.CATS.index(category)] = 1
    pool = {"ids": ids, "pos": {sid: i for i, sid in enumerate(ids)},
            "cat": dict(zip(ids, cats)), "qz": qz, "X": features}
    monkeypatch.setattr(swipe, "pool", lambda: pool)
    return pool


def inherited_session():
    sess = swipe._new_session()
    sess["m"][3 + swipe.CATS.index("한식")] = -0.5
    sess["m"][3 + swipe.CATS.index("일식")] = 1.2
    sess["prior_m"] = sess["m"].copy()
    sess["rng"] = MeanSampler()
    return sess


def test_inherited_prior_changes_first_card_and_peek_without_faking_feedback(legacy_pool):
    fresh = swipe._new_session()
    assert swipe._pick(fresh) == "quality_first"
    sess = inherited_session()
    before = copy.deepcopy(sess)
    preview = swipe.peek(sess, 3)
    assert swipe._pick(sess) == preview[0]["store_id"] == "prior_preferred"
    by_id = {row["store_id"]: row for row in preview}
    assert by_id["prior_preferred"] == {
        "store_id": "prior_preferred", "quality": 0.8, "personal": 1.2, "total": 2.0}
    assert by_id["quality_first"]["personal"] == -0.5
    assert sess["n"] == sess["likes"] == 0
    assert sess["hist"] == [] and sess["shown"] == set()
    for key in ("m", "v", "prior_m"):
        np.testing.assert_array_equal(sess[key], before[key])


@pytest.mark.parametrize("n", [0, 1, 15, 60])
@pytest.mark.parametrize("zero_prior", [False, True])
def test_no_inherited_evidence_preserves_original_scores(legacy_pool, n, zero_prior):
    sess = swipe._new_session()
    sess["n"] = n
    sess["m"] = np.linspace(-0.4, 0.6, len(sess["m"]))
    sess["rng"] = MeanSampler()
    if zero_prior:
        sess["prior_m"] = np.zeros_like(sess["m"])
    weight = 1 / (1 + n / 15)
    personal = (1 - weight) * (legacy_pool["X"] @ sess["m"])
    quality = weight * legacy_pool["qz"]
    total = quality + personal
    expected = [{"store_id": legacy_pool["ids"][i],
                 "quality": round(float(quality[i]), 3),
                 "personal": round(float(personal[i]), 3),
                 "total": round(float(total[i]), 3)} for i in np.argsort(-total)]
    assert swipe.peek(sess, 3) == expected
    assert swipe._pick(sess) == expected[0]["store_id"]


def test_current_learning_can_override_the_inherited_preference(legacy_pool):
    sess = inherited_session()
    assert swipe._pick(sess) == "prior_preferred"
    sess["n"] = 15
    sess["m"][3 + swipe.CATS.index("한식")] = 0.5
    sess["m"][3 + swipe.CATS.index("일식")] = -3.0
    preview = swipe.peek(sess, 3)
    assert swipe._pick(sess) == preview[0]["store_id"] == "quality_first"
    by_id = {row["store_id"]: row for row in preview}
    assert by_id["prior_preferred"]["personal"] == -0.9
    assert by_id["quality_first"]["personal"] == 0
    for row in preview:
        assert row["quality"] + row["personal"] == pytest.approx(row["total"])


def test_first_card_reason_uses_actual_prior_contribution(legacy_pool):
    sess = inherited_session()
    reasons = {}
    assert swipe._pick(sess, reasons=reasons) == "prior_preferred"
    assert reasons["prior_preferred"] == "지금까지의 취향과 잘 맞는 일식 가게예요."
    assert swipe.recommendation_reason(sess, "prior_preferred") == reasons["prior_preferred"]
    assert swipe.recommendation_reason(swipe._new_session(), "prior_preferred") == \
        "평점과 리뷰 수를 함께 고려해 골랐어요."


def test_sampling_cannot_make_an_overridden_prior_sound_like_a_preference(legacy_pool):
    sess = inherited_session()
    sess["n"] = 15
    category = 3 + swipe.CATS.index("일식")
    sess["m"][category] = -3.0
    sampled = sess["m"].copy()
    sampled[category] = 3.0
    reason = swipe.recommendation_reason(sess, "prior_preferred", sampled_weights=sampled)
    assert reason == "평점과 리뷰 수를 함께 고려해 골랐어요."
