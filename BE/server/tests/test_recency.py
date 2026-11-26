"""Recency retains vector evidence and respects current-session validation."""
import copy
import numpy as np
from recsys import content, decision, taste


def test_recent_evidence_wins_between_distinct_clusters(art):
    a = taste._new_session()
    a.update(like_ids=['s0', 's2'], n=30, evidence_at={'s0': 1, 's2': 30})
    b = copy.deepcopy(a)
    b['evidence_at'] = {'s0': 30, 's2': 1}
    za, zb = taste.components(a, art), taste.components(b, art)
    menu = content.IDX['menu']
    assert za[menu, 3] > za[menu, 1]
    assert zb[menu, 1] > zb[menu, 3]


def test_old_unopposed_signal_fades_after_normalization(art):
    a = taste._new_session()
    a.update(like_ids=['s0'], n=1, evidence_at={'s0': 1})
    b = copy.deepcopy(a)
    b['n'] += 20
    za, zb = taste.components(a, art), taste.components(b, art)
    personal = [content.IDX[k] for k in ('cf', 'menu', 'text', 'review', 'food')]
    np.testing.assert_allclose(zb[personal], za[personal] * 0.25)
    np.testing.assert_array_equal(zb[:2], za[:2])


def test_memory_is_weak_without_inherited_certainty(art):
    old = taste.replay([('s0', True), ('s2', False), ('s1', True)])
    new = taste.start_from(old)
    assert new['n'] == 0 and not new['like_ids'] and not new['shown']
    assert not new['hist'] and not new['decision_checkpoints']
    np.testing.assert_array_equal(new['v'], taste._new_session()['v'])
    personal = [content.IDX[k] for k in ('cf', 'menu', 'text', 'review', 'food', 'neg')]
    z = taste.components(new, art)
    assert np.max(np.abs(z[personal])) <= content.Z_CLIP * taste.MEMORY_SHARE + 1e-10
    assert np.any(z[personal]) and not decision.evaluate(new)['ready']
    empty_rotation = taste.start_from(new)
    np.testing.assert_array_equal(empty_rotation['m'], new['m'])
    assert empty_rotation['memory'] == new['memory']


def test_current_reassessment_overrides_old_label(art):
    new = taste.start_from(taste.replay([('s0', True)]))
    taste.record(new, 's0', False)
    without = copy.deepcopy(new)
    without['memory'] = []
    np.testing.assert_array_equal(taste.components(new, art), taste.components(without, art))
    assert not new['like_ids'] and new['dislike_ids'] == ['s0']
    assert not taste.has_positive(new)


def test_long_trained_model_adapts_and_replay_is_exact(art):
    seq = [('s0', True), ('s2', False)] * 40 + [('s2', True), ('s0', False)] * 5
    a, b = taste.replay(seq), taste.replay(seq)
    np.testing.assert_array_equal(a['m'], b['m'])
    np.testing.assert_array_equal(a['v'], b['v'])
    assert np.isfinite(a['m']).all() and min(a['v']) > 0
    assert np.linalg.norm(a['m'] - taste.replay(seq[:80])['m']) > 0.01


def test_memory_holdout_removes_self_similarity(art):
    new = taste.start_from(taste.replay([('s0', True)]))
    np.testing.assert_array_equal(taste.components(new, art, {'s0'})[2:], 0)


def test_session_memory_does_not_drop_older_distinct_stores(art):
    old = taste._new_session()
    ids = [f's{i}' for i in range(80)]
    old.update(n=80, like_ids=ids, evidence_at={s: i+1 for i,s in enumerate(ids)})
    new = taste.start_from(old)
    assert {r['store_id'] for r in new['memory']} == set(ids)
    weights = {r['store_id']: r['weight'] for r in new['memory']}
    assert 0 < weights['s0'] < weights['s40'] < weights['s79'] == 1
