# -*- coding: utf-8 -*-
"""트렌드 층(`recsys/trend.py`) — 계약과 그 층이 **넘지 말아야 할 선**.

이 파일에서 제일 중요한 것은 두 가지를 잠그는 테스트다.

  ① **기본이 꺼짐이다** (`test_기본값은_꺼져_있다`, `test_꺼져_있으면_보정이_예전과_같다`)
     지금 legacy·taste 팀-드래프트 인터리빙이 돌고 있다. 트렌드가 순서를 건드리면
     기간 전후의 like 율을 합칠 수 없어 그 실험이 죽는다. 켜는 것은 실험이 끝난
     뒤의 결정이고, 그때까지 이 모듈은 **비트 단위로 아무것도 바꾸지 않아야** 한다.

  ② **학습에 들어가지 않는다** (`test_트렌드는_취향을_바꾸지_않는다`)
     `m`·`v` 는 `state/preferences.jsonl` 하나로 소수점까지 복원돼야 한다
     (`tests/test_replay.py`). 트렌드는 시간에 따라 변하는 값이라 학습에 섞이면
     **같은 로그를 다시 먹여도 다른 취향이 나온다**. `app/ranking.py` 의 거리
     보너스가 같은 이유로 학습 밖에 있는 것과 똑같은 계약이다.

나머지는 신호 자체의 계약이다: 증가율이 수준이 아니라는 것, 별점이 수축된다는 것,
표본이 모자라면 '모른다'(0 = 후보 평균)라는 것, 자료가 낡으면 조용히 0 을 주는 게
아니라 **꺼진다**는 것.
"""
from __future__ import annotations

import copy
import json
import datetime as dt

import numpy as np
import pytest

from recsys import trend


def _day(before: int) -> str:
    return (dt.date.today() - dt.timedelta(days=before)).isoformat()


def _rv(days_ago: int, rating: int | None = 5) -> dict:
    return {"date": _day(days_ago) + " 12:00:00", "rating": rating}


#: 가게별 합성 리뷰. 윈도우는 180일이므로 0~179 가 '최근', 180~359 가 '직전' 이다.
#:   up    최근에 몰렸다      -> 증가율 큼
#:   flat  고르게             -> 증가율 0 근처
#:   down  예전에 몰렸다      -> 증가율 작음
#:   huge  전부 예전, 양만 많다 -> **수준은 높지만 증가율은 낮다** (수준 != 트렌드)
REVIEWS = {
    "up":   [_rv(10), _rv(20), _rv(30), _rv(40), _rv(50), _rv(60), _rv(300)],
    "flat": [_rv(20), _rv(60), _rv(100), _rv(200), _rv(250), _rv(300)],
    "down": [_rv(170), _rv(200), _rv(220), _rv(250), _rv(280), _rv(300), _rv(340)],
    "huge": [_rv(200 + i) for i in range(60)],
}
STORES = {sid: {"cat2": "한식", "cat3": "국밥"} for sid in REVIEWS}


@pytest.fixture
def data(monkeypatch):
    """합성 리뷰로 갈아끼운다. 진짜 33,300건을 읽지 않는다."""
    monkeypatch.setattr(trend.svc, "all_store_ids", lambda: list(REVIEWS))
    monkeypatch.setattr(trend.svc, "get_reviews",
                        lambda sid, limit=5: REVIEWS.get(sid, [])[:limit])
    monkeypatch.setattr(trend.svc, "get_store", lambda sid: STORES.get(sid))
    monkeypatch.setattr(trend, "_reviews_mtime", lambda: 1.0)
    monkeypatch.setattr(trend, "_artifact_mtimes", lambda: (0.0, 0.0))
    monkeypatch.setattr(trend, "_category_table", lambda: {})
    monkeypatch.setattr(trend, "_menu_table", lambda: ({}, {}))
    trend.reset()
    yield
    trend.reset()


IDS = ["up", "flat", "down", "huge"]

