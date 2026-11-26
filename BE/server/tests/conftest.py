# -*- coding: utf-8 -*-
"""테스트 공통 설정 — 작은 합성 풀과 임시 state 디렉터리.

**진짜 산출물(62MB)을 읽지 않고도** 성분 계산·학습·인터리빙을 검증할 수 있어야 한다.
그래야 데이터가 바뀌어도 규칙이 바뀌지 않았음을 빠르게 확인할 수 있다. 합성 풀은
가게 6곳이고 modality 마다 구멍(NaN)을 하나씩 뚫어 뒀다 — NaN 규칙이 실제로 도는지는
구멍이 있어야만 잴 수 있다.

실행:
    cd server && MATCHURI_STATE=$(mktemp -d) ~/venvs/recsys/bin/python -m pytest tests -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

# 파일에 쓰기 전에 정해야 한다 — app.state 가 import 시점에 경로를 굳힌다.
# 밖에서 주지 않았으면 실행마다 새 임시 디렉터리를 쓴다(이전 실행의 기록이 남아
# `main` import 때 되먹여지면 테스트가 서로를 오염시킨다).
if not os.environ.get("MATCHURI_STATE"):
    import tempfile
    os.environ["MATCHURI_STATE"] = tempfile.mkdtemp(prefix="matchuri-test-")

# 운영에서는 복원이 백그라운드 스레드로 돈다(`app/boot.py`) — 그래야 포트가 먼저 열린다.
# 테스트는 `TestClient(app)` 를 `with` 없이 쓰므로 lifespan 이 없고, 스레드로 돌면 첫
# 요청이 503 을 받는다. 동기로 돌려 `import main` 이 끝나면 복원도 끝나 있게 한다 —
# 옛 동작과 같다. 게이트 자체는 `tests/test_boot_gate.py` 가 상태를 직접 놓고 잰다.
os.environ.setdefault("MATCHURI_RESTORE_SYNC", "1")

from recsys import content, swipe, taste  # noqa: E402

#: 합성 풀. s0~s1 은 메뉴가 거의 같고(같은 브랜드), s2~s3 이 한 무리, s4 는 메뉴가
#: 없고(NaN), s5 는 요약문이 없고, s3 은 음식 사진이 없다. modality 마다 구멍의
#: 자리가 다른 것이 요점이다 — NaN 규칙이 modality 별로 도는지는 그래야 잴 수 있다.
IDS = ["s0", "s1", "s2", "s3", "s4", "s5"]
BRANDS = ["b0", "b0", "b2", "b3", "b4", "b5"]
CATS = {"s0": "한식", "s1": "한식", "s2": "일식", "s3": "일식", "s4": "카페", "s5": "중식"}
NAMES = {"s0": "가게영", "s1": "가게일", "s2": "가게이", "s3": "가게삼",
         "s4": "가게사", "s5": "가게오"}


def _sym(rows: list[list[float]]) -> np.ndarray:
    m = np.array(rows, dtype=np.float32)
    return ((m + m.T) / 2).astype(np.float32)


def _blank(mat: np.ndarray, i: int) -> np.ndarray:
    """가게 i 의 행·열을 통째로 NaN 으로 — 그 modality 가 없는 가게의 표현."""
    out = mat.copy()
    out[i, :] = np.nan
    out[:, i] = np.nan
    return out


def make_artifacts() -> content.Artifacts:
    menu = _sym([
        [1.00, 0.95, 0.20, 0.10, 0.00, 0.05],
        [0.95, 1.00, 0.15, 0.10, 0.00, 0.05],
        [0.20, 0.15, 1.00, 0.80, 0.00, 0.10],
        [0.10, 0.10, 0.80, 1.00, 0.00, 0.10],
        [0.00, 0.00, 0.00, 0.00, 1.00, 0.00],
        [0.05, 0.05, 0.10, 0.10, 0.00, 1.00],
    ])
    menu = _blank(menu, 4)                       # s4 는 메뉴가 없다
    text = _sym([
        [1.00, 0.60, 0.30, 0.20, 0.40, 0.10],
        [0.60, 1.00, 0.25, 0.20, 0.35, 0.10],
        [0.30, 0.25, 1.00, 0.70, 0.20, 0.15],
        [0.20, 0.20, 0.70, 1.00, 0.20, 0.15],
        [0.40, 0.35, 0.20, 0.20, 1.00, 0.05],
        [0.10, 0.10, 0.15, 0.15, 0.05, 1.00],
    ])
    text = _blank(text, 5)                       # s5 는 요약문이 없다
    review = _sym([
        [1.00, 0.50, 0.40, 0.30, 0.20, 0.10],
        [0.50, 1.00, 0.45, 0.30, 0.20, 0.10],
        [0.40, 0.45, 1.00, 0.60, 0.20, 0.10],
        [0.30, 0.30, 0.60, 1.00, 0.20, 0.10],
        [0.20, 0.20, 0.20, 0.20, 1.00, 0.10],
        [0.10, 0.10, 0.10, 0.10, 0.10, 1.00],
    ])
    # 음식 사진 — s0 과 가장 닮은 곳이 메뉴·요약문·리뷰(전부 s1)와 **다르다**(s2).
    # 성분마다 다른 가게를 추천 이유로 내세우는지 잴 수 있어야 한다.
    food = _sym([
        [1.00, 0.30, 0.85, 0.10, 0.20, 0.15],
        [0.30, 1.00, 0.25, 0.10, 0.10, 0.10],
        [0.85, 0.25, 1.00, 0.10, 0.15, 0.20],
        [0.10, 0.10, 0.10, 1.00, 0.10, 0.10],
        [0.20, 0.10, 0.15, 0.10, 1.00, 0.30],
        [0.15, 0.10, 0.20, 0.10, 0.30, 1.00],
    ])
    food = _blank(food, 3)                       # s3 은 음식 사진이 없다
    cooc = _sym([
        [1.0, 0.1, 0.5, 0.0, 0.0, 0.0],
        [0.1, 1.0, 0.0, 0.0, 0.0, 0.0],
        [0.5, 0.0, 1.0, 0.2, 0.0, 0.0],
        [0.0, 0.0, 0.2, 1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    ])
    has = {"menu": np.isfinite(np.diagonal(menu)).copy(),
           "text": np.isfinite(np.diagonal(text)).copy(),
           "review": np.isfinite(np.diagonal(review)).copy(),
           "food": np.isfinite(np.diagonal(food)).copy()}
    # 설명용 리뷰 벡터: 가게마다 2개, 2차원. 실제 산출물과 같은 모양(≤6개/가게)이다.
    vecs = np.array([[1, 0], [0.9, 0.44], [0.95, 0.31], [0.8, 0.6],
                     [0.6, 0.8], [0.5, 0.87], [0.4, 0.92], [0.3, 0.95],
                     [0, 1], [0.1, 0.99], [0.7, 0.71], [0.71, 0.7]],
                    dtype=np.float16)
    return content.Artifacts(
        ids=list(IDS), pos={s: i for i, s in enumerate(IDS)},
        pop=np.log1p(np.array([100.0, 50.0, 10.0, 5.0, 1.0, 0.0])),
        quality=np.array([1.0, 0.5, 0.0, -0.5, -1.0, 0.2]),
        sim_menu=menu, sim_text=text, sim_review=review, sim_food=food, cooc=cooc,
        has=has, brand=list(BRANDS),
        prior={"source": "test", "mean": {"pop": 1.0, "quality": 0.3, "cf": 0.5,
                                          "menu": 0.3, "text": 0.4, "review": 0.6,
                                          "food": 0.0, "neg": -0.25},
               "sd": {c: 0.5 for c in content.COMPONENTS}},
        review_vecs=vecs,
        review_starts=np.array([0, 2, 4, 6, 8, 10]),
        review_counts=np.array([2, 2, 2, 2, 2, 2]),
        review_snippets=[[f"{s} 리뷰 {j}" for j in range(2)] for s in IDS],
        review_ids=[[i * 2, i * 2 + 1] for i in range(len(IDS))])


@pytest.fixture
def art(monkeypatch) -> content.Artifacts:
    """합성 산출물로 갈아끼운다. 진짜 데이터·행렬을 하나도 읽지 않는다."""
    a = make_artifacts()
    monkeypatch.setattr(content, "load", lambda *args, **kw: a)
    monkeypatch.setattr(swipe, "pool", lambda: {
        "ids": list(IDS), "pos": {s: i for i, s in enumerate(IDS)},
        "cat": dict(CATS), "qz": np.array(a.quality),
        "X": np.zeros((len(IDS), 1))})
    monkeypatch.setattr(taste.svc, "get_store",
                        lambda sid: {"name": NAMES.get(sid), "kakao_review_count": 10,
                                     "kakao_avg_score": 4.5} if sid in NAMES else None)
    taste._sessions.clear()
    return a


@pytest.fixture
def sess(art):
    return taste.session_for("u_test")
