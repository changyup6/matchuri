"""Synthetic contract checks only; no reserved scenario or user data is loaded."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

import numpy as np
import pytest

from . import attribute_bench as b
from . import matrix_bench


def fixture():
    ids = [f"s{i}" for i in range(10)]
    common = {"family": "attribute_transfer", "context": "meal", "dimension": "attribute",
        "seed": 0, "current": [["s2", True], ["s3", False]], "context_ids": ids[2:],
        "heldout_ids": ids[4:], "evaluation_groups": {"target": ["s4", "s5"], "comparison": ["s6", "s7"]},
        "group_labels": {"target": "audited attribute A", "comparison": "audited attribute B"},
        "axis": "audited meal property", "history_mode": "cross_food",
        "source_groups": {"A": ["s0"], "B": ["s1"]}}
    runs = []
    for prior, past in (("none", []), ("neutral", [["s0", True], ["s1", False]]),
                        ("prefer", [["s0", False], ["s1", True]])):
        runs.append({**deepcopy(common), "id": prior, "prior": prior, "past": past, "history_n": len(past)})
    return {"schema_version": 1, "catalog_ids": ids, "brand_keys": {s: s for s in ids}, "runs": runs}


def test_valid_controls_empty_support_and_optional_avoid_do_not_mutate_input():
    d = fixture()
    avoid = deepcopy(d["runs"][1]); avoid.update(id="avoid", prior="avoid")
    d["runs"].append(avoid)
    for r in d["runs"]:
        r["evaluation_groups"]["comparison"] = []
    before = deepcopy(d)
    assert b.validate(d, d["catalog_ids"]) == 1
    assert d == before


@pytest.mark.parametrize("prior", ["none", "neutral", "prefer"])
def test_missing_control_is_not_a_paired_comparison(prior):
    d = fixture(); d["runs"] = [r for r in d["runs"] if r["prior"] != prior]
    with pytest.raises(ValueError, match="requires none, neutral and prefer"):
        b.validate(d, d["catalog_ids"])


@pytest.mark.parametrize("change", ["unknown", "integer_label", "repeat_current", "repeat_past", "overlap", "wrong_n"])
def test_invalid_feedback_or_history_denominator_is_rejected(change):
    d = fixture(); r = d["runs"][1]
    if change == "unknown": r["past"][0][0] = "not-in-catalogue"
    elif change == "integer_label": r["past"][0][1] = 1
    elif change == "repeat_current": r["current"].append(["s2", True])
    elif change == "repeat_past": r["past"][1][0] = "s0"
    elif change == "overlap": r["past"][0][0] = "s2"
    else: r["history_n"] = 3
    with pytest.raises(ValueError):
        b.validate(d, d["catalog_ids"])


@pytest.mark.parametrize("change", ["past_order", "like_count", "current_order", "evaluation_support"])
def test_matched_arms_cannot_change_recency_response_amount_or_reference(change):
    d = fixture(); r = d["runs"][2]
    if change == "past_order": r["past"].reverse()
    elif change == "like_count": r["past"][0][1] = True
    elif change == "current_order": r["current"].reverse()
    else: r["evaluation_groups"]["target"].append("s8")
    with pytest.raises(ValueError):
        b.validate(d, d["catalog_ids"])


@pytest.mark.parametrize("store, brand", [("s4", "s0"), ("s4", "s2"), ("s2", "s0")])
def test_brand_siblings_cannot_leak_into_heldout_or_current(store, brand):
    d = fixture(); d["brand_keys"][store] = brand
    with pytest.raises(ValueError, match="brand"):
        b.validate(d, d["catalog_ids"])


def test_attribute_contrast_rejects_overlap_and_changed_label_meaning():
    for change in ("overlap", "label"):
        d = fixture()
        if change == "overlap":
            for r in d["runs"]: r["evaluation_groups"]["comparison"].append("s4")
        else: d["runs"][2]["group_labels"]["target"] = "different meaning"
        with pytest.raises(ValueError):
            b.validate(d, d["catalog_ids"])


def test_fingerprints_reject_stale_input_and_changes_after_measurement(tmp_path):
    source = tmp_path / "audited-input.json"; source.write_text("frozen input")
    doc = {"source_sha256": {str(source): b.digest(source)}}
    frozen = b.freeze_sources(doc, [source])
    b.verify_sources(frozen)
    source.write_text("new data at the same path")
    with pytest.raises(ValueError, match="Fixture source changed"):
        b.freeze_sources(doc, [source])
    with pytest.raises(RuntimeError, match="changed during measurement"):
        b.verify_sources(frozen)


def test_runtime_reuses_temporary_cache_without_touching_source_state(tmp_path):
    original = tmp_path / "user-state"; original.mkdir()
    marker = original / "preferences.jsonl"; marker.write_bytes(b"test sentinel, not real user data")
    cache = original / "review-ranks.cache"; cache.write_bytes(b"source cache")
    before = {p.name: p.read_bytes() for p in original.iterdir()}
    locations = []
    @contextmanager
    def fake_runtime(data, serving):
        with tempfile.TemporaryDirectory() as state:
            locations.append(Path(state))
            target = Path(state) / "review-ranks.cache"
            assert target.read_bytes() == b"source cache"
            target.write_bytes(b"disposable cache refresh")
            yield ("art", "decision", "taste")
    with patch.object(matrix_bench, "offline_runtime", fake_runtime):
        with b.runtime(tmp_path, tmp_path, cache):
            assert locations[0] != original
    assert {p.name: p.read_bytes() for p in original.iterdir()} == before
    assert not locations[0].exists()


def synthetic_runtime(ids):
    """Deterministic selector adapter; GP itself is the unchanged real class."""
    art = SimpleNamespace(ids=ids, pos={s: i for i, s in enumerate(ids)})
    decision = SimpleNamespace(DEFAULT=SimpleNamespace(count=4, uncertainty_weight=.5),
                               _diverse=lambda *args: True)
    decision._components = lambda *args, **kwargs: np.zeros((1, len(ids)))
    operational_mean = np.linspace(-.5, .5, len(ids))
    decision._scores = lambda *args, **kwargs: (operational_mean, operational_mean, np.ones(len(ids)))
    def recommend(state, allow=None):
        _, mu, sd = decision._scores(art, state)
        permitted = set(ids) if allow is None else set(allow)
        order = sorted(permitted, key=lambda s: (-(mu[art.pos[s]] - .5 * sd[art.pos[s]]), s))
        return {"store_ids": order[:4], "qualified_count": len(permitted)}
    decision.recommend = recommend
    taste = SimpleNamespace(replay=lambda past: {"past": list(past)},
        start_from=lambda previous: {"previous": previous, "current": []},
        record=lambda state, s, like: state["current"].append((s, like)))
    diversity = SimpleNamespace(pair_redundancy=lambda *args: .25)
    return art, decision, taste, diversity, {s: s for s in ids}


def test_replay_keeps_current_cache_independent_and_exports_exact_vectors_and_metadata():
    d = fixture(); before = deepcopy(d); ids = d["catalog_ids"]
    # Real tiny high-dimensional feature map, independent of any catalogue labels.
    x = np.random.default_rng(7).normal(size=(len(ids), 5))
    profiles = b.SpectralProfiles.from_arrays(ids, {"menu": x @ x.T})
    art, decision, taste, diversity, names = synthetic_runtime(ids)
    rows, arrays, groups = b.measure(d, profiles, art, decision, taste, diversity, names, 1)
    assert groups == 1 and len(rows) == 3 and d == before
    current = b.RegularizedTemporalTasteModel(profiles)
    for s, like in d["runs"][0]["current"]: current.record(s, like)
    expected_current = current.predict()
    for r, saved in zip(d["runs"], rows):
        model = b.RegularizedTemporalTasteModel(profiles)
        for s, like in r["past"] + r["current"]: model.record(s, like)
        np.testing.assert_array_equal(arrays[r["id"] + "__research_mean"], model.predict().mean)
        np.testing.assert_array_equal(arrays[r["id"] + "__current_mean"], expected_current.mean)
        np.testing.assert_array_equal(arrays[r["id"] + "__current_std"], expected_current.latent_std)
        np.testing.assert_allclose(profiles.x @ arrays[r["id"] + "__research_total"] + saved["vector"]["intercept"], model.predict().mean, atol=1e-12)
        np.testing.assert_allclose(arrays[r["id"] + "__research_history"] + arrays[r["id"] + "__research_current"], arrays[r["id"] + "__research_total"], atol=1e-12)
        assert saved["source_groups"] == r["source_groups"]
        assert saved["group_labels"] == r["group_labels"]
        for result in saved["models"].values():
            assert len({c["store_id"] for c in result["cards"]}) == 4
            assert result["groups"]["target"]["heldout"]["count"] == 2
    assert all(row["current_slate_ids"] == rows[0]["current_slate_ids"] for row in rows)
    assert all(row["context_floor"] == rows[0]["context_floor"] for row in rows)
    np.testing.assert_array_equal(arrays["neutral__research_std"], arrays["prefer__research_std"])


def test_current_cache_cannot_cross_attribute_dimensions_with_different_feedback():
    d = fixture()
    second = deepcopy(d["runs"])
    for r in second:
        r["id"] = "other-" + r["id"]
        r["dimension"] = "another attribute"
        r["current"] = [[s, not like] for s, like in r["current"]]
    d["runs"].extend(second)
    ids = d["catalog_ids"]
    profiles = b.SpectralProfiles.from_arrays(ids, {"menu": np.eye(len(ids))})
    art, decision, taste, diversity, names = synthetic_runtime(ids)
    _, arrays, groups = b.measure(d, profiles, art, decision, taste, diversity, names, 1)
    assert groups == 2
    assert not np.array_equal(arrays["none__current_mean"], arrays["other-none__current_mean"])
    np.testing.assert_allclose(arrays["other-none__current_mean"], -arrays["none__current_mean"], atol=1e-12)
    for prior in ("neutral", "prefer"):
        np.testing.assert_array_equal(arrays["other-" + prior + "__current_mean"], arrays["other-none__current_mean"])
