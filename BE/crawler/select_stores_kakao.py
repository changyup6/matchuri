# -*- coding: utf-8 -*-
"""거점 x 카테고리별로 '상위 가게' 1곳씩 선정한다.

선정 규칙
  1) 카카오맵 검색 기본 정렬(정확도/인기) 순서를 그대로 따라 후보를 훑는다.
  2) 카테고리 2depth 가 요청한 카테고리와 일치해야 한다.
  3) 추천순 첫 페이지에서 본문 있는 후기를 MIN_REVIEWS(20)개 이상 확보할 수 있어야 한다.
  4) 조건을 만족하는 첫 번째 가게를 선정한다. (전체 중복 제거)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import CATEGORIES, HUBS, MIN_REVIEWS, SEARCH_PAGES
from kakao_client import KakaoMapClient

OUT = Path(__file__).resolve().parents[1] / "data" / "kakao" / "stores.json"


def pick(client: KakaoMapClient, hub: dict, category: str, taken: set[str]) -> dict | None:
    query = f"{hub['query']} {category}"
    for page in range(1, SEARCH_PAGES + 1):
        for rank, p in enumerate(client.search_places(query, page=page), start=(page - 1) * 15 + 1):
            pid = str(p.get("confirmid") or "")
            if not pid or pid in taken:
                continue
            if p.get("cate_name_depth1") != "음식점" or p.get("cate_name_depth2") != category:
                continue

            rev = client.reviews(pid)
            if not rev:
                continue
            score = rev.get("score_set") or {}
            n_review = int(score.get("review_count") or 0)
            n_text = sum(1 for r in (rev.get("reviews") or []) if (r.get("contents") or "").strip())
            if n_review < MIN_REVIEWS or n_text < MIN_REVIEWS:
                continue

            taken.add(pid)
            return {
                "source_platform": "kakao", "store_id": pid,
                "hub": hub["key"],
                "hub_query": hub["query"],
                "category": category,
                "search_query": query,
                "search_rank": rank,
                "name": p.get("name"),
                "category_full": " > ".join(
                    x for x in [p.get("cate_name_depth1"), p.get("cate_name_depth2"), p.get("cate_name_depth3")] if x
                ),
                "address": p.get("address_disp") or p.get("address"),
                "road_address": p.get("new_address_disp") or p.get("new_address"),
                "tel": p.get("tel"),
                "lat": p.get("lat"),
                "lon": p.get("lon"),
                "review_count": n_review,
                "avg_rating": score.get("average_score"),
                "url": f"https://place.map.kakao.com/{pid}",
            }
    return None


def main() -> None:
    client = KakaoMapClient()
    stores: list[dict] = []
    taken: set[str] = set()
    for hub in HUBS:
        for cat in CATEGORIES:
            s = pick(client, hub, cat, taken)
            if s:
                stores.append(s)
                print(f"[OK]   {hub['key']:>3} / {cat} -> {s['name']} "
                      f"(후기 {s['review_count']}, 평점 {s['avg_rating']}, 검색순위 {s['search_rank']})")
            else:
                print(f"[MISS] {hub['key']:>3} / {cat} -> 조건 만족 가게 없음")
            sys.stdout.flush()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(stores, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n총 {len(stores)}개 가게 선정 -> {OUT}")


if __name__ == "__main__":
    main()