#: 검색 산출물 합성. `up`·`flat` 은 국밥집(업종 season +0.2), `down` 은 냉면집(-0.5).
#:   up    메뉴 키워드 넷 — 오르는 것 셋 + 밀도 낮은 스파이크 하나(걸러져야 한다)
#:   flat  메뉴 키워드 없음 → 업종 값을 그대로 받아야 한다
#:   down  냉면 하나 (내림)
MENU_KWS = {
    "유자차":   {"season": 1.0, "yoy": 0.1, "density": 20.0},
    "모듬전":   {"season": 0.6, "yoy": 0.0, "density": 30.0},
    "국밥":     {"season": 0.2, "yoy": -0.1, "density": 40.0},
    "동지팥죽": {"season": 2.9, "yoy": 1.5, "density": 0.1},      # 밀도 미달 — 스파이크
    "냉면":     {"season": -0.7, "yoy": -0.2, "density": 50.0},
}
MENU_OF = {"up": ["유자차", "모듬전", "국밥", "동지팥죽"], "down": ["냉면"]}
CAT_TABLE = {"season": {"국밥": 0.2, "냉면": -0.5}, "fad": {"국밥": -0.1, "냉면": -0.3}}
STORES_MENU = {"up": {"cat3": "국밥"}, "flat": {"cat3": "국밥"}, "down": {"cat3": "냉면"},
               "huge": {"cat3": "없는업종"}}


@pytest.fixture
def menu(data, monkeypatch):
    """검색 산출물(업종 + 메뉴 키워드)을 합성으로 끼운다."""
    monkeypatch.setattr(trend.svc, "get_store", lambda sid: STORES_MENU.get(sid))
    monkeypatch.setattr(trend, "_category_table", lambda: CAT_TABLE)
    monkeypatch.setattr(trend, "_menu_table", lambda: (MENU_KWS, MENU_OF))
    trend.reset()
    yield
    trend.reset()


# ────────────────────────────────────────────── ① 기본은 꺼짐 (인터리빙 보호)
def test_기본값은_꺼져_있다(monkeypatch):
    monkeypatch.delenv(trend.WEIGHT_ENV, raising=False)
    assert trend.weight() == 0.0
    assert trend.enabled() is False


def test_꺼져_있으면_보너스가_비어_있다(data, monkeypatch):
    monkeypatch.delenv(trend.WEIGHT_ENV, raising=False)
    assert trend.bonus(IDS) == {}


def test_꺼져_있으면_정렬_보정이_예전과_같다(data, monkeypatch):
    """`ranking.build` 가 트렌드를 더해도 꺼져 있으면 결과가 **완전히 같아야** 한다."""
    from app import ranking

    monkeypatch.delenv(trend.WEIGHT_ENV, raising=False)
    monkeypatch.setattr(ranking.filters.svc, "all_store_ids", lambda: list(IDS))
    monkeypatch.setattr(ranking.geo, "coords", lambda: {})
    allow = set(IDS)

    tier, bonus = ranking.build(None, None, allow)
    assert bonus == {}                      # 거리도 트렌드도 없으면 보정 자체가 없다
    assert tier == {}


def test_켜면_보너스가_무게만큼_커진다(data, monkeypatch):
    monkeypatch.setenv(trend.WEIGHT_ENV, "0.5")
    z = trend.scores(IDS)
    b = trend.bonus(IDS)
    assert b and set(b) == set(z)
    for sid in IDS:
        assert b[sid] == pytest.approx(0.5 * z[sid])


