# -*- coding: utf-8 -*-
"""사용자 후기가 **카카오 리뷰 배열 안에** 실린다.

앱은 `user_reviews` 를 파싱하는 코드가 없고 가게 상세의 리뷰 섹션은 `store.reviews` 만
그린다. `FE/` 는 고칠 수 없으므로 서버가 앱이 이미 그리는 자리에 넣는다
(`app/user_reviews.py: as_store_reviews`). 잠그는 것 일곱:

1. **상세의 `store.reviews[0]` 이 우리 후기다** — 모양은 카카오 리뷰와 같고
   `source_platform`·id 대역으로 우리 글임을 가릴 수 있다
2. **FE 가 실제로 부르는 세션 경로도 같다** — `app/user_api.py` 가 상세 핸들러를 복사해
   다는 구조라 저절로 따라오지만, 그 구조가 바뀌면 여기서 깨져야 한다
3. **"리뷰 더보기"(`/cards/{id}/reviews`)에서도 안 사라진다** — 상세에서 본 글이
   더보기에서 없어지면 사용자에게는 글이 지워진 것과 구분되지 않는다
4. **`store.score` 는 불변, `review_count` 는 후기 수만큼 증가** — score 는 추천 점수의
   평판 성분이 읽는 값이고, count 는 화면에 적히는 숫자일 뿐이다
4-1. **`review_count` == 더보기 응답 길이** (2026-09-22) — 그 숫자는 "카카오에 달린 총
   리뷰 수" 가 아니라 **"여기서 볼 수 있는 리뷰 수"** 다. 수집 상한이 있어 2,795곳 중
   717곳에서 둘이 달랐다(최대 928 대 37) — 헤더가 928 이라고 적고 더보기가 37건을 주는
   화면이었다. 목록과 상세도 같은 값을 준다
5. **`user_reviews` 배열은 그대로 남는다** — 같은 응답에 두 자리 모두 있다
6. **목록에는 섞이지 않는다** — 카드 수만큼 곱해지는 자리다
7. **안 가 본 가게의 후기는 여기에도 안 뜬다** — 게이트는 한 곳(`_entry`)이다
"""
from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import user_reviews


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


def _user(client, name="섞임쟁이"):
    return client.post("/users", json={"name": name}).json()["user_id"]


@pytest.fixture
def uid(client, request):
    """테스트마다 **다른 이름**으로 발급한다.

    같은 모듈이 같은 가게에 계속 쓰기 때문에, 이름이 같으면 앞 테스트가 남긴 후기가
    섞여 `store.reviews[0]` 이 누구 것인지 말할 수 없게 된다
    (`tests/test_user_reviews.py` 와 같은 이유).
    """
    return _user(client, request.node.name[-24:])


@pytest.fixture
def card(client):
    return client.get("/cards?limit=1").json()[0]["card_id"]


REVIEW = {"taste": "고등어초회가 신선했어요", "price": "2인 4만원, 적당",
          "mood": "조용하고 좌석 간격이 넓어요"}

#: 카카오 리뷰의 `date` 모양. 섞이는 쪽이 ISO 8601 이면 앱의 날짜 파서가 우리 줄에서만 깨진다.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _survey(client, uid, card, review=None, satisfaction="good", visited=True,
            base="/users"):
    """설문 한 건. `base` 는 옛 경로(`/users`)와 실제 사용자 경로(`/user`) 중 하나다.

    세션 경로를 재는 테스트는 `POST /user` 로 만든 **실제 사용자**가 있어야 하고
    (`/users` 는 추천 세션 id 를 내주는 호환 경로다), 그 사용자에게는 설문도
    `/user/{uid}/surveys` 로 넣어야 같은 사람의 후기가 된다.
    """
    client.post(f"{base}/{uid}/directions",
                json={"card_id": card, "map_app": "kakao", "mode": "walk"})
    body = {"card_id": card, "visited": visited}
    if visited:
        body["satisfaction"] = satisfaction
        body["review"] = review if review is not None else dict(REVIEW)
    else:
        body["not_visited_reason"] = "no_time"
    r = client.post(f"{base}/{uid}/surveys", json=body)
    assert r.status_code == 200, r.text
    return r


