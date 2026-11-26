# -*- coding: utf-8 -*-
"""수집 원본(parsed) -> 서비스 런타임 데이터(슬림).

parsed 테이블은 177MB 라 저장소에 넣기엔 크다. 서버가 실제로 쓰는 필드만 남기고
가게당 사진 수를 제한해 ~50MB 로 줄인다. 원본은 재수집 없이 언제든 다시 빌드된다.

  photos    148,380장 -> 가게당 15장(우선순위 순), 필드 4개      76MB -> 7MB
  reviews   전량 유지, 사진은 URL 6장까지, 메타 제거             30MB -> 17MB
  stores    전량 유지 (사람이 안 보는 필드만 제거)                8.8MB -> 6MB
  menus     전량 유지 (mod_at 등 제거)                           18MB -> 13MB
  blog      런타임 미사용 -> 제외

  python scripts/build_runtime_data.py --out BE/data
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "yeoksam_full" / "parsed"

# 카드 사진 우선순위: 공식(매장주·업체) -> 음식 -> 분위기 -> 외관 -> 메뉴판.
# '후기'(사람 사진 섞임) 태그는 수집 단계에서 이미 제외됐다.
PHOTO_PRIORITY = ["MYSTORE", "VENDOR", "FOOD", "INDOOR", "OUTDOOR", "MENU"]
PHOTOS_PER_STORE = 15
REVIEW_PHOTOS = 6

# 런타임에 쓰지 않는 가게 필드(원본에는 남아 있다)
STORE_DROP = {"roadview_url", "navi_guides", "visitor_uv", "ai_sheets",
              "payments", "region", "menu_updated_at", "trend_rank_card",
              "address_road", "zone_no", "fetched_at", "status"}


def jsonl(p: Path):
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write(path: Path, rows) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    src, out = Path(args.src), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    n = write(out / "stores.jsonl",
              ({"source_platform": "kakao", **{k: v for k, v in s.items() if k not in STORE_DROP}}
               for s in jsonl(src / "stores.jsonl")))
    print(f"  stores  {n:,}곳")

    # 카카오는 '가격 없음'을 -1(변동/미표기) 또는 0(미입력)으로 보낸다. 값이 아니라
    # 결측이므로 여기서 None 으로 바꾼다(전체 메뉴의 44%).
    def price(m):
        p = m.get("price")
        return p if isinstance(p, int) and not isinstance(p, bool) and p > 0 else None

    n = write(out / "menus.jsonl", (
        {"source_platform": m.get("source_platform", "kakao"), "store_id": m["store_id"], "menu_index": m["menu_index"],
         "menu_name": m["menu_name"], "price": price(m),
         "price_text": m["price_text"], "is_recommend": m["is_recommend"],
         "description": m["description"], "photo_url": m["photo_url"]}
        for m in jsonl(src / "menus.jsonl")))
    print(f"  menus   {n:,}개")

    n = write(out / "reviews.jsonl", (
        {"source_platform": r.get("source_platform", "kakao"), "store_id": r["store_id"], "review_id": r["review_id"],
         "rating": r["rating"], "content": r["content"], "date": r["date"],
         "photo_count": r["photo_count"],
         "photo_urls": [p["url"] for p in (r.get("photos") or [])
                        if p.get("url")][:REVIEW_PHOTOS],
         "strength_tags": r["strength_tags"], "like_count": r["like_count"],
         "author_id": r["author_id"],
         "author_review_count": r["author_review_count"],
         "author_avg_score": r["author_avg_score"]}
        for r in jsonl(src / "reviews.jsonl")))
    print(f"  reviews {n:,}건")

    rank = {t: i for i, t in enumerate(PHOTO_PRIORITY)}
    by = collections.defaultdict(list)
    for p in jsonl(src / "photos.jsonl"):
        by[p["store_id"]].append(p)
    rows = []
    for sid, ps in by.items():
        ps.sort(key=lambda x: rank.get(x.get("tag"), 99))
        seen = set()
        for p in ps:
            if not p.get("url") or p["url"] in seen:
                continue
            seen.add(p["url"])
            rows.append({"source_platform": p.get("source_platform", "kakao"), "store_id": sid, "tag": p["tag"],
                         "type": p["type"], "url": p["url"]})
            if len(seen) >= PHOTOS_PER_STORE:
                break
    n = write(out / "photos.jsonl", rows)
    print(f"  photos  {n:,}장 ({len(by):,}곳)")

    total = sum(f.stat().st_size for f in out.glob("*.jsonl"))
    print(f"-> {out}  ({total/1048576:.1f} MB)")


if __name__ == "__main__":
    main()