# ────────────────────────────────────────────── ② 학습에 들어가지 않는다
def test_트렌드는_취향을_바꾸지_않는다(art, sess, monkeypatch):
    """보너스를 켜고 꺼도 `record` 가 만드는 `m`·`v` 는 **한 톨도** 달라지지 않는다.

    이게 깨지면 `state/preferences.jsonl` 재생으로 취향이 복원된다는 계약이 깨진다
    (`tests/test_replay.py`). 트렌드는 시계의 함수이므로 로그의 함수인 `m` 에
    섞이면 안 된다.
    """
    from recsys import taste

    monkeypatch.setenv(trend.WEIGHT_ENV, "0.0")
    taste.record(sess, "s0", True)
    taste.record(sess, "s2", False)
    m_off, v_off = sess["m"].copy(), sess["v"].copy()

    taste._sessions.clear()
    s2 = taste.session_for("u_test")
    monkeypatch.setenv(trend.WEIGHT_ENV, "5.0")      # 크게 켠다
    taste.record(s2, "s0", True)
    taste.record(s2, "s2", False)

    assert np.array_equal(s2["m"], m_off)
    assert np.array_equal(s2["v"], v_off)


# ────────────────────────────────────────────── 신호의 계약
def test_증가율이_큰_가게가_높다(data):
    z = trend.scores(IDS)
    assert z["up"] > z["flat"] > z["down"]


def test_수준이_아니라_증가율이다(data):
    """`huge` 는 리뷰가 60건으로 제일 많지만 전부 예전이라 **트렌드는 낮아야** 한다.

    최근 유입 '수준' 은 `pop`(리뷰 총수)과 상관 +0.576 인 인기의 다른 이름이다.
    이 모듈이 나르는 새 정보는 증가율(+0.079)뿐이라 그쪽만 본다.
    """
    z = trend.scores(IDS)
    assert z["huge"] < z["up"]
    assert z["huge"] < 0                    # 후보 평균보다 아래


def test_z는_후보_집합_안에서_난다(data):
    """후보가 달라지면 같은 가게의 점수도 달라진다 — `ranking.distance_bonus` 와 같다."""
    wide = trend.scores(IDS)
    narrow = trend.scores(["up", "flat"])
    assert wide["up"] != narrow["up"]


def test_모르는_가게는_0이다(data):
    """풀에 있지만 리뷰가 없는 가게는 후보 평균(0.0) — 벌하지도 봐주지도 않는다."""
    z = trend.scores(IDS + ["없는가게"])
    assert z["없는가게"] == 0.0


def test_빈_후보는_빈_결과(data):
    assert trend.scores([]) == {}


# ────────────────────────────────────────────── 별점 항
def test_별점은_표본이_모자라면_모른다(data):
    """두 윈도우 각각 `MIN_RATING_N` 건 미만이면 None(= '모른다')."""
    rows = [(_day(10), 5), (_day(200), 1)]          # 각 1건
    assert trend._rating(rows, _day(180), _day(360)) is None


def test_별점_변화는_수축된다(data):
    """관측된 변화의 26.6%만 신호다(플라시보 테스트). 그대로 쓰면 잡음을 학습한다."""
    rows = ([(_day(10), 5)] * 3) + ([(_day(200), 1)] * 3)
    got = trend._rating(rows, _day(180), _day(360))
    assert got == pytest.approx(trend.SHRINK_RATING * 4.0)
    assert abs(got) < 4.0                            # 날것보다 반드시 작다


def test_윈도우는_같은_길이다(data):
    """비대칭 윈도우('직전 전체')는 없는 추세를 만든다 — 실측 −0.317 대 −0.154."""
    rows = [(_day(10), 5), (_day(500), 1)]
    assert trend._rating(rows, _day(180), _day(360)) is None   # 500일 전은 안 센다


# ────────────────────────────────────────────── 외부 업종 트렌드
def test_업종_트렌드가_없으면_그_항은_0이다(data):
    assert trend._category("up", {}) is None


def test_업종_트렌드는_cat3이_cat2보다_우선(data):
    assert trend._category("up", {"국밥": 2.0, "한식": 1.0}) == 2.0
    assert trend._category("up", {"한식": 1.0}) == 1.0


