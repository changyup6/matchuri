"""Past exact-store rejection is a shared swipe priority, not permanent exclusion."""
import copy
import numpy as np
import pytest

from recsys import content, policy, swipe, taste
from test_policy import router
from test_user_sessions import api, person, new, path, react


def inherited(uid='memory'):
    session = policy.session_for(uid)
    session['taste']['memory'] = [
        {'store_id': 's0', 'like': False, 'weight': .1},
        {'store_id': 's2', 'like': True, 'weight': .9}]
    return session


@pytest.mark.parametrize('mode', ['legacy', 'taste', 'interleave'])
def test_previous_dislike_waits_while_new_options_exist(router, monkeypatch, mode):
    monkeypatch.setenv(policy.ENV, mode)
    session = inherited()
    tags = {}
    ids = policy.pick_many(session, 3, tags=tags, tiers={'s0': 1000}, bonus={'s0': 1e6})
    assert len(ids) == len(set(ids)) == 3 and 's0' not in ids
    assert 's0' not in session['taste']['shown'] | session['legacy']['shown']
    assert session['taste']['n'] == session['legacy']['n'] == 0
    assert tags and set(tags.values()) <= {'legacy', 'taste'}
    assert session['taste']['memory'][0]['like'] is False


@pytest.mark.parametrize('mode', ['legacy', 'taste', 'interleave'])
def test_narrow_filter_falls_back_without_widening_or_empty_cards(router, monkeypatch, mode):
    monkeypatch.setenv(policy.ENV, mode)
    session = inherited()
    tags = {}
    ids = policy.pick_many(session, 4, allow={'s0', 's1'}, tags=tags,
                           tiers={'s0': 1000}, bonus={'s0': 1e6})
    assert ids == ['s1', 's0']
    assert set(tags) == set(ids)
    assert policy.pick_many(session, 1, allow={'s0', 's1'}) == []


def test_taste_fallback_keeps_menu_diversity_from_the_preferred_cards(router, monkeypatch):
    monkeypatch.setenv(policy.ENV, 'taste')
    art = content.load()
    # These two stores have near-identical menus but distinct brands, so only
    # the within-batch menu rule can keep the fallback from immediately repeating.
    art.brand[1] = 'different-brand'
    assert art.sim_menu[0, 1] > taste.DUP_MENU_SIM
    session = policy.session_for('menu-diversity')
    session['taste']['memory'] = [
        {'store_id': sid, 'like': False, 'weight': 1.} for sid in ('s1', 's5')]
    tags = {}
    ids = policy.pick_many(session, 2, allow={'s0', 's1', 's5'}, tags=tags,
                           bonus={'s0': 1000, 's1': 100, 's5': 10})
    assert ids == ['s0', 's5']
    assert tags == {'s0': 'taste', 's5': 'taste'}
    assert session['taste']['shown'] == session['legacy']['shown'] == set(ids)
    assert session['taste']['served'] == 2


def test_declined_store_is_not_a_category_or_brand_ban(router):
    session = inherited()
    assert policy.pick_many(session, 1, allow={'s0', 's1'}, tiers={'s0': 1000}) == ['s1']


def test_current_label_overrides_inherited_rejection(router):
    session = inherited()
    assert policy._fresh_allow(session, {'s0', 's1'}) == {'s1'}
    policy.record(session, 's0', True)
    assert policy._fresh_allow(session, {'s0', 's1'}) == {'s0', 's1'}
    assert policy.pick_many(session, 2, allow={'s0', 's1'}) == ['s1']


def test_inheritance_rule_also_applies_during_artifact_fallback(router, monkeypatch):
    session = inherited()
    monkeypatch.setattr(content, 'load', lambda *a, **kw: None)
    tags = {}
    ids = policy.pick_many(session, 2, allow={'s0', 's1'}, tags=tags, tiers={'s0': 1000})
    assert ids == ['s1', 's0'] and set(tags.values()) == {'legacy'}


def test_no_history_or_positive_only_keeps_existing_pool(router):
    session = policy.session_for('fresh')
    allow = {'s0', 's1'}
    assert policy._fresh_allow(session, None) is None
    assert policy._fresh_allow(session, allow) is allow
    session['taste']['memory'] = [{'store_id': 's0', 'like': True, 'weight': 1.}]
    assert policy._fresh_allow(session, allow) is allow


@pytest.mark.parametrize('mode', ['legacy', 'taste', 'interleave'])
def test_http_cross_session_rejection_survives_replay(api, monkeypatch, mode):
    import main
    from app import identities, state, users, recommendation_sessions as sessions
    monkeypatch.setenv(policy.ENV, mode)
    uid = person(api)
    old = new(api, uid)
    first = api.get(path(uid, old) + '/cards?limit=1').json()[0]
    cid, sid = first['card_id'], first['store']['id']
    react(api, uid, old, cid, False)
    fresh = new(api, uid)
    baseline = copy.deepcopy(policy.session_for(fresh))
    memory = baseline['taste']['memory']
    assert any(row['store_id'] == sid and not row['like'] for row in memory)
    assert baseline['taste']['n'] == baseline['legacy']['n'] == 0
    assert not baseline['taste']['shown'] and not baseline['legacy']['shown']
    cards = api.get(path(uid, fresh) + '/cards?limit=3').json()
    assert len(cards) == 3 and cid not in [c['card_id'] for c in cards]
    for module in (policy, taste, swipe):
        module._sessions.clear()
    sessions._active.clear(); sessions._initial.clear(); sessions._user_memory.clear()
    identities.restore()
    state.restore(users.all_users(), [], main._apply, sessions.apply_boundary)
    restored = policy.session_for(fresh)
    assert restored['taste']['memory'] == memory
    np.testing.assert_array_equal(restored['legacy']['m'], baseline['legacy']['m'])
    assert sid in set(swipe.pool()['ids']) - policy._fresh_allow(restored, None)
    react(api, uid, old, cid, True)
    after = new(api, uid)
    assert policy._fresh_allow(policy.session_for(after), {sid}) == {sid}
