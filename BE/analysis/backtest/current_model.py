# -*- coding: utf-8 -*-
"""현재 서버 모델의 오프라인 재현.

`server/recsys/swipe.py` 의 세 가지를 그대로 옮긴다.

  특성   `_featurize` = [절편, 품질z, log1p(리뷰수)/6, 업종 11(원핫), 가격대 5(원핫)]
         -> 특성 자체는 `pool.build_pool` 이 만든다(풀과 같이 만들어야 qz 가 맞는다).
  갱신   `record` — 온라인 로지스틱 1스텝 + 라플라스 근사 분산
         prob = sigmoid(x·m);  v <- 1/(1/v + prob(1-prob)x^2);  m <- m - v·(prob-y)x
         **분산을 먼저 갱신하고 새 v 로 평균을 민다.** 순서가 바뀌면 값이 달라진다.
  점수   `peek` — w_q = 1/(1+n/15) 일 때  w_q·품질z + (1-w_q)·(X·m)

`main._apply` 는 선호 표현 하나를 `record` + `shown` 추가로 처리한다. shown(이미 준
카드 제외)은 하네스가 '이미 간 가게 제외'로 처리하므로 여기서는 갱신만 재현한다.

서버 모듈을 import 하지 않는 이유: 하네스가 fastapi 와 전역 세션 상태에 묶이면 실험이
무거워진다. 대신 `tests/test_current_model.py` 가 서버 함수와의 **정확한 일치**를 건다.
"""
from __future__ import annotations

import numpy as np

from .pool import Pool

QUALITY_HALFLIFE = 15.0     # swipe: w_q = 1/(1 + n/15)


def quality_weight(n: int) -> float:
    """스와이프 n 건일 때 품질 백본의 비중. 15장에서 반반."""
    return 1.0 / (1.0 + n / QUALITY_HALFLIFE)


class CurrentModel:
    """세션 하나. 서버의 `sess` 중 학습에 관여하는 부분만 갖는다."""

    def __init__(self, pool: Pool):
        self.pool = pool
        d = pool.X.shape[1]
        self.m = np.zeros(d)
        self.v = np.full(d, 1.0)
        self.n = 0

    def record(self, store_id: str, like: bool) -> None:
        """swipe.record 와 같은 1스텝. 풀 밖 가게는 서버와 마찬가지로 학습에서 뺀다."""
        i = self.pool.pos.get(store_id)
        if i is None:
            return
        x = self.pool.X[i]
        prob = 1 / (1 + np.exp(-x @ self.m))
        g = (prob - (1.0 if like else 0.0)) * x
        self.v = 1 / (1 / self.v + prob * (1 - prob) * x * x)
        self.m = self.m - self.v * g
        self.n += 1

    def replay(self, history) -> "CurrentModel":
        """(가게, like) 를 순서대로. main._apply 가 기동 복원 때 하는 일과 같다."""
        for store_id, like in history:
            self.record(store_id, like)
        return self

    def scores(self) -> np.ndarray:
        """사후 평균 점수 — swipe.peek 과 같다(탐색 난수 없음)."""
        w_q = quality_weight(self.n)
        return w_q * self.pool.qz + (1 - w_q) * (self.pool.X @ self.m)
