# -*- coding: utf-8 -*-
"""요일별 영업시간.

원본(`data/kakao/stores.jsonl`)의 `open_hours` 는 **수집 시점 그 주의 날짜가 박힌**
라벨을 쓴다 — `"수(9/2)"`. 그대로 내보내면 다음 주에 거짓말이 되므로 요일만 남긴다.

여기서 잠그는 것 넷:

1. 그 요일 변환과 `open`·`close` 가 `null` 이 되는 경우
2. **월~일 7개를 항상 채운다** — 배열 길이가 가게마다 다르면 "일요일에 여나" 를
   묻는 쪽이 매번 요일을 뒤져야 한다
3. `overnight` — `11:00 ~ 02:00` 인지. 앱이 `익일 02:00` 을 그릴 때 쓴다
4. `breaks` 를 **시각으로 쪼개서** 내보낸다 — 문자열을 앱이 쪼개면 앱 두 개가
   각자 다르게 틀린다

`open_status`(수집 순간에 카카오가 렌더한 문자열)는 **더 이상 나가지 않는다.**
'지금' 을 물으면 거짓말을 하는 값이었다 — 지금 상태는 응답 최상단 `open_now` 가
요청 시점에 계산한다(`app/hours.py`, `tests/test_hours.py`).
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import loader

DAYS = set("월화수목금토일")


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


@pytest.fixture(scope="module")
def stores(client):
    """카드 여러 장의 store 블록 (실데이터)."""
    return [c["store"] for c in client.get("/cards?limit=30").json()]


def test_카드에_영업시간이_붙는다(stores):
    assert any(s["open_hours"] for s in stores), "30장 중 하나도 시간표가 없다"


def test_요일만_남고_날짜는_떨어진다(stores):
    """`"수(9/2)"` -> `"수"`. 날짜를 그대로 두면 다음 주에 틀린 값이 된다."""
    for s in stores:
        for row in s["open_hours"]:
            assert row["day"] in DAYS, f"요일이 아니다: {row['day']!r}"
            assert not re.search(r"\d", row["day"]), "날짜가 남아 있다"


def test_시각은_HH_MM_이거나_null(stores):
    for s in stores:
        for row in s["open_hours"]:
            for key in ("open", "close"):
                v = row[key]
                assert v is None or re.fullmatch(r"\d{1,2}:\d{2}", v), f"{key}={v!r}"


def test_여는_시각과_닫는_시각은_함께_있거나_함께_없다(stores):
    """한쪽만 있으면 앱이 '언제까지인지 모르는 영업일' 을 그려야 한다."""
    for s in stores:
        for row in s["open_hours"]:
            assert (row["open"] is None) == (row["close"] is None)


def test_시간표가_없는_가게는_빈_배열이다(stores):
    """404 도 null 도 아니다 — 카드마다 분기하지 않게 한다."""
    for s in stores:
        assert isinstance(s["open_hours"], list)


def test_breaks_는_항상_배열이다(stores):
    for s in stores:
        for row in s["open_hours"]:
            assert isinstance(row["breaks"], list)
            assert all(b for b in row["breaks"]), "빈 문자열이 섞였다"


def test_휴무일은_open_close_가_null_이다():
    """원본에서 `time` 이 없는 날(대개 정기휴무)을 그대로 통과시키지 않는다."""
    rows = loader._open_hours({"open_hours": [
        {"day": "수(9/2)", "time": "11:00 ~ 21:00", "breaks": []},
        {"day": "일(9/6)", "time": None, "breaks": []},
    ]})
    by = {r.day: r for r in rows}
    assert (by["수"].open, by["수"].close) == ("11:00", "21:00")
    assert (by["일"].open, by["일"].close) == (None, None)


def test_요일이_빠져_있어도_일곱_줄을_채운다():
    """원본에 없는 요일도 `null` 줄로 채운다 — 2,443곳 중 86곳이 그렇다.

    길이가 가게마다 다르면 "일요일에 여나" 를 묻는 쪽이 매번 요일을 뒤져야 한다.
    """
    rows = loader._open_hours({"open_hours": [
        {"day": "수(9/2)", "time": "11:00 ~ 21:00", "breaks": []},
    ]})
    assert [r.day for r in rows] == list("월화수목금토일")
    assert [r.day for r in rows if r.open] == ["수"]


def test_요일을_못_읽으면_버린다():
    """거짓 요일을 만들어 내보내느니 그 줄을 버린다(빈 줄로 남는다)."""
    rows = loader._open_hours({"open_hours": [
        {"day": "매일", "time": "11:00 ~ 21:00", "breaks": []},
        {"day": "", "time": "11:00 ~ 21:00", "breaks": []},
        {"day": "금(9/4)", "time": "09:00 ~ 18:00", "breaks": []},
    ]})
    assert [r.day for r in rows if r.open] == ["금"]


def test_시간표가_아예_없으면_빈_배열_그대로():
    """7줄을 null 로 채우면 '일주일 내내 휴무' 라는 **다른 말**이 된다.

    352곳이 여기 해당한다. 정보가 없는 것과 쉬는 것은 다르다.
    """
    assert loader._open_hours({}) == []
    assert loader._open_hours({"open_hours": []}) == []
    assert loader._open_hours({"open_hours": [{"day": "매일", "time": "11:00 ~ 2"}]}) == []


def test_자정을_넘기면_overnight_가_참이다():
    """`11:00 ~ 02:00` — 전체 15,738건 중 2,567건(16.3%)이다.

    앱이 `익일 02:00` 으로 적으려면 이 플래그가 필요하다. 시각만 보고 판단하게 하면
    앱 두 개가 각자 다르게 틀린다.
    """
    rows = loader._open_hours({"open_hours": [
        {"day": "금(9/4)", "time": "11:00 ~ 02:00", "breaks": []},
        {"day": "토(9/5)", "time": "11:00 ~ 22:00", "breaks": []},
        {"day": "일(9/6)", "time": None, "breaks": []},
    ]})
    by = {r.day: r for r in rows}
    assert by["금"].overnight is True
    assert by["토"].overnight is False
    assert by["일"].overnight is False        # 쉬는 날은 넘길 영업이 없다


def test_breaks_는_시각으로_쪼개져_나간다():
    """`"15:00 ~ 17:30 브레이크타임"` 을 앱이 쪼개게 하지 않는다.

    쪼개는 곳이 둘이면(앱·서버) 화면의 배지와 시간표가 서로 다른 말을 한다.
    """
    rows = loader._open_hours({"open_hours": [
        {"day": "수(9/2)", "time": "11:00 ~ 21:00",
         "breaks": ["15:00 ~ 17:30 브레이크타임", "못 읽는 줄"]},
    ]})
    br = {r.day: r for r in rows}["수"].breaks
    assert len(br) == 1, "못 읽는 줄은 버린다 — 거짓 시각을 만들지 않는다"
    assert (br[0].start, br[0].end, br[0].label) == ("15:00", "17:30", "브레이크타임")


def test_open_status_는_더_이상_나가지_않는다(stores):
    """수집 순간에 굳은 문자열이라 '지금' 을 물으면 거짓말을 한다 — 필드를 없앴다.

    2,795곳 중 **1,932곳이 `영업 중`** 으로 굳어 있었다(수집을 낮에 돌렸다). 새벽에
    받아도 그 값이므로 화면에 그대로 띄우면 닫힌 가게가 영업 중으로 보인다. 지금
    상태는 응답 최상단의 `open_now` 가 요청 시점에 계산한다(`app/hours.py`).
    """
    assert not any("open_status" in s for s in stores)


def test_시간표는_추천을_바꾸지_않는다(client):
    """표시용 데이터다. 카드 순서가 이것 때문에 달라지면 안 된다."""
    a = [c["card_id"] for c in client.get("/cards?limit=10").json()]
    b = [c["card_id"] for c in client.get("/cards?limit=10").json()]
    assert a == b
