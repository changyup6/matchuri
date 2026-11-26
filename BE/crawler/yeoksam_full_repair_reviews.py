# -*- coding: utf-8 -*-
"""yeoksam_full 보수: panel3 의 review_count 가 0 이라 리뷰 호출을 건너뛴 가게를 다시 받는다.

원인(실측): panel3.kakaomap_review.score_set 이 일부 가게에서 0 으로 오는데
실제 리뷰 탭에는 리뷰가 있다. 즉 panel 집계는 스킵 판단 근거로 못 쓴다.
-> 리뷰 raw 가 없는 모든 가게에 대해 리뷰 탭을 무조건 1회 호출하고,
   탭의 score_set.review_count 가 20 을 넘으면 최신순도 받는다.

  python crawler/yeoksam_full_repair_reviews.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent
from kakao_client import KakaoMapClient

D = ROOT / "data" / "yeoksam_full"


def main() -> None:
    have = set()
    for l in (D / "reviews.jsonl").open(encoding="utf-8"):
        try:
            have.add(json.loads(l)["store_id"])
        except Exception:
            pass
    stores = []
    for l in (D / "panel.jsonl").open(encoding="utf-8"):
        try:
            stores.append(json.loads(l)["store_id"])
        except Exception:
            pass
    todo = [s for s in dict.fromkeys(stores) if s not in have]
    print(f"리뷰 raw 없는 가게 {len(todo):,}곳 재호출 (예상 {len(todo)*0.9/60:.0f}분)", flush=True)

    cl = KakaoMapClient(delay=0.45, jitter=0.3)
    got_stores = 0
    got_reviews = 0
    t0 = time.time()
    with (D / "reviews.jsonl").open("a", encoding="utf-8") as f:
        for i, sid in enumerate(todo, 1):
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                d = cl.reviews(sid, order="RECOMMENDED")
            except Exception as exc:
                print(f"  [!] {sid} {exc!r}", flush=True)
                continue
            if not d or not (d.get("reviews") or []):
                continue
            f.write(json.dumps({"store_id": sid, "order": "RECOMMENDED",
                                "fetched_at": now, "data": d}, ensure_ascii=False) + "\n")
            got_stores += 1
            got_reviews += len(d["reviews"])
            n = ((d.get("score_set") or {}).get("review_count") or 0)
            if n > 20:
                try:
                    d2 = cl.reviews(sid, order="LATEST")
                except Exception as exc:
                    print(f"  [!] {sid} LATEST {exc!r}", flush=True)
                    d2 = None
                if d2:
                    f.write(json.dumps({"store_id": sid, "order": "LATEST",
                                        "fetched_at": now, "data": d2},
                                       ensure_ascii=False) + "\n")
                    got_reviews += len(d2.get("reviews") or [])
            f.flush()
            if i % 100 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(todo)} · 리뷰 있던 가게 {got_stores} · 리뷰 {got_reviews:,} · "
                      f"남은 {(len(todo)-i)*el/i/60:.0f}분", flush=True)
    print(f"\n완료 · 리뷰 있던 가게 {got_stores:,} · 리뷰 {got_reviews:,}건 추가")


if __name__ == "__main__":
    main()
