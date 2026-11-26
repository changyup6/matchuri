"""Input/provenance guards only; never score reserved study scenarios."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from .bench import digest
from .depth_bench import (
    audit_groups, audit_source_checks, evaluation_brands, source_checks,
    validate_depth_scenarios,
)
from .metrics import recommendation_counts


@pytest.fixture
def frozen_catalog(tmp_path):
    serving = tmp_path / "serving"
    serving.mkdir()
    (serving / "pool_ids.json").write_text(json.dumps(["a", "b"]))
    # These guards inspect file bytes, independently of array loading/scoring.
    (serving / "sim_menu.npy").write_bytes(b"frozen similarity artifact")
    (tmp_path / "menus.jsonl").write_text('{"store_id":"a","name":"meal"}\n')
    sources = {"menu": digest(serving / "sim_menu.npy")}
    profiles = SimpleNamespace(sources=sources)
    document = {
        "catalog_sources": dict(sources),
        "pool_ids_sha256": digest(serving / "pool_ids.json"),
        "label_source_sha256": {"menus.jsonl": digest(tmp_path / "menus.jsonl")},
    }
    return tmp_path, document, profiles


@pytest.mark.parametrize("relative_path, error", [
    ("serving/sim_menu.npy", "Serving matrix changed"),
    ("serving/pool_ids.json", "Serving ID order"),
    ("menus.jsonl", "Label source changed"),
])
def test_source_checks_rereads_files_after_initial_success(frozen_catalog, relative_path, error):
    data, document, profiles = frozen_catalog
    source_checks(document, data, profiles)
    original_sources = dict(profiles.sources)
    (data / relative_path).write_bytes(b"changed after model was loaded")
    # The in-memory fingerprint remains unchanged: checking it alone would miss
    # a concurrent source update and incorrectly certify a reproducible run.
    assert profiles.sources == original_sources == document["catalog_sources"]
    with pytest.raises(ValueError, match=error):
        source_checks(document, data, profiles)


def test_source_checks_rejects_other_catalog_even_when_its_bytes_are_stable(frozen_catalog):
    data, document, profiles = frozen_catalog
    document["catalog_sources"]["menu"] = "different frozen catalog"
    with pytest.raises(ValueError, match="Serving matrices differ"):
        source_checks(document, data, profiles)


@pytest.mark.parametrize("recorded_path, local_path", [
    ("menus.jsonl", "menus.jsonl"),
    ("/unavailable/original/catalog/menus.jsonl", "menus.jsonl"),
    ("serving/brand_key.json", "serving/brand_key.json"),
    ("/unavailable/original/serving/brand_key.json", "serving/brand_key.json"),
])
def test_audit_source_checks_binds_absolute_and_relative_records_to_input_catalog(
        tmp_path, recorded_path, local_path):
    local = tmp_path / local_path
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("audited source\n")
    audit = {"source_sha256": {
        recorded_path: digest(local),
        # Historical build scripts need not exist on the measurement machine.
        "/unavailable/analysis/build-audit.py": "historical script digest",
    }}
    audit_source_checks([audit], tmp_path)
    local.write_text("same catalog IDs, different content\n")
    with pytest.raises(ValueError, match="Audited catalogue source changed"):
        audit_source_checks([audit], tmp_path)


def test_evaluation_brand_aliases_exclude_a_known_chain_without_mutating_selector_brands():
    ids = ["observed_branch", "sibling_branch", "independent", "unknown_brand"]
    serving = ["chain local", "chain flagship", "other", ""]
    before = list(serving)
    document = {
        "brand_aliases": {"chain local": "chain", "chain flagship": "chain"},
        "observed_brand_keys": dict(zip(ids, ["chain", "chain", "other", ""])),
    }
    brands = evaluation_brands(document, ids, serving)
    counts = recommendation_counts(
        ids, ids, ids, {"food": ids}, observed=["observed_branch"], brands=brands,
    )
    assert counts["unseen_store_and_brand_count"] == 2
    assert counts["groups"]["food"]["unseen_card_hits"] == 2
    assert counts["groups"]["food"]["unseen_count"] == 2
    assert serving == before  # Production diversity still uses its own brands.


@pytest.mark.parametrize("change", ["unexplained_alias", "missing_store", "extra_store"])
def test_evaluation_brands_rejects_unreproducible_frozen_mapping(change):
    document = {"brand_aliases": {"chain local": "chain"},
                "observed_brand_keys": {"a": "chain", "b": "other"}}
    if change == "unexplained_alias":
        document["observed_brand_keys"]["a"] = "unrelated"
    elif change == "missing_store":
        del document["observed_brand_keys"]["b"]
    else:
        document["observed_brand_keys"]["outside"] = "other"
    with pytest.raises(ValueError, match="Frozen evaluation brand mapping"):
        evaluation_brands(document, ["a", "b"], ["chain local", "other"])


def test_salad_previous_fried_group_is_deduplicated_chicken_and_cutlet_union():
    cutlet = {"cutlet": "principal", "chicken": "absent", "both": "co_main",
              "uncertain": "mixed", "salad": "absent"}
    chicken = {"cutlet": "absent", "chicken": "principal", "both": "co_main",
               "uncertain": "unknown", "salad": "absent"}
    food = {"masks": {"cutlet": {
        s: {"food": status, "style": "not_established"} for s, status in cutlet.items()
    }}}
    salad = {"annotations": {
        s: {"salad_poke": {"status": "co_main" if s in {"salad", "both"} else "absent"},
            "fried_chicken": {"status": chicken[s]}} for s in cutlet
    }}
    rows, groups = audit_groups(food, salad, "salad_poke")
    assert groups["previous_fried_food"] == ["both", "chicken", "cutlet"]
    assert set(groups["current_food"]) == {"salad", "both"}
    assert rows["uncertain"]["food"] == "absent"
    # A co-main restaurant may legitimately be in both groups; menu categories
    # are not mutually exclusive satisfaction labels.
    assert "both" in groups["food_status_co_main"]


def test_food_groups_keep_uncertain_and_out_of_scope_separate_from_confirmed_style():
    mask = {
        "j": {"food": "principal", "style": "japanese"},
        "k": {"food": "co_main", "style": "korean"},
        "mixed": {"food": "mixed", "style": "japanese"},
        "other_noodle": {"food": "outside_scope", "style": "not_established"},
        "unknown": {"food": "unknown", "style": "not_established"},
    }
    _, groups = audit_groups({"masks": {"ramen_korean_hot_noodle": mask}}, {},
                             "ramen_korean_hot_noodle")
    assert set(groups["current_food"]) == {"j", "k"}
    assert groups["japanese"] == ["j"] and groups["korean"] == ["k"]
    assert groups["food_status_outside_scope"] == ["other_noodle"]
    assert groups["food_status_unknown"] == ["unknown"]


def depth_document():
    return {"schema_version": 1, "runs": [{
        "id": "synthetic", "history_n": 2, "past": [["a", True], ["b", False]],
        "phases": [
            {"events": [["c", True]], "groups": {"A": ["e"], "B": ["f"]}},
            {"events": [["c", False], ["d", True]], "groups": {"A": ["e"], "B": ["f"]}},
        ],
    }]}


def test_depth_validation_allows_current_reassessment_but_does_not_change_fixture():
    document = depth_document()
    before = deepcopy(document)
    validate_depth_scenarios(document, list("abcdef"))
    assert document == before


@pytest.mark.parametrize("change, error", [
    ("wrong_n", "History N"),
    ("duplicate_history", "History N"),
    ("current_history_overlap", "overlap the frozen history"),
    ("heldout_feedback", "held-out restaurant"),
    ("non_boolean_feedback", "invalid observation"),
])
def test_depth_validation_rejects_denominator_errors_and_training_leakage(change, error):
    document = depth_document()
    run = document["runs"][0]
    if change == "wrong_n":
        run["history_n"] = 3
    elif change == "duplicate_history":
        run["past"][1][0] = "a"
    elif change == "current_history_overlap":
        run["phases"][1]["events"][0][0] = "a"
    elif change == "heldout_feedback":
        run["phases"][1]["events"][0][0] = "e"
    else:
        run["past"][0][1] = 1
    with pytest.raises(ValueError, match=error):
        validate_depth_scenarios(document, list("abcdef"))
