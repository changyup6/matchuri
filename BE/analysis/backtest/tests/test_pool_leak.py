# -*- coding: utf-8 -*-
"""후보 풀 규칙과 누수 차단 — PLAN 1절 '후보 풀' · '누수 규칙'."""
from __future__ import annotations

import numpy as np
import pytest

from analysis.backtest import config
from analysis.backtest.evaluate import evaluate
from analysis.backtest.pool import attach_crawled_quality, build_pool, price_bucket
from analysis.backtest.scorers import ScoreContext, Scorer
from analysis.backtest.split import build_splits
from analysis.backtest.tests.conftest import linear_history, make_dataset

STORES = [
    ("ok",        10, 4.0, "한식", [10000], 3),      # 리뷰 1+ · 사진 1+ -> 자격 있음
    ("no_review",  0, None, "한식", [10000], 3),     # 리뷰 0 -> 탈락
    ("no_photo",  10, 4.0, "한식", [10000], 0),      # 사진 0 -> 탈락
    ("edge",       1, 5.0, "카페", [], 1),           # 경계값 1/1 -> 자격 있음
]


def test_카드_자격은_리뷰_1이상_사진_1이상():
    pool = build_pool(make_dataset(STORES, []))
    assert pool.ids == ["ok", "edge"]              # stores.jsonl 등장 순서 그대로
    assert pool.pos == {"ok": 0, "edge": 1}


def test_베이지안_평균_식이_서버와_같다():
    pool = build_pool(make_dataset(STORES, []))
    cnt, avg = pool.review_count, pool.review_avg
    mu = float((np.nan_to_num(avg, nan=0.0) * cnt).sum() / cnt.sum())
    expect = (config.BAYES_C * mu + avg * cnt) / (config.BAYES_C + cnt)
    assert pool.bayes == pytest.approx(expect)
    assert pool.qz.mean() == pytest.approx(0.0, abs=1e-12)


def test_평점이_없으면_전체평균으로_수축한다():
    stores = [("a", 5, None, "한식", [], 1), ("b", 5, 4.0, "한식", [], 1)]
    pool = build_pool(make_dataset(stores, []))
    assert pool.bayes[0] == pytest.approx(pool.bayes[0])   # nan 이 아니다
    assert not np.isnan(pool.bayes).any()


@pytest.mark.parametrize("prices,bucket", [
    ([], 4), ([7999], 0), ([8000], 1), ([12999], 1), ([13000], 2),
    ([20999], 2), ([21000], 3), ([1000, 30000], 2),        # 중앙가 15,500
])
def test_가격대는_메뉴_중앙가의_4분위(prices, bucket):
    assert price_bucket(prices) == bucket


# ────────────────────────────────────────────── 누수
# 가게 20곳. 평가 사용자는 그중 12곳만 가므로 후보에 '가지 않은 가게' 가 남는다.
LEAK_STORES = [(f"s{i}", 10, 4.0, "한식", [10000], 1) for i in range(20)]
EVAL_VISITS = [f"s{i}" for i in range(12, 20)] + ["s0", "s1", "s2", "s3"]
# T=12 -> F=max(3, ceil(3.6))=4. 과거 = s12..s19, 미래 = s0..s3.


def _leak_dataset():
    """평가 사용자 1명(u_eval, 별점 전부 1) + 리뷰 9건짜리 비평가 사용자 3명(별점 5)."""
    reviews = linear_history("u_eval", EVAL_VISITS, [1] * 12)
    for j in range(3):                       # T=9 < 10 이라 평가 사용자가 아니다
        reviews += linear_history(f"other{j}", [f"s{i}" for i in range(9)], [5] * 9)
    return make_dataset(LEAK_STORES, reviews)


def _eval_split(ds, pool):
    (sp,) = [s for s in build_splits(ds, pool) if s.user_id == "u_eval"]
    return sp


def test_평가_사용자만_골라낸다():
    ds = _leak_dataset()
    assert {s.user_id for s in build_splits(ds, build_pool(ds))} == {"u_eval"}


