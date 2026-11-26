# -*- coding: utf-8 -*-
"""가게 요약문 modality — 코사인·NaN 규칙·캐시 지문.

임베딩 산출물은 git 에 없으므로 여기서 실데이터를 요구하면 안 된다. 손으로 만든
2차원 단위벡터로 답을 직접 계산해 검증한다(`test_menu_content.py` 와 같은 방식).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from analysis.backtest.scorers_menu import content_score
from analysis.content.store_text import StoreTextSpace, similarity_matrix

ANGLES = {"A": 0.0, "B": 90.0, "C": 60.0}


def unit(deg: float) -> np.ndarray:
    return np.array([math.cos(math.radians(deg)), math.sin(math.radians(deg))],
                    dtype=np.float32)


def make_space(store_angles: dict[str, float | None]) -> StoreTextSpace:
    """`None` 이면 요약문이 없는 가게(NaN 행)."""
    ids = list(store_angles)
    vecs = np.full((len(ids), 2), np.nan, dtype=np.float32)
    for i, sid in enumerate(ids):
        deg = store_angles[sid]
        if deg is not None:
            vecs[i] = unit(deg)
    return StoreTextSpace(store_ids=ids, vecs=vecs,
                          texts=["요약문" if store_angles[s] is not None else ""
                                 for s in ids])


# ────────────────────────────────────────────── 코사인
def test_유사도는_손으로_계산한_코사인이다():
    space = make_space({"A": 0.0, "B": 90.0, "C": 60.0})
    sim = similarity_matrix(space)
    assert sim[0, 1] == pytest.approx(0.0, abs=1e-6)               # 0° vs 90°
    assert sim[0, 2] == pytest.approx(math.cos(math.radians(60)), abs=1e-6)
    assert sim[1, 2] == pytest.approx(math.cos(math.radians(30)), abs=1e-6)


def test_자기자신은_1_이고_대칭이다():
    space = make_space({"A": 0.0, "B": 90.0, "C": 60.0})
    sim = similarity_matrix(space)
    np.testing.assert_allclose(np.diag(sim), 1.0, atol=1e-6)
    np.testing.assert_allclose(sim, sim.T, atol=1e-7)


# ────────────────────────────────────────────── NaN 규칙 (Step 1 과 같아야 한다)
def test_요약문이_없는_가게는_행과_열이_전부_NaN():
    space = make_space({"A": 0.0, "없음": None, "C": 60.0})
    sim = similarity_matrix(space)
    assert np.isnan(sim[1]).all() and np.isnan(sim[:, 1]).all()
    assert np.isfinite(sim[0, 2])                 # 나머지는 멀쩡하다


def test_커버리지는_요약문_있는_가게_비율():
    space = make_space({"A": 0.0, "없음": None, "C": 60.0, "없음2": None})
    assert space.coverage == pytest.approx(0.5)
    assert space.has_text.tolist() == [True, False, True, False]


def test_요약문_없는_후보는_중립값으로_채운다():
    """0 이 아니라 그 사용자의 평균 — '모른다' 를 '싫다' 로 번역하지 않는다."""
    space = make_space({"H": 0.0, "A": 0.0, "B": 90.0, "없음": None})
    sim = similarity_matrix(space)
    pos = {sid: i for i, sid in enumerate(space.store_ids)}
    score = content_score(sim, pos, [("H", True)], "max", 0.0)
    assert score[pos["A"]] == pytest.approx(1.0, abs=1e-6)
    assert score[pos["B"]] == pytest.approx(0.0, abs=1e-6)
    finite = [score[pos["H"]], score[pos["A"]], score[pos["B"]]]
    assert score[pos["없음"]] == pytest.approx(float(np.mean(finite)), abs=1e-6)


def test_요약문_없는_이력은_집계에서_빠진다():
    space = make_space({"H1": 0.0, "없음": None, "A": 60.0})
    sim = similarity_matrix(space)
    pos = {sid: i for i, sid in enumerate(space.store_ids)}
    with_missing = content_score(sim, pos, [("H1", True), ("없음", True)], "mean", 0.0)
    only_ok = content_score(sim, pos, [("H1", True)], "mean", 0.0)
    np.testing.assert_allclose(with_missing, only_ok, atol=1e-7)


def test_전부_NaN_이면_0으로_떨어진다():
    space = make_space({"없음": None, "없음2": None})
    sim = similarity_matrix(space)
    assert np.isnan(sim).all()
    pos = {sid: i for i, sid in enumerate(space.store_ids)}
    np.testing.assert_allclose(content_score(sim, pos, [("없음", True)], "max", 0.0),
                               np.zeros(2))


# ────────────────────────────────────────────── 캐시 지문
def test_지문은_임베딩_sha와_풀_구성에_달려_있다():
    a = make_space({"A": 0.0, "B": 90.0})
    b = make_space({"A": 0.0, "B": 90.0})
    assert a.fingerprint() == b.fingerprint()
    b.manifest_sha = "다른임베딩"
    assert a.fingerprint() != b.fingerprint()
    c = make_space({"A": 0.0, "B": 90.0, "C": 60.0})
    assert a.fingerprint() != c.fingerprint()
    d = make_space({"B": 90.0, "A": 0.0})          # 순서만 바뀌어도 다른 캐시다
    assert a.fingerprint() != d.fingerprint()
