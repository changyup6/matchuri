# -*- coding: utf-8 -*-
"""방문 근거 — **앱이 계산하고 서버는 받아 적는다.**

길찾기는 방문 의사다. 실제로 갔는지는 위치로 확인해야 하는데, 그 판정을 **서버에서
하지 않는다.** 앱이 이미 자기 위치를 들고 있고 가게 좌표도 응답으로 받으므로, 기기에서
계산하고 결과만 보내면 된다 — 위치가 기기를 떠나지 않는 쪽이 맞고, 서버가 궤적을 들고
있을 이유도 없다.

잠그는 것 넷:

1. **가게 좌표가 응답에 있다** — 없으면 앱이 판정할 수 없다. FE 가 주소를 지오코딩해서
   쓰고 있었는데(`지하 1층` 같은 접미사에서 자주 실패한다) 서버는 정확한 값을 갖고 있었다.
2. **근거가 숫자로 저장된다** — `closest_m` 을 쌓아 두지 않으면 '도착 반경 얼마가
   맞나' 가 앱에 굳어 버리고 나중에 데이터로 다시 볼 수 없다.
3. **좌표는 받지 않는다** — 설문에 위도·경도를 받는 필드가 없다. 스칼라만 받는다.
4. **서버가 거르지 않는다** — 근거가 없어도, `arrived=false` 여도 설문은 받는다.
   콜드스타트에 문을 잠그면 규칙을 검증할 표본도 못 모은다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import geo, state


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


@pytest.fixture
def uid(client):
    return client.post("/users", json={"name": "근거"}).json()["user_id"]


@pytest.fixture
def card(client):
    return client.get("/cards?limit=1").json()[0]["card_id"]


REVIEW = {"taste": "맛있었다", "price": "적당했다", "mood": "조용했다"}


def _answer(client, uid, card, **evidence):
    client.post(f"/users/{uid}/directions",
                json={"card_id": card, "map_app": "kakao", "mode": "walk"})
    return client.post(f"/users/{uid}/surveys",
                       json={"card_id": card, "visited": True,
                             "satisfaction": "good", "review": dict(REVIEW),
                             **evidence})


def _last(uid) -> dict:
    rows = [x for x in state.read_surveys() if x.get("user_id") == uid]
    assert rows, "설문이 기록되지 않았다"
    return rows[-1]["answers"]


# ────────────────────────────────────────────── 가게 좌표
def test_가게_좌표가_응답에_있다(client, card):
    """없으면 앱이 도착 판정을 할 수 없다. 그리고 주소 지오코딩은 자주 실패한다."""
    s = client.get(f"/cards/{card}").json()["store"]
    assert isinstance(s["lat"], float) and isinstance(s["lon"], float)
    # 역삼동이다(위도 37.49~37.51, 경도 127.02~127.06).
    assert 37.48 < s["lat"] < 37.52, s["lat"]
    assert 127.01 < s["lon"] < 127.07, s["lon"]


def test_좌표가_실제_가게_좌표와_같다(client, card):
    """서버가 거리 필터에 쓰는 값과 앱에 주는 값이 달라지면 안 된다."""
    s = client.get(f"/cards/{card}").json()["store"]
    assert geo.coords()[s["id"]] == (s["lat"], s["lon"])


def test_목록_카드에도_좌표가_있다(client):
    """상세를 다시 불러야 판정할 수 있으면 카드 수만큼 요청이 늘어난다."""
    for c in client.get("/cards?limit=10").json():
        assert c["store"]["lat"] is not None, c["store"]["name"]


# ────────────────────────────────────────────── 근거를 받아 적는다
def test_앱이_계산한_근거를_저장한다(client, uid, card):
    r = _answer(client, uid, card, arrived=True, closest_m=34, dwell_min=41)
    assert r.status_code == 200 and r.json()["recorded"] is True
    a = _last(uid)
    assert (a["arrived"], a["closest_m"], a["dwell_min"]) == (True, 34, 41)


def test_근거가_없어도_설문은_받는다(client, uid, card):
    """위치 권한을 거부한 사용자의 방문도 물어봐야 한다."""
    assert _answer(client, uid, card).status_code == 200
    a = _last(uid)
    assert "arrived" not in a and "closest_m" not in a


def test_안_갔다는_근거여도_설문은_받는다(client, uid, card):
    """콜드스타트에 문을 잠그면 규칙이 맞는지 확인할 표본도 못 모은다.

    `arrived=false` 는 '앱이 보기에 멀었다' 이지 '거짓말' 이 아니다 — 실내에서 좌표가
    튀었을 수도 있다. 판정은 기록하고, 막는 것은 나중에 데이터를 보고 정한다.
    """
    r = _answer(client, uid, card, arrived=False, closest_m=800)
    assert r.status_code == 200
    a = _last(uid)
    assert a["closest_m"] == 800
    # `false` 는 **남는다.** '앱이 보기에 멀었다' 는 음성 근거이고, 위치를 못 본
    # '모른다'(필드 없음)와 다른 사실이다. 둘을 합치면 나중에 구분할 수 없다.
    assert a["arrived"] is False


def test_dwell_만_있어도_된다(client, uid, card):
    r = _answer(client, uid, card, dwell_min=0)
    assert r.status_code == 200


@pytest.mark.parametrize("bad", [
    {"closest_m": -1},
    {"dwell_min": -5},
    {"closest_m": "가까움"},
    {"dwell_min": 1.5},
])
def test_망가진_근거는_422(client, uid, card, bad):
    assert _answer(client, uid, card, **bad).status_code == 422


def test_설문은_좌표를_받지_않는다(client, uid, card):
    """**위치가 기기를 떠나지 않는 것이 이 설계의 요점이다.**

    스키마에 좌표 필드가 없으므로 앱이 실수로 보내도 무시된다(pydantic 기본값).
    이 시험이 깨지면 궤적이 서버에 쌓이기 시작한다.
    """
    spec = client.get("/openapi.json").json()
    fields = set(spec["components"]["schemas"]["SurveyIn"]["properties"])
    assert not (fields & {"lat", "lon", "latitude", "longitude", "samples",
                          "locations", "track"}), fields
    r = _answer(client, uid, card, lat=37.5, lon=127.0)
    assert r.status_code == 200
    a = _last(uid)
    assert "lat" not in a and "lon" not in a


# ────────────────────────────────────────────── 서버는 판정하지 않는다
def test_pending_에는_판정_필드가_없다(client, uid, card):
    """서버가 판정하지 않으므로 항상 `null` 이 될 필드를 두지 않는다.

    있으면 프론트가 '서버가 채워 주겠지' 하고 기다린다.
    """
    client.post(f"/users/{uid}/directions",
                json={"card_id": card, "map_app": "kakao", "mode": "walk"})
    row = client.get(f"/users/{uid}/surveys/pending").json()[0]
    assert not ({"arrived", "closest_m", "dwell_min"} & set(row)), row


def test_위치_저장_API_는_판정_필드를_받지_않는다(client):
    """`accuracy_m` 은 서버가 판정할 때 쓰던 값이다 — 판정을 앱으로 옮겨 없앴다."""
    spec = client.get("/openapi.json").json()
    fields = set(spec["components"]["schemas"]["LocationIn"]["properties"])
    assert fields == {"lat", "lon", "at"}, fields


def test_위치_저장은_그대로_동작한다(client, uid, card):
    """판정을 떼어 냈다고 설정 저장이 흔들리면 안 된다 — 거리 필터가 그걸 읽는다."""
    r = client.put(f"/users/{uid}/location", json={"lat": 37.5006, "lon": 127.0366})
    assert r.status_code == 200
    assert r.json()["location"]["lat"] == pytest.approx(37.5006)
    one = client.get(f"/cards?user_id={uid}&limit=1").json()[0]
    assert one["distance_m"] is not None
