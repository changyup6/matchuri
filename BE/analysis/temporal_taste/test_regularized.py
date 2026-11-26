"""Independent finite-prior GP checks; no catalogue or private user data."""
from copy import deepcopy
import json

import numpy as np
import pytest

from .model import TemporalTasteModel
from .regularized import RegularizedTemporalTasteModel


def make_model(**parameters):
    rng = np.random.default_rng(1909)
    features = rng.normal(size=(12, 6))
    features /= np.linalg.norm(features, axis=1, keepdims=True)
    return (RegularizedTemporalTasteModel(ids=[str(i) for i in range(len(features))],
                                         gram=features @ features.T, **parameters), features)


def integrated_prediction(model, query, exclude):
    """Independent GP solve with a constant feature integrated into covariance."""
    keep = [s for s in model.evidence if s not in exclude]
    indices = [model.pos[s] for s in keep]
    at = np.array([model.evidence[s]["at"] for s in keep])
    labels = np.array([1. if model.evidence[s]["like"] else -1. for s in keep])
    p = model.parameters
    bias_variance = p.observation_variance
    spatial = model.gram[np.ix_(indices, indices)]
    temporal = p.slow_variance + p.fast_variance * np.exp2(-abs(at[:, None] - at[None, :]) / p.half_life)
    covariance = spatial * temporal + bias_variance + p.observation_variance * np.eye(len(keep))
    cross = (model.gram[model.pos[query], indices]
             * (p.slow_variance + p.fast_variance * np.exp2(-(model.clock - at) / p.half_life))
             + bias_variance)
    prior = model.gram[model.pos[query], model.pos[query]] * (p.slow_variance + p.fast_variance) + bias_variance
    mean = cross @ np.linalg.solve(covariance, labels) if keep else 0.
    variance = prior - (cross @ np.linalg.solve(covariance, cross) if keep else 0.)
    return mean, np.sqrt(variance)


@pytest.mark.parametrize("length", [0, 1, 2, 9, 24])
@pytest.mark.parametrize("noise", [1., .7])
def test_exact_posterior_and_deletion_match_integrated_constant_feature(length, noise):
    model, _ = make_model(observation_variance=noise)
    for j in range(length):
        model.record(str(j % 9), bool(j % 3))
    before = model.snapshot()
    # Nondefault R verifies that the intercept prior is tied to R, not a
    # hidden extra constant. These are math cases, not parameter selection.
    for excluded in [set(), {"0"}, {"0", "3", "6"}, set(model.evidence)]:
        for loo in [False, True]:
            queries = ["11", "0", "3", "0", "1", "5"]
            actual = model.predict(queries, exclude_stores=excluded, leave_store_out=loo)
            expected = [integrated_prediction(model, sid, excluded | ({sid} if loo else set()))
                        for sid in queries]
            np.testing.assert_allclose(actual.mean, [v[0] for v in expected], atol=1e-12)
            np.testing.assert_allclose(actual.latent_std, [v[1] for v in expected], atol=1e-12)
            assert actual.identified.all()
    assert model.snapshot() == before


def test_all_positive_history_has_nonzero_latent_vector_and_exact_decomposition():
    model, features = make_model()
    v1 = TemporalTasteModel(ids=model.ids, gram=model.gram)
    for sid in model.ids[:4]:
        model.record(sid, True)
        v1.record(sid, True)
    assert np.ptp(v1.predict().mean) < 1e-12
    assert np.linalg.norm(v1._solve().alpha) < 1e-12
    fit = model._solve()
    assert np.linalg.norm(fit.alpha) > 1e-4
    assert np.ptp(model.predict().mean) > 1e-4
    slow = model.parameters.slow_variance * features[fit.indices].T @ fit.alpha
    decay = np.exp2(-(model.clock - fit.at) / model.parameters.half_life)
    fast = model.parameters.fast_variance * features[fit.indices].T @ (fit.alpha * decay)
    np.testing.assert_allclose(features @ (slow + fast) + fit.intercept,
                               model.predict().mean, atol=1e-12)


