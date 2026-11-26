# -*- coding: utf-8 -*-
"""설문에 붙이는 후기와 사진 — API 층.

파일 처리 자체는 `test_media.py` 가 잠근다(EXIF 좌표 제거·회전·형식). 여기서 잠그는
것은 **누가 무엇을 올릴 수 있나**다.

1. **길찾기가 선행돼야 한다.** 아무 카드에나 올릴 수 있으면 이 API 는 그냥 파일
   저장소가 된다. 설문과 같은 문을 쓴다.
2. **장수 상한.** 재시작 뒤에도 유지돼야 한다 — 색인을 되살리지 않으면 3장을 또 올린다.
3. **추천과의 경계.** 찜·길찾기와 같은 이유다(`test_journeys.py`). 사진을 올린다고
   취향 모델이 움직이면 인터리빙의 like 율이 오염된다.
"""
from __future__ import annotations

import copy
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import journeys, media, state
from recsys import policy


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


@pytest.fixture
def uid(client):
    return client.post("/users", json={"name": "사진"}).json()["user_id"]


@pytest.fixture
def card(client):
    return client.get("/cards?limit=3").json()[0]["card_id"]


@pytest.fixture(autouse=True)
def 임시저장소(tmp_path, monkeypatch):
    """사진을 실제 미디어 폴더에 쓰지 않는다."""
    monkeypatch.setattr(media, "DIR", tmp_path / "media")
    return tmp_path