# ────────────────────────────────────────────── 낡은 자료
def test_자료가_낡으면_꺼진다(monkeypatch):
    """조용히 0 을 주지 않고 **끈다**. '최근 180일' 이 반년 전 이야기면 의미가 없다."""
    old = {"s": [{"date": _day(400) + " 12:00:00", "rating": 5}]}
    monkeypatch.setattr(trend.svc, "all_store_ids", lambda: list(old))
    monkeypatch.setattr(trend.svc, "get_reviews", lambda sid, limit=5: old.get(sid, []))
    monkeypatch.setattr(trend.svc, "get_store", lambda sid: {"cat2": "한식"})
    monkeypatch.setattr(trend, "_reviews_mtime", lambda: 2.0)
    monkeypatch.setenv(trend.WEIGHT_ENV, "1.0")
    trend.reset()

    assert trend.status()["stale"] is True
    assert trend.scores(["s"]) == {}
    assert trend.bonus(["s"]) == {}
    trend.reset()


def test_상태에_필요한_것이_다_있다(data):
    st = trend.status()
    for key in ("enabled", "weight", "source_weights", "as_of", "stale_days",
                "stale", "stores", "window_days", "shrink_rating"):
        assert key in st
    assert st["stores"] == len(REVIEWS)


def test_설명은_출처별로_나뉜다(data):
    ex = trend.explain("up")
    assert ex["store_id"] == "up"
    assert set(ex["raw"]) == set(trend.SOURCES)


# ────────────────────────────────────────────── 포화 (상위권 동점 방지)
def test_상위권이_동점이_되지_않는다(data, monkeypatch):
    """하드 클립을 쓰면 상위 20곳이 전부 +3.00 으로 같아진다(실측). `tanh` 는 단조다.

    트렌드의 일은 상위를 띄우는 것인데, 그 구간이 동점이면 아무 일도 안 하는 것과 같다.
    """
    z = np.linspace(-6, 6, 40)
    out = trend._saturate(z)
    assert np.all(np.diff(out) > 0)                  # 어디서도 평평해지지 않는다
    assert out.max() < trend.Z_CLIP                  # 경계는 지킨다
    assert out.min() > -trend.Z_CLIP


def test_포화는_평범한_구간에서_거의_선형이다(data):
    """|z| < 1.5 인 대부분의 가게는 눈금이 바뀌지 않아야 한다."""
    z = np.array([-1.5, -0.5, 0.0, 0.5, 1.5])
    assert np.allclose(trend._saturate(z), z, atol=0.25)


# ────────────────────────────────────────────── ④ 트렌드 슬롯 (MATCHURI_TREND_EVERY)
def test_슬롯_기본은_꺼짐(monkeypatch):
    monkeypatch.delenv(trend.SLOT_ENV, raising=False)
    assert trend.slot_every() == 0 and trend.slot_enabled() is False
    monkeypatch.setenv(trend.SLOT_ENV, "이상한값")
    assert trend.slot_every() == 0
    monkeypatch.setenv(trend.SLOT_ENV, "-3")
    assert trend.slot_every() == 0
    monkeypatch.setenv(trend.SLOT_ENV, "10")
    assert trend.slot_every() == 10 and trend.slot_enabled() is True


def test_슬롯은_오르는_가게만_고른다(data):
    """z > 0 만 후보다. `down`·`huge` 는 씨앗을 아무리 바꿔도 안 나온다."""
    picks = {trend.slot_pick(IDS, 1, seed=s)[0] for s in range(20)}
    assert picks <= {"up", "flat"} and "up" in picks
    many = trend.slot_pick(IDS, 4, seed=0)
    assert set(many) <= {"up", "flat"} and len(many) == len(set(many))
    assert trend.slot_pick(IDS, 0, seed=0) == [] and trend.slot_pick([], 1, seed=0) == []


def test_슬롯은_씨앗으로_재현된다(data):
    assert trend.slot_pick(IDS, 1, seed=7) == trend.slot_pick(IDS, 1, seed=7)


def test_슬롯은_태그_계층을_z_보다_먼저_본다(data, monkeypatch):
    monkeypatch.setattr(trend, "SLOT_TOP_N", 1)
    assert trend.slot_pick(IDS, 1, seed=0) == ["up"]                   # z 1위
    assert trend.slot_pick(IDS, 1, seed=0, tiers={"flat": 2}) == ["flat"]  # 계층이 이긴다
    assert trend.slot_pick(IDS, 1, seed=0, tiers={"huge": 9}) == ["up"]    # 내리는 곳은 후보가 아니다


