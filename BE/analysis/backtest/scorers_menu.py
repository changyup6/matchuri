# -*- coding: utf-8 -*-
"""메뉴 다중 벡터 scorer (Step 1).

사용자 점수는 이력의 like 가게 h 에 대한 **집계**다.

    score(i) = agg_{h ∈ like} sim(h, i)  −  β · agg_{h ∈ dislike} sim(h, i)

  * `agg ∈ {mean, max, logsumexp(τ)}`, τ ∈ {0.05, 0.1, 0.2} — "평균이 다봉형 취향을
    뭉갠다"는 PLAN 의 가설을 직접 재기 위해 셋을 전부 등록한다. logsumexp 는
    `τ·log(Σ exp(s/τ)/H)` 로 쓴다(H 로 나눠서) — τ→0 이면 max, τ→∞ 면 mean 이 되고
    이력 길이가 달라도 눈금이 같다.
  * dislike 는 like 와 섞지 않고 **따로 접어서 뺀다.** 낮은 별점은 '이 음식이 싫다'
    가 아니라 '그날 그 집이 못했다' 일 수 있어(Step 0 진단) 같은 축에 더하면
    like 신호까지 흐려진다. β ∈ {0, 0.5, 1} 이 곧 dislike 가중 {0, −0.5, −1} 이다.
  * like 안에서의 가중 w_h 는 전부 1 이다. 제품이 아는 것은 like/dislike 뿐이라
    like 끼리 차등을 둘 근거가 아직 없다(별점 크기는 입력이 아니다).

**NaN(메뉴 없는 가게).** 이력 쪽이 NaN 이면 그 가게를 집계에서 뺀다. 후보 쪽이 NaN
이면 그 사용자의 **평균 콘텐츠 점수**로 채운다 — 중립. 0 으로 채우면 메뉴가 없다는
이유만으로 순위 바닥에 깔리는데, 그건 '싫다' 가 아니라 '모른다' 이다.

**브랜드 중복 억제**(Step 1.5, PLAN 5절 결정 4번). 같은 브랜드의 다른 지점은 메뉴가
같아 유사도가 1.0 이 되고 추천 상위를 점거한다. 그건 취향을 맞춘 것이 아니라 **근접
중복**이다. `brand_suppress=True` 면 이력 가게 h 와 **같은 브랜드**인 후보 i 의
`sim(h, i)` 를 그 사용자의 평균 콘텐츠 점수(중립)로 바꾼다 — 0 으로 만들면 '싫다' 가
되므로 NaN 후보를 다루는 것과 같은 중립값을 쓴다. 다른 이력 가게와의 유사도는 그대로
남으므로, 브랜드가 아닌 이유로 닮은 후보는 계속 위로 올라온다.

**하이브리드.** `popularity_z + λ·content_z`, z 는 그 사용자의 후보(이미 간 가게를 뺀
나머지) 안에서 낸다. λ 는 `cv.select_hyperparam` 의 사용자 5-fold 로 고른다.

**난수 대조군 `menu_null_<집계>`** (Step 3a, PLAN 7절 결정 1번). 가게별 메뉴 개수는
그대로 두고 벡터만 난수 단위벡터로 바꾼 유사도다. MaxSim 은 집합이 클수록 커지므로
'메뉴가 많은 가게' 라는 구조만으로도 점수가 난다 — 진짜 벡터가 이 대조군을 넘지
못하면 메뉴 MaxSim 의 성능은 내용의 증거가 아니다. 리뷰에서는 실제로 넘지 못했다.
"""
from __future__ import annotations

import re

import numpy as np

from analysis.content import brands as brand_norm
from analysis.content import menus as menu_content

from .scorers import History, ScoreContext, Scorer, register, register_resolver

#: 집계 이름 -> (설명, τ). τ 가 None 이면 mean/max.
AGGS: dict[str, tuple[str, float | None]] = {
    "mean": ("평균", None),
    "max": ("최댓값", None),
    "lse005": ("logsumexp τ=0.05", 0.05),
    "lse01": ("logsumexp τ=0.1", 0.1),
    "lse02": ("logsumexp τ=0.2", 0.2),
}

