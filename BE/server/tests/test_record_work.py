"""Reduce repeated feature work without changing a single learned value."""
import copy

import numpy as np
import pytest

from recsys import content, taste


def assert_learning_equal(actual, expected):
    for key in ('m', 'v', 'prior_m', 'prior_v'):
        np.testing.assert_array_equal(actual[key], expected[key])
    for key in ('like_ids', 'dislike_ids', 'n', 'likes', 'hist', 'evidence_at', 'memory'):
        assert actual[key] == expected[key]
    assert len(actual['decision_checkpoints']) == len(expected['decision_checkpoints'])
    for a, b in zip(actual['decision_checkpoints'], expected['decision_checkpoints']):
        for key in a:
            if isinstance(a[key], np.ndarray):
                np.testing.assert_array_equal(a[key], b[key])
            else:
                assert a[key] == b[key]


@pytest.mark.parametrize('inherited', [False, True])
@pytest.mark.parametrize('recency', [False, True])
def test_pair_memory_reuse_preserves_every_learning_step(art, monkeypatch, inherited, recency):
    old = taste.replay([('s5', True), ('s4', False), ('s0', False), ('s3', True)])
    actual = taste.start_from(old) if inherited else taste._new_session()
    # Missing catalog references are ignored in both paths. Existing rows retain
    # their exact weights and order, including self-evidence removed by holdout.
    actual['memory'].append({'store_id': 'outside-catalog', 'like': True, 'weight': .4})
    expected = copy.deepcopy(actual)
    components = taste.components
    seq = [('s0', True), ('s1', False), ('s2', False), ('s3', False), ('s0', True),
           ('s0', False), ('s1', True), ('s5', False), ('s4', True), ('s5', False)] * 3
    for sid, label in seq:
        if not recency:
            actual['evidence_at'].clear()
            expected['evidence_at'].clear()
        # Reference path deliberately recomputes the memory features for every
        # pair, as before optimization, even when record supplies a reusable one.
        with monkeypatch.context() as patch:
            patch.setattr(taste, 'components', lambda state, a, hold=frozenset(), **kw:
                          components(state, a, hold))
            taste.record(expected, sid, label)
        taste.record(actual, sid, label)
        assert_learning_equal(actual, expected)
        np.testing.assert_array_equal(components(actual, art), components(expected, art))
    assert all(key not in actual for key in ('memory_prior', 'component_cache'))


def test_multiple_pairs_compute_unchanged_memory_only_once(art, monkeypatch):
    sess = taste._new_session()
    sess.update(like_ids=['s0'], dislike_ids=['s1', 's2', 's3'], n=8,
                evidence_at={'s0': 1, 's1': 3, 's2': 5, 's3': 8},
                memory=[{'store_id': 's4', 'like': True, 'weight': .7},
                        {'store_id': 's5', 'like': False, 'weight': .2}])
    original = art.component_scores
    calls = []
    def counted(like, dislike, exclude=None, **kw):
        if list(like) == [4] and list(dislike) == [5]:
            calls.append((list(exclude), kw['like_weights'].copy(), kw['dislike_weights'].copy()))
        return original(like, dislike, exclude, **kw)
    monkeypatch.setattr(art, 'component_scores', counted)
    taste.record(sess, 's0', True)
    assert sess['hist'][-1]['pairs'] == 3
    assert len(calls) == 1
    assert calls[0][0] == [1, 2, 3]
    np.testing.assert_array_equal(calls[0][1], [.7])
    np.testing.assert_array_equal(calls[0][2], [.2])
    taste.record(sess, 's0', False)
    # A new event must use its own memory/exclusion mask, never the old array.
    assert len(calls) == 2


def test_loaded_artifact_negative_average_is_exact_and_scoped(art, tmp_path):
    import json
    # A restaurant with all three negative-evidence modalities missing is
    # omitted, while a restaurant missing just one modality remains usable.
    for kind in ('menu', 'text', 'review'):
        art.has[kind][5] = False
        matrix = getattr(art, 'sim_' + kind)
        matrix[5, :] = matrix[:, 5] = np.nan
    (tmp_path / 'pool_ids.json').write_text(json.dumps(art.ids))
    (tmp_path / 'prior.json').write_text(json.dumps(art.prior))
    for name in ('sim_menu', 'sim_text', 'sim_review', 'sim_food', 'cooc'):
        np.save(tmp_path / (name + '.npy'), getattr(art, name))
    loaded = content._build(tmp_path)
    assert loaded is not None and loaded._negative_average is not None
    assert art._negative_average is None  # directly constructed artifacts stay mutable
    assert loaded._negative_average.dtype == np.float32
    assert not loaded._negative_average.flags.writeable
    for indices in ([], [5], [4, 5, 2, 4, 0], list(range(len(art.ids)))):
        idx = np.array(indices, dtype=int)
        np.testing.assert_array_equal(loaded._avg_sim(idx), art._avg_sim(idx))
    before = loaded._avg_sim(np.array([0])).copy()
    art.sim_menu[0, 1] = .01
    np.save(tmp_path / 'sim_menu.npy', art.sim_menu)
    refreshed = content._build(tmp_path)
    np.testing.assert_array_equal(refreshed._avg_sim(np.array([0])), art._avg_sim(np.array([0])))
    assert not np.array_equal(before, refreshed._avg_sim(np.array([0])), equal_nan=True)
    np.testing.assert_array_equal(loaded._avg_sim(np.array([0])), before)