def test_status_에_슬롯이_보인다(data, monkeypatch):
    monkeypatch.setenv(trend.SLOT_ENV, "10")
    st = trend.status()
    assert st["slot_enabled"] is True and st["slot_every"] == 10
    assert st["slot_top_n"] == trend.SLOT_TOP_N


# ────────────────────────────────────────────── ⑤ 검색 트렌드 — 메뉴 키워드 + 업종 수축
def test_출처는_검색_둘과_리뷰_둘이다():
    assert trend.SOURCES == ("season", "fad", "inflow", "rating")
    assert set(trend.SOURCE_ENV) == set(trend.DEFAULT_SOURCE_W) == set(trend.SOURCES)
    assert trend.DEFAULT_SOURCE_W["season"] >= trend.DEFAULT_SOURCE_W["inflow"]   # 검색이 주 신호


def test_메뉴_점수는_밀도를_거르고_상위_K_평균이다(menu):
    """`up` 은 키워드 넷인데 동지팥죽(밀도 0.1)은 스파이크라 빠지고 셋의 평균이다."""
    m, n, top = trend._menu("up", "season", MENU_KWS, MENU_OF)
    assert n == 3 and [k for k, _ in top] == ["유자차", "모듬전", "국밥"]
    assert m == pytest.approx((1.0 + 0.6 + 0.2) / 3)
    assert trend._menu("flat", "season", MENU_KWS, MENU_OF) == (None, 0, [])
    # fad 는 산출물의 yoy 열이다
    m2, n2, _ = trend._menu("down", "fad", MENU_KWS, MENU_OF)
    assert (m2, n2) == (-0.2, 1)


def test_부분_수축은_증거가_적으면_업종이_말한다():
    assert trend._pooled(None, 0, None) is None
    assert trend._pooled(None, 0, 0.2) == 0.2                       # 메뉴 없음 → 업종
    assert trend._pooled(0.9, 1, None) == 0.9                       # 업종 없음 → 메뉴
    one = trend._pooled(0.9, 1, 0.1)                                # 키워드 1개: 반반
    three = trend._pooled(0.9, 3, 0.1)                              # 키워드 3개: 3:1
    assert one == pytest.approx(0.5) and three == pytest.approx(0.7)
    assert abs(three - 0.9) < abs(one - 0.9)                        # 증거가 많을수록 자기 값


def test_가게의_계절_항은_메뉴와_업종을_합친_값이다(menu):
    ex = {sid: trend.explain(sid)["raw"] for sid in IDS}
    assert ex["up"]["season"] == pytest.approx((3 * 0.6 + 1.0 * 0.2) / 4)   # 메뉴 0.6, 업종 0.2
    assert ex["flat"]["season"] == pytest.approx(0.2)                        # 업종 값 그대로
    assert ex["down"]["season"] == pytest.approx((1 * -0.7 + 1.0 * -0.5) / 2)
    assert ex["huge"]["season"] is None                                      # 메뉴도 업종도 없다
    assert ex["up"]["fad"] == pytest.approx((3 * 0.0 + 1.0 * -0.1) / 4)      # 0.1·0.0·-0.1 평균 0.0


def test_설명에_쓴_메뉴_키워드가_붙는다(menu):
    ex = trend.explain("up")
    assert [m["keyword"] for m in ex["menu"]] == ["유자차", "모듬전", "국밥"]
    assert trend.explain("flat")["menu"] == []
    assert set(ex["raw"]) == set(trend.SOURCES)                     # raw 의 키는 출처 넷뿐


