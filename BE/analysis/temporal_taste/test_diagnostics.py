import numpy as np
import pytest

from .diagnostics import geometry, latent_state, reference_contrast
from .model import SpectralProfiles, TemporalTasteModel


def example():
    x = np.random.default_rng(921).normal(size=(10, 6))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    profiles = SpectralProfiles.from_arrays([str(i) for i in range(10)], {"menu": x @ x.T})
    model = TemporalTasteModel(profiles)
    for sid, like in [("0", True), ("1", False), ("2", True), ("3", False), ("0", False)]:
        model.record(sid, like)
    return profiles, model


def test_export_reconstructs_actual_mean_and_attributes_reassessment_to_current():
    profiles, model = example()
    before = model.snapshot()
    state = latent_state(model, profiles, history_end_clock=2)
    assert model.snapshot() == before
    assert state.observations == 4 and state.history_observations == 1
    assert state.clock == 5 and state.reconstruction_max_error < 1e-12
    np.testing.assert_allclose(state.history + state.current, state.total, atol=1e-12)
    np.testing.assert_allclose(state.slow + state.fast, state.total, atol=1e-12)
    np.testing.assert_allclose(profiles.x @ state.total + state.intercept,
                               model.predict().mean, atol=1e-12)
    with pytest.raises(ValueError):
        state.total[0] = 0


def test_reference_contrast_is_exact_heldout_score_gap_not_an_axis_label():
    profiles, model = example()
    state = latent_state(model, profiles, history_end_clock=2)
    groups = {"A": ["6", "7"], "B": ("8", "9")}
    contrast = reference_contrast(state, profiles, groups)
    scores = model.predict().mean
    assert contrast["mean_score_gap"] == pytest.approx(scores[[6, 7]].mean() - scores[[8, 9]].mean())
    assert contrast["mean_score_gap"] == pytest.approx(contrast["history_score_gap"] + contrast["current_score_gap"])
    assert contrast["mean_score_gap"] == pytest.approx(contrast["slow_score_gap"] + contrast["fast_score_gap"])
    assert contrast["group_counts"] == {"A": 2, "B": 2}


def test_geometry_is_basis_invariant_and_zero_direction_is_undefined():
    a, b = np.array([1., 2., 3.]), np.array([-.5, .2, 1.])
    q, _ = np.linalg.qr(np.random.default_rng(3).normal(size=(3, 3)))
    for key, value in geometry(a, b).items():
        assert geometry(q @ a, q @ b)[key] == pytest.approx(value)
    assert geometry(np.zeros(3), b)["angle_degrees"] is None
    assert geometry(np.zeros(3), b)["cosine"] is None
    assert geometry(a, a)["distance"] == 0


def test_cold_state_has_no_invented_vector_direction():
    profiles, _ = example()
    model = TemporalTasteModel(profiles)
    state = latent_state(model, profiles)
    assert state.observations == state.history_observations == 0
    assert np.all(state.total == 0) and state.intercept == 0
    assert geometry(state.total, state.total)["angle_degrees"] is None


@pytest.mark.parametrize("cutoff", [-1, 6, True, 1.5])
def test_invalid_history_cutoff_is_rejected(cutoff):
    profiles, model = example()
    with pytest.raises(ValueError):
        latent_state(model, profiles, history_end_clock=cutoff)


@pytest.mark.parametrize("groups", [{"A": [], "B": ["9"]}, {"A": ["6"], "B": ["6"]}, {"A": ["outside"], "B": ["9"]}])
def test_invalid_reference_groups_are_rejected(groups):
    profiles, model = example()
    with pytest.raises(ValueError):
        reference_contrast(latent_state(model, profiles), profiles, groups)
