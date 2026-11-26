# -*- coding: utf-8 -*-
"""'지금 영업 중인가' 를 서버가 계산한다.

**왜 서버가 하나.** 규칙이 세 겹인데 하나라도 틀리면 눈에 띄게 잘못된 화면이 나온다.
앱이 둘(Flutter·Next.js)이라 각자 짜면 각자 다르게 틀린다.

경계 셋:

1. **자정을 넘기는 영업** — 전체 15,738건 중 **2,567건(16.3%)** 이 `11:00 ~ 02:00`
   처럼 닫는 시각이 여는 시각보다 작다. `open <= now <= close` 로 비교하면 이 16%가
   하루 종일 '영업 종료' 로 나온다. 역삼동이라 술집·심야식당이 그만큼 많다.
2. **새벽에는 어제 영업분을 봐야 한다** — 토요일 01:00 에 토요일 시간표만 보면
   '아직 안 열었다' 가 된다. 실제로는 금요일 `11:00 ~ 02:00` 이 아직 안 끝났다.
3. **브레이크타임** — 영업 시간 안이어도 `15:00 ~ 17:30` 에는 닫혀 있다.
   3,081건이 있고 형식은 `"HH:MM ~ HH:MM 라벨"` 로 일정하다.

**시각을 넷으로 나눠 내보낸다.** `until` 은 *이 상태가 끝나는 시각*이라 상태에 따라
가리키는 대상이 바뀐다 — 배지 한 줄("22:00 영업 종료")에는 그게 맞지만, 그것만 주면
"오늘 몇 시까지 하나" 와 "브레이크가 언제 걸리나" 를 **동시에** 보여 줄 수 없다.
`open` 인데 15:00 에 브레이크가 있으면 `until` 은 15:00 이고, 22:00 이라는 사실은
응답에서 사라진다. 그래서 `opens_at` · `closes_at` · `break_start` · `break_end` 를
따로 싣고 **해당 없는 것은 `None`** 으로 둔다. `until` 은 그중 하나를 골라 준 값이라
남겨 둔다(기존 클라이언트도 그대로 돈다).

ISO 8601 로 주는 이유는 `"11:00"` 만 주면 오늘인지 내일인지 알 수 없기 때문이다.

시각 기준은 **서버 시간대(Asia/Seoul)** 다. 역삼동 가게를 다루므로 그게 맞다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

#: `datetime.weekday()` 가 월=0 … 일=6 이라 그 순서에 맞춘다.
DAYS = "월화수목금토일"

DAY_MINUTES = 24 * 60

#: `"15:00 ~ 17:30 브레이크타임"` — 원본 문자열을 그대로 받았을 때의 대비책이다.
#: 정상 경로에서는 `app/model.py: OpenHours._split_breaks` 가 이미 쪼개 놓는다.
_BREAK = re.compile(r"^\s*(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})")


def _minutes(hhmm: str | None) -> int | None:
    """`"11:30"` -> 690. 못 읽으면 `None` — 한 줄 때문에 응답이 깨지면 안 된다."""
    if not hhmm:
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", hhmm)
    if m is None:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59:
        return None
    return h * 60 + mi


def _shift(row) -> tuple[int, int] | None:
    """그 요일의 `(여는 분, 닫는 분)`. 닫는 쪽이 작으면 **자정을 넘긴 것**으로 본다."""
    start = _minutes(getattr(row, "open", None))
    end = _minutes(getattr(row, "close", None))
    if start is None or end is None:
        return None
    if end <= start:
        end += DAY_MINUTES            # 11:00 ~ 02:00 -> 11:00 ~ 26:00
    return start, end


def _breaks(row) -> list[tuple[int, int]]:
    """그 요일의 브레이크 구간들, **시작 시각 순으로**.

    정렬하는 이유: 아래에서 '아직 남아 있는 첫 브레이크' 를 앞에서부터 찾는다.
    원본 순서를 믿으면 브레이크가 둘인 가게에서 나중 것을 먼저 집을 수 있다.
    """
    out: list[tuple[int, int]] = []
    for item in getattr(row, "breaks", None) or []:
        a = _minutes(getattr(item, "start", None))
        b = _minutes(getattr(item, "end", None))
        if a is None or b is None:
            # 모델을 거치지 않은 원본 문자열. 정상 경로에서는 오지 않는다.
            m = _BREAK.match(str(item))
            if m is None:
                continue
            a, b = _minutes(m.group(1)), _minutes(m.group(2))
        if a is None or b is None:
            continue
        if b <= a:
            b += DAY_MINUTES
        out.append((a, b))
    return sorted(out)


#: 시간표를 못 읽을 때의 답. 모든 시각이 `None` 이다 — 없는 것을 0 이나 빈 문자열로
#: 채우면 앱이 "0시 영업 종료" 를 그린다.
_NOTHING = {"state": "unknown", "until": None, "opens_at": None,
            "closes_at": None, "break_start": None, "break_end": None,
            "day": None}


def _session(rows, weekday: int, now_min: int):
    """지금 걸쳐 있는 영업 구간, 없으면 **다음** 구간.

    `(요일, 시작, 끝, 브레이크들, 지금_안에_있나)` — 시각은 모두 오늘 자정 기준 분이라
    음수(어제)와 1440 이상(내일 이후)이 나온다. 부르는 쪽이 그대로 날짜로 바꾼다.

    `-1` 부터 훑는 이유는 새벽에 **전날 영업이 아직 안 끝났을 수 있기** 때문이다
    (`11:00 ~ 02:00`). `+7` 까지 보므로 일주일에 하루만 여는 가게도 다음 영업을 찾는다.

    지금 걸쳐 있는 구간은 `ahead <= 0` 에서만 나온다(내일 이후는 시작이 항상 미래다).
    그래서 걸친 구간을 만나면 바로 돌려주고, 미래 구간은 **첫 번째만** 기억해 둔다.
    """
    by_day = {getattr(r, "day", None): r for r in rows}
    upcoming = None
    for ahead in range(-1, 8):
        day = DAYS[(weekday + ahead) % 7]
        row = by_day.get(day)
        if row is None:
            continue
        shift = _shift(row)
        if shift is None:
            continue                  # 그날은 쉰다(또는 정보가 없다)
        off = ahead * DAY_MINUTES
        start, end = shift[0] + off, shift[1] + off
        breaks = [(a + off, b + off) for a, b in _breaks(row)]
        if start <= now_min < end:
            return day, start, end, breaks, True
        if upcoming is None and start > now_min:
            upcoming = (day, start, end, breaks, False)
    return upcoming


def state_at(rows, now: datetime | None = None) -> dict:
    """지금 영업 중인가 — `app/model.py: OpenNow` 그대로의 dict 를 돌려준다.

    `state` 는 `open` · `break` · `closed` · `unknown` 중 하나다. 시간표가 아예 없거나
    모든 요일이 비어 있으면 `unknown` 이고, 그때는 **모든 시각이 `None`** 이다.

    시각 넷(`opens_at`·`closes_at`·`break_start`·`break_end`)은 전부 **같은 영업 구간
    하나**를 설명한다 — 지금 열려 있으면 그 구간, 닫혀 있으면 다음 구간이다. 그래서
    닫혀 있을 때도 "다음에 열면 몇 시까지 하고 브레이크는 언제인가" 가 함께 나온다.
    `until` 은 그중 지금 배지에 쓸 하나를 골라 준 것이다(아래 머리말 참고).
    """
    now = now or datetime.now().astimezone()
    rows = list(rows or [])
    if not any(_shift(r) for r in rows):
        return dict(_NOTHING)

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    now_min = now.hour * 60 + now.minute

    def at(minutes: int) -> str:
        return (midnight + timedelta(minutes=minutes)).isoformat(timespec="seconds")

    found = _session(rows, now.weekday(), now_min)
    if found is None:
        # 영업 구간이 있는데 지금도 앞으로 8일도 안 걸리는 경우. 위 `_session` 이
        # 같은 요일까지 보므로 실데이터에서는 나오지 않지만, 시각을 지어내지는 않는다.
        return dict(_NOTHING, state="closed")

    day, start, end, breaks, inside = found

    if inside:
        # 지금 이 브레이크 안인가. 아니면 **닫기 전에 남아 있는** 첫 브레이크는 언제인가.
        active = next(((a, b) for a, b in breaks if a <= now_min < b), None)
        coming = next(((a, b) for a, b in breaks if now_min < a < end), None)
        if active is not None:
            bs, be = active
            state, opens_at, until = "break", at(be), at(be)
        else:
            bs, be = coming if coming is not None else (None, None)
            state, opens_at = "open", None
            # 브레이크가 먼저 오면 배지는 그 시각을 말해야 한다("15:00 브레이크").
            until = at(bs) if bs is not None else at(end)
    else:
        # 닫혀 있다. 넷은 **다음** 영업 구간을 가리킨다.
        bs, be = breaks[0] if breaks else (None, None)
        state, opens_at, until = "closed", at(start), at(start)

    return {"state": state, "until": until, "opens_at": opens_at,
            "closes_at": at(end),
            "break_start": at(bs) if bs is not None else None,
            "break_end": at(be) if be is not None else None,
            "day": day}
