# -*- coding: utf-8 -*-
"""`current` 베이스라인이 **서버와 정확히 같은 모델**임을 증명한다.

PLAN 1절: "현재 서버 모델을 오프라인으로 재현 … 서버 함수와 갱신 수식이 같음을
테스트로 증명한다."

여기서만 `server/recsys/swipe.py` 를 실제로 import 한다(fastapi 필요). 하네스 본체는
서버를 import 하지 않는다 — 실험이 웹 프레임워크와 전역 세션에 묶이지 않게 하려고
재구현했고, 그 대가로 이 테스트가 있다.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest

from analysis.backtest import config, pool as pool_mod
from analysis.backtest.current_model import CurrentModel, quality_weight
from analysis.backtest.scorers import ScoreContext, get

if str(config.SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(config.SERVER_DIR))

swipe = pytest.importorskip("recsys.swipe",
                            reason="서버 모듈 import 실패 — fastapi 가 필요하다: "
                                   "~/venvs/recsys/bin/pip install fastapi pydantic")


@pytest.fixture(scope="module")
def server_pool():
    return swipe._build_pool()


def test_상수가_서버와_같다():
    assert pool_mod.CATS == swipe.CATS
    assert pool_mod.PRICE_EDGES == swipe.PRICE_EDGES
    assert pool_mod.PRICE_NAMES == swipe.PRICE_NAMES
    assert config.BAYES_C == swipe.BAYES_C


def test_후보_풀이_서버와_같다(real_pool, server_pool):
    _, pool = real_pool
    assert pool.ids == server_pool["ids"]        # 집합만이 아니라 순서까지
    assert pool.pos == server_pool["pos"]


def test_특성과_품질z가_서버와_같다(real_pool, server_pool):
    _, pool = real_pool
    assert pool.X.shape == server_pool["X"].shape == (len(pool), pool_mod.FEATURE_DIM)
    np.testing.assert_allclose(pool.qz, server_pool["qz"], rtol=0, atol=1e-12)
    np.testing.assert_allclose(pool.X, server_pool["X"], rtol=0, atol=1e-12)


def test_featurize_함수_자체와도_같다(real_pool, server_pool):
    """서버 `_featurize` 를 가게 200곳에 직접 돌려 우리 X 행과 맞춘다."""
    ds, pool = real_pool
    for i in range(0, len(pool), max(1, len(pool) // 200)):
        sid = pool.ids[i]
        x = swipe._featurize(sid, ds.stores[sid], float(pool.qz[i]))
        np.testing.assert_allclose(pool.X[i], x, rtol=0, atol=1e-12)


def _synthetic_history(pool, k=24):
    """like/dislike 가 섞인 결정적인 스와이프 열. 업종·가격대가 골고루 섞이게 고른다."""
    step = max(1, len(pool) // k)
    ids = [pool.ids[i * step] for i in range(k)]
    # T T F T F F T T F … 규칙적이되 한쪽으로 몰리지 않게
    likes = [(i % 3) != 2 if i % 2 == 0 else (i % 5) < 2 for i in range(k)]
    return list(zip(ids, likes))


def test_갱신_수식이_서버_record와_한_스텝씩_같다(real_pool, server_pool):
    _, pool = real_pool
    hist = _synthetic_history(pool)

    sess = {"m": np.zeros(pool.X.shape[1]), "v": np.full(pool.X.shape[1], 1.0),
            "n": 0, "likes": 0, "cat_stats": {}, "hist": []}
    mine = CurrentModel(pool)

    # 초기 상태부터 같아야 한다
    np.testing.assert_allclose(mine.m, sess["m"])
    np.testing.assert_allclose(mine.v, sess["v"])

    for store_id, like in hist:
        swipe.record(sess, store_id, like)
        mine.record(store_id, like)
        np.testing.assert_allclose(mine.m, sess["m"], rtol=0, atol=0)   # 비트까지 동일
        np.testing.assert_allclose(mine.v, sess["v"], rtol=0, atol=0)
        assert mine.n == sess["n"]


def test_사후평균_점수가_서버_peek와_같다(real_pool, server_pool):
    """`swipe.peek` 은 Thompson 난수 없이 사후 평균으로 점수를 낸다 — 우리와 같은 식."""
    _, pool = real_pool
    hist = _synthetic_history(pool)

    sess = swipe._new_session()
    for store_id, like in hist:
        swipe.record(sess, store_id, like)
        sess["shown"].add(store_id)          # main._apply 가 하는 일

    mine = CurrentModel(pool).replay(hist)
    scores = mine.scores()

    peeked = swipe.peek(sess, 50)
    assert len(peeked) == 50
    for row in peeked:
        assert row["total"] == pytest.approx(round(float(scores[pool.pos[row["store_id"]]]), 3))

    # 순위도 같아야 한다: peek 이 고른 50곳 = 우리 점수 상위 50곳(이미 본 카드 제외).
    # 점수가 정확히 같은 가게가 있어(업종·가격대·품질이 모두 같은 경우) 동점 순서는
    # argsort 구현에 달렸으므로 **점수 수열**로 비교한다 — 순위 자체는 동일하다.
    shown = {sid for sid, _ in hist}
    masked = np.where([sid not in shown for sid in pool.ids], scores, -np.inf)
    top = np.sort(masked)[::-1][:50]
    np.testing.assert_allclose([scores[pool.pos[r["store_id"]]] for r in peeked], top,
                               rtol=0, atol=0)
    assert all(sid not in shown for sid in (r["store_id"] for r in peeked))


def test_품질_가중치가_서버와_같다():
    for n in range(0, 60):
        assert quality_weight(n) == pytest.approx(1 / (1 + n / 15))


def test_scorer로_불러도_같은_점수(real_pool):
    _, pool = real_pool
    hist = _synthetic_history(pool)
    got = get("current").score(hist, ScoreContext(pool=pool), user_id="u")
    np.testing.assert_allclose(got, CurrentModel(pool).replay(hist).scores())


def test_풀_밖_가게는_학습에서_빠진다(real_pool):
    _, pool = real_pool
    a = CurrentModel(pool).replay([(pool.ids[0], True)])
    b = CurrentModel(pool).replay([("없는가게id", True), (pool.ids[0], True)])
    np.testing.assert_allclose(a.m, b.m)
    assert a.n == b.n == 1


def test_이력이_없으면_품질_정렬과_같다(real_pool):
    """n=0 이면 w_q=1 이라 점수 = 품질z. 콜드스타트가 서버와 같은 동작이라는 뜻."""
    _, pool = real_pool
    np.testing.assert_allclose(CurrentModel(pool).scores(), pool.qz)
