# -*- coding: utf-8 -*-
"""리뷰 표현력 채점 — 가게 화면의 카카오 리뷰 순서와 사용자 후기의 자격을 정하는 잣대.

잠그는 것:
1. **레벨 정의** — L1 평가만 · L2 속성 · L3 속성 + 신호 2종. 기준 문장(`analysis/README.md`)이
   그 등급을 받는다
2. **analysis 판에서 고친 것들** — 어근 중복이 multiattr 을 켜지 않고, "먹으면"·"리뷰가"·
   "직원이"·"24시간이" 같은 함정어에 걸리지 않고, 외국어는 `lang` 으로 구분된다
3. **정렬** — `recsys/store.py` 가 표현력을 첫 키로 쓴다. 같은 등급 안에서만 옛 규칙이 든다
"""
from __future__ import annotations

import pytest

from recsys import expressiveness as ex
from recsys import store as svc


def lv(text, axis):
    return ex.score(text)["levels"][axis]


# ────────────────────────────────────────────── 레벨 정의
@pytest.mark.parametrize("text, axis, level", [
    ("맛있어요", "taste", 1),                              # 평가만
    ("친절해요", "service", 1),
    ("가성비 좋아요", "price", 1),                          # '가성비 좋' 은 평가일 뿐
    ("편안한 분위기", "ambience", 1),
    ("고등어초회가 신선했어요", "taste", 2),                  # 속성 하나
    ("조용하고 좌석 간격이 넓어요", "ambience", 2),
    ("2인 4만원, 적당했어요", "price", 2),                   # 금액 + 정도 → 신호 하나
    ("국물이 걸쭉해서 계란이 퍽퍽함", "taste", 3),           # 속성 둘 + 지시대상 + 근거
    ("생갈비 18,000원이에요", "price", 3),                   # 금액 + 수량 + 지시대상
    ("", "taste", 0),
])
def test_기준_문장의_레벨(text, axis, level):
    assert lv(text, axis) == level


def test_등급은_축_레벨의_최댓값이고_폭과_합이_따라온다():
    s = ex.score("국물이 걸쭉해서 계란이 퍽퍽함. 사장님 친절하세요. 4만원.")
    assert s["grade"] == 3 and s["top_category"] == "taste"
    assert s["levels"]["service"] == 1 and s["levels"]["price"] == 2
    assert s["breadth"] == 3 and s["depth_sum"] == 6


def test_감정이_아니라_구체성을_잰다():
    """"맛없다" 도 "맛있다" 와 같은 레벨이다."""
    assert lv("맛없어요", "taste") == lv("맛있어요", "taste") == 1
    assert lv("면이 퍽퍽하고 국물이 짜요", "taste") == lv("면이 쫄깃하고 국물이 담백해요", "taste")


# ────────────────────────────────────────────── analysis 판에서 고친 것
def test_같은_어근은_속성_하나다():
    """'느끼'·'느끼하'·'느끼함' 이 다 걸려도 multiattr 이 켜지지 않는다 — 옛 판은 L3 을 줬다."""
    s = ex.score("느끼하고 느끼함이 심해요")
    assert s["attrs"]["taste"] == ["느끼"]
    assert s["levels"]["taste"] == 2


def test_한_글자_지시대상이_헛돌지_않는다():
    """"먹으면" 의 '면', "파우더" 의 '파', "김 사장" 의 '김' 은 재료가 아니다."""
    for text in ("먹으면 좋아요", "시나몬 파우더 뿌려요", "김 사장님이 계세요"):
        assert ex._hits(ex.normalize(text), ex._SIG_F["referent"]) == [], text
    # 조사가 붙은 꼴은 잡는다 — 속성(쫄깃) + 지시대상(면이) + 정도(살짝) 로 L3
    assert lv("면이 살짝 쫄깃해요", "taste") == 3


@pytest.mark.parametrize("text, axis", [
    ("음식이 친절하고 직원이 맛있어요", "price"),    # '원이' ← 직원이
    ("정원에 앉았어요", "price"),                    # '원에' ← 정원에
    ("리뷰가 좋아서 왔어요", "ambience"),            # '뷰가' ← 리뷰가
    ("24시간이라고 해두지 마세요", "taste"),          # '간이' ← 시간이
    ("부모님 인사치레할 때 좋아요", "service"),      # '인사' ← 인사치레
    ("방문한적 있어요", "ambience"),                 # '한적' 은 사전에서 뺐다
    ("고양이도 놀러와요", "taste"),                  # '양이' ← 고양이 (옛 함정어)
])
def test_함정어에_걸리지_않는다(text, axis):
    assert lv(text, axis) == 0


