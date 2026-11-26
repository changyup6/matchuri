# -*- coding: utf-8 -*-
"""퇴화한 소개글 벡터를 `sim_text` 에서 NaN 으로 막는 규칙 (`recsys/content.py`).

설명(`ai_title`·`ai_summary`)이 없는 가게들이 태그만으로 만든 **글자까지 같은
문장**을 받아, 서로의 유사도가 정확히 1.000 으로 나왔다(실측: 후보 풀 1,629곳에서
9쌍 · 15곳 — 중국집 오늘완탕 ↔ 카페 에슬로우커피 등). 대각선만 보는 `has` 는 이것을
못 잡는다 — 자기 자신과의 유사도는 어차피 1.0 이라 통과한다.

**`has["text"]` 를 내리는 것만으로는 모자라다.** `has` 를 보는 것은 `content.py` 의
`component_scores` 뿐이고 `recsys/diversity.py`·`recsys/decision.py` 는
`art.sim_text` 를 직접 읽는다. 그래서 **행렬 자체**를 NaN 으로 막는다.

여기서 잠그는 것은 넷이다.
  * 다른 가게와 1.0 인 행을 **찾는다**
  * 그 행·열이 NaN 이 되면 `has["text"]` 가 **자동으로** False 가 된다
  * 평범한 행과 이미 NaN 인 행은 **그대로**다
  * `_degenerate_text_rows` 는 **찾기만 한다** — 행렬을 고치지 않는다
"""
from __future__ import annotations

import numpy as np

from recsys import content


def _space(n: int, angles: list[float | None]) -> np.ndarray:
    """각도로 2차원 단위벡터를 만들어 코사인 행렬을 굽는다. None 은 소개글 없음(NaN).

    같은 각도를 둘에게 주면 **같은 벡터**가 되어 서로의 코사인이 1.0 이다 — 설명이
    없어 태그 문장만 남은 가게들이 실제로 만든 상황과 같다.
    """
    out = np.full((n, n), np.nan, dtype=np.float32)
    idx = [i for i, a in enumerate(angles) if a is not None]
    v = np.array([[np.cos(angles[i]), np.sin(angles[i])] for i in idx],
                 dtype=np.float32)
    sim = v @ v.T
    out[np.ix_(idx, idx)] = ((sim + sim.T) * 0.5).astype(np.float32)
    return out


def _has_text(sim: np.ndarray) -> list[bool]:
    """`content._build` 가 `has` 를 만드는 것과 같은 규칙 — 대각선이 유한한가."""
    return np.isfinite(np.diagonal(sim)).tolist()


def test_다른_가게와_1점0_인_행만_찾는다():
    # 0·1·2 는 같은 벡터(퇴화) · 3·4 는 서로 다른 소개글 · 5 는 소개글 없음
    sim = _space(6, [0.3, 0.3, 0.3, 1.1, 2.0, None])
    assert _has_text(sim) == [True] * 5 + [False]

    bad = content._degenerate_text_rows(sim)
    assert bad.tolist() == [True, True, True, False, False, False]
    # 이미 NaN 이던 행은 퇴화로 세지 않는다 — '모른다' 는 이미 올바른 상태다
    assert not bool(bad[5])
    # 찾기만 한다 — 행렬은 그대로다
    assert np.isfinite(sim[0, 1])


def test_행_열을_NaN_으로_막으면_has_가_자동으로_내려간다():
    """`_build` 가 하는 것과 같은 조작. 소비자가 갈리지 않게 **행렬**을 막는다."""
    sim = _space(6, [0.3, 0.3, 0.3, 1.1, 2.0, None])
    bad = content._degenerate_text_rows(sim)
    sim[bad, :] = np.nan
    sim[:, bad] = np.nan

    # 대각선이 NaN 이 되었으므로 has["text"] 가 따로 손대지 않아도 False 다
    assert _has_text(sim) == [False, False, False, True, True, False]
    # `art.sim_text` 를 직접 읽는 소비자(diversity·decision)에게도 같은 규칙이 닿는다
    assert not np.isfinite(sim[0, 1])        # 퇴화 쌍
    assert not np.isfinite(sim[0, 3])        # 퇴화 가게가 낀 모든 쌍
    assert np.isfinite(sim[3, 4])            # 멀쩡한 쌍은 남는다
    # 1.000 짜리 쌍이 하나도 남지 않는다
    off = sim.copy()
    np.fill_diagonal(off, np.nan)
    assert not (off >= 1.0 - content._DEGENERATE_TOL).any()


def test_퇴화가_없으면_아무것도_안_찾는다():
    sim = _space(4, [0.1, 0.9, 1.7, 2.5])
    assert not content._degenerate_text_rows(sim).any()


def test_float16_산출물에서도_같은_판정():
    """산출물은 float16 으로 저장된다(`npy()` 가 float32 로 편다).

    float16 에서 1.0 바로 아래 표현 가능한 값이 0.99951 이라, 허용오차 1e-6 은
    캐스팅 뒤에도 '정확히 1.0' 을 뜻한다. 0.99 짜리 '아주 닮은' 쌍은 걸리지 않는다.
    """
    sim = _space(3, [0.4, 0.4, 0.4 + 0.14])       # cos(0.14) ≈ 0.990
    sim16 = np.asarray(np.asarray(sim, dtype=np.float16), dtype=np.float32)

    assert content._degenerate_text_rows(sim16).tolist() == [True, True, False]


def test_행이_하나뿐이면_퇴화가_없다():
    """자기 자신밖에 없으면 '다른 가게와 1.0' 이 성립할 수 없다."""
    sim = _space(3, [0.5, None, None])
    assert not content._degenerate_text_rows(sim).any()
