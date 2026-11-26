# -*- coding: utf-8 -*-
"""후보 풀 — 서버 `swipe._build_pool` 과 같은 규칙, 같은 순서.

카드 자격은 **리뷰 1+ · 사진 1+ · 대표 사진이 음식 · 카드 3칸에 가격이 보임**.
실제 수는 하네스가 계산해 결과에 적는다.

뒤의 두 조건은 2026-09-11 에 더했다. 앞의 둘만 걸면 2,294곳인데, 그중 9%는
대표 사진이 간판·인테리어·메뉴판 글자였고 18%는 카드에 가격이 한 줄도 안
보였다. **수를 줄여 질을 올리는 쪽이 남는 장사다** — 1,629곳이면 한 세션
30장 기준 54바퀴다. 조건은 `config.FOOD_HERO_ONLY` · `MENU_PRICE_REQUIRED`.

**이 파일의 규칙은 서버 `recsys/swipe.py` 와 같아야 한다.** `build_serving`
이 두 경로를 대조하고 다르면 멈춘다.

품질 통계는 두 벌을 만든다.
  - `bayes`/`qz`  : 카카오 집계(review_avg·review_count) 기반. 서버가 쓰는 것이고
                    주 베이스라인 `quality` 가 이걸 쓴다. 평가 사용자의 리뷰가 집계에
                    섞여 있지만 가게당 최대 1건이라 영향이 미미하다.
  - `crawled_*`   : 크롤링한 리뷰만으로 다시 계산하되 **평가 사용자 리뷰를 전부 뺀다.**
                    누수 없는 대조군(`quality_crawled`).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import numpy as np

from . import config
from .data import Dataset
from .metrics import popularity_deciles

# 서버 swipe.py 와 같은 상수여야 한다(테스트로 고정).
CATS = ["한식", "카페", "술집", "일식", "양식", "중식",
        "간식", "분식", "치킨", "샐러드"]
PRICE_EDGES = [8000, 13000, 21000]
PRICE_NAMES = ["~8천", "8천~1.3만", "1.3~2.1만", "2.1만+", "가격미상"]
FEATURE_DIM = 3 + len(CATS) + 1 + 5      # 절편·품질z·리뷰수 + 업종 11 + 가격 5 = 19


def price_bucket(prices: list[int]) -> int:
    """서버 `swipe._price_bucket`: 메뉴 중앙가의 4분위. 가격이 없으면 4(미상)."""
    if not prices:
        return 4
    med = statistics.median(prices)
    for i, e in enumerate(PRICE_EDGES):
        if med < e:
            return i
    return 3


@dataclass
class Pool:
    """후보 풀과 가게별 사전 계산값. scorer 가 받는 컨텍스트의 알맹이."""
    ids: list[str]
    pos: dict[str, int]
    review_count: np.ndarray             # 카카오 집계 리뷰 수
    review_avg: np.ndarray               # 카카오 집계 평점 (없으면 nan)
    bayes: np.ndarray                    # 베이지안 평균 (BAYES_C=10)
    qz: np.ndarray                       # bayes 의 풀 내 z 점수 — 서버 점수의 백본
    cat2: list[str | None]
    price_bucket: np.ndarray
    X: np.ndarray                        # current 모델 특성 (N x 19)
    log_review_count: np.ndarray         # popularity 베이스라인
    #: `popularity` 가 쓰는 변수(log1p 리뷰 수)의 십분위. `auc_pm` 의 매칭 기준.
    pop_decile: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=int))
    crawled_count: np.ndarray = field(default_factory=lambda: np.zeros(0))
    crawled_avg: np.ndarray = field(default_factory=lambda: np.zeros(0))
    crawled_bayes: np.ndarray = field(default_factory=lambda: np.zeros(0))
    crawled_qz: np.ndarray = field(default_factory=lambda: np.zeros(0))
    #: 이름 -> 베이지안 품질 벡터. 누수 조건이 다른 변종을 나란히 두고 비교한다.
    quality_variants: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.ids)


def _bayesian(avg: np.ndarray, cnt: np.ndarray, mu: float) -> np.ndarray:
    """서버와 같은 식: (C*mu + avg*cnt) / (C + cnt). 평점이 없으면 avg 자리에 mu."""
    a = np.where(np.isnan(avg), mu, avg)
    return (config.BAYES_C * mu + a * cnt) / (config.BAYES_C + cnt)


def _zscore(v: np.ndarray) -> np.ndarray:
    return (v - v.mean()) / (v.std() or 1.0)


def build_pool(ds: Dataset) -> Pool:
    """카드 자격을 만족하는 가게만, stores.jsonl 등장 순서 그대로."""
    # 서버 `swipe._eligible` 과 **같은 순서로 같은 조건**을 건다. 하나라도
    # 어긋나면 `build_serving` 이 빌드를 거부한다 (그게 이 중복의 목적이다).
    ids = [sid for sid in ds.store_order
           if (ds.stores[sid].get("kakao_review_count") or 0) >= config.MIN_POOL_REVIEWS
           and ds.photo_count.get(sid, 0) >= config.MIN_POOL_PHOTOS
           and (not config.FOOD_HERO_ONLY or ds.hero_food.get(sid, False))
           and (not config.MENU_PRICE_REQUIRED or ds.card_prices.get(sid))]

    cnt = np.array([float(ds.stores[s].get("kakao_review_count") or 0) for s in ids])
    avg = np.array([ds.stores[s].get("kakao_avg_score")
                    if ds.stores[s].get("kakao_avg_score") is not None else np.nan
                    for s in ids], dtype=float)

    # 서버 _build_pool 의 mu: 평점 결측을 0 으로 두고 리뷰 수로 가중 평균한다.
    mu = float(np.nansum(np.nan_to_num(avg, nan=0.0) * cnt) / max(cnt.sum(), 1.0))
    bayes = _bayesian(avg, cnt, mu)
    qz = _zscore(bayes)

    cat2 = [ds.stores[s].get("cat2") for s in ids]
    pb = np.array([price_bucket(ds.menu_prices.get(s, [])) for s in ids], dtype=int)

    X = np.zeros((len(ids), FEATURE_DIM))
    X[:, 0] = 1.0                                    # 절편
    X[:, 1] = qz                                     # 품질 z
    X[:, 2] = np.log1p(cnt) / 6.0                    # 리뷰 수
    for i, c in enumerate(cat2):
        X[i, 3 + (CATS.index(c) if c in CATS else len(CATS))] = 1.0
        X[i, 3 + len(CATS) + 1 + int(pb[i])] = 1.0

    log_cnt = np.log1p(cnt)
    return Pool(ids=ids, pos={s: i for i, s in enumerate(ids)},
                review_count=cnt, review_avg=avg, bayes=bayes, qz=qz,
                cat2=cat2, price_bucket=pb, X=X, log_review_count=log_cnt,
                pop_decile=popularity_deciles(log_cnt))


def crawled_quality(pool: Pool, ds: Dataset,
                    excluded_authors: set[str]) -> tuple[np.ndarray, ...]:
    """크롤링 리뷰만으로 계산한 (건수, 평균, 베이지안, z). 지정한 저자는 뺀다.

    리뷰가 하나도 남지 않은 가게는 전체 평균 mu 로 수렴한다(베이지안 평균의 자연스러운
    극한: cnt=0 이면 결과가 mu).
    """
    n = len(pool)
    cnt = np.zeros(n)
    total = np.zeros(n)
    for r in ds.reviews:
        i = pool.pos.get(r.store_id)
        if i is None or r.author_id in excluded_authors:
            continue
        cnt[i] += 1
        total[i] += r.rating
    with np.errstate(invalid="ignore", divide="ignore"):
        avg = np.where(cnt > 0, total / np.maximum(cnt, 1), np.nan)
    mu = float(total.sum() / max(cnt.sum(), 1.0))
    bayes = _bayesian(avg, cnt, mu)
    return cnt, avg, bayes, _zscore(bayes)


def attach_crawled_quality(pool: Pool, ds: Dataset, excluded_authors: set[str]) -> Pool:
    """누수 규칙(PLAN 1절)의 '보조 품질' 두 벌을 붙인다.

      `crawled`     평가 사용자 리뷰를 **전부 뺀 것** — 누수 없는 대조군.
      `crawled_all` 아무도 빼지 않은 것 — 진단용. 두 값의 차이가 곧 '평가 사용자의
                    리뷰가 품질 점수에 남긴 흔적' 의 크기다. 카카오 집계(`quality`)
                    에 섞인 누수는 직접 뜯어낼 수 없으므로 이 차이로 대신 가늠한다.
    """
    cnt, avg, bayes, qz = crawled_quality(pool, ds, excluded_authors)
    pool.crawled_count, pool.crawled_avg = cnt, avg
    pool.crawled_bayes, pool.crawled_qz = bayes, qz
    pool.quality_variants["crawled"] = bayes
    pool.quality_variants["crawled_all"] = crawled_quality(pool, ds, set())[2]
    return pool
