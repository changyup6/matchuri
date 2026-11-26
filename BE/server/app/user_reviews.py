# -*- coding: utf-8 -*-
"""사용자 후기 — 설문에 쓴 세 줄을 가게 화면에 되돌려 준다.

설문(`app/journeys.py`)은 추천이 맞았는지 재려고 만든 것이라, 사용자가 쓴 글이 어디에도
보이지 않았다. **쓰기만 하고 읽지 않는 데이터**였다. 여기서 그 글을 가게에 붙인다.

**저장소를 따로 만들지 않는다.** `state/surveys.jsonl` 에 이미 필요한 것이 다 있다
(누가·어느 가게에·언제·무엇을 썼나). 같은 사실을 두 파일에 적으면 언젠가 갈라진다.
기동 때 그 파일을 다시 읽어 가게별로 모아 둘 뿐이다.

**카카오 리뷰에 섞는다.** 2026-09-21 에 뒤집었다. 그전까지는 `user_reviews` 라는 다른
배열로만 내보냈다 — "출처가 다르니 화면에서 나눠 보여 달라" 는 약속이었다. 그런데 앱은
그 배열을 **파싱하는 코드가 한 줄도 없고**, 가게 상세의 리뷰 섹션은 `store.reviews` 만
그린다. 즉 사용자가 쓴 글은 서버에 정상으로 쌓이면서 화면에는 한 건도 안 떴다. 첫 문단이
말하는 "쓰기만 하고 읽지 않는 데이터" 가 배열만 바뀐 채 그대로였던 것이다. `FE/` 는 팀
규정상 우리가 고칠 수 없으므로, **앱이 이미 그리는 자리에 우리가 넣는다.**

섞어도 잃는 것이 없다는 근거: 앱의 리뷰 카드(`_ReviewCard`)는 작성자 이름을 아예 그리지
않는다. 나눠 보여 달라던 이유가 "어느 쪽 글인지 화면에서 구분" 이었는데 화면이 애초에
구분해 보여 주지 않았다. 대신 **가릴 수단은 남긴다** — `source_platform: "matchuri"` 와
`id >= ID_BASE`(10^12) 대역이면 나중에 신고·삭제를 붙일 때 대상을 코드로 고를 수 있다.
카카오 id 는 양수, 네이버는 음수라 대역이 겹치지 않는다(`app/model.py: Review.id`).

`user_reviews` 배열은 **그대로 둔다.** 세 줄이 축별로 나뉜 원본 모양이라 화면을 고칠 수
있게 되면 그쪽이 낫고, 이미 그 배열을 읽는 쪽(마이페이지·관리자)이 있다.

**`store.score` 는 건드리지 않는다.** 그 값은 추천 점수의 평판 성분이 읽는 값이다
(`recsys/content.py` 의 `quality`). 후기 한두 건이 평균에 들어가면 콜드스타트에 순위가
흔들리고, "왜 이 가게가 올라왔나" 를 설명할 수 없게 된다. 표시와 점수는 다른 문제다 —
점수에 넣을지는 후기가 쌓인 뒤에 따로 정한다.

`store.review_count` 에는 섞은 만큼 들어간다(`app/loader.py: _store` 가 `count_for` 를
더한다). 리스트에 6건이 보이는데 머리말이 5건이라고 적혀 있으면 그건 그냥 틀린 숫자다.
2026-09-22 부터 그 숫자의 기준 자체가 **우리가 들고 있는 리뷰 수**다 — 수집 원본에 적힌
카카오 총수가 아니다(`recsys/store.py: review_count` 머리말). 이건 표시용 개수일 뿐이고
추천이 읽는 값이 아니다 — 추천은 `recsys` 가 원본 데이터에서 직접 읽는다.

한 사람이 같은 가게에 두 번 답하면(다시 길찾기하고 또 방문) **마지막 것만 남는다.**
같은 사람의 후기가 두 줄 보이는 것보다 최신 하나가 맞다.

**표현력은 자격이 아니라 순서다.** 처음엔 문턱이었다 — 줄마다 자기 축에서 속성을
말했는지(레벨 2)를 보고 못 미치면 싣지 않았다. 카카오 리뷰가 표현력 순으로 서는데
(`recsys/store.py: _sort_reviews`) 우리 후기만 "맛있어요 / 괜찮아요 / 좋아요" 를 그대로
실으면 잣대가 둘이 된다는 이유였다. 2026-09-21 에 없앴다. 운영 카드 2,795장을 전수
조회했을 때 후기가 실린 카드는 **1장**이었고 그 내용은 Swagger 예제 문장 그대로였다 —
사용자 후기가 실제로는 **하나도** 없었다는 뜻이다. 카카오 실데이터 28,633건으로 재면
세 축 동시 L2 는 카페 1.0%·그 외 1.4%다. 문턱이 아니라 벽이었다. 게다가 떨어진 사실이
응답(200)·마이페이지(보임)·로그(`review_len` 만) 어디에도 남지 않아 **아무도 몰랐다.**

지금은 세 줄이 다 차 있으면 싣고, 표현력은 `for_store` 의 **순서**와 응답의
`review_levels` 에만 쓴다(`recsys/expressiveness.py`). 약한 후기를 지울 이유가 없다 —
뒤로 보내면 된다. 문턱을 1 로 낮추는 절충도 하지 않았다. 게이트가 남아 있는 한
"왜 내 글이 안 보이나" 는 되풀이되고, 그때도 아무 데도 기록이 남지 않는다.
"""
from __future__ import annotations

