# -*- coding: utf-8 -*-
"""후보 풀 가게의 음식/공간 사진 내려받기 (Step 3b).

`data/kakao/photos.jsonl` 32,130장 중 **후보 풀 2,216곳** 것만, 가게당 음식 ≤ 4장 ·
공간 ≤ 3장을 골라 내려받는다. (PLAN 2절은 ≤ 6 / ≤ 4 로 적혀 있으나 이번 실행은 시간
예산 때문에 ≤ 4 / ≤ 3 으로 줄였다 — 결과 문서에 편차로 적는다.)

**고르는 규칙** (결정적이어야 실험이 재현된다):
  음식 = tag FOOD. type 우선순위 `MYSTORE`·`VENDOR` → `KMAPREVIEW` → `BLOG` → 나머지.
         가게가 직접 올린 사진(MYSTORE/VENDOR)이 대표 메뉴일 확률이 높고, 카카오맵
         리뷰 사진이 그다음, 블로그 사진은 음식 아닌 것(간판·사람)이 섞이는 비율이 높다.
  공간 = tag INDOOR 먼저, 그다음 OUTDOOR. 같은 tag 안에서는 음식과 같은 type 우선순위.
  같은 순위 안에서는 **`photos.jsonl` 등장 순서** 그대로 — 카카오의 노출 순서이고,
  메뉴(Step 1)에서 `menu_index` 순을 쓴 것과 같은 취급이다.

**내려받기.** CDN 이 리사이즈 파라미터(`?width=`)를 무시해서 원본(170KB~1.5MB)이 온다.
동시 8, 타임아웃 15초, 재시도 2회(총 3번 시도), 브라우저 UA + `map.kakao.com` Referer.
저장할 때 **짧은 변 256px** 로 줄여 JPEG q=85 로 쓴다(CLIP 입력이 224px 이라 256 이면
충분하고, 원본을 그대로 두면 15GB 다). 파일명은 `sha1(url).jpg`.

`data/photos_cache/index.jsonl` 이 내려받기 대장이다(store_id, tag, type, url, path,
ok, bytes, error). 다시 돌리면 ok=True 이고 파일이 실제로 있는 것은 건너뛴다(이어받기).

실행:
    nice -n 10 ~/venvs/recsys/bin/python -m analysis.photos.download            # 전체
    nice -n 10 ~/venvs/recsys/bin/python -m analysis.photos.download --limit 50 # 연기 시험
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PHOTOS_JSONL = ROOT / "data" / "kakao" / "photos.jsonl"
CACHE_DIR = ROOT / "data" / "photos_cache"
INDEX_PATH = CACHE_DIR / "index.jsonl"

#: 가게당 상한.
MAX_FOOD = 4
MAX_SPACE = 3

FOOD_TAGS = ("FOOD",)
SPACE_TAGS = ("INDOOR", "OUTDOOR")            # 순서가 곧 우선순위

#: type 우선순위. 작을수록 먼저. 목록에 없는 type 은 맨 뒤(9).
TYPE_RANK = {"MYSTORE": 0, "VENDOR": 0, "KMAPREVIEW": 1, "BLOG": 2}

#: 저장 규격.
SHORT_SIDE = 256
JPEG_QUALITY = 85

#: 네트워크.
WORKERS = 8
TIMEOUT = 15
RETRIES = 2                                   # 재시도 횟수(총 시도 = RETRIES + 1)
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REFERER = "https://map.kakao.com/"

#: **호스트마다 Referer 가 다르다.** `photos.jsonl` 의 url 은 카카오 CDN 만 있는 게 아니다 —
#: type=BLOG 사진의 대부분은 네이버 블로그 CDN(`postfiles.pstatic.net`)에 있고, 여기에
#: `map.kakao.com` Referer 를 보내면 **403** 이 온다(실측: 첫 실행에서 BLOG 693장 전부 403).
#: 네이버 호스트에는 네이버 Referer 를 보낸다(빈 Referer 도 되지만 브라우저와 같게 둔다).
NAVER_HOSTS = ("pstatic.net", "naver.net", "naver.com")
NAVER_REFERER = "https://blog.naver.com/"


def referer_for(url: str) -> str:
    """url 의 호스트에 맞는 Referer."""
    host = urllib.parse.urlsplit(url).hostname or ""
    return NAVER_REFERER if host.endswith(NAVER_HOSTS) else REFERER


# ────────────────────────────────────────────────── 고르기 (순수 함수 — 테스트가 찌른다)
@dataclass(frozen=True)
class PhotoRow:
    """`photos.jsonl` 한 줄 + 등장 순서."""
    line: int
    store_id: str
    tag: str
    type: str
    url: str
    menu_name: str = ""


def read_photo_rows(path: Path | None = None) -> list[PhotoRow]:
    """`photos.jsonl` 을 등장 순서 그대로 읽는다."""
    path = Path(path or PHOTOS_JSONL)
    rows: list[PhotoRow] = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            url = d.get("url")
            if not url:
                continue
            rows.append(PhotoRow(line=i, store_id=str(d.get("store_id")),
                                 tag=str(d.get("tag") or ""), type=str(d.get("type") or ""),
                                 url=str(url), menu_name=str(d.get("menu_name") or "")))
    return rows


def _sort_key(tags: tuple[str, ...]):
    def key(r: PhotoRow) -> tuple[int, int, int]:
        return (tags.index(r.tag), TYPE_RANK.get(r.type, 9), r.line)
    return key


def select_for_store(rows: list[PhotoRow], *, max_food: int = MAX_FOOD,
                     max_space: int = MAX_SPACE) -> tuple[list[PhotoRow], list[PhotoRow]]:
    """한 가게의 사진 목록 -> (음식 ≤ max_food, 공간 ≤ max_space).

    같은 url 이 두 번 나오면 앞의 것만 남긴다(카카오 데이터에 중복이 있다).
    """
    seen: set[str] = set()
    uniq: list[PhotoRow] = []
    for r in rows:
        if r.url in seen:
            continue
        seen.add(r.url)
        uniq.append(r)
    food = sorted([r for r in uniq if r.tag in FOOD_TAGS], key=_sort_key(FOOD_TAGS))
    space = sorted([r for r in uniq if r.tag in SPACE_TAGS], key=_sort_key(SPACE_TAGS))
    return food[:max_food], space[:max_space]


def select_photos(rows: list[PhotoRow], store_ids: list[str], *,
                  max_food: int = MAX_FOOD,
                  max_space: int = MAX_SPACE) -> list[PhotoRow]:
    """후보 풀 순서대로 (음식, 공간) 을 골라 한 줄로 편다. 풀 밖 가게는 버린다."""
    by_store: dict[str, list[PhotoRow]] = collections.defaultdict(list)
    pool = set(store_ids)
    for r in rows:
        if r.store_id in pool:
            by_store[r.store_id].append(r)
    out: list[PhotoRow] = []
    for sid in store_ids:                     # 풀 순서 = stores.jsonl 순서
        food, space = select_for_store(by_store.get(sid, []), max_food=max_food,
                                       max_space=max_space)
        out.extend(food)
        out.extend(space)
    return out


# ────────────────────────────────────────────────── 내려받기
def cache_path(url: str, cache_dir: Path | None = None) -> Path:
    """URL -> 캐시 파일 경로 (`sha1(url).jpg`)."""
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return Path(cache_dir or CACHE_DIR) / f"{name}.jpg"


def fetch(url: str, *, timeout: int = TIMEOUT, retries: int = RETRIES) -> bytes:
    """원본 바이트. 실패하면 마지막 예외를 올린다."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": referer_for(url),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:               # HTTPError·URLError·타임아웃·SSL 전부
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
                break                          # 다시 시도해도 같은 답이다
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise last if last else RuntimeError("알 수 없는 실패")


