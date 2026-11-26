# -*- coding: utf-8 -*-
"""쌍(pair) 대신 그룹(group)으로 취향 추론.

문제의식
  쌍 단위는 50곳+ 공유가 전체의 0.1% 뿐이라 적용 범위가 좁다.
  하지만 한 가게를 리뷰한 사람은 많다. 유사도가 약한 이웃이라도 여럿을 모아
  가중합하면 개별 노이즈가 상쇄되어 신호가 남을 수 있다.

설계
  held-out 인 (유저 u, 가게 s) 각각에 대해
    그룹 = train 에서 s 를 리뷰한 다른 유저들
    각 이웃 v 의 '이 가게에 대한 태도' = (v 의 s 별점 - v 의 평균)   ← 개인 후함 보정
    예측 = 이웃들의 태도를 sim(u,v) 로 가중평균
  sim 은 s 를 제외한 train 이력으로만 계산(누수 차단).

비교 대상
  (a) 무가중 평균  = 그냥 '이 가게가 평이 좋은가' = 가게 평판
  (b) 유사도 가중  = 취향 반영
  (b) 가 (a) 를 이겨야 '취향이 평판 위에 기여한다'가 증명된다.

  python analysis/group_pref.py
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
SEED = 7


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

    us: dict[str, dict[str, int]] = collections.defaultdict(dict)
    su: dict[str, set[str]] = collections.defaultdict(set)
    for u, s, st in train:
        us[u][s] = st
        su[s].add(u)
    umean = {u: statistics.mean(v.values()) for u, v in us.items()}
    uset = {u: set(v) for u, v in us.items()}
    print(f"train {len(train):,} · test {len(test):,} · 유저 {len(us):,}")

    # 평가 대상: train 에 그 가게가 있고 이웃이 있는 경우
    cand = [(u, s, st) for u, s, st in test
            if u in us and s in su and len(su[s] - {u}) >= 1]
    print(f"평가 가능 {len(cand):,}건 ({100*len(cand)/len(test):.1f}%)\n")

    def sim(a: str, b: str, drop: str) -> float:
        A = uset[a] - {drop}
        B = uset[b] - {drop}
        if not A or not B:
            return 0.0
        inter = len(A & B)
        if inter == 0:
            return 0.0
        return inter / len(A | B)          # 자카드

    # 그룹 크기별로 나눠서 평가
    buckets = collections.defaultdict(lambda: {"n": 0,
                                               "hit_w": 0, "hit_u": 0,
                                               "simsum": []})
    for u, s, st in cand:
        nbrs = [v for v in su[s] if v != u]
        actual = 1 if st >= umean.get(u, 3.9) else 0

        # (a) 무가중 = 가게 평판
        att = [(us[v][s] - umean[v]) for v in nbrs]
        unweighted = statistics.mean(att)

        # (b) 유사도 가중
        num = den = 0.0
        sims = []
        for v, a_v in zip(nbrs, att):
            w = sim(u, v, s)
            if w <= 0:
                continue
            sims.append(w)
            num += w * a_v
            den += w
        weighted = (num / den) if den > 0 else unweighted

        g = len(nbrs)
        band = ('2-4명' if g < 5 else '5-9명' if g < 10 else
                '10-29명' if g < 30 else '30-99명' if g < 100 else '100명+')
        if g == 1:
            band = '1명'
        b = buckets[band]
        b["n"] += 1
        b["hit_u"] += int((1 if unweighted >= 0 else 0) == actual)
        b["hit_w"] += int((1 if weighted >= 0 else 0) == actual)
        if sims:
            b["simsum"].append(len(sims))

    order = ['1명', '2-4명', '5-9명', '10-29명', '30-99명', '100명+']
    print("■ 그룹 크기별 선호/비선호 예측 정확도")
    print(f"  {'그룹':>8} {'n':>8} {'평판만':>8} {'취향가중':>9} {'차이':>7} {'유효이웃':>8}")
    tot_n = tot_u = tot_w = 0
    for band in order:
        b = buckets.get(band)
        if not b or b["n"] < 30:
            continue
        n, hu, hw = b["n"], b["hit_u"], b["hit_w"]
        tot_n += n
        tot_u += hu
        tot_w += hw
        ns = statistics.mean(b["simsum"]) if b["simsum"] else 0
        print(f"  {band:>8} {n:8,} {100*hu/n:7.1f}% {100*hw/n:8.1f}% "
              f"{100*(hw-hu)/n:+7.2f}% {ns:8.1f}")
    if tot_n:
        lo_u, hi_u = wilson(tot_u, tot_n)
        lo_w, hi_w = wilson(tot_w, tot_n)
        print(f"\n  {'전체':>8} {tot_n:8,} {100*tot_u/tot_n:7.1f}% "
              f"{100*tot_w/tot_n:8.1f}% {100*(tot_w-tot_u)/tot_n:+7.2f}%")
        print(f"    평판만  95%CI [{100*lo_u:.1f}, {100*hi_u:.1f}]")
        print(f"    취향가중 95%CI [{100*lo_w:.1f}, {100*hi_w:.1f}]")

    # ── 이웃 중 '유사도 상위 k명'만 쓸 때 ──────────────────────
    print("\n■ 그룹에서 유사도 상위 k명만 사용")
    print(f"  {'k':>5} {'n':>8} {'평판만':>8} {'취향가중':>9} {'차이':>7}")
    big = [(u, s, st) for u, s, st in cand if len(su[s] - {u}) >= 10]
    print(f"  (이웃 10명+ 인 {len(big):,}건 대상)")
    for topk in [3, 5, 10, 20, 0]:
        hu = hw = n = 0
        for u, s, st in big:
            nbrs = [v for v in su[s] if v != u]
            actual = 1 if st >= umean.get(u, 3.9) else 0
            scored = []
            for v in nbrs:
                w = sim(u, v, s)
                if w > 0:
                    scored.append((w, us[v][s] - umean[v]))
            if not scored:
                continue
            scored.sort(reverse=True)
            use = scored[:topk] if topk else scored
            num = sum(w * a for w, a in use)
            den = sum(w for w, _ in use)
            weighted = num / den if den else 0.0
            unw = statistics.mean([us[v][s] - umean[v] for v in nbrs])
            n += 1
            hu += int((1 if unw >= 0 else 0) == actual)
            hw += int((1 if weighted >= 0 else 0) == actual)
        if n >= 30:
            lab = f"top{topk}" if topk else "전체"
            print(f"  {lab:>5} {n:8,} {100*hu/n:7.1f}% {100*hw/n:8.1f}% "
                  f"{100*(hw-hu)/n:+7.2f}%")


if __name__ == "__main__":
    main()
