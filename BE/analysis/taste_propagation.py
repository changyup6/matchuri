# -*- coding: utf-8 -*-
"""취향 유사도 재귀 전파 — 겹침 없는 쌍까지 확장.

배경
  선호일치 기반 유사도는 평판 대비 +1~2%p 개선을 냈지만, 겹침이 있는 쌍에만
  정의된다(평가 대상의 약 14% 손실). A-B, B-C 가 취향 일치면 A-C 도 일치할
  것이라는 전이성을 이용해 2-hop 까지 넓힌다.

  겹침 기반 유사도로 전파하면 노이즈가 증폭되지만(이미 확인), 선호일치 링크는
  실제 신호를 담고 있으므로 전파에 의미가 있다.

전파 규칙
  sim2(a,c) = max over b [ sim(a,b) * sim(b,c) ]      (감쇠 곱)
  1-hop 이 있으면 그것을 우선하고, 없을 때만 2-hop 을 쓴다.

  python analysis/taste_propagation.py
"""
from __future__ import annotations

import collections
import json
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"
MAX_STORE_USERS = 400
SEED = 7
MIN_SHARED = 3          # 1-hop 유사도를 신뢰할 최소 겹침
STRONG = 0.60           # 전파에 쓸 링크의 최소 선호일치율


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (c - m) / d, (c + m) / d


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star"):
            rows.append((r["user_hash"], r["store_id"], r["star"]))
    rng.shuffle(rows)
    k = int(len(rows) * 0.8)
    train, test = rows[:k], rows[k:]

    tr = collections.defaultdict(dict)
    for u, s, st in train:
        tr[u][s] = st
    umean = {u: statistics.mean(v.values()) for u, v in tr.items()}
    su = collections.defaultdict(set)
    for u, sm in tr.items():
        for s in sm:
            su[s].add(u)
    print(f"train {len(train):,} · test {len(test):,} · 유저 {len(tr):,}")

    def pref(u, s):
        return 1 if tr[u][s] >= umean[u] else 0

    # ── 1-hop: 겹침 + 선호일치 ────────────────────────────────
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

    sim1 = {}
    for p, n in shared.items():
        if n < MIN_SHARED:
            continue
        a = (agree[p] + 1.0) / (n + 2.0)          # 평활
        w = (a - 0.5) * 2                          # -1 ~ +1
        if w > 0:
            sim1[p] = w
    print(f"1-hop 유사도(겹침 {MIN_SHARED}곳+, 일치>0.5): {len(sim1):,}쌍")

    # 전파용 인접: 강한 링크만
    adj = collections.defaultdict(list)
    for (a, b), w in sim1.items():
        n = shared[(a, b)]
        if agree[(a, b)] / n >= STRONG:
            adj[a].append((b, w))
            adj[b].append((a, w))
    print(f"전파용 강한 링크(일치 {STRONG:.0%}+): "
          f"{sum(len(v) for v in adj.values())//2:,}개 · 참여 {len(adj):,}명")

    def get_sim1(a, b):
        return sim1.get((a, b) if a < b else (b, a), 0.0)

    def get_sim2(a, b):
        """2-hop 최대 경로 강도."""
        best = 0.0
        na = adj.get(a)
        if not na:
            return 0.0
        nb = {x: w for x, w in adj.get(b, ())}
        if not nb:
            return 0.0
        for m, w1 in na:
            w2 = nb.get(m)
            if w2:
                v = w1 * w2
                if v > best:
                    best = v
        return best

    # ── 평가 ──────────────────────────────────────────────────
    cand = [(u, s, st) for u, s, st in test
            if u in tr and s in su and len(su[s] - {u}) >= 3]
    print(f"\n평가 대상 {len(cand):,}건")

    def run(mode, topk=0):
        hit = tot = 0
        covered = 0
        for u, s, st in cand:
            actual = 1 if st >= umean[u] else 0
            scored = []
            for v in su[s]:
                if v == u:
                    continue
                att = tr[v][s] - umean[v]
                if mode == "none":
                    w = 1.0
                elif mode == "hop1":
                    w = get_sim1(u, v)
                elif mode == "hop2":
                    w = get_sim1(u, v)
                    if w <= 0:
                        w = get_sim2(u, v) * 0.7    # 2-hop 감쇠
                else:
                    w = 1.0
                if w <= 0:
                    continue
                scored.append((w, att))
            if not scored:
                continue
            covered += 1
            scored.sort(reverse=True)
            use = scored[:topk] if topk else scored
            den = sum(w for w, _ in use)
            if den <= 0:
                continue
            val = sum(w * a for w, a in use) / den
            tot += 1
            hit += int((1 if val >= 0 else 0) == actual)
        return hit, tot

    print("\n■ 선호 예측 정확도 — 1-hop vs 2-hop 전파")
    print(f"  {'방식':>14} {'n':>8} {'커버리지':>9} {'정확도':>8} {'95%CI':>16}")
    base_h, base_t = run("none")
    lo, hi = wilson(base_h, base_t)
    print(f"  {'평판(무가중)':>14} {base_t:8,} {100*base_t/len(cand):8.1f}% "
          f"{100*base_h/base_t:7.1f}% [{100*lo:5.1f},{100*hi:5.1f}]")
    for mode, lab in [("hop1", "선호일치 1-hop"), ("hop2", "+2-hop 전파")]:
        h, t = run(mode)
        if t < 50:
            continue
        lo, hi = wilson(h, t)
        print(f"  {lab:>14} {t:8,} {100*t/len(cand):8.1f}% "
              f"{100*h/t:7.1f}% [{100*lo:5.1f},{100*hi:5.1f}]")

    # ── 동일 표본 비교(공정 비교) ─────────────────────────────
    print("\n■ 동일 표본 비교 — 2-hop 으로 커버되는 건만")
    both = []
    for u, s, st in cand:
        has = False
        for v in su[s]:
            if v == u:
                continue
            if get_sim1(u, v) > 0 or get_sim2(u, v) > 0:
                has = True
                break
        if has:
            both.append((u, s, st))
    print(f"  대상 {len(both):,}건")

    def run_on(rows_, mode):
        hit = tot = 0
        for u, s, st in rows_:
            actual = 1 if st >= umean[u] else 0
            scored = []
            for v in su[s]:
                if v == u:
                    continue
                att = tr[v][s] - umean[v]
                if mode == "none":
                    w = 1.0
                elif mode == "hop1":
                    w = get_sim1(u, v)
                else:
                    w = get_sim1(u, v) or get_sim2(u, v) * 0.7
                if w <= 0:
                    continue
                scored.append((w, att))
            if not scored:
                continue
            den = sum(w for w, _ in scored)
            val = sum(w * a for w, a in scored) / den
            tot += 1
            hit += int((1 if val >= 0 else 0) == actual)
        return hit, tot

    for mode, lab in [("none", "평판"), ("hop1", "1-hop"), ("hop2", "1+2-hop")]:
        h, t = run_on(both, mode)
        if t < 50:
            continue
        lo, hi = wilson(h, t)
        print(f"  {lab:>8} {t:8,} {100*h/t:7.1f}% [{100*lo:5.1f},{100*hi:5.1f}]")

    # ── 전파가 실제로 새 쌍을 만들었는가 ──────────────────────
    print("\n■ 전파 효과")
    n1 = n2 = 0
    sample = rng.sample(cand, min(3000, len(cand)))
    for u, s, st in sample:
        for v in su[s]:
            if v == u:
                continue
            if get_sim1(u, v) > 0:
                n1 += 1
            elif get_sim2(u, v) > 0:
                n2 += 1
    print(f"  표본 {len(sample):,}건에서 이웃 링크: "
          f"1-hop {n1:,}개 · 2-hop 신규 {n2:,}개 (+{100*n2/max(n1,1):.0f}%)")


if __name__ == "__main__":
    main()