def test_cold_identification_means_prior_variance_not_learned_evidence():
    model, _ = make_model()
    cold = model.predict(leave_store_out=True)
    assert model.clock == 0 and not model.evidence
    np.testing.assert_array_equal(cold.mean, np.zeros(len(model.ids)))
    np.testing.assert_allclose(cold.latent_std, np.sqrt(2.1), atol=1e-12)
    assert cold.identified.all()
    model.record("0", True)
    deleted = model.predict(["0"], leave_store_out=True)
    np.testing.assert_allclose(deleted.mean, [0.], atol=1e-12)
    np.testing.assert_allclose(deleted.latent_std, [np.sqrt(2.1)], atol=1e-12)
    assert deleted.identified.all() and model.clock == 1


def test_zero_features_only_learn_regularized_global_bias():
    model = RegularizedTemporalTasteModel(ids=["a", "b", "c"], gram=np.zeros((3, 3)))
    model.record("a", True)
    model.record("b", True)
    prediction = model.predict()
    np.testing.assert_allclose(prediction.mean, 2. / 3., atol=1e-12)
    np.testing.assert_allclose(prediction.latent_std, np.sqrt(1. / 3.), atol=1e-12)
    # No feature information exists to distinguish candidates; regularization
    # must not create artificial content differences in this case.
    assert np.ptp(prediction.mean) == 0


def test_snapshot_roundtrip_session_clone_and_latest_reassessment():
    model, _ = make_model()
    for sid, like in [("0", True), ("1", False), ("2", True), ("0", False)]:
        model.record(sid, like)
    snapshot = json.loads(json.dumps(model.snapshot()))
    restored = RegularizedTemporalTasteModel.from_snapshot(None, snapshot,
                                                          ids=model.ids, gram=model.gram)
    assert isinstance(restored, RegularizedTemporalTasteModel)
    assert restored.snapshot() == snapshot
    clone = model.clone()
    clone.start_session()
    np.testing.assert_array_equal(clone.predict(leave_store_out=True).mean,
                                  restored.predict(leave_store_out=True).mean)
    assert clone.gram is model.gram and clone.clock == model.clock == 4
    clone.record("0", True)
    assert len(clone.evidence) == 3 and clone.evidence["0"]["at"] == 5
    assert model.evidence["0"]["like"] is False


def test_v1_and_regularized_snapshots_cannot_cross_restore():
    model, _ = make_model()
    model.record("0", True)
    v1 = TemporalTasteModel(ids=model.ids, gram=model.gram)
    v1.record("0", True)
    with pytest.raises(ValueError, match="regularized"):
        RegularizedTemporalTasteModel.from_snapshot(None, v1.snapshot(),
                                                   ids=model.ids, gram=model.gram)
    with pytest.raises(ValueError, match="Unsupported snapshot"):
        TemporalTasteModel.from_snapshot(None, model.snapshot(), ids=model.ids, gram=model.gram)
    wrong_kind = deepcopy(model.snapshot())
    wrong_kind["model"] = "another_model"
    with pytest.raises(ValueError, match="regularized"):
        RegularizedTemporalTasteModel.from_snapshot(None, wrong_kind,
                                                   ids=model.ids, gram=model.gram)


def test_restore_and_query_keep_existing_validation_guards():
    model, _ = make_model()
    model.record("0", True)
    future = deepcopy(model.snapshot())
    future["evidence"][0]["at"] = model.clock + 1
    with pytest.raises(ValueError, match="future"):
        RegularizedTemporalTasteModel.from_snapshot(None, future, ids=model.ids, gram=model.gram)
    with pytest.raises(ValueError, match="catalog mismatch"):
        RegularizedTemporalTasteModel.from_snapshot(None, model.snapshot(),
                                                   ids=model.ids[::-1], gram=model.gram)
    for kwargs in [{"store_ids": ["unknown"]}, {"exclude_stores": ["unknown"]},
                   {"store_ids": "0"}, {"exclude_stores": "0"}]:
        with pytest.raises(ValueError):
            model.predict(**kwargs)
    assert model.predict([]).mean.size == 0
