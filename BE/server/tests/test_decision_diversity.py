"""Final choices improve vector overlap without redefining fit or availability."""
from collections import Counter
from dataclasses import replace
from itertools import combinations
from types import SimpleNamespace

import numpy as np
import pytest

from recsys import content, decision, diversity


def _case(size=6, *, overlap=True, missing=(), same_brand=False):
    ids = [f"s{i:02}" for i in range(size)]
    matrices = {}
    for kind in diversity.MODALITIES:
        matrix = np.full((size, size), .3, dtype=np.float32)
        np.fill_diagonal(matrix, 1)
        if overlap:
            matrix[0, 1] = matrix[1, 0] = .9 if kind == "food" else 0
        if kind == "food":
            for index in missing:
                matrix[index, :] = matrix[:, index] = np.nan
        matrices["sim_" + kind] = matrix
    return SimpleNamespace(ids=ids, pos={sid: i for i, sid in enumerate(ids)},
                           brand=["brand"] * size if same_brand else ids.copy(), **matrices)


def _objective(art, selected):
    return tuple(sorted((diversity.pair_redundancy(art, a, b)
                         for a, b in combinations(selected, 2)), reverse=True))


def test_first_feasible_slate_improves_photo_overlap_that_raw_mean_hides():
    art = _case()
    original = [0, 1, 2, 3]
    assert all(decision._diverse(art, a, b, decision.DEFAULT)
               for a, b in combinations(original, 2))
    mean = lambda a, b: np.mean([getattr(art, "sim_" + kind)[a, b]
                                for kind in diversity.MODALITIES])
    assert mean(0, 1) < mean(0, 4)  # the old average makes the close pair look safer
    selected = decision._select(art, list(range(6)), decision.DEFAULT)
    assert selected == [0, 2, 3, 4]
    assert _objective(art, selected) < _objective(art, original)
    assert all(decision._diverse(art, a, b, decision.DEFAULT)
               for a, b in combinations(selected, 2))


def test_same_fine_category_can_contain_distinct_options_and_labels_do_not_rank(monkeypatch):
    art = _case()
    monkeypatch.setattr(decision, "_fine_groups", lambda _: dict.fromkeys(art.ids, "중식"))
    assert decision._diverse(art, 0, 2, decision.DEFAULT)
    same = decision._select(art, list(range(6)), decision.DEFAULT)
    monkeypatch.setattr(decision, "_fine_groups", lambda _: {sid: sid for sid in art.ids})
    distinct = decision._select(art, list(range(6)), decision.DEFAULT)
    monkeypatch.setattr(decision, "_fine_groups", lambda _: dict.fromkeys(art.ids, ""))
    absent = decision._select(art, list(range(6)), decision.DEFAULT)
    assert same == distinct == absent == [0, 2, 3, 4]


def test_soft_fallback_is_also_independent_of_category_labels(monkeypatch):
    art = _case(same_brand=True)
    monkeypatch.setattr(decision, "_fine_groups", lambda _: dict.fromkeys(art.ids, "중식"))
    same = decision._soft_select(art, list(range(6)), decision.DEFAULT)
    monkeypatch.setattr(decision, "_fine_groups", lambda _: {sid: sid for sid in art.ids})
    assert decision._soft_select(art, list(range(6)), decision.DEFAULT) == same
    assert same == [0, 2, 3, 4]


@pytest.mark.parametrize("strict", [True, False])
def test_refinement_does_not_gain_diversity_by_losing_an_observed_modality(strict):
    art = _case(size=5, missing=(4,))
    original = [0, 1, 2, 3]
    # Candidate 4 still has three observed modalities and passes the old guard.
    assert decision._diverse(art, 0, 4, decision.DEFAULT)
    assert diversity.pair_redundancy(art, 0, 4) < diversity.pair_redundancy(art, 0, 1)
    assert decision._refine(art, list(range(5)), original, decision.DEFAULT,
                            strict=strict) == original


def test_soft_refinement_does_not_trade_observed_evidence_for_fewer_conflicts():
    art = _case(size=5, missing=(4,))
    art.brand[1] = art.brand[0]
    original = [0, 1, 2, 3]
    assert not decision._diverse(art, 0, 1, decision.DEFAULT)
    assert decision._diverse(art, 0, 4, decision.DEFAULT)
    assert decision._refine(art, list(range(5)), original, decision.DEFAULT,
                            strict=False) == original


