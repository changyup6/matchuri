"""Proposal outcomes are slate feedback, never fabricated binary taste labels."""
import copy
import pickle
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from itertools import combinations
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import decision_feedback as feedback
from recsys import content, decision, decision_comments, decision_metrics, decision_summary, taste
from test_decision import case


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    from app import state
    monkeypatch.setattr(state, 'DIR', tmp_path)
    monkeypatch.setattr(feedback, '_offers', {})
    return tmp_path / 'decisions.jsonl'


def test_continue_rotates_combination_without_disliking_members(case):
    art, sess = case
    a = decision.evaluate(sess)
    assert a['ready']
    before = pickle.dumps(sess)
    state = dict(sess, declined_slates=[a['store_ids']])
    b = decision.evaluate(state)
    assert b['ready'] and set(b['store_ids']) != set(a['store_ids'])
    assert len(set(b['store_ids']) & set(a['store_ids'])) == 3
    assert b['qualified_count'] == a['qualified_count']
    assert pickle.dumps(sess) == before
    # Only one valid combination exists in this scope: don't relax quality.
    assert not decision.evaluate(state, allow=set(a['store_ids']))['ready']


def test_declined_search_matches_exhaustive_search(monkeypatch):
    # Compare the exact feasible seed search; vector refinement and its declined
    # constraints are exercised separately with complete similarity artifacts.
    monkeypatch.setattr(decision, '_refine', lambda art, ranked, chosen, *a, **kw: chosen)
    art = SimpleNamespace(ids=[str(i) for i in range(9)])
    for seed in range(6):
        rng = np.random.default_rng(seed)
        mat = rng.random((9, 9)) < .7
        mat = np.triu(mat, 1); mat |= mat.T
        monkeypatch.setattr(decision, '_diverse', lambda art, a, b, config: bool(mat[a, b]))
        for n in (1, 2, 4, 6):
            valid = [list(c) for c in combinations(range(9), n)
                     if all(mat[a, b] for a, b in combinations(c, 2))]
            declined = [[art.ids[i] for i in c] for c in valid[:3]]
            got = decision._select(art, list(range(9)), replace(decision.DEFAULT, count=n), declined)
            assert got == (valid[3] if len(valid) > 3 else [])


def test_outcome_is_durable_idempotent_and_scoped(ledger):
    context = {'categories':['한식'], 'tags':[], 'within_m':500}
    assert feedback.scope_key(context, (37.5,127.0)) == feedback.scope_key(context, (37.5001,127.0001))
    assert feedback.scope_key(context, None) != feedback.scope_key({**context,'within_m':1000}, None)
    pid = feedback.offer('u', 'area-a', {1:'s1', 2:'s2'})
    size = ledger.stat().st_size
    assert feedback.offer('u', 'area-a', {2:'s2', 1:'s1'}) == pid
    assert ledger.stat().st_size == size
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: feedback.record('u', pid, 'continue', None), range(8)))
    assert len(ledger.read_text().splitlines()) == 2
    assert feedback.declined('u', 'area-a') == [['s1','s2']]
    assert feedback.declined('u', 'area-b') == feedback.declined('other', 'area-a') == []
    feedback.restore()
    assert feedback.declined('u', 'area-a') == [['s1','s2']]
    with pytest.raises(RuntimeError): feedback.record('u', pid, 'select', 1)
    with pytest.raises(KeyError): feedback.record('other', pid, 'continue', None)
    # Stable store IDs are retained even if the catalogue later remaps numeric IDs.
    assert feedback._offers['u'][pid]['store_ids'] == ['s1', 's2']


def test_selected_card_and_failure_do_not_change_outcomes(ledger, monkeypatch):
    pid = feedback.offer('u', 'area', {1:'s1',2:'s2'})
    with pytest.raises(ValueError): feedback.record('u', pid, 'select', 3)
    original = feedback._save
    monkeypatch.setattr(feedback, '_save', lambda row: (_ for _ in ()).throw(OSError('full')))
    with pytest.raises(OSError): feedback.record('u', pid, 'continue', None)
    assert feedback.declined('u', 'area') == []
    monkeypatch.setattr(feedback, '_save', original)
    feedback.record('u', pid, 'select', 2)
    feedback.restore()
    row = feedback._offers['u'][pid]['outcome']
    assert row['card_id'] == 2 and row['store_id'] == 's2'
    assert feedback.declined('u', 'area') == []


def test_offer_retention_is_bounded_and_replayed(ledger, monkeypatch):
    monkeypatch.setattr(feedback, 'MAX_PROPOSALS', 3)
    first = feedback.offer('u', 'scope', {0:'s0'})
    for i in range(1, 5):feedback.offer('u', 'scope', {i:f's{i}'})
    feedback.restore()
    assert len(feedback._offers['u']) == 3
    with pytest.raises(KeyError):feedback.record('u', first, 'continue', None)


def test_comments_use_observed_positive_contributions_without_self_reference(art, sess):
    sess['like_ids'] = ['s0','s1'];sess['m'][:] = 0;sess['m'][content.IDX['menu']] = 1
    before = pickle.dumps(sess)
    reasons = decision_comments.reasons(sess, ['s0', 's1', 's4'])
    assert '가게일' in reasons['s0'] and '가게영' not in reasons['s0']
    assert '가게영' in reasons['s1'] and '가게일' not in reasons['s1']
    assert '메뉴 구성' in reasons['s0']
    assert '메뉴 구성' not in reasons['s4']  # missing menu cannot explain anything
    sess['m'][content.IDX['menu']] = -1
    assert '메뉴 구성' not in decision_comments.reasons(sess, ['s0'])['s0']
    sess['m'][content.IDX['menu']] = 1
    assert pickle.dumps(sess) == before


