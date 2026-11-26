"""판단 수치의 성분 z 캐시 — 같은 학습 상태면 다시 계산하지 않고, 답은 캐시 없이 낸 것과 같다."""
import copy

import numpy as np

from recsys import decision, taste


def _feed(sess, answers):
    for sid, like in answers:
        taste.record(sess, sid, like)


def test_캐시한_성분이_새로_계산한_것과_같다(art, sess):
    _feed(sess, [("s0", True), ("s3", False), ("s1", True), ("s4", False)])
    for loo in (False, True):
        cached = decision._components(art, sess, loo=loo)
        again = decision._components(art, sess, loo=loo)
        fresh = decision._compute_components(art, sess, loo=loo)
        np.testing.assert_array_equal(cached, fresh)
        np.testing.assert_array_equal(again, fresh)


def test_같은_학습_상태면_성분을_다시_계산하지_않는다(art, sess, monkeypatch):
    _feed(sess, [("s0", True), ("s3", False), ("s1", True)])
    calls = []
    real = taste.components

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(taste, "components", counting)
    decision._components(art, sess, loo=True)
    first = len(calls)
    assert first > 1                                   # 답한 가게마다 한 번씩(LOO)
    decision._components(art, sess, loo=True)
    assert len(calls) == first                         # 두 번째는 캐시에서
    snap = copy.deepcopy(sess)                         # 세션 사본(판단 API 가 쓰는 스냅숏)도 같은 열쇠다
    decision._components(art, snap, loo=True)
    assert len(calls) == first


def test_학습_상태가_바뀌면_다시_계산한다(art, sess):
    _feed(sess, [("s0", True), ("s3", False)])
    before = decision._components(art, sess, loo=True)
    _feed(sess, [("s1", True)])
    after = decision._components(art, sess, loo=True)
    np.testing.assert_array_equal(after, decision._compute_components(art, sess, loo=True))
    assert not np.array_equal(before, after)


def test_돌려준_배열을_고쳐도_캐시는_그대로다(art, sess):
    _feed(sess, [("s0", True), ("s3", False)])
    z = decision._components(art, sess)
    z[:] = -123.0
    np.testing.assert_array_equal(decision._components(art, sess), decision._compute_components(art, sess))
