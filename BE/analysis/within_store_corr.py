# -*- coding: utf-8 -*-
"""같은 가게 안에서, 취향 유사도가 평가 일치를 설명하는가.

설계
  가게를 고정하면 음식/서비스/가격 등 객관적 요인이 전부 통제된다.
  같은 가게를 리뷰한 두 사람의 별점 차이는 (측정오차를 빼면) 취향 차이다.
  따라서  취향유사도(a,b)  vs  |별점_a(s) - 별점_b(s)|  의 상관이
  음(-)이면 "성향이 비슷할수록 같은 가게를 비슷하게 평가한다"가 증명된다.

누수 차단
  유사도는 **평가 대상 가게 s 를 제외한** 나머지 공유 가게로만 계산한다(leave-one-out).
  s 를 포함하면 "s 를 비슷하게 평가한 사람은 s 를 비슷하게 평가한다"는 순환논리가 된다.

  python analysis/within_store_corr.py
"""
from __future__ import annotations

import collections
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"

MAX_STORE_USERS = 400      # 조합 폭발 방지
MIN_SHARED_FOR_SIM = 2     # s 제외 후에도 최소 이만큼 공유해야 유사도를 신뢰


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else None


def main() -> None:
    us: dict[str, dict[str, int]] = collections.defaultdict(dict)
    store_users: dict[str, set[str]] = collections.defaultdict(set)
    for line in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("star"):
            us[r["user_hash"]][r["store_id"]] = r["star"]
            store_users[r["store_id"]].add(r["user_hash"])
    print(f"유저 {len(us):,}명 · 가게 {len(store_users):,}곳")

    # 유저 평균(개인 후함/박함 보정용)
    umean = {u: statistics.mean(sm.values()) for u, sm in us.items()}

    # ── 가게별 쌍 만들기 ────────────────────────────────────────
    recs = []            # (sim, diff, adj_diff, n_shared_excl)
    n_pairs = 0
    for s, users in store_users.items():
        if len(users) < 2 or len(users) > MAX_STORE_USERS:
            continue
        ul = sorted(users)
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                sa, sb = set(us[a]), set(us[b])
                shared_excl = (sa & sb) - {s}          # ← s 제외 (누수 차단)
                if len(shared_excl) < MIN_SHARED_FOR_SIM:
                    continue
                # 유사도: s 를 뺀 나머지에서의 자카드
                union_excl = (sa | sb) - {s}
                sim = len(shared_excl) / len(union_excl) if union_excl else 0.0
                # 같은 가게에 대한 별점 차이
                diff = abs(us[a][s] - us[b][s])
                # 개인 편향 보정(후한 사람/박한 사람 차이 제거)
                adj = abs((us[a][s] - umean[a]) - (us[b][s] - umean[b]))
                recs.append((sim, diff, adj, len(shared_excl)))
                n_pairs += 1
    print(f"분석 대상 (가게, 쌍) {n_pairs:,}건  [s 제외 공유 {MIN_SHARED_FOR_SIM}곳 이상]")
    if n_pairs < 50:
        print("표본 부족 — 카테고리 단위로 올려야 함")
        return

    sims = [r[0] for r in recs]
    diffs = [r[1] for r in recs]
    adjs = [r[2] for r in recs]

    print("\n■ 상관계수 (음수여야 가설 지지)")
    print(f"  유사도 vs 별점차이(원본)   r = {pearson(sims, diffs):+.4f}")
    print(f"  유사도 vs 별점차이(편향보정) r = {pearson(sims, adjs):+.4f}")

    # ── 유사도 구간별 평균 별점차이 ─────────────────────────────
    order = sorted(recs, key=lambda r: r[0])
    n = len(order)
    print(f"\n■ 유사도 구간별 같은 가게 별점차이  (n={n:,})")
    print(f"    {'구간':>10} {'n':>7} {'유사도평균':>10} {'별점차이':>9} {'보정차이':>9}")
    prev = None
    for lo, hi, label in [(0, .20, '하위20%'), (.20, .40, '20-40%'), (.40, .60, '40-60%'),
                          (.60, .80, '60-80%'), (.80, .95, '80-95%'), (.95, 1.0, '상위5%')]:
        seg = order[int(n * lo):int(n * hi)]
        if not seg:
            continue
        ms = statistics.mean(r[0] for r in seg)
        md = statistics.mean(r[1] for r in seg)
        ma = statistics.mean(r[2] for r in seg)
        arrow = ""
        if prev is not None:
            arrow = " ↓" if md < prev else " ↑"
        prev = md
        print(f"    {label:>10} {len(seg):7,} {ms:10.4f} {md:9.3f}{arrow} {ma:9.3f}")

    # ── 베이스라인: 무작위 쌍 ───────────────────────────────────
    import random
    random.seed(3)
    rnd = []
    ulist = list(us)
    for _ in range(50000):
        a, b = random.sample(ulist, 2)
        common = set(us[a]) & set(us[b])
        if not common:
            continue
        s = random.choice(list(common))
        rnd.append(abs(us[a][s] - us[b][s]))
    print(f"\n■ 베이스라인: 무작위 두 사람의 같은 가게 별점차이 = {statistics.mean(rnd):.3f} (n={len(rnd):,})")

    # ── 공유 수를 통제한 재확인 ─────────────────────────────────
    print("\n■ 공유 가게 수를 통제해도 관계가 남는가")
    byshare = collections.defaultdict(list)
    for sim, d, adj, k in recs:
        band = '2곳' if k == 2 else '3-4곳' if k < 5 else '5-9곳' if k < 10 else '10곳+'
        byshare[band].append((sim, d))
    for band in ['2곳', '3-4곳', '5-9곳', '10곳+']:
        g = byshare[band]
        if len(g) < 30:
            continue
        r = pearson([x[0] for x in g], [x[1] for x in g])
        print(f"  공유 {band:>5}: n={len(g):6,}  r={r:+.4f}  평균차이 {statistics.mean(x[1] for x in g):.3f}")


if __name__ == "__main__":
    main()
