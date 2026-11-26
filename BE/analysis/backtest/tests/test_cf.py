# -*- coding: utf-8 -*-
"""협업 필터 — 공방문 코사인의 수식과 **누수 차단**.

두 종류를 검증한다.

  1. 손으로 답을 낼 수 있는 작은 공방문 사례 — `sim = |U_h ∩ U_i| / sqrt(|U_h|·|U_i|)`
     가 정확히 그 값인지.
  2. **평가 사용자의 리뷰가 행렬에 한 건도 들어가지 않는다.** 이게 무너지면 CF 는
     자기가 맞혀야 할 정답을 미리 본 셈이 된다 — 이 Step 의 결론이 통째로 무의미해진다.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from analysis.backtest import cf, scorers
from analysis.backtest.pool import build_pool
from analysis.backtest.scorers import ScoreContext
from analysis.backtest.scorers_cf import cf_score
from analysis.backtest.split import build_splits
from analysis.backtest.tests.conftest import linear_history, make_dataset

STORES = [(f"s{i}", 10, 4.0, "한식", [10000], 1) for i in range(4)]


def _ds(reviews):
    ds = make_dataset(STORES, reviews)
    return ds, build_pool(ds)


# ────────────────────────────────────────────── 손으로 낸 공방문
def test_손으로_만든_공방문_코사인():
    """저자 4명.

        a1: s0, s1     a2: s0, s1     a3: s1, s2     a4: s0 (한 곳뿐)

      |U_s0| = 3 (a1,a2,a4) · |U_s1| = 3 (a1,a2,a3) · |U_s2| = 1 (a3) · |U_s3| = 0
      sim(s0,s1) = 2/sqrt(3·3) = 2/3
      sim(s1,s2) = 1/sqrt(3·1)
      sim(s0,s2) = 0        s3 은 전부 0 (저자가 없다)
    """
    reviews = [("s0", "a1", 5, "2026-01-01"), ("s1", "a1", 5, "2026-01-02"),
               ("s0", "a2", 5, "2026-01-01"), ("s1", "a2", 3, "2026-01-02"),
               ("s1", "a3", 1, "2026-01-03"), ("s2", "a3", 5, "2026-01-04"),
               ("s0", "a4", 5, "2026-01-05")]
    ds, pool = _ds(reviews)
    m = cf.build(ds, pool, set())

    assert m.item_users.tolist() == [3.0, 3.0, 1.0, 0.0]
    assert m.sim[0, 1] == pytest.approx(2 / 3, abs=1e-6)
    assert m.sim[1, 2] == pytest.approx(1 / math.sqrt(3), abs=1e-6)
    assert m.sim[0, 2] == pytest.approx(0.0)
    assert m.sim[3].tolist() == [0.0, 0.0, 0.0, 0.0]     # 저자 없는 가게
    np.testing.assert_allclose(m.sim, m.sim.T, atol=1e-6)  # 대칭
    assert m.sim[0, 0] == pytest.approx(1.0)             # |U∩U|/|U| = 1
    assert m.n_authors == 4 and m.n_authors_multi == 3
    assert m.n_pairs_nonzero == 2 and m.n_pairs_total == 6


def test_같은_가게_재방문은_한_번으로_센다():
    """공방문은 '갔다/안 갔다' 의 문제다. 두 번 갔다고 두 명이 되지 않는다."""
    once = [("s0", "a1", 5, "2026-01-01"), ("s1", "a1", 5, "2026-01-02")]
    twice = once + [("s0", "a1", 4, "2026-02-01")]
    m1 = cf.build(*_ds(once), set())
    m2 = cf.build(*_ds(twice), set())
    np.testing.assert_allclose(m1.sim, m2.sim)


def test_한_곳만_간_저자는_분모만_키운다():
    """교집합에 기여할 수 없지만 |U_i| 는 늘린다 — 표준 코사인의 정의다."""
    base = [("s0", "a1", 5, "2026-01-01"), ("s1", "a1", 5, "2026-01-02")]
    m1 = cf.build(*_ds(base), set())
    m2 = cf.build(*_ds(base + [("s0", "solo", 5, "2026-01-03")]), set())
    assert m1.sim[0, 1] == pytest.approx(1.0)
    assert m2.sim[0, 1] == pytest.approx(1 / math.sqrt(2), abs=1e-6)
    assert m2.n_authors_multi == 1


# ────────────────────────────────────────────── 누수
def _leak_dataset():
    """평가 사용자 1명(리뷰 12건)과, 그 사람만 잇는 가게 쌍을 가진 데이터."""
    stores = [(f"s{i}", 10, 4.0, "한식", [10000], 1) for i in range(14)]
    reviews = linear_history("eval_user", [f"s{i}" for i in range(12)], [5] * 12)
    # 평가 사용자 **만** 이은 쌍: s12 - s13
    reviews += [("s12", "eval_user", 5, "2026-02-01 12:00:00"),
                ("s13", "eval_user", 5, "2026-02-02 12:00:00")]
    # 비평가 저자 몇 명
    reviews += [("s0", "other1", 5, "2026-01-01 12:00:00"),
                ("s1", "other1", 5, "2026-01-02 12:00:00"),
                ("s12", "other2", 5, "2026-01-03 12:00:00")]
    ds = make_dataset(stores, reviews)
    pool = build_pool(ds)
    return ds, pool, build_splits(ds, pool)


def test_평가_사용자의_리뷰는_한_건도_행렬에_들어가지_않는다():
    ds, pool, splits = _leak_dataset()
    evals = {s.user_id for s in splits}
    assert evals == {"eval_user"}

    sets = cf.user_store_sets(ds, pool, evals)
    assert "eval_user" not in sets                      # 저자 자체가 없다
    m = cf.build(ds, pool, evals)
    assert m.n_excluded_authors == 1 and m.n_excluded_reviews == 14

    # 평가 사용자만 이었던 쌍은 0 이어야 한다.
    i, j = pool.pos["s12"], pool.pos["s13"]
    assert m.sim[i, j] == pytest.approx(0.0)
    # s13 은 비평가 저자가 하나도 없어 |U|=0 -> 행 전체가 0
    assert m.item_users[pool.pos["s13"]] == 0.0


def test_제외한_행렬은_그_리뷰를_지운_행렬과_같다():
    """'뺐다' 가 정말 '없었던 것과 같다' 인지 — 반쪽 제외를 잡는 테스트."""
    ds, pool, splits = _leak_dataset()
    evals = {s.user_id for s in splits}
    excluded = cf.build(ds, pool, evals)

    ds_without = make_dataset(
        [(sid, 10, 4.0, "한식", [10000], 1) for sid in ds.store_order],
        [(r.store_id, r.author_id, r.rating, r.date) for r in ds.reviews
         if r.author_id not in evals])
    physical = cf.build(ds_without, build_pool(ds_without), set())
    np.testing.assert_allclose(excluded.sim, physical.sim)
    assert excluded.item_users.tolist() == physical.item_users.tolist()


def test_scorer는_행렬_재료가_없으면_이유를_말하며_거절한다():
    _, pool, _ = _leak_dataset()
    ctx = ScoreContext(pool=pool)
    with pytest.raises(KeyError, match="eval_users"):
        scorers.get("cf_item_max").score([("s0", True)], ctx, user_id="u")


# ────────────────────────────────────────────── 집계
def test_집계_세_종류():
    sim = np.array([[1.0, 0.4, 0.1], [0.4, 1.0, 0.6], [0.1, 0.6, 1.0]])
    pos = {"A": 0, "B": 1, "C": 2}
    hist = [("A", True), ("B", True)]
    np.testing.assert_allclose(cf_score(sim, pos, hist, "sum"), [1.4, 1.4, 0.7])
    np.testing.assert_allclose(cf_score(sim, pos, hist, "mean"), [0.7, 0.7, 0.35])
    np.testing.assert_allclose(cf_score(sim, pos, hist, "max"), [1.0, 1.0, 0.6])


def test_sum과_mean은_사용자_안에서_순위가_같다():
    """이력 길이는 그 사용자에게 상수다 — 나눠도 순서가 안 바뀐다."""
    rng = np.random.default_rng(0)
    sim = rng.random((6, 6))
    sim = (sim + sim.T) / 2
    pos = {f"s{i}": i for i in range(6)}
    hist = [("s0", True), ("s1", True), ("s2", True)]
    a = cf_score(sim, pos, hist, "sum")
    b = cf_score(sim, pos, hist, "mean")
    np.testing.assert_array_equal(np.argsort(-a), np.argsort(-b))


def test_이력이_비면_전부_0():
    sim = np.array([[1.0, 0.4], [0.4, 1.0]])
    np.testing.assert_allclose(cf_score(sim, {"A": 0, "B": 1}, [], "sum"), [0.0, 0.0])


def test_visit_해석이면_방문_전부가_신호다():
    """CF 는 '무엇을 소비했나' 만 본다 — visit 해석과 정확히 맞는다."""
    ds, pool, splits = _leak_dataset()
    ctx = ScoreContext(pool=pool, history_mode="visit",
                       extras={"dataset": ds, "eval_users": {s.user_id for s in splits}})
    s = scorers.get("cf_item_sum").score(splits[0].signals(5, "visit"), ctx, user_id="e")
    assert s.shape == (len(pool),)
