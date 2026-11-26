# -*- coding: utf-8 -*-
"""사용자 후기 — 설문에 쓴 세 줄이 가게 화면에 뜬다.

설문은 추천이 맞았는지 재려고 만든 것이라 사용자가 쓴 글이 어디에도 보이지 않았다.
여기서 그 글을 가게에 붙인다. 잠그는 것 다섯:

1. **카카오 수집분을 고치지 않는다** — 우리 후기는 2026-09-21 부터 `store.reviews`
   **앞에 얹히지만**(`test_user_review_merge.py`), 뒤쪽 수집분은 내용도 순서도 그대로다.
2. **`store.score` 가 움직이지 않는다** — 추천 점수의 평판 성분이 읽는 값이라 후기
   한두 건이 콜드스타트에 순위를 흔든다. (`review_count` 는 섞은 만큼 늘어난다.)
3. **`user_id` 가 새어 나가지 않는다** — 이 서버에서 그 값은 그 사람의 열쇠다.
4. **안 가 본 가게의 후기는 안 뜬다.**
5. **재시작해도 남는다** — 저장소를 따로 두지 않고 `surveys.jsonl` 을 다시 읽는다.
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import state, user_reviews


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


def _user(client, name="후기쟁이"):
    return client.post("/users", json={"name": name}).json()["user_id"]


@pytest.fixture
def uid(client, request):
    """테스트마다 **다른 이름**으로 발급한다.

    후기를 이름으로 골라내는데 이름이 같으면 앞 테스트가 남긴 후기가 섞인다
    (같은 모듈에서 같은 가게에 계속 쓴다).
    """
    return _user(client, request.node.name[-24:])


@pytest.fixture
def card(client):
    return client.get("/cards?limit=1").json()[0]["card_id"]


REVIEW = {"taste": "고등어초회가 신선했어요", "price": "2인 4만원, 적당",
          "mood": "조용하고 좌석 간격이 넓어요"}


def _survey(client, uid, card, review=None, satisfaction="good", visited=True):
    client.post(f"/users/{uid}/directions",
                json={"card_id": card, "map_app": "kakao", "mode": "walk"})
    body = {"card_id": card, "visited": visited}
    if visited:
        body["satisfaction"] = satisfaction
        body["review"] = review if review is not None else dict(REVIEW)
    else:
        body["not_visited_reason"] = "no_time"
    r = client.post(f"/users/{uid}/surveys", json=body)
    assert r.status_code == 200, r.text
    return r


def _name(uid, client) -> str | None:
    return client.get(f"/users/{uid}").json()["name"]


def _detail(client, card) -> dict:
    r = client.get(f"/cards/{card}")
    assert r.status_code == 200
    return r.json()


# ────────────────────────────────────────────── 뜬다
def test_설문_후기가_가게_상세에_뜬다(client, uid, card):
    _survey(client, uid, card)
    got = _detail(client, card)["user_reviews"]
    assert got, "후기가 안 붙었다"
    mine = got[0]
    assert (mine["taste"], mine["price"], mine["mood"]) == \
           (REVIEW["taste"], REVIEW["price"], REVIEW["mood"])
    assert mine["satisfaction"] == "good"
    assert mine["name"] == _name(uid, client)
    assert mine["at"]


def test_이름을_안_준_사용자는_name_이_null(client, card):
    uid = client.post("/users", json={}).json()["user_id"]
    _survey(client, uid, card)
    mine = [r for r in _detail(client, card)["user_reviews"] if r["name"] is None]
    assert mine, "이름 없는 사용자의 후기가 없다"


def test_사진이_함께_붙는다(client, uid, card):
    client.post(f"/users/{uid}/directions",
                json={"card_id": card, "map_app": "kakao", "mode": "walk"})
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 120, 200)).save(buf, "JPEG")
    up = client.post(f"/users/{uid}/surveys/{card}/photos",
                     files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")})
    assert up.status_code == 201
    client.post(f"/users/{uid}/surveys",
                json={"card_id": card, "visited": True, "satisfaction": "good",
                      "review": dict(REVIEW)})
    mine = [r for r in _detail(client, card)["user_reviews"] if r["name"] == _name(uid, client)]
    assert mine and mine[0]["images"], "올린 사진이 후기에 안 붙었다"
    assert mine[0]["images"][0].startswith("/media/")
    client.delete(f"/users/{uid}/surveys/{card}/photos/{up.json()['photo_id']}")


def test_사진을_지우면_후기에서도_사라진다(client, uid, card):
    """사진의 주인은 `journeys` 다. 후기에 복사해 두면 지운 사진이 계속 보인다."""
    client.post(f"/users/{uid}/directions",
                json={"card_id": card, "map_app": "kakao", "mode": "walk"})
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 10, 10)).save(buf, "JPEG")
    pid = client.post(f"/users/{uid}/surveys/{card}/photos",
                      files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")}
                      ).json()["photo_id"]
    client.post(f"/users/{uid}/surveys",
                json={"card_id": card, "visited": True, "satisfaction": "good",
                      "review": dict(REVIEW)})
    client.delete(f"/users/{uid}/surveys/{card}/photos/{pid}")
    mine = [r for r in _detail(client, card)["user_reviews"] if r["name"] == _name(uid, client)]
    assert mine and mine[0]["images"] == []


# ────────────────────────────────────────────── 수집분을 고치지 않는다
def test_카카오_수집분은_손대지_않고_앞에만_얹는다(client, uid, card):
    """2026-09-21 부터 우리 후기는 `store.reviews` **앞에 얹힌다**(`test_user_review_merge.py`).

    얹는 것과 **고치는 것**은 다르다. 여기서 잠그는 것은 뒤쪽 — 카카오에서 받아 온 줄이
    하나도 바뀌지 않고, 순서도 그대로라는 것이다. 우리 글이 수집분 사이로 파고들면
    "왜 이 리뷰가 여기 있나" 를 설명할 수 없다.
    """
    before = [r for r in _detail(client, card)["store"]["reviews"]
              if r["source_platform"] != "matchuri"]
    _survey(client, uid, card)
    after = _detail(client, card)["store"]["reviews"]
    수집분 = [r for r in after if r["source_platform"] != "matchuri"]
    assert 수집분 == before, "수집한 리뷰 배열이 바뀌었다"
    # 우리 글은 **맨 앞에 몰려** 있다. 사이에 끼면 앞쪽 슬라이스로 가릴 수 없다.
    우리_것 = [r["source_platform"] == "matchuri" for r in after]
    assert 우리_것 == sorted(우리_것, reverse=True), "우리 후기가 수집분 사이로 파고들었다"
    # 같은 요청을 두 번 해도 같은 답이다(id·순서가 요청마다 흔들리지 않는다).
    assert client.get(f"/cards/{card}/reviews").json() == \
        client.get(f"/cards/{card}/reviews").json()


def test_별점을_바꾸지_않는다(client, uid, card):
    """추천 점수의 평판 성분이 `score` 를 읽는다 — 후기 한 건이 순위를 흔들면 안 된다.

    `review_count` 는 2026-09-21 부터 섞은 만큼 **늘어난다**(화면에 적히는 숫자일 뿐이고
    추천은 원본 데이터에서 직접 읽는다). 증가분은 `test_user_review_merge.py` 가 잰다.
    """
    before = _detail(client, card)["store"]
    _survey(client, uid, card)
    after = _detail(client, card)["store"]
    assert after["score"] == before["score"]


def test_목록에는_후기가_붙지_않는다(client, uid, card):
    """카드 수만큼 곱해지는 자리다 — 사진 주소까지 붙으면 응답이 금세 커진다."""
    _survey(client, uid, card)
    one = client.get("/cards?limit=3").json()[0]
    assert "user_reviews" not in one


def test_user_id_가_새어_나가지_않는다(client, uid, card):
    """`user_id` 를 쥐면 그 사람의 찜·취향·설문을 다 읽고 쓸 수 있다."""
    _survey(client, uid, card)
    blob = json.dumps(_detail(client, card), ensure_ascii=False)
    assert uid not in blob


# ────────────────────────────────────────────── 거르는 것
def test_방문하지_않은_설문은_후기가_되지_않는다(client, card):
    uid = _user(client, "안갔음")
    _survey(client, uid, card, visited=False)
    names = [r["name"] for r in _detail(client, card)["user_reviews"]]
    assert "안갔음" not in names


def test_옛_자유_문자열_후기는_싣지_않는다(client, card):
    """축이 없어서 화면 모양이 다르고, 그때는 '보인다' 는 약속 없이 받은 글이다."""
    assert user_reviews.apply({
        "user_id": "u_old", "store_id": "9999", "card_id": 1, "visited": True,
        "answers": {"review": "그냥 쓴 한 줄"}, "at": "2026-09-01T12:00:00+09:00",
    }) is False


def test_한_줄이라도_비면_싣지_않는다(client):
    assert user_reviews.apply({
        "user_id": "u_x", "store_id": "9999", "card_id": 1, "visited": True,
        "answers": {"review": {"taste": "맛", "price": "  ", "mood": "좋음"}},
        "at": "2026-09-01T12:00:00+09:00",
    }) is False


# ────────────────────────────────────────────── 표현력은 자격이 아니라 순서다
def _apply(review: dict, uid: str = "u_expr") -> bool:
    return user_reviews.apply({
        "user_id": uid, "store_id": "9999", "card_id": 1, "visited": True,
        "answers": {"satisfaction": "good", "review": review},
        "at": "2026-09-01T12:00:00+09:00",
    })


def test_평가만_있는_줄도_실린다(client):
    """L2 문턱이 있던 자리다. 그 문턱으로 운영 카드 2,795장 중 후기가 실린 카드가 1장이었다 —
    지우는 것이 아니라 뒤로 보내면 된다(`for_store` 가 표현력 순으로 세운다)."""
    assert _apply({"taste": "맛있어요", "price": "괜찮아요", "mood": "좋아요"}) is True
    assert _apply({**REVIEW, "taste": "맛있어요"}) is True
    assert _apply({**REVIEW, "price": "적당해요"}) is True
    assert _apply({**REVIEW, "mood": "좋았어요"}) is True


def test_속성을_말한_세_줄은_실린다(client):
    assert _apply(dict(REVIEW)) is True
    assert _apply({"taste": "국물이 걸쭉해서 계란이 퍽퍽함", "price": "18,000원, 강남 치고 저렴",
                   "mood": "시끄럽고 테이블이 붙어 있어요"}) is True


def test_줄은_자기_축에서_잰다(client):
    """맛 줄에 분위기 속성을 써도 **맛 축에서는** L0 이다 — 줄마다 묻는 것이 다르다.
    실리는 것과는 무관하고(실린다) 순서와 `review_levels` 에만 나타난다."""
    lv = user_reviews.levels_of("매장이 넓고 조용했어요", REVIEW["price"], REVIEW["mood"])
    assert lv["taste"] == 0
    assert _apply({**REVIEW, "taste": "매장이 넓고 조용했어요"}) is True


def test_짧은_후기도_실리고_표현력_순으로_뒤에_선다(client, card):
    """게이트가 있던 시절 A 는 조용히 사라졌다 — 응답도 200, 마이페이지에도 보여서
    아무도 몰랐다. 지금은 둘 다 실리고 구체적인 B 가 앞에 선다."""
    a, b = _user(client, "짧게쓴이"), _user(client, "길게쓴이")
    plain = {"taste": "아메리카노가 맛있어요", "price": "가격이 좀 비싸요",
             "mood": "분위기가 좋아요"}
    rich = {"taste": "라떼가 부드럽고 고소했어요", "price": "와플이 8000원인데 양이 많아요",
            "mood": "테라스가 넓고 조용해요"}
    got_a = _survey(client, a, card, review=plain).json()
    _survey(client, b, card, review=rich)

    assert got_a["published"] is True, "짧은 후기가 또 조용히 떨어졌다"
    assert got_a["review_levels"] == {"taste": 1, "price": 1, "mood": 1}

    names = [r["name"] for r in _detail(client, card)["user_reviews"]]
    assert "짧게쓴이" in names and "길게쓴이" in names
    assert names.index("길게쓴이") < names.index("짧게쓴이"), "표현력 순이 아니다"


def test_안_간_가게는_published_가_거짓이다(client, uid, card):
    """실리지 않았으면 응답이 그렇게 말한다. 200 만 돌려주면 앱도 우리도 알 수 없다."""
    got = _survey(client, uid, card, visited=False).json()
    assert got["published"] is False
    assert got["review_levels"] is None


def test_두_번째_응답은_기록도_게시도_되지_않는다(client, uid, card):
    """`recorded=false` 면 덮어쓰지 않으므로 실리지도 않는다 — 둘이 같은 말을 해야 한다."""
    assert _survey(client, uid, card).json()["recorded"] is True
    again = client.post(f"/users/{uid}/surveys",
                        json={"card_id": card, "visited": True, "satisfaction": "good",
                              "review": dict(REVIEW)}).json()
    assert again["recorded"] is False and again["published"] is False


def test_관리자_현황에_후기_수가_있다(client, uid, card):
    """세지 않는 것은 없는 것과 같다 — 이 숫자가 있었으면 후기 1건을 놓치지 않았다."""
    _survey(client, uid, card)
    s = client.get("/admin/api/stats").json()
    assert "user_reviews" in s
    assert s["user_reviews"] >= 1


# ────────────────────────────────────────────── 한 사람 한 가게 하나
def test_같은_사람이_다시_답하면_마지막_것만_남는다(client, uid, card):
    """사람당 가게당 하나다. 같은 사람의 후기가 두 줄 보이는 것보다 최신 하나가 맞다.

    API 로는 이 상황을 만들 수 없다 — `journeys` 가 같은 길찾기에 두 번 답하는 것을
    막는다(표본이 부푸는 것을 막는 그쪽 계약이다). 그래서 다시 길찾기하고 또 방문한
    경우를 `apply` 로 직접 만든다.
    """
    first = {**REVIEW, "taste": "첫번째 국물이 걸쭉했어요"}
    second = {**REVIEW, "taste": "두번째 면발이 쫄깃했어요"}
    _survey(client, uid, card, review=first)
    store_id = _detail(client, card)["store"]["id"]
    assert user_reviews.apply({
        "user_id": uid, "store_id": store_id, "card_id": card, "visited": True,
        "answers": {"satisfaction": "soso", "review": second},
        "at": "2026-09-30T12:00:00+09:00",
    }) is True

    mine = [r for r in _detail(client, card)["user_reviews"]
            if r["name"] == _name(uid, client)]
    assert len(mine) == 1, "같은 사람의 후기가 둘 보인다"
    assert mine[0]["taste"] == second["taste"]
    assert mine[0]["satisfaction"] == "soso"


def test_표현력이_높은_후기가_먼저_온다(client):
    """카카오 리뷰와 같은 잣대 — 문턱만 같고 순서가 다르면 같은 화면에서 잣대가 둘이 된다."""
    from app import user_reviews
    store = "s_expr_order"
    # 셋 다 L2 문턱은 넘는다. 셋째가 가장 구체적(L3 축이 있다), 첫째가 가장 덜하다.
    plain = {"taste": "고등어초회가 신선했어요", "price": "2인 4만원, 적당",
             "mood": "조용하고 좌석 간격이 넓어요"}
    rich = {"taste": "국물이 걸쭉하고 계란이 퍽퍽해서 다른 집보다 진했어요",
            "price": "2인 4만원인데 양이 많아서 가성비가 좋았어요",
            "mood": "조용하고 좌석 간격이 넓어서 옆 테이블 소리가 안 들려요"}
    rows = [("u_e1", plain, "2026-09-21T10:00:00+09:00"),     # 덜 구체적인데 가장 최근
            ("u_e2", rich, "2026-09-20T10:00:00+09:00"),      # 가장 구체적
            ("u_e3", plain, "2026-09-19T10:00:00+09:00")]     # 덜 구체적, 가장 오래됨
    for uid_, review, at in rows:
        assert user_reviews.apply({"user_id": uid_, "store_id": store, "card_id": None,
                                   "visited": True, "at": at,
                                   "answers": {"satisfaction": "good", "review": review}})
    got = user_reviews.for_store(store)
    assert [g["taste"] for g in got][0] == rich["taste"]           # 표현력이 먼저
    assert [g["at"] for g in got][1:] == [rows[0][2], rows[2][2]]  # 같으면 최근 순


def test_여러_사람의_후기가_최근_순으로_온다(client, card):
    a, b = _user(client, "먼저"), _user(client, "나중")
    _survey(client, a, card)
    _survey(client, b, card)
    names = [r["name"] for r in _detail(client, card)["user_reviews"]]
    assert names.index("나중") < names.index("먼저")


# ────────────────────────────────────────────── 복원
def test_재시작해도_후기가_남는다(client, uid, card):
    """저장소를 따로 두지 않는다 — `surveys.jsonl` 을 다시 읽어 되살린다.

    복원을 빠뜨리면 재시작 뒤에 가게 화면의 후기가 전부 사라지고, 사용자가 다시 쓸
    방법도 없다(같은 길찾기에 두 번 답할 수 없다).
    """
    _survey(client, uid, card)
    me = _name(uid, client)
    before = [r for r in _detail(client, card)["user_reviews"] if r["name"] == me]
    assert before

    user_reviews._by_store.clear()               # 재시작 흉내
    assert _detail(client, card)["user_reviews"] == []
    n = user_reviews.restore()

    assert n > 0
    # **내 후기만 비교한다.** 전체를 비교하면 파일을 거치지 않고 메모리에만 넣은
    # 다른 테스트의 항목이 섞인다(그건 복원되지 않는 것이 맞다).
    after = [r for r in _detail(client, card)["user_reviews"] if r["name"] == me]
    assert after == before


def test_복원이_파일만_보고도_같은_결과를_낸다(client, uid, card):
    """실시간 경로와 복원이 같은 함수(`apply`)를 쓰는지 확인한다.

    두 길이 갈라지면 '재시작 전에는 보이는데 재시작하면 사라지는 후기' 가 생긴다.
    """
    _survey(client, uid, card)
    live = _detail(client, card)["user_reviews"]
    user_reviews._by_store.clear()
    for row in state.read_surveys():
        user_reviews.apply(row)
    assert _detail(client, card)["user_reviews"] == live