def test_크롤링_품질은_평가_사용자_리뷰를_전부_뺀다():
    ds = _leak_dataset()
    pool = build_pool(ds)
    attach_crawled_quality(pool, ds, {"u_eval"})
    # s0..s2 는 u_eval(1점)과 other 3명(5점)이 함께 남긴 곳 -> 1점이 빠져야 한다.
    for sid in ("s0", "s1", "s2"):
        i = pool.pos[sid]
        assert pool.crawled_count[i] == 3 and pool.crawled_avg[i] == pytest.approx(5.0)
    # u_eval 만 남긴 곳은 아예 리뷰가 없어진다.
    for sid in ("s12", "s19"):
        assert pool.crawled_count[pool.pos[sid]] == 0
    # 진단용 변종(미제외)은 1점이 섞여 있어 어디서나 더 낮다 = 그만큼이 누수 크기다.
    assert (pool.quality_variants["crawled_all"] < pool.crawled_bayes).all()


def test_제외하면_리뷰가_없는_가게는_전체평균으로_간다():
    ds = make_dataset(LEAK_STORES, linear_history("u_eval", EVAL_VISITS, [5] * 12))
    pool = build_pool(ds)
    attach_crawled_quality(pool, ds, {"u_eval"})
    assert (pool.crawled_count == 0).all()
    assert np.allclose(pool.crawled_bayes, pool.crawled_bayes[0])   # 전부 mu


class _Spy(Scorer):
    """scorer 가 무엇을 받았는지 기록만 한다. 점수는 상수."""
    name = "spy"

    def __init__(self):
        self.seen: list[tuple[str, list]] = []

    def score(self, history, ctx, *, user_id):
        self.seen.append((user_id, list(history)))
        return np.zeros(len(ctx.pool))


class _PreferSeen(Scorer):
    """과거에 간 가게를 최상위로 올리려는 scorer.

    하네스가 그 가게들을 후보에서 빼지 않으면 정답이 전부 밀려 지표가 무너진다.
    """
    name = "prefer-seen"

    def score(self, history, ctx, *, user_id):
        n = len(ctx.pool)
        s = np.array([1.0 if int(sid[1:]) >= 12 else 0.0 for sid in ctx.pool.ids])
        return s + (n - np.arange(n)) * 1e-6      # 나머지는 인덱스 오름차순


def test_scorer는_미래를_절대_보지_못한다():
    ds = _leak_dataset()
    pool = build_pool(ds)
    sp = _eval_split(ds, pool)
    spy = _Spy()
    evaluate(spy, [sp], ScoreContext(pool=pool), n_history=5)
    (_, history), = spy.seen
    future = {sid for sid, _ in sp.future}
    assert future == {"s0", "s1", "s2", "s3"}
    assert not ({sid for sid, _ in history} & future)
    assert len(history) <= 5
    assert [sid for sid, _ in history] == ["s15", "s16", "s17", "s18", "s19"]


def test_이미_간_가게는_후보에서_빠진다():
    ds = _leak_dataset()
    pool = build_pool(ds)
    sp = _eval_split(ds, pool)
    res = evaluate(_PreferSeen(), [sp], ScoreContext(pool=pool), n_history=12)
    # 과거 8곳이 빠지면 후보 12곳 중 정답 s0..s3 이 1~4위 -> 완벽한 랭킹.
    # 빠지지 않았다면 s12..s19 가 1~8위를 차지해 AUC 는 0.5, NDCG 는 1 미만이 된다.
    assert res.p["auc"][0] == pytest.approx(1.0)
    assert res.p[f"ndcg@{config.NDCG_K}"][0] == pytest.approx(1.0)
    assert res.p[f"hit@{config.HIT_K}"][0] == 1.0
    assert res.p[f"recall@{config.RECALL_K}"][0] == pytest.approx(1.0)


def test_상수_점수면_AUC는_0_5():
    ds = _leak_dataset()
    pool = build_pool(ds)
    res = evaluate(_Spy(), [_eval_split(ds, pool)], ScoreContext(pool=pool), n_history=5)
    assert res.p["auc"][0] == pytest.approx(0.5)


def test_과제_R은_양쪽_클래스가_있을_때만_센다():
    ds = _leak_dataset()                     # u_eval 은 미래 별점이 전부 1 -> 한쪽뿐
    pool = build_pool(ds)
    res = evaluate(_Spy(), [_eval_split(ds, pool)], ScoreContext(pool=pool), n_history=5)
    assert np.isnan(res.r_auc[0])

    ds2 = make_dataset(LEAK_STORES,
                       linear_history("u_eval", EVAL_VISITS, [1] * 8 + [5, 5, 1, 1]))
    pool2 = build_pool(ds2)
    res2 = evaluate(_Spy(), [_eval_split(ds2, pool2)], ScoreContext(pool=pool2),
                    n_history=5)
    assert res2.r_auc[0] == pytest.approx(0.5)   # 상수 점수 -> 동점 -> 0.5
