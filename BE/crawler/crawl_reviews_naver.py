# -*- coding: utf-8 -*-
"""네이버 지도: 선정된 가게별 방문자 리뷰 상위 20개 수집.

네이버 visitorReviews API 는 정렬 파라미터를 무시하고 항상 최신순으로 내려준다.
따라서 '상위 20개' = 리뷰 탭 첫 화면에 노출되는 최신 리뷰 20개다.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import REVIEWS_PER_STORE
from naver_dates import parse as parse_date

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "naver"

CSV_COLS = [
    "source_platform",
    "review_id", "store_id", "store_name", "hub", "category", "category_full",
    "address", "rating", "content", "review_date", "visit_date", "photo_count",
    "like_count", "strength_tags", "author_nickname", "author_review_count",
    "visit_count", "view_count", "origin_type",
    "store_avg_rating", "store_review_count", "store_url",
]


def normalize(store: dict, payload: dict, limit: int) -> list[dict]:
    out = []
    for r in (payload.get("items") or []):
        body = (r.get("body") or "").strip()
        if not body:
            continue
        reactions = ((r.get("reactionStat") or {}).get("typeCount")) or []
        author = r.get("author") or {}
        out.append({
            "source_platform": "naver",
            "review_id": r.get("reviewId") or r.get("id"),
            "store_id": store["store_id"],
            "store_name": store["name"],
            "hub": store["hub"],
            "category": store["category"],
            "category_full": store["category_full"],
            "address": store["address"],
            "rating": r.get("rating"),
            "content": body,
            "review_date": parse_date(r.get("created")),
            "visit_date": parse_date(r.get("visited")),
            "photo_count": sum(1 for m in (r.get("media") or []) if m.get("type") == "image"),
            "like_count": sum(int(t.get("count") or 0) for t in reactions),
            "strength_tags": "|".join(k.get("name", "") for k in (r.get("votedKeywords") or []) if k.get("name")),
            "author_nickname": author.get("nickname"),
            "author_review_count": ((author.get("review") or {}).get("totalCount")),
            "visit_count": r.get("visitCount"),
            "view_count": r.get("viewCount"),
            "origin_type": r.get("originType"),
            "store_avg_rating": store["avg_rating"],
            "store_review_count": store["review_count"],
            "store_url": store["url"],
        })
        if len(out) >= limit:
            break
    return out


def main() -> None:
    from naver_client import NaverPlaceClient
    stores = json.loads((DATA / "stores.json").read_text(encoding="utf-8"))
    client = NaverPlaceClient()

    rows: list[dict] = []
    for i, s in enumerate(stores, 1):
        got: list[dict] = []
        seen: set = set()
        # 본문 없는 리뷰가 섞이므로 필요하면 다음 페이지까지 넘겨 20개를 채운다
        for page in range(1, 4):
            payload = client.visitor_reviews(s["store_id"], size=REVIEWS_PER_STORE + 10, page=page)
            if not payload:
                break
            for r in normalize(s, payload, REVIEWS_PER_STORE * 3):
                if r["review_id"] in seen:
                    continue
                seen.add(r["review_id"])
                got.append(r)
            if len(got) >= REVIEWS_PER_STORE:
                break

        got = got[:REVIEWS_PER_STORE]
        rows.extend(got)
        print(f"[{i:2}/{len(stores)}] {s['hub']:>3} {s['category']} | {s['name']:<24} 리뷰 {len(got):>2}개")
        sys.stdout.flush()

    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "reviews.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (DATA / "reviews.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        w.writerows(rows)
    with (DATA / "stores.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(stores[0].keys()))
        w.writeheader()
        w.writerows(stores)

    print(f"\n총 리뷰 {len(rows)}개 / 가게 {len(stores)}개 -> {DATA}")


if __name__ == "__main__":
    main()
