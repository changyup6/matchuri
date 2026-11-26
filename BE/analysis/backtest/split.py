# -*- coding: utf-8 -*-
"""시간 분할 — PLAN 1절 '평가 사용자와 시간 분할'.

  사용자별로 후보 풀 안 리뷰를 date 오름차순 정렬 -> 총 T 건
  평가 사용자   T >= 10
  미래(정답) F = max(3, ceil(0.3*T)) 건, 가장 최근. **N 과 무관하게 고정.**
  이력 N       미래 직전의 최근 N 건 (중첩). 모자라면 있는 만큼.

이력의 해석은 **두 벌**이다 (`signals(n, mode)`). Step 1.5 의 계약 수정으로 과제 P 의
기본이 `visit`(방문 = 관심, 별점 무관, dislike 없음)이 됐고, Step 0·1 의 `like_dislike`
는 그대로 남겨 옛 결과를 재현할 수 있게 했다. 분할 자체(과거·미래·N 창)는 두 해석에서
**완전히 같다** — 바뀌는 것은 모델에 먹이는 (가게, like) 목록뿐이다.

재방문 처리:
  - 이력에서는 중복을 그대로 둔다(제품도 스와이프를 여러 번 받는다).
  - 미래는 가게 단위로 유일화하고(가장 이른 방문을 남긴다), 과거에 이미 간 가게는
    미래에서 뺀다. 제품이 한 번 준 카드를 다시 주지 않기 때문이다.

**계약 해석 한 가지.** '이력에 이미 있으면 미래에서 제외' 의 '이력' 을 N 창(최근 N 건)
으로 읽으면 정답 집합이 N 마다 달라져 N 간 비교가 성립하지 않는다. 그래서 여기서는
**과거 전체(T-F 건)** 로 읽는다. 미래 정답도, 랭킹 후보도 N 과 무관하게 고정된다.
제품 관점으로도 이쪽이 맞다 — 앱은 사용자가 이미 간 가게를 N 과 무관하게 안다.
(두 해석의 차이는 결과 .md 에 건수로 적는다.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import config
from .data import Dataset
from .pool import Pool


def to_signal(rating: int) -> bool | None:
    """rating -> like/dislike. 3 은 중립이라 모델에 넣지 않는다(None)."""
    if rating >= config.LIKE_MIN_RATING:
        return True
    if rating <= config.DISLIKE_MAX_RATING:
        return False
    return None


@dataclass(frozen=True)
class UserSplit:
    """한 사용자의 분할 결과. N 과 무관한 부분만 담는다(N 은 조회 시 자른다)."""
    user_id: str
    total: int                                   # T
    past: tuple[tuple[str, int], ...]            # (store_id, rating) 시간순, 중복 유지
    future: tuple[tuple[str, int], ...]          # 유일화·과거제외 뒤의 미래 방문
    n_future_raw: int                            # 유일화 전 미래 건수
    n_future_dup: int                            # 같은 가게 재방문으로 합쳐진 건수
    n_future_seen: int                           # 과거에 이미 간 가게라 제외된 수
    n_future_seen_recent: dict                   # N 창 해석이면 제외됐을 수 (참고용)

    @property
    def seen_stores(self) -> set[str]:
        """이미 아는 가게 = 과거 전체. 랭킹 후보에서 뺀다."""
        return {sid for sid, _ in self.past}

    def history(self, n: int) -> tuple[tuple[str, int], ...]:
        """미래 직전의 최근 N 건. 모자라면 있는 만큼."""
        return self.past[-n:] if n > 0 else ()

    def signals(self, n: int, mode: str = "like_dislike") -> list[tuple[str, bool]]:
        """모델에 먹이는 입력 — (가게, like) 순서 그대로.

        `mode="visit"`   방문 = 관심. 이력의 **모든** 방문이 like(가중 1)다.
                         별점을 보지 않으므로 dislike 가 없고 신호가 곧 N 이다.
        `mode="like_dislike"` rating >= 4 like / <= 2 dislike, 3(중립)은 뺀다.
                         Step 0·1 이 쓴 해석. 그 결과를 재현하려면 이쪽이다.
        """
        if mode == "visit":
            return [(sid, True) for sid, _ in self.history(n)]
        if mode != "like_dislike":
            raise ValueError(f"모르는 이력 해석 '{mode}' — "
                             f"{', '.join(config.HISTORY_MODES)} 중 하나여야 한다")
        out = []
        for sid, rating in self.history(n):
            s = to_signal(rating)
            if s is not None:
                out.append((sid, s))
        return out

    def has_full_history(self, n: int) -> bool:
        return len(self.past) >= n

    def satisfaction_labels(self) -> list[tuple[str, int]]:
        """과제 R: 미래 방문 가게 -> 만족(1, rating>=4) / 불만족(0, rating<=3)."""
        return [(sid, 1 if rating >= config.SATISFIED_MIN_RATING else 0)
                for sid, rating in self.future]


def _future_size(total: int) -> int:
    return max(config.FUTURE_MIN, math.ceil(config.FUTURE_RATIO * total))


def build_splits(ds: Dataset, pool: Pool,
                 min_reviews: int | None = None) -> list[UserSplit]:
    """평가 사용자 전원의 분할. 정렬 키는 (date, review_id) — 같은 초에 달린 리뷰도
    실행마다 같은 순서가 되도록 결정적으로 묶는다."""
    min_reviews = config.MIN_USER_REVIEWS if min_reviews is None else min_reviews

    by_user: dict[str, list] = {}
    for r in ds.reviews:
        if r.store_id in pool.pos:
            by_user.setdefault(r.author_id, []).append(r)

    splits: list[UserSplit] = []
    for uid in sorted(by_user):
        rows = sorted(by_user[uid], key=lambda r: (r.date, r.review_id or 0))
        total = len(rows)
        if total < min_reviews:
            continue
        f = _future_size(total)
        past_rows, future_rows = rows[:total - f], rows[total - f:]
        past = tuple((r.store_id, r.rating) for r in past_rows)
        past_stores = {sid for sid, _ in past}

        seen_in_future: set[str] = set()
        uniq: list[tuple[str, int]] = []
        n_dup = 0
        for r in future_rows:
            if r.store_id in seen_in_future:
                n_dup += 1                 # 미래 안 재방문 — 가장 이른 것만 남긴다
                continue
            seen_in_future.add(r.store_id)
            uniq.append((r.store_id, r.rating))
        future = tuple((sid, rt) for sid, rt in uniq if sid not in past_stores)
        n_seen = len(uniq) - len(future)

        # 참고: '이력' 을 최근 N 창으로 읽었다면 몇 건이 제외됐을지.
        seen_recent = {}
        for n in config.HISTORY_SIZES:
            recent = {sid for sid, _ in past[-n:]}
            seen_recent[n] = sum(1 for sid, _ in uniq if sid in recent)

        splits.append(UserSplit(user_id=uid, total=total, past=past, future=future,
                                n_future_raw=len(future_rows), n_future_dup=n_dup,
                                n_future_seen=n_seen, n_future_seen_recent=seen_recent))
    return splits