import threading
from datetime import datetime

from app import identities, journeys, media, state
from recsys import expressiveness

#: 설문 줄 → 표현력 축. `mood` 는 분위기·공간(ambience)이다.
LINE_AXIS = {"taste": "taste", "price": "price", "mood": "ambience"}

#: 우리 후기의 리뷰 id 대역. 카카오는 양수, 네이버는 그 id 를 뒤집은 음수라
#: (`app/model.py: Review.id`) 10^12 위로 올려 두면 셋이 절대 겹치지 않는다.
#: `store.reviews` 에 섞은 뒤 "이건 우리 글인가" 를 id 만 보고 답할 수 있어야 한다 —
#: `source_platform` 은 수집 과정에서 null 이 되는 줄이 이미 있어서 혼자로는 못 믿는다.
ID_BASE = 10**12

#: 만족도 → 별점. 설문은 3단계인데 카카오 리뷰는 1~5 별점이라 그 축으로 옮긴다.
#: 가운데(3)를 중심으로 양끝을 잡았다 — 4/3/2 로 좁히면 만족·불만이 화면에서 구분되지
#: 않고, 사용자가 고른 것은 원래 '좋다/보통/나쁘다' 세 칸이다. 만족도를 안 준 설문은
#: `None` 이고, 그건 별점 없는 카카오 리뷰와 같은 자리에 선다.
SCORE_OF = {"good": 5, "soso": 3, "bad": 1}

_lock = threading.Lock()

#: store_id -> {user_id: 후기}. 사람당 가게당 하나이므로 dict 로 둔다(마지막이 이긴다).
_by_store: dict[str, dict[str, dict]] = {}

#: 먹인 순번. **`at` 만으로는 정렬이 안 된다** — 초 단위라(`state.now()` 가
#: `timespec="seconds"`) 같은 초에 들어온 후기 둘의 순서가 뒤집힌다. 파일도 시간순이라
#: 복원할 때 매기는 순번이 실시간과 같은 순서를 낸다.
_seq = 0


def levels_of(taste: str, price: str, mood: str) -> dict[str, int]:
    """설문 세 줄의 표현력 레벨(0~3) — 줄마다 **자기 축**에서 잰다.

    `_entry` 는 정렬 키로, `main.py: post_survey` 는 응답의 `review_levels` 로 같은 값을
    쓴다. 두 곳이 따로 재면 "앱에 1 이라 떴는데 순서는 다르다" 가 된다.
    """
    lines = {"taste": taste, "price": price, "mood": mood}
    return {k: expressiveness.level_of(lines[k], axis) for k, axis in LINE_AXIS.items()}


