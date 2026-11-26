# -*- coding: utf-8 -*-
"""데이터 로딩 — data/kakao 런타임 테이블(scripts/build_runtime_data.py 산출물).

파일마다 mtime 을 따로 본다(바뀐 파일만 다시 읽는다). 이전 API 와의 호환을 위해
kakao_review_count / kakao_avg_score 같은 옛 필드명도 같이 채운다.
"""
from __future__ import annotations

import collections
import hashlib
import itertools
import json
import logging
import random
import threading
from pathlib import Path

from recsys import expressiveness, review_rank_cache

log = logging.getLogger(__name__)

# BE/server/recsys/store.py -> parents[2] = BE.  데이터는 세 파트(server·crawler·
# analysis)가 함께 쓰므로 BE/data 에 둔다. 컨테이너도 같은 배치를 그대로 따른다.
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "kakao"
STORES = DATA / "stores.jsonl"
REVIEWS = DATA / "reviews.jsonl"
# 네이버 방문자 리뷰. **가게 화면에 보여 줄 때만** 카카오 리뷰와 합친다(`load`).
# reviews.jsonl 은 카카오 리뷰 = 카카오 사용자 기록이라 백테스트·트렌드·서빙 행렬이 그대로 읽는다.
# 네이버 줄을 거기 섞으면 사용자·인기·평가 분리가 바뀌므로 파일을 나눴다. 없으면 없는 대로 돈다.
# 만드는 곳: 데이터 파이프라인 `reco/export_naver_reviews.py` (id 는 음의 정수 — 카카오 리뷰 id 는 양수라
# 부호로 갈린다 · 별점은 정수로 반올림 · 작성자 닉네임 없음 — 줄마다 source_platform="naver").
REVIEWS_NAVER = DATA / "reviews_naver.jsonl"
MENUS = DATA / "menus.jsonl"
PHOTOS = DATA / "photos.jsonl"

# 카드 사진 우선순위: 공식(매장주·업체) -> 음식 -> 분위기 -> 외관 -> 메뉴판.
# '후기' 태그(사람 사진 섞임)는 수집 단계에서 이미 제외됐다.
#
# 이 순서보다 **`hero: true` 인 줄이 먼저다.** 데이터 파이프라인이 사진을 받아 보고
# 음식이며 가장 먹음직스럽다고 고른 한 장이다. 태그 순서만 쓰면 가게가 올린
# 메뉴판·가격 포스터가 음식 사진보다 앞에 섰다(서경한우프라자 역삼점, 봉우리 역삼본점).
# 표시가 없는 가게는 예전 태그 순서 그대로다.
PHOTO_PRIORITY = ["MYSTORE", "VENDOR", "FOOD", "INDOOR", "OUTDOOR", "MENU"]

#: `popular_rank` 가 없는 메뉴 줄의 순위. 모든 줄이 같은 값이 되어 예전 순서를 따른다.
NO_POPULAR_RANK = 1_000_000


def popular_rank(row: dict) -> int:
    """메뉴 줄의 손님 인기 순위(0 이 1등). 없거나 정수가 아니면 NO_POPULAR_RANK.

    bool 은 int 의 하위형이라 따로 막는다 — `True` 가 1등 다음 순위로 읽히면 안 된다.
    """
    r = row.get("popular_rank")
    if isinstance(r, bool) or not isinstance(r, int) or r < 0:
        return NO_POPULAR_RANK
    return r


def menu_sort_key(row: dict):
    """클라이언트에 전달하는 카드·상세의 메뉴 순서.

    원본 menu_index 오름차순. 같은 인덱스는 파일 순서를 유지하며, 원본 인덱스가
    없거나 잘못된 줄은 끝에 둔다. 인기·추천·음식 여부로 순서를 바꾸지 않는다.
    """
    index = row.get("menu_index")
    valid = isinstance(index, int) and not isinstance(index, bool) and index >= 0
    return (not valid, index if valid else 0)


def menu_feature_sort_key(row: dict):
    """기존 추천 산출물의 가격 특성·후보 자격을 재현하는 내부 순서.

    화면 정렬을 바꿔도 pool_ids와 모델 입력은 유지한다. 분석 로더도 이 규칙을 쓴다.
    """
    return (row.get("is_dish") is False, popular_rank(row),
            not row.get("is_recommend"), row.get("menu_index") or 0)