#: 접미사 -> β (dislike 벌점 계수). 접미사가 없으면 β=0.
DISLIKE_BETA: dict[str, float] = {"": 0.0, "_d05": 0.5, "_d10": 1.0}

LAMBDAS = (0.25, 0.5, 1.0, 2.0, 4.0)

#: 난수 대조군이 개수 구조를 빌려 오는 변종 — 결선에 오른 `menu_top8_max` 와 같은
#: 집합이다(같은 K, 같은 무가중). 대조군은 개수만 남기고 벡터를 바꾸는 것이므로
#: 비교 대상과 개수 구조가 **정확히 같아야** 뜻이 있다.
NULL_VARIANT = "top8"


# ────────────────────────────────────────────────── 공용 계산
def menu_space(ctx: ScoreContext) -> menu_content.MenuSpace:
    """풀 전체의 메뉴 공간. 실험 한 번에 한 개만 만든다."""
    box = ctx.extras.setdefault("menu", {})
    if "space" not in box:
        box["space"] = menu_content.MenuSpace.build(ctx.pool.ids, seed=ctx.seed)
    return box["space"]


def similarity(ctx: ScoreContext, variant_name: str) -> np.ndarray:
    """변종의 가게-가게 유사도 (캐시 → 없으면 계산 후 캐시).

    `variant_name == "null"` 이면 개수 구조만 남기고 벡터를 난수로 바꾼 대조군
    행렬이다(PLAN 7절 결정 1번 — 집합형 modality 의 의무 대조군).
    """
    box = ctx.extras.setdefault("menu", {})
    sims = box.setdefault("sim", {})
    if variant_name not in sims:
        null = variant_name == "null"
        variant = menu_content.VARIANTS[NULL_VARIANT if null else variant_name]
        mat, _ = menu_content.load_or_build_similarity(menu_space(ctx), variant,
                                                       null=null)
        sims[variant_name] = mat
    return sims[variant_name]


def aggregate(rows: np.ndarray, agg: str) -> np.ndarray:
    """(H, S) 유사도 -> (S,) 사용자 점수."""
    if rows.shape[0] == 0:
        return np.zeros(rows.shape[1], dtype=float)
    if agg == "mean":
        return rows.mean(axis=0)
    if agg == "max":
        return rows.max(axis=0)
    tau = AGGS[agg][1]
    z = rows / tau
    hi = z.max(axis=0)
    # log-sum-exp 안정화 + 이력 길이 H 로 나눠 눈금을 맞춘다.
    return tau * (hi + np.log(np.exp(z - hi).sum(axis=0) / rows.shape[0]))


def _neutral_fill(score: np.ndarray) -> np.ndarray:
    """NaN(메뉴 없는 후보)을 유한한 후보들의 평균으로 채운다."""
    ok = np.isfinite(score)
    if not ok.any():
        return np.zeros_like(score)
    return np.where(ok, score, float(score[ok].mean()))


def brand_index(ctx: ScoreContext) -> np.ndarray:
    """후보 풀 가게 -> 브랜드 번호. 실행기가 넣어준 가게 이름으로 한 번만 만든다."""
    box = ctx.extras.setdefault("brand", {})
    if "ids" not in box:
        names = ctx.extras.get("store_names")
        if not names:
            raise KeyError(
                "brand_suppress 에는 `ctx.extras['store_names']`(가게 id -> 이름)"
                "가 필요하다 — 이름이 없으면 브랜드를 알 수 없다")
        box["ids"] = np.asarray(
            brand_norm.brand_ids([names.get(sid, "") for sid in ctx.pool.ids]),
            dtype=np.int64)
    return box["ids"]


def suppress_same_brand(rows: np.ndarray, hist_idx: list[int],
                        brand: np.ndarray) -> np.ndarray:
    """이력 가게와 같은 브랜드인 후보의 그 행 값을 **중립값**으로 바꾼다.

    중립값은 이 사용자가 보는 유사도의 평균(유한한 값들의 평균)이다. 0 으로 두면
    '같은 브랜드라서 싫다' 는 뜻이 되고, 1.0 그대로 두면 근접 중복이 상위를 점거한다.
    바꾸는 것은 **그 이력 가게와의 유사도 한 칸**뿐이다 — 다른 이력 가게와의 유사도는
    그대로라, 브랜드가 아닌 이유로 닮았다면 후보는 여전히 위로 올라온다.
    """
    if rows.shape[0] == 0:
        return rows
    finite = np.isfinite(rows)
    neutral = float(rows[finite].mean()) if finite.any() else 0.0
    out = rows.copy()
    for r, h in enumerate(hist_idx):
        same = brand == brand[h]
        out[r] = np.where(same & finite[r], neutral, out[r])
    return out


