"""The user headline describes feedback; map geometry is not cuisine evidence."""
from __future__ import annotations

import copy
import pickle

import numpy as np
import pytest

from app import loader, state, taste_api, taste_hierarchy
from recsys import store, taste


EARLY = "조금 더 넘겨 보면 취향 지도가 그려져요"
NEUTRAL = "좋아요한 가게를 바탕으로 취향을 알아가고 있어요"


def named(label):
    return f"최근 좋아요에서는 {label} 쪽이 두드러져요"


def feedback(*ids, at=None, n=None):
    evidence = dict(zip(ids, range(1, len(ids) + 1))) if at is None else dict(at)
    return {"like_ids": list(ids), "dislike_ids": [], "evidence_at": evidence,
            "n": max(evidence.values(), default=0) if n is None else n,
            "likes": len(ids), "memory": [], "hist": [
                {"n": i, "like": True, "store_id": sid}
                for i, sid in enumerate(ids, 1)]}


@pytest.fixture
def catalogue(monkeypatch, art):
    rows = {
        "s0": {"cat2": "한식", "cat3": "육류,고기"},
        "s1": {"cat2": "한식", "cat3": "육류,고기"},
        "s2": {"cat2": "한식", "cat3": "육류,고기"},
        "s3": {"cat2": "한식", "cat3": "국밥"},
        "s4": {"cat2": "카페", "cat3": "커피전문점"},
        "s5": {"cat2": "술집", "cat3": "호프,요리주점"},
    }
    monkeypatch.setattr(store, "get_store", rows.get)
    return rows


def test_three_unrelated_likes_do_not_invent_a_cuisine(catalogue):
    catalogue["s0"] = {"cat2": "한식", "cat3": "해물,생선"}
    # A pub, a fish restaurant and a cafe do not establish a chicken preference.
    got = taste_api._headline(feedback("s5", "s0", "s4"))
    assert got == NEUTRAL
    assert "치킨" not in got


def test_headline_is_independent_of_map_labels_and_coordinates(monkeypatch, catalogue):
    model = feedback("s0", "s1", "s2")
    monkeypatch.setattr(taste, "sessions", lambda: {"u_headline": model})
    monkeypatch.setattr(loader, "card_id_by_store", {sid: i for i, sid in enumerate(model["like_ids"])})
    monkeypatch.setattr(taste_hierarchy, "read_level", lambda detail: {"level": detail, "nodes": []})
    responses = []
    for label, x in (("치킨", [0.1, 0.2, 0.3]), ("중식", [0.9, 0.4, 0.1])):
        layout = taste_api._fold({"cats": [label], "ids": model["like_ids"],
            "name": ["고깃집 A", "고깃집 B", "고깃집 C"], "cat": [0, 0, 0],
            "x": x, "y": [0.1, 0.4, 0.8]})
        monkeypatch.setattr(taste_api, "_load_layout", lambda: layout)
        response = taste_api.user_taste("u_headline")
        assert response["now"]["region"] == label  # Still a map label, not a preference.
        assert response["headline"] == named("육류,고기")
        assert set(response) == {"user_id", "name", "swipes", "likes", "map",
                                 "trail", "now", "drift", "headline"}
        taste_api.UserTasteOut.model_validate(response)
        responses.append(response)
    assert responses[0]["now"]["x"] != responses[1]["now"]["x"]
    assert responses[0]["headline"] == responses[1]["headline"]


def test_specific_observed_type_precedes_its_broad_category(catalogue):
    assert taste_api._headline(feedback("s0", "s1", "s3")) == named("육류,고기")


def test_broad_category_is_used_only_when_specific_types_do_not_have_majority(catalogue):
    catalogue["s1"]["cat3"] = "냉면"
    assert taste_api._headline(feedback("s0", "s1", "s3")) == named("한식")


def test_equal_specific_names_under_different_parents_do_not_combine(catalogue):
    catalogue.update({"s0": {"cat2": "한식", "cat3": "기타"},
                      "s1": {"cat2": "일식", "cat3": "기타"},
                      "s2": {"cat2": "중식", "cat3": "기타"}})
    assert taste_api._headline(feedback("s0", "s1", "s2")) == NEUTRAL


@pytest.mark.parametrize("unknown", [None, {}, {"cat2": None, "cat3": None},
                                     {"cat2": "  ", "cat3": ""}])