def save_resized(data: bytes, path: Path, *, short_side: int = SHORT_SIDE,
                 quality: int = JPEG_QUALITY) -> int:
    """짧은 변을 `short_side` 로 줄여 JPEG 저장. 이미 더 작으면 그대로 둔다.

    돌려주는 값은 **저장된 파일 크기**다(원본 크기가 아니다).
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        w, h = im.size
        if min(w, h) > short_side:
            scale = short_side / float(min(w, h))
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                           Image.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        im.save(path, "JPEG", quality=quality, optimize=True)
    return path.stat().st_size


def error_label(exc: Exception) -> str:
    """실패 사유를 짧은 라벨로. HTTP 상태별 집계를 위해서다."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            return "timeout"
        return f"URLError {type(reason).__name__}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    return f"{type(exc).__name__}"


@dataclass
class Stats:
    ok: int = 0
    skip: int = 0
    fail: int = 0
    bytes_saved: int = 0
    by_error: collections.Counter = field(default_factory=collections.Counter)


def read_index(index_path: Path | None = None) -> dict[str, dict]:
    """대장 -> url 별 **마지막** 기록. 실패한 것을 다시 받으면 줄이 늘어나므로 필요하다."""
    index_path = Path(index_path or INDEX_PATH)
    out: dict[str, dict] = {}
    if not index_path.exists():
        return out
    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = d.get("url")
            if url:
                out[str(url)] = d
    return out


