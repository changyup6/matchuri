# -*- coding: utf-8 -*-
"""취향 커뮤니티가 존재하는가.

지금까지 확인된 것
  - 취향 신호는 공유 20곳+ 쌍에서만 명확하다(일치율 68%, 50곳+ 는 82%)
  - 약한 유사도를 여럿 모으는 방식(그룹 가중)은 오히려 평판보다 나쁘다

따라서 검증된 '강한 링크'만으로 그래프를 만들고 묻는다.
  1) 강한 링크가 삼각형을 이루는가(=전이성). 고립된 쌍만 있으면 커뮤니티가 아니다.
  2) 연결 성분이 뭉치는가, 잘게 흩어지는가.
  3) 커뮤니티가 있다면 그 안에서 예측이 실제로 좋아지는가.
  4) 그 커뮤니티는 '취향'인가, 그냥 '같은 동네'인가.   <- 핵심 반증 검사

  python analysis/taste_community.py --threshold 20
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"
MAX_STORE_USERS = 400


def build_pairs(us, uset):
    """공유 가게 수를 쌍별로 집계."""
    su = collections.defaultdict(set)
    for u, sm in us.items():
        for s in sm:
            su[s].add(u)
    cnt = collections.Counter()
    for s, U in su.items():
        if len(U) < 2 or len(U) > MAX_STORE_USERS:
            continue
        ul = sorted(U)
        for i in range(len(ul)):
            for j in range(i + 1, len(ul)):
                cnt[(ul[i], ul[j])] += 1
    return cnt, su


def components(adj, nodes):
    seen, comps = set(), []
    for n in nodes:
        if n in seen:
            continue
        stack, cur = [n], []
        seen.add(n)
        while stack:
            x = stack.pop()
            cur.append(x)
            for y in adj.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        comps.append(cur)
    return comps


def louvain_like(adj, weights, seed=7, rounds=12):
    """가벼운 라벨 전파 커뮤니티 검출(외부 의존 없이)."""
    rng = random.Random(seed)
    label = {n: i for i, n in enumerate(adj)}
    nodes = list(adj)
    for _ in range(rounds):
        rng.shuffle(nodes)
        changed = 0
        for n in nodes:
            if not adj[n]:
                continue
            score = collections.Counter()
            for m in adj[n]:
                score[label[m]] += weights.get((min(n, m), max(n, m)), 1)
            best = max(score.items(), key=lambda kv: kv[1])[0]
            if best != label[n]:
                label[n] = best
                changed += 1
        if changed == 0:
            break
    return label


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=20)
    args = ap.parse_args()
    TH = args.threshold

    us = collections.defaultdict(dict)
    for l in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("star"):
            us[r["user_hash"]][r["store_id"]] = r["star"]
    uset = {u: set(v) for u, v in us.items()}
    umean = {u: statistics.mean(v.values()) for u, v in us.items()}
    print(f"유저 {len(us):,}명")

    cnt, su = build_pairs(us, uset)
    strong = {p: c for p, c in cnt.items() if c >= TH}
    print(f"강한 링크(공유 {TH}곳+): {len(strong):,}개 / 전체 쌍 {len(cnt):,}개")

    adj = collections.defaultdict(set)
    for (a, b) in strong:
        adj[a].add(b)
        adj[b].add(a)
    nodes = list(adj)
    print(f"참여 유저 {len(nodes):,}명 ({100*len(nodes)/len(us):.1f}%)")
    if len(nodes) < 10:
        print("표본 부족")
        return

    # ── 1) 전이성(삼각형) ─────────────────────────────────────
    tri = 0
    open_tri = 0
    for n in nodes:
        nb = sorted(adj[n])
        for i in range(len(nb)):
            for j in range(i + 1, len(nb)):
                open_tri += 1
                if nb[j] in adj[nb[i]]:
                    tri += 1
    cc = tri / open_tri if open_tri else 0
    print(f"\n■ 전이성(군집계수) {cc:.4f}")
    print(f"  닫힌 삼각형 {tri//1:,} / 열린 삼각형 {open_tri:,}")
    # 무작위 그래프 기대치
    m = len(strong)
    n_nodes = len(nodes)
    p_rand = 2 * m / (n_nodes * (n_nodes - 1)) if n_nodes > 1 else 0
    print(f"  같은 밀도의 무작위 그래프 기대 군집계수 {p_rand:.4f}")
    print(f"  -> 실측/무작위 = {cc/p_rand:.1f}배" if p_rand > 0 else "")

    # ── 2) 연결 성분 ─────────────────────────────────────────
    comps = components(adj, nodes)
    comps.sort(key=len, reverse=True)
    sizes = [len(c) for c in comps]
    print(f"\n■ 연결 성분 {len(comps)}개")
    print(f"  최대 {sizes[0]}명 ({100*sizes[0]/len(nodes):.0f}%) · "
          f"상위5 {sizes[:5]} · 2명짜리 {sum(1 for s in sizes if s==2)}개")

    # ── 3) 커뮤니티 검출 + 내부 예측력 ────────────────────────
    label = louvain_like(adj, strong)
    groups = collections.defaultdict(list)
    for n, c in label.items():
        groups[c].append(n)
    big = sorted([g for g in groups.values() if len(g) >= 5], key=len, reverse=True)
    print(f"\n■ 커뮤니티(5명 이상) {len(big)}개 · 크기 {[len(g) for g in big[:8]]}")

    def agree_rate(pairs):
        """같은 가게에 대한 선호/비선호 일치율."""
        hit = tot = 0
        for a, b in pairs:
            common = uset[a] & uset[b]
            for s in common:
                pa = 1 if us[a][s] >= umean[a] else 0
                pb = 1 if us[b][s] >= umean[b] else 0
                tot += 1
                hit += int(pa == pb)
        return (hit / tot, tot) if tot else (0, 0)

    rng = random.Random(3)
    print(f"\n■ 커뮤니티 내부 vs 외부 선호 일치율")
    inside, outside = [], []
    for g in big[:12]:
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                inside.append((g[i], g[j]))
    allu = list(us)
    while len(outside) < len(inside) and len(outside) < 20000:
        a, b = rng.sample(allu, 2)
        if label.get(a) is not None and label.get(a) == label.get(b):
            continue
        if uset[a] & uset[b]:
            outside.append((a, b))
    ai, ni = agree_rate(inside[:20000])
    ao, no = agree_rate(outside)
    print(f"  같은 커뮤니티  {100*ai:.1f}%  (비교 {ni:,}건)")
    print(f"  다른 커뮤니티  {100*ao:.1f}%  (비교 {no:,}건)")
    print(f"  차이 {100*(ai-ao):+.1f}%p")

    # ── 4) 반증: 취향인가 지역인가 ────────────────────────────
    meta = {}
    p = DATA / "store_meta.jsonl"
    if p.exists():
        for l in p.open(encoding="utf-8"):
            m = json.loads(l)
            meta[m["store_id"]] = (m.get("region2"), m.get("cat2"))
    if meta:
        print(f"\n■ 반증 검사 — 커뮤니티는 '지역'인가 '취향'인가")

        def profile(u, idx):
            c = collections.Counter()
            for s in uset[u]:
                v = meta.get(s)
                if v and v[idx]:
                    c[v[idx]] += 1
            return c

        def prof_sim(a, b, idx):
            ca, cb = profile(a, idx), profile(b, idx)
            if not ca or not cb:
                return None
            keys = set(ca) | set(cb)
            va = [ca.get(k, 0) for k in keys]
            vb = [cb.get(k, 0) for k in keys]
            na = sum(x * x for x in va) ** .5
            nb = sum(x * x for x in vb) ** .5
            if na == 0 or nb == 0:
                return None
            return sum(x * y for x, y in zip(va, vb)) / (na * nb)

        for idx, name in [(0, "지역(구)"), (1, "음식 카테고리")]:
            ins = [prof_sim(a, b, idx) for a, b in inside[:3000]]
            ous = [prof_sim(a, b, idx) for a, b in outside[:3000]]
            ins = [x for x in ins if x is not None]
            ous = [x for x in ous if x is not None]
            if ins and ous:
                print(f"  {name:<12} 같은커뮤 {statistics.mean(ins):.3f} · "
                      f"다른커뮤 {statistics.mean(ous):.3f} · "
                      f"차이 {statistics.mean(ins)-statistics.mean(ous):+.3f}")
        print("  해석: 지역 유사도 차이가 크고 카테고리 차이가 작으면,")
        print("        커뮤니티는 취향이 아니라 '생활권'일 가능성이 높다.")


if __name__ == "__main__":
    main()
