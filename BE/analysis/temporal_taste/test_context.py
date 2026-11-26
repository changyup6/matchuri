from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from .context import select_context
from .regularized import RegularizedTemporalTasteModel


def config(**updates):
    return SimpleNamespace(**{"count": 4, "fit_quantile": .75,
                             "uncertainty_weight": .5, **updates})


def full(ids=("a", "b", "c", "d")):
    return {"store_ids": list(ids), "ready": True, "status": "ready",
            "qualified_count": 17, "extra": {"preserve": [1, 2]}}


def never_called(*_):
    raise AssertionError("Fallback must not call a selector or a diversity check")


def test_no_current_evidence_preserves_full_slate_even_with_positive_prior_scores():
    original = full()
    result = select_context(list("abcd"), np.ones(4), np.zeros(4), [],
                            config=config(), full_slate=original,
                            select_allowed=never_called, is_diverse=never_called)
    assert not result.used_context and result.fallback_reason == "no_current_evidence"
    assert result.slate == original and result.slate is not original
    result.slate["extra"]["preserve"].append(3)
    assert original["extra"]["preserve"] == [1, 2]


@pytest.mark.parametrize("catalogue, baseline, observed", [
    (list("abcd"), full(), ["a"]),
    (list("ab"), {"store_ids": [], "status": "insufficient_candidates"}, ["a"]),
])
def test_insufficient_context_or_catalogue_falls_back_without_partial_slate(catalogue, baseline, observed):
    values = np.arange(1., len(catalogue)+1)
    result = select_context(catalogue, values, np.zeros(len(catalogue)), observed,
                            config=config(), full_slate=baseline,
                            select_allowed=never_called, is_diverse=never_called)
    assert not result.used_context
    assert result.fallback_reason == "fewer_than_requested_current_qualified"
    assert result.slate == baseline


@pytest.mark.parametrize("selection, conflicting_pair", [
    (["a", "b", "c"], None),
    (["a", "b", "c", "d"], frozenset(("b", "d"))),
])
def test_short_or_nondiverse_context_selection_returns_exact_full_result(selection, conflicting_pair):
    original = full(("e", "f", "g", "h"))
    result = select_context(list("abcdefgh"), np.ones(8), np.zeros(8), ["a"],
                            config=config(), full_slate=original,
                            select_allowed=lambda _: full(selection),
                            is_diverse=lambda a, b: frozenset((a, b)) != conflicting_pair)
    assert not result.used_context
    assert result.fallback_reason == "context_selector_did_not_return_strictly_diverse_slate"
    assert result.slate == original


def test_configured_count_uncertainty_quantile_and_full_ranking_are_preserved_without_mutation():
    ids = ["current", "prior_only", "uncertain", "new_a", "new_b"]
    mean = np.array([.8, .9, 10., .4, .2])
    std = np.array([0., 0., 20., 0., 0.])
    before_mean, before_std = mean.copy(), std.copy()
    params = config(count=2)
    baseline = full(("new_a", "new_b"))
    original = deepcopy(baseline)
    # Full-history ranking can disagree with current-only ranking (.8 < .9).
    # Eligibility must not reorder the full-history selector's answer.
    selected = full(("current", "prior_only"))
    calls, pairs = [], []

    def choose(allowed):
        calls.append(allowed)
        return selected  # The injected selector's full-history order is kept.

    def diverse(a, b):
        pairs.append((a, b))
        return True

    result = select_context(ids, mean, std, ["current"], config=params,
                            full_slate=baseline, select_allowed=choose, is_diverse=diverse)
    # Reference scores [.9, 0, .4, .2] have upper quartile .525.
    assert result.cutoff == pytest.approx(.525)
    assert calls == [frozenset(("current", "prior_only"))]
    assert pairs == [("current", "prior_only")]
    assert result.used_context and result.fallback_reason is None
    assert result.slate == selected and result.slate is not selected
    assert result.slate["store_ids"] == ["current", "prior_only"]
    assert baseline == original and vars(params) == vars(config(count=2))
    np.testing.assert_array_equal(mean, before_mean)
    np.testing.assert_array_equal(std, before_std)


