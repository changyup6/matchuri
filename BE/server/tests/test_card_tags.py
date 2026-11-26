# -*- coding: utf-8 -*-
"""카드에 찍히는 태그와 필터가 보는 태그가 **같은 정의**인지.

**왜 필요한가.** 필터는 `가게 태그 ∪ 리뷰 태그` 전부로 고르는데 카드는 리뷰 태그를 개수 순으로
앞 5개만 띄웠다. 두 곳이 어긋나서 `가성비` 로 거른 카드에 가성비가 안 뜨는 일이 생겼다 —
멘노아지는 가게 태그에 `가성비` 가 있지만 리뷰 태그는 `{혼밥:13, 건강한:6}` 뿐이었다.
실서비스에서 가성비로 걸리는 1,835곳 중 **734곳(40%)**, `점심식사` 는 걸리는 764곳 **전부**가
그랬다. 고른 조건이 카드에 안 보이면 사용자에게는 그냥 오작동이다.

이제 `filters.card_tags` 하나가 그 가게의 태그를 정하고, 카드도 필터도 그것만 본다.
여기서 잠그는 것 여섯.
  ① 걸린 태그는 **반드시** 카드에 있다.
  ② 카드에 없는 태그로는 **걸리지 않는다**.
  ③ 표를 많이 받은 순이 아니라 **그 태그의 가게당 평균을 넘었나**로 고른다.
  ④ 점수를 그 가게 표 총합으로 나누지 않는다(표 1개짜리 가게가 100% 가 되던 문제).
  ⑤ 투표 수가 없는 가게 태그도 자리를 얻는다(`점심식사`·`야식` 은 투표에 아예 없다).
  ⑥ 카드 칩은 **필터 칸 이름**이다 — `데이트` 로 걸렀는데 `소개팅` 이 뜨지 않는다.

**경계값은 데이터를 따라 움직인다.** "맛 1표는 탈락" 같은 것은 규칙이 아니라 오늘 `맛` 의
가게당 평균이 1.13이라서 나오는 결과다. 표가 줄면 1표로도 평균을 넘는다. 그래서 여기서는
평균을 고정해 놓고 **규칙**(평균 이상이면 싣는다)만 잰다 — 실데이터 숫자를 박지 않는다.
"""
from __future__ import annotations

import pytest

from app import filters, loader

#: 멘노아지(실서비스 1815309929)와 같은 모양 — 가게 태그에만 가성비가 있다.
MENNOAJI = {"tags": ["양많은", "혼밥", "가성비"], "review_tags": {"혼밥": 13, "건강한": 6}}


@pytest.fixture
def stats(monkeypatch):
    """태그별 **가게당 평균 표 수**를 고정한다. 값 자체는 실서비스에서 잰 것이지만,
    테스트가 재는 것은 "평균 이상이면 싣는다" 는 규칙이지 이 숫자가 아니다."""
    avg = {"친절한": 5.59, "가성비": 2.49, "감성적": 1.28, "맛": 1.13,
           "혼밥": 0.71, "건강한": 0.39, "주차": 0.17}
    monkeypatch.setattr(filters, "_tag_stats", lambda: avg)
    return avg


@pytest.mark.parametrize("tag", ["감성적", "가성비", "주차"])
def test_평균_이상이면_싣고_미만이면_안_싣는다(stats, tag):
    """③. 경계는 그 태그의 가게당 평균이다 — 태그마다 다르고, 데이터가 바뀌면 같이 움직인다."""
    a = stats[tag]
    import math
    just_under = math.floor(a * 100) / 100          # 평균보다 아주 조금 적게
    slot = filters._slot_of[tag]                    # 감성적 -> 분위기
    assert filters.card_tags({"review_tags": {tag: just_under - 0.01}}) == []
    assert filters.card_tags({"review_tags": {tag: a}}) == [slot]
    assert filters.card_tags({"review_tags": {tag: a * 2}}) == [slot]


