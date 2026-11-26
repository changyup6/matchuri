# -*- coding: utf-8 -*-
"""사용자 5-fold 교차검증 — 하이퍼파라미터(λ 등)를 **테스트 사용자에게서 고르지 않기** 위해.

Step 1 이후 하이브리드(`quality_z + λ·content_z`)가 쓴다. 하네스를 바꾸지 않고 붙이도록
지금 만들어 둔다.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from . import config
from .evaluate import evaluate
from .scorers import ScoreContext, Scorer
from .split import UserSplit


def user_folds(splits: list[UserSplit], k: int = 5,
               seed: int | None = None) -> list[np.ndarray]:
    """사용자를 k 조각으로. 사용자 단위로 나눠야 같은 사람이 학습·평가에 겹치지 않는다."""
    seed = config.SEED if seed is None else seed
    idx = np.random.default_rng(seed).permutation(len(splits))
    return [idx[i::k] for i in range(k)]


def select_hyperparam(candidates: dict, splits: list[UserSplit], ctx: ScoreContext,
                      n_history: int, *, metric: str = "auc", k: int = 5,
                      seed: int | None = None) -> tuple:
    """`{라벨: scorer 팩토리}` 중 폴드 평균이 가장 좋은 라벨을 고른다.

    각 폴드에서 '남은 사용자' 로 고르고 '그 폴드' 에서 재지 않는다 — 여기서는 학습이
    사용자 안에서만 일어나므로(전역 파라미터는 λ 하나뿐) 폴드별 평가 평균이 곧
    out-of-fold 선택 근거가 된다.
    """
    folds = user_folds(splits, k=k, seed=seed)
    table: dict = {}
    for label, factory in candidates.items():
        per_fold = []
        for f in folds:
            sub = [splits[i] for i in f]
            res = evaluate(factory(), sub, ctx, n_history)
            per_fold.append(float(np.nanmean(res.p[metric])))
        table[label] = float(np.mean(per_fold))
    best = max(table, key=lambda lb: table[lb])
    return best, table


def outer_cv(candidates: dict, splits: list[UserSplit], ctx: ScoreContext,
             n_history: int, *, metric: str = "auc", k: int = 5,
             seed: int | None = None):
    """폴드 밖에서 고른 하이퍼파라미터로 그 폴드를 재는 out-of-fold 평가.

    폴드 f 의 사용자를 뺀 나머지로 `select_hyperparam` 을 돌려 라벨을 고르고, 그 라벨의
    모델로 폴드 f 만 평가한다. 사용자마다 **자기가 선택에 참여하지 않은** 모델의 점수가
    남으므로, 나온 지표를 다른 모델과 그대로 paired bootstrap 에 넣을 수 있다.

    반환: (TaskResult, [폴드별 고른 라벨]). TaskResult 의 사용자 순서는 입력 splits 와
    같다 — paired bootstrap 이 이 순서에 의존한다.
    """
    folds = user_folds(splits, k=k, seed=seed)
    n = len(splits)
    p_arrays: dict[str, np.ndarray] = {}
    r_arr = np.full(n, np.nan)
    full = np.zeros(n, dtype=bool)
    user_ids: list[str] = [s.user_id for s in splits]
    chosen: list[str] = []

    for f in folds:
        held = set(int(i) for i in f)
        train = [splits[i] for i in range(n) if i not in held]
        label, _ = select_hyperparam(candidates, train, ctx, n_history,
                                     metric=metric, k=k, seed=seed)
        chosen.append(label)
        res = evaluate(candidates[label](), [splits[i] for i in f], ctx, n_history)
        for key, vals in res.p.items():
            if key not in p_arrays:
                p_arrays[key] = np.full(n, np.nan)
            p_arrays[key][f] = vals
        r_arr[f] = res.r_auc
        full[f] = res.full_history

    from .evaluate import TaskResult
    return TaskResult(user_ids=user_ids, p=p_arrays, r_auc=r_arr,
                      full_history=full), chosen
