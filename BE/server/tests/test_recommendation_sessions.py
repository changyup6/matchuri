"""Durable session boundaries, stale clients, and replay compatibility."""
import copy
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from app import state, recommendation_sessions as sessions
from recsys import policy, taste, swipe


@pytest.fixture
def api(tmp_path, monkeypatch):
    import main
    for key in ('PREFS', 'USERS', 'EXPOSURES', 'BOOKMARKS'):
        monkeypatch.setattr(state, key, tmp_path / (key.lower() + '.jsonl'))
    monkeypatch.setattr(state, 'DIR', tmp_path)
    monkeypatch.setattr(state, '_enabled', True)
    monkeypatch.setattr(sessions, '_active', {})
    monkeypatch.setattr(sessions, '_initial', {})
    for module in (policy, swipe, taste):
        monkeypatch.setattr(module, '_sessions', {})
    monkeypatch.setattr(policy, '_exposed', {})
    monkeypatch.setattr(main, 'logs', [])
    return TestClient(main.app)


def new_user(api):
    uid = api.post('/users', json={}).json()['user_id']
    return uid, api.get(f'/users/{uid}/session').json()['session_id']


def feedback(api, uid, sid, cid, like=True):
    return api.post('/preferences', json={'user_id': uid, 'session_id': sid,
        'card_id': cid, 'prefer': 'like' if like else 'dislike'})


def test_rotation_keeps_user_data_but_resets_progress(api):
    uid, old = new_user(api)
    cards = api.get('/cards', params={'user_id': uid, 'limit': 3}).json()
    assert feedback(api, uid, old, cards[0]['card_id']).status_code == 200
    api.put(f'/users/{uid}/bookmarks/{cards[0]["card_id"]}')
    api.put(f'/users/{uid}/filter', json={'cat': ['한식']})
    api.put(f'/users/{uid}/location', json={'lat': 37.5, 'lon': 127.03})
    before = api.get(f'/users/{uid}').json()
    response = api.post(f'/users/{uid}/sessions', json={'previous_session_id': old})
    assert response.status_code == 200, response.text
    new = response.json()
    assert set(new) == {'user_id', 'session_id', 'swipes'}
    assert new['session_id'] != old and new['swipes'] == 0
    after = api.get(f'/users/{uid}').json()
    for key in ('user_id', 'created_at', 'bookmarks', 'location', 'filter'):
        assert after[key] == before[key]
    session = policy.session_for(uid)['taste']
    assert session['memory'] and not session['hist'] and not session['shown']
    assert not session['decision_checkpoints']
    assert not api.get(f'/users/{uid}/decision?mode=auto').json()['ready']
    assert api.get('/cards', params={'user_id': uid, 'session_id': old}).status_code == 409
    assert feedback(api, uid, old, cards[1]['card_id']).status_code == 409
    assert feedback(api, uid, new['session_id'], cards[1]['card_id']).status_code == 200
    assert api.post('/preferences', json={'user_id': uid, 'card_id': cards[2]['card_id'], 'prefer': 'like'}).status_code == 200


def test_retry_is_idempotent_and_foreign_session_rejected(api):
    uid, old = new_user(api)
    first = api.post(f'/users/{uid}/sessions', json={'previous_session_id': old}).json()
    saved = state.PREFS.read_bytes()
    assert api.post(f'/users/{uid}/sessions', json={'previous_session_id': old}).json() == first
    assert state.PREFS.read_bytes() == saved
    other, _ = new_user(api)
    assert api.post(f'/users/{other}/sessions', json={'previous_session_id': first['session_id']}).status_code == 409
    assert api.post('/users/missing/sessions').status_code == 404


