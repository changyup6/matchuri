# -*- coding: utf-8 -*-
"""협업 필터 scorer (Step 1.5) — `cf_item_{sum,mean,max}`.

    score(i) = agg_{h ∈ 이력} sim(h, i)

`sim` 은 `cf.build` 가 만든 item-item 공방문 코사인이다. 이력 해석은 하네스가
`ctx.history_mode` 로 정하며, 과제 P 의 기본인 `visit` 모드에서는 이력의 모든 방문이
가중 1 의 신호다(dislike 없음) — CF 는 원래 '무엇을 소비했는가' 만 보는 모델이라
이 해석과 정확히 맞는다.

**집계 셋의 뜻이 다르다.**
  `sum`   이력이 길수록 점수가 커진다. 사용자 안에서만 순위를 매기므로 이력 길이가
          순위를 바꾸지는 않지만, 여러 이력 가게와 조금씩 겹치는 가게가 유리하다.
  `mean`  `sum` 을 이력 길이로 나눈 것 — 사용자 안에서는 `sum` 과 **순위가 같다.**
          그래도 등록해 두는 것은 하이브리드에서 z 정규화 전 눈금이 다르기 때문이고,
          숫자가 같음을 보여 주는 것 자체가 결과의 일부다.
  `max`   가장 강하게 공방문된 이력 가게 하나로 결정한다.

**행렬을 만드는 곳.** `ctx.extras["cf"]["sim"]` 에 실행기가 미리 넣거나, 없으면
`ctx.extras["dataset"]` 과 `ctx.extras["eval_users"]` 로 여기서 한 번 만든다.
평가 사용자 제외(누수 규칙)는 `cf.build` 안에서만 일어난다 — 제외 집합을 두 군데서
만들면 언젠가 어긋난다.
"""
from __future__ import annotations

import numpy as np

from . import cf
from .scorers import History, ScoreContext, Scorer, register

#: 집계 이름 -> 사람이 읽을 설명.
CF_AGGS: dict[str, str] = {"sum": "합", "mean": "평균", "max": "최댓값"}


def covisit(ctx: ScoreContext) -> cf.CoVisit:
    """공방문 행렬 (실험 한 번에 한 개). 없으면 만들고 `ctx.extras` 에 캐시한다."""
    box = ctx.extras.setdefault("cf", {})
    if "covisit" not in box:
        ds = ctx.extras.get("dataset")
        eval_users = ctx.extras.get("eval_users")
        if ds is None or eval_users is None:
            raise KeyError(
                "cf scorer 는 `ctx.extras['dataset']` 와 `ctx.extras['eval_users']` "
                "가 필요하다 (평가 사용자를 빼고 공방문 행렬을 만들기 위해서다)")
        box["covisit"] = cf.build(ds, ctx.pool, set(eval_users))
    return box["covisit"]


def cf_score(sim: np.ndarray, pool_pos: dict, history: History, agg: str) -> np.ndarray:
    """이력 가게들의 행을 집계한다. 이력이 비면 전부 0(중립)."""
    idx = [pool_pos[sid] for sid, _ in history if sid in pool_pos]
    n = sim.shape[0]
    if not idx:
        return np.zeros(n, dtype=float)
    rows = sim[idx].astype(float)
    if agg == "sum":
        return rows.sum(axis=0)
    if agg == "mean":
        return rows.mean(axis=0)
    if agg == "max":
        return rows.max(axis=0)
    raise ValueError(f"모르는 집계 '{agg}' — {', '.join(CF_AGGS)} 중 하나여야 한다")


class CoVisitScorer(Scorer):
    """item-item 공방문 코사인을 이력 위에서 접는다."""

    def __init__(self, agg: str, name: str) -> None:
        self.agg, self.name = agg, name
        self.description = (f"협업 필터 — item-item 공방문 코사인 · 집계 "
                            f"{CF_AGGS[agg]} (평가 사용자 리뷰 전면 제외)")

    def prepare(self, ctx: ScoreContext) -> None:
        covisit(ctx)

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        return cf_score(covisit(ctx).sim, ctx.pool.pos, history, self.agg)


def _register_all() -> None:
    for agg in CF_AGGS:
        name = f"cf_item_{agg}"
        register(name)(lambda a=agg, n=name: CoVisitScorer(a, n))


_register_all()