def _mine(reviews: list[dict]) -> list[dict]:
    return [x for x in reviews if x["source_platform"] == "matchuri"]


def _detail(client, card) -> dict:
    r = client.get(f"/cards/{card}")
    assert r.status_code == 200, r.text
    return r.json()


def _person(client, name) -> str:
    """`POST /user` — FE 가 쓰는 **실제 사용자**. 세션은 이 아래에만 달린다."""
    r = client.post("/user", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["user_id"]


def _session(client, uid) -> str:
    r = client.post(f"/user/{uid}/session")
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _assert_우리_후기(row: dict):
    """`store.reviews` 한 줄이 우리 후기인가 — 필드 매핑 전부를 여기서 한 번에 잰다."""
    assert row["source_platform"] == "matchuri"
    # id 대역. 카카오는 양수, 네이버는 음수라 10^12 위는 우리 것뿐이다.
    assert row["id"] >= user_reviews.ID_BASE
    assert row["score"] == 5, "만족도 good 은 별점 5"
    lines = row["content"].split("\n")
    assert len(lines) == 3, f"세 줄이 줄바꿈으로 나뉘어야 한다: {row['content']!r}"
    assert lines[0].startswith("맛 · ") and REVIEW["taste"] in lines[0]
    assert lines[1].startswith("가격 · ") and REVIEW["price"] in lines[1]
    assert lines[2].startswith("분위기 · ") and REVIEW["mood"] in lines[2]
    # `user_id` 를 실을 수 있는 유일한 자리다. 이 서버에서 그 값은 그 사람의 열쇠다.
    assert row["reviewer_id"] is None
    assert DATE_RE.match(row["date"] or ""), f"카카오와 다른 날짜 모양: {row['date']!r}"


# ────────────────────────────────────────────── 앱이 그리는 자리에 있다
def test_상세의_첫_리뷰가_우리_후기다(client, uid, card):
    _survey(client, uid, card)
    got = _detail(client, card)["store"]["reviews"]
    assert got, "리뷰가 한 건도 없다"
    _assert_우리_후기(got[0])
    assert got[0]["store_id"] == _detail(client, card)["store"]["id"]


def test_세션_경로도_같다(client, card, request):
    """FE 가 실제로 부르는 경로다. `app/user_api.py` 가 상세 핸들러를 복사해 단다."""
    uid = _person(client, request.node.name[-24:])
    _survey(client, uid, card, base="/user")
    sid = _session(client, uid)
    r = client.get(f"/user/{uid}/session/{sid}/cards/{card}")
    assert r.status_code == 200, r.text
    got = r.json()["store"]["reviews"]
    assert got, "세션 경로 상세에 리뷰가 없다"
    _assert_우리_후기(got[0])


def test_더보기에서도_사라지지_않는다(client, uid, card):
    _survey(client, uid, card)
    r = client.get(f"/cards/{card}/reviews")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got, "더보기에 리뷰가 없다"
    _assert_우리_후기(got[0])
    assert _mine(got), "더보기 경로에서 우리 후기가 사라졌다"


# ────────────────────────────────────────────── 숫자
def test_score_는_그대로이고_review_count_만_늘어난다(client, uid, card):
    before = _detail(client, card)["store"]
    _survey(client, uid, card)
    after = _detail(client, card)["store"]
    # score 는 추천 점수의 평판 성분이 읽는 값이다 — 후기가 들어가도 움직이면 안 된다.
    assert after["score"] == before["score"]
    # 앞 테스트들이 같은 가게에 남긴 후기가 이미 있으므로 **증분**으로 잰다.
    늘어난_후기 = len(_mine(after["reviews"])) - len(_mine(before["reviews"]))
    assert 늘어난_후기 == 1, "이 테스트가 심은 후기 한 건이 안 보인다"
    assert after["review_count"] == before["review_count"] + 늘어난_후기


def test_review_count_는_더보기_길이와_정확히_같다(client, uid, card):
    """헤더에 적히는 수 == "리뷰 더보기" 가 실제로 주는 건수.

    앱은 이 숫자를 그대로 그린다(`리뷰 {store.reviewCount}`). 예전에는 수집 원본에 적힌
    카카오 총수를 내보내서, 928 이라고 적고 더보기를 누르면 37건이 나오는 가게가 있었다.
    """
    _survey(client, uid, card)
    for cid in [row["card_id"] for row in client.get("/cards?limit=5").json()]:
        detail = _detail(client, cid)["store"]
        전부 = client.get(f"/cards/{cid}/reviews").json()
        assert detail["review_count"] == len(전부),             f"카드 {cid}: 적힌 수 {detail['review_count']} vs 더보기 {len(전부)}건"


def test_수집_상한에_걸린_가게에서_카카오_총수를_쓰지_않는다(client, uid, card):
    """수집 원본의 `review_count`(카카오가 자기 화면에 적어 둔 총수)와 갈라지는 자리.

    2,795곳 중 717곳이 여기 해당한다. 한 곳도 없으면 이 테스트는 잴 것이 없으므로 건너뛴다
    (테스트용 축소 데이터에서 그럴 수 있다).
    """
    from recsys import store as store_svc
    갈라진_곳 = None
    for cid in [row["card_id"] for row in client.get("/cards?limit=30").json()]:
        store = _detail(client, cid)["store"]
        카카오_총수 = (store_svc.get_store(store["id"]) or {}).get("review_count") or 0
        보유 = len(client.get(f"/cards/{cid}/reviews").json())
        if 카카오_총수 > 보유:
            갈라진_곳 = (cid, 카카오_총수, 보유, store["review_count"])
            break
    if 갈라진_곳 is None:
        pytest.skip("수집 상한에 걸린 가게가 이 데이터에 없다")
    cid, 카카오_총수, 보유, 적힌수 = 갈라진_곳
    assert 적힌수 == 보유, f"카드 {cid}: 카카오 총수 {카카오_총수} 를 그대로 내보냈다"


def test_목록과_상세의_review_count_가_같다(client, uid, card):
    """목록은 후기 **본문**을 안 싣지만 **개수는 센다.**

    본문은 카드 수만큼 곱해지지만 개수는 리스트 길이 하나라 공짜고, 두 화면이 같은 가게에
    다른 숫자를 적으면 그건 그냥 틀린 숫자다.
    """
    _survey(client, uid, card)
    목록 = {row["card_id"]: row["store"]["review_count"]
          for row in client.get("/cards?limit=5").json()}
    for cid, 목록수 in 목록.items():
        assert _detail(client, cid)["store"]["review_count"] == 목록수,             f"카드 {cid}: 목록 {목록수} vs 상세가 다르다"
    assert 목록[card] >= 1, "후기를 심은 가게인데 목록 숫자가 그것도 못 센다"


# ────────────────────────────────────────────── 두 자리 모두 남는다
def test_user_reviews_배열도_그대로_있다(client, uid, card):
    _survey(client, uid, card)
    body = _detail(client, card)
    assert body["user_reviews"], "`user_reviews` 가 사라졌다"
    assert body["user_reviews"][0]["taste"] == REVIEW["taste"]
    assert any(x["source_platform"] == "matchuri" for x in body["store"]["reviews"])


# ────────────────────────────────────────────── 섞이지 않아야 할 곳
def test_목록에는_섞이지_않는다(client, uid, card):
    """목록은 카드 수만큼 곱해지는 자리다."""
    _survey(client, uid, card)
    for row in client.get("/cards?limit=20").json():
        for rv in row["store"]["reviews"]:
            assert rv["source_platform"] != "matchuri", f"목록 카드 {row['card_id']} 에 섞였다"
            assert rv["id"] < user_reviews.ID_BASE


def test_안_가_본_가게의_후기는_안_실린다(client, uid, card):
    """게이트는 `_entry` 한 곳이다 — 새 경로가 그걸 우회하지 않는지 잠근다."""
    before = _detail(client, card)["store"]["reviews"]
    _survey(client, uid, card, review=dict(REVIEW), visited=False)
    after = _detail(client, card)["store"]["reviews"]
    새것 = [x for x in after if x not in before and x["source_platform"] == "matchuri"]
    assert not 새것, f"안 가 본 가게의 후기가 실렸다: {새것}"


# ────────────────────────────────────────────── 사진 확대가 우리 후기에서도 돈다
def test_사진_원본_주소가_썸네일과_글자_그대로_같다(client, uid, card):
    """앱의 사진 확대 경로가 우리 후기에서도 도는지.

    앱은 미리보기에서 받은 주소를 쥐고 `original_images=true` 로 상세를 다시 부른 뒤
    **같은 `id` 의 리뷰**를 찾아, 찾은 주소가 그 썸네일과 맞는지 본다
    (`FE/app/lib/services/original_card_images.dart` 의 `_resolve`·`_samePhoto`).
    맞다고 인정하는 갈래는 둘뿐이다 — 주소가 같거나, 썸네일이 `/media/catalog/...`
    이고 파일명이 `sha256(원본).jpg` 이거나.

    **우리 사진은 두 번째 갈래 밖이다**(`/media/<해시앞2>/<해시>.jpg`). 그러니 원본
    요청에 조금이라도 다른 URL 을 내보내면 앱에서 "이 사진의 원본을 찾을 수 없습니다"
    가 뜬다 — 그래서 `loader._user_review` 는 `catalog_media` 를 태우지 않는다.
    id 가 요청마다 달라져도 같은 자리에서 깨지므로 그것도 여기서 잠근다.
    """
    client.post(f"/users/{uid}/directions",
                json={"card_id": card, "map_app": "kakao", "mode": "walk"})
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 120, 200)).save(buf, "JPEG")
    up = client.post(f"/users/{uid}/surveys/{card}/photos",
                     files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")})
    assert up.status_code == 201, up.text
    _survey(client, uid, card)

    mine = _mine(_detail(client, card)["store"]["reviews"])
    assert mine and mine[0]["images"], "올린 사진이 섞인 리뷰에 안 붙었다"
    rid, thumb = mine[0]["id"], mine[0]["images"][0]
    assert not thumb.startswith("/media/catalog/"), \
        "우리 사진이 카탈로그 경로로 나간다 — 그러면 앱의 sha256 갈래와 규칙이 엉킨다"

    # ① 사진을 누르면 상세를 original_images=true 로 다시 부른다
    orig = client.get(f"/cards/{card}?original_images=true").json()
    같은것 = [x for x in orig["store"]["reviews"] if x["id"] == rid]
    assert 같은것, "원본 요청에 같은 id 의 리뷰가 없다 — 앱이 원본을 못 고른다"
    assert 같은것[0]["images"][0] == thumb, "원본 주소가 썸네일과 다르다"

    # ② '리뷰 더보기' 에서 누른 사진은 /cards/{id}/reviews 로 간다
    full = client.get(f"/cards/{card}/reviews?original_images=true").json()
    같은것2 = [x for x in full if x["id"] == rid]
    assert 같은것2, "더보기 응답에 같은 id 의 리뷰가 없다"
    assert 같은것2[0]["images"][0] == thumb, "더보기 쪽 원본 주소가 썸네일과 다르다"

    client.delete(f"/users/{uid}/surveys/{card}/photos/{up.json()['photo_id']}")