def load_done(index_path: Path) -> set[str]:
    """이미 성공한 url 집합 (파일이 실제로 남아 있는 것만)."""
    return {url for url, d in read_index(index_path).items()
            if d.get("ok") and d.get("path") and Path(d["path"]).exists()}


def run(rows: list[PhotoRow], *, cache_dir: Path, index_path: Path,
        workers: int = WORKERS, resume: bool = True, log_every: int = 200) -> Stats:
    """실제 내려받기. 진행 상황을 stdout 으로 흘린다(nohup 로그가 그대로 대장이 된다)."""
    from concurrent.futures import ThreadPoolExecutor

    cache_dir.mkdir(parents=True, exist_ok=True)
    done = load_done(index_path) if resume else set()
    todo = [r for r in rows if r.url not in done]
    stats = Stats(skip=len(rows) - len(todo))
    print(f"[선택] {len(rows)}장 중 이미 받은 것 {stats.skip}장, 받을 것 {len(todo)}장",
          flush=True)

    lock = threading.Lock()
    start = time.time()
    index_fh = index_path.open("a", encoding="utf-8")

    def work(r: PhotoRow) -> None:
        path = cache_path(r.url, cache_dir)
        rec = {"store_id": r.store_id, "tag": r.tag, "type": r.type, "url": r.url,
               "path": str(path), "ok": False, "bytes": 0, "error": ""}
        try:
            size = save_resized(fetch(r.url), path)
            rec["ok"], rec["bytes"] = True, int(size)
        except Exception as exc:                          # noqa: BLE001 — 전부 기록한다
            rec["error"] = error_label(exc)
            if path.exists():
                path.unlink(missing_ok=True)              # 반쯤 쓰인 파일은 지운다
        with lock:
            index_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec["ok"]:
                stats.ok += 1
                stats.bytes_saved += rec["bytes"]
            else:
                stats.fail += 1
                stats.by_error[rec["error"]] += 1
            n = stats.ok + stats.fail
            if n % log_every == 0 or n == len(todo):
                el = time.time() - start
                print(f"[{n}/{len(todo)}] ok={stats.ok} fail={stats.fail} "
                      f"{n / max(el, 1e-9):.1f}장/초 경과 {el / 60:.1f}분 "
                      f"저장 {stats.bytes_saved / 2**20:.0f}MB", flush=True)
                index_fh.flush()

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, todo))
    finally:
        index_fh.close()

    el = time.time() - start
    print(f"[끝] ok={stats.ok} fail={stats.fail} skip={stats.skip} "
          f"{el / 60:.1f}분 ({stats.ok / max(el, 1e-9):.1f}장/초) "
          f"저장 {stats.bytes_saved / 2**20:.0f}MB", flush=True)
    if stats.by_error:
        print("[실패 사유]", ", ".join(f"{k}={v}" for k, v in
                                   stats.by_error.most_common()), flush=True)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="후보 풀 가게의 음식/공간 사진 내려받기")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N 장만 (연기 시험)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--max-food", type=int, default=MAX_FOOD)
    ap.add_argument("--max-space", type=int, default=MAX_SPACE)
    ap.add_argument("--cache-dir", default=str(CACHE_DIR))
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="고르기만 하고 받지 않는다")
    args = ap.parse_args(argv)

    try:                                       # 백그라운드에서 다른 실험과 같이 돈다
        os.nice(10)
    except (AttributeError, OSError):
        pass

    from analysis.backtest.data import load_dataset
    from analysis.backtest.pool import build_pool

    pool = build_pool(load_dataset())
    rows = select_photos(read_photo_rows(), pool.ids, max_food=args.max_food,
                         max_space=args.max_space)
    n_food = sum(1 for r in rows if r.tag in FOOD_TAGS)
    stores_food = len({r.store_id for r in rows if r.tag in FOOD_TAGS})
    stores_space = len({r.store_id for r in rows if r.tag in SPACE_TAGS})
    print(f"[풀] 가게 {len(pool.ids)}곳 · 고른 사진 {len(rows)}장 "
          f"(음식 {n_food}장/{stores_food}곳, 공간 {len(rows) - n_food}장/{stores_space}곳)",
          flush=True)
    if args.limit:
        rows = rows[:args.limit]
        print(f"[연기 시험] 앞 {len(rows)}장만 받는다", flush=True)
    if args.dry_run:
        return 0

    cache_dir = Path(args.cache_dir)
    stats = run(rows, cache_dir=cache_dir, index_path=cache_dir / "index.jsonl",
                workers=args.workers, resume=not args.no_resume)
    return 0 if stats.ok else 1


if __name__ == "__main__":
    sys.exit(main())
