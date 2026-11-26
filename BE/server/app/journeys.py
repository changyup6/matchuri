# -*- coding: utf-8 -*-
"""길찾기와 설문 — 추천의 **결과**를 재는 자리.

찜(`app/bookmarks.py`)과 마찬가지로 이 모듈은 `recsys` 를 **하나도 부르지 않는다.**
길찾기도 설문도 취향 모델·`shown`·노출 로그를 건드리지 않는다. 건드리면 팀-드래프트
인터리빙의 like 율이 오염되고, 그러면 정책 비교가 무의미해진다
(`tests/test_journeys.py::test_길찾기와_설문은_추천을_바꾸지_않는다` 가 잠근다).

**왜 길찾기인가.** 방문을 GPS 로 재려면 백그라운드 위치 추적이 필요하고, 역삼동처럼
건물이 밀집한 곳에서는 반경 100m 안에 수십 곳이 들어와 가게를 특정할 수 없다. 길찾기는
사용자가 **그 가게를 명시적으로 지목**한 순간이라 가게가 정확하고, 기기 부하도 없다.
다만 길찾기는 방문 의사이지 방문 확인이 아니다 — 방문 여부는 설문에서 직접 묻는다.

**"3시간 뒤"는 저장하지 않는다.** `pending()` 이 조회 시점에 `now - at` 을 계산할 뿐이다.
서버에 타이머를 돌려도 알릴 수단(푸시)이 아직 없으므로 스케줄러는 의미가 없다. 앱이
열릴 때 물어보면 그때 계산해서 답한다 — 사용자당 길찾기가 몇 건뿐이라 계산이 싸다.
나중에 푸시를 붙여도 이 함수를 그대로 재사용한다.

영속화는 찜과 같은 방식이다 — `state/directions.jsonl` 과 `state/surveys.jsonl` 에
**추가 전용**으로 쌓고, 기동 때 순서대로 다시 먹인다(`restore`).
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from app import state

#: 설문을 **권하는** 시점(길찾기 + 이만큼). 식사와 이동 시간을 넉넉히 본다.
#:
#: **서버는 이걸로 거르지 않는다.** 언제 물을지는 클라이언트가 정한다 — 앱이 `hours_ago`
#: 를 보고 배너를 띄울 시점을 결정하고, 서버는 후보를 다 준다. 그래야 개발 중에 3시간을
#: 기다리지 않고 흐름을 확인할 수 있고, 나중에 값을 바꿀 때 배포가 필요 없다.
#: `POST /directions` 응답의 `survey_at` 이 이 값으로 계산된 **권장 시점**이다.
SURVEY_AFTER = timedelta(hours=3)

#: 이보다 오래된 길찾기는 묻지 않는다. 일주일 전 방문은 기억이 남아 있지 않다.
SURVEY_EXPIRE = timedelta(days=7)

#: 한 번에 물을 후보 수. 배너에 리스트로 띄우므로 이보다 길면 화면에서 쓸 수 없다.
#: 넘치면 **최근 길찾기부터** 준다 — 기억이 선명한 쪽이 응답 품질이 높다.
MAX_PENDING = 10

_lock = threading.Lock()

#: user_id -> {card_id: 길찾기 누른 시각(ISO)}. 같은 카드를 다시 누르면 **갱신**한다 —
#: 어제 길찾기하고 오늘 또 했으면 물어야 하는 것은 오늘 방문이다(찜과 반대 판단).
_directions: dict[str, dict[int, str]] = {}

#: user_id -> {card_id: 설문에 답한 시각(ISO)}. 시각을 들고 있는 이유: 답한 뒤에 또
#: 길찾기했다면 **다른 방문**이므로 다시 물어야 한다. 단순 집합이면 그걸 구분할 수 없다.
_answered: dict[str, dict[int, str]] = {}

#: user_id -> {card_id: 응답 내용}. `_answered` 는 **언제** 답했나만 들고 있어서
#: "무엇이라 답했나" 를 물으면 답할 수 없었다. 집계(`recsys/policy.py: survey_stats`)는
#: 파일을 직접 훑지만 그건 관리자 호출이고, 마이페이지는 앱이 열릴 때마다 부른다.
#: 찜·길찾기와 같은 규칙이다 — 추가 전용 로그가 진실이고 이건 그것을 접어 둔 것이다.
#:
#: **카드당 하나이고 마지막이 이긴다.** `answer()` 의 중복 규칙이 그렇게 잠겨 있다 —
#: 다시 길을 찾고 또 가서 답하면 그게 이번 방문의 답이다.
_answers: dict[str, dict[int, dict]] = {}

#: 답한 순번. `at` 이 초 단위라(`state.now()`) 같은 초에 들어온 둘의 순서가 뒤집힌다.
#: 파일도 시간순이라 복원할 때 매기는 순번이 실시간과 같은 순서를 낸다.
_answer_seq = 0

#: user_id -> {card_id: [photo_id]}. 올린 순서를 유지한다 — 사용자가 고른 순서가
#: 그대로 화면 순서가 된다. 파일은 `media` 볼륨에 있고 여기엔 id 만 둔다.
_photos: dict[str, dict[int, list[str]]] = {}


def _parse(at: str | None) -> datetime | None:
    """ISO 문자열을 datetime 으로. 로그가 손상돼도 서버가 죽지 않아야 한다.

    `state.now()` 가 타임존을 붙여 쓰므로 정상 값은 항상 aware 다. naive 가 들어오면
    비교에서 터지니 버린다 — 한 줄을 잃는 것이 요청을 잃는 것보다 낫다.
    """
    if not at:
        return None
    try:
        dt = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else None


def record(user_id: str, store_id: str | None, card_id: int,
           map_app: str, mode: str) -> str:
    """길찾기를 눌렀다. 돌려주는 것은 기록 시각(ISO).

    멱등이 아니다 — 같은 카드를 다시 누르면 시각이 갱신되고 파일에도 한 줄 더 쌓인다.
    그 줄들이 '몇 번 길을 찾았나' 를 재는 원본이 된다.
    """
    at = state.now()
    with _lock:
        _directions.setdefault(user_id, {})[card_id] = at
    state.record_direction(user_id, store_id, card_id, map_app, mode)
    return at


def pending(user_id: str, now: datetime | None = None,
            after_hours: float = 0.0) -> list[tuple[int, str]]:
    """설문 후보 `(card_id, 길찾기 시각)` — **최근 순**, 최대 `MAX_PENDING` 개.

    고르는 조건 셋.
      1. 길찾기 후 `after_hours` 이상 지났다 — **기본 0, 즉 거르지 않는다**
      2. `SURVEY_EXPIRE` 를 넘지 않았다 (오래된 것은 기억이 없어 버린다)
      3. 그 길찾기 **이후에** 답한 기록이 없다

    시점 판단을 클라이언트로 넘긴 이유: 서버가 3시간을 막고 있으면 개발 중에 흐름을
    확인할 수 없고, 값을 바꿀 때마다 배포해야 한다. 앱은 응답의 `hours_ago` 로 직접
    고르면 된다. 서버 쪽에서 걸러 받고 싶으면 `after_hours` 를 주면 된다.
    """
    now = now or datetime.now().astimezone()
    min_age = timedelta(hours=max(0.0, after_hours))
    with _lock:
        marks = dict(_directions.get(user_id) or {})
        answered = dict(_answered.get(user_id) or {})

    out: list[tuple[int, str, datetime]] = []
    for card_id, at in marks.items():
        dt = _parse(at)
        if dt is None:
            continue
        age = now - dt
        if age < min_age or age > SURVEY_EXPIRE:
            continue
        answered_at = _parse(answered.get(card_id))
        if answered_at is not None and answered_at >= dt:
            continue                      # 이 길찾기에 대해서는 이미 답했다
        out.append((card_id, at, dt))

    out.sort(key=lambda r: r[2], reverse=True)
    return [(c, a) for c, a, _ in out[:MAX_PENDING]]


def answer(user_id: str, store_id: str | None, card_id: int,
           visited: bool, answers: dict, source: str | None = None) -> bool:
    """설문 응답을 받는다. 돌려주는 것은 **새로 기록됐나**.

    **길찾기 기록을 요구하지 않는다.** 앱이 방문 판단을 스스로 하고 설문만 보내는 것이
    기본 경로다 — 서버가 길찾기 로그를 갖고 있어야 할 이유는 없었다(정책 성적의 앵커로
    쓰이던 것을 노출 로그로 옮겼다. `recsys/policy.py: survey_stats`).

    중복은 두 갈래로 막는다. 표본이 부풀면 집계를 믿을 수 없다.
      * 길찾기 기록이 **있으면** 그 길찾기 뒤에 답한 적이 있는지 본다 — 다시 길을 찾고
        또 갔으면 다시 물어야 하므로, 새 길찾기가 문을 다시 연다.
      * 없으면 그 카드에 **한 번이라도** 답했으면 무시한다. 문을 다시 열어 줄 근거가
        없기 때문이다(앱이 같은 카드로 두 번 보내면 두 번째가 버려진다).

    `source` 를 안 주면 길찾기 기록 유무로 정한다 — `"direction"`(배너를 거쳤다) 또는
    `"app"`(앱이 직접 판단해 보냈다). 소급이 안 되는 정보라 지금 남겨 둔다.
    """
    with _lock:
        at_direction = (_directions.get(user_id) or {}).get(card_id)
        answered_at = _parse((_answered.get(user_id) or {}).get(card_id))
        dt = _parse(at_direction)
        if at_direction is None:
            if answered_at is not None:
                return False               # 이미 답했고 문을 다시 열 근거가 없다
        elif dt is not None and answered_at is not None and answered_at >= dt:
            return False                   # 이 길찾기에 대해서는 이미 답했다
        if source is None:
            source = "direction" if at_direction is not None else "app"
        now = state.now()
        _answered.setdefault(user_id, {})[card_id] = now
        # '언제' 와 '무엇' 을 **같은 자물쇠 안에서** 함께 넣는다. 따로 넣으면 그 사이에
        # 읽은 마이페이지가 답한 기록은 있는데 내용이 없는 항목을 본다.
        _remember(user_id, card_id, store_id, visited, answers, now)
    # 길찾기로부터 몇 시간 뒤에 답했나. 3시간 전 응답도 **거절하지 않고 기록**한다 —
    # 집계가 이 값으로 걸러낼 수 있고, SURVEY_AFTER 가 적절한지 데이터로 볼 수 있다.
    hours_after = None
    answered_dt = _parse(now)
    if dt is not None and answered_dt is not None:
        hours_after = round((answered_dt - dt).total_seconds() / 3600, 1)
    state.record_survey(user_id, store_id, card_id, visited, answers, hours_after,
                        source)
    return True


def _remember(user_id: str, card_id: int, store_id: str | None, visited: bool,
              answers: dict, at: str) -> None:
    """응답 내용을 사람별로 접어 둔다. **`_lock` 을 쥔 채로 부를 것.**

    **방문 근거(`arrived`·`closest_m`·`dwell_min`)는 담지 않는다.** 그 셋은 앱이 위치로
    계산한 값이라 이탈 분석에만 쓰인다. 사용자에게 되돌려 줄 것이 아니고, 화면에 실으면
    "서버가 내 위치를 알고 있다" 로 읽힌다. 파일에는 남아 있으니 집계는 그대로 된다.
    """
    global _answer_seq
    _answer_seq += 1
    review = answers.get("review")
    _answers.setdefault(user_id, {})[card_id] = {
        "card_id": card_id, "store_id": store_id, "visited": bool(visited),
        "satisfaction": answers.get("satisfaction"),
        # 옛 자유 문자열 후기(`review` 가 str)는 축이 없어 화면 모양이 다르다. 목록에서는
        # 없는 것으로 둔다 — `user_reviews._entry` 가 가게 화면에서 하는 판단과 같다.
        "review": dict(review) if isinstance(review, dict) else None,
        "not_visited_reason": answers.get("not_visited_reason"),
        "at": at, "seq": _answer_seq}


def history(user_id: str | None, limit: int | None = None,
            offset: int = 0) -> list[dict]:
    """그 사람이 답한 설문 — **최근 순**. 마이페이지가 읽는다.

    **안 갔다고 답한 것도 준다.** '내가 답한 설문' 의 목록이지 '내가 쓴 후기' 의 목록이
    아니다. 거르는 것은 화면의 몫이라 `visited` 를 그대로 실어 보낸다.

    사진은 **여기서 그때그때 붙인다.** 저장해 두면 사용자가 지운 사진이 계속 보인다
    (사진의 주인은 `_photos` 다).

    가게 화면의 후기(`app/user_reviews.py`)와 **다른 것**이다. 저쪽은 남에게 보이는
    글이라 방문·세 줄·빈칸을 다 거르지만, 여기는 자기 기록이라 답한 대로 다 보인다.
    """
    if not user_id:
        return []
    with _lock:
        rows = list((_answers.get(user_id) or {}).values())
    rows.sort(key=lambda r: (r["at"], r["seq"]), reverse=True)
    rows = rows[offset:] if offset else rows
    if limit is not None:
        rows = rows[:limit]
    return [{"card_id": r["card_id"], "store_id": r["store_id"],
             "visited": r["visited"], "satisfaction": r["satisfaction"],
             "review": r["review"],
             "not_visited_reason": r["not_visited_reason"],
             "images": photos_of(user_id, r["card_id"]),
             "at": r["at"]} for r in rows]


def answered_history_count(user_id: str | None) -> int:
    """그 사람이 답한 설문 수 — 마이페이지가 개수만 물을 때."""
    if not user_id:
        return 0
    with _lock:
        return len(_answers.get(user_id) or {})


def has_direction(user_id: str, card_id: int) -> bool:
    """그 사용자가 이 카드로 길을 찾은 적이 있나.

    **설문의 전제 조건이 아니다**(2026-09-16). 앱이 방문 판단을 스스로 하고 설문만
    보내므로 길찾기 기록 없이도 답할 수 있다. 남겨 둔 이유는 `answer()` 의 중복 규칙이
    이 값으로 갈라지고, 관리자 화면이 '배너를 거친 응답' 을 세는 데 쓰기 때문이다.
    """
    return card_id in (_directions.get(user_id) or {})


def photos_of(user_id: str, card_id: int) -> list[str]:
    """그 사람이 그 카드에 붙인 사진 id. 올린 순서."""
    return list((_photos.get(user_id) or {}).get(card_id) or [])


def photo_count(user_id: str) -> int:
    """그 사람이 올린 사진 총 수 — `media.MAX_PER_USER` 상한을 재는 자리다."""
    return sum(len(v) for v in (_photos.get(user_id) or {}).values())


def apply_photo(user_id: str, card_id: int, photo_id: str, action: str) -> bool:
    """사진 기록 하나를 색인에 반영. 기동 복원과 실시간 요청이 같은 길을 쓴다.

    찜과 같은 성질이다 — 같은 사진을 두 번 더해도 한 번이고, 없는 것을 지워도 조용하다.
    `False` 는 '무효한 줄' 이지 실패가 아니다(복원이 줄 수를 세는 데 쓴다).
    """
    if not user_id or not photo_id or not isinstance(card_id, int) \
            or isinstance(card_id, bool):
        return False
    with _lock:
        per = _photos.setdefault(user_id, {})
        ids = per.setdefault(card_id, [])
        if action == "add":
            if photo_id not in ids:
                ids.append(photo_id)
        elif action == "remove":
            if photo_id in ids:
                ids.remove(photo_id)
        else:
            return False
        if not ids:
            per.pop(card_id, None)
        return True


def add_photo(user_id: str, store_id: str | None, card_id: int, photo_id: str,
              meta: dict | None = None) -> None:
    """사진을 색인에 넣고 기록한다. 파일은 이미 저장돼 있어야 한다."""
    apply_photo(user_id, card_id, photo_id, "add")
    state.record_survey_photo(user_id, store_id, card_id, photo_id, "add", meta)


def remove_photo(user_id: str, store_id: str | None, card_id: int,
                 photo_id: str) -> bool:
    """색인에서 빼고 지웠다고 기록한다. 그 사람 사진이 아니면 `False`."""
    if photo_id not in photos_of(user_id, card_id):
        return False
    apply_photo(user_id, card_id, photo_id, "remove")
    state.record_survey_photo(user_id, store_id, card_id, photo_id, "remove")
    return True


def answered_count(user_id: str | None) -> int:
    if not user_id:
        return 0
    return len(_answered.get(user_id) or {})


def direction_count(user_id: str | None) -> int:
    if not user_id:
        return 0
    return len(_directions.get(user_id) or {})


def apply_direction(user_id: str, card_id: int, at: str | None) -> bool:
    """복원 전용 — 파일에 쓰지 않고 메모리만 되살린다."""
    if not user_id or not isinstance(card_id, int) or isinstance(card_id, bool):
        return False
    with _lock:
        _directions.setdefault(user_id, {})[card_id] = at or state.now()
    return True


def apply_survey(user_id: str, card_id: int, at: str | None,
                 row: dict | None = None) -> bool:
    """복원 전용 — 답한 사실을 되살린다. `row` 를 주면 **내용까지** 되살린다.

    `row` 가 선택인 이유는 이 함수가 원래 '답했나' 만 되살렸고(중복 방지가 그것만
    필요로 한다), 그 계약에 기대는 호출부가 있기 때문이다. 마이페이지가 생기면서
    내용도 필요해졌지만, 내용이 없는 옛 줄을 만나도 중복 방지는 계속 돌아야 한다.
    """
    if not user_id or not isinstance(card_id, int) or isinstance(card_id, bool):
        return False
    at = at or state.now()
    with _lock:
        _answered.setdefault(user_id, {})[card_id] = at
        if row is not None:
            _remember(user_id, card_id, row.get("store_id"),
                      bool(row.get("visited")), row.get("answers") or {}, at)
    return True


def restore() -> dict:
    """세 파일을 순서대로 다시 먹인다. 돌려주는 것은 먹인 줄 수.

    **`surveys.jsonl` 을 빠뜨리면 재시작 후 이미 답한 설문을 또 묻는다.** 두 파일을
    모두 읽어야 한다. 순서도 중요하다 — 길찾기를 먼저 먹여야 답한 기록이 어느 길찾기에
    대한 것인지 비교할 대상이 생긴다.
    """
    n_dir = n_srv = n_pho = 0
    for row in state.read_directions():
        if apply_direction(row.get("user_id"), row.get("card_id"), row.get("at")):
            n_dir += 1
    for row in state.read_surveys():
        if apply_survey(row.get("user_id"), row.get("card_id"), row.get("at"), row):
            n_srv += 1
    # 사진도 함께 되살린다 — 없으면 재시작 뒤에 이미 올린 사진이 안 보이고, 장수
    # 상한도 처음부터 다시 세어 3장을 또 올릴 수 있다.
    for row in state.read_survey_photos():
        if apply_photo(row.get("user_id"), row.get("card_id"),
                       row.get("photo_id"), row.get("action")):
            n_pho += 1
    return {"directions": n_dir, "surveys": n_srv, "photos": n_pho}


def all_directions() -> dict[str, dict[int, str]]:
    """전체(관리자·테스트용). 사본이 아니므로 읽기만 할 것."""
    return _directions
