# -*- coding: utf-8 -*-
"""찜 — '나중에 볼' 카드를 담아 두는 목록.

**좋아요와 다른 것이다.** `POST /preferences` 의 like 는 취향 모델의 입력이고, 답한 카드는
`shown` 에 들어가 그 사람에게 다시 나오지 않는다. 찜은 그런 신호가 아니다 — 사용자가
'이 집 가 보자' 며 담아 두는 목록일 뿐이고, 담았다고 취향이 바뀌지도 않는다. 그래서
이 모듈은 `recsys` 를 **하나도 부르지 않는다**: 모델·`shown`·노출 로그가 찜 때문에
움직이면 팀-드래프트 인터리빙의 like 율이 오염된다
(`tests/test_bookmarks.py::test_찜은_추천을_하나도_바꾸지_않는다` 가 호출 전후를 비교해 잠근다).

담은 것은 **순서가 있다.** dict 의 삽입 순서를 그대로 쓰고(파이썬 3.7+ 보장) 목록은 그
역순으로 준다 — 방금 담은 것이 맨 위여야 한다. 지웠다 다시 담으면 맨 위로 온다.

영속화는 사용자 설정과 같은 방식이다 — `state/bookmarks.jsonl` 에 **추가 전용**으로
`{user_id, card_id, action, at}` 을 쌓고, 기동 때 순서대로 다시 먹인다(`restore`).
지운 것도 한 줄 남긴다: 그 사실이 파일에 없으면 복원이 지운 찜을 되살린다.

한 사람당 `MAX_PER_USER` 개를 넘으면 **가장 오래된 것부터** 버린다. 상한이 없으면 한 사람이
2,795곳을 전부 담아 메모리와 파일이 부풀고, 목록 화면도 쓸 수 없게 된다.
"""
from __future__ import annotations

import logging
import threading

from app import observe, state

#: 한 사람이 담을 수 있는 찜 수. 넘으면 오래된 것부터 버린다(그 사실도 파일에 남긴다).
MAX_PER_USER = 500

_lock = threading.Lock()
#: user_id -> {card_id: 담은 시각}. dict 의 **삽입 순서가 담은 순서**다.
_marks: dict[str, dict[int, str]] = {}


def _evict(marks: dict[int, str]) -> list[int]:
    """상한을 넘은 만큼 **가장 오래된 것부터** 버린다(메모리만). 락을 잡은 채 부른다.

    파일에 남기는 일은 호출자가 한다 — 담은 줄을 먼저 쓰고 그 다음에 버린 줄을 써야
    파일을 순서대로 읽었을 때 '담아서 오래된 게 빠졌다' 가 그대로 보인다.
    """
    dropped: list[int] = []
    while len(marks) > MAX_PER_USER:
        oldest = next(iter(marks))
        del marks[oldest]
        dropped.append(oldest)
    return dropped


def add(user_id: str, card_id: int) -> tuple[str, int, bool]:
    """찜을 담는다. 이미 담긴 카드면 **아무것도 바꾸지 않는다**(멱등).

    돌려주는 것은 `(담은 시각, 그 사람의 찜 수, 이번에 새로 담겼나)` 다. 이미 있으면
    처음 담은 시각을 그대로 준다 — '언제 담았나' 가 다시 누른 시각으로 바뀌면 목록의
    순서가 사용자가 기억하는 순서와 달라진다.
    """
    with _lock:
        marks = _marks.setdefault(user_id, {})
        existing = marks.get(card_id)
        if existing is not None:
            return existing, len(marks), False
        at = state.now()
        marks[card_id] = at
        dropped = _evict(marks)
        count = len(marks)
    state.record_bookmark(user_id, card_id, "add")
    for old in dropped:                     # 버린 것도 파일에 남긴다(복원이 알아야 한다)
        state.record_bookmark(user_id, old, "remove")
    if dropped:
        observe.log("bookmark.evict", level=logging.WARNING, user_id=user_id,
                    dropped=dropped, cap=MAX_PER_USER)
    return at, count, True


def remove(user_id: str, card_id: int) -> tuple[bool, int]:
    """찜을 뺀다. 없던 것을 빼도 실패가 아니다 — `(정말 지웠나, 남은 수)`."""
    with _lock:
        marks = _marks.get(user_id) or {}
        removed = marks.pop(card_id, None) is not None
        count = len(marks)
    if removed:
        state.record_bookmark(user_id, card_id, "remove")
    return removed, count


def has(user_id: str | None, card_id: int) -> bool:
    """카드 응답의 `bookmarked` 가 이걸 쓴다. `user_id` 가 없으면 늘 False."""
    if not user_id:
        return False
    return card_id in (_marks.get(user_id) or {})


def count(user_id: str | None) -> int:
    if not user_id:
        return 0
    return len(_marks.get(user_id) or {})


def page(user_id: str, limit: int, offset: int = 0) -> list[tuple[int, str]]:
    """`(card_id, 담은 시각)` 을 **최신 순**으로. 목록 화면이 그대로 그린다."""
    marks = _marks.get(user_id) or {}
    newest = list(marks.items())[::-1]
    return newest[max(0, offset):max(0, offset) + max(0, limit)]


def apply(user_id: str, card_id: int, action: str, at: str | None) -> bool:
    """복원 전용 — 파일에 **쓰지 않고** 메모리만 되살린다(기동 때 순서대로 먹인다)."""
    if not user_id or not isinstance(card_id, int):
        return False
    with _lock:
        marks = _marks.setdefault(user_id, {})
        if action == "add":
            marks.pop(card_id, None)            # 다시 담았으면 순서도 그때로 옮긴다
            marks[card_id] = at or state.now()
            _evict(marks)
        elif action == "remove":
            marks.pop(card_id, None)
        else:
            return False
    return True


def restore() -> int:
    """`state/bookmarks.jsonl` 을 순서대로 다시 먹인다. 돌려주는 것은 먹인 줄 수.

    마지막 줄이 이긴다 — 담았다/뺐다를 순서대로 적용하면 그것이 곧 지금 상태다
    (사용자 레코드처럼 '마지막 레코드가 그 사용자' 인 방식과 같은 성질).
    """
    n = 0
    for row in state.read_bookmarks():
        if apply(row.get("user_id"), row.get("card_id"), row.get("action"),
                 row.get("at")):
            n += 1
    return n


def all_marks() -> dict[str, dict[int, str]]:
    """전체(관리자·테스트용). 사본이 아니라 원본이므로 읽기만 할 것."""
    return _marks