def test_learning_restores_in_timeline_order_and_map_replays_current_session(api):
    import main
    uid, sid = new_user(api)
    cards = api.get('/cards', params={'user_id': uid, 'limit': 6}).json()
    for i, card in enumerate(cards[:3]):
        assert feedback(api, uid, sid, card['card_id'], i % 2 == 0).status_code == 200
    sid = api.post(f'/users/{uid}/sessions').json()['session_id']
    for i, card in enumerate(cards[3:]):
        assert feedback(api, uid, sid, card['card_id'], i % 2 != 0).status_code == 200
    before = copy.deepcopy(policy.session_for(uid))
    baseline = sessions.initial_taste(uid)
    for module in (policy, swipe, taste):
        module._sessions.clear()
    sessions._active.clear()
    sessions._initial.clear()
    assert state.restore({}, [], main._apply, sessions.apply_boundary)['preferences'] == 6
    assert sessions.current_id(uid) == sid
    after = policy.session_for(uid)
    for name in ('taste', 'legacy'):
        np.testing.assert_array_equal(before[name]['m'], after[name]['m'])
        np.testing.assert_array_equal(before[name]['v'], after[name]['v'])
        assert after[name]['n'] == 3
    assert after['taste']['memory'] == before['taste']['memory']
    np.testing.assert_array_equal(baseline['m'], sessions.initial_taste(uid)['m'])
    live = api.get(f'/admin/api/map/user/{uid}').json()
    replay = api.get(f'/admin/api/map/user/{uid}?upto=3').json()
    zero = api.get(f'/admin/api/map/user/{uid}?upto=0').json()
    assert live['scores'] == replay['scores'] and live['prefs'] == 3
    assert zero['swipes'] == 0 and zero['session_id'] == sid
    assert live['feedback'] == replay['feedback']
    assert [r['n'] for r in live['feedback']] == [1, 2, 3]
    assert zero['feedback'] == []  # 이전 세션은 현재 반응 타임라인에 섞지 않는다.
    for snapshot in (live, replay, zero):
        focus = snapshot['focus']
        assert sum(focus['parts'].values()) == pytest.approx(snapshot['scores'][focus['idx']], abs=0.00006)


def test_failed_persistence_does_not_reset_state(api, monkeypatch, tmp_path):
    uid, sid = new_user(api)
    monkeypatch.setattr(state, 'PREFS', tmp_path)
    assert api.post(f'/users/{uid}/sessions').status_code == 503
    assert sessions.current_id(uid) == sid


def test_legacy_rows_readable_and_boundaries_are_not_preferences(api):
    import main
    uid, _ = new_user(api)
    cid = api.get('/cards?limit=1').json()[0]['card_id']
    state.PREFS.write_text(json.dumps({'user_id': uid, 'card_id': cid, 'prefer': 'like'}) + '\n')
    state.restore({}, [], main._apply, sessions.apply_boundary)
    assert policy.session_for(uid)['taste']['n'] == 1
    api.post(f'/users/{uid}/sessions')
    assert len(list(state.read_preferences())) == 1


def test_other_users_do_not_evict_durable_learning_or_session_memory(api):
    uid, sid = new_user(api)
    cards = api.get('/cards', params={'user_id': uid, 'limit': 3}).json()
    assert feedback(api, uid, sid, cards[0]['card_id']).status_code == 200
    sid = api.post(f'/users/{uid}/sessions').json()['session_id']
    assert feedback(api, uid, sid, cards[1]['card_id'], False).status_code == 200
    before = copy.deepcopy(policy.session_for(uid))
    # The old 300-entry caches discarded this user even though policy retained
    # references. The next lookup silently replaced both models with empty ones.
    for i in range(301):
        policy.session_for(f'u_other_{i}')
    assert uid in taste.sessions() and uid in swipe.sessions()
    assert api.get(f'/users/{uid}/session').json()['swipes'] == 1
    after = policy.session_for(uid)
    for name in ('taste', 'legacy'):
        assert after[name]['hist'] == before[name]['hist']
        np.testing.assert_array_equal(after[name]['m'], before[name]['m'])
    assert after['taste']['memory'] == before['taste']['memory']
    assert sessions.current_id(uid) == sid
