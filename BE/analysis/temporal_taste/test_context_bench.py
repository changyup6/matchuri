"""The eligibility report must not pad missing slots with excluded stores."""
from .context_bench import filtered_counts, reference_rows, study_brands


def test_short_eligible_ranking_keeps_actual_top_denominator():
    ids = ["a", "b", "c", "d", "e"]
    result = filtered_counts(ids, ids, ["c", "d"], ["c", "d"],
                             {"food": ["a", "c", "e"]}, {"c"}, dict.fromkeys(ids, ""))
    group = result["groups"]["food"]
    assert result["top_count"] == 2
    assert group["catalogue_count"] == 3
    assert group["unseen_count"] == 2
    assert group["eligible_count"] == 1
    assert group["eligible_unseen_count"] == 0
    assert group["top_hits"] == 1
    assert group["top_share"] == .5
    assert group["unseen_top_hits"] == 0
    assert group["eligible_unseen_top_recall"] is None


def test_evaluation_brand_alias_is_not_counted_as_new():
    ids = ["old", "alias", "new"]
    result = filtered_counts(ids, ["alias", "new", "old"], ["alias", "new"],
                             ["alias", "new"], {"food": ["alias", "new"]},
                             {"old"}, {"old": "normalized-brand", "alias": "normalized-brand", "new": ""})
    group = result["groups"]["food"]
    assert group["card_hits"] == 2
    assert group["unseen_card_hits"] == 1
    assert group["unseen_count"] == group["eligible_unseen_count"] == 1
    assert group["eligible_unseen_top_recall"] == 1


def test_depth_reference_retains_distinct_checkpoints():
    early = {"id": "a", "phase": "today", "current_responses": 4}
    final = {"id": "a", "phase": "today", "current_responses": 20}
    rows, kind = reference_rows({"rows": [early, final]})
    assert kind == "depth"
    assert rows["a", "today", 4] is early
    assert rows["a", "today", 20] is final


def test_legacy_partial_brand_audit_does_not_replace_replay_protocol():
    legacy = {"runs": [{"past": []}], "observed_brand_keys": {"a": "manual-name"}}
    assert study_brands(legacy, ["a", "b"], ["original", ""]) == {"a": "original", "b": ""}


def test_depth_uses_complete_declared_brand_aliases():
    depth = {"runs": [{"history_n": 0}], "brand_aliases": {"old": "canonical"},
             "observed_brand_keys": {"a": "canonical", "b": ""}}
    assert study_brands(depth, ["a", "b"], ["old", ""]) == depth["observed_brand_keys"]
