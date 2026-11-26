"""Feasible-slate and current-utility invariants, without catalogue labels."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from .context_floor import select_context_floor
from .regularized import RegularizedTemporalTasteModel


def config(count=2):
    return SimpleNamespace(count=count, uncertainty_weight=.5)


def slate(ids):
    return {"store_ids": list(ids), "ready": True, "extra": {"unchanged": [1]}}


def never(*_):
    raise AssertionError("Invalid current witness must not invoke the full selector")


def test_actual_current_slate_remains_feasible_and_sets_each_chosen_utility_floor():
    ids = tuple("abcdef")
    mean, std = np.array([3., 2.5, 2., 1.5, 2., .5]), np.array([0., 0., 0., 0., 2., 0.])
    # Weakest conservative score is e: 2 - .5*2 = 1 (not weakest raw mean).
    current, baseline = slate("ace"), slate("fba")
    chosen = slate("edc")  # Full-history order differs from today's ranking.
    mean_before, std_before = mean.copy(), std.copy()
    inputs_before = deepcopy((current, baseline, chosen))
    seen_allowed = []

    def choose(allowed):
        seen_allowed.append(allowed)
        return chosen

    result = select_context_floor(ids, mean, std, config=config(3),
                                  full_slate=baseline, current_slate=current,
                                  select_allowed=choose, is_diverse=lambda a, b: a != b)
    assert result.used_context and result.floor == 1.
    assert result.qualified_ids == frozenset("abcde")
    assert set(current["store_ids"]) <= result.qualified_ids
    assert seen_allowed == [result.qualified_ids]
    assert result.slate["store_ids"] == ["e", "d", "c"]
    scores = dict(zip(ids, mean - .5*std))
    assert all(scores[s] >= result.floor for s in result.slate["store_ids"])
    assert (current, baseline, chosen) == inputs_before
    np.testing.assert_array_equal(mean, mean_before)
    np.testing.assert_array_equal(std, std_before)
    result.slate["extra"]["unchanged"].append(2)
    assert chosen["extra"]["unchanged"] == [1]


@pytest.mark.parametrize("count", [1, 2, 4])
def test_configured_count_and_all_negative_scores_need_no_positive_gate(count):
    ids = tuple("abcdef")
    result = select_context_floor(ids, -np.arange(1., 7.), np.zeros(6), config=config(count),
        full_slate=slate(ids[-count:]), current_slate=slate(ids[:count]),
        select_allowed=lambda allowed: slate(sorted(allowed, reverse=True)),
        is_diverse=lambda _a, _b: True)
    assert result.used_context and result.floor == -count
    assert result.qualified_ids == frozenset(ids[:count])
    assert len(result.slate["store_ids"]) == count


def test_zero_and_tied_scores_preserve_the_whole_pool_and_full_ranking():
    ids = tuple("abcdef")
    baseline = slate("fe")
    result = select_context_floor(ids, np.zeros(6), np.zeros(6), config=config(),
        full_slate=baseline, current_slate=slate("ab"),
        select_allowed=lambda _: baseline, is_diverse=lambda _a, _b: True)
    assert result.floor == 0. and result.qualified_ids == frozenset(ids)
    assert result.slate == baseline and result.used_context


@pytest.mark.parametrize("offset", [-16., 4.])
def test_a_common_score_origin_shift_changes_only_the_reported_floor(offset):
    ids = tuple("abcdef")
    mean = np.array([.75, .5, .25, .125, -.25, 0.])
    std = np.full(6, .25)
    inputs = dict(config=config(), full_slate=slate("ef"), current_slate=slate("ac"),
                  select_allowed=lambda _: slate("cb"), is_diverse=lambda _a, _b: True)
    before = select_context_floor(ids, mean, std, **inputs)
    after = select_context_floor(ids, mean + offset, std, **inputs)
    assert after.floor == before.floor + offset
    assert after.qualified_ids == before.qualified_ids
    assert after.slate == before.slate and after.used_context == before.used_context


@pytest.mark.parametrize("current_ids, conflict", [
    ([], False), (["a"], False), (["a", "a"], False),
    (["a", "outside"], False), (["a", "b"], True),
])
def test_missing_or_invalid_strict_current_witness_returns_exact_full_fallback(current_ids, conflict):
    baseline = slate("cd")
    result = select_context_floor(tuple("abcd"), np.ones(4), np.zeros(4), config=config(),
        full_slate=baseline, current_slate=slate(current_ids), select_allowed=never,
        is_diverse=lambda _a, _b: not conflict)
    assert not result.used_context
    assert result.fallback_reason == "current_selector_did_not_return_strictly_diverse_slate"
    assert result.floor is None and not result.qualified_ids
    assert result.slate == baseline
    result.slate["extra"]["unchanged"].append(2)
    assert baseline["extra"]["unchanged"] == [1]


@pytest.mark.parametrize("selected_ids", [[], ["a"], ["a", "a"], ["a", "outside"], ["a", "d"], ["b", "c"]])
def test_invalid_full_ranked_output_cannot_bypass_floor_or_diversity(selected_ids):
    # Current witness ac is feasible. Pair bc conflicts, and d is below floor.
    baseline = slate("ad")
    result = select_context_floor(tuple("abcd"), np.array([4., 3., 2., 1.]), np.zeros(4), config=config(),
        full_slate=baseline, current_slate=slate("ac"), select_allowed=lambda _: slate(selected_ids),
        is_diverse=lambda a, b: {a, b} != {"b", "c"})
    assert not result.used_context and result.floor == 2.
    assert result.qualified_ids == frozenset("abc")
    assert set("ac") <= result.qualified_ids  # Feasibility does not ensure a bounded selector finds it.
    assert result.fallback_reason == "full_ranking_did_not_return_strictly_diverse_context_slate"
    assert result.slate == baseline  # A fallback explicitly has no floor guarantee.


def test_latest_reassessment_can_change_current_witness_without_mutating_model():
    ids = tuple("abcd")
    model = RegularizedTemporalTasteModel(ids=ids, gram=np.eye(4))
    chosen = []
    for label in (True, False):
        model.record("a", label)
        prediction = model.predict()
        score = prediction.mean - .5*prediction.latent_std
        current_rank = sorted(ids, key=lambda s: (-score[ids.index(s)], s))
        before = model.snapshot()
        result = select_context_floor(ids, prediction.mean, prediction.latent_std, config=config(1),
            full_slate=slate("a"), current_slate=slate(current_rank[:1]),
            select_allowed=lambda allowed: slate([s for s in ids if s in allowed][:1]),
            is_diverse=never)
        assert result.used_context
        chosen.append(result.slate["store_ids"][0])
        assert model.snapshot() == before
        np.testing.assert_array_equal(model.predict().mean, prediction.mean)
        np.testing.assert_array_equal(model.predict().latent_std, prediction.latent_std)
    assert model.clock == 2 and len(model.evidence) == 1
    assert chosen[0] == "a" and chosen[1] != "a"


@pytest.mark.parametrize("mean, std", [
    ([np.nan, 1.], [0., 0.]), ([1., 2.], [0., -.5]),
    ([1., np.inf], [0., 0.]), ([1., 2.], [0.]),
])
def test_invalid_prediction_inputs_are_rejected(mean, std):
    with pytest.raises(ValueError, match="prediction"):
        select_context_floor(tuple("ab"), mean, std, config=config(),
            full_slate=slate("ab"), current_slate=slate("ab"),
            select_allowed=never, is_diverse=never)


def test_invalid_fallback_is_not_silently_returned():
    with pytest.raises(ValueError, match="Full fallback"):
        select_context_floor(tuple("ab"), np.ones(2), np.zeros(2), config=config(),
            full_slate=slate(["outside"]), current_slate=slate([]),
            select_allowed=never, is_diverse=never)
