# -*- coding: utf-8 -*-
"""강남/역삼 음식점 전수 목록 수집 — 볼륨 파악 겸 실제 수집.

검색 API 제약
  place_totalcount 는 수만으로 나오지만 실제 페이지네이션은 ~34페이지(약 500곳)에서
  끊긴다. 따라서 '동 x 카테고리' 로 검색어를 잘게 쪼개 합집합을 만든다.
  역삼동 실측: 검색어 10개로 3,740곳 확보(단일 검색 500곳 대비 7.5배).

  python crawler/gangnam_volume.py            # 전체
  python crawler/gangnam_volume.py --dongs 3  # 앞 3개 동만
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "gangnam"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DONGS = ["역삼동", "논현동", "삼성동", "대치동", "청담동", "신사동",
         "압구정동", "도곡동", "개포동", "일원동", "수서동", "세곡동"]
# 검색어를 늘릴수록 커버리지가 오른다(중복은 자동 제거).
CATS = ["음식점", "맛집", "한식", "일식", "중식", "양식", "카페", "술집",
        "분식", "치킨", "고기", "아시아음식", "간식", "해산물", "국수",
        "돈까스", "샐러드", "베이커리", "바", "포차"]


class Search:
    def __init__(self, delay=0.45, jitter=0.25, retries=3):
        self.delay, self.jitter, self.retries = delay, jitter, retries
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": "https://map.kakao.com/"})
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
                if not cid:
                    continue
                out[cid] = {
                    "store_id": cid,
                    "name": x.get("name"),
                    "cat1": x.get("cate_name_depth1"),
                    "cat2": x.get("cate_name_depth2"),
                    "cat3": x.get("cate_name_depth3"),
                    "address": x.get("address_disp"),
                    "road_address": x.get("new_address_disp"),
                    "tel": x.get("tel"),
                    "lat": x.get("lat"), "lon": x.get("lon"),
                    "review_count_raw": x.get("reviewCount"),
                    "rating": x.get("kplace_rating"),
                    "rating_n": x.get("kplace_ratings_count"),
                }
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dongs", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.45)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "stores.jsonl"

    have = {}
    if path.exists():
        for l in path.open(encoding="utf-8"):
            try:
                r = json.loads(l)
                have[r["store_id"]] = r
            except Exception:
                pass
        print(f"이어받기: 기존 {len(have):,}곳")

    dongs = DONGS[:args.dongs] if args.dongs else DONGS
    sr = Search(delay=args.delay)
    t0 = time.time()
    per_dong = {}

    for di, dong in enumerate(dongs, 1):
        before = len(have)
        for ci, c in enumerate(CATS, 1):
            got = sr.harvest(f"{dong} {c}")
            have.update(got)
            print(f"  [{di}/{len(dongs)}] {dong} {c:<8} +{len(got):>4} "
                  f"누적 {len(have):,} (API {sr.calls:,})", flush=True)
        per_dong[dong] = len(have) - before
        # 매 동마다 저장
        with path.open("w", encoding="utf-8") as f:
            for v in have.values():
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        print(f"  -> {dong} 완료 · 신규 {per_dong[dong]:,} · 저장 {len(have):,}곳", flush=True)

    el = time.time() - t0
    food = [v for v in have.values() if v.get("cat1") == "음식점"]
    print(f"\n완료 {el/60:.0f}분 · API {sr.calls:,}콜")
    print(f"전체 {len(have):,}곳 · 음식점 {len(food):,}곳")
    c2 = collections.Counter(v.get("cat2") for v in food)
    print(f"카테고리 상위: {dict(c2.most_common(12))}")
    print(f"동별 신규: {per_dong}")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
