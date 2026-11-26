# -*- coding: utf-8 -*-
"""paired bootstrap — 재현성과 '우위 미확정' 판정."""
from __future__ import annotations

import math

import numpy as np
import pytest

from analysis.backtest.bootstrap import paired_diff_ci


def test_seed가_같으면_결과가_같다():
    rng = np.random.default_rng(1)
    a, b = rng.random(50), rng.random(50)
    assert paired_diff_ci(a, b, seed=7) == paired_diff_ci(a, b, seed=7)


def test_일관된_우위는_CI가_0을_넘긴다():
    b = np.linspace(0.4, 0.6, 80)
    a = b + 0.1                                  # 모든 사용자가 +0.1
    r = paired_diff_ci(a, b, seed=7)
    assert r["diff"] > 0 and r["ci_low"] > 0 and r["significant"]


def test_차이가_없으면_우위_미확정():
    rng = np.random.default_rng(2)
    b = rng.random(200)
    a = b + rng.normal(0, 0.05, 200)
    r = paired_diff_ci(a, b, seed=7)
    assert r["ci_low"] < 0 < r["ci_high"] and not r["significant"]


def test_자기_자신과_비교하면_차이_0():
    a = np.random.default_rng(3).random(30)
    r = paired_diff_ci(a, a, seed=7)
    assert r["diff"] == 0.0 and r["ci_low"] == 0.0 and r["ci_high"] == 0.0


def test_nan인_사용자는_쌍에서_빠진다():
    a = np.array([0.5, np.nan, 0.7, 0.9])
    b = np.array([0.4, 0.3, np.nan, 0.8])
    r = paired_diff_ci(a, b, seed=7)
    assert r["n_users"] == 2                     # 0번·3번만 남는다
    assert r["diff"] == pytest.approx(0.1)       # (0.1 + 0.1) / 2


def test_쌍이_없으면_nan():
    r = paired_diff_ci(np.array([np.nan]), np.array([0.5]), seed=7)
    assert r["n_users"] == 0 and math.isnan(r["diff"]) and not r["significant"]
