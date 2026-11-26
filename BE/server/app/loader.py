"""카드 카탈로그 — data/kakao 를 목서버 응답 모양으로 바꿔 준다.

데이터를 직접 읽지 않고 `recsys.store` 를 그대로 쓴다. 추천 엔진과 같은 메모리를
공유하므로 79MB 를 두 번 올리지 않고, 데이터가 바뀌면 양쪽이 함께 갱신된다.

  card_id  int  — store_id 를 정렬해 0,1,2… 로 매긴다. 서버를 껐다 켜도 그대로다.
  store_id str  — 카카오 장소 id. 추천 엔진이 쓰는 키.
"""
from __future__ import annotations

import re
import threading

from app import card_ids, catalog_media, filters, hours, observe, user_reviews
from app.model import Card, Food, OpenHours, Review, Store
from recsys import store as svc

_lock = threading.Lock()
_mtime = None                  # 데이터가 바뀌면 매핑을 다시 만든다
cards: dict[int, Card] = {}    # card_id -> Card (리뷰는 비어 있다. 요청 때 채운다)
card_id_by_store: dict[str, int] = {}
store_id_by_card: dict[int, str] = {}

PREVIEW_REVIEW = 5     # 상세보기 기본 리뷰 수
LIST_REVIEW = 1        # 목록에서 카드 앞면에 쓸 리뷰 수
CARD_IMAGES = 5        # 카드에서 넘겨 볼 사진 수


def _tags(s: dict) -> list[str]:
    """카드에 찍히는 태그 — **필터가 보는 것과 같은 함수에서 나온다**(`filters.card_tags`).

    `category_group` 이 업종에 대해 지키던 규칙과 같다: 필터에 쓰는 라벨과 카드에 찍히는
    라벨이 같은 데서 나와야 '이 카드가 왜 걸렸나' 를 설명할 수 있다.
    """
    return filters.card_tags(s)


def _subway(s: dict) -> str | None:
    if not s.get("subway_station"):
        return None
    exit_no = s.get("subway_exit")
    part = f"{exit_no}번 출구 " if exit_no else ""
    mins = s.get("subway_minutes")
    return f"{s['subway_station']} {part}{mins}분" if mins else f"{s['subway_station']} {part}".strip()


#: `"ᄁ09(9/2)"` 같은 라벨에서 요일만 남긴다. 원본은 수집 시점 그 주의 날짜가
#: 박혀 있어서, 그대로 내보내면 다음 주에 거짓말이 된다. 요일 7개가 다 들어 있으므로
#: 주간 시간표로 읽으면 된다 — 다만 공휴일·임시휴무는 반영되지 않는다.
_DAY = re.compile(r"^\s*([월화수목금토일])")


#: 응답의 요일 순서. `datetime.weekday()` 와 같은 순서라 `app/hours.py` 와 맞는다.
_WEEK = "월화수목금토일"


def _open_hours(s: dict) -> list[OpenHours]:
    """요일별 영업시간. 하나라도 있으면 **월~일 7개를 채워서** 돌려준다.

    원본에 요일이 빠져 있는 가게가 2,443곳 중 86곳이다. 그 줄을 그냥 빼면 배열 길이가
    가게마다 달라지고, "일요일에 여나" 를 묻는 쪽이 매번 요일을 뒤져야 한다. 쉬는 날과
    **줄이 아예 없는 날**을 같은 모양(`open`·`close` 가 `null`)으로 맞춰 둔다 — 어차피
    원본도 그 둘을 구분하지 않는다(`time` 이 빈 줄이 1,205건 있다).

    시간표가 **아예** 없는 가게(352곳)는 빈 배열이다. 그건 '정보가 없다' 는 뜻이고,
    7줄을 null 로 채우면 '일주일 내내 휴무' 라는 다른 말이 된다.
    """
    found: dict[str, OpenHours] = {}
    for row in s.get("open_hours") or []:
        m = _DAY.match(str(row.get("day") or ""))
        if m is None:
            continue                      # 요일을 못 읽으면 버린다(거짓 요일을 만들지 않는다)
        t = (row.get("time") or "").strip()
        open_at = close_at = None
        if " ~ " in t:
            a, _, b = t.partition(" ~ ")
            open_at, close_at = a.strip() or None, b.strip() or None
        found[m.group(1)] = OpenHours(
            day=m.group(1), open=open_at, close=close_at,
            overnight=_overnight(open_at, close_at),
            # 쪼개는 일은 모델이 한다(`app/model.py: OpenHours._split_breaks`).
            breaks=[x for x in (row.get("breaks") or []) if x])
    if not found:
        return []
    return [found.get(d) or OpenHours(day=d, open=None, close=None,
                                      overnight=False, breaks=[])
            for d in _WEEK]


