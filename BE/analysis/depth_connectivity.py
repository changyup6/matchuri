# -*- coding: utf-8 -*-
"""스노우볼이 깊어져도 관계가 유지되는가 — depth별 연결도 검증.

depth1 은 시드 가게에서 발견된 사람들이라 서로 겹치는 게 당연하다.
진짜 검증은 depth3(두 다리 건너)에서도 겹침이 유지되는지다.
유지되지 않으면 fan-out 규칙(공유 가게 우선)이 작동하지 않았다는 뜻.

  python analysis/depth_connectivity.py
"""
from __future__ import annotations

import collections
import itertools
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"


def main() -> None:
    users = {}
    for line in (DATA / "users.jsonl").open(encoding="utf-8"):
        u = json.loads(line)
        users[u["user_hash"]] = u

    user_stores: dict[str, set[str]] = collections.defaultdict(set)
    store_users: dict[str, set[str]] = collections.defaultdict(set)
    for line in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        user_stores[r["user_hash"]].add(r["store_id"])
        store_users[r["store_id"]].add(r["user_hash"])

    print(f"유저 {len(users):,}명 · 가게 {len(store_users):,}곳\n")

    # depth별 기본 통계
    print("■ depth별 수집 현황")
    by_depth = collections.defaultdict(list)
    for h, u in users.items():
        by_depth[u["depth"]].append(h)
    for d in sorted(by_depth):
        hs = by_depth[d]
        rc = [len(user_stores[h]) for h in hs]
        print(f"  depth{d}: {len(hs):4}명 · 유저당 가게 중앙 {statistics.median(rc):.0f}곳")

    # 연결도: 자기 depth 안에서 / 전체에 대해
    shared = {s: us for s, us in store_users.items() if len(us) >= 2}
    print(f"\n■ 2명 이상 공유 가게: {len(shared):,}곳 / 전체 {len(store_users):,}곳")

    neighbors: dict[str, set[str]] = collections.defaultdict(set)
    for s, us in shared.items():
        if len(us) > 400:
            continue
        for a, b in itertools.combinations(us, 2):
            neighbors[a].add(b)
            neighbors[b].add(a)

    print("\n■ depth별 연결도 (몇 명과 가게를 공유하는가)")
    print(f"  {'depth':>6} {'n':>5} {'고립':>5} {'연결률':>7} {'평균이웃':>9} {'중앙이웃':>9}")
    for d in sorted(by_depth):
        hs = by_depth[d]
        degs = [len(neighbors[h]) for h in hs]
        iso = sum(1 for x in degs if x == 0)
        print(f"  {d:>6} {len(hs):5} {iso:5} {100*(len(hs)-iso)/len(hs):6.1f}% "
              f"{statistics.mean(degs):9.1f} {statistics.median(degs):9.0f}")

    # depth 간 교차 연결 (같은 depth끼리만 뭉치는지, 서로 섞이는지)
    print("\n■ depth 간 교차 연결 (edge 양쪽의 depth 조합)")
    cross = collections.Counter()
    for a, nbrs in neighbors.items():
        for b in nbrs:
            if a < b:
                da, db = users[a]["depth"], users[b]["depth"]
                cross[tuple(sorted((da, db)))] += 1
    total = sum(cross.values())
    for k, v in sorted(cross.items()):
        print(f"  depth{k[0]}-depth{k[1]}: {v:8,}쌍 ({100*v/total:5.1f}%)")

    # 희소 가게 기준 연결 (진짜 취향 신호)
    print("\n■ 희소 가게(리뷰어 5명 이하)에서만 겹치는 경우")
    rare_nb: dict[str, set[str]] = collections.defaultdict(set)
    for s, us in shared.items():
        if len(us) > 5:
            continue
        for a, b in itertools.combinations(us, 2):
            rare_nb[a].add(b)
            rare_nb[b].add(a)
    for d in sorted(by_depth):
        hs = by_depth[d]
        degs = [len(rare_nb[h]) for h in hs]
        iso = sum(1 for x in degs if x == 0)
        print(f"  depth{d}: 연결률 {100*(len(hs)-iso)/len(hs):5.1f}% · 평균이웃 {statistics.mean(degs):5.1f}")


if __name__ == "__main__":
    main()
