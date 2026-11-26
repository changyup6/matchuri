# -*- coding: utf-8 -*-
"""역삼동 음식점 전체(2,801곳)의 카카오맵 리뷰 수집.

검색 API 의 rating/rating_count 는 전부 0 이라 쓸 수 없다(실측).
가게별 리뷰 API 를 호출해야 평점·리뷰수·리뷰 본문이 나온다.

  GET place-api.map.kakao.com/places/tab/reviews/kakaomap/{id}
      ?order=RECOMMENDED&only_photo_review=false
  -> score_set(리뷰수·평균평점·강점태그) + reviews(최대 20건)

리뷰가 없는 가게도 store_stats 에 0 으로 기록한다(전수 확보가 목표).
추천순/최신순을 모두 받아 합집합으로 리뷰를 최대화한다.
이어받기 지원.

  python crawler/yeoksam_reviews.py
  python crawler/yeoksam_reviews.py --both-orders   # 추천순+최신순(느리지만 리뷰 2배)
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent

from kakao_client import KakaoMapClient

DATA = ROOT / "data" / "yeoksam"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--both-orders", action="store_true",
                    help="추천순+최신순 둘 다 받아 리뷰 합집합(호출 2배)")
    args = ap.parse_args()

    stores = [json.loads(l) for l in (DATA / "stores.jsonl").open(encoding="utf-8")]
    meta = {s["store_id"]: s for s in stores}
    print(f"대상 {len(stores):,}곳 · {'추천순+최신순' if args.both_orders else '추천순만'}")

    stats_path = DATA / "store_stats.jsonl"
    rev_path = DATA / "reviews.jsonl"

    done: set[str] = set()
    if stats_path.exists():
        for l in stats_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(l)["store_id"])
            except Exception:
                pass
        print(f"이어받기: 이미 {len(done):,}곳")

    todo = [s for s in stores if s["store_id"] not in done]
    per = 2 if args.both_orders else 1
    print(f"남은 {len(todo):,}곳 · 예상 {len(todo)*per*(args.delay+0.6)/60:.0f}분\n")

    cl = KakaoMapClient(delay=args.delay, jitter=0.4)
    t0 = time.time()
    n_rev = 0
    n_empty = 0
    fails = 0

    fs = stats_path.open("a", encoding="utf-8")
    fr = rev_path.open("a", encoding="utf-8")
    try:
        for i, s in enumerate(todo, 1):
            sid = s["store_id"]
            orders = ("RECOMMENDED", "LATEST") if args.both_orders else ("RECOMMENDED",)
            seen: set = set()
            score = None
            tags: dict = {}
            got = 0
            ok = False
            for order in orders:
                try:
                    d = cl.reviews(sid, order=order)
                except Exception as exc:
                    print(f"  [!] {sid} {exc!r}", flush=True)
                    continue
                if not d:
                    continue
                ok = True
                if score is None:
                    score = d.get("score_set") or {}
                    tags = {t["id"]: t["name"]
                            for t in (d.get("strength_description") or [])}
                for r in d.get("reviews") or []:
                    rid = r.get("review_id")
                    if rid in seen:
                        continue
                    seen.add(rid)
                    m = r.get("meta") or {}
                    o = m.get("owner") or {}
                    fr.write(json.dumps({
                        "store_id": sid,
                        "store_name": s.get("name"),
                        "cat2": s.get("cat2"), "cat3": s.get("cat3"),
                        "review_id": rid,
                        "rating": r.get("star_rating"),
                        "content": (r.get("contents") or "").strip(),
                        "date": r.get("registered_at"),
                        "photo_count": r.get("photo_count") or 0,
                        # 원본 URL 만 저장한다. 실제 이미지는 장당 약 1.5MB 라
                        # 전량(추정 10~15만 장) 받으면 220GB/20시간이 든다.
                        # URL 은 영구적이므로 필요할 때 선별해서 받으면 된다.
                        "photo_urls": [ph.get("url") for ph in (r.get("photos") or [])
                                       if ph.get("url")],
                        "like_count": m.get("like_count") or 0,
                        "strength_tags": "|".join(
                            tags.get(x, str(x)) for x in (r.get("strength_ids") or [])),
                        "author_id": o.get("map_user_id"),
                        "author_review_count": o.get("review_count"),
                        "author_avg_score": o.get("average_score"),
                    }, ensure_ascii=False) + "\n")
                    got += 1

            if not ok:
                fails += 1
            sc = score or {}
            if got == 0:
                n_empty += 1
            n_rev += got
            fs.write(json.dumps({
                "store_id": sid,
                "name": s.get("name"),
                "cat2": s.get("cat2"), "cat3": s.get("cat3"),
                "address": s.get("address"),
                "lat": s.get("lat"), "lon": s.get("lon"),
                "blog_review_count": s.get("blog_review_count"),
                "kakao_review_count": sc.get("review_count") or 0,
                "kakao_avg_score": sc.get("average_score"),
                "reviews_collected": got,
            }, ensure_ascii=False) + "\n")
            fs.flush()
            fr.flush()

            if i % 100 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(todo)} · 리뷰 {n_rev:,}건 · 리뷰없음 {n_empty} · "
                      f"실패 {fails} · 남은 {(len(todo)-i)*el/i/60:.0f}분", flush=True)
    finally:
        fs.close()
        fr.close()

    el = time.time() - t0
    print(f"\n완료 {el/60:.0f}분 · 리뷰 {n_rev:,}건 · 리뷰없는 가게 {n_empty:,} · 실패 {fails}")
    print(f"-> {stats_path}\n-> {rev_path}")


if __name__ == "__main__":
    main()
