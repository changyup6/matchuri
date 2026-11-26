# -*- coding: utf-8 -*-
"""선정된 가게별로 카카오맵 상위(추천순) 리뷰 20개씩 수집한다."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import REVIEWS_PER_STORE

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "kakao"

CSV_COLS = [
    "source_platform",
    "review_id", "store_id", "store_name", "hub", "category", "category_full",
    "address", "rating", "content", "review_date", "photo_count", "like_count",
    "strength_tags", "author_nickname", "author_review_count", "author_avg_rating",
    "store_avg_rating", "store_review_count", "store_url",
]


def normalize(store: dict, payload: dict, limit: int) -> list[dict]:
    tag_name = {s["id"]: s["name"] for s in (payload.get("strength_description") or [])}
    score = payload.get("score_set") or {}
    out = []
    for r in (payload.get("reviews") or [])[:limit]:
        meta = r.get("meta") or {}
        owner = meta.get("owner") or {}
        content = (r.get("contents") or "").strip()
        if not content:
            continue
        out.append({
            "source_platform": "kakao",
            "review_id": r.get("review_id"),
            "store_id": store["store_id"],
            "store_name": store["name"],
            "hub": store["hub"],
            "category": store["category"],
            "category_full": store["category_full"],
            "address": store["address"],
            "rating": r.get("star_rating"),
            "content": content,
            "review_date": r.get("registered_at"),
            "photo_count": r.get("photo_count") or 0,
            "like_count": meta.get("like_count") or 0,
            "strength_tags": "|".join(tag_name.get(i, str(i)) for i in (r.get("strength_ids") or [])),
            "author_nickname": owner.get("nickname"),
            "author_review_count": owner.get("review_count"),
            "author_avg_rating": owner.get("average_score"),
            "store_avg_rating": score.get("average_score"),
            "store_review_count": score.get("review_count"),
            "store_url": store["url"],
        })
    return out


def main() -> None:
    from kakao_client import KakaoMapClient
    stores = json.loads((DATA / "stores.json").read_text(encoding="utf-8"))
    client = KakaoMapClient()

    rows: list[dict] = []
    for i, s in enumerate(stores, 1):
        payload = client.reviews(s["store_id"], order="RECOMMENDED")
        got = normalize(s, payload or {}, REVIEWS_PER_STORE) if payload else []

        # 본문이 빈 리뷰가 섞여 20개에 못 미치면 최신순으로 보충
        if len(got) < REVIEWS_PER_STORE:
            extra = client.reviews(s["store_id"], order="LATEST")
            seen = {r["review_id"] for r in got}
            for r in normalize(s, extra or {}, 100):
                if r["review_id"] not in seen:
                    got.append(r)
                    seen.add(r["review_id"])
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