def test_검색_항은_리뷰가_없는_가게에도_있다(menu, monkeypatch):
    """계절은 검색 자료라 리뷰 0건 가게도 값을 받아야 한다 — 우주는 가게 전체다."""
    monkeypatch.setattr(trend.svc, "all_store_ids", lambda: list(REVIEWS) + ["noreview"])
    monkeypatch.setattr(trend.svc, "get_store",
                        lambda sid: {"cat3": "국밥"} if sid == "noreview" else STORES_MENU.get(sid))
    trend.reset()
    ex = trend.explain("noreview")
    assert ex["raw"]["season"] == pytest.approx(0.2) and ex["raw"]["inflow"] is None
    z = trend.scores(IDS + ["noreview"])
    assert "noreview" in z


def test_계절이_오르는_가게가_슬롯에_먼저_선다(menu, monkeypatch):
    """리뷰 증가율을 꺼도 검색 계절만으로 순서가 난다 — `up` > `flat` > `down`."""
    for name in ("inflow", "rating", "fad"):
        monkeypatch.setenv(trend.SOURCE_ENV[name], "0")
    z = trend.scores(IDS)
    assert z["up"] > z["flat"] > z["down"]
    monkeypatch.setattr(trend, "SLOT_TOP_N", 1)
    assert trend.slot_pick(IDS, 1, seed=0) == ["up"]


def test_업종_표는_열별로_읽고_옛_산출물은_유행만_준다(tmp_path, monkeypatch):
    new = tmp_path / "new.json"
    new.write_text(json.dumps({"scores": {"국밥": 0.1}, "yoy": {"국밥": 0.1},
                               "season": {"국밥": 0.4}}), encoding="utf-8")
    monkeypatch.setattr(trend, "CATEGORY_FILE", new)
    assert trend._category_table() == {"season": {"국밥": 0.4}, "fad": {"국밥": 0.1}}
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"scores": {"국밥": 0.1}, "basis": "yoy"}), encoding="utf-8")
    monkeypatch.setattr(trend, "CATEGORY_FILE", old)
    assert trend._category_table() == {"fad": {"국밥": 0.1}}          # 계절은 그 파일에 없다
    monkeypatch.setattr(trend, "CATEGORY_FILE", tmp_path / "none.json")
    assert trend._category_table() == {}
    monkeypatch.setattr(trend, "MENU_FILE", tmp_path / "none.json")
    assert trend._menu_table() == ({}, {})                              # 없어도 죽지 않는다


def test_산출물이_바뀌면_캐시가_다시_만들어진다(menu, monkeypatch):
    first = trend._state()
    monkeypatch.setattr(trend, "_artifact_mtimes", lambda: (1.0, 2.0))
    assert trend._state() is not first


def test_상태에_메뉴_항이_보인다(menu):
    st = trend.status()
    assert st["menu_entries"] == len(MENU_KWS) and st["menu_stores"] == len(MENU_OF)
    assert st["category_entries"] == 2
    assert st["min_density"] == trend.MIN_DENSITY and st["menu_top_k"] == trend.MENU_TOP_K


def test_슬롯_후보는_브랜드마다_한_곳이다(data, monkeypatch):
    """같은 체인은 메뉴 키워드가 같아 점수도 같다(실측: 본죽 4곳이 상위 10 중 4곳). 한 곳만 남긴다."""
    monkeypatch.setattr(trend, "scores", lambda ids: {"a": 3.0, "b": 2.0, "c": 1.0, "d": -1.0})
    monkeypatch.setattr(trend, "_brand_map", lambda: {"a": "본죽", "b": "본죽"})
    monkeypatch.setattr(trend, "SLOT_TOP_N", 2)
    picks = {trend.slot_pick(["a", "b", "c", "d"], 1, seed=s)[0] for s in range(30)}
    assert picks == {"a", "c"}                      # b 는 a 와 같은 브랜드라 접혔다, d 는 내림세
    monkeypatch.setattr(trend, "_brand_map", lambda: {})
    picks = {trend.slot_pick(["a", "b", "c", "d"], 1, seed=s)[0] for s in range(30)}
    assert picks == {"a", "b"}                      # 브랜드 정보가 없으면 접지 않는다
