# -*- coding: utf-8 -*-
"""유저 간 취향 유사도 계산 + 유사도가 '진짜 신호'인지 검증.

핵심 질문: 유사도가 높은 쌍은 정말로 취향이 비슷한가?
검증 방법 - 유사도 계산에 쓰지 않은 별점(holdout)으로 일치도를 재본다.
유사도가 무의미하면 홀드아웃 일치도가 무작위 베이스라인과 같아야 한다.

  python analysis/similarity.py
"""
from __future__ import annotations

import collections
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"

# 이 인원 이상이 리뷰한 가게는 '유명해서 다들 간 곳'이라 취향 신호가 약하다.
POPULAR = 50


def load():
    us: dict[str, dict[str, int]] = collections.defaultdict(dict)   # user -> {store: star}
    store_users: dict[str, set[str]] = collections.defaultdict(set)
    for line in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("star"):
            us[r["user_hash"]][r["store_id"]] = r["star"]
            store_users[r["store_id"]].add(r["user_hash"])
    return us, store_users


def main() -> None:
    random.seed(11)
    us, store_users = load()
    print(f"유저 {len(us):,}명 · 가게 {len(store_users):,}곳")

    # ── holdout 분리 ────────────────────────────────────────────
    # 각 유저의 리뷰 20%를 떼어 유사도 계산에서 제외한다.
    train: dict[str, dict[str, int]] = {}
    hold: dict[str, dict[str, int]] = {}
    for u, sm in us.items():
        items = list(sm.items())
        random.shuffle(items)
        k = max(1, int(len(items) * 0.2))
        hold[u] = dict(items[:k])
        train[u] = dict(items[k:])
    print(f"train 평균 {statistics.mean(len(v) for v in train.values()):.0f}건 · "
          f"holdout 평균 {statistics.mean(len(v) for v in hold.values()):.0f}건")

    # ── train 기준 IDF 가중치 ───────────────────────────────────
    train_store_users: dict[str, set[str]] = collections.defaultdict(set)
    for u, sm in train.items():
        for s in sm:
            train_store_users[s].add(u)
    N = len(train)
    idf = {s: math.log(N / len(v)) for s, v in train_store_users.items() if v}

    # ── 후보 쌍: train 에서 가게를 공유하는 쌍만 ─────────────────
    pair_shared: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for s, users in train_store_users.items():
        if len(users) < 2 or len(users) > 400:
            continue
        ul = sorted(users)
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                pair_shared[(ul[i], ul[j])].append(s)
    print(f"후보 쌍 {len(pair_shared):,}개")

    # ── 유사도 3종 ──────────────────────────────────────────────
    rows = []
    for (a, b), shared in pair_shared.items():
        ta, tb = train[a], train[b]
        sa, sb = set(ta), set(tb)

        jacc = len(sa & sb) / len(sa | sb)
        # IDF 가중 겹침: 희소한 가게에서 겹칠수록 높은 점수
        w_shared = sum(idf.get(s, 0) for s in shared)
        w_union = sum(idf.get(s, 0) for s in (sa | sb))
        widf = w_shared / w_union if w_union else 0.0
        # 공유 가게에 매긴 별점의 코사인(평균 보정)
        va = [ta[s] for s in shared]
        vb = [tb[s] for s in shared]
        ma = statistics.mean(ta.values())
        mb = statistics.mean(tb.values())
        ca = [x - ma for x in va]
        cb = [x - mb for x in vb]
        den = math.sqrt(sum(x * x for x in ca)) * math.sqrt(sum(x * x for x in cb))
        cos = (sum(x * y for x, y in zip(ca, cb)) / den) if den else 0.0

        rows.append({"a": a, "b": b, "shared": len(shared),
                     "jaccard": jacc, "widf": widf, "cos": cos})

    # ── 검증: holdout 별점 일치도 ────────────────────────────────
    def holdout_agreement(a: str, b: str) -> float | None:
        """유사도 계산에 안 쓴 리뷰에서, 두 사람이 같은 가게에 준 별점의 일치도."""
        common = set(hold[a]) & set(hold[b])
        # train 에 있던 가게는 제외(정보 누수 방지)
        common -= set(train[a]) | set(train[b])
        if not common:
            return None
        return statistics.mean(1 - abs(hold[a][s] - hold[b][s]) / 4 for s in common)

    for r in rows:
        r["holdout"] = holdout_agreement(r["a"], r["b"])

    with (DATA / "similarity.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"유사도 {len(rows):,}쌍 -> similarity.jsonl")

    # ── 베이스라인 ──────────────────────────────────────────────
    all_stars = [s for sm in us.values() for s in sm.values()]
    base = statistics.mean(
        1 - abs(random.choice(all_stars) - random.choice(all_stars)) / 4
        for _ in range(200000))

    print(f"\n■ 무작위 베이스라인 별점일치: {base:.4f}")
    print("\n■ 유사도 구간별 holdout 별점일치  <- 유사도가 진짜인지 검증")
    for metric in ("jaccard", "widf", "cos"):
        vals = [r for r in rows if r["holdout"] is not None]
        vals.sort(key=lambda r: r[metric])
        n = len(vals)
        print(f"\n  [{metric}]  (검증 가능 쌍 {n:,}개)")
        print(f"    {'구간':>10} {'n':>7} {'holdout일치':>11} {'베이스대비':>10}")
        for lo, hi, label in [(0, .20, '하위20%'), (.20, .40, '20-40%'),
                              (.40, .60, '40-60%'), (.60, .80, '60-80%'),
                              (.80, .95, '80-95%'), (.95, 1.0, '상위5%')]:
            seg = vals[int(n * lo):int(n * hi)]
            if not seg:
                continue
            m = statistics.mean(r["holdout"] for r in seg)
            print(f"    {label:>10} {len(seg):7,} {m:11.4f} {m-base:+10.4f}")


if __name__ == "__main__":
    main()
