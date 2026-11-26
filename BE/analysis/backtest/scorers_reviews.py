# -*- coding: utf-8 -*-
"""리뷰 다중 벡터 scorer (Step 2) — `review_<변종>_<집계>`.

    score(i) = agg_{h ∈ 이력} MaxSim(리뷰집합_h, 리뷰집합_i)

메뉴 scorer 와 **같은 집계·같은 NaN 규칙·같은 하이브리드**를 쓴다(`scorers_menu` 의
함수를 그대로 부른다). 리뷰 쪽에서 새로 정하는 것은 가게마다 어떤 리뷰 12개를 고르는가
(`longest12` / `random12` / `recent12`)뿐이고, 그것이 이 Step 이 묻는 질문이다.

대조군 `review_<변종>_single` 은 **가게 = 리뷰 벡터 평균 1개, 사용자 = 이력 가게 평균
1개, 코사인** 이다. '평균이 다봉형 취향을 뭉갠다'(PLAN 원칙 1)를 리뷰에서 다시 재는
자리이자, 다중 벡터의 이득이 K 때문인지 late interaction 때문인지 가르는 잣대다.

**누수.** 가게 표현에 들어가는 리뷰에서 평가 사용자의 것은 전부 빠진다. 제외는
`analysis.content.reviews.ReviewSpace.build` 안에서만 일어나고, 여기서는 실행기가
`ctx.extras["eval_users"]` 로 넘겨준 집합을 그대로 전달한다 — CF(`scorers_cf`)가 쓰는
집합과 같은 것이다.
"""
from __future__ import annotations

import numpy as np

from analysis.content import reviews as review_content

from . import scorers_menu as menu_scorers
from .scorers import History, ScoreContext, Scorer, register

# `scorers_text` 와 같은 이유로 모듈째 들고 온다 (순환 import).

#: 리뷰 modality 가 쓰는 집계. 메뉴와 같은 이름·같은 구현이다.
REVIEW_AGGS = ("max", "mean", "lse01")


def review_space(ctx: ScoreContext) -> review_content.ReviewSpace:
    """풀 전체의 리뷰 공간. 실험 한 번에 한 개만 만든다."""
    box = ctx.extras.setdefault("reviews", {})
    if "space" not in box:
        eval_users = ctx.extras.get("eval_users")
        if eval_users is None:
            raise KeyError(
                "review scorer 는 `ctx.extras['eval_users']` 가 필요하다 "
                "(평가 사용자의 리뷰를 가게 표현에서 빼기 위해서다 — PLAN 1절 누수 규칙)")
        box["space"] = review_content.ReviewSpace.build(
            ctx.pool.ids, set(eval_users), seed=ctx.seed)
    return box["space"]


#: 대조군이 개수 구조를 빌려 오는 변종. 세 변종의 가게별 개수는 모두 같으므로
#: (같은 후보 집합을 같은 상한으로 자른다) 어느 것을 써도 결과가 같다.
NULL_VARIANT = "longest12"


def similarity(ctx: ScoreContext, variant_name: str) -> np.ndarray:
    """변종의 가게-가게 MaxSim (캐시 → 없으면 계산 후 캐시).

    `variant_name == "null"` 이면 벡터만 난수로 바꾼 대조군 행렬이다.
    """
    box = ctx.extras.setdefault("reviews", {})
    sims = box.setdefault("sim", {})
    if variant_name not in sims:
        null = variant_name == "null"
        variant = review_content.VARIANTS[NULL_VARIANT if null
                                          else variant_name]
        mat, _ = review_content.load_or_build_similarity(review_space(ctx), variant,
                                                         null=null)
        sims[variant_name] = mat
    return sims[variant_name]


class ReviewMaxSimScorer(Scorer):
    """리뷰 MaxSim 유사도를 이력 위에서 접는다."""

    def __init__(self, variant: str, agg: str, name: str) -> None:
        self.variant, self.agg, self.name = variant, agg, name
        if variant == "null":
            self.description = (f"**대조군** 리뷰 MaxSim — 개수 구조는 그대로, 벡터만 "
                                f"난수 · 집계 {menu_scorers.AGGS[agg][0]}")
        else:
            v = review_content.VARIANTS[variant]
            self.description = (f"리뷰 MaxSim ({v.description}) · 집계 "
                                f"{menu_scorers.AGGS[agg][0]}")

    def prepare(self, ctx: ScoreContext) -> None:
        similarity(ctx, self.variant)

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        # beta=0 — `visit` 해석에는 dislike 가 없다.
        return menu_scorers.content_score(similarity(ctx, self.variant), ctx.pool.pos,
                                          history, self.agg, 0.0)


class ReviewSingleVectorScorer(Scorer):
    """대조군 — 가게 = 리뷰 평균 벡터 1개, 사용자 = 이력 가게 평균 1개, 코사인."""

    def __init__(self, variant: str, name: str) -> None:
        self.variant, self.name = variant, name
        v = review_content.VARIANTS[variant]
        self.description = f"대조군: 단일 벡터 코사인 (리뷰 {v.k}개 평균, {v.description})"

    def _vecs(self, ctx: ScoreContext) -> np.ndarray:
        box = ctx.extras.setdefault("reviews", {})
        cache = box.setdefault("mean_vec", {})
        if self.variant not in cache:
            cache[self.variant] = review_content.store_mean_vectors(
                review_space(ctx), review_content.VARIANTS[self.variant])
        return cache[self.variant]

    def prepare(self, ctx: ScoreContext) -> None:
        self._vecs(ctx)

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        V = self._vecs(ctx)
        pos = ctx.pool.pos
        idx = [pos[sid] for sid, like in history if like and sid in pos]
        idx = [i for i in idx if np.isfinite(V[i]).all()]
        if not idx:
            return np.zeros(V.shape[0])
        u = V[idx].astype(float).mean(axis=0)
        norm = float(np.linalg.norm(u))
        if norm == 0:
            return np.zeros(V.shape[0])
        return menu_scorers._neutral_fill(V.astype(float) @ (u / norm))


def _register_all() -> None:
    for agg in REVIEW_AGGS:                      # 개수 구조만 남긴 대조군
        name = f"review_null_{agg}"
        register(name)(lambda a=agg, n=name: ReviewMaxSimScorer("null", a, n))
    for variant in review_content.VARIANTS:
        for agg in REVIEW_AGGS:
            name = f"review_{variant}_{agg}"
            register(name)(lambda v=variant, a=agg, n=name:
                           ReviewMaxSimScorer(v, a, n))
        single = f"review_{variant}_single"
        register(single)(lambda v=variant, n=single: ReviewSingleVectorScorer(v, n))


_register_all()
