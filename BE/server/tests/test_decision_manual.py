"""Explicit choice is available before automatic personalization gates pass."""
import copy
import pickle
from dataclasses import replace
from itertools import combinations
from types import SimpleNamespace

import numpy as np
import pytest

from recsys import content, decision, taste


@pytest.fixture
def manual_case(monkeypatch):
    size = 40
    ids = [f's{i:02}' for i in range(size)]
    sim = np.full((size, size), .2)
    np.fill_diagonal(sim, 1)
    art = SimpleNamespace(ids=ids, pos={sid: i for i, sid in enumerate(ids)},
                          brand=ids.copy(), sim_menu=sim.copy(), sim_text=sim.copy(),
                          sim_review=sim.copy(), sim_food=sim.copy())
    sess = {'m': np.ones(8), 'v': np.ones(8), 'like_ids': [], 'dislike_ids': [],
            'shown': set(), 'n': 0, 'hist': [], 'memory': [], 'decision_checkpoints': []}
    z = np.zeros((8, size))
    z[content.IDX['pop']] = np.arange(size)
    monkeypatch.setattr(content, 'load', lambda: art)
    monkeypatch.setattr(decision, '_components', lambda *a, **kw: z.copy())
    monkeypatch.setattr(decision, '_scores', lambda *a, **kw:
                        (np.arange(size, dtype=float), np.arange(size, dtype=float), np.zeros(size)))
    monkeypatch.setattr(decision, '_fine_groups', lambda a: dict.fromkeys(a.ids, ''))
    return art, sess


def test_zero_swipes_use_existing_default_rank_without_mutation(manual_case):
    _, sess = manual_case
    before = pickle.dumps(sess)
    result = decision.recommend(sess)
    assert result['ready'] and result['store_ids'] == ['s39', 's38', 's37', 's36']
    assert result['qualified_count'] == 0
    assert not decision.evaluate(sess)['ready']
    assert decision.recommend(sess) == result
    assert pickle.dumps(sess) == before


@pytest.mark.parametrize('kind', ['all_dislikes', 'low_auc', 'too_few_likes'])
def test_manual_is_available_when_automatic_quality_gates_fail(manual_case, kind):
    art, sess = manual_case
    sess.update(n=20, like_ids=art.ids[:4], dislike_ids=art.ids[4:20])
    if kind == 'all_dislikes':
        sess.update(like_ids=[], dislike_ids=art.ids[:20])
    elif kind == 'too_few_likes':
        sess.update(like_ids=art.ids[:1], dislike_ids=art.ids[1:20])
    else:
        # 검증 수는 채우고 **구분력만** 떨어뜨린다 — 그래야 이 갈래가 AUC 를 잰다.
        sess['hist'] = [{'personal_rank': .1, 'like': True}] * 5 + [
            {'personal_rank': .9, 'like': False}] * 15
    assert not decision.evaluate(sess)['ready']
    assert decision.recommend(sess)['ready']


def test_personal_evidence_and_uncertainty_drive_manual_ranking(manual_case, monkeypatch):
    art, sess = manual_case
    personal = np.arange(40, dtype=float)
    personal[0] = 50
    uncertainty = np.zeros(40)
    uncertainty[0] = 100
    monkeypatch.setattr(decision, '_scores', lambda *a, **kw:
                        (personal, personal, uncertainty))
    config = replace(decision.DEFAULT, count=1)
    assert decision.recommend(sess, config=config)['store_ids'] == ['s39']
    sess['memory'] = [{'store_id': art.ids[1], 'like': True, 'weight': 1}]
    assert decision.recommend(sess, config=config)['store_ids'] == ['s39']
    uncertainty[:] = 0
    assert decision.recommend(sess, config=config)['store_ids'] == ['s00']


def test_all_negative_fit_is_still_ranked_and_returned(manual_case, monkeypatch):
    _, sess = manual_case
    sess.update(n=1, dislike_ids=['s00'])
    scores = -np.arange(1, 41, dtype=float)
    monkeypatch.setattr(decision, '_scores', lambda *a, **kw:
                        (scores, scores, np.zeros(40)))
    result = decision.recommend(sess)
    assert result['qualified_count'] == 0 and result['ready']
    assert result['store_ids'] == ['s00', 's01', 's02', 's03']


@pytest.mark.parametrize('kind', ['brand', 'fine_category', 'similarity', 'missing'])
def test_similar_or_missing_candidates_do_not_block_explicit_choice(manual_case, monkeypatch, kind):
    art, sess = manual_case
    if kind == 'brand':
        art.brand = ['same'] * 40
    elif kind == 'fine_category':
        monkeypatch.setattr(decision, '_fine_groups', lambda a: dict.fromkeys(a.ids, '돈까스'))
    elif kind == 'similarity':
        art.sim_menu[:] = .95
    else:
        art.sim_text[:] = art.sim_review[:] = art.sim_food[:] = np.nan
    result = decision.recommend(sess, allow=set(art.ids[:4]))
    assert result['ready'] and set(result['store_ids']) == set(art.ids[:4])


