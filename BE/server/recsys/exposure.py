# -*- coding: utf-8 -*-
"""노출 집중도 — **쏠림을 감시하는 자리.**

`analysis/TREND.md` 7절이 "켜기 전에 만들어야 할 것" 으로 적은 셋 중 셋째다.

    1. 노출로 나눈다        이미 한다(like/노출)
    2. 탐색을 보장한다      이미 있다(Thompson σ×0.4, EXPLORE_EVERY=10)
    3. 집중도를 감시한다    ← 없었다. 이 모듈이다

**왜 필요한가.** 트렌드는 **전역 값**이라 모두를 같은 방향으로 민다. 거리는 사용자가
준 값이라 그 사람 맥락 안에서만 밀지만 트렌드는 그렇지 않다 → 인기 쏠림
(rich-get-richer). 이 지표가 없으면 쏠림이 생겨도 **모른다.**

지금 `MATCHURI_TREND_WEIGHT` 가 0 이므로 여기서 나오는 값은 **끄고 잰 기준선**이다.
켠 뒤 같은 수를 다시 재서 견주라는 뜻으로 `trend_enabled` 를 같이 싣는다.

## 판단 셋 — 다 다르게 할 수도 있었다

**① 지니는 풀 전체에서 낸다(노출 0 인 가게 포함).** 노출된 가게만 모아 재면
'한 번도 안 나온 가게' 가 분모에서 빠져 집중도가 실제보다 **좋아 보인다.** 쏠림을
감시하라고 만든 지표가 쏠림을 숨기면 그건 지표가 아니다. `never_shown` 을 따로
싣는 이유도 같다 — 지니 한 숫자로는 "얇고 넓게" 와 "몇몇만 자주" 가 안 갈린다.

**② 재노출을 센다.** `policy.outcome_stats` 는 like 율을 재려고 (사용자·세션·카드)로
중복을 지운다. 여기는 목적이 다르다 — 같은 가게를 **몇 번 밀었나**가 곧 쏠림이다.

**③ 정책별로 쪼갠다.** legacy 와 taste 중 어느 쪽이 더 쏠리는지가 판단의 알맹이다.
합쳐 놓으면 인터리빙이 둘을 섞어 버려 아무것도 안 보인다.
"""
from __future__ import annotations

import datetime as dt

from app import loader, state

from . import swipe

#: 상위 몇 곳의 점유율을 볼 것인가. 풀이 1,600곳대라 10/50/100 이면
#: 각각 0.6% · 3% · 6% 다 — 완전 균등이면 점유율도 그 값이 나온다.
TOP_N = (10, 50, 100)

#: 노출 라벨. `policy.outcome_stats` 와 같은 집합이다(`popular` 은 user_id 없는 길이라 뺀다).
#: 셋째 `trend` 는 트렌드 슬롯이 보여준 카드다 — 슬롯이 쏠림을 만드는지가 곧 이 관문의
#: 질문이므로 **따로** 잰다(`MATCHURI_TREND_EVERY`, `recsys/trend.py`).
POLICIES = ("legacy", "taste", "trend")


def _gini(counts: list[int]) -> float | None:
    """0 = 완전 균등, 1 = 한 곳이 다 먹음. 전부 0 이면 `None`(= 잴 것이 없다).

    정렬 후 `G = 2·Σ(i·x_i) / (n·Σx) − (n+1)/n`. 노출 0 인 가게가 그대로 들어와야
    하므로 `counts` 는 **풀 전체 길이**여야 한다(모듈 머리말 ①).
    """
    n = len(counts)
    if n == 0:
        return None
    xs = sorted(counts)
    total = sum(xs)
    if total <= 0:
        return None
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (2.0 * cum) / (n * total) - (n + 1.0) / n


def _shares(counts: list[int], top_n=TOP_N) -> dict[str, float]:
    """상위 N곳이 전체 노출의 몇 퍼센트를 먹었나."""
    total = sum(counts)
    if total <= 0:
        return {}
    desc = sorted(counts, reverse=True)
    return {str(k): round(sum(desc[:k]) / total, 4) for k in top_n}


def _summary(counts: dict[str, int], pool_ids: list[str]) -> dict:
    """가게별 노출 수 -> 집중도 한 묶음. `pool_ids` 가 분모다."""
    full = [counts.get(sid, 0) for sid in pool_ids]
    shown = sum(1 for c in full if c > 0)
    return {"exposures": sum(full), "shown": shown,
            "never_shown": len(pool_ids) - shown,
            "gini": (lambda g: None if g is None else round(g, 4))(_gini(full)),
            "top_share": _shares(full)}


def concentration(days: int = 0) -> dict:
    """노출 집중도. 기록이 없으면 `available: false` 이고 **오류가 아니다**.

    `days` 가 0 이면 전 기간이다. 켜고 끈 전후를 견주려면 창을 잘라서 본다.
    """
    from . import trend

    pool_ids = swipe.pool()["ids"]
    out: dict = {"available": False, "days": days, "pool": len(pool_ids),
                 "trend_enabled": trend.enabled(), "trend_weight": trend.weight(),
                 "trend_slot_every": trend.slot_every(),
                 "hint": "아직 노출 기록이 없습니다(`state/exposures.jsonl`). "
                         "카드가 실제로 나가야 쌓입니다 — 기동 복원에서는 안 쌓입니다."}

    cut = ""
    if days > 0:
        cut = (dt.datetime.now().astimezone()
               - dt.timedelta(days=days)).isoformat()

    total: dict[str, int] = {}
    per: dict[str, dict[str, int]] = {p: {} for p in POLICIES}
    users: set[str] = set()
    n_rows = 0
    for e in state.read_exposures():
        uid, cid, name = e.get("user_id"), e.get("card_id"), e.get("policy")
        if uid is None or cid is None or name not in POLICIES:
            continue
        if cut and (e.get("at") or "") < cut:
            continue
        sid = loader.to_store_id(int(cid))
        if sid is None:                 # 데이터가 바뀌어 카드가 사라진 경우
            continue
        n_rows += 1
        users.add(uid)
        total[sid] = total.get(sid, 0) + 1
        per[name][sid] = per[name].get(sid, 0) + 1

    if n_rows == 0:
        return out

    out.pop("hint", None)
    out.update(available=True, users=len(users),
               **_summary(total, pool_ids),
               by_policy={p: _summary(c, pool_ids) for p, c in per.items()})
    return out
