# -*- coding: utf-8 -*-
"""가게 카테고리/지역 수집 (취향 벡터의 축을 만들기 위한 것).

가게 단위 유사도는 밀도 0.09% 라 노이즈다. 카테고리(20~30종)로 압축하면
밀도가 거의 100% 가 되어 유사도를 안정적으로 추정할 수 있다.

대상: 리뷰어 N명 이상인 가게(기본 3명). 1명만 리뷰한 가게는 edge 를 만들지
않으므로 상관 분석에 불필요하다.

응답이 66KB 로 무거우므로 필요한 필드만 뽑아 저장한다.
이어받기 지원 — 이미 수집한 store_id 는 건너뛴다.

  python crawler/crawl_store_meta.py --min-reviewers 3
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

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PANEL = "https://place-api.map.kakao.com/places/panel3/{pid}"

DATA = ROOT / "data" / "user_graph"
OUT = DATA / "store_meta.jsonl"


class MetaClient:
    def __init__(self, delay: float = 1.5, jitter: float = 0.7, retries: int = 3):
        import random
        self._random = random
        self.delay, self.jitter, self.retries = delay, jitter, retries
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA, "Referer": "https://place.map.kakao.com/",
            "pf": "web", "Origin": "https://place.map.kakao.com",
            "Accept": "application/json", "Accept-Language": "ko-KR,ko;q=0.9",
        })
        self.calls = 0

    def fetch(self, pid: str) -> dict | None:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.s.get(PANEL.format(pid=pid), timeout=25)
                self.calls += 1
                if r.status_code == 200:
                    time.sleep(self.delay + self._random.random() * self.jitter)
                    return r.json()
                if r.status_code in (403, 404):
                    time.sleep(self.delay)
                    return None
                last = f"HTTP {r.status_code}"
            except Exception as exc:
                last = repr(exc)
            time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"실패 {pid}: {last}")


def slim(pid: str, d: dict) -> dict:
    s = d.get("summary") or {}
    c = s.get("category") or {}
    regions = [x.get("name") for x in (s.get("regions") or [])]
    score = ((d.get("kakaomap_review") or {}).get("score_set") or {})
    return {
        "store_id": pid,
        "name": s.get("name"),
        "cat1": c.get("name1"),          # 음식점 / 카페 / 기타
        "cat2": c.get("name2"),          # 한식 / 일식 / 간식 ...
        "cat3": c.get("name3"),          # 감자탕 / 제과,베이커리 ...
        "region1": regions[0] if len(regions) > 0 else None,
        "region2": regions[1] if len(regions) > 1 else None,
        "region3": regions[2] if len(regions) > 2 else None,
        "kakao_review_count": score.get("review_count"),
        "kakao_avg_score": score.get("average_score"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-reviewers", type=int, default=3)
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    # 대상 선정
    store_users: dict[str, set[str]] = collections.defaultdict(set)
    for line in (DATA / "reviews.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        store_users[r["store_id"]].add(r["user_hash"])
    targets = [s for s, u in store_users.items() if len(u) >= args.min_reviewers]
    targets.sort(key=lambda s: -len(store_users[s]))     # 많이 공유된 가게부터
    print(f"대상 {len(targets):,}곳 (리뷰어 {args.min_reviewers}명 이상)")

    done: set[str] = set()
    if OUT.exists():
        for line in OUT.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["store_id"])
            except Exception:
                pass
        print(f"이어받기: 이미 {len(done):,}곳")
    todo = [s for s in targets if s not in done]
    print(f"남은 {len(todo):,}곳 · 예상 {len(todo)*(args.delay+0.85)/3600:.1f}시간")

    cl = MetaClient(delay=args.delay)
    t0 = time.time()
    ok = fail = 0
    with OUT.open("a", encoding="utf-8") as f:
        for i, pid in enumerate(todo, 1):
            try:
                d = cl.fetch(pid)
            except Exception as exc:
                print(f"  [!] {pid} {exc!r}", flush=True)
                fail += 1
                continue
            if not d:
                fail += 1
                continue
            f.write(json.dumps(slim(pid, d), ensure_ascii=False) + "\n")
            f.flush()
            ok += 1
            if i % 200 == 0:
                el = time.time() - t0
                print(f"  {i}/{len(todo)} · 성공 {ok} 실패 {fail} · "
                      f"남은 {(len(todo)-i)*el/i/60:.0f}분", flush=True)

    print(f"\n완료: 성공 {ok:,} 실패 {fail:,} · {(time.time()-t0)/60:.0f}분 -> {OUT}")


if __name__ == "__main__":
    main()
