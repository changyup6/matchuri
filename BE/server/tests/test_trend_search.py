# -*- coding: utf-8 -*-
"""검색 축(`recsys/trend_search.py`) — 접기·반복성·분류.

**분류 규칙을 지키는 것이 이 파일의 목적이다.** 규칙 하나하나가 실제로 잘못 나온 표를
보고 고친 것이라, 되돌아가면 표가 다시 거짓말을 한다.

    깐부치킨  진폭 9.34배 · 반복 -0.23  -> 계절이 아니다(한 해만 튀었다)
    떡볶이    반복 +0.89               -> 유행이 아니다(달력이다)
    전체      71개 중 64개 음수         -> 중앙값을 빼야 업종 얘기가 된다

합성 자료로만 잰다 — 수집 캐시(`data/analysis/datalab`)가 없어도 돌아야 한다.
"""
from __future__ import annotations

import math

from recsys import trend_search as ts


def _months(vals: list[float], start_year: int = 2024) -> dict[str, float]:
    """`vals` 를 2024-01 부터 달마다 하나씩. 24개를 주면 두 해가 된다."""
    out = {}
    for i, v in enumerate(vals):
        y, m = divmod(i, 12)
        out[f"{start_year + y}-{m + 1:02d}"] = v
    return out


# ────────────────────────────────────────────── 접기
def test_접은_프로파일은_연평균이_1이다():
    """수준은 `level` 이 따로 들고 있으므로 모양만 남긴다 — 1.0 이 연평균이어야
    업종이 달라도 같은 눈금에 겹쳐 그릴 수 있다."""
    got = ts._fold(_months([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]))
    prof = got["season"]
    assert len(prof) == 12
    assert abs(sum(prof) / 12 - 1.0) < 1e-9
    assert got["peak"] == 12 and got["trough"] == 1
    assert abs(got["swing"] - 12.0) < 1e-6      # 12월/1월 = 12배


def test_한_바퀴가_안_차면_진폭을_말하지_않는다():
    """달이 비면 최대·최소가 우연히 정해진다. 그때 배수를 내면 거짓 숫자가 된다."""
    got = ts._fold(_months([1, 2, 3, 4, 5, 6]))     # 6달뿐
    assert got["swing"] is None
    assert got["peak"] is None
    assert got["season"][6] is None                  # 7월 칸은 비어 있다


# ────────────────────────────────────────────── 반복성
def test_두_해가_같은_모양이면_반복성이_1에_가깝다():
    shape = [1, 1, 2, 3, 5, 8, 9, 7, 4, 2, 1, 1]
    r = ts._repeat(_months(shape + shape))
    assert r is not None and r > 0.99


def test_수준이_두_배가_돼도_모양이_같으면_반복성이_유지된다():
    """각 해의 평균으로 나누는 이유다. 안 나누면 전체 성장이 상관을 밀어 올려
    계절이 아닌 것도 높게 나온다."""
    shape = [1, 1, 2, 3, 5, 8, 9, 7, 4, 2, 1, 1]
    r = ts._repeat(_months(shape + [v * 2 for v in shape]))
    assert r is not None and r > 0.99


def test_한_해만_튀면_반복성이_낮다():
    """깐부치킨이 이 모양이었다 — 진폭 9.34배인데 반복 -0.23 이라 계절이 아니다."""
    # 첫 해를 완전 상수로 두면 상관이 **정의되지 않아** None 이 된다(그건 다른
    # 규칙이다). 실제 자료처럼 잔물결을 준다.
    calm = [1.0, 1.1, 0.9, 1.0, 1.1, 0.9, 1.0, 1.1, 0.9, 1.0, 1.1, 0.9]
    spike = [1, 1, 1, 1, 1, 1, 1, 1, 1, 6, 9, 1]
    r = ts._repeat(_months(calm + spike))
    assert r is not None and r < ts.SEASON_REPEAT


def test_두_해가_안_차면_반복성을_주지_않는다():
    assert ts._repeat(_months([1] * 23)) is None


# ────────────────────────────────────────────── 증가율
def _daily(last: str, days: int, f) -> dict[str, float]:
    """`last` 에서 거꾸로 `days` 일. `f(k)` 가 k일 전의 값이다."""
    import datetime as dt
    end = dt.date.fromisoformat(last)
    return {(end - dt.timedelta(days=k)).isoformat(): f(k) for k in range(days)}


