# -*- coding: utf-8 -*-
"""사람(리뷰어) 기준 스노우볼 크롤링 -> user x store 이분 그래프.

  python crawler/crawl_user_graph.py --seed-only    # 시드 가게만 선정
  python crawler/crawl_user_graph.py                # 전체 실행(이어받기 지원)

설계 요지
  - 유저를 크롤하면 그 사람의 '전체' 리뷰(=가게 목록)를 얻는다. 이게 취향 프로파일.
  - 가게를 크롤하면 그 가게 리뷰어들을 얻는다. 이게 신규 유저 발견 경로.
  - 다음 depth로 넘길 가게를 고를 때, **이미 수집한 유저들이 많이 리뷰한 가게**를
    우선한다. 그래야 새 유저가 기존 유저와 겹쳐 edge 가 생긴다.
  - 개인 식별자는 저장하지 않는다. map_user_id 는 해시로 치환하고 닉네임/프로필
    이미지는 수집하지 않는다. 원본 id 는 중복 체크용으로 메모리에만 둔다.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent

from kakao_client import KakaoMapClient
from kakao_user_client import KakaoUserClient
from user_graph_config import (CATEGORIES, DELAY, DEPTH_BUDGET, FANOUT, HUBS,
                               JITTER, MAX_DEPTH, MAX_REVIEWS,
                               MAX_VELOCITY_PER_MONTH, MIN_REVIEWS,
                               TARGET_USERS, VELOCITY_MIN_SAMPLE)

OUT = ROOT / "data" / "user_graph"
SALT = "pop_review_2026"      # 해시 고정용. 같은 유저 = 같은 해시(재실행 간 일관)


def uhash(uid: str) -> str:
    return hashlib.sha256((SALT + str(uid)).encode()).hexdigest()[:16]


def velocity(reviews: list[dict]) -> float | None:
    """월평균 리뷰 작성 수. 표본이 적으면 None(판단 보류)."""
    if len(reviews) < VELOCITY_MIN_SAMPLE:
        return None
    ds = []
    for r in reviews:
        try:
            ds.append(dt.datetime.strptime(r["date"][:10], "%Y-%m-%d"))
        except Exception:
            pass
    if len(ds) < VELOCITY_MIN_SAMPLE:
        return None
    ds.sort()
    span = max(1, (ds[-1] - ds[0]).days)
    return len(ds) / span * 30.0


# ─────────────────────────────────────────────────────────── 시드
def build_seeds(client: KakaoMapClient) -> list[dict]:
    seeds, seen = [], set()
    for hub in HUBS:
        for cat in CATEGORIES:
            try:
                places = client.search_places(f"{hub} {cat}")
            except Exception as exc:
                print(f"  [!] 검색 실패 {hub} {cat}: {exc!r}", flush=True)
                continue
            for p in places:
                pid = str(p.get("confirmid") or "")
                if (not pid or pid in seen
                        or p.get("cate_name_depth1") != "음식점"
                        or p.get("cate_name_depth2") != cat):
                    continue
                seen.add(pid)
                seeds.append({"store_id": pid, "name": p.get("name"),
                              "hub": hub, "category": cat,
                              "address": p.get("address_disp")})
                break          # 조합당 1곳
            print(f"  시드 {hub} {cat} -> {seeds[-1]['name'] if seeds else '없음'}", flush=True)
    return seeds


def users_of_store(place_client: KakaoMapClient, store_id: str) -> list[dict]:
    """가게 리뷰에서 작성자 목록을 뽑는다(추천순+최신순 합집합)."""
    found = {}
    for order in ("RECOMMENDED", "LATEST"):
        try:
            d = place_client.reviews(store_id, order=order)
        except Exception:
            continue
        for r in (d or {}).get("reviews") or []:
            o = (r.get("meta") or {}).get("owner") or {}
            uid = o.get("map_user_id")
            if uid:
                found[str(uid)] = int(o.get("review_count") or 0)
    return [{"uid": u, "count": c} for u, c in found.items()]


# ─────────────────────────────────────────────────────── 메인 루프
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--target", type=int, default=TARGET_USERS)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    seed_path = OUT / "seeds.json"

    place_client = KakaoMapClient(delay=DELAY, jitter=JITTER)
    user_client = KakaoUserClient(delay=DELAY, jitter=JITTER)

    # 1) 시드
    if seed_path.exists():
        seeds = json.loads(seed_path.read_text(encoding="utf-8"))
        print(f"시드 재사용: {len(seeds)}곳")
    else:
        print(f"시드 선정: {len(HUBS)}거점 x {len(CATEGORIES)}카테고리")
        seeds = build_seeds(place_client)
        seed_path.write_text(json.dumps(seeds, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"시드 {len(seeds)}곳 -> {seed_path}")
    if args.seed_only:
        return

    # 2) 이어받기 상태 복원
    users_path = OUT / "users.jsonl"
    reviews_path = OUT / "reviews.jsonl"
    done_uids: set[str] = set()
    done_by_depth: collections.Counter = collections.Counter()
    user_stores: dict[str, set[str]] = {}      # uhash -> {store_id}
    store_users: dict[str, set[str]] = collections.defaultdict(set)
    if users_path.exists():
        for line in users_path.open(encoding="utf-8"):
            u = json.loads(line)
            done_uids.add(u["_raw_uid"])
            done_by_depth[u.get("depth", 1)] += 1
        print(f"이어받기: 이미 {len(done_uids)}명 수집됨 (depth별 {dict(done_by_depth)})")
    if reviews_path.exists():
        for line in reviews_path.open(encoding="utf-8"):
            r = json.loads(line)
            user_stores.setdefault(r["user_hash"], set()).add(r["store_id"])
            store_users[r["store_id"]].add(r["user_hash"])

    # 3) depth 1 프론티어: 시드 가게의 리뷰어
    print("\n[depth 0] 시드 가게에서 유저 발견")
    frontier: list[str] = []
    seen_uids: set[str] = set(done_uids)
    for i, s in enumerate(seeds, 1):
        for u in users_of_store(place_client, s["store_id"]):
            if u["uid"] in seen_uids:
                continue
            if MIN_REVIEWS <= u["count"] <= MAX_REVIEWS:
                seen_uids.add(u["uid"])
                frontier.append(u["uid"])
        if i % 10 == 0:
            print(f"  가게 {i}/{len(seeds)} · 후보 유저 {len(frontier)}명", flush=True)
    print(f"  -> depth1 후보 {len(frontier)}명")

    fu = users_path.open("a", encoding="utf-8")
    fr = reviews_path.open("a", encoding="utf-8")
    t0 = time.time()
    collected = len(done_uids)
    skipped = collections.Counter()

    try:
        for depth in range(1, MAX_DEPTH + 1):
            # 이어받기: 이 depth 에서 이미 수집한 만큼 예산을 깎는다
            budget = DEPTH_BUDGET.get(depth, 0) - done_by_depth.get(depth, 0)
            if not frontier or budget <= 0 or collected >= args.target:
                if budget <= 0 and DEPTH_BUDGET.get(depth, 0) > 0:
                    print(f"\n[depth {depth}] 이미 예산 소진(수집 {done_by_depth.get(depth,0)}명) — 건너뜀")
                    continue
                break
            print(f"\n[depth {depth}] 예산 {budget}명(잔여) · 프론티어 {len(frontier)}명")
            next_store_pool: collections.Counter = collections.Counter()
            taken = 0

            for uid in frontier:
                if taken >= budget or collected >= args.target:
                    break
                try:
                    revs = user_client.reviews(uid, max_reviews=MAX_REVIEWS)
                except Exception as exc:
                    skipped["error"] += 1
                    print(f"  [!] {uid} 실패 {exc!r}", flush=True)
                    continue
                if revs is None:
                    skipped["private"] += 1
                    continue
                n = len(revs)
                if not (MIN_REVIEWS <= n <= MAX_REVIEWS):
                    skipped["count"] += 1
                    continue
                v = velocity(revs)
                if v is not None and v > MAX_VELOCITY_PER_MONTH:
                    skipped["velocity"] += 1
                    continue

                h = uhash(uid)
                stars = [r["star"] for r in revs if r.get("star")]
                ds = sorted(r["date"][:10] for r in revs if r.get("date"))
                fu.write(json.dumps({
                    "_raw_uid": uid,          # 이어받기 전용(공유 전 제거 대상)
                    "user_hash": h, "depth": depth, "review_count": n,
                    "velocity_per_month": round(v, 2) if v else None,
                    "avg_star": round(sum(stars)/len(stars), 2) if stars else None,
                    "first_review": ds[0] if ds else None,
                    "last_review": ds[-1] if ds else None,
                }, ensure_ascii=False) + "\n")

                sids = set()
                for r in revs:
                    sid = str(r.get("confirmId") or "")
                    if not sid:
                        continue
                    sids.add(sid)
                    fr.write(json.dumps({
                        "user_hash": h, "store_id": sid,
                        "store_name": r.get("name"),
                        "review_id": r.get("reviewId"),
                        "star": r.get("star"), "date": r.get("date"),
                        "contents": (r.get("contents") or "").strip(),
                        "like_count": r.get("likeCount") or 0,
                        "revisit": bool(r.get("revisit")),
                        "photo_count": len(r.get("photos") or []),
                        "depth": depth,
                    }, ensure_ascii=False) + "\n")
                fu.flush(); fr.flush()

                user_stores[h] = sids
                for sid in sids:
                    store_users[sid].add(h)
                    next_store_pool[sid] += 1

                collected += 1
                taken += 1
                if collected % 25 == 0:
                    el = time.time() - t0
                    rate = (collected - len(done_uids)) / max(el, 1)
                    left = (args.target - collected) / max(rate, 1e-9)
                    print(f"  유저 {collected}/{args.target} · 리뷰 누적 "
                          f"{sum(len(v) for v in user_stores.values()):,}건 · "
                          f"API {user_client.calls + place_client.__dict__.get('calls', 0):,}콜 · "
                          f"남은예상 {left/60:.0f}분", flush=True)

            # 다음 depth 프론티어 구성
            fan = FANOUT.get(depth, 0)
            if fan <= 0 or depth >= MAX_DEPTH:
                frontier = []
                continue
            # 관계 밀도 우선: 우리 유저가 많이 리뷰한 가게부터.
            # 이번 라운드(next_store_pool)만이 아니라 **지금까지 수집한 전체 유저**
            # 기준으로 고른다. 재시작해도 이전 라운드 정보를 살리고, 여러 유저가
            # 겹치는 가게일수록 신규 유저도 기존 유저와 겹칠 확률이 높다.
            global_pool = collections.Counter(
                {sid: len(us) for sid, us in store_users.items() if len(us) >= 2})
            for sid, c in next_store_pool.items():
                global_pool[sid] = max(global_pool.get(sid, 0), c)
            hot = [sid for sid, _ in global_pool.most_common()]
            print(f"  다음 depth 후보 가게 {len(hot)}곳 중 상위 {fan * taken}곳 탐색")
            new_frontier: list[str] = []
            for sid in hot[: fan * max(taken, 1)]:
                for u in users_of_store(place_client, sid):
                    if u["uid"] in seen_uids:
                        continue
                    if MIN_REVIEWS <= u["count"] <= MAX_REVIEWS:
                        seen_uids.add(u["uid"])
                        new_frontier.append(u["uid"])
                if len(new_frontier) >= DEPTH_BUDGET.get(depth + 1, 0) * 2:
                    break
            frontier = new_frontier
            print(f"  -> depth{depth+1} 프론티어 {len(frontier)}명")
    finally:
        fu.close(); fr.close()

    el = time.time() - t0
    print(f"\n완료: 유저 {collected}명 · {el/60:.0f}분")
    print(f"  건너뜀: {dict(skipped)}")
    print(f"  -> {OUT}")


if __name__ == "__main__":
    main()