def test_비교_정도_신호의_함정어():
    """"먹어보다" 의 '보다' 는 비교가 아니고 "더 이상" 의 '더 ' 는 정도가 아니다."""
    plain = ex.score_sentence(ex.normalize("국물이 진해요"))["taste"]["signals"]
    assert ex.score_sentence(ex.normalize("국물이 진해요 먹어보다 말았어요"))["taste"]["signals"] == plain
    assert ex.score_sentence(ex.normalize("국물이 진해요 더 이상은 못 먹어요"))["taste"]["signals"] == plain
    assert "compare" in ex.score_sentence(ex.normalize("국물이 다른 곳보다 진해요"))["taste"]["signals"]


def test_반복_글자는_판정을_흔들지_않는다():
    assert ex.score("짱짱짱짱짱 맛있어요")["grade"] == ex.score("짱짱 맛있어요")["grade"] == 1
    assert ex.normalize("와....와...!!!!") == "와..와..!!"


def test_외국어와_기호만인_글은_못_잰_것이다():
    """0 점이 아니라 '사전이 재지 못함' 이다 — `lang` 으로 구분된다."""
    assert ex.score("It was delicious!")["lang"] == ex.FOREIGN
    assert ex.score("😀😀👍")["lang"] == ex.NON_TEXT
    assert ex.score("ㅇㅇ")["lang"] == ex.NON_TEXT
    assert ex.score("맛있어요")["lang"] == ex.KO
    assert ex.score("It was delicious!")["grade"] == 0


def test_금액은_속성_하나다():
    """"4만원" 이 '만원' 어휘와 금액 정규식에 둘 다 걸려 속성 둘이 되면 multiattr 이 헛돈다."""
    assert ex.score("2인 4만원")["attrs"]["price"] == ["금액"]
    assert lv("2인 4만원", "price") == 2


def test_보강한_어휘가_속성으로_잡힌다():
    assert lv("도우가 너무 딱딱해요", "taste") == 2
    assert lv("공기밥에서 머리카락 나왔어요", "ambience") >= 2
    assert lv("불러도 안 와요", "service") == 2
    assert lv("언덕에 있어서 찾아가기 어려워요", "wait") >= 2


def test_카페_어휘가_맛_축에서_잡힌다():
    """사전이 밥집 말로만 차 있어서 카페 리뷰의 taste L2↑ 가 20.7%(그외 34.6%)였다.
    "아메리카노가 맛있어요" 는 여전히 L1 이다 — 순서가 뒤일 뿐 실리지 않는 건 아니다."""
    assert lv("커피가 연해요", "taste") == 2
    assert lv("빵이 촉촉해요", "taste") == 2
    assert lv("에어컨이 빵빵해요", "taste") == 0          # 함정어 — 음식 이야기가 아니다
    assert lv("아메리카노가 맛있어요", "taste") == 1      # 화제어 + 평가


def test_level_of_는_모르는_축을_거부한다():
    assert ex.level_of("맛있어요", "taste") == 1
    with pytest.raises(KeyError):
        ex.level_of("맛있어요", "mood")


def test_rank_key_는_클수록_표현력이_높다():
    a, b, c = (ex.rank_key(t) for t in ("국물이 걸쭉해서 계란이 퍽퍽함", "고등어초회가 신선했어요", "맛있어요"))
    assert a > b > c > ex.rank_key("")


# ────────────────────────────────────────────── 정렬
def test_미리보기는_표현력_순이고_같은_등급_안에서만_옛_규칙이_든다(monkeypatch):
    """옛 순서(평균에 가까운 별점 → 사진 → 길이)는 "맛있어요" 를 대표로 세울 수 있었다."""
    rows = [
        {"review_id": 1, "content": "맛있어요", "rating": 4, "photo_count": 3},
        {"review_id": 2, "content": "국물이 걸쭉해서 계란이 퍽퍽함", "rating": 2, "photo_count": 0},
        {"review_id": 3, "content": "고등어초회가 신선했어요", "rating": 4, "photo_count": 0},
        {"review_id": 4, "content": "면발이 정말 쫄깃했어요", "rating": 4, "photo_count": 2},   # 3 과 같은 등급, 사진 많음
        {"review_id": 5, "content": "", "rating": 4, "photo_count": 5},
        {"review_id": 6, "content": "So good!!", "rating": 4, "photo_count": 1},         # 외국어 — 못 잰 것
    ]
    monkeypatch.setattr(svc, "_reviews", {"s": rows})
    monkeypatch.setattr(svc, "_stores", {"s": {"review_avg": 4.0}})
    svc._sort_reviews()
    order = [r["review_id"] for r in rows]
    assert order[0] == 2, "가장 구체적인 리뷰가 먼저다 — 별점이 평균에서 멀어도"
    assert order[1:3] == [4, 3], "같은 등급 안에서는 옛 규칙(사진 많은 순)"
    assert order[3] == 1
    assert set(order[4:]) == {5, 6}, "빈 글과 외국어는 뒤로"
    assert all("_expr" in r for r in rows), "등급을 줄에 남겨 관리자가 순서를 따질 수 있다"