def test_증가율은_전년_같은_창과_견준다():
    """최근을 두 배로 올리면 log(2) 가 나와야 한다."""
    daily = _daily("2026-09-16", 730, lambda k: 2.0 if k < 180 else 1.0)
    g = ts._growth(daily, "2026-03-21", "2026-09-16", "2025-03-21", "2025-09-16")
    assert g is not None and abs(g - math.log(2)) < 1e-3   # 소수 4자리로 반올림된다


def test_한쪽_창이_비면_증가율이_없다():
    assert ts._growth({"2026-09-16": 1.0}, "2026-09-16", "2026-09-16",
                      "2025-09-16", "2025-09-16") is None


# ────────────────────────────────────────────── 창 셋
def _fake_base(monkeypatch, days: int = 806, last: str = "2026-09-16"):
    """캐시 파일 없이 `search()` 를 돌리기 위한 가짜 원자료. 업종 둘 — 하나는 여름
    계절형(두 해 반복), 하나는 평평하다."""
    import datetime as dt

    def seasonal(k):
        d = dt.date.fromisoformat(last) - dt.timedelta(days=k)
        return 1.0 + (1.0 if d.month in (6, 7, 8) else 0.0)

    rows = [
        {"name": "냉면", "level": 0.5, "points": days, "level_rank": 2,
         "_daily": _daily(last, days, seasonal)},
        {"name": "카페", "level": 6.6, "points": days, "level_rank": 1,
         "_daily": _daily(last, days, lambda k: 1.0)},
    ]
    all_days = sorted(rows[0]["_daily"])
    base = {"available": True, "rows": rows, "days": all_days, "anchor": "칼국수",
            "anchor_keywords": ["칼국수"], "as_of": last,
            "fetched": [all_days[0], all_days[-1]]}
    monkeypatch.setattr(ts, "_cached", lambda: base)


def test_창을_안_주면_계절은_전체_트렌드는_마지막_180일이다(monkeypatch):
    _fake_base(monkeypatch)
    w = ts.search()["windows"]
    assert w["season"]["from"] == "2024-07-03" and w["season"]["to"] == "2026-09-16"
    assert w["trend"]["days"] == ts.GROWTH_WINDOW
    assert w["trend"]["to"] == "2026-09-16"
    assert w["trend"]["prior_to"] == "2025-09-16"      # 전년 창은 정확히 365일 앞
    assert w["volume"]["from"] == "2024-07-03"


def test_계절_창이_두_해를_못_덮으면_계절형이_안_나온다(monkeypatch):
    """진폭만으로는 계절을 말하지 않는다 — 반복성이 없으면 계절형이 없어야 한다.
    화면은 이 상태를 경고로 보여 준다(`two_years: false`)."""
    _fake_base(monkeypatch)
    wide = ts.search()
    narrow = ts.search(season_from="2025-09-01")
    assert wide["windows"]["season"]["two_years"] is True
    assert any(c["kind"] == "계절형" for c in wide["categories"])
    assert narrow["windows"]["season"]["two_years"] is False
    assert not any(c["kind"] == "계절형" for c in narrow["categories"])
    assert all(c["repeat"] is None for c in narrow["categories"])


def test_트렌드_창이_너무_짧으면_증가율을_내지_않는다(monkeypatch):
    _fake_base(monkeypatch)
    r = ts.search(trend_from="2026-09-10")
    assert r["windows"]["trend"]["too_short"] is True
    assert all(c["growth"] is None for c in r["categories"])
    assert not any(c["fad"] for c in r["categories"])


def test_트렌드_창의_최근과_전년은_같은_칸_수다(monkeypatch):
    """겹쳐 그리기 위한 조건이다. 칸의 기준이 날짜가 아니라 창 시작 기준 일수라서
    두 창이 며칠이든 길이가 같아야 한다."""
    _fake_base(monkeypatch)
    r = ts.search(trend_from="2026-07-01", trend_to="2026-09-16")
    for c in r["categories"]:
        assert len(c["trend_recent"]) == len(c["trend_prior"]) == \
            len(r["windows"]["trend"]["axis"])


def test_창은_자료_밖으로_안_나가고_뒤집히면_바로_세운다(monkeypatch):
    _fake_base(monkeypatch)
    r = ts.search(frm="2030-01-01", to="2000-01-01")
    assert r["windows"]["volume"]["from"] == "2024-07-03"
    assert r["windows"]["volume"]["to"] == "2026-09-16"
    # 트렌드 창은 전년 같은 창이 자료 안에 있어야 하므로 first+365 앞으로는 못 간다.
    r = ts.search(trend_from="2024-01-01", trend_to="2025-01-01")
    assert r["windows"]["trend"]["from"] == "2025-07-03"


