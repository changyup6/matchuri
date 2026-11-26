"""Manual choice is independent of automatic prompting and model confidence.

Use a deterministic synthetic catalogue, with real scoring/filtering/HTTP code.
These are contract regressions, not a recommendation accuracy experiment.
"""
import pickle

import numpy as np
import pytest

from app import decision_feedback, geo, loader, state
from app.model import Card
from recsys import content, policy, store, swipe
from test_user_sessions import api, new, path, person


@pytest.fixture
def manual_api(api, monkeypatch):
    ids = [f'manual-{i:02}' for i in range(40)]
    stores = {
        sid: dict(store_id=sid, name=f'검증 가게 {i}', address='검증 주소',
                  cat2='한식' if i < 20 else '일식', cat3=f'세부 업종 {i}',
                  lat=37.5 + (i % 20) * .001, lon=127.,
                  kakao_review_count=10, kakao_avg_score=4.5,
                  review_count=10, review_avg=4.5, tags=['혼밥'])
        for i, sid in enumerate(ids)
    }
    sim = np.full((40, 40), .2)
    np.fill_diagonal(sim, 1)
    artifacts = content.Artifacts(
        ids=ids, pos={sid: i for i, sid in enumerate(ids)},
        pop=np.arange(40, dtype=float), quality=np.linspace(-1, 1, 40),
        sim_menu=sim.copy(), sim_text=sim.copy(), sim_review=sim.copy(),
        sim_food=sim.copy(), cooc=sim.copy(), brand=ids.copy(),
        has={kind: np.ones(40, dtype=bool) for kind in ('menu', 'text', 'review', 'food')},
        prior={'mean': {'pop': 1., 'quality': .3, 'menu': .5, 'neg': -.25},
               'sd': {kind: .5 for kind in content.COMPONENTS}},
    )
    cards = {i: Card(card_id=i, store=loader._store(stores[sid], []), image=None)
             for i, sid in enumerate(ids)}
    monkeypatch.setattr(content, 'load', lambda *a, **kw: artifacts)
    monkeypatch.setattr(swipe, 'pool', lambda: {
        'ids': ids, 'pos': artifacts.pos, 'qz': artifacts.quality,
        'cat': {sid: stores[sid]['cat2'] for sid in ids},
        'X': np.zeros((40, 3 + len(swipe.CATS) + 1 + len(swipe.PRICE_NAMES))),
    })
    monkeypatch.setattr(store, 'get_store', lambda sid: stores.get(sid))
    monkeypatch.setattr(store, 'all_store_ids', lambda: ids)
    monkeypatch.setattr(loader, 'get_card', lambda cid, **kw: cards.get(cid))
    monkeypatch.setattr(loader, 'to_card_ids', lambda selected: [artifacts.pos[sid] for sid in selected])
    monkeypatch.setattr(loader, 'to_store_id', lambda cid: ids[cid] if 0 <= cid < len(ids) else None)
    monkeypatch.setattr(geo, 'coords', lambda: {sid: (s['lat'], s['lon']) for sid, s in stores.items()})
    monkeypatch.setattr(decision_feedback, '_offers', {})
    return api


def session(api):
    uid = person(api)
    sid = new(api, uid)
    return uid, sid, path(uid, sid)


def test_cold_manual_choice_keeps_automatic_gate_and_score_honest(manual_api):
    api = manual_api
    uid, sid, base = session(api)
    summary_before = api.get(base + '/decision/metrics/summary').json()
    full_before = api.get(base + '/decision/metrics').json()
    memory_before = pickle.dumps(policy.session_for(sid))
    preferences_before = state.PREFS.read_bytes()
    exposures_before = state.EXPOSURES.read_bytes() if state.EXPOSURES.exists() else None
    manual = api.get(base + '/decision')
    assert manual.status_code == 200, manual.text
    out = manual.json()
    assert set(out) == {'ready', 'status', 'proposal_id', 'message', 'cards'}
    assert out['ready'] and out['status'] == 'ready' and len(out['cards']) == 4
    assert out['proposal_id'] and out['message']
    assert all(not card['liked'] for card in out['cards'])
    assert all(card['recommendation_reason'] for card in out['cards'])
    text = out['message'] + ' '.join(card['recommendation_reason'] for card in out['cards'])
    assert '적합 기준을 통과' not in text and '취향에 맞는' not in text
    auto = api.get(base + '/decision?mode=auto').json()
    assert auto == dict(ready=False, status='learning', proposal_id=None, message=None, cards=[])
    assert not full_before['decision']['ready']
    assert api.get(base + '/decision/metrics').json() == full_before
    assert api.get(base + '/decision/metrics/summary').json() == summary_before
    assert summary_before['decision_readiness_score'] == summary_before['personalization']['readiness_score'] == 0
    assert api.get(base + '/decision?mode=manual').json() == out
    assert api.get(f'/users/{sid}/decision').json() == out
    # Recording the offer is intentional; learning, RNG and swipe exposures stay untouched.
    assert pickle.dumps(policy.session_for(sid)) == memory_before
    assert state.PREFS.read_bytes() == preferences_before
    assert (state.EXPOSURES.read_bytes() if state.EXPOSURES.exists() else None) == exposures_before


