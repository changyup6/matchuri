# -*- coding: utf-8 -*-
"""가게 요약문 scorer (Step 2) — `text_{max,mean,lse01}`.

    score(i) = agg_{h ∈ 이력} cos(v_h, v_i)

`v` 는 `ai_title + ai_summary + tags + review_tags` 를 이어 붙인 문장 하나의 임베딩이다
(가게당 벡터 1개, `analysis/content/store_text.py`). 메뉴·리뷰와 달리 집합이 아니라서
MaxSim 이 없고, 남는 자유도는 **이력 위의 집계**뿐이다.

집계·NaN·하이브리드는 전부 메뉴 쪽(`scorers_menu`)의 코드를 그대로 쓴다. 규칙을 두 벌
만들면 언젠가 어긋나고, 그러면 modality 사이의 비교가 성립하지 않는다.
  * 이력 가게의 요약문이 없으면(NaN) 그 가게를 집계에서 뺀다.
  * 후보의 요약문이 없으면 그 사용자의 평균 콘텐츠 점수(중립)로 채운다.
  * `hybrid_pop_text_*_l<λ>` 는 `scorers_menu._resolve_hybrid` 가 이름 규칙으로 만든다.
"""
from __future__ import annotations

import numpy as np

from analysis.content import store_text as text_content

from . import scorers_menu as menu_scorers
from .scorers import History, ScoreContext, Scorer, register

# 메뉴 쪽 함수는 **모듈째** 들고 와서 호출 시점에 꺼내 쓴다. `scorers_menu` 를 먼저
# import 하면 이 모듈이 그 초기화 도중에 불려 오므로(scorers.py 맨 아래), import 시점에
# 속성을 꺼내면 순환 import 로 깨진다.

#: 요약문 modality 가 쓰는 집계. 메뉴와 같은 이름·같은 구현이다.
TEXT_AGGS = ("max", "mean", "lse01")


def text_space(ctx: ScoreContext) -> text_content.StoreTextSpace:
    """풀 전체의 요약문 공간. 실험 한 번에 한 개만 만든다."""
    box = ctx.extras.setdefault("store_text", {})
    if "space" not in box:
        box["space"] = text_content.StoreTextSpace.build(ctx.pool.ids)
    return box["space"]


def similarity(ctx: ScoreContext) -> np.ndarray:
    """가게-가게 코사인 (캐시 → 없으면 계산 후 캐시)."""
    box = ctx.extras.setdefault("store_text", {})
    if "sim" not in box:
        mat, _ = text_content.load_or_build_similarity(text_space(ctx))
        box["sim"] = mat
    return box["sim"]


class StoreTextScorer(Scorer):
    """요약문 코사인을 이력 위에서 접는다."""

    def __init__(self, agg: str, name: str) -> None:
        self.agg, self.name = agg, name
        self.description = (f"가게 요약문(ai_title+ai_summary+tags) 코사인 · 집계 "
                            f"{menu_scorers.AGGS[agg][0]}")

    def prepare(self, ctx: ScoreContext) -> None:
        similarity(ctx)

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        # beta=0 — 이력 해석 `visit` 에는 dislike 가 없다(PLAN 5절 Step 1.5 결정 1번).
        return menu_scorers.content_score(similarity(ctx), ctx.pool.pos, history,
                                          self.agg, 0.0)


def _register_all() -> None:
    for agg in TEXT_AGGS:
        name = f"text_{agg}"
        register(name)(lambda a=agg, n=name: StoreTextScorer(a, n))


_register_all()
