"""Synthetic mathematical tests: no serving data or private user snapshots."""
from copy import deepcopy
import hashlib
import json

import numpy as np
import pytest

from .model import Parameters, SpectralProfiles, TemporalTasteModel


def model(seed=71):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(9, 5))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    return TemporalTasteModel(ids=[str(i) for i in range(len(x))], gram=x @ x.T)


def populated():
    result = model()
    for sid, like in [("0", True), ("1", False), ("2", True), ("0", False),
                      ("3", True), ("4", False), ("5", True), ("1", True)]:
        result.record(sid, like)
    return result


def augmented_prediction(m, query, exclude=()):
    """Independent universal kriging solve; no model fitting/deletion helpers."""
    keep = [s for s in m.evidence if s not in exclude]
    if not keep:
        return 0., np.nan
    at = np.array([m.evidence[s]["at"] for s in keep])
    y = np.array([1. if m.evidence[s]["like"] else -1. for s in keep])
    indices = [m.pos[s] for s in keep]
    p = m.parameters
    temporal = p.slow_variance + p.fast_variance * np.exp2(-abs(at[:, None] - at[None, :]) / p.half_life)
    matrix = m.gram[np.ix_(indices, indices)] * temporal + p.observation_variance * np.eye(len(keep))
    augmented = np.block([[matrix, np.ones((len(keep), 1))],
                          [np.ones((1, len(keep))), np.zeros((1, 1))]])
    cross = m.gram[m.pos[query], indices] * (p.slow_variance + p.fast_variance * np.exp2(-(m.clock - at) / p.half_life))
    weights = np.linalg.solve(augmented, np.r_[cross, 1.])
    variance = m.gram[m.pos[query], m.pos[query]] * (p.slow_variance + p.fast_variance) - np.r_[cross, 1.] @ weights
    return weights[:-1] @ y, np.sqrt(max(variance, 0.))


@pytest.mark.parametrize("exclude", [(), ("0",), ("0", "2", "4"), tuple(str(i) for i in range(6))])
@pytest.mark.parametrize("loo", [False, True])
def test_mean_uncertainty_and_fast_deletion_match_independent_augmented_solve(exclude, loo):
    m = populated()
    before = m.snapshot()
    # Duplicates and unseen candidates also exercise arbitrary query order.
    queries = ["8", "0", "3", "0", "1", "6", "2", "4", "5"]
    actual = m.predict(queries, exclude_stores=exclude, leave_store_out=loo)
    expected = [augmented_prediction(m, s, set(exclude) | ({s} if loo else set())) for s in queries]
    np.testing.assert_allclose(actual.mean, [r[0] for r in expected], atol=1e-12)
    np.testing.assert_allclose(actual.latent_std, [r[1] for r in expected], atol=1e-12)
    np.testing.assert_array_equal(actual.identified, np.isfinite(actual.latent_std))
    assert m.snapshot() == before


def test_session_continuity_latest_response_and_independent_cloning():
    m = populated()
    clone = m.clone()
    before = m.snapshot()
    m.start_session()
    assert m.snapshot() == before
    assert m.clock == 8 and len(m.evidence) == 6
    assert dict(m.evidence["0"]) == {"like": False, "at": 4}
    m.record("0", True)
    clone.record("0", True)
    np.testing.assert_array_equal(m.predict().mean, clone.predict().mean)
    assert len(m.evidence) == 6 and m.evidence["0"]["at"] == 9
    clone.record("8", False)
    assert "8" not in m.evidence and clone.clock == m.clock + 1
    assert clone.gram is m.gram
    with pytest.raises(TypeError):
        m.evidence["0"]["like"] = False
    with pytest.raises(ValueError):
        m.gram[0, 0] = 0


def test_snapshot_roundtrip_preserves_clock_labels_and_catalog_binding():
    m = populated()
    snapshot = json.loads(json.dumps(m.snapshot()))
    restored = TemporalTasteModel.from_snapshot(None, snapshot, ids=m.ids, gram=m.gram)
    assert restored.snapshot() == snapshot
    np.testing.assert_allclose(restored.predict(leave_store_out=True).mean,
                               m.predict(leave_store_out=True).mean, atol=1e-12)
    # Same shapes with a reordered catalog must not silently reuse responses.
    with pytest.raises(ValueError, match="catalog mismatch"):
        TemporalTasteModel.from_snapshot(None, snapshot, ids=m.ids[::-1], gram=m.gram)


@pytest.mark.parametrize("change", [
    lambda s: s.update(clock=-1),
    lambda s: s.update(clock=True),
    lambda s: s.update(version=True),
    lambda s: s["evidence"][0].update(at=s["clock"] + 1),
    lambda s: s["evidence"][0].update(at=0),
    lambda s: s["evidence"][0].update(at=s["evidence"][1]["at"]),
    lambda s: s["evidence"][0].update(like="false"),
    lambda s: s["evidence"][0].update(store_id="unknown"),
    lambda s: s["evidence"].append(dict(s["evidence"][0])),
    lambda s: s["parameters"].update(observation_variance=0),
])
def test_invalid_or_future_snapshot_rejected(change):
    m = populated()
    snapshot = deepcopy(m.snapshot())
    change(snapshot)
    with pytest.raises(ValueError):
        TemporalTasteModel.from_snapshot(None, snapshot, ids=m.ids, gram=m.gram)