@pytest.mark.parametrize('n', [1, 4, 20])
def test_manual_choice_accepts_exact_requested_card_count(manual_api, n):
    api = manual_api
    _, _, base = session(api)
    result = api.get(base + '/decision', params={'n': n})
    assert result.status_code == 200, result.text
    out = result.json()
    assert out['ready'] and len({card['card_id'] for card in out['cards']}) == n
    assert api.get(base + '/decision/metrics/summary', params={'n': n}).json()['decision_readiness_score'] == 0


def test_manual_choice_preserves_filters_and_requires_location(manual_api):
    api = manual_api
    _, _, base = session(api)
    assert api.put(base + '/filter', json={'cat': ['한식'], 'tag': ['혼밥'], 'within_m': 500}).status_code == 200
    missing = api.get(base + '/decision').json()
    assert missing['status'] == 'location_required' and not missing['ready'] and not missing['cards']
    assert api.put(base + '/location', json={'lat': 37.5, 'lon': 127.}).status_code == 200
    out = api.get(base + '/decision').json()
    assert out['ready'] and len(out['cards']) == 4
    assert all(card['store']['category_group'] == '한식' and card['distance_m'] <= 500 for card in out['cards'])
    assert api.put(base + '/filter', json={'cat': ['한식'], 'within_m': 50}).status_code == 200
    scarce = api.get(base + '/decision').json()
    assert scarce['status'] == 'insufficient_candidates' and not scarce['ready']
    assert scarce['cards'] == [] and scarce['proposal_id'] is None
    assert api.get(base + '/decision?n=1').json()['ready']


def test_continue_changes_next_manual_slate_without_creating_dislikes(manual_api):
    api = manual_api
    uid, sid, base = session(api)
    first = api.get(base + '/decision').json()
    memory_before = pickle.dumps(policy.session_for(sid))
    score_before = api.get(base + '/decision/metrics/summary').json()
    result = api.post(base + '/decision/feedback', json={'proposal_id': first['proposal_id'], 'action': 'continue'})
    assert result.status_code == 200, result.text
    second = api.get(base + '/decision').json()
    assert second['ready'] and second['proposal_id'] != first['proposal_id']
    assert {c['card_id'] for c in first['cards']} != {c['card_id'] for c in second['cards']}
    assert pickle.dumps(policy.session_for(sid)) == memory_before
    assert api.get(base + '/decision/metrics/summary').json() == score_before
    sibling = new(api, uid)
    assert api.post(path(uid, sibling) + '/decision/feedback', json={
        'proposal_id': second['proposal_id'], 'action': 'continue'}).status_code == 404
    other = person(api, '다른 사용자')
    assert api.get(path(other, sid) + '/decision').status_code == 404
    assert api.get(path(other, sid) + '/decision?mode=auto').status_code == 404


def test_bundled_swipe_still_uses_automatic_prompting(manual_api):
    api = manual_api
    _, _, base = session(api)
    manual = api.get(base + '/decision').json()
    result = api.post(base + '/preferences?include_decision=true&include_decision_metrics=true', json={
        'card_id': manual['cards'][0]['card_id'], 'prefer': 'like'})
    assert result.status_code == 200, result.text
    out = result.json()
    assert not out['decision']['ready'] and out['decision']['cards'] == []
    assert not out['decision_metrics']['decision']['ready']
    assert api.get(base + '/decision').json()['ready']
    assert api.get(base).json()['swipes'] == 1


def test_manual_mode_openapi_and_input_validation(manual_api):
    api = manual_api
    _, sid, base = session(api)
    for endpoint in (base + '/decision', f'/users/{sid}/decision'):
        assert api.get(endpoint, params={'mode': 'invalid'}).status_code == 422
        assert api.get(endpoint, params={'n': 0}).status_code == 422
        assert api.get(endpoint, params={'n': 21}).status_code == 422
    schema = api.get('/openapi.json').json()
    for endpoint in ('/user/{user_id}/session/{session_id}/decision', '/users/{user_id}/decision'):
        params = {p['name']: p for p in schema['paths'][endpoint]['get']['parameters']}
        assert params['mode']['schema']['default'] == 'manual'
        assert params['mode']['schema']['enum'] == ['manual', 'auto']
        assert params['n']['schema']['default'] == 4
    assert set(schema['components']['schemas']['DecisionOut']['properties']) == {
        'ready', 'status', 'proposal_id', 'message', 'cards'}
