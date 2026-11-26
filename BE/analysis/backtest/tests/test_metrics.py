# -*- coding: utf-8 -*-
"""지표 — 손으로 답을 낼 수 있는 작은 경우로 고정한다."""
from __future__ import annotations

import math

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from analysis.backtest.metrics import (auc, hit_at_k, ndcg_at_k,
                                       popularity_deciles,
                                       popularity_matched_auc, rank_order,
                                       ranking_metrics, recall_at_k)


def _order(scores):
    return rank_order(np.asarray(scores, dtype=float), np.arange(len(scores)))


def test_AUC_완전분리와_완전역전():
    s = np.array([0.9, 0.8, 0.2, 0.1])
    assert auc(s, np.array([1, 1, 0, 0])) == 1.0
    assert auc(s, np.array([0, 0, 1, 1])) == 0.0


def test_AUC_동점은_0_5로_센다():
    # 양성 1개, 음성 1개가 같은 점수 -> 정확히 0.5
    assert auc(np.array([1.0, 1.0]), np.array([1, 0])) == 0.5
    # 양성 1 · 음성 2 중 하나만 동점: (1 이김 + 0.5 동점) / 2 = 0.75
    assert auc(np.array([2.0, 2.0, 1.0]), np.array([1, 0, 0])) == 0.75


def test_AUC_한쪽_클래스뿐이면_nan():
    assert math.isnan(auc(np.array([1.0, 2.0]), np.array([1, 1])))
    assert math.isnan(auc(np.array([1.0, 2.0]), np.array([0, 0])))


def test_AUC가_sklearn과_같다():
    rng = np.random.default_rng(0)
    for _ in range(20):
        s = rng.integers(0, 5, size=200).astype(float)   # 동점이 많이 생기게
        y = (rng.random(200) < 0.1).astype(int)
        if 0 < y.sum() < y.size:
            assert auc(s, y) == pytest.approx(roc_auc_score(y, s))


def test_Hit는_상위K_안에_정답이_하나라도_있으면_1():
    labels = np.array([0, 0, 1, 0, 0], dtype=bool)
    scores = np.array([5.0, 4.0, 3.0, 2.0, 1.0])         # 정답은 3위
    assert hit_at_k(_order(scores), labels, 3) == 1.0
    assert hit_at_k(_order(scores), labels, 2) == 0.0


def test_Recall은_상위K에_든_정답_비율():
    labels = np.array([1, 0, 1, 0, 1], dtype=bool)
    scores = np.array([5.0, 4.0, 3.0, 2.0, 1.0])         # 정답 3개 중 상위3에 2개
    assert recall_at_k(_order(scores), labels, 3) == pytest.approx(2 / 3)
    assert recall_at_k(_order(scores), labels, 5) == 1.0


def test_NDCG_이상순위면_1_손으로_계산한_값과_일치():
    labels = np.array([1, 1, 0, 0], dtype=bool)
    assert ndcg_at_k(_order([4.0, 3.0, 2.0, 1.0]), labels, 4) == pytest.approx(1.0)
    # 정답이 2·3위 -> DCG = 1/log2(3) + 1/log2(4), IDCG = 1/log2(2) + 1/log2(3)
    labels2 = np.array([0, 1, 1, 0], dtype=bool)
    dcg = 1 / math.log2(3) + 1 / math.log2(4)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at_k(_order([4.0, 3.0, 2.0, 1.0]), labels2, 4) == pytest.approx(dcg / idcg)


def test_NDCG의_이상순위는_K에서_잘린다():
    # 정답 5개, K=2 -> IDCG 는 2개까지만 채운 값
    labels = np.array([1, 1, 1, 1, 1, 0], dtype=bool)
    scores = np.array([6.0, 5.0, 4.0, 3.0, 2.0, 1.0])
    assert ndcg_at_k(_order(scores), labels, 2) == pytest.approx(1.0)


def test_정답이_없으면_Recall과_NDCG는_nan():
    labels = np.zeros(4, dtype=bool)
    o = _order([4.0, 3.0, 2.0, 1.0])
    assert math.isnan(recall_at_k(o, labels, 2))
    assert math.isnan(ndcg_at_k(o, labels, 2))


def test_동점은_인덱스가_아니라_주어진_순열로_깬다():
    """상수 점수 모델이 '앞쪽 가게' 덕에 점수를 얻으면 안 된다."""
    scores = np.zeros(6)
    labels = np.array([1, 0, 0, 0, 0, 0], dtype=bool)
    assert hit_at_k(rank_order(scores, np.arange(6)), labels, 1) == 1.0        # 인덱스순
    assert hit_at_k(rank_order(scores, np.array([5, 0, 1, 2, 3, 4])),
                    labels, 1) == 0.0                                          # 순열