def _entry(row: dict) -> tuple[str, str, dict] | None:
    """설문 한 줄에서 후기를 뽑는다. 후기가 아니면 `None`.

    거르는 것 셋:
      1. 방문하지 않은 응답 — 안 가 본 가게의 후기는 후기가 아니다
      2. 세 줄 형식이 아닌 것 — 옛 자유 문자열 후기(`review` 가 str)는 싣지 않는다.
         축이 없어서 화면 모양이 다르고, 그때는 '보인다' 는 약속 없이 받은 글이다
      3. 한 줄이라도 빈 것 — 빈 칸이 가게 화면에 그대로 나간다

    표현력은 여기서 거르지 않는다. 재기는 재되(`_expr`) 순서에만 쓴다 — 모듈 docstring.
    """
    if not row.get("visited"):
        return None
    uid, sid = row.get("user_id"), row.get("store_id")
    if not uid or not sid:
        return None
    answers = row.get("answers") or {}
    review = answers.get("review")
    if not isinstance(review, dict):
        return None
    taste, price, mood = (str(review.get(k) or "").strip()
                          for k in ("taste", "price", "mood"))
    if not (taste and price and mood):
        return None
    levels = levels_of(taste, price, mood)
    return sid, uid, {
        "user_id": uid, "card_id": row.get("card_id"),
        "satisfaction": answers.get("satisfaction"),
        "taste": taste, "price": price, "mood": mood,
        "at": row.get("at") or "",
        # 표현력 정렬 키 — (세 축 레벨 합, 최고 레벨). 카카오 리뷰의 (깊이, 합, 폭)과 같은
        # 재료를 세 줄 형식에 맞게 접은 것이다. 응답에는 싣지 않는다(관리자가 순서를 따질 때 본다).
        "_expr": (sum(levels.values()), max(levels.values())),
        # `seq` 는 `apply` 가 채운다(정렬용). 여기서 채우면 `_lock` 밖이 된다.
    }


def apply(row: dict) -> bool:
    """설문 한 줄을 먹인다(기동 복원과 실시간이 같은 길을 쓴다). 먹였으면 True."""
    got = _entry(row)
    if got is None:
        return False
    sid, uid, rec = got
    global _seq
    with _lock:
        _seq += 1
        rec["seq"] = _seq
        _by_store.setdefault(sid, {})[uid] = rec
    return True


def restore() -> int:
    """`surveys.jsonl` 을 순서대로 다시 먹인다. 돌려주는 것은 먹인 줄 수."""
    n = 0
    for row in state.read_surveys():
        if apply(row):
            n += 1
    return n


def _rows_for(store_id: str | None, limit: int | None = None) -> list[dict]:
    """그 가게의 후기 원본 줄 — **표현력 순, 같으면 최근 순**.

    `for_store`(우리 모양)와 `as_store_reviews`(카카오 모양)가 둘 다 이걸 쓴다. 정렬 키가
    두 벌이 되면 같은 후기가 `user_reviews` 와 `store.reviews` 에서 다른 순서로 서고,
    그때 어느 쪽이 맞는지 따질 근거가 없다. 잣대는 한 곳에만 둔다.

    키는 `_expr` = (세 축 레벨 합, 최고 레벨) → `at` → `seq`. `at` 이 초 단위라
    같은 초에 들어온 둘은 `seq` 가 가른다.
    """
    if not store_id:
        return []
    with _lock:
        rows = list((_by_store.get(store_id) or {}).values())
    rows.sort(key=lambda r: (r.get("_expr", (0, 0)), r["at"], r["seq"]), reverse=True)
    return rows if limit is None else rows[:limit]


def _photos(r: dict) -> list[str]:
    """후기에 붙은 사진 URL — **그때그때** 붙인다(저장하면 지운 사진이 남는다)."""
    photos = journeys.photos_of(r["user_id"], r["card_id"]) if r["card_id"] else []
    return [media.url_of(p) for p in photos]