def test_표가_흔한_태그일수록_더_많이_받아야_한다(stats):
    """같은 1표라도 `주차`(평균 0.17)는 넘고 `친절한`(평균 5.59)은 못 넘는다."""
    assert filters.card_tags({"review_tags": {"주차": 1}}) == ["주차"]
    assert filters.card_tags({"review_tags": {"친절한": 1}}) == []


def test_표가_많다고_앞에_오지_않는다(stats):
    """`친절한` 5표(평균 5.59 -> 0.89)는 `감성적` 2표(평균 1.28 -> 1.56)에 밀린다."""
    s = {"tags": [], "review_tags": {"친절한": 5, "감성적": 2}}
    assert [k for k, _ in sorted(s["review_tags"].items(), key=lambda kv: -kv[1])][0] == "친절한"
    assert filters.card_tags(s) == ["분위기"]        # 감성적은 분위기 칸. 친절한은 평균 미달이라 실리지도 않는다


def test_그_가게_표_총합으로_나누지_않는다(stats):
    """④. 점유율로 재면 표가 1개뿐인 가게는 그 태그가 100% 라 무조건 1등이 된다."""
    only = {"tags": [], "review_tags": {"친절한": 1}}          # 점유율 100%
    many = {"tags": [], "review_tags": {"친절한": 12, "맛": 40}}  # 점유율 23%
    assert filters.card_tags(only) == []                       # 평균 5.59 미달
    assert "친절" in filters.card_tags(many)                    # 평균의 2.1배, 칸 이름으로


def test_걸린_태그는_반드시_카드에_있다(stats):
    """①."""
    for s in (MENNOAJI,
              {"tags": ["점심식사"], "review_tags": {}},
              {"tags": [], "review_tags": {"혼밥": 3}},
              {"tags": ["가성비", "혼밥", "양많은", "점심식사", "야식"],
               "review_tags": {"맛": 9, "친절한": 8, "감성적": 7}}):
        shown = set(filters.card_tags(s))
        for name in filters.client_tags(s):
            assert name in shown, (name, s, shown)


def test_카드에_없는_태그로는_걸리지_않는다(stats):
    """②. 상한에 잘려 나간 태그로 걸리면 예전 버그가 그대로 돌아온다."""
    s = {"tags": ["가성비", "혼밥", "양많은", "점심식사", "야식", "조용한",
                  "단체모임", "데이트", "술한잔", "주차", "분위기", "친절"],
         "review_tags": {"맛": 30, "친절한": 40}}
    shown = set(filters.card_tags(s))
    assert len(shown) == filters.CARD_TAGS          # 상한에 걸린 상황이어야 뜻이 있다
    assert filters.tag_names(s) - shown             # 실제로 잘린 것이 있다
    for name in filters.client_tags(s):
        assert name in shown


def test_투표가_없는_가게_태그도_자리를_얻는다(stats):
    """⑤. `점심식사`·`야식` 은 리뷰 투표에 아예 없다 — 여기서 빠지면 그 필터가 죽는다."""
    s = {"tags": ["점심식사", "야식"], "review_tags": {"맛": 10}}
    got = filters.card_tags(s)
    assert "점심식사" in got and "야식" in got
    assert {"점심식사", "야식"} <= filters.client_tags(s)


def test_가게_태그는_평균을_넘은_것과_못_넘은_것_사이에_선다(stats):
    """딱 평균만큼(1.0) 받은 셈으로 본다 — 그게 이 값의 뜻이다."""
    s = {"tags": ["점심식사"], "review_tags": {"감성적": 2, "친절한": 2}}
    #  감성적 2/1.28 = 1.56 · 점심식사 1.0 · 친절한 2/5.59 = 0.36 -> 친절한은 탈락
    assert filters.card_tags(s) == ["분위기", "점심식사"]


