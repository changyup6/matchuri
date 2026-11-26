# -*- coding: utf-8 -*-
"""취향 유사도 = 겹침 x 선호 일치.

이전 정의의 오류
  유사도를 '공유 가게 수'로만 쟀다. 같은 동네 사람은 같은 가게를 많이 가므로
  겹침은 크지만 취향은 정반대일 수 있다. 실제로 커뮤니티 검출 결과가
  '취향'이 아니라 '생활권'으로 나온 원인이 이것이다.

새 정의
  두 사람이 함께 간 가게들에서 '선호/비선호가 같았던 비율'을 유사도로 쓴다.
    agree(a,b) = P(pref_a(s) == pref_b(s))  over s in shared
  선호 = 별점 >= 본인 평균 (개인 후함/박함 보정)

  겹침이 적으면 agree 가 불안정하므로 라플라스 평활 + 최소 겹침 조건을 둔다.

핵심 검증
  이 유사도로 뽑은 이웃이 **다른(보류한) 가게**의 선호를 예측하는가.
  겹침 기반과 정면 비교한다.

  python analysis/pref_agreement_sim.py
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


def main() -> None:
    rng = random.Random(SEED)

    # ── 학습/검증 분할 (리뷰 단위) ─────────────────────────────
    rows = []
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star"):
            rows.append((r["user_hash"], r["store_id"], r["star"]))
    rng.shuffle(rows)
    k = int(len(rows) * 0.8)
    train, test = rows[:k], rows[k:]

    tr: dict[str, dict[str, int]] = collections.defaultdict(dict)
    for u, s, st in train:
        tr[u][s] = st
    umean = {u: statistics.mean(v.values()) for u, v in tr.items()}
    tset = {u: set(v) for u, v in tr.items()}

    def pref(u, s, table):
        return 1 if table[u][s] >= umean[u] else 0

    su_tr = collections.defaultdict(set)
    for u, sm in tr.items():
        for s in sm:
            su_tr[s].add(u)
    print(f"train {len(train):,} · test {len(test):,} · 유저 {len(tr):,}")

    # ── 쌍별: 겹침 수 + 선호 일치율 ────────────────────────────
    pair_shared = collections.Counter()
    pair_agree = collections.Counter()
    for s, U in su_tr.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul = sorted(U)
        pr = {u: pref(u, s, tr) for u in ul}
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                a, b = ul[i], ul[j]
                pair_shared[(a, b)] += 1
                if pr[a] == pr[b]:
                    pair_agree[(a, b)] += 1
    print(f"쌍 {len(pair_shared):,}개\n")

    # 라플라스 평활: 겹침이 적을 때 0/1 로 튀는 것을 막음
    def agree_sim(p):
        n = pair_shared[p]
        a = pair_agree[p]
        return (a + 1.0) / (n + 2.0)          # 0.5 로 수축

    # ── 두 유사도의 성격 비교 ─────────────────────────────────
    print("■ 두 유사도가 실제로 다른가")
    for th in [5, 10, 20]:
        ps = [p for p, n in pair_shared.items() if n >= th]
        if len(ps) < 30:
            continue
        ag = [pair_agree[p] / pair_shared[p] for p in ps]
        print(f"  겹침 {th}곳+ 쌍 {len(ps):,}개 · 선호일치율 "
              f"평균 {statistics.mean(ag):.3f} · "
              f"최저 {min(ag):.2f} 최고 {max(ag):.2f} · "
              f"일치율<0.5 인 쌍 {100*sum(1 for x in ag if x<0.5)/len(ag):.0f}%")
    print("  -> 겹침이 커도 선호 일치율은 넓게 퍼진다 = 서로 다른 정보")

    # ── 본 검증: held-out 가게 선호 예측 ──────────────────────
    # test 의 (u,s): train 에서 s 를 리뷰한 이웃들의 태도를
    #   (A) 겹침 가중  (B) 선호일치 가중  (C) 무가중(평판)  으로 종합
    cand = [(u, s, st) for u, s, st in test
            if u in tr and s in su_tr and len(su_tr[s] - {u}) >= 3]
    print(f"\n평가 대상 {len(cand):,}건 (이웃 3명+)")

    def run(min_shared, weight_mode, topk=0):
        hit = tot = 0
        for u, s, st in cand:
            actual = 1 if st >= umean[u] else 0
            scored = []
            for v in su_tr[s]:
                if v == u:
                    continue
                p = (u, v) if u < v else (v, u)
                n_sh = pair_shared.get(p, 0)
                if n_sh < min_shared:
                    continue
                if weight_mode == "overlap":
                    w = n_sh
                elif weight_mode == "agree":
                    a = agree_sim(p)
                    w = max(0.0, (a - 0.5) * 2) * n_sh   # 일치율 0.5 이하는 배제
                else:
                    w = 1.0
                if w <= 0:
                    continue
                scored.append((w, tr[v][s] - umean[v]))
            if not scored:
                continue
            scored.sort(reverse=True)
            use = scored[:topk] if topk else scored
            den = sum(w for w, _ in use)
            if den <= 0:
                continue
            val = sum(w * a for w, a in use) / den
            tot += 1
            hit += int((1 if val >= 0 else 0) == actual)
        return hit, tot

    print("\n■ held-out 가게 선호 예측 정확도")
    print(f"  {'최소겹침':>8} {'방식':>10} {'n':>8} {'정확도':>8} {'95%CI':>16}")
    for ms in [1, 3, 5, 10]:
        for mode, lab in [("none", "평판(무가중)"), ("overlap", "겹침가중"),
                          ("agree", "선호일치가중")]:
            h, t = run(ms, mode)
            if t < 50:
                continue
            lo, hi = wilson(h, t)
            print(f"  {ms:>6}곳+ {lab:>10} {t:8,} {100*h/t:7.1f}% "
                  f"[{100*lo:5.1f},{100*hi:5.1f}]")
        print()

    # ── 상위 k 이웃만 ─────────────────────────────────────────
    print("■ 선호일치 가중, 상위 k 이웃만 사용 (최소겹침 5곳)")
    print(f"  {'k':>5} {'n':>8} {'정확도':>8}")
    for topk in [1, 3, 5, 10, 0]:
        h, t = run(5, "agree", topk)
        if t < 50:
            continue
        print(f"  {('top'+str(topk)) if topk else '전체':>5} {t:8,} {100*h/t:7.1f}%")

    # ── 커뮤니티 재검출: 선호일치 기반 강한 링크 ───────────────
    print("\n■ 취향 커뮤니티 재검출 (겹침 10곳+ AND 선호일치 70%+)")
    strong = {p for p, n in pair_shared.items()
              if n >= 10 and pair_agree[p] / n >= 0.70}
    anti = {p for p, n in pair_shared.items()
            if n >= 10 and pair_agree[p] / n <= 0.35}
    print(f"  취향 일치 링크 {len(strong):,}개 · 취향 상반 링크 {len(anti):,}개")

    meta = {}
    p = DATA / "store_meta.jsonl"
    if p.exists():
        for l in p.open(encoding="utf-8"):
            m = json.loads(l)
            meta[m["store_id"]] = (m.get("region2"), m.get("cat2"))

    def prof_sim(a, b, idx):
        ca = collections.Counter()
        cb = collections.Counter()
        for s in tset[a]:
            v = meta.get(s)
            if v and v[idx]:
                ca[v[idx]] += 1
        for s in tset[b]:
            v = meta.get(s)
            if v and v[idx]:
                cb[v[idx]] += 1
        if not ca or not cb:
            return None
        keys = set(ca) | set(cb)
        va = [ca.get(x, 0) for x in keys]
        vb = [cb.get(x, 0) for x in keys]
        na = sum(x * x for x in va) ** .5
        nb = sum(x * x for x in vb) ** .5
        if na == 0 or nb == 0:
            return None
        return sum(x * y for x, y in zip(va, vb)) / (na * nb)

    if meta and strong and anti:
        print("\n  반증: 취향 일치 링크 vs 취향 상반 링크 (둘 다 겹침 10곳+)")
        for idx, name in [(0, "지역(구)"), (1, "카테고리")]:
            s1 = [prof_sim(a, b, idx) for a, b in list(strong)[:2000]]
            s2 = [prof_sim(a, b, idx) for a, b in list(anti)[:2000]]
            s1 = [x for x in s1 if x is not None]
            s2 = [x for x in s2 if x is not None]
            if s1 and s2:
                print(f"    {name:<10} 일치링크 {statistics.mean(s1):.3f} · "
                      f"상반링크 {statistics.mean(s2):.3f} · "
                      f"차이 {statistics.mean(s1)-statistics.mean(s2):+.3f}")
        print("    해석: 겹침을 동일하게 맞췄으므로 지역 효과는 통제됨.")
        print("          여기서 차이가 나면 그건 순수 취향 차이다.")


if __name__ == "__main__":
    main()
