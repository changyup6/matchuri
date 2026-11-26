# -*- coding: utf-8 -*-
"""분할 캐시 — 사용자별 분할을 한 번만 만들고 재사용한다.

분할은 데이터와 상수만으로 결정되므로(난수 없음) 지문이 같으면 결과도 같다. 지문에는
입력 파일의 크기·mtime 과 분할에 관여하는 상수를 전부 넣는다 — 상수를 바꿔 놓고 옛
캐시를 쓰는 사고를 막는다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import config
from .data import Dataset
from .pool import Pool
from .split import UserSplit, build_splits


def fingerprint(data_dir: Path | None = None) -> str:
    data_dir = Path(data_dir or config.DATA_DIR)
    parts = []
    for name in ("stores.jsonl", "reviews.jsonl", "menus.jsonl", "photos.jsonl"):
        p = data_dir / name
        st = p.stat() if p.exists() else None
        parts.append(f"{name}:{st.st_size if st else 0}:{int(st.st_mtime) if st else 0}")
    parts += [f"min_reviews={config.MIN_USER_REVIEWS}",
              f"future={config.FUTURE_RATIO}/{config.FUTURE_MIN}",
              f"pool={config.MIN_POOL_REVIEWS}/{config.MIN_POOL_PHOTOS}",
              f"N={config.HISTORY_SIZES}",
              f"like={config.LIKE_MIN_RATING}/{config.DISLIKE_MAX_RATING}"]
    return hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=12).hexdigest()


def _to_json(s: UserSplit) -> dict:
    return {"user_id": s.user_id, "total": s.total,
            "past": [list(x) for x in s.past], "future": [list(x) for x in s.future],
            "n_future_raw": s.n_future_raw, "n_future_dup": s.n_future_dup,
            "n_future_seen": s.n_future_seen,
            "n_future_seen_recent": {str(k): v for k, v in s.n_future_seen_recent.items()}}


def _from_json(d: dict) -> UserSplit:
    return UserSplit(user_id=d["user_id"], total=d["total"],
                     past=tuple((a, b) for a, b in d["past"]),
                     future=tuple((a, b) for a, b in d["future"]),
                     n_future_raw=d["n_future_raw"], n_future_dup=d["n_future_dup"],
                     n_future_seen=d["n_future_seen"],
                     n_future_seen_recent={int(k): v
                                           for k, v in d["n_future_seen_recent"].items()})


def load_or_build(ds: Dataset, pool: Pool, *, cache_dir: Path | None = None,
                  use_cache: bool = True) -> tuple[list[UserSplit], bool]:
    """(분할, 캐시에서 읽었는지)."""
    cache_dir = Path(cache_dir or config.CACHE_DIR)
    path = cache_dir / f"splits-{fingerprint()}.json"
    if use_cache and path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            return [_from_json(d) for d in blob["splits"]], True
        except (json.JSONDecodeError, KeyError):
            pass                      # 깨진 캐시는 무시하고 다시 만든다
    splits = build_splits(ds, pool)
    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fingerprint": fingerprint(),
                                    "pool_size": len(pool),
                                    "splits": [_to_json(s) for s in splits]},
                                   ensure_ascii=False), encoding="utf-8")
    return splits, False
