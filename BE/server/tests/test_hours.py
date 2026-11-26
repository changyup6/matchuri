# -*- coding: utf-8 -*-
"""'지금 영업 중인가' 의 경계.

여기서 잠그는 것은 세 가지다. 하나라도 틀리면 화면에 바로 보이는 오류가 된다.

1. **자정을 넘기는 영업** — 실데이터 15,738건 중 2,567건(16.3%)이 `11:00 ~ 02:00`
   꼴이다. `open <= now <= close` 로 비교하면 이 16%가 하루 종일 '영업 종료' 가 된다.
2. **새벽에는 어제 영업분** — 토요일 01:00 에 토요일 시간표만 보면 '아직 안 열었다' 다.
   실제로는 금요일 영업이 아직 안 끝났다.
3. **브레이크타임** — 영업 구간 안이어도 닫혀 있다. 그리고 브레이크가 앞에 있으면
   `until` 은 닫는 시각이 아니라 **브레이크 시작**이어야 한다.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app import hours
from app.model import OpenHours

KST = datetime.now().astimezone().tzinfo
DAYS = "월화수목금토일"


def at(y, m, d, hh, mi=0) -> datetime:
    return datetime(y, m, d, hh, mi, tzinfo=KST)


def row(day, open_=None, close=None, breaks=()):
    return OpenHours(day=day, open=open_, close=close, breaks=list(breaks))


def week(open_="11:00", close="22:00", breaks=()):
    """일주일 내내 같은 시간표."""
    return [row(d, open_, close, breaks) for d in DAYS]


# 2026-09-14 는 월요일이다.
MON = (2026, 9, 14)
TUE = (2026, 9, 15)


# ────────────────────────────────────────────── 기본
def test_영업중이면_닫는_시각을_준다():
    s = hours.state_at(week(), at(*MON, 13))
    assert s["state"] == "open"
    assert s["until"].startswith("2026-09-14T22:00")


def test_열기_전이면_오늘_여는_시각을_준다():
    s = hours.state_at(week(), at(*MON, 9))
    assert s["state"] == "closed"
    assert s["until"].startswith("2026-09-14T11:00")


def test_닫은_뒤에는_내일_여는_시각을_준다():
    s = hours.state_at(week(), at(*MON, 23))
    assert s["state"] == "closed"
    assert s["until"].startswith("2026-09-15T11:00")


@pytest.mark.parametrize("hh,mi,expect", [
    (10, 59, "closed"),
    (11, 0, "open"),      # 여는 시각 정각은 영업 중
    (21, 59, "open"),
    (22, 0, "closed"),    # 닫는 시각 정각은 영업 종료
])
def test_경계_시각(hh, mi, expect):
    assert hours.state_at(week(), at(*MON, hh, mi))["state"] == expect


# ────────────────────────────────────────────── 자정 넘김 (16.3%)
def test_자정_넘기는_영업은_저녁에_영업중이다():
    """`11:00 ~ 02:00`. 단순 비교로는 하루 종일 '종료' 가 나온다."""
    s = hours.state_at(week("11:00", "02:00"), at(*MON, 23))
    assert s["state"] == "open"
    assert s["until"].startswith("2026-09-15T02:00"), "닫는 시각이 다음날이어야 한다"


def test_자정_넘기는_영업은_새벽에도_영업중이다():
    """화요일 01:00 — 월요일 영업분이 아직 안 끝났다. 이걸 놓치면 심야식당이 전부 닫힌다."""
    s = hours.state_at(week("11:00", "02:00"), at(*TUE, 1))
    assert s["state"] == "open"
    assert s["until"].startswith("2026-09-15T02:00")


def test_자정_넘긴_영업이_끝나면_닫힌다():
    s = hours.state_at(week("11:00", "02:00"), at(*TUE, 3))
    assert s["state"] == "closed"
    assert s["until"].startswith("2026-09-15T11:00")


def test_새벽에_어제만_영업하고_오늘은_휴무여도_영업중이다():
    """월요일 심야 영업이 화요일 새벽까지 이어지는데 화요일은 정기휴무인 경우."""
    rows = [row(d, "18:00", "03:00") for d in DAYS]
    rows[DAYS.index("화")] = row("화", None, None)      # 화요일 휴무
    s = hours.state_at(rows, at(*TUE, 1))
    assert s["state"] == "open", "어제 영업분을 보지 않았다"
    assert s["until"].startswith("2026-09-15T03:00")


# ────────────────────────────────────────────── 브레이크타임
BREAK = ["15:00 ~ 17:30 브레이크타임"]


def test_브레이크타임_중에는_break_이고_다시_여는_시각을_준다():
    s = hours.state_at(week(breaks=BREAK), at(*MON, 16))
    assert s["state"] == "break"
    assert s["until"].startswith("2026-09-14T17:30")


def test_브레이크_전에는_브레이크_시작에서_끊긴다():
    """`until` 이 22:00 이면 '아직 6시간 열려 있다' 고 오해하게 된다."""
    s = hours.state_at(week(breaks=BREAK), at(*MON, 13))
    assert s["state"] == "open"
    assert s["until"].startswith("2026-09-14T15:00")


def test_브레이크_뒤에는_닫는_시각을_준다():
    s = hours.state_at(week(breaks=BREAK), at(*MON, 19))
    assert s["state"] == "open"
    assert s["until"].startswith("2026-09-14T22:00")


@pytest.mark.parametrize("hh,mi,expect", [
    (14, 59, "open"),
    (15, 0, "break"),     # 브레이크 시작 정각
    (17, 29, "break"),
    (17, 30, "open"),     # 브레이크 끝 정각
])
def test_브레이크_경계(hh, mi, expect):
    assert hours.state_at(week(breaks=BREAK), at(*MON, hh, mi))["state"] == expect


def test_라벨이_없는_브레이크도_읽는다():
    s = hours.state_at(week(breaks=["15:00 ~ 17:30"]), at(*MON, 16))
    assert s["state"] == "break"


# ────────────────────────────────────────────── 휴무·정보 없음
TIMES = ("until", "opens_at", "closes_at", "break_start", "break_end")


def test_시간표가_없으면_unknown():
    s = hours.state_at([], at(*MON, 13))
    assert s["state"] == "unknown"
    # **시각을 지어내지 않는다.** 하나라도 0 이나 "" 로 채우면 앱이 '0시 영업 종료' 를 그린다.
    assert all(s[k] is None for k in TIMES)
    assert s["day"] is None


def test_모든_요일이_비어_있으면_unknown():
    s = hours.state_at([row(d) for d in DAYS], at(*MON, 13))
    assert s["state"] == "unknown"
    assert all(s[k] is None for k in TIMES)


def test_휴무일은_건너뛰고_다음_영업일을_찾는다():
    rows = week()
    rows[DAYS.index("화")] = row("화", None, None)      # 화요일 휴무
    s = hours.state_at(rows, at(*MON, 23))
    assert s["state"] == "closed"
    assert s["until"].startswith("2026-09-16T11:00"), "휴무인 화요일을 건너뛰어야 한다"


def test_한_요일만_영업해도_찾아낸다():
    rows = [row(d) for d in DAYS]
    rows[DAYS.index("토")] = row("토", "10:00", "18:00")
    s = hours.state_at(rows, at(*MON, 13))
    assert s["state"] == "closed"
    assert s["until"].startswith("2026-09-19T10:00"), "다음 토요일이어야 한다"


# ────────────────────────────────────────────── 망가진 값
@pytest.mark.parametrize("bad", ["25:00", "11:70", "열한시", "11", "", None])
def test_망가진_시각은_그_요일을_건너뛴다(bad):
    rows = week()
    rows[DAYS.index("월")] = row("월", bad, "22:00")
    s = hours.state_at(rows, at(*MON, 13))
    assert s["state"] == "closed", "못 읽는 요일을 영업 중으로 치면 안 된다"


def test_망가진_브레이크는_무시한다():
    s = hours.state_at(week(breaks=["브레이크타임 있음"]), at(*MON, 16))
    assert s["state"] == "open"


def test_모르는_요일_라벨은_무시한다():
    rows = week() + [row("매일", "00:00", "23:59")]
    assert hours.state_at(rows, at(*MON, 23))["state"] == "closed"


# ──────────────────────────────── 시각 넷 (`until` 하나로는 부족한 것들)


def test_영업중이면_브레이크와_종료를_함께_준다():
    """`until` 만 주면 22:00 이라는 사실이 응답에서 사라진다.

    브레이크가 앞에 있으면 `until` 은 브레이크 시작이어야 하지만(배지), 화면에는
    "오늘 22:00 까지" 도 같이 적어야 한다. 그래서 `closes_at` 을 따로 싣는다.
    """
    s = hours.state_at(week(breaks=BREAK), at(*MON, 13))
    assert s["state"] == "open"
    assert s["until"].startswith("2026-09-14T15:00")       # 배지 = 먼저 오는 것
    assert s["closes_at"].startswith("2026-09-14T22:00")   # 진짜 종료는 안 사라진다
    assert s["break_start"].startswith("2026-09-14T15:00")
    assert s["break_end"].startswith("2026-09-14T17:30")
    assert s["opens_at"] is None, "이미 열려 있다"


def test_브레이크가_지났으면_브레이크_시각은_null():
    """'해당 없음' 은 `null` 이다 — 지나간 브레이크를 남겨 두면 앱이 또 그린다."""
    s = hours.state_at(week(breaks=BREAK), at(*MON, 19))
    assert s["state"] == "open"
    assert s["break_start"] is None and s["break_end"] is None
    assert s["until"] == s["closes_at"]


def test_브레이크_중이면_다시_여는_시각이_opens_at_이다():
    s = hours.state_at(week(breaks=BREAK), at(*MON, 16))
    assert s["state"] == "break"
    assert s["opens_at"].startswith("2026-09-14T17:30")
    assert s["until"] == s["opens_at"]
    assert s["break_start"].startswith("2026-09-14T15:00")
    assert s["closes_at"].startswith("2026-09-14T22:00"), "브레이크가 종료를 덮지 않는다"


def test_닫혀_있으면_다음_영업을_통째로_준다():
    """`closed` 여도 "열면 몇 시까지 하고 브레이크는 언제인지" 를 미리 준다.

    상세 화면이 "오늘 11:00 ~ 22:00 (15:00 브레이크)" 를 그리려면 필요하다.
    한 번 더 물어보게 만들 이유가 없다.
    """
    s = hours.state_at(week(breaks=BREAK), at(*MON, 9))
    assert s["state"] == "closed"
    assert s["opens_at"].startswith("2026-09-14T11:00")
    assert s["until"] == s["opens_at"]
    assert s["closes_at"].startswith("2026-09-14T22:00")
    assert s["break_start"].startswith("2026-09-14T15:00")


def test_브레이크가_둘이면_먼저_오는_것을_준다():
    """원본 순서를 믿지 않는다 — 뒤에 적힌 브레이크를 먼저 집으면 배지가 틀린다."""
    s = hours.state_at(week(breaks=["18:00 ~ 19:00 저녁", "15:00 ~ 17:00 브레이크"]),
                       at(*MON, 13))
    assert s["break_start"].startswith("2026-09-14T15:00")


def test_day_는_새벽에_어제_요일이다():
    """`11:00 ~ 02:00` 을 토요일 01:00 에 보면 답은 **금요일** 시간표에서 나온다.

    앱이 `open_hours` 에서 강조할 줄을 고를 때 이 값을 봐야 한다 — 오늘 요일을
    강조하면 영업 중이라면서 '오늘 11:00 시작' 을 함께 띄우게 된다.
    """
    sat_1am = at(2026, 9, 19, 1)          # 2026-09-19 는 토요일이다
    s = hours.state_at(week("11:00", "02:00"), sat_1am)
    assert s["state"] == "open"
    assert s["day"] == "금"
    assert s["closes_at"].startswith("2026-09-19T02:00")


def test_day_는_닫혀_있으면_다음_영업일이다():
    s = hours.state_at(week(), at(*MON, 23))
    assert s["state"] == "closed"
    assert s["day"] == "화"


def test_일주일에_하루만_열어도_다음_영업을_찾는다():
    """`+7` 까지 훑는다 — 같은 요일 다음 주가 답일 수 있다."""
    s = hours.state_at([row("월", "11:00", "22:00")], at(*MON, 23))
    assert s["state"] == "closed"
    assert s["day"] == "월"
    assert s["opens_at"].startswith("2026-09-21T11:00")
