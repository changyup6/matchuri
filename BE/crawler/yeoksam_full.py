# -*- coding: utf-8 -*-
"""역삼동 음식점 전수 — 카카오맵에서 가져올 수 있는 것을 전부, 원본 그대로 저장.

이전 수집은 응답에서 필요한 필드만 뽑고 버렸다. 이번엔 응답 JSON 을 통째로 적는다.
(한 번 긁고, 파싱은 yeoksam_full_parse.py 로 몇 번이든 다시 한다.)

가게당 호출
  ① panel3                    요약·메뉴·영업시간·AI요약·방문자시간대·지하철·편의·태그·
                              리뷰통계(강점태그 집계)·사진집계·블로그수·배달연동
  ② reviews x2 (추천/최신)    리뷰 전문 + 작성자 프로필 + 리뷰 태그(strength) + 사진 메타
  ③ photos ?tag=X (page 1)    분류별 사진: 업체제공/매장주제공/음식/분위기/외관/메뉴판
                              -> '후기'(사람 사진 섞임) 태그는 제외. 집계가 0 인 태그는 건너뜀
  ④ blog page 1               블로그 리뷰 10건 (제목·본문 일부·작성자·사진)

출력  data/yeoksam_full/{panel,reviews,photos,blog}.jsonl  (한 줄 = 응답 하나)
      data/yeoksam_full/done.jsonl                          (이어받기 표식)

**회차(run).** 모든 줄에 `run` 을 적는다. 기본값은 오늘 날짜다. 건너뛰기는 **이번 회차에
성공한 가게**만 대상이라, 날짜가 바뀌면 저절로 전수 재수집이 되고 같은 날 다시 돌리면
중단 지점부터 이어받는다. 주 1회 갱신에 따로 플래그가 필요 없다.

    python crawler/yeoksam_full.py                    # 오늘 회차. 처음이면 전수, 중단됐으면 이어받기
    python crawler/yeoksam_full.py --run 2026-09-14   # 자정을 넘겼을 때 어제 회차를 이어받는다
    python crawler/yeoksam_full.py --limit 3 --out data/yeoksam_full_test   # 시험

기존 파일에는 `run` 이 없다. 그건 '어느 회차도 아님' 으로 보고 다시 긁는다 — 첫 갱신에서
전수를 다시 받는 것이 맞다(마지막 수집이 2026-09-02 다).

실패한 가게는 건너뛰기 대상이 아니다. 이어받기로 다시 시도하고, 회차가 끝날 때까지 끝내
성공하지 못한 가게가 **폐업 후보**다. 그 판정은 `yeoksam_full_parse.py` 가 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent


SRC = ROOT / "data" / "yeoksam" / "stores.jsonl"


def dig(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return d if d is not None else default


class Sink:
    """엔드포인트별 jsonl. 줄마다 flush 해서 중단돼도 이어받는다."""

    def __init__(self, out: Path, run: str | None = None):
        out.mkdir(parents=True, exist_ok=True)
        # 회차를 안 주면 오늘 날짜. 필수 인자로 두면 `Sink(out)` 을 쓰는 쪽이 깨진다
        # (tests/test_source_platform.py 가 그렇게 쓴다).
        self.run = run or datetime.now().strftime("%Y-%m-%d")
        self.f = {n: (out / f"{n}.jsonl").open("a", encoding="utf-8")
                  for n in ("panel", "reviews", "photos", "blog", "done")}

    def write(self, name: str, rec: dict) -> None:
        # 회차를 모든 줄에 박는다. 파싱이 '이번에 실제로 본 가게' 를 이걸로 가린다.
        # `source_platform` 은 앞, `run` 은 뒤에 둔다 — 줄을 눈으로 볼 때 출처가
        # 먼저 보이고 회차가 마지막에 오는 편이 읽기 쉽다.
        self.f[name].write(json.dumps({"source_platform": "kakao", **rec,
                                       "run": self.run},
                                      ensure_ascii=False) + "\n")
        self.f[name].flush()

    def close(self) -> None:
        for f in self.f.values():
            f.close()


def done_in_run(path: Path, run: str) -> set[str]:
    """이번 회차에 **성공한** 가게. 이것만 건너뛴다.

    실패는 넣지 않는다 — 한 번의 네트워크 오류로 가게를 영영 놓치면 안 되고, 이어받기로
    다시 시도해야 한다. 회차가 없는 옛 줄은 어느 회차에도 속하지 않으므로 자연히 제외된다.
    """
    out: set[str] = set()
    if not path.exists():
        return out
    for l in path.open(encoding="utf-8"):
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r.get("run") == run and r.get("ok"):
            out.add(r["store_id"])
    return out


def main() -> None:
    from kakao_client import PHOTO_TAGS, KakaoMapClient
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.45)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "data" / "yeoksam_full"))
    ap.add_argument("--run", default=datetime.now().strftime("%Y-%m-%d"),
                    help="회차 이름. 기본값은 오늘 날짜. 같은 값으로 다시 돌리면 이어받는다")
    args = ap.parse_args()
    out = Path(args.out)

    stores = [json.loads(l) for l in SRC.open(encoding="utf-8")]
    done = done_in_run(out / "done.jsonl", args.run)
    todo = [s for s in stores if s["store_id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"회차 {args.run} · 대상 {len(stores):,}곳 · 이번 회차 완료 {len(done):,} · "
          f"남은 {len(todo):,} · 예상 {len(todo)*6*(args.delay+0.45)/3600:.1f}시간", flush=True)
    if not todo:
        print("이번 회차는 이미 끝났다. 다시 받으려면 --run 에 다른 이름을 준다.")
        return

    cl = KakaoMapClient(delay=args.delay, jitter=0.3)
    sink = Sink(out, args.run)
    t0 = time.time()
    calls = 0
    stats = {"panel_fail": 0, "reviews": 0, "photos": 0, "blog": 0}

    def now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def call(fn, *a, **kw):
        nonlocal calls
        calls += 1
        try:
            return fn(*a, **kw)
        except Exception as exc:
            print(f"  [!] {fn.__name__}{a} {exc!r}", flush=True)
            return None

    try:
        for i, s in enumerate(todo, 1):
            sid = s["store_id"]
            n_calls_before = calls

            # ① panel3
            panel = call(cl.place_detail, sid)
            if not panel:
                stats["panel_fail"] += 1
                sink.write("done", {"store_id": sid, "ts": now(), "ok": False,
                                    "reason": "panel_none"})
                continue
            sink.write("panel", {"store_id": sid, "fetched_at": now(), "data": panel})

            # ② 리뷰 (리뷰가 있는 가게만, 추천순+최신순)
            n_rev = dig(panel, "kakaomap_review", "score_set", "review_count", default=0) or 0
            if n_rev > 0:
                for order in ("RECOMMENDED", "LATEST"):
                    d = call(cl.reviews, sid, order=order)
                    if d:
                        sink.write("reviews", {"store_id": sid, "order": order,
                                               "fetched_at": now(), "data": d})
                        stats["reviews"] += len(d.get("reviews") or [])
                    if n_rev <= 20:          # 20건 이하면 최신순이 추천순과 동일 집합
                        break

            # ③ 분류별 사진 (집계가 0 인 태그는 호출하지 않는다)
            counts = dig(panel, "photos", "counts", default={}) or {}
            for tag in PHOTO_TAGS:
                if (counts.get(tag.lower()) or 0) <= 0:
                    continue
                d = call(cl.photos, sid, tag=tag, page=1)
                if d:
                    sink.write("photos", {"store_id": sid, "tag": tag, "page": 1,
                                          "fetched_at": now(), "data": d})
                    stats["photos"] += len(d.get("photos") or [])

            # ④ 블로그 리뷰 1페이지
            if (dig(panel, "blog_review", "review_count", default=0) or 0) > 0:
                d = call(cl.blog_reviews, sid, page=1)
                if d:
                    sink.write("blog", {"store_id": sid, "page": 1,
                                        "fetched_at": now(), "data": d})
                    stats["blog"] += len(d.get("reviews") or [])

            sink.write("done", {"store_id": sid, "ts": now(), "ok": True,
                                "n_calls": calls - n_calls_before})

            if i % 50 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"  {i}/{len(todo)} · 호출 {calls:,} ({calls/i:.1f}/곳) · "
                      f"리뷰 {stats['reviews']:,} · 사진 {stats['photos']:,} · "
                      f"블로그 {stats['blog']:,} · panel실패 {stats['panel_fail']} · "
                      f"남은 {(len(todo)-i)*el/i/60:.0f}분", flush=True)
    finally:
        sink.close()

    el = time.time() - t0
    print(f"\n완료 {el/60:.0f}분 · 호출 {calls:,} · {stats}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