def _kakao_date(at: str | None) -> str | None:
    """ISO 8601 → `"2026-09-21 16:16:34"`.

    우리는 `2026-09-21T16:16:34+09:00` 로 적어 두는데(`state.now()`), 카카오 리뷰의
    `date` 는 `"2026-08-19 09:57:16"` 꼴이다. 한 배열에 섞을 것이라 모양이 다르면 앱의
    날짜 파서가 우리 줄에서만 깨진다. 읽을 수 없는 값이면 `None` — 그 필드는 원래
    nullable 이고, 거짓 날짜를 지어내는 것보다 없는 편이 낫다.
    """
    try:
        return datetime.fromisoformat(at).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def as_store_reviews(store_id: str | None) -> list[dict]:
    """그 가게의 후기를 **카카오 리뷰와 같은 모양**(`app/model.py: Review`)으로.

    왜 섞는지는 모듈 docstring 에 있다. 순서는 `for_store` 와 같다 — 같은 `_rows_for`.

    세 줄은 축 이름을 붙여 한 본문으로 접는다(`맛 · …\\n가격 · …\\n분위기 · …`).
    카카오 리뷰 본문에도 줄바꿈이 들어 있어서(`Review.content` 의 설명) 앱은 이미
    여러 줄을 그릴 줄 안다. 축 이름을 떼면 "2인 4만원, 적당" 이 무슨 말인지 알 수 없다.

    **`reviewer_id` 는 `None` 이다.** 여기에 넣을 수 있는 값은 `user_id` 뿐인데, 이
    서버에서 그 값은 그 사람의 열쇠다 — 쥐고 있으면 찜·취향·설문을 다 읽고 쓸 수 있다
    (`app/users.py` 의 발급 규칙이 '추측하기 어렵게' 인 이유). 가게 화면은 누구나 본다.
    """
    return [{"source_platform": "matchuri",
             "id": ID_BASE + r["seq"],
             "store_id": store_id,
             "content": f"맛 · {r['taste']}\n가격 · {r['price']}\n분위기 · {r['mood']}",
             "score": SCORE_OF.get(r["satisfaction"]),
             "images": _photos(r),
             "reviewer_id": None,
             "date": _kakao_date(r["at"]),
             # 태그와 좋아요는 카카오가 자기 화면에서 모으는 것이라 우리에게는 없다.
             # null 이 아니라 빈 값으로 둔다 — 앱이 길이만 보고 그리기 때문이다.
             "tags": [],
             "like_count": 0}
            for r in _rows_for(store_id)]


def for_store(store_id: str | None, limit: int | None = None) -> list[dict]:
    """그 가게의 후기 — **표현력 순, 같으면 최근 순**. `app/model.py: UserReviewOut` 모양이다.

    처음엔 최근 순만이었다("몇 건이라 다시 세우지 않는다"). 그런데 카카오 리뷰는 표현력 순으로
    서는데 우리 후기만 시간순이면 같은 화면에서 잣대가 둘이다. 키는 `_expr` = (세 축 레벨 합,
    최고 레벨). "국물이 걸쭉하고 계란이 퍽퍽했어요"(L3) 가 "고등어초회가 신선했어요"(L2)
    앞에 오고, 그 뒤에 "맛있어요"(L1) 가 선다. 같으면 최근 것이 먼저다.
    정렬은 `_rows_for` 한 곳에 있다 — `as_store_reviews` 와 순서가 갈라지지 않게.

    **약한 후기도 실린다 — 뒤에 설 뿐이다.** 여기가 순서를 세우므로 지울 이유가 없다.

    사진과 이름은 **여기서 그때그때 붙인다.** 저장해 두면 사용자가 사진을 지우거나
    이름을 바꿨을 때 옛 값이 남는다(사진은 `journeys` 가, 이름은 등록부가 주인이다).

    **`user_id` 를 내보내지 않는다.** 이 서버에서 `user_id` 는 그 사람의 열쇠다 —
    가지고 있으면 그 사람의 찜·취향·설문을 다 읽고 쓸 수 있다(`app/users.py` 의 발급
    규칙이 '추측하기 어렵게' 인 이유가 그것이다). 가게 화면에 실으면 아무나 줍는다.
    """
    return [{"name": identities.name_of(r["user_id"]),
             "satisfaction": r["satisfaction"],
             "taste": r["taste"], "price": r["price"], "mood": r["mood"],
             "images": _photos(r),
             "at": r["at"]}
            for r in _rows_for(store_id, limit)]


def count_for(store_id: str | None) -> int:
    """그 가게에 달린 우리 후기 수 — `as_store_reviews` 가 내보낼 건수와 **같다**.

    `_rows_for` 는 거르지 않고 정렬만 하므로 여기서도 거르지 않는다. 둘이 어긋나면
    화면 숫자와 목록이 어긋난다(`app/loader.py: _store` 가 이 값을 더한다).
    본문·사진을 만들지 않고 개수만 세는 길이라 목록 응답에서도 부담이 없다.
    """
    if not store_id:
        return 0
    with _lock:
        return len(_by_store.get(store_id) or ())


def count() -> int:
    """전체 후기 수 — 지표·관리자용."""
    with _lock:
        return sum(len(v) for v in _by_store.values())
