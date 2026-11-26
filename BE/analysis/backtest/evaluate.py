# -*- coding: utf-8 -*-
"""한 모델 × 한 N 을 평가한다 — 과제 P(다음 방문)와 R(방문 중 만족).

과제 P
  후보 = 후보 풀에서 **이미 간 가게를 뺀 나머지 전부**(샘플링 없음).
  양성 = 미래 방문 가게, 음성 = 나머지 후보 전부.
  지표 = AUC · auc_pm(인기 매칭 AUC) · Hit@10 · Recall@50 · NDCG@20.
과제 R
  미래 방문 가게만 놓고 rating>=4 를 양성, <=3 을 음성으로 AUC. 양쪽 클래스가 다 있는
  사용자만 센다.

이력 해석(`visit` / `like_dislike`)은 `ctx.history_mode` 가 정한다 — 모델마다 다르면
비교가 성립하지 않으므로 여기 한 곳에서만 읽는다.

scorer 는 풀 전체 점수만 내면 된다. '이미 간 가게 제외' 와 동점 처리는 전부 여기서
한다 — 모델마다 다르면 비교가 성립하지 않기 때문이다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import config, metrics
from .rng import generator
from .scorers import ScoreContext, Scorer
from .split import UserSplit

P_METRICS = ("auc", "auc_pm", f"hit@{config.HIT_K}",
             f"recall@{config.RECALL_K}", f"ndcg@{config.NDCG_K}")


@dataclass
class TaskResult:
    """사용자 순서를 보존한 지표 배열. paired bootstrap 이 이 순서에 의존한다."""
    user_ids: list[str]
    p: dict[str, np.ndarray]      # 지표 이름 -> 사용자별 값
    r_auc: np.ndarray             # 과제 R (해당 없는 사용자는 nan)
    full_history: np.ndarray      # 이력 N 을 다 채웠는지 (bool)


def _tiebreak(user_id: str, size: int, seed: int) -> np.ndarray:
    """동점을 깨는 사용자별 무작위 순열. 모델과 무관하게 같은 값을 쓴다."""
    return generator(seed, "tiebreak", user_id).permutation(size)


def evaluate(scorer: Scorer, splits: list[UserSplit], ctx: ScoreContext,
             n_history: int) -> TaskResult:
    scorer.prepare(ctx)
    pool = ctx.pool
    n_pool = len(pool)

    user_ids: list[str] = []
    p_vals: dict[str, list[float]] = {k: [] for k in P_METRICS}
    r_vals: list[float] = []
    full: list[bool] = []

    for sp in splits:
        signals = sp.signals(n_history, ctx.history_mode)
        scores = np.asarray(scorer.score(signals, ctx, user_id=sp.user_id),
                            dtype=float)
        if scores.shape != (n_pool,):
            raise ValueError(f"{scorer.name}: 점수 길이 {scores.shape} != 풀 {n_pool}")

        # ── 과제 P
        seen = np.zeros(n_pool, dtype=bool)
        for sid in sp.seen_stores:
            seen[pool.pos[sid]] = True
        cand = ~seen
        labels = np.zeros(n_pool, dtype=bool)
        for sid, _ in sp.future:
            labels[pool.pos[sid]] = True

        tb = _tiebreak(sp.user_id, n_pool, ctx.seed)[cand]
        m = metrics.ranking_metrics(scores[cand], labels[cand], tb,
                                    hit_k=config.HIT_K, recall_k=config.RECALL_K,
                                    ndcg_k=config.NDCG_K)
        # 인기 매칭 AUC — 같은 리뷰수 십분위 안에서만 순위를 비교한다(PLAN 5절 2번).
        m["auc_pm"] = metrics.popularity_matched_auc(
            scores[cand], labels[cand], pool.pop_decile[cand])

        # ── 과제 R
        lab = sp.satisfaction_labels()
        if lab and 0 < sum(y for _, y in lab) < len(lab):
            idx = np.array([pool.pos[sid] for sid, _ in lab])
            r = metrics.auc(scores[idx], np.array([y for _, y in lab], dtype=bool))
        else:
            r = float("nan")

        user_ids.append(sp.user_id)
        for k in P_METRICS:
            p_vals[k].append(m[k])
        r_vals.append(r)
        full.append(sp.has_full_history(n_history))

    return TaskResult(user_ids=user_ids,
                      p={k: np.array(v, dtype=float) for k, v in p_vals.items()},
                      r_auc=np.array(r_vals, dtype=float),
                      full_history=np.array(full, dtype=bool))
