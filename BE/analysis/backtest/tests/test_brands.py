# -*- coding: utf-8 -*-
"""브랜드 정규화와 중복 억제 (PLAN 5절 'Step 1 → Step 1.5' 결정 4번).

규칙은 데이터를 보고 정했으므로 **실제로 나온 이름**으로 검증한다. 지키려는 것 두 가지.

  - 지점명(`역삼점`·`강남역점`·`2호점`·`본점`·꼬리 괄호)은 떼어낸다.
  - 이름 자체가 `점` 으로 끝나는 한 단어 가게(`홍반점`·`문익점`)는 **건드리지 않는다.**
"""
from __future__ import annotations

import numpy as np
import pytest

from analysis.backtest.scorers_menu import content_score, suppress_same_brand
from analysis.content import brands


# ────────────────────────────────────────────── 브랜드 키
@pytest.mark.parametrize("name,expected", [
    ("스타벅스 역삼점", "스타벅스"),
    ("파크루안 역삼점", "파크루안"),
    ("바나프레소 강남점", "바나프레소"),
    ("써브웨이 강남역점", "써브웨이"),
    ("고래불 강남역삼본점", "고래불"),
    ("땀땀 본점", "땀땀"),
    ("근성순대 2호점", "근성순대"),
    ("강남불백 강남1호점", "강남불백"),
    ("농민백암순대 강남직영점", "농민백암순대"),
    ("BBQ 역삼GS타워점", "BBQ"),
    ("태키커피(tackycoffee)", "태키커피"),
    ("솔솥 GFC몰점(강남파이낸스센터)", "솔솥"),
])
def test_지점명을_떼어낸다(name, expected):
    assert brands.brand_name(name) == expected


@pytest.mark.parametrize("name", [
    "홍반점", "청운반점", "흑룡반점", "호돌이반점",     # 중국집 — 이름이 곧 '반점'
    "문익점", "역삼주점", "강남아이스크림할인점",
    "명성양갈비전문점", "청담각짜장짬뽕전문점",
    "웅칼강남점",                                   # 공백이 없어 놓친다(문서에 적었다)
])
def test_이름_자체가_점으로_끝나면_건드리지_않는다(name):
    assert brands.brand_name(name) == name


def test_업태_낱말이_붙은_토큰은_지점이_아니다():
    assert not brands.is_branch_token("전문점")
    assert not brands.is_branch_token("반점")
    assert brands.is_branch_token("역삼점")
    assert brands.is_branch_token("본점")
    assert brands.is_branch_token("2호점")


def test_지점명이_여러_개면_반복해서_뗀다():
    assert brands.brand_name("어떤집 강남 역삼점") == "어떤집 강남"
    assert brands.brand_name("어떤집 강남점 역삼점") == "어떤집"


def test_키는_공백과_대소문자를_지운다():
    assert brands.brand_key("본죽&비빔밥cafe 강남점") == brands.brand_key("본죽&비빔밥CAFE")
    assert brands.brand_key("AC 키친") == brands.brand_key("ac키친")


def test_같은_브랜드는_같은_번호_다른_브랜드는_다른_번호():
    ids = brands.brand_ids(["스타벅스 역삼점", "스타벅스 선릉역점", "홍반점", "홍반점"])
    assert ids[0] == ids[1]
    assert ids[2] == ids[3]
    assert ids[0] != ids[2]


def test_이름이_없으면_자기만의_브랜드가_된다():
    """이름 없는 가게끼리 한 브랜드로 묶여 서로를 억제하면 안 된다."""
    ids = brands.brand_ids(["", "", "스타벅스 역삼점"])
    assert ids[0] != ids[1]


def test_가장_큰_브랜드_목록():
    names = {"a": "스타벅스 역삼점", "b": "스타벅스 선릉역점", "c": "스타벅스 강남점",
             "d": "공차 역삼점", "e": "공차 강남점", "f": "혼자집"}
    top = brands.largest_brands(list(names), names, 2)
    assert [(b, c) for b, c, _ in top] == [("스타벅스", 3), ("공차", 2)]


