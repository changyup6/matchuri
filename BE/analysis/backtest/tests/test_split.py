# -*- coding: utf-8 -*-
"""시간 분할 규칙 — PLAN 1절 '평가 사용자와 시간 분할'."""
from __future__ import annotations

import math

import pytest

from analysis.backtest import config
from analysis.backtest.pool import build_pool
from analysis.backtest.split import build_splits, to_signal
from analysis.backtest.tests.conftest import linear_history, make_dataset

# 가게 12곳: 전부 카드 자격(리뷰 1+, 사진 1+)을 갖춘다.
STORES = [(f"s{i}", 10, 4.0, "한식", [10000], 1) for i in range(12)]


def _ds(reviews):
    return make_dataset(STORES, reviews)


def _split_one(store_ids, ratings, user="u1"):
    ds = _ds(linear_history(user, store_ids, ratings))
    return build_splits(ds, build_pool(ds))


def test_평가_사용자는_T가_10_이상():
    ids = [f"s{i}" for i in range(9)]
    assert _split_one(ids, [5] * 9) == []
    ids10 = [f"s{i}" for i in range(10)]
    assert len(_split_one(ids10, [5] * 10)) == 1


def test_풀_밖_리뷰는_T에_들어가지_않는다():
    # 사진 0장이라 카드 자격이 없는 가게. T 가 10 을 못 채워 평가 사용자가 아니다.
    stores = STORES + [("no_photo", 10, 4.0, "한식", [10000], 0)]
    reviews = linear_history("u1", [f"s{i}" for i in range(9)] + ["no_photo"], [5] * 10)
    ds = make_dataset(stores, reviews)
    assert build_splits(ds, build_pool(ds)) == []


@pytest.mark.parametrize("total,expected_f", [(10, 3), (12, 4), (20, 6), (11, 4), (30, 9)])
def test_미래_구간_F는_max3_ceil_0_3T(total, expected_f):
    stores = [(f"s{i}", 10, 4.0, "한식", [10000], 1) for i in range(total)]
    ds = make_dataset(stores, linear_history("u1", [f"s{i}" for i in range(total)],
                                             [5] * total))
    (sp,) = build_splits(ds, build_pool(ds))
    assert expected_f == max(config.FUTURE_MIN,
                             math.ceil(config.FUTURE_RATIO * total))
    assert len(sp.future) == expected_f
    assert len(sp.past) == total - expected_f


def test_미래는_N과_무관하게_고정():
    ids = [f"s{i}" for i in range(12)]
    (sp,) = _split_one(ids, [5] * 12)
    # 미래 4곳(=ceil(0.3*12)) 은 가장 최근. 어떤 N 을 물어도 같다.
    assert [sid for sid, _ in sp.future] == ["s8", "s9", "s10", "s11"]
    for n in (3, 5, 10, 20):
        assert [sid for sid, _ in sp.future] == ["s8", "s9", "s10", "s11"]
        assert set(sp.history(n)).isdisjoint(sp.future)


def test_이력은_미래_직전의_최근_N건이고_중첩된다():
    ids = [f"s{i}" for i in range(12)]
    (sp,) = _split_one(ids, [5] * 12)
    assert [sid for sid, _ in sp.history(3)] == ["s5", "s6", "s7"]
    assert [sid for sid, _ in sp.history(5)] == ["s3", "s4", "s5", "s6", "s7"]
    # 중첩: 작은 N 은 큰 N 의 꼬리다 (가장 오래된 N 개가 아니다 — 이전 실수)
    assert sp.history(3) == sp.history(5)[-3:] == sp.history(8)[-3:]


def test_N이_이력보다_길면_있는_만큼만():
    ids = [f"s{i}" for i in range(10)]
    (sp,) = _split_one(ids, [5] * 10)           # T=10, F=3 -> 과거 7건
    assert len(sp.history(20)) == 7
    assert sp.has_full_history(5) and not sp.has_full_history(10)


def test_미래는_가게_단위로_유일화되고_가장_이른_방문을_남긴다():
    # s9 를 미래에서 두 번 방문: 뒤엣것이 합쳐지고 별점은 먼저 것(2)이 남는다.
    ids = [f"s{i}" for i in range(9)] + ["s9", "s9", "s10"]
    ratings = [5] * 9 + [2, 5, 5]
    (sp,) = _split_one(ids, ratings)            # T=12, F=4 -> 미래 = s8,s9,s9,s10
    assert [sid for sid, _ in sp.future] == ["s8", "s9", "s10"]
    assert dict(sp.future)["s9"] == 2
    assert sp.n_future_dup == 1


def test_과거에_간_가게는_미래에서_빠진다():
    # s0 을 과거에도 미래에도 방문 -> 제품은 한 번 준 카드를 다시 주지 않는다.
    ids = [f"s{i}" for i in range(9)] + ["s9", "s10", "s0"]
    (sp,) = _split_one(ids, [5] * 12)
    assert "s0" in sp.seen_stores
    assert "s0" not in [sid for sid, _ in sp.future]
    assert sp.n_future_seen == 1


def test_이력의_중복은_그대로_둔다():
    ids = ["s0", "s0"] + [f"s{i}" for i in range(1, 10)]
    (sp,) = _split_one(ids, [5] * 11)           # T=11, F=4 -> 과거 7건
    assert [sid for sid, _ in sp.past][:2] == ["s0", "s0"]


@pytest.mark.parametrize("rating,expected", [(5, True), (4, True), (3, None),
                                             (2, False), (1, False)])
def test_별점을_like_dislike로_매핑(rating, expected):
    assert to_signal(rating) is expected


def test_중립_별점은_모델_입력에서_빠진다():
    ids = [f"s{i}" for i in range(12)]
    ratings = [5, 5, 5, 5, 3, 1, 4, 3, 5, 5, 5, 5]
    (sp,) = _split_one(ids, ratings)            # 과거 8건 = s0..s7
    assert [sid for sid, _ in sp.history(8)] == [f"s{i}" for i in range(8)]
    assert sp.signals(8) == [("s0", True), ("s1", True), ("s2", True), ("s3", True),
                             ("s5", False), ("s6", True)]


def test_과제_R_라벨은_별점_4_이상이_양성():
    ids = [f"s{i}" for i in range(12)]
    ratings = [5] * 8 + [5, 4, 3, 1]            # 미래 = s8(5), s9(4), s10(3), s11(1)
    (sp,) = _split_one(ids, ratings)
    assert sp.satisfaction_labels() == [("s8", 1), ("s9", 1), ("s10", 0), ("s11", 0)]


def test_정렬은_날짜순이고_동일_시각은_review_id로_고정():
    ids = [f"s{i}" for i in range(10)]
    reviews = [(sid, "u1", 5, "2026-01-01 00:00:00") for sid in ids]   # 전부 같은 시각
    ds = make_dataset(STORES, reviews)
    (sp,) = build_splits(ds, build_pool(ds))
    assert [sid for sid, _ in sp.past] + [sid for sid, _ in sp.future] == ids
