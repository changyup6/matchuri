# -*- coding: utf-8 -*-
"""협업 필터 베이스라인 — item-item 공방문 코사인 (PLAN 5절 'Step 1.5' 결정 2번).

리뷰 33,300건은 그 자체로 **사용자 × 가게 행렬**이다. 콘텐츠(메뉴·리뷰·사진)를 하나도
쓰지 않고 "이 가게에 간 사람들은 저 가게에도 갔다" 만으로 다음 방문을 얼마나 맞히는지
재는 것이 이 모듈이다. 콘텐츠가 이겨야 할 진짜 상대이자 '다음 방문이 얼마나 예측
가능한가' 의 상한 참고치다.

    sim(h, i) = |U_h ∩ U_i| / sqrt(|U_h| · |U_i|)

U_x 는 가게 x 에 리뷰를 남긴 **저자 집합**이다.

**누수 규칙(깨끗한 쪽).** 평가 사용자 227명의 리뷰는 **전부** 뺀다 — 이력 구간뿐
아니라 미래 구간도, 후보 풀 밖 가게의 리뷰도 전부다. 평가 사용자 본인의 미래 방문이
공방문 통계에 한 톨이라도 남으면 그 사용자의 정답을 정답으로 맞히게 된다.
`pool.attach_crawled_quality` 가 쓰는 제외 집합과 같은 집합이다.

**U 에 누구를 넣는가.** 분모의 |U_i| 는 후보 풀 안에 리뷰가 하나라도 있는 비평가 저자
**전부**를 센다(표준 코사인). 풀 리뷰가 1건뿐인 저자는 교집합에 기여할 수 없으므로
비영(非零) 쌍을 만드는 것은 풀 리뷰 2건 이상인 저자뿐이고, 그 수를 결과에 적는다.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass

import numpy as np

from .data import Dataset
from .pool import Pool


@dataclass
class CoVisit:
    """가게-가게 공방문 코사인과, 그것을 만든 재료의 크기."""
    sim: np.ndarray                  # (S, S) float32, 대칭. 대각선은 1(또는 0).
    item_users: np.ndarray           # (S,) 그 가게에 리뷰를 남긴 비평가 저자 수
    n_authors: int                   # 풀 리뷰 1+ 인 비평가 저자 수 (= |U| 의 모집단)
    n_authors_multi: int             # 그 중 풀 리뷰 **2곳 이상** — 공방문을 만드는 저자
    n_pairs_nonzero: int             # sim > 0 인 (i<j) 쌍의 수
    n_excluded_authors: int          # 통째로 뺀 평가 사용자 수
    n_excluded_reviews: int          # 그래서 버린 리뷰 건수
    n_items_empty: int               # 저자가 한 명도 없는 후보 풀 가게 수

    @property
    def n_pairs_total(self) -> int:
        s = self.sim.shape[0]
        return s * (s - 1) // 2

    @property
    def density(self) -> float:
        return self.n_pairs_nonzero / max(self.n_pairs_total, 1)


def user_store_sets(ds: Dataset, pool: Pool,
                    excluded_authors: set[str]) -> dict[str, set[int]]:
    """저자 -> 그 저자가 리뷰한 **후보 풀 가게의 인덱스 집합**. 제외 저자는 아예 없다.

    같은 저자가 같은 가게에 여러 번 리뷰해도 한 번으로 센다(집합) — 공방문은
    '갔다/안 갔다' 의 문제이지 몇 번 갔나의 문제가 아니다.
    """
    out: dict[str, set[int]] = collections.defaultdict(set)
    for r in ds.reviews:
        if r.author_id in excluded_authors:
            continue
        i = pool.pos.get(r.store_id)
        if i is not None:
            out[r.author_id].add(i)
    return dict(out)


def build(ds: Dataset, pool: Pool, excluded_authors: set[str]) -> CoVisit:
    """공방문 코사인을 만든다. 평가 사용자(`excluded_authors`)는 전부 빠진다."""
    n = len(pool)
    sets = user_store_sets(ds, pool, excluded_authors)

    item_users = np.zeros(n, dtype=np.float64)
    co = np.zeros((n, n), dtype=np.float32)
    n_multi = 0
    for stores in sets.values():
        idx = np.fromiter(sorted(stores), dtype=np.int64, count=len(stores))
        item_users[idx] += 1.0
        if idx.size >= 2:
            n_multi += 1
            # 이 저자가 만든 모든 (i, j) 쌍에 1 을 더한다. 저자당 풀 가게가 최대
            # 9곳이라(평가 사용자 = 10곳 이상) 쌍은 많아야 36개다 — 밀집 행렬로 충분하다.
            co[np.ix_(idx, idx)] += 1.0

    denom = np.sqrt(np.outer(item_users, item_users))
    with np.errstate(invalid="ignore", divide="ignore"):
        sim = np.where(denom > 0, co / np.maximum(denom, 1e-12), 0.0)
    sim = sim.astype(np.float32)
    np.fill_diagonal(sim, np.where(item_users > 0, 1.0, 0.0).astype(np.float32))

    off = np.triu(sim, k=1)
    n_excluded_reviews = sum(1 for r in ds.reviews if r.author_id in excluded_authors)
    return CoVisit(sim=sim, item_users=item_users,
                   n_authors=len(sets), n_authors_multi=n_multi,
                   n_pairs_nonzero=int((off > 0).sum()),
                   n_excluded_authors=len(excluded_authors),
                   n_excluded_reviews=n_excluded_reviews,
                   n_items_empty=int((item_users == 0).sum()))
