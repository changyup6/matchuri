# -*- coding: utf-8 -*-
"""검증된 조건(겹침 10곳+ & 선호일치 가중)을 기반으로 한 확장 실험 총괄.

확인된 사실
  - 겹침 자체는 취향 정보가 없다(선호동일률이 겹침과 무관하게 57~58%).
  - 겹침은 '유사도를 신뢰할 수 있는가'만 결정한다.
  - 겹침 10곳+ 에서 선호일치 가중 -> 66.8% (평판 61.6%), 적용 범위 6%.

목표: 그 6% 를 넓히거나 정확도를 더 올리는 방법을 전부 시도.
  1  임계값 스윕            겹침 임계를 5~30 으로 바꿔가며 정확도/커버리지 곡선
  2  강한 링크만 2-hop 전파  10곳+ 링크만 전파(이전엔 3곳+ 로 전파해 실패)
  3  비대칭 가중            상반 이웃(일치<0.5)의 태도를 뒤집어 활용
  4  신뢰도 가중            겹침 수로 유사도 신뢰도를 스케일
  5  하이브리드             취향 신호가 없으면 평판으로 폴백
  6  확실한 평가만          이웃의 태도가 명확한 경우만 사용
  7  최종 조합              위에서 이긴 것들을 합쳐 최고 성능

  python analysis/full_sweep.py
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


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (c - m) / d, (c + m) / d


def fmt(h, t):
    if t == 0:
        return "     -          "
    lo, hi = wilson(h, t)
    return f"{100*h/t:6.1f}% [{100*lo:5.1f},{100*hi:5.1f}]"


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star"):
            rows.append((r["user_hash"], r["store_id"], r["star"]))
    rng.shuffle(rows)
    k = int(len(rows) * 0.8)
    tr_rows, te_rows = rows[:k], rows[k:]

    tr = collections.defaultdict(dict)
    for u, s, st in tr_rows:
        tr[u][s] = st
    tmean = {u: statistics.mean(v.values()) for u, v in tr.items()}
    tsu = collections.defaultdict(set)
    for u, sm in tr.items():
        for s in sm:
            tsu[s].add(u)
    print(f"train {len(tr_rows):,} · test {len(te_rows):,} · 유저 {len(tr):,}")

    # 쌍별 겹침/일치 (train 기준)
    sh = collections.Counter()
    ag = collections.Counter()
    for s, U in tsu.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul = sorted(U)
        pr = {u: (1 if tr[u][s] >= tmean[u] else 0) for u in ul}
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                sh[(a, b)] += 1
                if pr[a] == pr[b]:
                    ag[(a, b)] += 1

    def raw(a, b):
        p = (a, b) if a < b else (b, a)
        n = sh.get(p, 0)
        if n == 0:
            return 0, 0.5
        return n, (ag[p] + 1.0) / (n + 2.0)

    cand = [(u, s, st) for u, s, st in te_rows
            if u in tmean and s in tsu and len(tsu[s] - {u}) >= 3]
    print(f"평가 대상 {len(cand):,}건\n")

    # 공통 실행기 --------------------------------------------------
    def evaluate(scorer, name, subset=None):
        """scorer(u, s) -> (예측값, 커버 여부)"""
        pool = subset if subset is not None else cand
        hit = tot = 0
        for u, s, st in pool:
            act = 1 if st >= tmean[u] else 0
            val, ok = scorer(u, s)
            if not ok:
                continue
            tot += 1
            hit += int((1 if val >= 0 else 0) == act)
        return name, hit, tot

    def baseline(u, s):
        att = [tr[v][s] - tmean[v] for v in tsu[s] if v != u]
        return (statistics.mean(att), True) if att else (0, False)

    # ── 1. 임계값 스윕 ────────────────────────────────────────
    print("■ 1. 겹침 임계값 스윕 (선호일치 가중)")
    print(f"  {'임계':>6} {'n':>7} {'커버':>7} {'취향가중':>22} {'같은표본 평판':>22}")
    best_th = None
    best_gain = -1
    for th in [3, 5, 8, 10, 15, 20, 30]:
        def sc(u, s, th=th):
            num = den = 0.0
            for v in tsu[s]:
                if v == u:
                    continue
                n, a = raw(u, v)
                if n < th:
                    continue
                w = (a - 0.5) * 2
                if w <= 0:
                    continue
                num += w * (tr[v][s] - tmean[v])
                den += w
            return (num / den, True) if den > 0 else (0, False)
        _, h, t = evaluate(sc, "")
        if t < 40:
            continue
        sub = [(u, s, st) for u, s, st in cand if sc(u, s)[1]]
        _, hb, tb = evaluate(baseline, "", sub)
        gain = (h / t - hb / tb) if t and tb else 0
        if gain > best_gain and t >= 200:
            best_gain, best_th = gain, th
        print(f"  {th:>4}곳+ {t:7,} {100*t/len(cand):6.1f}% "
              f"{fmt(h,t):>22} {fmt(hb,tb):>22}")
    print(f"  -> 최적 임계 {best_th}곳 (이득 {100*best_gain:+.1f}%p)\n")
    TH = best_th or 10

    # ── 2. 강한 링크만 2-hop 전파 ─────────────────────────────
    print(f"■ 2. 강한 링크({TH}곳+ & 일치 65%+)만 2-hop 전파")
    adj = collections.defaultdict(dict)
    for p, n in sh.items():
        if n < TH:
            continue
        a = (ag[p] + 1.0) / (n + 2.0)
        if a >= 0.65:
            w = (a - 0.5) * 2
            adj[p[0]][p[1]] = w
            adj[p[1]][p[0]] = w
    print(f"  강한 링크 {sum(len(v) for v in adj.values())//2:,}개 · 참여 {len(adj):,}명")

    def sim2(u, v):
        na = adj.get(u)
        nb = adj.get(v)
        if not na or not nb:
            return 0.0
        best = 0.0
        if len(na) > len(nb):
            na, nb = nb, na
        for m, w1 in na.items():
            w2 = nb.get(m)
            if w2:
                best = max(best, w1 * w2)
        return best

    def sc_hop2(u, s):
        num = den = 0.0
        for v in tsu[s]:
            if v == u:
                continue
            n, a = raw(u, v)
            w = (a - 0.5) * 2 if n >= TH else 0.0
            if w <= 0:
                w = sim2(u, v) * 0.6
            if w <= 0:
                continue
            num += w * (tr[v][s] - tmean[v])
            den += w
        return (num / den, True) if den > 0 else (0, False)

    _, h, t = evaluate(sc_hop2, "")
    sub = [(u, s, st) for u, s, st in cand if sc_hop2(u, s)[1]]
    _, hb, tb = evaluate(baseline, "", sub)
    print(f"  2-hop 포함 {t:,}건 ({100*t/len(cand):.1f}%) {fmt(h,t)}")
    print(f"  같은 표본 평판              {fmt(hb,tb)}\n")

    # ── 3. 비대칭(상반 이웃 반전) ─────────────────────────────
    print(f"■ 3. 취향 상반 이웃의 태도를 반전해 활용 (겹침 {TH}곳+)")
    def sc_anti(u, s):
        num = den = 0.0
        for v in tsu[s]:
            if v == u:
                continue
            n, a = raw(u, v)
            if n < TH:
                continue
            w = (a - 0.5) * 2          # 음수면 상반
            if abs(w) < 0.1:
                continue
            num += w * (tr[v][s] - tmean[v])
            den += abs(w)
        return (num / den, True) if den > 0 else (0, False)
    _, h, t = evaluate(sc_anti, "")
    sub = [(u, s, st) for u, s, st in cand if sc_anti(u, s)[1]]
    _, hb, tb = evaluate(baseline, "", sub)
    print(f"  반전 활용 {t:,}건 {fmt(h,t)} · 같은표본 평판 {fmt(hb,tb)}\n")

    # ── 4. 신뢰도 가중 ────────────────────────────────────────
    print("■ 4. 겹침 수로 신뢰도 스케일 (임계 없이 전 구간)")
    def sc_conf(u, s):
        num = den = 0.0
        for v in tsu[s]:
            if v == u:
                continue
            n, a = raw(u, v)
            if n == 0:
                continue
            conf = n / (n + 8.0)              # 겹침이 클수록 신뢰
            w = (a - 0.5) * 2 * conf
            if w <= 0:
                continue
            num += w * (tr[v][s] - tmean[v])
            den += w
        return (num / den, True) if den > 0 else (0, False)
    _, h, t = evaluate(sc_conf, "")
    sub = [(u, s, st) for u, s, st in cand if sc_conf(u, s)[1]]
    _, hb, tb = evaluate(baseline, "", sub)
    print(f"  신뢰도가중 {t:,}건 ({100*t/len(cand):.1f}%) {fmt(h,t)}")
    print(f"  같은 표본 평판             {fmt(hb,tb)}\n")

    # ── 5. 하이브리드 (취향 없으면 평판) ──────────────────────
    print(f"■ 5. 하이브리드 — 겹침 {TH}곳+ 이웃 있으면 취향, 없으면 평판")
    def sc_hybrid(u, s):
        num = den = 0.0
        for v in tsu[s]:
            if v == u:
                continue
            n, a = raw(u, v)
            if n < TH:
                continue
            w = (a - 0.5) * 2
            if w > 0:
                num += w * (tr[v][s] - tmean[v])
                den += w
        if den > 0:
            return num / den, True
        return baseline(u, s)
    _, h, t = evaluate(sc_hybrid, "")
    _, hb, tb = evaluate(baseline, "")
    print(f"  하이브리드 {t:,}건 (100%) {fmt(h,t)}")
    print(f"  전체 평판                {fmt(hb,tb)}\n")

    # ── 6. 확실한 태도만 ─────────────────────────────────────
    print(f"■ 6. 이웃의 태도가 명확한 경우만 (|태도| >= 0.5)")
    for th in [TH, 5]:
        def sc_strong(u, s, th=th):
            num = den = 0.0
            for v in tsu[s]:
                if v == u:
                    continue
                att = tr[v][s] - tmean[v]
                if abs(att) < 0.5:
                    continue
                n, a = raw(u, v)
                if n < th:
                    continue
                w = (a - 0.5) * 2
                if w <= 0:
                    continue
                num += w * att
                den += w
            return (num / den, True) if den > 0 else (0, False)
        _, h, t = evaluate(sc_strong, "")
        if t < 40:
            continue
        sub = [(u, s, st) for u, s, st in cand if sc_strong(u, s)[1]]
        _, hb, tb = evaluate(baseline, "", sub)
        print(f"  겹침{th}곳+ · 명확태도 {t:,}건 {fmt(h,t)} · 평판 {fmt(hb,tb)}")
    print()

    # ── 7. 최종 조합 ─────────────────────────────────────────
    print(f"■ 7. 최종 조합 (겹침 {TH}곳+ 취향 → 2-hop → 평판 폴백)")
    def sc_final(u, s):
        num = den = 0.0
        for v in tsu[s]:
            if v == u:
                continue
            n, a = raw(u, v)
            if n >= TH:
                w = (a - 0.5) * 2
                if w > 0:
                    att = tr[v][s] - tmean[v]
                    boost = 1.5 if abs(att) >= 0.5 else 1.0
                    num += w * boost * att
                    den += w * boost
        if den > 0:
            return num / den, True
        for v in tsu[s]:
            if v == u:
                continue
            w = sim2(u, v)
            if w > 0:
                num += w * (tr[v][s] - tmean[v])
                den += w
        if den > 0:
            return num / den, True
        return baseline(u, s)
    _, h, t = evaluate(sc_final, "")
    _, hb, tb = evaluate(baseline, "")
    print(f"  최종 {t:,}건 (100%) {fmt(h,t)}")
    print(f"  평판                {fmt(hb,tb)}")
    print(f"  개선 {100*(h/t - hb/tb):+.2f}%p")


if __name__ == "__main__":
    main()
