# -*- coding: utf-8 -*-
"""선호일치 유사도로 기존 실패 실험을 전부 재시도.

유사도 정의를 바꿨다.
  (구) 겹침 수 = 같은 가게를 몇 곳 함께 갔나        -> 생활권만 잡힘, 정보 없음
  (신) 선호일치 = 함께 간 곳에서 호불호가 같았나  -> 실제 취향

이 정의로 아래를 다시 본다.
  A. 접합도별 선호 동일률 (기초 곡선)
  B. 같은 가게 평가차이 상관        (이전 r=-0.036)
  C. 그룹 가중 예측                 (이전 평판보다 나쁨)
  D. 커뮤니티 검출 + 지역 반증      (이전 '생활권'으로 판정)
  E. 카테고리 축 취향               (이전 유의하게 악화)

  python analysis/redo_with_pref_sim.py
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
SEED = 7


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (c - m) / d, (c + m) / d


def pearson(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else None


def main() -> None:
    rng = random.Random(SEED)
    us = collections.defaultdict(dict)
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star"):
            us[r["user_hash"]][r["store_id"]] = r["star"]
    umean = {u: statistics.mean(v.values()) for u, v in us.items()}
    su = collections.defaultdict(set)
    for u, sm in us.items():
        for s in sm:
            su[s].add(u)
    uset = {u: set(v) for u, v in us.items()}
    print(f"유저 {len(us):,}명 · 가게 {len(su):,}곳")

    def pref(u, s):
        return 1 if us[u][s] >= umean[u] else 0

    # ── 전체 쌍의 겹침/일치 집계 ──────────────────────────────
    shared = collections.Counter()
    agree = collections.Counter()
    for s, U in su.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul = sorted(U)
        pr = {u: pref(u, s) for u in ul}
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                shared[(a, b)] += 1
                if pr[a] == pr[b]:
                    agree[(a, b)] += 1
    print(f"쌍 {len(shared):,}개\n")

    # ══════════════════════════════════════════════════════════
    print("■ A. 접합도별 선호 동일률 (기초 곡선)")
    print(f"  {'겹침':>9} {'쌍수':>8} {'선호동일률':>11} {'95%CI':>16} "
          f"{'>=0.7':>7} {'<=0.3':>7}")
    bands = [(1, 1), (2, 2), (3, 4), (5, 9), (10, 19), (20, 49), (50, 10**9)]
    curveA = []
    for lo, hi in bands:
        ps = [p for p, n in shared.items() if lo <= n <= hi]
        if len(ps) < 20:
            continue
        rates = [agree[p] / shared[p] for p in ps]
        tot_sh = sum(shared[p] for p in ps)
        tot_ag = sum(agree[p] for p in ps)
        clo, chi = wilson(tot_ag, tot_sh)
        hi_r = 100 * sum(1 for x in rates if x >= 0.7) / len(rates)
        lo_r = 100 * sum(1 for x in rates if x <= 0.3) / len(rates)
        lab = f"{lo}곳" if lo == hi else (f"{lo}+곳" if hi > 1000 else f"{lo}-{hi}곳")
        print(f"  {lab:>9} {len(ps):8,} {100*tot_ag/tot_sh:10.1f}% "
              f"[{100*clo:5.1f},{100*chi:5.1f}] {hi_r:6.0f}% {lo_r:6.0f}%")
        curveA.append((lab, len(ps), tot_ag / tot_sh))
    print("  (>=0.7 = 취향 일치 쌍 비율, <=0.3 = 취향 상반 쌍 비율)")

    # 유사도 정의
    def sim(a, b):
        p = (a, b) if a < b else (b, a)
        n = shared.get(p, 0)
        if n < 3:
            return 0.0
        w = ((agree[p] + 1.0) / (n + 2.0) - 0.5) * 2
        return max(0.0, w)

    # ══════════════════════════════════════════════════════════
    print("\n■ B. 같은 가게 평가차이 상관 (구: r=-0.036)")
    recs = []
    for s, U in su.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul = sorted(U)
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                p = (a, b)
                n_ex = shared[p] - 1                    # 이 가게 제외
                if n_ex < 3:
                    continue
                ag_ex = agree[p] - int(pref(a, s) == pref(b, s))
                w = ((ag_ex + 1.0) / (n_ex + 2.0) - 0.5) * 2
                ov = n_ex / len((uset[a] | uset[b]) - {s})
                recs.append((w, ov, abs(us[a][s] - us[b][s])))
    if recs:
        w_ = [r[0] for r in recs]
        o_ = [r[1] for r in recs]
        d_ = [r[2] for r in recs]
        print(f"  n={len(recs):,}")
        print(f"  선호일치 유사도 vs 별점차이  r = {pearson(w_, d_):+.4f}")
        print(f"  겹침(자카드)   vs 별점차이  r = {pearson(o_, d_):+.4f}  <- 구 방식")
        print(f"  {'유사도구간':>12} {'n':>8} {'평균 별점차이':>13}")
        order = sorted(recs)
        n = len(order)
        for lo, hi, lab in [(0, .2, '하위20%'), (.2, .4, '20-40%'),
                            (.4, .6, '40-60%'), (.6, .8, '60-80%'),
                            (.8, 1., '상위20%')]:
            seg = order[int(n*lo):int(n*hi)]
            if seg:
                print(f"  {lab:>12} {len(seg):8,} {statistics.mean(x[2] for x in seg):12.3f}")

    # ══════════════════════════════════════════════════════════
    print("\n■ C. 그룹 가중 예측 (구: 평판보다 나쁨)")
    rows = [(u, s, us[u][s]) for u in us for s in us[u]]
    rng.shuffle(rows)
    k = int(len(rows) * 0.8)
    tr_rows, te_rows = rows[:k], rows[k:]
    tr = collections.defaultdict(dict)
    for u, s, st in tr_rows:
        tr[u][s] = st
    tmean = {u: statistics.mean(v.values()) for u, v in tr.items() if v}
    tsu = collections.defaultdict(set)
    for u, sm in tr.items():
        for s in sm:
            tsu[s].add(u)
    # train 기준 재집계
    t_sh = collections.Counter()
    t_ag = collections.Counter()
    for s, U in tsu.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul = sorted(U)
        pr = {u: (1 if tr[u][s] >= tmean[u] else 0) for u in ul}
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                t_sh[(a, b)] += 1
                if pr[a] == pr[b]:
                    t_ag[(a, b)] += 1

    def tsim(a, b, min_sh):
        p = (a, b) if a < b else (b, a)
        n = t_sh.get(p, 0)
        if n < min_sh:
            return 0.0
        return max(0.0, ((t_ag[p] + 1.0) / (n + 2.0) - 0.5) * 2)

    cand = [(u, s, st) for u, s, st in te_rows
            if u in tmean and s in tsu and len(tsu[s] - {u}) >= 3]
    print(f"  평가 {len(cand):,}건")
    print(f"  {'최소겹침':>9} {'방식':>12} {'n':>8} {'정확도':>8} {'95%CI':>16}")
    for ms in [3, 5, 10]:
        res = {}
        for mode in ("none", "pref"):
            hit = tot = 0
            for u, s, st in cand:
                act = 1 if st >= tmean[u] else 0
                sc = []
                for v in tsu[s]:
                    if v == u:
                        continue
                    att = tr[v][s] - tmean[v]
                    w = 1.0 if mode == "none" else tsim(u, v, ms)
                    if w > 0:
                        sc.append((w, att))
                if not sc:
                    continue
                den = sum(w for w, _ in sc)
                val = sum(w * a for w, a in sc) / den
                tot += 1
                hit += int((1 if val >= 0 else 0) == act)
            res[mode] = (hit, tot)
        # 공정 비교: pref 로 커버되는 건만 평판도 재계산
        h_p, t_p = res["pref"]
        h_n, t_n = res["none"]
        for mode, lab in [("none", "평판"), ("pref", "선호일치가중")]:
            h, t = res[mode]
            if t < 50:
                continue
            lo, hi = wilson(h, t)
            print(f"  {ms:>7}곳+ {lab:>12} {t:8,} {100*h/t:7.1f}% "
                  f"[{100*lo:5.1f},{100*hi:5.1f}]")
        print()

    # ══════════════════════════════════════════════════════════
    print("■ D. 커뮤니티 재검출 + 지역 반증 (구: '생활권'으로 판정)")
    meta = {}
    p = DATA / "store_meta.jsonl"
    if p.exists():
        for l in p.open(encoding="utf-8"):
            m = json.loads(l)
            meta[m["store_id"]] = (m.get("region2"), m.get("cat2"))

    def prof(u, idx):
        c = collections.Counter()
        for s in uset[u]:
            v = meta.get(s)
            if v and v[idx]:
                c[v[idx]] += 1
        return c

    def psim(a, b, idx):
        ca, cb = prof(a, idx), prof(b, idx)
        if not ca or not cb:
            return None
        ks = set(ca) | set(cb)
        va = [ca.get(x, 0) for x in ks]
        vb = [cb.get(x, 0) for x in ks]
        na = sum(x*x for x in va) ** .5
        nb = sum(x*x for x in vb) ** .5
        return sum(x*y for x, y in zip(va, vb)) / (na*nb) if na and nb else None

    if meta:
        # 겹침을 동일 구간으로 고정하고 선호일치만 다르게
        for lo_s, hi_s in [(5, 9), (10, 10**9)]:
            hi_grp = [p for p, n in shared.items()
                      if lo_s <= n <= hi_s and agree[p]/n >= 0.70]
            lo_grp = [p for p, n in shared.items()
                      if lo_s <= n <= hi_s and agree[p]/n <= 0.35]
            if len(hi_grp) < 15 or len(lo_grp) < 15:
                continue
            lab = f"겹침 {lo_s}-{hi_s}곳" if hi_s < 1000 else f"겹침 {lo_s}곳+"
            print(f"  [{lab}] 취향일치 {len(hi_grp)}쌍 vs 취향상반 {len(lo_grp)}쌍")
            for idx, nm in [(0, "지역"), (1, "카테고리")]:
                s1 = [x for x in (psim(a, b, idx) for a, b in hi_grp[:1500]) if x]
                s2 = [x for x in (psim(a, b, idx) for a, b in lo_grp[:1500]) if x]
                if s1 and s2:
                    print(f"    {nm:<6} 일치 {statistics.mean(s1):.3f} · "
                          f"상반 {statistics.mean(s2):.3f} · "
                          f"차이 {statistics.mean(s1)-statistics.mean(s2):+.3f}")


if __name__ == "__main__":
    main()
