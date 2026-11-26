# -*- coding: utf-8 -*-
"""역삼동 음식점 전수 수집.

검색 API 특성(실측)
  - place_totalcount 는 신뢰할 수 없다(역삼동 음식점 8,333 이라 주장하나 실제 ~2,450).
  - 검색어 하나당 페이지네이션은 ~34페이지(약 500곳)에서 끊긴다.
  - '역삼동 X' 로 검색해도 강남구 밖(서초구 등)이 22% 섞인다.

따라서
  1) 검색어를 여러 개로 쪼개 합집합을 만들고
  2) 주소에 '역삼동' 이 있는 것만 남기고
  3) cat1 이 '음식점' 인 것만 남긴다.

  python crawler/yeoksam_stores.py
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "yeoksam"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 실측 포화 곡선상 12개에서 +103 까지 둔화. 여유를 두고 확장.
QUERIES = [
    "음식점", "맛집", "한식", "일식", "중식", "양식", "카페", "술집",
    "분식", "치킨", "고기", "간식", "아시아음식", "해산물", "국수",
    "돈까스", "김밥", "베이커리", "포차", "이자카야", "파스타", "햄버거",
    "곱창", "찌개", "덮밥", "샐러드", "브런치", "디저트", "족발", "회",
]
REGION = "역삼동"


class Search:
    def __init__(self, delay=0.45, jitter=0.25, retries=3):
        self.delay, self.jitter, self.retries = delay, jitter, retries
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA,
                               "Referer": "https://map.kakao.com/"})
        self.calls = 0

    def page(self, q, p):
        for a in range(self.retries):
            try:
                r = self.s.get("https://search.map.kakao.com/mapsearch/map.daum",
                               params={"q": q, "msFlag": "A", "sort": 0, "page": p},
                               timeout=20)
                self.calls += 1
                if r.status_code == 200:
                    time.sleep(self.delay + random.random() * self.jitter)
                    return r.json().get("place") or []
            except Exception:
                pass
            time.sleep(1.5 * (a + 1))
        return []

    def harvest(self, q, maxpage=40):
        out = {}
        for p in range(1, maxpage + 1):
            pl = self.page(q, p)
            if not pl:
                break
            for x in pl:
                cid = x.get("confirmid")
                if cid:
                    out[cid] = x
        return out


def slim(x: dict) -> dict:
    return {
        "store_id": x.get("confirmid"),
        "name": x.get("name"),
        "cat1": x.get("cate_name_depth1"),
        "cat2": x.get("cate_name_depth2"),
        "cat3": x.get("cate_name_depth3"),
        "address": x.get("address_disp"),
        "road_address": x.get("new_address_disp"),
        "tel": x.get("tel"),
        "lat": x.get("lat"), "lon": x.get("lon"),
        "blog_review_count": x.get("reviewCount"),
        "rating": x.get("kplace_rating"),
        "rating_count": x.get("kplace_ratings_count"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.45)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "stores.jsonl"

    sr = Search(delay=args.delay)
    raw: dict[str, dict] = {}
    t0 = time.time()
    prev_kept = 0

    print(f"검색어 {len(QUERIES)}개 · 지역 필터 '{REGION}' · cat1='음식점'")
    for i, q in enumerate(QUERIES, 1):
        raw.update(sr.harvest(f"{REGION} {q}"))
        kept = {k: v for k, v in raw.items()
                if REGION in (v.get("address_disp") or "")
                and v.get("cate_name_depth1") == "음식점"}
        gain = len(kept) - prev_kept
        prev_kept = len(kept)
        print(f"  [{i:2}/{len(QUERIES)}] {q:<8} 원본누적 {len(raw):>5,} · "
              f"역삼동음식점 {len(kept):>5,} (+{gain}) · API {sr.calls:,}", flush=True)

    final = {k: slim(v) for k, v in raw.items()
             if REGION in (v.get("address_disp") or "")
             and v.get("cate_name_depth1") == "음식점"}
    with path.open("w", encoding="utf-8") as f:
        for v in final.values():
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    el = time.time() - t0
    print(f"\n완료 {el/60:.0f}분 · API {sr.calls:,}콜")
    print(f"원본 {len(raw):,}곳 -> 역삼동 음식점 {len(final):,}곳 "
          f"(필터로 {100*(1-len(final)/len(raw)):.0f}% 제거)")
    c2 = collections.Counter(v["cat2"] for v in final.values())
    print(f"\n카테고리 분포:")
    for k, v in c2.most_common(15):
        print(f"  {k:<14} {v:>5,}곳 ({100*v/len(final):4.1f}%)")
    withr = sum(1 for v in final.values() if v.get("rating_count"))
    print(f"\n별점 보유 가게: {withr:,}곳 ({100*withr/len(final):.0f}%)")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