def test_지표_묶음의_키와_값():
    scores = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    labels = np.array([1, 0, 0, 0, 1], dtype=bool)
    m = ranking_metrics(scores, labels, np.arange(5), hit_k=1, recall_k=2, ndcg_k=5)
    assert set(m) == {"auc", "hit@1", "recall@2", "ndcg@5"}
    assert m["hit@1"] == 1.0
    assert m["recall@2"] == 0.5
    assert m["auc"] == pytest.approx(roc_auc_score(labels, scores))


# ────────────────────────────────────────────── 인기 매칭 AUC (auc_pm)
def test_십분위는_동점_경계를_접는다():
    """리뷰 1건짜리가 수백 곳이면 분위 경계가 겹친다 — 같은 값이 갈라지면 안 된다."""
    v = np.array([1.0] * 8 + [2.0, 3.0])
    d = popularity_deciles(v)
    assert len(set(d[:8])) == 1                    # 같은 값은 같은 구간
    assert d[8] != d[0] and d[9] != d[8]


def test_십분위는_균등한_값을_10등분한다():
    d = popularity_deciles(np.arange(100.0))
    assert d.min() == 0 and d.max() == 9
    assert np.bincount(d).tolist() == [10] * 10


def test_auc_pm은_같은_십분위_안에서만_비교한다():
    """양성보다 점수가 높은 음성이 있어도 **다른 십분위**면 세지 않는다."""
    scores = np.array([1.0, 0.0, 9.0])
    labels = np.array([1, 0, 0], dtype=bool)
    decile = np.array([0, 0, 1])                   # 9.0 은 다른 구간
    assert popularity_matched_auc(scores, labels, decile) == 1.0
    # 전수 AUC 는 9.0 을 세므로 0.5
    assert auc(scores, labels) == 0.5


def test_auc_pm_동점은_0_5로_센다():
    scores = np.array([1.0, 1.0, 0.0])
    labels = np.array([1, 0, 0], dtype=bool)
    decile = np.zeros(3, dtype=int)
    # 음성 2개 중 하나는 동점(0.5), 하나는 이김(1) -> 0.75
    assert popularity_matched_auc(scores, labels, decile) == 0.75


def test_auc_pm은_양성마다_같은_무게를_준다():
    """구간마다 음성 수가 달라도 양성 하나가 한 표다."""
    scores = np.array([1.0, 0.0, 0.0, 0.0,      # 구간0: 양성 1, 음성 3 -> 1.0
                       0.0, 1.0])               # 구간1: 양성 1, 음성 1 -> 0.0
    labels = np.array([1, 0, 0, 0, 1, 0], dtype=bool)
    decile = np.array([0, 0, 0, 0, 1, 1])
    assert popularity_matched_auc(scores, labels, decile) == pytest.approx(0.5)


def test_auc_pm은_상대가_없는_양성을_건너뛴다():
    scores = np.array([1.0, 0.5, 0.0])
    labels = np.array([1, 1, 0], dtype=bool)
    decile = np.array([1, 0, 0])                   # 구간1 에는 음성이 없다
    # 구간0 의 양성(0.5)만 센다: 음성 0.0 을 이기므로 1.0
    assert popularity_matched_auc(scores, labels, decile) == 1.0


def test_auc_pm은_한쪽_클래스뿐이면_nan():
    d = np.zeros(3, dtype=int)
    assert math.isnan(popularity_matched_auc(np.array([1.0, 2.0, 3.0]),
                                             np.ones(3, dtype=bool), d))
    assert math.isnan(popularity_matched_auc(np.array([1.0, 2.0, 3.0]),
                                             np.zeros(3, dtype=bool), d))


def test_auc_pm은_모든_음성이_다른_구간이면_nan():
    scores = np.array([1.0, 0.0])
    labels = np.array([1, 0], dtype=bool)
    assert math.isnan(popularity_matched_auc(scores, labels, np.array([0, 1])))


def test_auc_pm은_구간이_하나면_전수_AUC와_같다():
    rng = np.random.default_rng(3)
    for _ in range(10):
        s = rng.integers(0, 5, size=60).astype(float)
        y = (rng.random(60) < 0.2)
        if 0 < y.sum() < y.size:
            assert popularity_matched_auc(s, y, np.zeros(60, dtype=int)) == \
                pytest.approx(auc(s, y))


def test_auc_pm은_인기순_모델의_이점을_깎는다():
    """양성이 인기 상위에 몰려 있으면 전수 AUC 는 높지만 매칭 AUC 는 0.5 근처가 된다."""
    n = 200
    pop = np.arange(n, dtype=float)
    labels = np.zeros(n, dtype=bool)
    labels[np.arange(180, 200, 2)] = True       # 정답이 전부 최상위 십분위에, 한 칸 건너
    decile = popularity_deciles(pop)
    assert auc(pop, labels) > 0.95
    # 같은 구간(마지막 십분위) 안에서는 양성/음성이 번갈아 있으니 0.5 근처
    assert popularity_matched_auc(pop, labels, decile) == pytest.approx(0.5, abs=0.1)
