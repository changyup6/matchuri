"""Eligibility origin changes; no food labels or scenario outcomes involved."""
from types import SimpleNamespace

import numpy as np
import pytest

from .context import select_context
from .context_bench import context_origin_mean
from .diagnostics import latent_state
from .model import SpectralProfiles
from .regularized import RegularizedTemporalTasteModel


def configuration():
    return SimpleNamespace(count=2, fit_quantile=.75, uncertainty_weight=.5)


def select_with_full_rank(mean, intercept, origin):
    ids = tuple("abcdef")
    # Full-history order deliberately differs from today's a > b > c order.
    full_rank = tuple("cbafde")
    return select_context(
        ids, context_origin_mean(mean, intercept, origin), np.full(6, .25), ["a"],
        config=configuration(), full_slate={"store_ids": ["a", "f"]},
        select_allowed=lambda allowed: {"store_ids": [s for s in full_rank if s in allowed][:2]},
        is_diverse=lambda _a, _b: True,
    )


def test_negative_common_intercept_does_not_erase_positive_relative_preferences():
    relative = np.array([.75, .5, .25, .125, -.25, 0.])
    intercept = -.75
    mean = relative + intercept
    absolute = select_with_full_rank(mean, intercept, "absolute")
    centered = select_with_full_rank(mean, intercept, "intercept")
    assert not absolute.qualified_ids and not absolute.used_context
    assert absolute.slate["store_ids"] == ["a", "f"]
    assert centered.qualified_ids == frozenset("abc")
    assert centered.used_context
    # The filter must not replace the original full-history candidate order.
    assert centered.slate["store_ids"] == ["c", "b"]
    assert centered.cutoff == absolute.cutoff - intercept


@pytest.mark.parametrize("offset", [-8., -1., 2., 16.])
def test_centered_eligibility_is_invariant_to_a_joint_reporting_origin_shift(offset):
    # This is a change to the representation of already computed predictions,
    # not a claim that refitting a proper-prior GP to shifted labels is invariant.
    intercept = -.75
    mean = np.array([.75, .5, .25, .125, -.25, 0.]) + intercept
    reference = select_with_full_rank(mean, intercept, "intercept")
    translated = select_with_full_rank(mean + offset, intercept + offset, "intercept")
    assert translated.qualified_ids == reference.qualified_ids
    assert translated.cutoff == reference.cutoff
    assert translated.slate == reference.slate
    assert translated.used_context == reference.used_context
    assert translated.fallback_reason == reference.fallback_reason


def test_only_the_positive_origin_gate_changes_not_ranking_or_upper_quartile():
    mean = np.array([-.75, -.5, -.25, .125, .25, .5])
    std = np.array([.5, .25, .5, .125, .25, .5])
    intercept = -.375
    relative = context_origin_mean(mean, intercept, "intercept")
    original_scores, relative_scores = mean-.5*std, relative-.5*std
    reference = [1, 2, 3, 4, 5]  # Only today's observed store is excluded.
    original_cutoff = np.quantile(original_scores[reference], .75)
    relative_cutoff = np.quantile(relative_scores[reference], .75)
    np.testing.assert_array_equal(np.argsort(original_scores), np.argsort(relative_scores))
    np.testing.assert_array_equal(original_scores >= original_cutoff, relative_scores >= relative_cutoff)
    assert relative_cutoff == original_cutoff - intercept


def test_relative_mean_is_the_actual_latent_vector_projection_without_model_mutation():
    ids = tuple(str(i) for i in range(9))
    x = np.random.default_rng(73).normal(size=(len(ids), 5))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    profiles = SpectralProfiles.from_arrays(ids, {"menu": x @ x.T})
    model = RegularizedTemporalTasteModel(profiles)
    for sid, like in [("0", True), ("1", False), ("2", False), ("3", False), ("0", False)]:
        model.record(sid, like)
    before = model.snapshot()
    prediction = model.predict()
    mean, std = prediction.mean.copy(), prediction.latent_std.copy()
    state = latent_state(model, profiles)
    relative = context_origin_mean(prediction.mean, model._solve().intercept, "intercept")
    np.testing.assert_allclose(relative, profiles.x @ state.total, atol=1e-12, rtol=0)
    assert model.snapshot() == before
    np.testing.assert_array_equal(prediction.mean, mean)
    np.testing.assert_array_equal(prediction.latent_std, std)
    np.testing.assert_array_equal(model.predict().mean, mean)
    np.testing.assert_array_equal(model.predict().latent_std, std)
    np.testing.assert_array_equal(latent_state(model, profiles).total, state.total)


def test_absolute_option_is_an_exact_detached_copy_of_frozen_policy_input():
    mean = np.array([-.7, .3, .2])
    output = context_origin_mean(mean, -.2, "absolute")
    np.testing.assert_array_equal(output, mean)
    output[0] = 123.
    assert mean[0] == -.7


def test_cold_prior_does_not_become_evidence_after_recentring():
    ids = tuple("abcd")
    model = RegularizedTemporalTasteModel(ids=ids, gram=np.eye(4))
    p = model.predict()
    origin = context_origin_mean(p.mean, model._solve().intercept, "intercept")

    def unexpected_call(*_):
        raise AssertionError("No-evidence fallback must not call selector/diversity")

    baseline = {"store_ids": ["a", "b"]}
    selected = select_context(ids, origin, p.latent_std, [], config=configuration(),
                              full_slate=baseline, select_allowed=unexpected_call,
                              is_diverse=unexpected_call)
    assert not selected.qualified_ids
    assert selected.slate == baseline
    assert selected.fallback_reason == "no_current_evidence"


@pytest.mark.parametrize("mean, intercept, origin", [
    ([np.nan, 1.], 0., "intercept"),
    ([0., 1.], np.inf, "intercept"),
    ([0., 1.], np.nan, "absolute"),
    ([0., 1.], 0., "unknown"),
])
def test_invalid_origin_inputs_are_rejected(mean, intercept, origin):
    with pytest.raises(ValueError):
        context_origin_mean(mean, intercept, origin)