def content_score(sim: np.ndarray, pool_pos: dict, history: History,
                  agg: str, beta: float, *,
                  brand: np.ndarray | None = None) -> np.ndarray:
    """like 집계 − β·dislike 집계. 메뉴 없는 후보는 중립값으로 채운다."""
    n = sim.shape[0]

    def rows_for(want_like: bool) -> tuple[np.ndarray, list[int]]:
        idx = [pool_pos[sid] for sid, like in history
               if like is want_like and sid in pool_pos]
        idx = [i for i in idx if np.isfinite(sim[i]).any()]   # 메뉴 없는 이력은 뺀다
        rows = sim[idx].astype(float) if idx else np.zeros((0, n))
        if brand is not None:
            rows = suppress_same_brand(rows, idx, brand)
        return rows, idx

    like_rows, _ = rows_for(True)
    if like_rows.shape[0] == 0:
        return np.zeros(n)                       # like 가 없으면 전부 같은 점수(중립)
    score = _neutral_fill(aggregate(like_rows, agg))
    if beta:
        dislike_rows, _ = rows_for(False)
        if dislike_rows.shape[0]:
            score = score - beta * _neutral_fill(aggregate(dislike_rows, agg))
    return score


def zscore_on(v: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """후보 마스크 안의 평균·표준편차로 z 정규화한다(마스크 밖 값도 같은 눈금)."""
    sub = v[mask]
    sd = float(sub.std())
    return (v - float(sub.mean())) / (sd if sd > 0 else 1.0)


def candidate_mask(ctx: ScoreContext, user_id: str, n: int) -> np.ndarray:
    """그 사용자의 후보(아직 안 가본 가게). 하네스가 넣어주지 않으면 풀 전체."""
    m = ctx.extras.get("candidates", {}).get(user_id)
    return np.ones(n, dtype=bool) if m is None else np.asarray(m, dtype=bool)


# ────────────────────────────────────────────────── 콘텐츠 단독
class MenuMaxSimScorer(Scorer):
    """메뉴 MaxSim 유사도를 이력 위에서 접는다."""

    def __init__(self, variant: str, agg: str, beta: float, name: str, *,
                 brand_suppress: bool = False) -> None:
        self.variant, self.agg, self.beta, self.name = variant, agg, beta, name
        self.brand_suppress = brand_suppress
        tail = f" · dislike −{beta:g}" if beta else ""
        bs = " · 브랜드 중복 억제" if brand_suppress else ""
        if variant == "null":
            v = menu_content.VARIANTS[NULL_VARIANT]
            self.description = (f"**대조군** 메뉴 MaxSim — 개수 구조({v.description})는 "
                                f"그대로, 벡터만 난수 · 집계 {AGGS[agg][0]}{tail}{bs}")
        else:
            v = menu_content.VARIANTS[variant]
            self.description = (f"메뉴 MaxSim ({v.description}) · 집계 {AGGS[agg][0]}"
                                f"{tail}{bs}")

    def prepare(self, ctx: ScoreContext) -> None:
        similarity(ctx, self.variant)
        if self.brand_suppress:
            brand_index(ctx)

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        brand = brand_index(ctx) if self.brand_suppress else None
        return content_score(similarity(ctx, self.variant), ctx.pool.pos,
                             history, self.agg, self.beta, brand=brand)


class MenuSingleVectorScorer(Scorer):
    """대조군 — 가게=메뉴 평균 벡터 1개, 사용자=like 가게 평균 1개, 코사인.

    '평균이 다봉형 취향을 뭉갠다' 가설의 대조군이자 이전 방식의 재현이다. 메뉴 집합을
    **전부** 평균낸 것과, 다중 벡터와 같은 상위 K 집합만 평균낸 것 두 벌을 둔다 —
    차이가 K 때문인지 mean-vs-MaxSim 때문인지 갈라 보기 위해서다.
    """

    def __init__(self, variant: str, name: str, label: str) -> None:
        self.variant, self.name = variant, name
        self.description = f"대조군: 단일 벡터 코사인 ({label})"

    def _vecs(self, ctx: ScoreContext) -> np.ndarray:
        box = ctx.extras.setdefault("menu", {})
        cache = box.setdefault("mean_vec", {})
        if self.variant not in cache:
            packed = menu_space(ctx).packed(menu_content.VARIANTS[self.variant])
            cache[self.variant] = menu_content.mean_vectors(packed)
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
        return _neutral_fill(V.astype(float) @ (u / norm))


class HybridPopScorer(Scorer):
    """`popularity_z + λ·content_z` — z 는 그 사용자의 후보 안에서."""

    def __init__(self, content: Scorer, lam: float, name: str) -> None:
        self.content, self.lam, self.name = content, lam, name
        self.description = f"popularity_z + {lam:g}·({content.name})_z"

    def prepare(self, ctx: ScoreContext) -> None:
        self.content.prepare(ctx)

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        pop = ctx.pool.log_review_count
        mask = candidate_mask(ctx, user_id, pop.size)
        c = self.content.score(history, ctx, user_id=user_id)
        return zscore_on(pop, mask) + self.lam * zscore_on(np.asarray(c, float), mask)


# ────────────────────────────────────────────────── 등록
#: 브랜드 중복 억제를 켠 변종의 이름 접미사.
BRAND_SUPPRESS_SUFFIX = "_bs"


def _register_all() -> None:
    for agg in AGGS:                             # 개수 구조만 남긴 난수 대조군
        name = f"menu_null_{agg}"
        register(name)(lambda a=agg, n=name: MenuMaxSimScorer("null", a, 0.0, n))
    for variant in menu_content.VARIANTS:
        for agg in AGGS:
            for suffix, beta in DISLIKE_BETA.items():
                name = f"menu_{variant}_{agg}{suffix}"
                register(name)(
                    lambda v=variant, a=agg, b=beta, n=name:
                    MenuMaxSimScorer(v, a, b, n))
                bs_name = name + BRAND_SUPPRESS_SUFFIX
                register(bs_name)(
                    lambda v=variant, a=agg, b=beta, n=bs_name:
                    MenuMaxSimScorer(v, a, b, n, brand_suppress=True))

    register("menu_single_vector")(
        lambda: MenuSingleVectorScorer("all_idf", "menu_single_vector",
                                       "메뉴 전체 IDF 가중 평균 — 이전 방식"))
    register("menu_single_vector_top8_idf")(
        lambda: MenuSingleVectorScorer("top8_idf", "menu_single_vector_top8_idf",
                                       "상위 8개 IDF 가중 평균 — top8_idf 와 같은 집합"))


_register_all()

# 단일 벡터 대조군만 쓰는 '메뉴 전체' 변종. MaxSim scorer 를 등록한 **뒤에** 넣는다 —
# 가게당 메뉴가 최대 267개라 이걸로 2,216² MaxSim 행렬을 만들면 비용이 터진다.
# 평균 벡터 한 개를 만드는 데만 쓴다.
menu_content.VARIANTS["all_idf"] = menu_content.Variant("all_idf", 10_000, True)

_HYBRID_RE = re.compile(r"^hybrid_pop_(?P<content>.+)_l(?P<lam>[0-9.]+)$")


def _resolve_hybrid(name: str):
    """`hybrid_pop_<콘텐츠모델>_l<λ>` 를 즉석에서 만든다.

    λ 격자 × 콘텐츠 모델 46종을 전부 레지스트리에 박으면 이름이 230개가 되어 목록이
    읽히지 않는다. 이름 규칙으로 푸는 편이 낫다.
    """
    from . import scorers as _s
    m = _HYBRID_RE.match(name)
    if not m:
        return None
    inner = m.group("content")
    if not _s.exists(inner):
        return None
    return lambda: HybridPopScorer(_s.get(inner), float(m.group("lam")), name)


register_resolver(_resolve_hybrid)