def card_photo_eligible(row: dict) -> bool:
    """Use explicit metadata only; a food=False indoor/outdoor photo is still valid."""
    if row.get('tag') == 'MENU':
        return False
    return (row.get('food') is True or row.get('tag') in ('INDOOR', 'OUTDOOR')
            or (row.get('tag') == 'FOOD' and row.get('food') is not False))


def photo_sort_key(row: dict, rank: dict[str, int]):
    """사진 순서. `hero` 가 정확히 True 인 줄이 먼저, 그다음 태그 우선순위."""
    return (row.get("hero") is not True, rank.get(row.get("tag"), 99))


_lock = threading.Lock()
_mt: dict[str, float] = {}
_stores: dict[str, dict] = {}
_reviews: dict[str, list] = {}
_menus: dict[str, list] = {}
_photos: dict[str, list[dict]] = {}


def _read_jsonl(p: Path, default_source: str | None = "kakao", *, hasher=None):
    if not p.exists():
        return
    with p.open("rb") as f:
        for raw in f:
            if hasher is not None:
                hasher.update(raw)
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                # This adapter reads the Kakao dataset only. Legacy files predate
                # provenance fields; explicit row values (including null) always win.
                row.setdefault("source_platform", default_source)
                yield row
            except json.JSONDecodeError:
                continue


def _changed(p: Path) -> bool:
    cur = p.stat().st_mtime if p.exists() else 0.0
    if _mt.get(p.name) == cur:
        return False
    _mt[p.name] = cur
    return True


def _sort_reviews() -> None:
    """미리보기용 '대표' 리뷰가 앞에 오게 — **표현력이 높은 것부터**, 같으면 옛 규칙대로.

    상세는 기본 5건만 보인다(`loader.PREVIEW_REVIEW`). 옛 순서(텍스트 있음 → 가게 평균에
    가까운 별점 → 사진 수 → 길이)는 "맛있어요" 다섯 줄을 앞에 세울 수 있었다 — 별점이
    평균에 가깝고 사진이 붙었으면 내용이 없어도 대표가 됐다. 그래서 첫 키를
    `recsys/expressiveness.py` 의 (깊이, 합, 폭) 으로 바꿨다. 등급이 같은 리뷰 사이에서만
    옛 규칙이 든다. 외국어·이모지만인 글은 등급 0 이라 뒤로 간다(내용이 없어서가 아니라
    사전이 못 재서다 — `lang` 을 보면 구분된다).

    네이버 포함 운영 31만 1천 건의 전체 채점은 105초였다(2026-09-21, 1 CPU).
    `load`가 원본 파일·채점 코드 SHA가 같은 캐시의 등급을 먼저 `_expr`로 채운다.
    캐시가 없거나 바뀌면 여기서 정확히 다시 채점한다. 동점 정렬은 매번 현재 가게
    평균·사진 수·원본 순서로 수행하며, 응답 모델은 `_expr`를 직접 내보내지 않는다.
    """
    for sid, rs in _reviews.items():
        avg = _stores.get(sid, {}).get("review_avg")
        try:
            avg = float(avg) if avg else None
        except (TypeError, ValueError):
            avg = None

        def key(x, _avg=avg):
            content = (x.get("content") or "").strip()
            expr = x.get("_expr")
            if expr is None:
                expr = x["_expr"] = expressiveness.rank_key(content)
            has_text = 1 if len(content) >= 10 else 0
            r = x.get("rating")
            gap = abs(r - _avg) if (r and _avg) else 1.5
            return (tuple(-v for v in expr), -has_text, gap, -(x.get("photo_count") or 0),
                    -min(len(content), 200))

        rs.sort(key=key)