def test_cold_single_observation_and_one_class_uncertainty():
    m = model()
    cold = m.predict(leave_store_out=True)
    assert np.all(cold.mean == 0) and np.isnan(cold.latent_std).all()
    assert not cold.identified.any()
    m.record("0", True)
    single = m.predict(leave_store_out=True)
    assert single.mean[0] == 0 and np.isnan(single.latent_std[0]) and not single.identified[0]
    np.testing.assert_allclose(single.mean[1:], 1., atol=1e-12)
    assert single.identified[1:].all() and np.all(single.latent_std[1:] > 0)
    for sid in ["1", "2", "3"]:
        m.record(sid, True)
    one_class = m.predict(leave_store_out=True)
    np.testing.assert_allclose(one_class.mean, 1., atol=1e-12)
    assert one_class.identified.all() and (one_class.latent_std > 0).all()
    # All-positive observations identify a common level, not content ranking.
    assert np.ptp(one_class.mean) < 1e-12


def test_zero_features_do_not_invent_content_and_free_mean_is_uncertain():
    m = TemporalTasteModel(ids=["a", "b", "c"], gram=np.zeros((3, 3)))
    m.record("a", True)
    m.record("b", False)
    result = m.predict()
    np.testing.assert_allclose(result.mean, 0., atol=1e-12)
    np.testing.assert_allclose(result.latent_std, np.sqrt(.5), atol=1e-12)
    loo = m.predict(leave_store_out=True)
    np.testing.assert_allclose(loo.mean, [-1., 1., 0.], atol=1e-12)
    np.testing.assert_allclose(loo.latent_std, [1., 1., np.sqrt(.5)], atol=1e-12)


def test_spectral_features_reconstruct_positive_kernel_and_preserve_missing_rows():
    raw = np.array([[1., 2., .3, np.nan], [2., 1., .6, np.nan],
                    [.3, .6, 1., np.nan], [np.nan, np.nan, np.nan, np.nan]])
    p = SpectralProfiles.from_arrays(["a", "b", "c", "d"], {"menu": raw})
    assert p.diagnostics["menu"]["negative_spectral_mass_fraction"] > 0
    assert p.diagnostics["menu"]["known"] == 3 and p.diagnostics["text"]["missing"]
    assert np.all(p.x[3] == 0) and np.all(p.gram[3] == 0)
    k = raw[:3, :3]
    k = k - k.mean(0)[None, :] - k.mean(1)[:, None] + k.mean()
    values, vectors = np.linalg.eigh(k)
    positive = (vectors * np.maximum(values, 0)[None, :]) @ vectors.T
    norm = np.sqrt(np.diag(positive))
    np.testing.assert_allclose(p.gram[:3, :3], positive / np.outer(norm, norm), atol=1e-12)
    assert np.linalg.eigvalsh(p.gram).min() > -1e-12


def test_empty_modality_zero_spectrum_and_serving_file_hashes(tmp_path):
    ids = ["a", "b", "c"]
    (tmp_path / "pool_ids.json").write_text(json.dumps(ids))
    np.save(tmp_path / "sim_menu.npy", np.zeros((3, 3)))
    np.save(tmp_path / "sim_review.npy", np.full((3, 3), np.nan))
    p = SpectralProfiles.from_serving(tmp_path)
    assert p.x.shape == (3, 0) and np.all(p.gram == 0)
    assert p.diagnostics["review"]["known"] == 0
    assert p.diagnostics["menu"]["negative_spectral_mass_fraction"] == 0
    assert p.sources["menu"] == hashlib.sha256((tmp_path / "sim_menu.npy").read_bytes()).hexdigest()
    empty = SpectralProfiles.from_arrays(ids, {})
    assert empty.x.shape == (3, 0) and np.all(empty.gram == 0)
    json.dumps(p.diagnostics)


@pytest.mark.parametrize("parameters", [
    {"slow_variance": -1}, {"fast_variance": float("nan")},
    {"half_life": 0}, {"observation_variance": -1}, {"half_life": "10"},
    {"slow_variance": 0, "fast_variance": 0}, {"half_life": True},
])
def test_invalid_parameters_rejected(parameters):
    with pytest.raises(ValueError):
        Parameters(**parameters)


@pytest.mark.parametrize("ids,gram", [
    (["a", "a"], np.eye(2)), (["a"], np.eye(2)),
    (["a", "b"], [[1, 2], [2, 1]]),
    (["a", "b"], [[1, .2], [.3, 1]]),
    (["a", "b"], [[1, np.nan], [np.nan, 1]]),
])
def test_invalid_gram_or_catalog_rejected(ids, gram):
    with pytest.raises(ValueError):
        TemporalTasteModel(ids=ids, gram=gram)


def test_invalid_input_does_not_advance_clock_and_known_missing_pairs_fail():
    m = model()
    for sid, label in [("unknown", True), ("0", 1), ([], False)]:
        with pytest.raises(ValueError):
            m.record(sid, label)
        assert m.clock == 0
    with pytest.raises(ValueError):
        m.predict(["unknown"])
    with pytest.raises(ValueError):
        m.predict(exclude_stores=["unknown"])
    with pytest.raises(ValueError, match="between known stores"):
        SpectralProfiles.from_arrays(["a", "b"], {"menu": [[1, np.nan], [np.nan, 1]]})
    assert m.predict([]).mean.size == 0
