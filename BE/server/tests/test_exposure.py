# -*- coding: utf-8 -*-
"""노출 집중도 — **쏠림을 숨기지 않게** 잠근다.

이 지표는 트렌드를 켤지 정하는 관문이다(`analysis/TREND.md` 7절). 관문이 실제보다
좋은 수를 내면 쏠린 채로 켜게 된다. 그래서 여기서 잠그는 것은 지니 공식의 산술이
아니라 **분모를 어디까지 잡았나**다 — 한 겹만 잘못 잡아도 그럴듯한 거짓 안심이 나온다.

1. **노출 0 인 가게가 분모에 있다** — 빼면 '한 번도 안 나온 가게' 가 사라져 집중도가
   좋아 보인다. 같은 노출 분포라도 풀이 크면 지니가 커져야 맞다.
2. **재노출을 센다** — 같은 가게를 몇 번 밀었나가 곧 쏠림이다.
3. **정책을 합치지 않는다** — 인터리빙이 둘을 섞으면 어느 쪽이 쏠리는지 안 보인다.
4. **기록이 없는 것은 오류가 아니다** — 대신 왜 없는지 말해야 한다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recsys import exposure as ex


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


# ────────────────────────────────────────────────────── 지니 눈금
def test_지니_눈금():
    assert ex._gini([1] * 100) == pytest.approx(0.0)
    assert ex._gini([0] * 50 + [1] * 50) == pytest.approx(0.5)
    assert ex._gini([0] * 99 + [7]) == pytest.approx(0.99)   # 유한 n 의 최댓값 (n-1)/n
    assert ex._gini([]) is None
    assert ex._gini([0, 0, 0]) is None                        # 잴 것이 없다


def test_지니는_규모가_아니라_모양을_본다():
    """노출을 전부 10배 해도 집중도는 그대로다. 아니면 트래픽이 늘 때마다 경보가 난다."""
    a = [3, 1, 0, 0, 5]
    assert ex._gini(a) == pytest.approx(ex._gini([x * 10 for x in a]))


# ────────────────────────────────────────────────────── 분모 (핵심)
def test_노출_0_인_가게가_분모에_들어간다():
    """같은 노출 분포인데 풀이 크면 지니가 **커져야** 한다.

    이게 이 모듈에서 제일 중요한 성질이다. 노출된 가게만 모아 재면 두 값이 같아지고,
    그러면 '1,600곳 중 3곳만 나갔다' 와 '3곳 중 3곳이 나갔다' 를 구별하지 못한다.
    """
    counts = {"a": 5, "b": 3, "c": 2}
    좁은풀 = ex._summary(counts, ["a", "b", "c"])
    넓은풀 = ex._summary(counts, ["a", "b", "c"] + [f"z{i}" for i in range(97)])

    assert 넓은풀["gini"] > 좁은풀["gini"]
    assert 좁은풀["never_shown"] == 0
    assert 넓은풀["never_shown"] == 97
    assert 좁은풀["exposures"] == 넓은풀["exposures"] == 10   # 노출 총량은 같다


def test_안_나온_곳을_따로_싣는다():
    """지니 한 숫자로는 '얇고 넓게' 와 '몇몇만 자주' 가 안 갈린다."""
    얇고넓게 = ex._summary({f"s{i}": 1 for i in range(100)}, [f"s{i}" for i in range(100)])
    몇몇만자주 = ex._summary({"s0": 100}, [f"s{i}" for i in range(100)])

    assert 얇고넓게["never_shown"] == 0 and 얇고넓게["gini"] == pytest.approx(0.0)
    assert 몇몇만자주["never_shown"] == 99 and 몇몇만자주["gini"] == pytest.approx(0.99)


def test_상위_N_점유율():
    counts = {"a": 10, "b": 5, "c": 3, "d": 1, "e": 1}
    s = ex._summary(counts, list("abcde"))["top_share"]
    assert s["10"] == pytest.approx(1.0)        # 다섯 곳뿐이라 상위 10 이 전부다
    assert sum(counts.values()) == 20
    assert ex._shares([10, 5, 3, 1, 1], top_n=(1, 3)) == {"1": 0.5, "3": 0.9}


# ────────────────────────────────────────────────────── 경로
def test_기록이_없는_것은_오류가_아니다(client):
    """노출은 카드가 실제로 나갈 때만 쌓인다. 없으면 **왜 없는지** 말해야 한다."""
    r = client.get("/admin/api/trend/concentration")
    assert r.status_code == 200
    body = r.json()
    assert {"available", "pool", "trend_enabled", "days"} <= set(body)
    assert body["pool"] > 0
    if not body["available"]:
        assert body["hint"]
    else:
        assert set(body["by_policy"]) == {"legacy", "taste", "trend"}


def test_관문은_트렌드가_켜졌는지_같이_말한다(client):
    """끄고 잰 값인지 켜고 잰 값인지 모르면 견줄 수가 없다."""
    body = client.get("/admin/api/trend/concentration").json()
    assert body["trend_enabled"] is False        # 기본은 꺼짐 (TREND.md 6절)
    assert body["trend_weight"] == 0.0


def test_창을_자를_수_있다(client):
    """켜고 끈 전후를 견주려면 기간을 잘라 봐야 한다."""
    assert client.get("/admin/api/trend/concentration?days=30").json()["days"] == 30
    assert client.get("/admin/api/trend/concentration?days=0").json()["days"] == 0
    assert client.get("/admin/api/trend/concentration?days=-1").status_code == 422