def load() -> None:
    with _lock:
        if _changed(STORES):
            _stores.clear()
            for s in _read_jsonl(STORES):
                if not s.get("store_id"):
                    continue
                # 옛 필드명 호환
                s["kakao_review_count"] = s.get("review_count") or 0
                s["kakao_avg_score"] = s.get("review_avg")
                _stores[s["store_id"]] = s

        # 네이버 파일은 **reviews.jsonl 옆에 있는 것**을 읽는다. 테스트가 REVIEWS 만 임시 폴더로
        # 돌려도 실데이터의 네이버 리뷰가 섞여 들어오지 않게 한다.
        naver = REVIEWS.parent / REVIEWS_NAVER.name
        # 둘 다 불러야 두 파일의 mtime 이 함께 기록된다 — `or` 로 줄이면 한쪽이 안 적힌다.
        kakao_changed, naver_changed = _changed(REVIEWS), _changed(naver)
        if kakao_changed or naver_changed:
            by = collections.defaultdict(list)
            # Hash exactly the bytes being parsed, including invalid/empty lines.
            # No second file pass or mtime-only trust is needed for cache reuse.
            kakao_hash, naver_hash = hashlib.sha256(), hashlib.sha256()
            rows = itertools.chain(_read_jsonl(REVIEWS, hasher=kakao_hash),
                                   _read_jsonl(naver, default_source=None, hasher=naver_hash))
            # 앱(Flutter)은 리뷰 id 를 `json['id'] as int` 로 읽고, 리뷰 사진 원본을 볼 때 가게 상세를
            # 다시 받아 **같은 id 의 리뷰를 찾는다**(FE/app/lib/services/original_card_images.dart).
            # id 가 정수가 아닌 줄이 하나만 섞여도 그 가게가 든 카드 목록 전체를 앱이 못 읽는다 —
            # 2026-09-18 네이버 리뷰를 id null 로 실었다가 서비스가 멈췄다. 그래서 **정수 id 가 없는 줄과
            # 가게 안에서 id 가 겹치는 줄은 화면에 싣지 않는다.** 데이터가 어떻게 오든 앱이 안전하게.
            dropped = collections.Counter()
            seen_ids: set[tuple[str, int]] = set()
            for r in rows:
                if r.get("store_id"):
                    rid = r.get("review_id")
                    if isinstance(rid, bool) or not isinstance(rid, (int, str)):
                        dropped["id 가 정수가 아님"] += 1
                        continue
                    try:
                        rid = int(rid)
                    except ValueError:
                        dropped["id 가 정수가 아님"] += 1
                        continue
                    if (r["store_id"], rid) in seen_ids:
                        dropped["가게 안에서 id 가 겹침"] += 1
                        continue
                    seen_ids.add((r["store_id"], rid))
                    r["review_id"] = rid
                    # 옛 API 는 파이프 구분 문자열이었다
                    r["strength_tags_str"] = "|".join(r.get("strength_tags") or [])
                    # Parsed rows contain photos; the runtime exporter keeps
                    # only photo_urls. Preserve that format across loading.
                    if not r.get("photo_urls"):
                        r["photo_urls"] = [p.get("url") for p in (r.get("photos") or [])
                                           if p.get("url")]
                    by[r["store_id"]].append(r)
            if dropped:
                log.warning("리뷰 줄을 화면에서 뺐다: %s", dict(dropped))
            _reviews.clear()
            _reviews.update(by)
            cache_key = review_rank_cache.source_key([kakao_hash.hexdigest(), naver_hash.hexdigest()])
            cached = review_rank_cache.restore(_reviews, cache_key)
            _sort_reviews()
            if not cached:
                review_rank_cache.save(_reviews, cache_key)

        if _changed(MENUS):
            by = collections.defaultdict(list)
            for m in _read_jsonl(MENUS):
                if m.get("store_id"):
                    by[m["store_id"]].append(m)
            # 수집 원본 순서를 그대로 전달한다. 안정 정렬이므로 동점은 파일 순서다.
            for rows in by.values():
                rows.sort(key=menu_sort_key)
            _menus.clear()
            _menus.update(by)

        if _changed(PHOTOS):
            by = collections.defaultdict(list)
            for p in _read_jsonl(PHOTOS):
                if p.get("store_id") and p.get("url"):
                    by[p["store_id"]].append(p)
            rank = {t: i for i, t in enumerate(PHOTO_PRIORITY)}
            for rows in by.values():
                rows.sort(key=lambda x: photo_sort_key(x, rank))
            _photos.clear()
            _photos.update(by)


def all_store_ids() -> list[str]:
    load()
    return list(_stores)


def all_stores() -> list[dict]:
    """가게 전부. 집계가 가게마다 조회하면 dict 조회가 2,795번이라 한 번에 준다."""
    load()
    return list(_stores.values())


