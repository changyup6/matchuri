# -*- coding: utf-8 -*-
"""네이버 지도: 거점 x 카테고리별 '상위 가게' 1곳씩 선정.

선정 규칙
  1) 네이버 플레이스 검색 랭킹(광고 제외) 순서대로 후보를 훑는다.
  2) 세부 업종이 해당 대분류 허용 목록(NAVER_CATEGORY_ALLOW)에 있어야 한다.
  3) 방문자 리뷰 중 본문 있는 것을 MIN_REVIEWS(20)개 이상 확보할 수 있어야 한다.
  4) 조건을 만족하는 첫 번째 가게를 선정한다. (전체 중복 제거)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import CATEGORIES, HUBS, MIN_REVIEWS, NAVER_CATEGORY_ALLOW
from naver_client import NaverPlaceClient

OUT = Path(__file__).resolve().parents[1] / "data" / "naver" / "stores.json"


def _int(v) -> int:
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def pick(client: NaverPlaceClient, hub: dict, category: str, taken: set[str]) -> dict | None:
    allow = NAVER_CATEGORY_ALLOW[category]
    query = f"{hub['query']} {category}"
    for rank, p in enumerate(client.search_places(query), start=1):
        pid = str(p.get("id") or "")
        if not pid or pid in taken or p.get("category") not in allow:
            continue
        if _int(p.get("visitorReviewCount")) < MIN_REVIEWS:
            continue

        rev = client.visitor_reviews(pid, size=MIN_REVIEWS + 10)
        if not rev:
            continue
        n_text = sum(1 for r in (rev.get("items") or []) if (r.get("body") or "").strip())
        if n_text < MIN_REVIEWS:
            continue

        taken.add(pid)
        return {
            "source_platform": "naver", "store_id": pid,
            "hub": hub["key"],
            "hub_query": hub["query"],
            "category": category,
            "search_query": query,
            "search_rank": rank,
            "name": p.get("name"),
            "category_full": p.get("category"),
            "address": p.get("address"),
            "road_address": p.get("roadAddress"),
            "tel": p.get("phone") or p.get("virtualPhone"),
            "lat": p.get("y"),
            "lon": p.get("x"),
            "review_count": _int(p.get("visitorReviewCount")),
            "avg_rating": p.get("visitorReviewScore"),
            "url": f"https://map.naver.com/p/entry/place/{pid}",
        }
    return None


def main() -> None:
    client = NaverPlaceClient()
    stores: list[dict] = []
    taken: set[str] = set()
    for hub in HUBS:
        for cat in CATEGORIES:
            s = pick(client, hub, cat, taken)
            if s:
                stores.append(s)
                print(f"[OK]   {hub['key']:>3} / {cat} -> {s['name']} "
                      f"({s['category_full']}, 리뷰 {s['review_count']}, 평점 {s['avg_rating']}, 검색순위 {s['search_rank']})")
            else:
                print(f"[MISS] {hub['key']:>3} / {cat} -> 조건 만족 가게 없음")
            sys.stdout.flush()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(stores, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n총 {len(stores)}개 가게 선정 -> {OUT}")


if __name__ == "__main__":
    main()
