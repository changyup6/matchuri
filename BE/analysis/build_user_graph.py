# -*- coding: utf-8 -*-
"""수집한 user x store 데이터로 관계 그래프를 만든다.

  python analysis/build_user_graph.py

산출
  stores.jsonl  가게별 집계(우리 데이터 안에서의 리뷰어 수 포함)
  edges.jsonl   유저 쌍 + 공유 가게 목록  <- 취향 연관성 분석의 입력
  graph_stats.json  요약 통계
"""
from __future__ import annotations

import collections
import itertools
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "user_graph"

# 공유 가게가 이보다 적으면 edge 로 치지 않는다.
# 1곳만 겹치는 건 우연일 수 있어 취향 유사도로 보기 약하다.
MIN_SHARED = 1
# 이 인원 이상이 리뷰한 가게는 유사도 계산에서 가중치를 낮춘다(체인점/랜드마크 효과).
POPULAR_CUTOFF = 50


def main() -> None:
    users = [json.loads(l) for l in (DATA / "users.jsonl").open(encoding="utf-8")]
    reviews = [json.loads(l) for l in (DATA / "reviews.jsonl").open(encoding="utf-8")]
    print(f"유저 {len(users):,}명 · 리뷰 {len(reviews):,}건")

    user_stores: dict[str, set[str]] = collections.defaultdict(set)
    store_users: dict[str, set[str]] = collections.defaultdict(set)
    store_name: dict[str, str] = {}
    star_of: dict[tuple[str, str], int] = {}

    for r in reviews:
        h, sid = r["user_hash"], r["store_id"]
        user_stores[h].add(sid)
        store_users[sid].add(h)
        if r.get("store_name"):
            store_name[sid] = r["store_name"]
        if r.get("star"):
            star_of[(h, sid)] = r["star"]

    # ── 가게 집계 ──────────────────────────────────────────────
    with (DATA / "stores.jsonl").open("w", encoding="utf-8") as f:
        for sid, us in store_users.items():
            stars = [star_of[(u, sid)] for u in us if (u, sid) in star_of]
            f.write(json.dumps({
                "store_id": sid,
                "store_name": store_name.get(sid),
                "reviewer_count": len(us),
                "avg_star": round(statistics.mean(stars), 2) if stars else None,
                "star_stdev": round(statistics.stdev(stars), 2) if len(stars) > 1 else None,
            }, ensure_ascii=False) + "\n")

    shared_stores = {s: u for s, u in store_users.items() if len(u) >= 2}
    print(f"고유 가게 {len(store_users):,}곳 · 2명+ 공유 가게 {len(shared_stores):,}곳")

    # ── edge 생성 ─────────────────────────────────────────────
    # 가게 단위로 조합을 만든다. 공유 가게에서만 만들므로 O(전체 유저^2)를 피한다.
    pair_stores: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for sid, us in shared_stores.items():
        if len(us) > 400:      # 지나치게 큰 가게는 조합 폭발 방지
            continue
        for a, b in itertools.combinations(sorted(us), 2):
            pair_stores[(a, b)].append(sid)

    n_edges = 0
    with (DATA / "edges.jsonl").open("w", encoding="utf-8") as f:
        for (a, b), sids in pair_stores.items():
            if len(sids) < MIN_SHARED:
                continue
            # 자카드 유사도: 취향 겹침의 기본 지표
            ua, ub = user_stores[a], user_stores[b]
            jacc = len(ua & ub) / len(ua | ub)
            # 희소 가게일수록 가중(같은 동네 체인점보다 의미 있는 신호)
            w = sum(1.0 / max(1, len(store_users[s]) / POPULAR_CUTOFF) for s in sids)
            # 별점이 둘 다 있는 가게에서 평가가 일치하는 정도
            both = [(star_of[(a, s)], star_of[(b, s)]) for s in sids
                    if (a, s) in star_of and (b, s) in star_of]
            agree = (round(statistics.mean(1 - abs(x - y) / 4 for x, y in both), 3)
                     if both else None)
            f.write(json.dumps({
                "user_a": a, "user_b": b,
                "shared_count": len(sids),
                "jaccard": round(jacc, 4),
                "weighted_score": round(w, 3),
                "star_agreement": agree,
                "shared_stores": sids[:20],
            }, ensure_ascii=False) + "\n")
            n_edges += 1
    print(f"edge {n_edges:,}개 생성")

    # ── 요약 ──────────────────────────────────────────────────
    deg = collections.Counter()
    for (a, b) in pair_stores:
        deg[a] += 1
        deg[b] += 1
    connected = len(deg)
    stats = {
        "users": len(users),
        "reviews": len(reviews),
        "unique_stores": len(store_users),
        "shared_stores": len(shared_stores),
        "edges": n_edges,
        "users_with_edge": connected,
        "isolated_users": len(users) - connected,
        "avg_degree": round(statistics.mean(deg.values()), 2) if deg else 0,
        "median_degree": statistics.median(deg.values()) if deg else 0,
        "avg_reviews_per_user": round(len(reviews) / max(1, len(users)), 1),
        "depth_dist": dict(collections.Counter(u["depth"] for u in users)),
    }
    (DATA / "graph_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n■ 그래프 요약")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if connected < len(users):
        print(f"\n  ! 고립 유저 {len(users)-connected}명 — 아무와도 가게를 공유하지 않음")


if __name__ == "__main__":
    main()
