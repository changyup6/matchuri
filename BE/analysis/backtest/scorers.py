# -*- coding: utf-8 -*-
"""scorer 레지스트리와 인터페이스.

**이 인터페이스가 하네스와 모델 사이의 유일한 접점이다.** 나중에 붙일 메뉴 MaxSim·
리뷰·사진·하이브리드도 여기에만 등록하면 되고 분할·지표·bootstrap 은 손대지 않는다.

    history : [(store_id, like)] — 미래 직전의 최근 N 건을 시간순으로. 제품이 추천
              시점에 아는 것은 이것뿐이다(리뷰 원문은 입력이 아니다).
              `ctx.history_mode == "visit"` 이면 **전부 like** 로 들어온다
              (방문 = 관심, PLAN 5절 Step 1.5 결정 1번). `current` 처럼 like/dislike
              를 나눠 받는 모델은 방문을 전부 like 로 먹는 셈이 된다.
    ctx     : 후보 풀 id·사전 계산 통계·seed. 무거운 것(유사도 행렬 등)은
              `ctx.extras` 에 담아 실험 전체에서 한 번만 만든다.
    반환    : 후보 풀 길이의 점수 벡터. 클수록 추천. 이미 간 가게를 빼는 것은
              하네스가 한다 — scorer 는 풀 전체에 점수를 매기면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import config
from .current_model import CurrentModel
from .pool import Pool
from .rng import generator

History = list[tuple[str, bool]]


@dataclass(frozen=True)
class ScoreContext:
    pool: Pool
    seed: int = config.SEED
    extras: dict = field(default_factory=dict)   # 나중 단계의 사전 계산물 자리
    #: 이력 해석 — `visit`(방문=관심) 또는 `like_dislike`. `evaluate` 가 이 값으로
    #: `UserSplit.signals` 를 부른다. 기본은 **옛 해석**이라 이 값을 넘기지 않는
    #: 코드(Step 0·1)는 동작이 바뀌지 않는다. 실행기가 `--history` 로 정한다.
    history_mode: str = "like_dislike"


class Scorer:
    """모델이 상속하는 최소 인터페이스."""

    name: str = "?"
    #: 사람이 읽을 한 줄 설명. 결과 .md 에 그대로 들어간다.
    description: str = ""

    def prepare(self, ctx: ScoreContext) -> None:
        """실험 시작에 한 번. 무거운 사전 계산은 여기서."""

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        raise NotImplementedError


REGISTRY: dict[str, Callable[[], Scorer]] = {}

#: 이름 규칙으로 모델을 만드는 함수들. 레지스트리에 없는 이름이 오면 순서대로 물어본다.
#: 하이브리드처럼 (콘텐츠 모델 × λ 격자) 로 이름이 수백 개가 되는 것을 위한 자리다.
RESOLVERS: list[Callable[[str], Callable[[], Scorer] | None]] = []


def register(name: str):
    def deco(factory: Callable[[], Scorer]):
        if name in REGISTRY:
            raise ValueError(f"scorer 이름 중복: {name}")
        REGISTRY[name] = factory
        return factory
    return deco


def register_resolver(fn) -> None:
    RESOLVERS.append(fn)


def factory(name: str) -> Callable[[], Scorer] | None:
    if name in REGISTRY:
        return REGISTRY[name]
    for resolve in RESOLVERS:
        made = resolve(name)
        if made is not None:
            return made
    return None


def exists(name: str) -> bool:
    return factory(name) is not None


def get(name: str) -> Scorer:
    made = factory(name)
    if made is None:
        raise KeyError(f"모르는 모델 '{name}'. 등록된 것: {', '.join(sorted(REGISTRY))}")
    return made()


def available() -> list[str]:
    return sorted(REGISTRY)


# ────────────────────────────────────────────────── 베이스라인 4종
@register("random")
class RandomScorer(Scorer):
    name = "random"
    description = "무작위 (바닥)"

    def score(self, history, ctx, *, user_id):
        # 사용자마다 결정적: 같은 seed 면 몇 번을 돌려도 같은 순위가 나온다.
        return generator(ctx.seed, "random", user_id).random(len(ctx.pool))


@register("popularity")
class PopularityScorer(Scorer):
    name = "popularity"
    description = "인기순 log1p(카카오 리뷰 수)"

    def score(self, history, ctx, *, user_id):
        return ctx.pool.log_review_count


@register("quality")
class QualityScorer(Scorer):
    name = "quality"
    description = f"베이지안 평균 품질 (카카오 집계, C={config.BAYES_C:g}) — 기준 베이스라인"

    def score(self, history, ctx, *, user_id):
        return ctx.pool.bayes


@register("quality_crawled")
class QualityCrawledScorer(Scorer):
    name = "quality_crawled"
    description = "베이지안 평균 품질 (크롤링 리뷰만, 평가 사용자 제외) — 누수 대조군"

    def score(self, history, ctx, *, user_id):
        return ctx.pool.crawled_bayes


@register("quality_crawled_all")
class QualityCrawledAllScorer(Scorer):
    """진단용. `quality_crawled` 와 같은데 평가 사용자 리뷰를 빼지 않는다.

    두 행의 차이가 '평가 사용자 본인의 별점이 품질 점수에 남긴 흔적' 의 크기다.
    카카오 집계(`quality`)의 누수는 원본을 되돌릴 수 없어 직접 못 재므로 이걸로 가늠한다.
    """

    name = "quality_crawled_all"
    description = ("(진단용) 크롤링 리뷰 전부 — 평가 사용자 **미제외**. "
                   "`quality_crawled` 와의 차이 = 크롤링 코퍼스 안에서의 누수 크기")

    def score(self, history, ctx, *, user_id):
        return ctx.pool.quality_variants["crawled_all"]


@register("current")
class CurrentScorer(Scorer):
    """현재 서버 모델(`server/recsys/swipe.py`)의 오프라인 재현.

    이력을 like/dislike 로 순서대로 먹여 같은 온라인 로지스틱(라플라스) 갱신을 돌리고,
    사후 평균으로 점수를 낸다 — Thompson 난수는 쓰지 않는다(`swipe.peek` 과 같다).
    갱신 수식이 서버 함수와 같음은 `tests/test_current_model.py` 가 증명한다.
    """

    name = "current"
    description = "현재 서버 모델 (업종·가격 로지스틱 + 품질 백본, 사후 평균)"

    def score(self, history, ctx, *, user_id):
        model = CurrentModel(ctx.pool)
        model.replay(history)
        return model.scores()


# 메뉴 다중 벡터 scorer(Step 1), 협업 필터 scorer(Step 1.5), 가게 요약문·리뷰 다중 벡터
# scorer(Step 2)는 여기서 등록된다. 맨 아래에 두는 것은 순환 import 를 피하기 위해서다 —
# 이 모듈들이 위의 register 를 쓴다.
from . import scorers_menu as _scorers_menu     # noqa: E402,F401  (등록 부작용)
from . import scorers_cf as _scorers_cf         # noqa: E402,F401  (등록 부작용)
from . import scorers_text as _scorers_text     # noqa: E402,F401  (등록 부작용)
from . import scorers_reviews as _scorers_rev   # noqa: E402,F401  (등록 부작용)
