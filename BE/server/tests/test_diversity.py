"""Distribution-relative comparisons, without pretending to calibrate semantics."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

from recsys import diversity


def _matrix(pairs, size=4):
    matrix = np.full((size, size), np.nan, dtype=np.float32)
    upper = np.triu_indices(size, 1)
    matrix[upper] = pairs
    matrix[(upper[1], upper[0])] = pairs
    np.fill_diagonal(matrix, 100)
    return matrix


def _art(**modalities):
    return SimpleNamespace(ids=["a", "b", "c", "d"], **{
        "sim_" + kind: _matrix(modalities.get(kind, [0, .2, .2, .4, .8, np.nan]))
        for kind in diversity.MODALITIES})


def test_midrank_ties_ignore_diagonal_missing_and_duplicate_direction():
    art = _art()
    matrix = diversity.matrices(art)["menu"]
    np.testing.assert_allclose(matrix[np.triu_indices(4, 1)],
                               [.1, .4, .4, .7, .9, np.nan], equal_nan=True)
    np.testing.assert_equal(matrix, matrix.T)
    assert np.isnan(np.diag(matrix)).all()
    assert diversity.pair_values(art, 2, 3) == dict.fromkeys(diversity.MODALITIES)
    assert diversity.pair_redundancy(art, 0, 0) is None


def test_increasing_scale_change_cannot_make_a_modality_dominate():
    base = np.array([0, .1, .2, .3, .4, .5], dtype=np.float32)
    art = _art(menu=base, text=.6 + base / 2, review=base ** 2, food=.9 + base / 10)
    matrices = diversity.matrices(art)
    for kind in diversity.MODALITIES:
        np.testing.assert_allclose(matrices[kind], matrices["menu"], equal_nan=True)


def test_strongest_observed_match_is_not_diluted_by_other_modalities():
    art = _art(menu=[0, 1, 2, 3, 4, 5], text=[5, 0, 1, 2, 3, 4],
               review=[0, 1, 2, 3, 4, 5], food=[0, 1, 2, 3, 4, 5])
    values = diversity.pair_values(art, 0, 1)
    assert values["menu"] == pytest.approx(1 / 12)
    assert values["text"] == pytest.approx(11 / 12)
    assert diversity.pair_redundancy(art, 0, 1) == pytest.approx(11 / 12)


def test_missing_modalities_are_unknown_and_do_not_dilute_known_matches():
    unknown = [np.nan] * 6
    art = _art(menu=[5, 0, 1, 2, 3, 4], review=unknown, food=unknown)
    assert diversity.pair_values(art, 0, 1)["review"] is None
    assert diversity.pair_redundancy(art, 0, 1) == pytest.approx(11 / 12)
    one = _art(menu=[5, 0, 1, 2, 3, 4], text=unknown, review=unknown, food=unknown)
    assert diversity.pair_values(one, 0, 1)["menu"] == pytest.approx(11 / 12)
    assert diversity.pair_redundancy(one, 0, 1) is None
    assert np.isnan(diversity.redundancy_matrix(one)).all()
    none = _art(**dict.fromkeys(diversity.MODALITIES, unknown))
    assert diversity.pair_redundancy(none, 0, 1) is None


def test_nonfinite_values_are_not_reference_pairs_or_evidence():
    art = _art(menu=[np.inf, -np.inf, np.nan, .2, .2, .4])
    values = diversity.matrices(art)["menu"][np.triu_indices(4, 1)]
    np.testing.assert_allclose(values, [np.nan, np.nan, np.nan, 1 / 3, 1 / 3, 5 / 6], equal_nan=True)


def test_all_tied_pairs_are_neutral_and_not_maximal_or_minimal():
    art = _art(**dict.fromkeys(diversity.MODALITIES, [.95] * 6))
    assert diversity.pair_redundancy(art, 0, 1) == .5
    assert diversity.pair_values(art, 0, 1) == dict.fromkeys(diversity.MODALITIES, .5)


def test_cached_matrices_are_read_only_and_do_not_modify_raw_artifact():
    art = _art()
    before = {kind: getattr(art, "sim_" + kind).copy() for kind in diversity.MODALITIES}
    first = diversity.matrices(art)
    assert diversity.matrices(art) is first
    assert diversity.redundancy_matrix(art) is diversity.redundancy_matrix(art)
    with pytest.raises(ValueError):
        first["menu"][0, 1] = 0
    with pytest.raises(TypeError):
        first["menu"] = np.zeros((4, 4))
    with pytest.raises(ValueError):
        diversity.redundancy_matrix(art)[0, 1] = 0
    for kind in diversity.MODALITIES:
        np.testing.assert_equal(getattr(art, "sim_" + kind), before[kind])
        assert getattr(art, "sim_" + kind).flags.writeable


def test_cache_uses_artifact_identity_and_only_builds_once_across_threads(monkeypatch):
    art = _art()
    original = diversity._build
    calls = []
    def track(current):
        calls.append(current)
        return original(current)
    monkeypatch.setattr(diversity, "_build", track)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: diversity.matrices(art), range(24)))
    assert len(calls) == 1
    assert all(value is results[0] for value in results)
    replacement = _art(menu=[5, 4, 3, 2, 1, 0])
    assert diversity.matrices(replacement) is not results[0]
    assert len(calls) == 2
    assert diversity.pair_values(replacement, 0, 1)["menu"] > .9


def test_empty_single_store_and_malformed_artifacts():
    for size in [0, 1]:
        art = SimpleNamespace(ids=list(range(size)), **{
            "sim_" + kind: np.ones((size, size)) for kind in diversity.MODALITIES})
        matrix = diversity.redundancy_matrix(art)
        assert matrix.shape == (size, size) and np.isnan(matrix).all()
    art = _art()
    art.sim_menu = np.ones((3, 3))
    with pytest.raises(ValueError, match="shape"):
        diversity.matrices(art)