# ────────────────────────────────────────────── 중복 억제
def test_같은_브랜드_칸만_중립값으로_바뀐다():
    # 가게 0,1 이 같은 브랜드. 이력은 가게 0.
    rows = np.array([[1.0, 1.0, 0.2, 0.0]])           # 0 과 1 이 근접 중복
    brand = np.array([0, 0, 1, 2])
    out = suppress_same_brand(rows, [0], brand)
    neutral = rows.mean()                              # 0.55
    assert out[0, 0] == pytest.approx(neutral)         # 자기 자신도 중립
    assert out[0, 1] == pytest.approx(neutral)         # 같은 브랜드 지점
    assert out[0, 2] == pytest.approx(0.2)             # 나머지는 그대로
    assert out[0, 3] == pytest.approx(0.0)


def test_다른_이력_가게와의_유사도는_남는다():
    """브랜드가 아닌 이유로 닮았다면 후보는 계속 위로 올라와야 한다."""
    rows = np.array([[1.0, 0.1], [0.3, 0.9]])          # 이력 2곳 x 후보 2곳
    brand = np.array([0, 0])                            # 후보 0,1 은 같은 브랜드
    out = suppress_same_brand(rows, [0, 1], brand)
    # 이력 0 과 이력 1 이 같은 브랜드(0)라 두 행 모두 중립으로 간다
    assert np.allclose(out, rows.mean())
    # 이력이 그 브랜드가 아니면 한 줄도 안 바뀐다
    other = suppress_same_brand(rows, [0, 1], np.array([0, 1]))
    assert other[0, 0] != rows[0, 0]                    # 이력0 == 후보0 브랜드
    assert other[0, 1] == pytest.approx(0.1)            # 다른 브랜드는 그대로


def test_NaN_후보는_억제하지_않는다():
    """메뉴가 없어 NaN 인 칸은 '모른다' 다 — 중립값으로 덮으면 정보를 만들어낸다."""
    rows = np.array([[1.0, np.nan]])
    out = suppress_same_brand(rows, [0], np.array([0, 0]))
    assert np.isnan(out[0, 1])


def test_content_score에_브랜드를_넘기면_체인이_상위에서_내려간다():
    """0(이력) 과 1 은 같은 브랜드로 유사도 1.0, 2 는 다른 브랜드인데 0.9 로 닮았다.

    억제 뒤 체인 지점은 **중립값(그 사용자의 평균 유사도)** 으로 내려가고 진짜로 닮은
    2 가 그 위로 올라온다. 바닥(0)이 아니라 가운데로 내려가는 것이 핵심이다 —
    0 으로 만들면 '같은 브랜드라서 싫다' 가 된다.
    """
    row = np.array([1.0, 1.0, 0.9, 0.2, 0.2, 0.2])
    sim = np.tile(row, (6, 1))
    pos = {"A": 0, "A2": 1, "B": 2}
    hist = [("A", True)]
    plain = content_score(sim, pos, hist, "max", 0.0)
    suppressed = content_score(sim, pos, hist, "max", 0.0,
                               brand=np.array([0, 0, 1, 2, 3, 4]))
    assert plain[1] > plain[2]                          # 억제 전: 체인 지점이 1등
    assert suppressed[2] > suppressed[1]                # 억제 후: 뒤집힌다
    assert suppressed[1] == pytest.approx(row.mean())   # 중립값 = 평균
    assert suppressed[1] > suppressed[3]                # 바닥이 아니라 가운데다
    np.testing.assert_allclose(suppressed[3:], plain[3:])   # 나머지는 그대로


def test_브랜드_이름이_없으면_이유를_말하며_거절한다():
    from analysis.backtest import scorers
    from analysis.backtest.pool import build_pool
    from analysis.backtest.tests.conftest import make_dataset
    ds = make_dataset([("s0", 10, 4.0, "한식", [10000], 1)], [])
    ctx = scorers.ScoreContext(pool=build_pool(ds))
    with pytest.raises(KeyError, match="store_names"):
        scorers.get("menu_top8_max_bs").score([("s0", True)], ctx, user_id="u")


def test_억제_변종이_전부_등록돼_있다():
    from analysis.backtest import scorers
    for name in ("menu_top8_max", "menu_top8_idf_mean", "menu_top20_idf_max"):
        assert scorers.exists(name + "_bs")
    # 하이브리드 이름 규칙도 그대로 통한다
    assert scorers.exists("hybrid_pop_menu_top8_max_bs_l0.5")