def test_unknown_metadata_remains_in_the_denominator(catalogue, unknown):
    catalogue["s1"] = unknown
    catalogue["s2"] = unknown
    assert taste_api._headline(feedback("s1", "s2", "s0")) == NEUTRAL


def test_all_unknown_does_not_claim_mixed_food_preferences(catalogue):
    catalogue.clear()
    assert taste_api._headline(feedback("s0", "s1", "s2")) == NEUTRAL


def test_exact_half_is_not_a_majority_and_order_does_not_break_the_tie(catalogue):
    catalogue["s4"] = catalogue["s5"] = {"cat2": "카페", "cat3": "커피"}
    at = {sid: 4 for sid in ("s0", "s1", "s4", "s5")}
    for ids in (("s0", "s1", "s4", "s5"), ("s5", "s4", "s1", "s0")):
        assert taste_api._headline(feedback(*ids, at=at)) == NEUTRAL


@pytest.mark.parametrize("age, expected", [(5, "육류,고기"), (15, "커피전문점")])
def test_recency_uses_the_recommendation_evidence_time_scale(catalogue, age, expected):
    # Two old meat likes beat one recent cafe like at age 5, but not at age 15.
    # The map EMA's much shorter half-life would yield a different first result.
    model = feedback("s0", "s1", "s4", at={"s0": 20 - age, "s1": 20 - age, "s4": 20}, n=20)
    assert taste_api._headline(model) == named(expected)


def test_recent_one_can_outweigh_twenty_old_without_claiming_more_stores(catalogue):
    old = [f"old{i}" for i in range(20)]
    catalogue.update({sid: {"cat2": "한식", "cat3": "육류,고기"} for sid in old})
    at = {sid: i for i, sid in enumerate(old, 1)} | {"s4": 100}
    model = feedback(*old, "s4", at=at, n=100)
    assert taste_api._headline(model) == named("커피전문점")
    assert "많" not in taste_api._headline(model)


def test_only_relative_age_matters_for_a_weighted_observation_summary(catalogue):
    model = feedback("s0", "s1", "s4")
    expected = taste_api._headline(model)
    model["n"] += 100_000  # No new like; absolute weights would underflow.
    assert taste_api._headline(model) == expected


def test_duplicate_ids_do_not_create_enough_evidence_or_extra_votes(catalogue):
    assert taste_api._headline(feedback(*(["s0"] * 20))) == EARLY
    model = feedback("s0", "s1", "s4", "s5", at={"s0": 4, "s1": 4, "s4": 4, "s5": 4})
    model["like_ids"] += ["s0"] * 20
    assert taste_api._headline(model) == NEUTRAL


def test_reassessment_removes_the_live_label_but_preserves_the_historical_trace(catalogue, sess):
    for sid in ("s0", "s1", "s2"):
        taste.record(sess, sid, True)
    assert taste_api._headline(sess) == named("육류,고기")
    before_history = copy.deepcopy(sess["hist"])
    taste.record(sess, "s2", False)
    assert taste_api._headline(sess) == EARLY
    assert sess["hist"][:3] == before_history
    taste.record(sess, "s2", True)
    assert taste_api._headline(sess) == named("육류,고기")
    assert len(sess["like_ids"]) == 3 and len(sess["hist"]) == 5
    assert sess["evidence_at"]["s2"] == 5


def test_previous_session_memory_is_not_reported_as_current_session_likes(catalogue):
    model = feedback("s0", "s1", "s2")
    model["memory"] = [{"store_id": "s4", "like": True, "weight": 10_000}]
    other = feedback("s4", "s5", "s3")
    assert taste_api._headline(model) == named("육류,고기")
    assert taste_api._headline(other) == NEUTRAL
    model["like_ids"] = []
    assert taste_api._headline(model) == EARLY


def test_headline_keeps_the_model_scores_rng_history_and_feedback_files_unchanged(catalogue, sess, art):
    for sid, like in (("s0", True), ("s1", False), ("s2", True), ("s4", True)):
        taste.record(sess, sid, like)
    before = pickle.dumps(sess)
    z = taste.components(sess, art).copy()
    files = {path: path.read_bytes() if path.exists() else None for path in (state.PREFS, state.EXPOSURES)}
    for _ in range(3):
        taste_api._headline(sess)
    assert pickle.dumps(sess) == before
    np.testing.assert_array_equal(taste.components(sess, art), z)
    assert {path: path.read_bytes() if path.exists() else None for path in files} == files