def _overnight(open_at: str | None, close_at: str | None) -> bool:
    """`11:00 ~ 02:00` 인가 — 닫는 시각이 여는 시각보다 작으면 다음 날이다.

    판정을 `app/hours.py: _shift` 와 같은 규칙으로 둔다(`close <= open`). 앱이
    `익일 02:00` 을 그릴 때 쓰는 값이고, 전체 15,738건 중 2,567건(16.3%)이 그렇다.
    """
    a, b = hours._minutes(open_at), hours._minutes(close_at)
    return a is not None and b is not None and b <= a


def _coord(v) -> float | None:
    """좌표 하나. 숫자가 아니면 `None` — 0.0 으로 두면 아프리카 앞바다를 가리킨다."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _store(s: dict, reviews: list[Review]) -> Store:
    """`review_count` 는 **여기서 볼 수 있는 리뷰 수**다(2026-09-22 부터).

    = 우리가 들고 있는 수집분(`recsys/store.py: review_count`) + 그 가게에 달린 우리
    후기(`app/user_reviews.py: count_for`). 즉 `GET /cards/{id}/reviews` 가 돌려주는
    배열의 길이와 같다.

    **수집 원본의 `review_count` 를 쓰지 않는다.** 저건 수집 순간 카카오 화면에 적혀
    있던 총수인데 우리 수집 상한은 가게당 20건이라, 2,795곳 중 717곳에서 화면 숫자가
    더보기로 볼 수 있는 것보다 컸다(최대 928 대 37). 숫자는 화면에 적히는 값일 뿐이고,
    리스트에 6건이 보이는데 5건이라고 적혀 있으면 그건 그냥 틀린 숫자다 — 반대 방향도
    똑같이 틀린 숫자다.

    후기를 **싣지 않는 목록에서도 센다.** 본문은 카드 수만큼 곱해지지만 개수는 리스트
    길이 하나라 공짜고, 목록과 상세가 다른 숫자를 주면 같은 가게가 화면마다 달라진다.

    **`score` 는 건드리지 않는다** — 그 값은 추천 점수의 평판 성분이 읽는 값이라
    (`recsys/content.py` 의 `quality`) 후기 몇 건이 평균에 들어가면 콜드스타트에 순위가
    흔들린다(`app/user_reviews.py` 머리말). 그래서 `score` 는 여전히 카카오가 전체
    리뷰로 낸 평균이고, 옆의 개수만 우리가 가진 수다.
    """
    return Store(
        source_platform=s.get("source_platform"),
        id=s["store_id"],
        name=s.get("name") or "",
        location=s.get("address") or "",
        # 좌표를 그대로 내보낸다. FE 가 주소를 지오코딩해서 쓰고 있었는데(`지하 1층`
        # 같은 접미사에서 자주 실패한다) 서버는 정확한 값을 갖고 있었다.
        lat=_coord(s.get("lat")),
        lon=_coord(s.get("lon")),
        category=s.get("cat3") or s.get("cat2") or "",
        # 필터가 쓰는 라벨과 같은 함수에서 나온다 — 카드에 찍힌 업종과 `cat=` 로 고를 수
        # 있는 값이 어긋나면 '이 카드가 왜 걸렸나' 를 설명할 수 없다.
        category_group=filters.category_group(s.get("cat2")),
        score=s.get("review_avg"),
        tags=_tags(s),
        review_count=svc.review_count(s["store_id"]) + user_reviews.count_for(s["store_id"]),
        reviews=reviews,
        summary=s.get("ai_title"),
        subway=_subway(s),
        # `open_headline` 은 싣지 않는다. 수집 순간 카카오 화면에 찍혀 있던 문자열이라
        # 2,795곳 중 1,932곳이 '영업 중' 으로 굳어 있고, 새벽에 봐도 그대로다.
        # '지금 영업 중인가' 는 `open_now` 가 요청 시점에 계산한다(`app/hours.py`).
        open_hours=_open_hours(s),
        parking=s.get("parking_summary"),
    )


def _review(r: dict, *, original_images: bool = False) -> Review:
    # 리뷰 칩은 리뷰 줄의 `tags`(필터 칸 이름 — 리뷰어가 고른 강점·네이버 키워드 + 본문 표현, AI 파이프라인
    # `reco/review_tags.py` 가 단다)를 먼저 쓴다. 칩 글자가 필터·카드 칩과 같아야 "혼밥 리뷰 N건" 이 목록에서
    # 보인다. `tags` 가 없는 옛 데이터는 예전처럼 수집 원본의 `strength_tags`(맛·친절·가성비·분위기·주차).
    # 빈 목록도 값이다 — '맛' 만 받은 리뷰는 칩이 없는 게 맞으므로 `or` 로 되돌아가지 않는다.
    return Review(
        source_platform=r.get("source_platform"),
        id=r.get("review_id"),
        store_id=r["store_id"],
        content=(r.get("content") or "").strip(),
        score=r.get("rating"),
        images=[url if original_images else catalog_media.public_url(url, variant="thumb")
                for url in r.get("photo_urls") or []],
        reviewer_id=r.get("author_id"),
        date=r.get("date"),
        tags=r["tags"] if isinstance(r.get("tags"), list) else (r.get("strength_tags") or []),
        like_count=r.get("like_count") or 0,
    )


def _user_review(r: dict) -> Review:
    """우리 사용자 후기 한 건(`app/user_reviews.py: as_store_reviews`) → `Review`.

    `_review` 와 달리 `catalog_media.public_url(..., variant="thumb")` 를 **태우지
    않는다.** 저건 카카오가 준 원격 주소를 우리가 받아 둔 사본으로 바꾸는 일인데,
    우리 후기의 `images` 는 이미 우리가 내보내는 최종 URL(`/media/…`)이다. 한 번 더
    태우면 없는 주소가 된다. `original_images` 를 받지 않는 것도 같은 이유다 —
    '수집 원본' 이라는 다른 주소가 존재하지 않아서, 어느 쪽이든 같은 URL 이다.
    """
    return Review(**r)


def _price(m: dict) -> int | None:
    """카카오는 '가격 없음'을 숫자로 보낸다 — -1(변동/미표기), 0(미입력).

    그대로 흘리면 클라이언트에 -1원, 0원으로 찍힌다. 전체 메뉴의 44%가 여기 해당한다
    (49,057개 중 21,706개, 546곳). 값이 아니라 결측이므로 None 으로 바꾼다.
    -1 중 300건만 price_text="변동가격" 이 함께 오고 나머지는 설명도 없다.
    """
    p = m.get("price")
    if not isinstance(p, int) or isinstance(p, bool) or p <= 0:
        return None
    return p


def _food(store_id: str, idx: int, m: dict, *, original_images: bool = False) -> Food:
    return Food(
        source_platform=m.get("source_platform"),
        id=idx,
        store_id=store_id,
        name=m.get("menu_name") or "",
        price=_price(m),
        price_text=m.get("price_text"),
        image=(m.get("photo_url") if original_images else
               catalog_media.public_url(m.get("photo_url"), variant="thumb")),
        description=m.get("description"),
        is_recommend=bool(m.get("is_recommend")),
    )


def load_all() -> None:
    """card_id 매핑을 만든다. 데이터 파일이 바뀌었을 때만 다시 만든다.

    번호는 파일에 **고정돼 있다**(`app/card_ids.py`). 예전에는 정렬 인덱스를 그대로
    썼는데, 그러면 가게가 하나만 늘어도 번호가 전부 밀려 찜·학습·집계가 조용히
    어긋났다. 이제 같은 가게는 데이터를 갈아도 같은 번호를 유지한다.
    """
    global _mtime
    svc.load()
    cur = tuple(sorted(svc._mt.items()))
    with _lock:
        if _mtime == cur and cards:
            return
        ids = sorted(svc.all_store_ids())
        mapping, fresh = card_ids.assign(card_ids.current(), ids)
        if fresh:
            # 새 가게가 보였다 = 데이터가 갱신됐다. state 볼륨에 적어 둔다(기준선은
            # 이미지 안이라 읽기 전용이다). 실패해도 그만이다 — 배정이 결정적이라
            # 다음 기동에도 같은 번호가 나온다.
            card_ids.save(mapping)
            observe.log("card_ids.extend", new=len(fresh), total=len(mapping),
                        sample=fresh[:5])
        cards.clear()
        card_id_by_store.clear()
        store_id_by_card.clear()
        for sid in ids:
            cid = mapping[sid]
            s = svc.get_store(sid) or {}
            cards[cid] = Card(card_id=cid, store=_store(s, []), image=None)
            card_id_by_store[sid] = cid
            store_id_by_card[cid] = sid
        _mtime = cur


def get_card(card_id: int, n_reviews: int = LIST_REVIEW, *,
             original_images: bool = False,
             include_user_reviews: bool = False) -> Card | None:
    """카드 한 장. 리뷰와 사진은 요청할 때 채운다(매번 다 담으면 응답이 무거워진다).

    `include_user_reviews` 는 **상세에서만** 참이다(`main.py: get_card`). 목록은 카드
    수만큼 곱해지는 자리라 한 장당 후기 몇 건씩 붙으면 응답이 금세 커진다.
    """
    load_all()
    sid = store_id_by_card.get(card_id)
    if sid is None:
        return None
    s = svc.get_store(sid) or {}
    # 우리 후기를 **맨 앞에 고정한다.** 표현력 순에 섞어 넣으면, 상세 미리보기가 5건뿐이라
    # (`PREVIEW_REVIEW`) 리뷰 수백 건짜리 가게에서 사용자 글이 그대로 묻힌다. 사용자가
    # 자기 글을 못 보는 것이 이 작업의 출발점이었으므로 순서가 아니라 자리로 보장한다.
    mine = [_user_review(r) for r in user_reviews.as_store_reviews(sid)] \
        if include_user_reviews else []
    reviews = mine + [_review(r, original_images=original_images)
                      for r in svc.get_reviews(sid, limit=n_reviews)]
    photos = svc.get_photos(sid, limit=CARD_IMAGES)
    hero = svc.hero_photo(sid, for_card=True)
    if hero:
        photos = ([hero["url"]] + [url for url in photos if url != hero["url"]])[:CARD_IMAGES]
    return Card(
        card_id=card_id,
        store=_store(s, reviews),
        image=(hero["url"] if original_images else catalog_media.public_url(hero["url"]))
              if hero else None,
        images=[url if original_images else catalog_media.public_url(url) for url in photos],
    )


def get_menus(card_id: int, *, original_images: bool = False) -> list[Food]:
    load_all()
    sid = store_id_by_card.get(card_id)
    if sid is None:
        return []
    return [_food(sid, i, m, original_images=original_images)
            for i, m in enumerate(svc.get_menus(sid, limit=300))]


def get_reviews(card_id: int, limit: int | None = None, *,
                original_images: bool = False) -> list[Review]:
    """"리뷰 더보기" 가 부르는 길. 우리 후기를 **항상** 맨 앞에 붙인다.

    상세(`get_card`)와 달리 여기는 옵션이 없다. 상세에서 우리 후기를 보고 더보기를
    눌렀는데 그 화면에서 사라지면, 사용자에게는 글이 지워진 것과 구분되지 않는다.

    `limit` 은 **합친 뒤** 자른다. 카카오 쪽에서 먼저 자르면 `limit` 을 준 호출자가
    요청한 것보다 많이 받는다.
    """
    load_all()
    sid = store_id_by_card.get(card_id)
    if sid is None:
        return []
    out = ([_user_review(r) for r in user_reviews.as_store_reviews(sid)]
           + [_review(r, original_images=original_images)
              for r in svc.get_reviews(sid, limit=limit or 10**9)])
    return out[:limit] if limit else out


def to_card_ids(store_ids: list[str]) -> list[int]:
    load_all()
    return [card_id_by_store[s] for s in store_ids if s in card_id_by_store]


def to_store_id(card_id: int) -> str | None:
    load_all()
    return store_id_by_card.get(card_id)
