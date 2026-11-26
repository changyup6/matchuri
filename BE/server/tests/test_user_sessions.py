"""Actual person → independent sessions, migration, ownership and durable replay."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import identities, state, users, recommendation_sessions as sessions
from recsys import policy, swipe, taste


@pytest.fixture
def api(tmp_path, monkeypatch):
    import main
    for key in ('PREFS', 'USERS', 'EXPOSURES', 'BOOKMARKS', 'DIRECTIONS', 'SURVEYS', 'SURVEY_PHOTOS'):
        monkeypatch.setattr(state, key, tmp_path / (key.lower() + '.jsonl'))
    monkeypatch.setattr(state, 'DIR', tmp_path)
    monkeypatch.setattr(state, '_enabled', True)
    for module, keys in ((sessions, ('_active', '_initial', '_user_memory')),
                         (identities, ('_people', '_sessions')), (users, ('_users',)),
                         (policy, ('_sessions', '_exposed')), (taste, ('_sessions',)),
                         (swipe, ('_sessions',))):
        for key in keys:
            monkeypatch.setattr(module, key, {})
    monkeypatch.setattr(main, 'logs', [])
    return TestClient(main.app)


def person(api, name='검증 사용자'):
    r = api.post('/user', json={'name': name})
    assert r.status_code == 200, r.text
    return r.json()['user_id']


def new(api, uid, key=None):
    r = api.post(f'/user/{uid}/session', json={'request_id': key})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {'user_id', 'session_id', 'swipes'}
    return r.json()['session_id']


def path(uid, sid):
    return f'/user/{uid}/session/{sid}'


def react(api, uid, sid, cid, like=True):
    r = api.post(path(uid, sid) + '/preferences', json={'card_id': cid, 'prefer': 'like' if like else 'dislike'})
    assert r.status_code == 200, r.text
    return r.json()


def test_swipe_response_carries_only_the_light_learn_state(api):
    """**앱이 실제로 쓰는 주소**가 관리자용 두 항목을 안 싣는지 모양으로 잠근다.

    `POST /preferences` 와 `GET /users/{id}` 에도 같은 잠금이 있지만 그 둘은 호환 API 다
    (`app/user_api.py` 가 `deprecated = True` 로 표시한다). FE 가 부르는 것은 이 주소다
    (`FE/app/lib/services/restaurant_api.dart`). 그 둘만 잠그면 정작 운영 경로가 다시
    무거워져도 스위트가 조용히 통과한다 — 2026-09-22 반박이 지적한 자리다.

    이름 둘의 부재가 아니라 **키 집합**을 잠그는 이유도 같다. 부재만 보면 남아야 할 필드가
    사라져도, 게이트 없는 새 무거운 필드가 들어와도 통과한다(둘 다 실제로 재현됐다).
    """
    uid = person(api)
    sid = new(api, uid)
    cards = api.get(path(uid, sid) + '/cards?limit=3').json()
    learned = react(api, uid, sid, cards[0]['card_id'])['learned']

    assert set(learned['policies']) == {'legacy', 'taste'}
    assert set(learned['policies']['taste']) == {
        'policy', 'available', 'weights', 'top', 'likes', 'dislikes', 'swipes', 'prior'}
    assert set(learned['policies']['legacy']) == {
        'quality_weight', 'top', 'cats', 'price', 'base'}
    assert 'hist' not in learned and 'prototypes' not in learned


def test_sessions_stay_independent_and_old_session_can_continue(api):
    uid = person(api)
    assert uid not in policy.sessions()
    a = new(api, uid)
    cards = api.get(path(uid, a) + '/cards?limit=4').json()
    react(api, uid, a, cards[0]['card_id'])
    before = copy.deepcopy(policy.session_for(a))
    b = new(api, uid)
    assert a != b != uid
    assert policy.session_for(a)['taste']['hist'] == before['taste']['hist']
    fresh = policy.session_for(b)['taste']
    assert fresh['n'] == 0 and not fresh['shown'] and not fresh['decision_checkpoints']
    assert fresh['memory']
    base = taste._new_session()['m']
    np.testing.assert_allclose(fresh['m'], base + .2 * (before['taste']['m'] - base))
    react(api, uid, b, cards[1]['card_id'], False)
    react(api, uid, a, cards[2]['card_id'])
    assert api.get(path(uid, a)).json()['swipes'] == 2
    assert api.get(path(uid, b)).json()['swipes'] == 1
    assert len(api.get(path(uid, a) + '/preferences?prefer=all').json()) == 2
    assert len(api.get(path(uid, b) + '/preferences?prefer=all').json()) == 1
    c = new(api, uid)
    # Includes feedback from both sessions, including a resumed older session.
    assert len(policy.session_for(c)['taste']['memory']) == 3
    assert len(api.get(f'/user/{uid}/session').json()) == 3
    assert api.get('/cards', params={'user_id': uid}).status_code == 400


def test_wrong_owner_rejected_for_every_session_operation(api):
    uid, other = person(api), person(api, '다른 사람')
    sid = new(api, uid)
    foreign = path(other, sid)
    for suffix in ('', '/cards', '/settings', '/preferences', '/decision',
                   '/decision/metrics', '/decision/metrics/summary', '/taste'):
        r = api.get(foreign + suffix)
        assert r.status_code == 404, (suffix, r.text)
    assert api.post(foreign + '/preferences', json={'card_id': 1, 'prefer': 'like'}).status_code == 404
    assert api.put(foreign + '/filter', json={'cat': ['한식']}).status_code == 404
    assert api.put(foreign + '/location', json={'lat': 37, 'lon': 127}).status_code == 404
    assert api.post(foreign + '/decision/feedback', json={'proposal_id': 'a' * 20, 'action': 'continue'}).status_code == 404
    clean = new(api, other)
    assert not policy.session_for(clean)['taste']['memory']


def test_retry_and_parallel_creation_are_idempotent(api):
    uid = person(api)
    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(lambda _: new(api, uid, 'request-1'), range(4)))
    assert len(set(ids)) == 1
    assert len(list(state._read(state.PREFS))) == 1


def replay():
    import main
    for module in (policy, taste, swipe):
        module._sessions.clear()
    sessions._active.clear()
    sessions._initial.clear()
    sessions._user_memory.clear()
    users._users.clear()
    identities.restore()
    main.logs.clear()
    state.restore(users.all_users(), main.logs, main._apply, sessions.apply_boundary)


def test_restart_restores_all_sessions_and_creation_time_prior(api):
    uid = person(api)
    a = new(api, uid)
    cards = api.get(path(uid, a) + '/cards?limit=5').json()
    react(api, uid, a, cards[0]['card_id'])
    b = new(api, uid, 'stable-key')
    react(api, uid, b, cards[1]['card_id'], False)
    react(api, uid, a, cards[2]['card_id'])
    assert api.put(path(uid, b) + '/filter', json={'cat': ['한식']}).status_code == 200
    before = copy.deepcopy(policy.sessions())
    initials = {sid: sessions.initial_taste(sid) for sid in (a, b)}
    raw = state.PREFS.read_bytes()
    replay()
    assert state.PREFS.read_bytes() == raw
    for sid in (a, b):
        after = policy.session_for(sid)
        for key in ('taste', 'legacy'):
            assert after[key]['n'] == before[sid][key]['n']
            np.testing.assert_array_equal(after[key]['m'], before[sid][key]['m'])
            np.testing.assert_array_equal(after[key]['v'], before[sid][key]['v'])
        np.testing.assert_array_equal(sessions.initial_taste(sid)['m'], initials[sid]['m'])
    assert api.get(path(uid, b) + '/settings').json()['filter']['cat'] == ['한식']
    assert new(api, uid, 'stable-key') == b


def test_legacy_migration_preserves_old_id_boundaries_and_bookmarks(api):
    old = api.post('/users', json={'name': '기존 기록'}).json()['user_id']
    cards = api.get('/cards', params={'user_id': old, 'limit': 3}).json()
    first_label = sessions.current_id(old)
    api.post('/preferences', json={'user_id': old, 'card_id': cards[0]['card_id'], 'prefer': 'like'})
    api.put(f'/users/{old}/bookmarks/{cards[0]["card_id"]}')
    api.post(f'/users/{old}/sessions')
    api.post('/preferences', json={'user_id': old, 'card_id': cards[1]['card_id'], 'prefer': 'dislike'})
    owner = api.get(f'/session/{old}').json()['user_id']
    assert owner != old
    original = state.PREFS.read_bytes()
    replay()
    assert state.PREFS.read_bytes() == original
    assert api.get(f'/session/{old}').json()['user_id'] == owner
    for sid in (old, first_label):
        assert api.get(path(owner, sid)).json()['swipes'] == 1
        assert len(api.get(path(owner, sid) + '/preferences?prefer=all').json()) == 1
    assert api.get(f'/user/{owner}/bookmarks').json()[0]['card_id'] == cards[0]['card_id']
    new_sid = new(api, owner)
    assert len(policy.session_for(new_sid)['taste']['memory']) == 2


def test_settings_are_session_scoped_bookmarks_are_user_scoped(api):
    uid = person(api)
    a, b = new(api, uid), new(api, uid)
    cid = api.get(path(uid, a) + '/cards?limit=1').json()[0]['card_id']
    api.put(path(uid, a) + '/filter', json={'cat': ['한식']})
    assert api.get(path(uid, a) + '/settings').json()['filter']['cat'] == ['한식']
    assert api.get(path(uid, b) + '/settings').json()['filter'] is None
    api.put(f'/user/{uid}/bookmarks/{cid}')
    from app import bookmarks
    assert bookmarks.has(identities.bookmark_key(a), cid)
    assert bookmarks.has(identities.bookmark_key(b), cid)
    assert api.get(f'/user/{uid}/bookmarks').json()[0]['card_id'] == cid
    assert api.get(path(uid, b) + f'/cards/{cid}').json()['bookmarked'] is True
    other = person(api, '다른 사용자')
    assert api.get(path(other, b) + f'/cards/{cid}').status_code == 404


def test_persistence_failure_returns_error_without_creating_model(api, monkeypatch, tmp_path):
    uid = person(api)
    monkeypatch.setattr(state, 'PREFS', tmp_path)
    before = set(policy.sessions())
    assert api.post(f'/user/{uid}/session').status_code == 503
    assert set(policy.sessions()) == before
    assert api.get(f'/user/{uid}/session').json() == []


def test_openapi_uses_path_identity_and_minimal_preference_body(api):
    schema = api.get('/openapi.json').json()
    op = schema['paths']['/user/{user_id}/session/{session_id}/cards']['get']
    assert {(p['name'], p['in']) for p in op['parameters']} == {
        ('user_id', 'path'), ('session_id', 'path'), ('limit', 'query'), ('reviews', 'query')}
    body = schema['components']['schemas']['PreferenceIn']
    assert set(body['properties']) == {'card_id', 'prefer'}
    assert body['additionalProperties'] is False
    summary = schema['paths']['/user/{user_id}/session/{session_id}/decision/metrics/summary']['get']
    assert summary['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/DecisionSummaryOut')
    assert schema['paths']['/users']['post']['deprecated'] is True


def test_durable_records_have_both_ids_and_exposures_restore_per_session(api):
    uid = person(api)
    a, b = new(api, uid), new(api, uid)
    cards = api.get(path(uid, a) + '/cards?limit=3').json()
    react(api, uid, a, cards[0]['card_id'])
    row = list(state._read(state.PREFS))[-1]
    assert row['user_id'] == uid and row['session_id'] == a
    assert all(r['user_id'] == uid and r['session_id'] == a for r in state._read(state.EXPOSURES))
    expected = set(policy.session_for(a)['taste']['shown'])
    replay()
    from app import loader
    policy._exposed.clear()
    policy.restore_exposures(loader.to_store_id)
    assert policy.session_for(a)['taste']['shown'] == expected
    assert not policy.session_for(b)['taste']['shown']
    assert policy.session_for(a)['batch'] == 1
    fresh = api.get(path(uid, a) + '/cards?limit=3').json()
    assert not {c['card_id'] for c in fresh} & {c['card_id'] for c in cards}


def test_decision_outcome_isolated_even_for_same_person(api):
    from app import decision_feedback
    uid = person(api)
    a, b = new(api, uid), new(api, uid)
    pid = decision_feedback.offer(a, 'scope', {1: 'store-1'})
    body = {'proposal_id': pid, 'action': 'continue'}
    assert api.post(path(uid, b) + '/decision/feedback', json=body).status_code == 404
    assert api.post(path(uid, a) + '/decision/feedback', json=body).status_code == 200
    assert decision_feedback.declined(a, 'scope') == [['store-1']]
    assert decision_feedback.declined(b, 'scope') == []


def test_map_history_and_trace_match_each_independent_session(api):
    uid = person(api)
    a = new(api, uid)
    cards = api.get(path(uid, a) + '/cards?limit=3').json()
    for card in cards:
        react(api, uid, a, card['card_id'])
    b = new(api, uid)
    for sid, count in ((a, 3), (b, 0)):
        live = api.get(f'/admin/api/map/user/{sid}').json()
        prior = api.get(f'/admin/api/map/user/{sid}?upto=0').json()
        trace = api.get(f'/admin/api/map/user/{sid}/trace').json()
        assert live['swipes'] == count and live['session_id'] == sid
        assert prior['swipes'] == 0 and trace['upto'] == count
        assert api.get(path(uid, sid) + '/taste').status_code == 200
    entries = [r for r in api.get('/admin/api/users').json() if r['owner_user_id'] == uid]
    assert {r['session_id'] for r in entries} == {a, b}


def test_person_visit_and_photo_routes_share_across_sessions(api, monkeypatch, tmp_path):
    import io
    from PIL import Image
    from app import media
    monkeypatch.setattr(media, 'DIR', tmp_path / 'media')
    uid = person(api)
    sid = new(api, uid)
    cid = api.get(path(uid, sid) + '/cards?limit=1').json()[0]['card_id']
    base = f'/user/{uid}'
    assert api.post(base + '/directions', json={'card_id': cid, 'map_app': 'kakao', 'mode': 'walk'}).status_code == 200
    buckets = {r['policy']: r for r in policy.survey_stats()['by_policy']}
    assert buckets['none']['directions'] == 0
    assert buckets['legacy']['directions'] + buckets['taste']['directions'] == 1
    new(api, uid)
    assert api.get(base + '/surveys/pending').json()[0]['card_id'] == cid
    raw = io.BytesIO()
    Image.new('RGB', (24, 24)).save(raw, 'PNG')
    photo = api.post(base + f'/surveys/{cid}/photos', files={'file': ('test.png', raw.getvalue(), 'image/png')})
    assert photo.status_code == 201, photo.text
    photo_id = photo.json()['photo_id']
    assert api.delete(base + f'/surveys/{cid}/photos/{photo_id}').status_code == 204


@pytest.mark.parametrize('legacy', [False, True])
def test_visit_dropoff_joins_session_likes_to_one_person(api, legacy):
    if legacy:
        first = api.post('/users', json={'name': '기존 사용자'}).json()['user_id']
        uid = api.get(f'/session/{first}').json()['user_id']
    else:
        uid = person(api)
        first = new(api, uid)
    cid = api.get(path(uid, first) + '/cards?limit=1').json()[0]['card_id']
    react(api, uid, first, cid)
    second = new(api, uid)
    react(api, uid, second, cid)
    assert api.post(f'/user/{uid}/directions', json={
        'card_id': cid, 'map_app': 'kakao', 'mode': 'walk'}).status_code == 200
    dropoff = policy.survey_stats()['dropoff']
    assert dropoff['exposed'] == dropoff['liked'] == dropoff['directed'] == 1
    assert dropoff['liked_not_directed'] == 0
    assert dropoff['rates']['liked_of_exposed'] == 1
    assert dropoff['rates']['directed_of_liked'] == 1


def test_person_review_keeps_author_name_across_sessions_and_replay(api, monkeypatch):
    from app import user_reviews
    monkeypatch.setattr(user_reviews, '_by_store', {})
    uid = person(api, '실제 후기 작성자')
    first = new(api, uid)
    cid = api.get(path(uid, first) + '/cards?limit=1').json()[0]['card_id']
    body = {'card_id': cid, 'visited': True, 'satisfaction': 'good',
            # 세 줄 다 찬 후기 — 여기서 재는 건 이름이다(표현력은 순서에만 쓴다)
            'review': {'taste': '국물이 진하다', 'price': '2인 4만원', 'mood': '조용하다'}}
    reply = api.post(f'/user/{uid}/surveys', json=body)
    assert reply.status_code == 200 and reply.json()['recorded'] is True
    second = new(api, uid)
    detail = api.get(path(uid, second) + f'/cards/{cid}').json()
    assert detail['user_reviews'][0]['name'] == '실제 후기 작성자'
    assert uid not in json.dumps(detail['user_reviews'])
    assert api.post(f'/user/{uid}/surveys', json=body).json()['recorded'] is False
    user_reviews._by_store.clear()
    identities.restore()
    user_reviews.restore()
    assert api.get(path(uid, second) + f'/cards/{cid}').json()['user_reviews'] == detail['user_reviews']