def _png(size=(90, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def _jpg(size=(90, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 60, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _upload(client, uid, card, data=None, name="a.jpg", ct="image/jpeg"):
    return client.post(f"/users/{uid}/surveys/{card}/photos",
                       files={"file": (name, data if data is not None else _jpg(), ct)})


def _direction(client, uid, card):
    r = client.post(f"/users/{uid}/directions",
                    json={"card_id": card, "map_app": "kakao", "mode": "walk"})
    assert r.status_code == 200


# ────────────────────────────────────────────────── 문
def test_길찾기_없이도_올릴_수_있다(client, uid, card):
    """설문이 길찾기를 요구하지 않게 되면서 사진도 같이 열렸다(2026-09-16).

    막는 것은 **사람당 총량**이다(`media.MAX_PER_USER`) — 길찾기 관문이 사실상의
    상한이었는데 그게 없어졌고, 그대로 두면 카드 2,795장 × 3장까지 한 사람이 밀어
    넣을 수 있다. 디스크가 먼저 찬다.
    """
    r = _upload(client, uid, card)
    assert r.status_code == 201
    client.delete(f"/users/{uid}/surveys/{card}/photos/{r.json()['photo_id']}")


def test_없는_카드에는_올릴_수_없다(client, uid):
    r = _upload(client, uid, 10 ** 9)
    assert r.status_code == 404


def test_사람당_총량을_넘으면_409(client, card):
    """상한이 없으면 이 API 는 누구나 쓰는 파일 저장소가 된다."""
    uid = client.post("/users", json={"name": "사진많음"}).json()["user_id"]
    cards = [c["card_id"] for c in client.get("/cards?limit=40").json()]
    assert len(cards) * media.MAX_PER_CARD > media.MAX_PER_USER, "상한을 넘길 카드가 부족하다"
    uploaded = []
    hit = None
    for cid in cards:
        for _ in range(media.MAX_PER_CARD):
            r = _upload(client, uid, cid)
            if r.status_code == 409:
                hit = r
                break
            assert r.status_code == 201, r.text
            uploaded.append((cid, r.json()["photo_id"]))
        if hit is not None:
            break
    assert hit is not None, "상한에 걸리지 않았다"
    assert str(media.MAX_PER_USER) in hit.json()["detail"]
    assert len(uploaded) == media.MAX_PER_USER
    for cid, pid in uploaded:
        client.delete(f"/users/{uid}/surveys/{cid}/photos/{pid}")


def test_길찾기한_카드에는_올릴_수_있다(client, uid, card):
    _direction(client, uid, card)
    r = _upload(client, uid, card)
    assert r.status_code == 201
    body = r.json()
    assert len(body["photo_id"]) == 32
    assert body["url"] == f"/media/{body['photo_id'][:2]}/{body['photo_id']}.jpg"
    assert body["width"] == 90 and body["height"] == 60
    assert media.path_of(body["photo_id"]).exists()


def test_없는_사용자_형식은_거절한다(client, card):
    r = _upload(client, "x" * 200, card)
    assert r.status_code in (400, 422)


# ────────────────────────────────────────────────── 형식·크기
def test_이미지가_아니면_415(client, uid, card):
    _direction(client, uid, card)
    r = _upload(client, uid, card, data=b"not an image at all")
    assert r.status_code == 415


def test_png_도_받는다(client, uid, card):
    _direction(client, uid, card)
    r = _upload(client, uid, card, data=_png(), name="a.png", ct="image/png")
    assert r.status_code == 201
    # 저장은 전부 JPEG 다 — 확장자·URL 도 jpg 다.
    assert r.json()["url"].endswith(".jpg")


def test_받지_않는_형식은_415(client, uid, card):
    _direction(client, uid, card)
    r = _upload(client, uid, card, data=_jpg(), name="a.gif", ct="image/gif")
    assert r.status_code == 415


def _padded(n: int) -> bytes:
    """정확히 `n` 바이트인 **진짜 JPEG**. EOI 뒤의 바이트는 디코더가 무시한다.

    크기 경계를 재려면 '읽을 수 있는 이미지인데 크기만 큰' 것이 필요하다. 깨진
    파일을 쓰면 415 가 크기 때문인지 디코딩 때문인지 구분되지 않는다.
    """
    head = _jpg()
    assert len(head) <= n
    return head + b"\x00" * (n - len(head))


def test_상한_크기까지는_받는다(client, uid, card):
    """정확히 상한인 파일은 통과한다 — `read(MAX_BYTES + 1)` 의 +1 이 그것을 위한 것이다."""
    _direction(client, uid, card)
    r = _upload(client, uid, card, data=_padded(media.MAX_BYTES))
    assert r.status_code == 201, r.text


def test_상한을_한_바이트_넘으면_415(client, uid, card):
    _direction(client, uid, card)
    r = _upload(client, uid, card, data=_padded(media.MAX_BYTES + 1))
    assert r.status_code == 415
    # 사용자에게 보이는 말에 실제 상한이 들어가야 한다. 상수를 올리고 문구를
    # 그대로 두면 앱이 틀린 안내를 한다.
    assert f"{media.MAX_BYTES // (1024 * 1024)}MB" in r.json()["detail"]


# ────────────────────────────────────────────────── 장수 상한
def test_상한을_넘으면_409(client, uid, card):
    _direction(client, uid, card)
    for _ in range(media.MAX_PER_CARD):
        assert _upload(client, uid, card).status_code == 201
    r = _upload(client, uid, card)
    assert r.status_code == 409
    assert str(media.MAX_PER_CARD) in r.json()["detail"]


def test_상한은_카드마다_따로다(client, uid):
    cards = [c["card_id"] for c in client.get("/cards?limit=3").json()]
    for c in cards[:2]:
        _direction(client, uid, c)
        for _ in range(media.MAX_PER_CARD):
            assert _upload(client, uid, c).status_code == 201
    assert len(journeys.photos_of(uid, cards[0])) == media.MAX_PER_CARD
    assert len(journeys.photos_of(uid, cards[1])) == media.MAX_PER_CARD


# ────────────────────────────────────────────────── 지우기
def test_지우면_파일과_색인이_같이_빠진다(client, uid, card):
    _direction(client, uid, card)
    pid = _upload(client, uid, card).json()["photo_id"]
    r = client.delete(f"/users/{uid}/surveys/{card}/photos/{pid}")
    assert r.status_code == 204
    assert not media.path_of(pid).exists()
    assert journeys.photos_of(uid, card) == []


def test_지우면_다시_올릴_자리가_생긴다(client, uid, card):
    _direction(client, uid, card)
    ids = [_upload(client, uid, card).json()["photo_id"]
           for _ in range(media.MAX_PER_CARD)]
    assert _upload(client, uid, card).status_code == 409
    client.delete(f"/users/{uid}/surveys/{card}/photos/{ids[0]}")
    assert _upload(client, uid, card).status_code == 201


def test_없는_사진은_404(client, uid, card):
    _direction(client, uid, card)
    r = client.delete(f"/users/{uid}/surveys/{card}/photos/{'0' * 32}")
    assert r.status_code == 404


def test_남의_사진은_지울_수_없다(client, card):
    a = client.post("/users", json={"name": "A"}).json()["user_id"]
    b = client.post("/users", json={"name": "B"}).json()["user_id"]
    _direction(client, a, card)
    pid = _upload(client, a, card).json()["photo_id"]
    _direction(client, b, card)
    assert client.delete(f"/users/{b}/surveys/{card}/photos/{pid}").status_code == 404
    assert media.path_of(pid).exists()          # 남의 파일이 지워지지 않았다


# ────────────────────────────────────────────────── 배너에 실린다
def test_pending_에_올린_사진이_실린다(client, uid, card):
    _direction(client, uid, card)
    pid = _upload(client, uid, card).json()["photo_id"]
    rows = [r for r in client.get(f"/users/{uid}/surveys/pending").json()
            if r["card_id"] == card]
    assert rows and rows[0]["photo_ids"] == [pid]


def test_올린_순서가_유지된다(client, uid, card):
    _direction(client, uid, card)
    ids = [_upload(client, uid, card).json()["photo_id"] for _ in range(3)]
    assert journeys.photos_of(uid, card) == ids


# ────────────────────────────────────────────────── 재시작
def test_재시작해도_사진이_남는다(client, uid, card):
    """색인을 되살리지 않으면 올린 사진이 안 보이고 상한도 처음부터 다시 센다."""
    _direction(client, uid, card)
    pid = _upload(client, uid, card).json()["photo_id"]
    journeys._photos.clear()                    # 재시작을 흉내낸다
    assert journeys.photos_of(uid, card) == []
    journeys.restore()
    assert journeys.photos_of(uid, card) == [pid]


def test_지운_사진은_되살아나지_않는다(client, uid, card):
    """지우기를 지우기로 기록하지 않으면 재시작 때 돌아온다."""
    _direction(client, uid, card)
    pid = _upload(client, uid, card).json()["photo_id"]
    client.delete(f"/users/{uid}/surveys/{card}/photos/{pid}")
    journeys._photos.clear()
    journeys.restore()
    assert journeys.photos_of(uid, card) == []


def test_기록에_store_id_가_함께_남는다(client, uid, card):
    """`card_id` 는 다시 매겨질 수 있다. 오래 남는 사실은 `store_id` 로 가리킨다."""
    _direction(client, uid, card)
    pid = _upload(client, uid, card).json()["photo_id"]
    rows = [r for r in state.read_survey_photos() if r.get("photo_id") == pid]
    assert rows and rows[0].get("store_id")


# ────────────────────────────────────────────────── 후기(텍스트)
#: 방문 설문의 필수 후기 세 줄(`app/model.py: ReviewIn`).
REVIEW = {"taste": "고등어초회가 신선했어요", "price": "2인 4만원",
          "mood": "조용했어요"}


def test_후기_세_줄이_저장된다(client, uid, card):
    _direction(client, uid, card)
    r = client.post(f"/users/{uid}/surveys",
                    json={"card_id": card, "visited": True, "satisfaction": "good",
                          "review": {"taste": "  고등어초회가 신선했어요  ",
                                     "price": "2인 4만원", "mood": "조용했어요"}})
    assert r.status_code == 200 and r.json()["recorded"] is True
    rows = [x for x in state.read_surveys() if x.get("user_id") == uid]
    got = rows[-1]["answers"]["review"]
    assert got["taste"] == "고등어초회가 신선했어요", "앞뒤 공백이 잘려야 한다"
    assert set(got) == {"taste", "price", "mood"}


@pytest.mark.parametrize("bad,why", [
    ({"taste": "   ", "price": "싸다", "mood": "좋다"}, "공백만 온 줄"),
    ({"taste": "맛있다", "price": "싸다"}, "한 줄이 빠졌다"),
    ({"taste": "가" * 201, "price": "싸다", "mood": "좋다"}, "200자를 넘겼다"),
])
def test_후기_한_줄이라도_비면_422(client, uid, card, bad, why):
    """'필수' 가 이름뿐이 되지 않게 입구에서 막는다 — 빈 줄이 가게 화면에 그대로 간다."""
    _direction(client, uid, card)
    r = client.post(f"/users/{uid}/surveys",
                    json={"card_id": card, "visited": True,
                          "satisfaction": "good", "review": bad})
    assert r.status_code == 422, why


@pytest.mark.parametrize("body,why", [
    ({"visited": True}, "만족도와 후기가 둘 다 없다"),
    ({"visited": True, "satisfaction": "good"}, "후기가 없다"),
    ({"visited": True, "review": REVIEW}, "만족도가 없다"),
])
def test_방문했으면_만족도와_후기가_둘_다_필수다(client, uid, card, body, why):
    """선택으로 두면 대부분 비워서 온다. 그러면 추천이 맞았는지도 모르고 가게 리뷰도
    안 쌓인다 — 이 설문이 있는 이유가 그 둘이다.
    """
    _direction(client, uid, card)
    r = client.post(f"/users/{uid}/surveys", json={"card_id": card, **body})
    assert r.status_code == 422, why


def test_방문하지_않았으면_후기가_필요없다(client, uid, card):
    _direction(client, uid, card)
    r = client.post(f"/users/{uid}/surveys",
                    json={"card_id": card, "visited": False,
                          "not_visited_reason": "no_time"})
    assert r.status_code == 200


def test_후기가_세부_평균을_오염시키지_않는다(client, uid, card):
    """`answers` 에 dict 가 들어가도 집계는 화이트리스트의 수치만 평균 낸다."""
    _direction(client, uid, card)
    client.post(f"/users/{uid}/surveys",
                json={"card_id": card, "visited": True, "satisfaction": "good",
                      "review": REVIEW})
    stats = policy.survey_stats()               # 터지지 않아야 한다
    assert isinstance(stats, dict)


# ────────────────────────────────────────────────── 추천과의 경계
def test_사진과_후기는_추천을_바꾸지_않는다(client, uid, card):
    """찜·길찾기와 같은 계약이다(`test_journeys.py`). 여기서 모델이 움직이면
    인터리빙의 like 율이 오염돼 정책 비교가 무의미해진다."""
    _direction(client, uid, card)
    client.get(f"/cards?user_id={uid}&limit=3")
    before = copy.deepcopy(policy.sessions().get(uid))
    _upload(client, uid, card)
    client.post(f"/users/{uid}/surveys",
                json={"card_id": card, "visited": True, "satisfaction": "good",
                      "review": "좋았어요"})
    after = policy.sessions().get(uid)
    for name in ("legacy", "taste"):
        assert before[name]["n"] == after[name]["n"]
        assert before[name]["shown"] == after[name]["shown"]
        assert (before[name]["m"] == after[name]["m"]).all()
