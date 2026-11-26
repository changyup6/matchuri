# -*- coding: utf-8 -*-
"""랭킹 지표 — AUC · Hit@K · Recall@K · NDCG@K.

전부 사용자 한 명의 후보 점수 벡터와 정답 마스크를 받아 스칼라를 낸다. 사용자 평균은
`run.py` 가 낸다.

동점 처리를 두 군데서 다르게 쓴다. AUC 는 동점을 0.5 로 세는 정식 정의(Mann-Whitney U)
를 쓰고, top-K 는 **사용자마다 결정적인 무작위 순열**로 깬다. 인덱스 순으로 깨면
가게 파일 순서가 곧 이득이 되어(예: 상수 점수 모델이 앞쪽 가게 덕에 점수를 얻는다)
지표가 거짓말을 한다.
"""
from __future__ import annotations

import numpy as np


def rank_order(scores: np.ndarray, tiebreak: np.ndarray) -> np.ndarray:
    """점수 내림차순 인덱스. 동점은 tiebreak 오름차순(무작위 순열)으로 깬다."""
    return np.lexsort((tiebreak, -scores))


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """ROC AUC. 동점은 0.5 로 센다. 한쪽 클래스만 있으면 nan."""
    labels = np.asarray(labels).astype(bool)
    n_pos = int(labels.sum())
    n_neg = int(labels.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    s = scores[order]
    ranks = np.empty(s.size, dtype=float)
    i = 0
    while i < s.size:                       # 동점 구간에 평균 순위를 준다
        j = i
        while j + 1 < s.size and s[j + 1] == s[i]:
            j += 1
        ranks[i:j + 1] = (i + j) / 2.0 + 1.0
        i = j + 1
    r = np.empty_like(ranks)
    r[order] = ranks
    return float((r[labels].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def hit_at_k(order: np.ndarray, labels: np.ndarray, k: int) -> float:
    """상위 K 안에 정답이 하나라도 있으면 1."""
    return float(labels[order[:k]].any())


def recall_at_k(order: np.ndarray, labels: np.ndarray, k: int) -> float:
    n_pos = int(np.asarray(labels).sum())
    if n_pos == 0:
        return float("nan")
    return float(labels[order[:k]].sum()) / n_pos


def ndcg_at_k(order: np.ndarray, labels: np.ndarray, k: int) -> float:
    """이진 이득 NDCG. 이상 순위는 정답을 앞에서부터 min(정답수, K) 개 채운 경우."""
    n_pos = int(np.asarray(labels).sum())
    if n_pos == 0:
        return float("nan")
    gains = np.asarray(labels)[order[:k]].astype(float)
    disc = 1.0 / np.log2(np.arange(2, gains.size + 2))
    dcg = float((gains * disc).sum())
    ideal = float(disc[:min(n_pos, k)].sum())
    return dcg / ideal if ideal > 0 else float("nan")


def ranking_metrics(scores: np.ndarray, labels: np.ndarray, tiebreak: np.ndarray,
                    *, hit_k: int, recall_k: int, ndcg_k: int) -> dict[str, float]:
    """과제 P 의 지표 묶음."""
    labels = np.asarray(labels).astype(bool)
    order = rank_order(scores, tiebreak)
    return {
        "auc": auc(scores, labels),
        f"hit@{hit_k}": hit_at_k(order, labels, hit_k),
        f"recall@{recall_k}": recall_at_k(order, labels, recall_k),
        f"ndcg@{ndcg_k}": ndcg_at_k(order, labels, ndcg_k),
    }


def popularity_deciles(values: np.ndarray, n_bins: int = 10) -> np.ndarray:
    """인기 변수를 십분위로 자른다. 반환값은 0..n_bins-1 의 구간 번호.

    카카오 리뷰 수는 동점이 아주 많아(리뷰 1건짜리가 수백 곳) 분위 경계가 겹친다.
    겹친 경계는 `np.unique` 로 접어서 실제 구간 수가 10 보다 적어질 수 있게 두었다 —
    억지로 10 등분하면 같은 리뷰 수가 서로 다른 구간에 갈라져 '같은 인기' 라는
    매칭의 전제가 깨진다.
    """
    v = np.asarray(values, dtype=float)
    edges = np.unique(np.quantile(v, np.arange(1, n_bins) / n_bins))
    return np.searchsorted(edges, v, side="right").astype(int)


def popularity_matched_auc(scores: np.ndarray, labels: np.ndarray,
                           decile: np.ndarray) -> float:
    """인기 매칭 AUC — 양성마다 **같은 인기 십분위의 음성만** 상대로 순위 비교.

    `auc` 는 풀 전체를 음성으로 쓰므로 '리뷰가 많은 가게가 정답이 되기 쉽다' 는
    표본틀 편향(PLAN 5절)을 그대로 점수로 준다. 여기서는 양성 하나마다 같은 십분위
    안의 음성만 상대로 P(score(양성) > score(음성)) 를 재고(동점 0.5) 양성 평균을 낸다.
    인기를 통제한 뒤에도 남는 신호가 있는지를 보는 지표다.

    입력은 **후보로 남은 것만** 이어야 한다(이미 간 가게는 호출자가 뺀다).
    자기 십분위에 음성이 하나도 없는 양성은 세지 않는다. 셀 양성이 없으면 nan.
    """
    labels = np.asarray(labels).astype(bool)
    scores = np.asarray(scores, dtype=float)
    decile = np.asarray(decile, dtype=int)
    if labels.sum() == 0 or (~labels).sum() == 0:
        return float("nan")

    per_pos: list[float] = []
    for d in np.unique(decile[labels]):
        in_bin = decile == d
        neg = np.sort(scores[in_bin & ~labels])
        if neg.size == 0:
            continue                                  # 상대가 없는 양성은 건너뛴다
        pos = scores[in_bin & labels]
        n_less = np.searchsorted(neg, pos, side="left")
        n_le = np.searchsorted(neg, pos, side="right")
        per_pos.extend((n_less + 0.5 * (n_le - n_less)) / neg.size)
    return float(np.mean(per_pos)) if per_pos else float("nan")
