# -*- coding: utf-8 -*-
"""사용자 단위 paired bootstrap — PLAN 1절 '집계'.

같은 재표본 인덱스를 모든 모델에 똑같이 쓴다(paired). 그래야 '모델 A 가 B 보다 나은가'
의 분산에서 사용자 난이도 차이가 빠진다. 2,000회, seed 고정.

CI 가 0 을 포함하면 **우위 미확정**이다. 작은 양수를 근거로 삼지 않는다.
"""
from __future__ import annotations

import numpy as np

from . import config


def paired_diff_ci(model: np.ndarray, baseline: np.ndarray, *,
                   resamples: int | None = None, seed: int | None = None,
                   alpha: float | None = None) -> dict:
    """사용자별 지표 두 벌(같은 순서)을 받아 평균 차이와 백분위 CI 를 낸다.

    한쪽이라도 nan 인 사용자는 쌍에서 뺀다(과제 R 처럼 지표가 정의되지 않는 경우).
    """
    resamples = config.BOOTSTRAP_RESAMPLES if resamples is None else resamples
    seed = config.SEED if seed is None else seed
    alpha = config.BOOTSTRAP_ALPHA if alpha is None else alpha

    m = np.asarray(model, dtype=float)
    b = np.asarray(baseline, dtype=float)
    ok = ~(np.isnan(m) | np.isnan(b))
    d = m[ok] - b[ok]
    n = d.size
    if n == 0:
        return {"diff": float("nan"), "ci_low": float("nan"),
                "ci_high": float("nan"), "n_users": 0, "significant": False}

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(resamples, n))
    means = d[idx].mean(axis=1)
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return {"diff": float(d.mean()), "ci_low": lo, "ci_high": hi,
            "n_users": int(n), "significant": bool(lo > 0 or hi < 0)}