def test_멘노아지가_가성비로_걸리고_카드에도_뜬다(stats):
    #  `건강한` 은 필터 어휘에 없어 실리지 않는다 — 고를 수 없는 태그는 카드에도 안 띄운다
    assert filters.card_tags(MENNOAJI) == ["혼밥", "가성비", "양많은"]
    assert "가성비" in filters.client_tags(MENNOAJI)


def test_같은_점수면_순서가_늘_같다(stats):
    """뜻이 있는 순서는 아니지만, 같은 가게가 요청마다 다른 태그를 보이면 안 된다."""
    s = {"tags": ["점심식사", "가성비"], "review_tags": {}}   # 둘 다 1.0
    assert filters.card_tags(s) == ["가성비", "점심식사"]     # 이름 순
    assert filters.card_tags(s) == filters.card_tags({"tags": ["가성비", "점심식사"]})


def test_같은_칸의_태그는_한_번만_뜬다(stats):
    """`분위기` 와 `감성적` 은 사용자에게 같은 태그 하나다 — 칩이 둘 뜨면 안 된다.
    터프커피(233889658)가 그랬다: 가게 태그에 `감성적`, 리뷰 투표에 `분위기`."""
    s = {"tags": ["감성적", "분위기좋은"], "review_tags": {"감성적": 4}}
    got = filters.card_tags(s)
    assert got == ["분위기"]
    assert filters.client_tags(s) == {"분위기"}


def test_같은_뜻의_다른_이름도_한_칸이다(stats):
    """`분위기좋은`·`혼술`·`점심특선` 은 어느 칸에도 없어 버려지고 있었다. 카드에는 칸 이름으로 뜬다."""
    for raw, slot in (("분위기좋은", "분위기"), ("혼술", "술한잔"),
                      ("점심특선", "점심식사"), ("가족모임", "단체모임"),
                      ("조용한카페", "조용한")):
        assert raw in filters.TAGS[slot], raw
        s = {"tags": [raw], "review_tags": {}}
        assert filters.card_tags(s) == [slot]
        assert filters.client_tags(s) == {slot}


def test_카드_칩은_필터_칸_이름이다(stats):
    """⑥. 원본 이름을 찍으면 `데이트` 로 거른 카드에 사장님이 단 `소개팅` 이 뜬다(9/22 실서비스
    307곳 중 41곳 `소개팅` · 80곳 `데이트코스`). `친절` 칸도 `친절`·`친절한` 두 글자로 갈라졌다."""
    for slot, group in filters.TAGS.items():
        for raw in group:
            assert filters.card_tags({"tags": [raw], "review_tags": {}}) == [slot], raw
    assert filters.card_tags({"tags": ["소개팅"], "review_tags": {}}) == ["데이트"]
    assert filters.card_tags({"tags": [], "review_tags": {"친절한": 12}}) == ["친절"]
    # 어떤 가게든 카드 칩은 필터 어휘 안이다
    s = {"tags": ["데이트코스", "회식장소", "점심특선", "조용한카페"],
         "review_tags": {"친절한": 12, "감성적": 4, "맛": 9}}
    got = filters.card_tags(s)
    assert set(got) <= set(filters.TAGS), got
    assert got == sorted(set(got), key=got.index)          # 같은 칸이 두 번 뜨지 않는다


def test_고를_수_없는_이름은_카드에_안_뜬다(stats):
    """가게 태그에 카카오 키워드가 1,156종 섞여 온다 — `초복메뉴추천`·`강남역맛집` 같은
    홍보 문구가 카드에 떴다. 필터로 고를 수 없는 이름을 카드에 띄울 이유가 없다."""
    s = {"tags": ["초복메뉴추천", "강남역맛집", "가성비"], "review_tags": {}}
    assert filters.card_tags(s) == ["가성비"]


def test_카드가_쓰는_것과_필터가_쓰는_것이_같은_함수다(stats):
    assert loader._tags(MENNOAJI) == filters.card_tags(MENNOAJI)


def test_태그가_없으면_빈_목록(stats):
    assert filters.card_tags({}) == []
    assert filters.client_tags({}) == set()
