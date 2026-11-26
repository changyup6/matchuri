# -*- coding: utf-8 -*-
"""data/kakao 원본 로딩.

서버(`server/recsys/store.py`)의 추천 후보·가격 특성과 **같은 규칙**으로 읽는다.
화면 메뉴 순서와 별개로 기존 모델 입력을 유지한다. 서버는 mtime 캐시를 둔 전역
싱글턴인 데 비해 여기는 한 번 읽고 끝나는 값 객체다.
파싱이 어긋나면 백테스트가 서버와 다른 세상을 재는 셈이라 `tests/test_pool.py` 가
후보 풀과 특성이 서버 함수와 정확히 같음을 검증한다.

카카오는 수치를 문자열로 주기도 한다(`"review_count": "65"`). 서버 로더는 `or 0`
으로만 걸러서 문자열이 그대로 흘러가므로, 여기서는 `_as_int`/`_as_float` 로 한 번
더 정규화한다 — 현재 data/kakao 는 전부 숫자형이라 결과는 같다.
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass
from pathlib import Path

from . import config

# 서버 store.py 의 사진 우선순위. 후보 풀은 '1장 이상' 만 보므로 순서는 영향이 없지만
# 나중 단계(사진 벡터)가 같은 순서를 쓰도록 여기 남긴다. 서버와 같이 `hero: true` 인
# 줄이 이 순서보다 먼저다.
PHOTO_PRIORITY = ["MYSTORE", "VENDOR", "FOOD", "INDOOR", "OUTDOOR", "MENU"]

# 서버 store.NO_POPULAR_RANK 와 같다. 화면 표시가 아닌 기존 가격 특성용이다.
NO_POPULAR_RANK = 1_000_000


def _popular_rank(m: dict) -> int:
    r = m.get("popular_rank")
    if isinstance(r, bool) or not isinstance(r, int) or r < 0:
        return NO_POPULAR_RANK
    return r


def _as_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def _as_float(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def read_jsonl(path: Path):
    """서버 `store._read_jsonl` 과 동일 — 빈 줄과 깨진 줄은 조용히 건너뛴다."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


@dataclass(frozen=True)
class Review:
    """리뷰 한 건. 백테스트가 쓰는 필드만 남긴다(본문은 Step 2 에서 쓴다)."""
    store_id: str
    review_id: int | None
    author_id: str
    rating: int
    date: str


@dataclass
class Dataset:
    stores: dict[str, dict]              # store_id -> 원본 dict (수치 정규화됨)
    store_order: list[str]               # stores.jsonl 등장 순서 = 서버의 all_store_ids()
    reviews: list[Review]
    menu_prices: dict[str, list[int]]    # store_id -> 가격(>0) 목록, 기존 모델 입력 순서
    photo_count: dict[str, int]          # store_id -> url 있는 사진 수
    #: 서버 정렬 뒤 **1번 사진**이 음식이라고 확인됐나. `photos.jsonl` 의 `food`
    #: 필드다. 값이 없는 것(= 받아 본 적이 없어 모른다)은 False 로 본다 —
    #: 서버 `swipe._eligible` 이 `is True` 로 보는 것과 같다.
    hero_food: dict[str, bool]
    #: 후보 자격에 쓰는 기존 대표 메뉴 3칸 안의 가격. 화면 표시 순서를 바꿔도
    #: 이미 구축한 후보 풀과 임베딩의 가게 목록이 달라지지 않도록 유지한다.
    card_prices: dict[str, list[int]]


def _menu_feature_sort_key(m: dict):
    # 서버 store.get_feature_menus() 와 같은 기존 가격 특성 입력 순서.
    # 화면용 get_menus() 의 원본 menu_index 정렬과 분리한다. 표시 변경 때문에
    # 후보 가게 목록이 바뀌면 기존 임베딩 산출물의 pool_ids 와 어긋난다.
    return (m.get("is_dish") is False, _popular_rank(m),
            not m.get("is_recommend"), _as_int(m.get("menu_index")) or 0)


def load_dataset(data_dir: Path | None = None) -> Dataset:
    data_dir = Path(data_dir or config.DATA_DIR)

    stores: dict[str, dict] = {}
    order: list[str] = []
    for s in read_jsonl(data_dir / "stores.jsonl"):
        sid = s.get("store_id")
        if not sid:
            continue
        s["review_count"] = _as_int(s.get("review_count")) or 0
        s["review_avg"] = _as_float(s.get("review_avg"))
        # 서버 로더가 채우는 옛 필드명. 후보 풀 코드가 서버와 같은 이름을 쓰게 한다.
        s["kakao_review_count"] = s["review_count"]
        s["kakao_avg_score"] = s["review_avg"]
        if sid not in stores:
            order.append(sid)
        stores[sid] = s

    reviews: list[Review] = []
    for r in read_jsonl(data_dir / "reviews.jsonl"):
        sid, author = r.get("store_id"), r.get("author_id")
        rating = _as_int(r.get("rating"))
        if not sid or not author or rating is None or not r.get("date"):
            continue
        reviews.append(Review(str(sid), _as_int(r.get("review_id")),
                              str(author), rating, str(r["date"])))

    raw_menus: dict[str, list[dict]] = collections.defaultdict(list)
    for m in read_jsonl(data_dir / "menus.jsonl"):
        if m.get("store_id"):
            raw_menus[m["store_id"]].append(m)
    menu_prices: dict[str, list[int]] = {}
    for sid, rows in raw_menus.items():
        rows.sort(key=_menu_feature_sort_key)
        # 서버 _price_bucket 과 같은 필터: get_feature_menus(limit=300) 뒤 양수 가격만.
        menu_prices[sid] = [p for p in (_as_int(m.get("price")) for m in rows[:300])
                            if p is not None and p > 0]

    # 후보 자격용 기존 3메뉴 가격. 화면에 표시하는 원본 순서와 독립적이다.
    # 기존 모델과 같이 이름 중복을 건너뛰고 다음 메뉴로 채운다.
    card_prices: dict[str, list[int]] = {}
    for sid, rows in raw_menus.items():
        seen: set = set()
        out: list[int] = []
        for m in rows[:config.CARD_MENUS * 4]:
            nm = m.get("menu_name")
            if nm in seen:
                continue
            seen.add(nm)
            v = _as_int(m.get("price"))
            out.append(v if (v is not None and v > 0) else 0)
            if len(out) >= config.CARD_MENUS:
                break
        card_prices[sid] = [v for v in out if v > 0]

    photo_count: collections.Counter = collections.Counter()
    hero: dict[str, dict] = {}
    rank = {t: i for i, t in enumerate(PHOTO_PRIORITY)}
    for p in read_jsonl(data_dir / "photos.jsonl"):
        sid = p.get("store_id")
        if not sid or not p.get("url"):
            continue
        photo_count[sid] += 1
        # 서버 store.load() 와 같은 정렬을 1등만 골라 흉내 낸다. 안정 정렬이라
        # 같은 키 안에서는 **파일에서 먼저 나온 것**이 앞이다.
        key = lambda x: (x.get("hero") is not True, rank.get(x.get("tag"), 99))  # noqa: E731
        cur = hero.get(sid)
        if cur is None or key(p) < key(cur):
            hero[sid] = p

    return Dataset(stores=stores, store_order=order, reviews=reviews,
                   menu_prices=menu_prices, photo_count=dict(photo_count),
                   hero_food={s: (h.get("food") is True) for s, h in hero.items()},
                   card_prices=card_prices)