def all_reviews() -> list[dict]:
    """리뷰 전부(가게별 리스트를 평탄화). 월별 추이 집계가 쓴다.

    `get_reviews` 는 기본 5건만 주는 **미리보기용**이라 집계에 쓰면 안 된다 —
    가게마다 앞 5건만 세어 추이가 통째로 틀어진다.
    """
    load()
    return [r for rows in _reviews.values() for r in rows]


def all_menus() -> list[dict]:
    """메뉴 전부(평탄화). 추적 키워드 어휘를 여기서 뽑는다."""
    load()
    return [m for rows in _menus.values() for m in rows]


def data_stamp() -> tuple:
    """데이터 파일들의 mtime 묶음 — 캐시를 언제 버릴지 판단하는 값이다.

    `_changed` 를 쓰지 않는 이유: 그 함수는 **부르면 기록을 갱신해** 두 번째 호출이
    False 가 된다(로더 전용이다). 읽기만 하는 값이 따로 필요하다.
    """
    load()
    return tuple(sorted(_mt.items()))


def get_store(store_id: str) -> dict | None:
    load()
    return _stores.get(store_id)


def get_reviews(store_id: str, limit: int = 5) -> list[dict]:
    load()
    return _reviews.get(store_id, [])[:limit]


def review_count(store_id: str) -> int:
    """그 가게에 대해 **우리가 실제로 들고 있는** 리뷰 수(카카오+네이버 수집분).

    `stores.jsonl` 의 `review_count` 와 **다른 값이다.** 저건 수집 순간 카카오가 자기
    화면에 적어 둔 총수고, 우리 수집 상한은 가게당 20건이다(`BE/crawler/config.py`:
    `REVIEWS_PER_STORE`). 2026-09-22 로컬 산출물 기준 2,795곳 중 717곳에서 저 숫자가
    보유분보다 크다(적힌 총합 65,763 vs 보유 33,300, 최대 928→37). 화면 헤더가 그 큰
    숫자를 그리면 "더보기를 눌러도 그만큼 안 나오는" 상태가 된다 — 그래서 응답 DTO 는
    이 값을 쓴다(`app/loader.py: _store`).

    리스트 길이 하나라 목록 응답(카드 수만큼 곱해지는 자리)에서도 공짜다.
    """
    load()
    return len(_reviews.get(store_id, ()))


def get_photos(store_id: str, limit: int = 10, tags=None) -> list[str]:
    """가게 사진 URL. 공식 -> 음식 -> 분위기 순, 중복 제거. tags 로 분류 제한 가능."""
    load()
    seen, out = set(), []
    for p in _photos.get(store_id, []):
        if tags and p.get("tag") not in tags:
            continue
        u = p["url"]
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def hero_photo(store_id: str, *, for_card: bool = False) -> dict | None:
    """기본값은 산출물 후보 풀을 정하는 원본 대표 사진. for_card는 화면용 대체 사진.

    URL 만 주는 get_photos 와 달리 줄 전체를 준다. `food` 같이 URL 옆에 붙어
    오는 값을 봐야 할 때 쓴다. 정렬은 load() 에서 이미 끝나 있다.
    """
    load()
    rows = _photos.get(store_id) or []
    if for_card:
        eligible = [row for row in rows if card_photo_eligible(row)]
        return min(eligible, key=lambda row: (row.get('hero') is not True,
                   row.get('food') is not True)) if eligible else None
    # The curated hero defines the already-built serving artifact's candidate pool.
    # Presentation fallback must not silently add stores missing vector artifacts.
    return rows[0] if rows else None


def get_menus(store_id: str, limit: int | None = 20) -> list[dict]:
    load()
    return _menus.get(store_id, [])[:limit]


def get_feature_menus(store_id: str, limit: int = 20) -> list[dict]:
    """추천 모델 입력용. 화면에는 get_menus의 원본 순서를 사용한다."""
    return sorted(get_menus(store_id, limit=None), key=menu_feature_sort_key)[:limit]


def random_store(min_reviews: int = 1) -> dict | None:
    load()
    pool = [sid for sid, s in _stores.items()
            if (s.get("review_count") or 0) >= min_reviews]
    return get_store(random.choice(pool)) if pool else None