def test_summary_and_export_share_declined_combinations(art, sess):
    for sid, label in [('s0', True), ('s1', True), ('s2', False), ('s3', False)]:
        taste.record(sess, sid, label)
    sess['hist'] = [{'personal_rank': .9, 'like': True}] * 5 + [{'personal_rank': .1, 'like': False}] * 5
    config = replace(decision.DEFAULT, count=1, min_rated=4, min_likes=2, stable_top=2)
    before = decision_metrics.export(sess, config=config)
    qualified = [r['store_id'] for r in before['candidates'] if r['qualified']]
    assert before['decision']['ready'] and qualified
    after_state = dict(sess, declined_slates=[[s] for s in qualified])
    after = decision_metrics.export(after_state, config=config)
    summary = decision_summary.summarize(after_state, [], config=config)
    assert before['snapshot_id'] != after['snapshot_id']
    assert before['personalization'] == after['personalization']
    assert before['fit'] == after['fit']
    assert not after['decision']['ready']
    # Declining combinations changes availability, never the evidence for taste.
    assert summary['personalization']['readiness_score'] == 100
    assert summary['personalization']['ready'] and summary['decision_readiness_score'] == 80
    assert summary['personalization'] == decision_summary.summarize(sess, [], config=config)['personalization']
    assert not next(g for g in after['gates'] if g['name'] == 'diverse_fit_count')['passed']


def test_http_outcomes_validation_restart_and_existing_swipe(ledger, monkeypatch):
    import main
    from recsys import policy
    client = TestClient(main.app)
    uid = client.post('/users', json={}).json()['user_id']
    cards = client.get('/cards?limit=6').json()
    ids = [c['store']['id'] for c in cards]
    matrix = np.full((len(ids), len(ids)), .2, dtype=np.float32)
    np.fill_diagonal(matrix, 1)
    art = SimpleNamespace(ids=ids, brand=ids.copy(), **{
        'sim_' + kind: matrix.copy() for kind in decision_metrics.MODALITIES})
    def evaluate(sess, config=decision.DEFAULT, **kwargs):
        chosen = decision._select(art, list(range(6)), config, sess.get('declined_slates', ()))
        return dict(ready=bool(chosen), status='ready' if chosen else 'insufficient_candidates',
                    store_ids=[ids[i] for i in chosen], rated_count=20, liked_count=5,
                    stability=.9, validation_auc=.9, qualified_count=6)
    monkeypatch.setattr(decision, 'evaluate', evaluate)
    monkeypatch.setattr(decision, 'recommend', evaluate)
    url = f'/users/{uid}/decision'
    a = client.get(url).json()
    session = policy.session_for(uid); before = pickle.dumps(session)
    payload = {'proposal_id':a['proposal_id'], 'action':'continue'}
    assert client.post(url+'/feedback', json=payload).json() == {'ok':True}
    assert client.post(url+'/feedback', json=payload).status_code == 200
    assert pickle.dumps(session) == before
    b = client.get(url).json()
    assert b['ready'] and b['proposal_id'] != a['proposal_id']
    assert len({c['card_id'] for c in a['cards']} & {c['card_id'] for c in b['cards']}) == 3
    feedback.restore()
    assert client.get(url).json() == b
    choose = {'proposal_id': b['proposal_id'], 'action':'select', 'card_id':b['cards'][0]['card_id']}
    assert client.post(url+'/feedback', json=choose).status_code == 200
    assert pickle.dumps(session) == before
    assert client.post(url+'/feedback', json={**choose, 'action':'continue', 'card_id':None}).status_code == 409
    assert client.post(url+'/feedback', json={**payload, 'card_id':1}).status_code == 422
    assert client.post(url+'/feedback', json={'proposal_id':b['proposal_id'],'action':'select'}).status_code == 422
    assert client.post(url+'/feedback', json={**choose,'card_id':-1}).status_code == 422
    assert client.post('/users/some-other-user/decision/feedback', json=payload).status_code == 404
    assert client.post(url+'/feedback', json={**payload,'proposal_id':'0'*20}).status_code == 404
    swipe = client.post('/preferences?include_decision=true', json={
        'user_id':uid, 'card_id':cards[-1]['card_id'], 'prefer':'like'})
    assert swipe.status_code == 200 and session['taste']['n'] == 1
    assert {c['card_id'] for c in swipe.json()['decision']['cards']} != {c['card_id'] for c in a['cards']}
    # No false success if the new result cannot be persisted.
    c = client.get(url+'?n=1').json()
    monkeypatch.setattr(feedback, '_save', lambda row: (_ for _ in ()).throw(OSError('full')))
    assert client.post(url+'/feedback', json={'proposal_id':c['proposal_id'], 'action':'continue'}).status_code == 503
    saved = client.post('/preferences?include_decision=true&n=2', json={
        'user_id':uid, 'card_id':cards[-2]['card_id'], 'prefer':'like'})
    assert saved.status_code == 200 and saved.json()['decision'] is None
    assert session['taste']['n'] == 2