def test_출처를_응답에_싣는다(monkeypatch):
    """그래프 밑에 적는 값이다 — 손으로 적었다가 틀린 적이 있어서 응답에서 가져간다."""
    _fake_base(monkeypatch)
    q = ts.search()["sources"]["search"]
    assert q["label"] == "네이버 데이터랩 검색어 트렌드"
    assert q["anchor_keywords"] == ["칼국수"] and q["categories"] == 2
    assert q["fetched"] == ["2024-07-03", "2026-09-16"]


# ────────────────────────────────────────────── 분류
def _row(**kw) -> dict:
    base = {"level": 1.0, "swing": 1.0, "repeat": 0.0, "growth_rel": 0.0}
    base.update(kw)
    return base


def test_문턱_미달은_변동성이_커도_관측_부족이다():
    """절대값이 문턱이고 변동성이 순위다. 분모가 작은 것이 순위를 먹으면 안 된다."""
    r = ts.classify(_row(level=0.001, swing=9.34, growth_rel=0.6), ts.LEVEL_GATE)
    assert r["steady"] is False
    assert r["kind"] == "관측 부족"


def test_문턱은_화면에서_움직인다():
    """코드에 박으면 그 값이 왜 그 값인지 아무도 모른다."""
    row = _row(level=0.5)
    assert ts.classify(row, 0.3)["steady"] is True
    assert ts.classify(row, 1.0)["steady"] is False


def test_반복되는_것은_유행이_아니다():
    """떡볶이가 반복 +0.89 인데 "유행 하락" 으로 들어왔다. 그래서 제외한다."""
    r = ts.classify(_row(repeat=0.89, swing=1.35, growth_rel=-0.30), ts.LEVEL_GATE)
    assert r["repeats"] is True
    assert r["fad"] is False
    assert r["kind"] == "스테디셀러"


def test_진폭이_커도_반복이_없으면_계절이_아니다():
    """깐부치킨: 진폭 9.34배 · 반복 -0.23. 계절이 아니라 사건이다."""
    r = ts.classify(_row(swing=9.34, repeat=-0.23, growth_rel=0.57), ts.LEVEL_GATE)
    assert r["seasonal"] is False
    assert r["kind"] == "유행 상승"


def test_반복되고_진폭도_크면_계절형이다():
    """냉면: 진폭 8.60배 · 반복 +0.90."""
    r = ts.classify(_row(swing=8.60, repeat=0.90, growth_rel=0.04), ts.LEVEL_GATE)
    assert r["seasonal"] is True
    assert r["kind"] == "계절형"


def test_계절이_유행보다_먼저다():
    """유행처럼 보이는 것이 달력일 때가 많다. 둘 다 걸리면 계절로 읽는다."""
    r = ts.classify(_row(swing=2.0, repeat=0.9, growth_rel=0.9), ts.LEVEL_GATE)
    assert r["kind"] == "계절형"


def test_평평하면_스테디셀러다():
    r = ts.classify(_row(level=6.6, swing=1.25, repeat=0.23, growth_rel=0.08),
                    ts.LEVEL_GATE)
    assert r["steady"] is True and r["seasonal"] is False and r["fad"] is False
    assert r["kind"] == "스테디셀러"


# ────────────────────────────────────────────── 눈금
def test_주_눈금은_그_주_월요일이다():
    assert ts._bucket("2026-09-16", "week") == "2026-09-14"   # 수요일 -> 월요일
    assert ts._bucket("2026-09-14", "week") == "2026-09-14"
    assert ts._bucket("2026-09-16", "month") == "2026-09"
    assert ts._bucket("2026-09-16", "day") == "2026-09-16"


# ────────────────────────────────────────────── 산출물이 없을 때
def test_수준_파일이_없으면_오류가_아니다(monkeypatch, tmp_path):
    """네이버 키가 없어도 리뷰 축과 전국 증가율은 그대로 돌아야 한다."""
    monkeypatch.setattr(ts, "LEVEL_FILE", tmp_path / "없다.json")
    monkeypatch.setattr(ts, "_base", None)
    got = ts.search()
    assert got["available"] is False
    assert "hint" in got and got["hint"]
    assert not any(k.startswith("_") for k in got)     # 내부 시계열이 새지 않는다
