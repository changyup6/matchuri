# -*- coding: utf-8 -*-
"""트렌드 추이 — **관측이 거짓말하지 않게** 잠근다.

여기서 잠그는 것은 계산의 정확성이 아니라 **해석의 정확성**이다. 트렌드 지표는 분모를
한 겹 잘못 잡으면 그럴듯한 거짓 신호를 낸다. 실제로 세 번 겪었고(모듈 머리말) 그때마다
순위표가 완전히 달라졌다.

1. **건수로 순위를 내지 않는다** — 월 총량이 한 해에 두 배(718 -> 1,513)라 건수로는
   전부 상승이다.
2. **반쪽 달을 빼고 센다** — 크롤이 달 중간에 끊긴 달을 넣으면 "급락" 이 만들어진다.
3. **업종 안에서 정규화한다** — 전체 대비로 재면 업종 구성 이동(한식 +5.4pp)이 그 업종
   메뉴를 전부 밀어 올린다.
4. **유행과 계절을 섞지 않는다** — 직전 3달 대비는 달력을 재고, 전년 대비가 유행이다.
5. **얇은 표본을 헤드라인에 올리지 않는다** — 분식은 월 30건이라 비율이 튄다.
6. **네이버가 없어도 돈다** — 두 축은 독립이다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recsys import trend_history as th


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


@pytest.fixture(scope="module")
def h():
    return th.local()


# ────────────────────────────────────────────── 달 고르기
def test_반쪽_달은_계산에서_빠진다(h):
    """크롤이 달 중간에 끝나면 그 달은 반쪽이다(2026-09 는 09-02 크롤이라 49건).

    넣으면 "9월에 급락" 이라는 거짓 신호가 된다. 빼고, 뺐다는 사실을 알려 준다.
    """
    if h["partial_month"]:
        assert h["partial_month"] not in h["months"]


def test_달이_연속이고_오름차순이다(h):
    ms = h["months"]
    assert ms == sorted(ms)
    for a, b in zip(ms, ms[1:]):
        ya, ma = int(a[:4]), int(a[5:])
        assert b == (f"{ya + 1}-01" if ma == 12 else f"{ya}-{ma + 1:02d}"), (a, b)


def test_세_창이_겹치지_않고_전년과_12달_떨어져_있다(h):
    w = h["windows"]
    assert len(w["recent"]) == th.SWING and len(w["prior"]) == th.SWING
    assert not set(w["recent"]) & set(w["prior"])
    if w["year_ago"]:
        a, b = w["recent"][0], w["year_ago"][0]
        assert int(a[:4]) - int(b[:4]) == 1 and a[5:] == b[5:], (a, b)


# ────────────────────────────────────────────── 분모
def test_비율의_분모가_그_달_총량이다(h):
    """건수가 아니라 점유율로 봐야 한다 — 월 총량이 한 해에 두 배가 됐다."""
    for row in h["by_category"][:5]:
        for m in h["months"]:
            n, tot = row["count"][m], h["total"][m]
            assert row["share"][m] == pytest.approx(n / tot if tot else 0.0, abs=1e-4)


def test_월_총량이_실제로_늘었다(h):
    """이 테스트가 깨지면 '건수로 보면 안 된다' 는 근거가 사라진 것이다 — 그때는
    모듈 머리말의 수치를 다시 재야 한다."""
    ms, tot = h["months"], h["total"]
    first = sum(tot[m] for m in ms[:3]) / 3
    last = sum(tot[m] for m in ms[-3:]) / 3
    assert last > first, (first, last)


def test_키워드는_업종_안에서_정규화된다(h):
    """전체 대비로 재면 업종 구성 이동이 그 업종 메뉴를 전부 밀어 올린다.

    `share` 는 **그 업종 리뷰 대비**, `share_overall` 이 전체 대비다. 업종 점유율이
    1 보다 작으므로 업종 안 비율이 항상 더 크거나 같아야 한다.
    """
    cat_share = {r["category"]: r["share"] for r in h["by_category"]}
    checked = 0
    for row in h["by_keyword"][:20]:
        scope = row["scope"]
        if scope not in cat_share:
            continue
        for m in h["months"]:
            if row["count"][m] == 0:
                continue
            assert row["share"][m] >= row["share_overall"][m] - 1e-9, (row["keyword"], m)
            checked += 1
    assert checked, "확인할 표본이 없었다"


# ────────────────────────────────────────────── 유행 vs 계절
def test_유행과_계절을_따로_준다(client):
    r = client.get("/admin/api/trend/movers?top=5")
    assert r.status_code == 200
    j = r.json()
    assert {"rising", "falling", "in_season", "out_of_season"} <= set(j)
    # **두 목록이 서로 다른 값으로 줄을 세우는지** 잠근다. 같은 값으로 정렬하면
    # 화면에 두 칸을 만들어 놓고 같은 것을 두 번 보여주는 셈이다.
    fad = [r["yoy_3m"]["delta"] for r in j["rising"]]
    season = [r["season_3m"]["delta"] for r in j["in_season"]]
    assert fad == sorted(fad, reverse=True), fad
    assert season == sorted(season, reverse=True), season


def test_전년_대비는_같은_달끼리_견준다(h):
    """계절을 상쇄하는 유일한 방법이다. 창이 어긋나면 그 성질이 사라진다."""
    w = h["windows"]
    if not w["year_ago"]:
        pytest.skip("창이 짧아 전년 비교가 없다")
    assert [m[5:] for m in w["recent"]] == [m[5:] for m in w["year_ago"]]


def test_얇은_업종은_헤드라인에서_빠진다(client, h):
    """분식은 월 30건 수준이라 비율이 튄다(순대 +21pp 가 나왔다) — 값은 남기고
    순위에서만 뺀다."""
    thin = {r["keyword"] for r in h["by_keyword"] if r["thin"]}
    j = client.get("/admin/api/trend/movers?top=50").json()
    ranked = {x["keyword"] for x in j["rising"] + j["falling"]}
    assert not (ranked & thin), sorted(ranked & thin)[:5]
    if thin:
        assert thin <= {r["keyword"] for r in h["by_keyword"]}, "값이 사라졌다"


# ────────────────────────────────────────────── 가게 증가율
def test_표본이_얇은_가게는_증가율_순위에_없다(client):
    """창 안 리뷰가 가게당 중앙값 2건이라, 안 걸러내면 순위표가 2건 대 0건짜리
    잡음으로 채워진다."""
    j = client.get("/admin/api/trend/stores?top=10").json()
    assert j["min_reviews"] == th.MIN_INFLOW_N
    for s in j["stores"]:
        assert s["recent"] >= th.MIN_INFLOW_N, s
    # 하한을 낮추면 자격 가게가 늘어야 한다 — 그게 이 필터가 실제로 거르고 있다는
    # 증거다. 상수만 비교하면 필터가 꺼져 있어도 통과한다.
    loose = th.store_inflow(months=6, min_n=1, top=1)
    assert loose["eligible"] > j["eligible"], (loose["eligible"], j["eligible"])


def test_증가율이_내림차순이다(client):
    j = client.get("/admin/api/trend/stores?top=20").json()
    vals = [s["inflow"] for s in j["stores"]]
    assert vals == sorted(vals, reverse=True)


# ────────────────────────────────────────────── 네이버가 없어도 돈다
def test_전국_추이가_없어도_역삼동은_돈다(client, monkeypatch):
    """두 축은 독립이다. 키가 없거나 수집을 안 돌렸어도 관리자 화면이 죽지 않는다."""
    monkeypatch.setattr(th, "SCORE_FILE", th.SCORE_FILE.with_name("__없는파일__.json"))
    j = client.get("/admin/api/trend/national").json()
    assert j["available"] is False and "hint" in j
    assert client.get("/admin/api/trend/movers").status_code == 200


def test_전국_추이는_같은_월_축에_올라온다(client):
    """역삼동과 나란히 그리려면 축이 같아야 한다. 일별 원본을 달 평균으로 접는다."""
    j = client.get("/admin/api/trend/national").json()
    if not j["available"] or not j["series"]:
        pytest.skip("수집 산출물이 없다")
    months = set(th.local()["months"])
    for name, series in list(j["series"].items())[:5]:
        assert set(series) <= months, (name, sorted(set(series) - months)[:3])


# ────────────────────────────────────────────── 추천과의 경계
def test_관측은_추천을_바꾸지_않는다(client):
    """**이 모듈은 관측 전용이다.** 여기서 점수가 움직이면 트렌드를 켤지 판단하려고
    만든 화면이 판단 대상을 오염시킨다."""
    before = [c["card_id"] for c in client.get("/cards?limit=8").json()]
    client.get("/admin/api/trend/movers")
    client.get("/admin/api/trend/stores")
    client.get("/admin/api/trend/national")
    assert [c["card_id"] for c in client.get("/cards?limit=8").json()] == before


# ────────────────────────────────────────────── 화면
def test_화면이_뜬다(client):
    r = client.get("/admin/trends")
    assert r.status_code == 200
    assert "트렌드 추이" in r.text


def test_화면에_외부_의존이_없다(client):
    """관리자 화면이 CDN 을 타면 망이 막힌 곳에서 통째로 안 뜬다.

    이 저장소의 관리자 화면 셋(`admin.html`·`map.html`·`taste.html`)이 다 그렇게
    되어 있다 — 차트는 인라인 SVG 로 그린다.
    """
    import re
    urls = re.findall(r"https?://[^\"' )]+", client.get("/admin/trends").text)
    assert not urls, urls


def test_화면이_쓰는_필드가_다_있다(client):
    """화면은 JSON 키를 손으로 읽는다. 응답 모양이 바뀌면 화면이 조용히 빈다 —
    타입이 없으니 여기서 잠근다."""
    h = client.get("/admin/api/trend/history").json()
    assert {"months", "windows", "reviews", "total",
            "by_keyword", "by_category", "partial_month"} <= set(h)
    assert {"keyword", "scope", "thin", "n", "share", "share_overall",
            "count", "yoy_3m", "season_3m"} <= set(h["by_keyword"][0])
    assert {"category", "n", "share", "yoy_3m", "season_3m"} <= set(h["by_category"][0])

    mv = client.get("/admin/api/trend/movers?top=3").json()
    assert {"rising", "falling", "in_season", "out_of_season", "windows"} <= set(mv)

    st = client.get("/admin/api/trend/stores?top=3").json()
    assert {"stores", "eligible", "window_days", "min_reviews"} <= set(st)
    if st["stores"]:
        assert {"store_id", "name", "cat2", "recent", "prior", "inflow"} <= set(st["stores"][0])

    na = client.get("/admin/api/trend/national").json()
    assert {"available", "scores", "series"} <= set(na)

    cc = client.get("/admin/api/trend/concentration").json()
    assert {"available", "pool", "trend_enabled", "days"} <= set(cc)
    if cc["available"]:
        assert {"exposures", "shown", "never_shown", "gini",
                "top_share", "by_policy", "users"} <= set(cc)
        assert set(cc["by_policy"]) == {"legacy", "taste", "trend"}
    else:
        assert cc["hint"]          # 없으면 왜 없는지 말해 줘야 한다