def test_soft_selection_prefers_less_similar_pairs_without_new_weights(manual_case):
    art, _ = manual_case
    art.brand = ['same'] * 40  # a strict slate cannot exist
    for matrix in (art.sim_menu, art.sim_text, art.sim_review, art.sim_food):
        matrix[0, 1] = matrix[1, 0] = .9
        matrix[0, 2] = matrix[2, 0] = .3
        matrix[0, 3] = matrix[3, 0] = .1
    result = decision._soft_select(art, [0, 1, 2, 3], replace(decision.DEFAULT, count=2))
    assert result == [0, 3]


def test_diverse_choice_can_come_from_outside_exact_search_window(manual_case):
    art, sess = manual_case
    art.brand = ['same'] * 40
    art.brand[:3] = ['different-a', 'different-b', 'different-c']
    result = decision.recommend(sess, config=replace(decision.DEFAULT, search_limit=4))
    assert result['ready'] and set(result['store_ids']) == {'s39', 's02', 's01', 's00'}


@pytest.mark.parametrize('count', [1, 4, 8, 20])
def test_exact_requested_count_filters_and_rank_adjustments(manual_case, count):
    art, sess = manual_case
    config = replace(decision.DEFAULT, count=count)
    allowed = set(art.ids[:count + 1])
    result = decision.recommend(sess, config=config, allow=allowed, tiers={'s00': 1})
    assert result['ready'] and len(set(result['store_ids'])) == count
    assert set(result['store_ids']) <= allowed and result['store_ids'][0] == 's00'
    boosted = decision.recommend(sess, config=config, allow=allowed, bonus={'s00': 100})
    assert boosted['store_ids'][0] == 's00'
    scarce = decision.recommend(sess, config=config, allow=set(art.ids[:count - 1]))
    assert scarce['status'] == 'insufficient_candidates' and scarce['store_ids'] == []


def test_manual_continue_changes_slate_without_learning_dislikes(manual_case):
    art, sess = manual_case
    art.brand = ['same'] * 40
    allowed = set(art.ids[:6])
    before = pickle.dumps(sess)
    first = decision.recommend(sess, allow=allowed)
    second_sess = dict(sess, declined_slates=[first['store_ids']])
    second = decision.recommend(second_sess, allow=allowed)
    assert first['ready'] and second['ready']
    assert set(first['store_ids']) != set(second['store_ids'])
    assert pickle.dumps(sess) == before
    assert not decision.recommend(second_sess, allow=set(first['store_ids']))['ready']


def test_all_rejected_combinations_are_exhausted_without_repeating(manual_case):
    art, sess = manual_case
    art.brand = ['same'] * 40
    allowed = set(art.ids[:6])
    sess['declined_slates'] = []
    for _ in combinations(allowed, 4):
        result = decision.recommend(sess, allow=allowed)
        assert result['ready']
        chosen = result['store_ids']
        assert set(chosen) not in [set(slate) for slate in sess['declined_slates']]
        sess['declined_slates'].append(chosen)
    assert not decision.recommend(sess, allow=allowed)['ready']


def test_existing_qualified_diverse_selection_is_preserved(manual_case):
    art, sess = manual_case
    sess.update(n=16, like_ids=art.ids[:4], dislike_ids=art.ids[4:16])
    # 긍정·부정 검증이 각각 min_validation_each(5) 이상이어야 자동 판정이 열린다.
    sess['hist'] = [{'personal_rank': .9, 'like': True}] * 5 + [
        {'personal_rank': .1, 'like': False}] * 11
    sess['decision_checkpoints'] = [copy.deepcopy(sess) for _ in range(4)]
    automatic = decision.evaluate(sess)
    manual = decision.recommend(sess)
    assert automatic['ready'] and manual['store_ids'] == automatic['store_ids']
    assert manual['qualified_count'] == automatic['qualified_count']


def test_real_components_keep_leave_one_out_and_session_immutable(art, sess):
    taste.record(sess, art.ids[0], True)
    taste.record(sess, art.ids[2], False)
    before = pickle.dumps(sess)
    result = decision.recommend(sess)
    assert result['ready'] and len(result['store_ids']) == 4
    assert pickle.dumps(sess) == before


def test_missing_artifacts_remain_unavailable(manual_case, monkeypatch):
    _, sess = manual_case
    monkeypatch.setattr(content, 'load', lambda: None)
    assert decision.recommend(sess)['status'] == 'unavailable'