def test_refinement_keeps_anchor_and_tier_composition():
    art = _case()
    tiers = {art.ids[0]: 3, art.ids[1]: 2, art.ids[5]: 2}
    art.brand[5] = art.brand[1]  # initial strict search must skip 5 while 1 is present
    ranked = [0, 1, 5, 2, 3, 4]
    selected = decision._select(art, ranked, decision.DEFAULT, tiers=tiers)
    assert selected == [0, 5, 2, 3]
    assert selected[0] == 0
    assert Counter(tiers.get(art.ids[i], 0) for i in selected) == Counter([3, 2, 0, 0])


def test_refinement_cannot_import_candidates_outside_the_original_window():
    art = _case()
    config = replace(decision.DEFAULT, search_limit=4)
    assert decision._select(art, list(range(6)), config) == [0, 1, 2, 3]


@pytest.mark.parametrize("strict", [True, False])
def test_declined_better_combination_is_not_reintroduced_by_refinement(strict):
    art = _case(same_brand=not strict)
    declined = [[art.ids[i] for i in [0, 2, 3, 4]]]
    selected = decision._refine(art, list(range(6)), [0, 1, 2, 3],
                                decision.DEFAULT, declined, strict=strict)
    assert selected == [0, 2, 3, 5]
    assert frozenset(art.ids[i] for i in selected) != frozenset(declined[0])


def test_equal_overlap_keeps_relevance_order_and_repeated_calls_are_deterministic():
    art = _case(overlap=False)
    ranked = [3, 2, 1, 0, 5, 4]
    assert decision._select(art, ranked, decision.DEFAULT) == ranked[:4]
    assert decision._select(art, ranked, decision.DEFAULT) == ranked[:4]


def test_one_card_keeps_ranking_and_declines_without_building_pair_cache(monkeypatch):
    art = _case()
    def forbidden(*args, **kwargs):
        raise AssertionError("One-card decisions do not need pair measurements")
    monkeypatch.setattr(diversity, "redundancy_matrix", forbidden)
    config = replace(decision.DEFAULT, count=1)
    ranked = [2, 1, 0]
    assert decision._select(art, ranked, config) == [2]
    assert decision._select(art, ranked, config, [[art.ids[2]]]) == [1]
    assert decision._select(art, [], config) == []


@pytest.mark.parametrize("count", [1, 4, 8, 20])
def test_manual_fallback_retains_count_filters_and_read_only_state(monkeypatch, count):
    import copy
    art = _case(size=22, overlap=False, same_brand=True)
    sess = {"m": np.ones(content.N_COMPONENTS), "v": np.ones(content.N_COMPONENTS),
            "like_ids": [], "dislike_ids": [], "n": 0, "memory": [], "hist": [],
            "shown": set(), "decision_checkpoints": []}
    before = copy.deepcopy(sess)
    z = np.zeros((content.N_COMPONENTS, len(art.ids)))
    z[content.IDX["pop"]] = np.arange(len(art.ids), dtype=float)
    monkeypatch.setattr(content, "load", lambda: art)
    monkeypatch.setattr(decision, "_components", lambda *args, **kwargs: z.copy())
    monkeypatch.setattr(decision, "_scores", lambda *args, **kwargs:
                        (np.zeros(len(art.ids)), np.zeros(len(art.ids)), np.zeros(len(art.ids))))
    config = replace(decision.DEFAULT, count=count)
    allowed = set(art.ids[:count])
    result = decision.recommend(sess, allow=allowed, config=config)
    assert result["ready"] and len(result["store_ids"]) == count
    assert set(result["store_ids"]) == allowed
    assert decision.recommend(sess, allow=allowed, config=config) == result
    assert not decision.recommend(sess, allow=set(art.ids[:count-1]), config=config)["ready"]
    for key in before:
        if isinstance(before[key], np.ndarray):
            np.testing.assert_array_equal(sess[key], before[key])
        else:
            assert sess[key] == before[key]
