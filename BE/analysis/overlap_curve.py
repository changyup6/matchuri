# -*- coding: utf-8 -*-
"""접합률(공유 가게 수)에 따른 상관계수 변화 + 고접합 유저 한정 재검증.

지금까지 살아남은 신호는 '가게 수준 겹침이 충분할 때만' 보였다.
그 관계를 연속적으로 그려서, 상관이 접합도의 함수임을 보인다.

  python analysis/overlap_curve.py
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
MAX_STORE_USERS = 400


def pearson(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else None


def boot_ci(xs, ys, n_boot=1000, seed=7):
    rng = random.Random(seed)
    n = len(xs)
    if n < 30:
        return None, None
    rs = []
    idx = list(range(n))
    for _ in range(n_boot):
        pick = [rng.choice(idx) for _ in range(n)]
        r = pearson([xs[i] for i in pick], [ys[i] for i in pick])
        if r is not None:
            rs.append(r)
    rs.sort()
    if not rs:
        return None, None
    return rs[int(.025 * len(rs))], rs[int(.975 * len(rs))]


def main() -> None:
    us: dict[str, dict[str, int]] = collections.defaultdict(dict)
    su: dict[str, set[str]] = collections.defaultdict(set)
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star"):
            us[r["user_hash"]][r["store_id"]] = r["star"]
            su[r["store_id"]].add(r["user_hash"])
    umean = {u: statistics.mean(v.values()) for u, v in us.items()}
    print(f"유저 {len(us):,}명 · 가게 {len(su):,}곳")

    # ── 같은 가게 안 쌍 수집 (s 제외 leave-one-out) ─────────────
    recs = []   # (n_shared_excl, sim, diff, adj, a, b)
    for s, U in su.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul = sorted(U)
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                sa, sb = set(us[a]), set(us[b])
                shared = (sa & sb) - {s}
                union = (sa | sb) - {s}
                k = len(shared)
                if k < 1:
                    continue
                sim = k / len(union) if union else 0.0
                diff = abs(us[a][s] - us[b][s])
                adj = abs((us[a][s] - umean[a]) - (us[b][s] - umean[b]))
                recs.append((k, sim, diff, adj, a, b))
    print(f"분석 쌍(가게×쌍) {len(recs):,}건\n")

    # ── ① 접합도별 상관계수 곡선 ────────────────────────────────
    print("■ 접합도(공유 가게 수)별 상관계수")
    print(f"  {'공유':>8} {'n':>9} {'r(원본)':>10} {'95%CI':>20} {'평균차이':>9}")
    bands = [(1, 1), (2, 2), (3, 4), (5, 9), (10, 19), (20, 49), (50, 10**9)]
    curve = []
    for lo, hi in bands:
        g = [x for x in recs if lo <= x[0] <= hi]
        if len(g) < 20:
            continue
        sims = [x[1] for x in g]
        diffs = [x[2] for x in g]
        r = pearson(sims, diffs)
        clo, chi = boot_ci(sims, diffs)
        label = f"{lo}곳" if lo == hi else (f"{lo}+곳" if hi > 1000 else f"{lo}-{hi}곳")
        ci = f"[{clo:+.3f},{chi:+.3f}]" if clo is not None else "  --  "
        print(f"  {label:>8} {len(g):9,} {r:+10.4f} {ci:>20} "
              f"{statistics.mean(diffs):9.3f}")
        curve.append((label, len(g), r, statistics.mean(diffs)))

    # ── ② 누적: 공유 N곳 이상만 볼 때 ──────────────────────────
    print("\n■ 누적 — 공유 N곳 '이상'인 쌍만 사용할 때")
    print(f"  {'기준':>8} {'n':>9} {'비율':>7} {'r':>9} {'평균차이':>9}")
    for th in [1, 2, 3, 5, 10, 20, 30, 50]:
        g = [x for x in recs if x[0] >= th]
        if len(g) < 20:
            continue
        r = pearson([x[1] for x in g], [x[2] for x in g])
        print(f"  {th:>6}곳+ {len(g):9,} {100*len(g)/len(recs):6.1f}% "
              f"{r:+9.4f} {statistics.mean(x[2] for x in g):9.3f}")

    # ── ③ 고접합 유저 한정 재검증 ──────────────────────────────
    print("\n■ 고접합 유저 한정 (공유 10곳+ 쌍에 참여하는 유저)")
    hi_users = set()
    for k, sim, d, adj, a, b in recs:
        if k >= 10:
            hi_users.add(a)
            hi_users.add(b)
    print(f"  대상 유저 {len(hi_users):,}명 / 전체 {len(us):,}명")
    sub = [x for x in recs if x[4] in hi_users and x[5] in hi_users]
    print(f"  해당 쌍 {len(sub):,}건")
    if len(sub) >= 30:
        r_o = pearson([x[1] for x in sub], [x[2] for x in sub])
        r_a = pearson([x[1] for x in sub], [x[3] for x in sub])
        clo, chi = boot_ci([x[1] for x in sub], [x[2] for x in sub])
        print(f"  r(원본)     {r_o:+.4f}  [95%CI {clo:+.4f}, {chi:+.4f}]")
        print(f"  r(편향보정) {r_a:+.4f}")
        # 이 부분집합 안에서도 접합도별로 나눠보기
        print(f"\n  고접합 유저 내부에서 다시 접합도별로:")
        for lo, hi in [(1, 4), (5, 9), (10, 19), (20, 10**9)]:
            g = [x for x in sub if lo <= x[0] <= hi]
            if len(g) < 30:
                continue
            r = pearson([x[1] for x in g], [x[2] for x in g])
            lab = f"{lo}-{hi}곳" if hi < 1000 else f"{lo}+곳"
            print(f"    {lab:>8} n={len(g):7,} r={r:+.4f} "
                  f"평균차이 {statistics.mean(x[2] for x in g):.3f}")

    # ── ④ 베이스라인 ───────────────────────────────────────────
    rng = random.Random(3)
    ul = list(us)
    rnd = []
    for _ in range(60000):
        a, b = rng.sample(ul, 2)
        c = set(us[a]) & set(us[b])
        if c:
            s = rng.choice(list(c))
            rnd.append(abs(us[a][s] - us[b][s]))
    print(f"\n■ 무작위 쌍 베이스라인 별점차이 {statistics.mean(rnd):.3f} (n={len(rnd):,})")

    json.dump({"curve": curve}, (DATA / "overlap_curve.json").open("w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