def test_only_current_observations_leave_quantile_reference_prior_only_stores_stay():
    # The very high score of an earlier-session store must participate in the
    # reference population, not disappear as though it was rated today.
    result = select_context(["today", "earlier_session", "new_a", "new_b"],
                            np.array([10., 100., 1., 3.]), np.zeros(4), ["today", "today"],
                            config=config(count=1), full_slate=full(("today",)),
                            select_allowed=lambda allowed: full(sorted(allowed)),
                            is_diverse=never_called)
    assert result.cutoff == 51.5
    assert result.qualified_ids == frozenset(("earlier_session",))
    assert result.slate["store_ids"] == ["earlier_session"]


def test_all_rated_reference_falls_back_to_catalogue_and_zero_mean_is_not_positive():
    result = select_context(list("ab"), np.array([0., 1.]), np.zeros(2), list("ab"),
                            config=config(count=1, fit_quantile=0.), full_slate=full(("a",)),
                            select_allowed=lambda allowed: full(sorted(allowed)), is_diverse=never_called)
    assert result.cutoff == 0
    assert result.qualified_ids == frozenset(("b",))
    assert result.slate["store_ids"] == ["b"]


def test_current_reassessment_uses_latest_posterior_without_a_session_reset():
    ids = list("abcd")
    model = RegularizedTemporalTasteModel(ids=ids, gram=np.eye(4))
    model.record("a", True)
    prediction = model.predict()
    positive = select_context(ids, prediction.mean, prediction.latent_std, list(model.evidence),
                              config=config(count=1), full_slate=full(("d",)),
                              select_allowed=lambda _: full(("a",)), is_diverse=never_called)
    assert positive.used_context and "a" in positive.qualified_ids
    model.record("a", False)
    prediction = model.predict()
    negative = select_context(ids, prediction.mean, prediction.latent_std, list(model.evidence),
                              config=config(count=1), full_slate=full(("d",)),
                              select_allowed=never_called, is_diverse=never_called)
    assert model.clock == 2 and len(model.evidence) == 1
    assert not negative.used_context and not negative.qualified_ids
    assert negative.slate["store_ids"] == ["d"]


@pytest.mark.parametrize("mean, std", [
    ([np.nan, 1.], [0., 0.]),
    ([1., np.inf], [0., 0.]),
    ([1., 2.], [0., -1.]),
    ([1., 2.], [0., np.nan]),
    ([[1., 2.]], [0., 0.]),
    ([1., 2.], [0.]),
])
def test_invalid_predictions_are_rejected_before_selection(mean, std):
    with pytest.raises(ValueError, match="prediction"):
        select_context(list("ab"), mean, std, ["a"], config=config(count=1),
                       full_slate=full(("b",)), select_allowed=never_called, is_diverse=never_called)


@pytest.mark.parametrize("parameters", [
    {"count": 0}, {"count": True}, {"count": 1.5},
    {"fit_quantile": -1.}, {"fit_quantile": 1.1}, {"fit_quantile": np.nan},
    {"uncertainty_weight": -.5}, {"uncertainty_weight": np.inf},
])
def test_invalid_configuration_is_rejected(parameters):
    with pytest.raises(ValueError):
        select_context(list("ab"), np.ones(2), np.zeros(2), ["a"], config=config(**parameters),
                       full_slate=full(("a",)), select_allowed=never_called, is_diverse=never_called)


def test_callback_cannot_escape_eligible_region():
    with pytest.raises(ValueError, match="outside the allowed context"):
        select_context(list("abc"), np.array([.1, 1., 2.]), np.zeros(3), ["a"],
                       config=config(count=1), full_slate=full(("b",)),
                       select_allowed=lambda _: full(("a",)), is_diverse=never_called)


@pytest.mark.parametrize("ids, observed, baseline", [
    (["a", "a"], ["a"], full(("a",))),
    (list("ab"), ["outside"], full(("a",))),
    (list("ab"), ["a"], full(("a", "a"))),
    (list("ab"), ["a"], full(("outside",))),
])
def test_unknown_or_duplicate_identity_is_rejected(ids, observed, baseline):
    with pytest.raises(ValueError):
        select_context(ids, np.ones(len(ids)), np.zeros(len(ids)), observed,
                       config=config(count=1), full_slate=baseline,
                       select_allowed=never_called, is_diverse=never_called)
