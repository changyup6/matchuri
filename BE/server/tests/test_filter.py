# -*- coding: utf-8 -*-
"""필터·거리·사용자 위치 — 후보 선택 단계에서 걸리는지, 학습이 그대로인지.

두 층을 따로 잰다.

  ① 합성 풀 위의 **선택** — 허용 집합 밖 후보는 fallback 으로도 안 나오고, 빈 집합이면
     빈 목록이며 세션(`shown`·학습)이 하나도 안 움직인다. 필터가 응답 필터링이라면
     걸러진 카드가 `shown` 에 남아 영영 다시 안 나오므로, 그게 아님을 여기서 잠근다.
  ② 실데이터 위의 **HTTP 계약** — `cat`/`tag`/`within_m` 가 실제 배치를 좁히는지,
     `distance_m` 값이 맞는지, 위치의 우선순위(쿼리 > 저장)와 영속·복원.
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi import HTTPException

from app import filters, geo
from recsys import policy, swipe, taste

# 합성 가게 6곳의 좌표 — 기준점에서 정확히 0·200·400·600·800·1000m 북쪽에 둔다.
# 거리 필터의 경계를 '어림' 이 아니라 값으로 잴 수 있어야 한다.
BASE = (37.5, 127.0)
DEG_LAT_M = 111_195.0                      # 위도 1도의 길이 (m)
COORDS = {f"s{i}": (BASE[0] + i * 200 / DEG_LAT_M, BASE[1]) for i in range(6)}
# s5 의 cat2(치킨)는 어휘의 상위 8 밖이라 `기타` 로 묶인다 — 그 규칙을 잴 자리가 필요하다.
CAT2 = {"s0": "한식", "s1": "한식", "s2": "일식", "s3": "일식", "s4": "카페", "s5": "치킨"}
# 원본 태그는 동의어로 흩어져 있다: s0 은 `친절한`, s1 은 `친절`, s2 는 `회식장소`(단체모임),
# s3 은 `감성적`(분위기)만 갖는다. 묶음이 실제로 도는지는 이렇게 갈라 놔야 잴 수 있다.
TAGS = {"s0": ["혼밥", "가성비"], "s1": ["가성비"], "s2": ["혼밥"],
        "s3": [], "s4": ["가성비"], "s5": ["혼밥", "가성비"]}
REVIEW_TAGS = {"s0": {"친절한": 3}, "s1": {"친절": 1, "혼밥": 2}, "s2": {"회식장소": 1},
               "s3": {"감성적": 2, "가성비": 1}, "s4": {}, "s5": {"분위기": 1}}


@pytest.fixture
def pool6(art, monkeypatch):
    """합성 풀 위의 정책 라우터 + 합성 가게 테이블(업종·태그·좌표).

    legacy 쪽 준비는 `tests/test_policy.py: router` 와 같다 — 특성 자리를 실제 배치대로
    잡아야 `learn_state` 가 이름으로 읽는 칸이 어긋나지 않는다.
    """
    monkeypatch.setenv(policy.ENV, "interleave")
    monkeypatch.setattr(policy, "_warned", False)
    policy._sessions.clear()
    swipe._sessions.clear()
    taste._sessions.clear()
    n = len(art.ids)
    cats = [CAT2[s] for s in art.ids]
    d = 3 + len(swipe.CATS) + 1 + 5
    X = np.zeros((n, d))
    X[:, 0] = 1.0
    X[:, 1] = art.quality
    X[:, 2] = art.pop / 6.0
    for i, c in enumerate(cats):
        X[i, 3 + swipe.CATS.index(c)] = 1.0
        X[i, 3 + len(swipe.CATS) + 1 + (i % 4)] = 1.0
    monkeypatch.setattr(swipe, "pool", lambda: {
        "ids": list(art.ids), "pos": dict(art.pos), "cat": dict(zip(art.ids, cats)),
        "qz": np.array(art.quality), "X": X})
    monkeypatch.setattr(swipe, "_new_session", lambda: {
        "m": np.zeros(d), "v": np.full(d, 1.0), "shown": set(), "last_cats": [],
        "n": 0, "likes": 0, "pending": None, "rng": np.random.default_rng(1),
        "cat_stats": {}, "hist": [], "created": 0.0})
    # 가게 테이블도 합성으로 — 필터는 `recsys.store` 를 읽는다.
    monkeypatch.setattr(filters.svc, "all_store_ids", lambda: list(art.ids))
    monkeypatch.setattr(filters.svc, "get_store", lambda sid: None if sid not in CAT2 else {
        "store_id": sid, "name": sid, "cat2": CAT2[sid], "tags": list(TAGS[sid]),
        "review_tags": dict(REVIEW_TAGS[sid]),
        "lat": COORDS[sid][0], "lon": COORDS[sid][1],
        "kakao_review_count": 10, "kakao_avg_score": 4.5})
    # 좌표 캐시는 실데이터의 mtime 으로 키가 잡히므로 조회 자체를 갈아끼운다
    # (합성 좌표가 캐시에 남아 뒤 테스트의 실데이터 거리를 오염시키면 안 된다).
    monkeypatch.setattr(geo, "coords", lambda: dict(COORDS))
    return policy


# ────────────────────────────────────────────────── ① 후보 선택
@pytest.mark.parametrize("mode", ["legacy", "taste", "interleave"])
def test_허용_집합_밖은_뽑히지_않는다(pool6, monkeypatch, mode):
    monkeypatch.setenv(policy.ENV, mode)
    allow = {"s2", "s3"}
    sess = policy.session_for(f"u_allow_{mode}")
    got = policy.pick_many(sess, 6, allow=allow)
    assert got and set(got) <= allow


def test_인터리빙에서도_두_정책이_같은_집합만_본다(pool6):
    sess = policy.session_for("u_inter")
    tags: dict[str, str] = {}
    allow = {"s0", "s2", "s4"}
    got = policy.pick_many(sess, 3, tags=tags, allow=allow)
    assert set(got) == allow                      # 셋이면 셋을 다 쓴다(중복 없음)
    assert set(tags.values()) <= {"legacy", "taste"}
    assert len(tags) == 3


def test_fallback_도_허용_집합_안에서만_고른다(pool6):
    """다양성 규칙을 어겨야 할 때(fallback) 조차 집합 밖으로 나가지 않는다.

    '같은 업종 3연속 금지' 가 걸린 상태에서 허용 집합에 그 업종만 남기면 두 정책은 규칙을
    어기고라도 한 장을 준다(fallback). 그때 집합 밖 가게를 집어 오면 필터가 깨진다.
    """
    for name, mod in (("legacy", swipe), ("taste", taste)):
        sess = policy.session_for(f"u_fb_{name}")
        one = sess[name]
        one["last_cats"] = ["일식", "일식"]        # 3연속 금지가 발동한 상태
        assert mod.pick_many(one, 1)[0] not in ("s2", "s3")   # 평소엔 규칙을 지킨다

        other = policy.session_for(f"u_fb2_{name}")[name]
        other["last_cats"] = ["일식", "일식"]
        got = mod.pick_many(other, 2, allow={"s2", "s3"})     # 허용 집합이 전부 일식
        assert set(got) == {"s2", "s3"}


def test_빈_허용_집합이면_빈_목록이고_세션이_그대로다(pool6):
    sess = policy.session_for("u_empty")
    before = ({s for s in sess["legacy"]["shown"]}, {s for s in sess["taste"]["shown"]},
              sess["legacy"]["n"], sess["taste"]["n"])
    assert policy.pick_many(sess, 10, allow=set()) == []
    assert (sess["legacy"]["shown"], sess["taste"]["shown"],
            sess["legacy"]["n"], sess["taste"]["n"]) == before
    assert sess["legacy"]["last_cats"] == [] and sess["taste"]["last_cats"] == []


def test_필터로_걸러진_카드는_다시_나온다(pool6):
    """걸러진 후보는 '보여준 것' 이 아니다 — 필터를 풀면 그대로 다시 후보가 된다."""
    sess = policy.session_for("u_reappear")
    got = policy.pick_many(sess, 2, allow={"s5"})
    assert got == ["s5"]
    rest = policy.pick_many(sess, 5)
    assert set(rest) == {"s0", "s1", "s2", "s3", "s4"}


def test_필터는_학습을_건드리지_않는다(pool6):
    """필터로 좁혀 뽑은 카드도 평소와 똑같이 학습된다(`record` 는 필터를 모른다)."""
    sess = policy.session_for("u_learn")
    got = policy.pick_many(sess, 1, allow={"s2"})
    m_before = sess["taste"]["m"].copy()
    policy.record(sess, got[0], True)
    assert sess["legacy"]["n"] == 1 and sess["taste"]["n"] == 1
    assert sess["taste"]["like_ids"] == ["s2"]
    assert not np.allclose(sess["taste"]["m"], m_before)


# ────────────────────────────────────────────────── ② 허용 집합 조립
def test_업종도_태그도_OR_이고_차원_사이는_AND(pool6):
    """칩을 더 누르면 넓어진다(OR). 좁히는 것은 **다른 차원을 겹칠 때**뿐이다."""
    assert filters.allow_ids(cats=["일식"], tags=[], within_m=None,
                             lat=None, lon=None) == {"s2", "s3"}
    assert filters.allow_ids(cats=["일식", "카페"], tags=[], within_m=None,
                             lat=None, lon=None) == {"s2", "s3", "s4"}

    # 태그 하나: s0·s2·s5 는 가게 태그로, s1 은 리뷰 태그로 `혼밥` 이다
    only_honbap = filters.allow_ids(cats=[], tags=["혼밥"], within_m=None,
                                    lat=None, lon=None)
    assert only_honbap == {"s0", "s1", "s2", "s5"}

    # 태그 둘이면 **합집합**이다 — s2 는 `단체모임`(회식장소), s3·s5 는 `분위기`(감성적·분위기).
    # 예전처럼 AND 였다면 셋 다 상대 태그가 없어 **빈 집합**이 나왔을 자리다.
    assert filters.allow_ids(cats=[], tags=["단체모임", "분위기"], within_m=None,
                             lat=None, lon=None) == {"s2", "s3", "s5"}
    assert filters.allow_ids(cats=[], tags=["단체모임"], within_m=None,
                             lat=None, lon=None) == {"s2"}

    # 고를수록 넓어진다 — 하나일 때의 집합을 항상 포함한다
    assert only_honbap <= filters.allow_ids(cats=[], tags=["혼밥", "단체모임"],
                                            within_m=None, lat=None, lon=None)

    # 업종과 태그는 **다른 차원**이라 함께 주면 교집합이다
    assert filters.allow_ids(cats=["한식"], tags=["혼밥"], within_m=None,
                             lat=None, lon=None) == {"s0", "s1"}
    assert filters.allow_ids(cats=["일식"], tags=["분위기"], within_m=None,
                             lat=None, lon=None) == {"s3"}


def test_상위_8_밖의_업종은_기타로_묶인다(pool6):
    assert filters.category_group("한식") == "한식"
    assert filters.category_group("치킨") == "기타"      # 어휘의 상위 8 밖
    assert filters.category_group(None) == "기타"        # cat2 가 없는 가게도 기타
    assert filters.allow_ids(cats=["기타"], tags=[], within_m=None,
                             lat=None, lon=None) == {"s5"}
    # 어휘 안이지만 그런 가게가 없으면 빈 집합이다(에러가 아니다)
    assert filters.allow_ids(cats=["술집"], tags=[], within_m=None,
                             lat=None, lon=None) == set()


def test_뜻이_같은_원본_태그는_한_칸이다(pool6):
    """`친절한`(가게 태그)과 `친절`(리뷰 태그)은 사용자에게 같은 태그 하나다."""
    assert filters.client_tags({"tags": ["친절한"], "review_tags": {}}) == {"친절"}
    assert filters.allow_ids(cats=[], tags=["친절"], within_m=None,
                             lat=None, lon=None) == {"s0", "s1"}
    # s2 는 `회식장소` 만, s3 은 `감성적` 만 갖는다
    assert filters.allow_ids(cats=[], tags=["단체모임"], within_m=None,
                             lat=None, lon=None) == {"s2"}
    assert filters.allow_ids(cats=[], tags=["분위기"], within_m=None,
                             lat=None, lon=None) == {"s3", "s5"}


def test_필터가_없으면_None_이다(pool6):
    assert filters.allow_ids(cats=[], tags=[], within_m=None,
                             lat=None, lon=None) is None


def test_어휘_밖의_값은_422_다(pool6):
    """오타를 빈 목록으로 뭉개면 '그런 가게가 없다' 와 구분되지 않는다."""
    for bad_cats, bad_tags, word in ([["초밥"], [], "cat"], [[], ["맛"], "tag"]):
        with pytest.raises(HTTPException) as e:
            filters.validate(bad_cats, bad_tags)
        assert e.value.status_code == 422
        assert word in e.value.detail                     # 어느 쿼리가 틀렸는지
        assert (bad_cats + bad_tags)[0] in e.value.detail  # 틀린 값 자체
        assert "허용" in e.value.detail                    # 쓸 수 있는 값 목록
    filters.validate(filters.CAT_CHOICES, list(filters.TAGS))   # 어휘 전부는 통과
    assert filters.split("일식, ,한식") == ["일식", "한식"]
    assert filters.split(None) == [] and filters.split("") == []


def test_거리_필터는_반경_안쪽만_남긴다(pool6):
    near = filters.allow_ids(cats=[], tags=[], within_m=500,
                             lat=BASE[0], lon=BASE[1])
    assert near == {"s0", "s1", "s2"}             # 0·200·400m
    both = filters.allow_ids(cats=["일식"], tags=[], within_m=500,
                             lat=BASE[0], lon=BASE[1])
    assert both == {"s2"}                         # 일식(s2·s3) ∩ 500m 안(s2)


def test_하버사인은_남북_동서를_다르게_잰다():
    """평면 근사와 갈리는 지점 — 같은 0.001도라도 동서가 짧다(cos 37.5° ≈ 0.79)."""
    ns = geo.haversine_m(37.5, 127.0, 37.501, 127.0)
    ew = geo.haversine_m(37.5, 127.0, 37.5, 127.001)
    assert 111.0 < ns < 111.5
    assert 88.0 < ew < 88.7
    assert geo.haversine_m(37.5, 127.0, 37.5, 127.0) == 0.0


def test_좌표를_모르는_가게는_거리가_없다(pool6, monkeypatch):
    monkeypatch.setattr(geo, "coords", lambda: {"s0": COORDS["s0"]})
    assert geo.distance_m("s0", *BASE) == 0
    assert geo.distance_m("s1", *BASE) is None          # 좌표 없음
    assert geo.distance_m("s0", None, None) is None     # 위치 없음
    # 좌표 없는 가게는 반경 안에도 넣지 않는다(모르는 것을 통과시키지 않는다)
    assert geo.within(*BASE, 10_000) == {"s0"}


# ────────────────────────────────────────────────── ③ HTTP (실데이터)
# 설정은 사용자에 붙는다 — `GET /cards` 에는 필터 쿼리가 없다. 순서는 셋이다:
# `GET /filters`(어휘) → `PUT /users/{id}/filter`·`/location`(설정) → `GET /cards?user_id=`.
YEOKSAM = (37.5006, 127.0366)          # 역삼역


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def _store_of(card_id: int) -> dict:
    from app import loader
    from recsys import store as svc
    return svc.get_store(loader.to_store_id(card_id)) or {}


def _tags_of(card_id: int) -> set[str]:
    return filters.client_tags(_store_of(card_id))


def _user(client, *, location=None, filter=None) -> str:
    """사용자 하나를 만들고 설정(위치·필터)을 **PUT 으로** 걸어 둔다."""
    uid = client.post("/users").json()["user_id"]
    if location:
        r = client.put(f"/users/{uid}/location",
                       json={"lat": location[0], "lon": location[1]})
        assert r.status_code == 200
    if filter is not None:
        r = client.put(f"/users/{uid}/filter", json=filter)
        assert r.status_code == 200
    return uid


def test_filters_어휘(client):
    """어휘는 서버가 정한다 — 목록·순서는 고정이고 개수만 데이터에서 온다."""
    body = client.get("/filters").json()
    assert body["within_m"] == [300, 500, 1000, 2000]

    cats = body["cats"]
    assert [c["name"] for c in cats] == filters.CAT_CHOICES    # 9개, 순서까지
    assert cats[0]["name"] == "한식" and cats[-1]["name"] == "기타"
    assert all(c["count"] > 0 for c in cats)
    head = [c["count"] for c in cats[:-1]]                     # 상위 8 은 많은 순
    assert head == sorted(head, reverse=True)

    tags = body["tags"]
    assert [t["name"] for t in tags] == list(filters.TAGS)     # 12개, 순서까지
    assert all(t["count"] > 0 for t in tags)

    pool_n = len(swipe.pool()["ids"])
    # 모든 가게가 9칸 중 정확히 한 칸에 들어간다 — 합이 풀 크기와 같아야 한다
    assert sum(c["count"] for c in cats) == pool_n
    assert all(t["count"] <= pool_n for t in tags)


def test_저장된_업종_필터를_두_정책이_모두_지킨다(client):
    uid = _user(client, filter={"cat": ["일식"]})
    cards = client.get(f"/cards?user_id={uid}&limit=10").json()
    assert cards
    for c in cards:
        assert c["store"]["category_group"] == "일식"
        assert _store_of(c["card_id"])["cat2"] == "일식"
        assert c["distance_m"] is None                 # 저장된 위치가 없다
    kinds = {c["policy"] for c in cards}
    assert kinds <= {"legacy", "taste"} and kinds
    if policy.active() == "interleave":
        assert kinds == {"legacy", "taste"}            # 두 정책이 같은 집합만 봤다


def test_저장된_태그_필터는_OR_다(client):
    """실데이터로도 OR 인가 — 카드마다 **하나라도** 맞으면 되고, 둘 다는 아니어도 된다."""
    want = {"단체모임", "분위기"}
    uid = _user(client, filter={"tag": sorted(want)})
    cards = client.get(f"/cards?user_id={uid}&limit=10").json()
    assert cards
    for c in cards:
        assert want & _tags_of(c["card_id"])           # 하나라도 있으면 통과

    # 한 가지만 건 필터의 결과가 이 안에 들어 있어야 '넓어진다' 가 참이다.
    uid2 = _user(client, filter={"tag": ["단체모임"]})
    one = client.get(f"/cards?user_id={uid2}&limit=10").json()
    assert one
    for c in one:
        assert "단체모임" in _tags_of(c["card_id"])


def test_저장된_반경과_distance_m(client):
    lat, lon = YEOKSAM
    uid = _user(client, location=YEOKSAM, filter={"within_m": 300})
    cards = client.get(f"/cards?user_id={uid}&limit=10").json()
    assert cards
    for c in cards:
        assert c["distance_m"] <= 300
        s = _store_of(c["card_id"])
        assert c["distance_m"] == round(
            geo.haversine_m(lat, lon, s["lat"], s["lon"]))


def test_필터가_촘촘하면_적게_온다(client):
    """`limit` 을 못 채우는 것은 정상이다 — 필터를 무시하고 채우지 않는다."""
    uid = _user(client, location=YEOKSAM,
                filter={"cat": ["중식"], "tag": ["혼밥"], "within_m": 200})
    cards = client.get(f"/cards?user_id={uid}&limit=20").json()
    assert len(cards) < 20
    for c in cards:
        assert _store_of(c["card_id"])["cat2"] == "중식"
        assert "혼밥" in _tags_of(c["card_id"]) and c["distance_m"] <= 200


def test_기타_업종과_category_group(client):
    """`기타` 는 상위 8 밖 전부다. 카드의 `store.category_group` 이 같은 라벨이다."""
    uid = _user(client, filter={"cat": ["기타"]})
    cards = client.get(f"/cards?user_id={uid}&limit=5").json()
    assert cards
    for c in cards:
        assert c["store"]["category_group"] == "기타"
        assert _store_of(c["card_id"]).get("cat2") not in filters.CATS
    other = _user(client, filter={"cat": ["한식"]})
    one = client.get(f"/cards?user_id={other}&limit=3").json()
    assert one and all(c["store"]["category_group"] == "한식" for c in one)


def test_필터_저장이_어휘를_검증한다(client):
    """어휘 밖 값은 **저장할 때** 막힌다 — `GET /cards` 는 검증된 값만 본다."""
    uid = client.post("/users").json()["user_id"]
    r = client.put(f"/users/{uid}/filter", json={"cat": ["초밥"]})
    assert r.status_code == 422
    assert "초밥" in r.json()["detail"] and "한식" in r.json()["detail"]
    r = client.put(f"/users/{uid}/filter", json={"tag": ["맛"]})   # 리뷰 태그 원본 이름
    assert r.status_code == 422 and "맛" in r.json()["detail"]
    r = client.put(f"/users/{uid}/filter", json={"within_m": 0})   # ge=1
    assert r.status_code == 422
    assert client.get(f"/users/{uid}").json()["filter"] is None    # 저장되지 않았다


def test_카드_쿼리로는_필터를_걸_수_없다(client):
    """필터 쿼리를 없앴다 — 모르는 쿼리는 FastAPI 가 버리고 저장된 설정만 남는다."""
    uid = _user(client, filter={"cat": ["일식"]})
    cards = client.get(f"/cards?user_id={uid}&cat=카페&limit=5").json()
    assert cards and all(c["store"]["category_group"] == "일식" for c in cards)
    # 어휘 밖 값을 쿼리에 실어도 422 가 아니다(그 쿼리는 아예 읽지 않는다)
    r = client.get(f"/cards?user_id={uid}&cat=초밥&tag=맛&within_m=1&limit=3")
    assert r.status_code == 200
    assert all(c["store"]["category_group"] == "일식" for c in r.json())


def test_필터를_지우면_다시_전체에서_추천한다(client):
    lat, lon = YEOKSAM
    uid = _user(client, location=YEOKSAM,
                filter={"cat": ["중식"], "tag": ["술한잔"], "within_m": 1})
    assert client.get(f"/cards?user_id={uid}&limit=5").json() == []   # 아무것도 안 걸린다
    rec = client.delete(f"/users/{uid}/filter").json()
    assert rec["filter"] is None
    cards = client.get(f"/cards?user_id={uid}&limit=5").json()
    assert len(cards) == 5                        # 필터가 사라졌다
    assert all(c["distance_m"] is not None for c in cards)   # 위치는 그대로 남아 있다
    # 없는 것을 지우는 것은 실패가 아니다
    again = client.delete(f"/users/{uid}/filter")
    assert again.status_code == 200 and again.json()["filter"] is None


def test_저장된_반경인데_위치가_없으면_400(client):
    uid = _user(client, filter={"within_m": 300})
    r = client.get(f"/cards?user_id={uid}&limit=5")
    assert r.status_code == 400
    assert "저장된 필터" in r.json()["detail"] and "location" in r.json()["detail"]
    # 위치를 저장하면 같은 요청이 그대로 돈다
    client.put(f"/users/{uid}/location", json={"lat": YEOKSAM[0], "lon": YEOKSAM[1]})
    cards = client.get(f"/cards?user_id={uid}&limit=5").json()
    assert cards and all(c["distance_m"] <= 300 for c in cards)


def test_빈_필터는_저장_안_된_것과_같다(client):
    from app import users as registry
    uid = _user(client, filter={"cat": [], "tag": [], "within_m": None})
    assert client.get(f"/users/{uid}").json()["filter"]["cat"] == []
    assert registry.filter_of(uid) is None        # 조건이 하나도 없다
    assert len(client.get(f"/cards?user_id={uid}&limit=3").json()) == 3


def test_user_id_가_없으면_필터도_위치도_없다(client):
    """설정은 사용자에 붙는다 — 익명 호출에는 걸릴 설정이 없다(예전 인기순)."""
    cards = client.get("/cards?limit=5").json()
    assert len(cards) == 5
    for c in cards:
        assert c["policy"] is None and c["distance_m"] is None
        assert c["store"]["category_group"] in filters.CAT_CHOICES
        assert c["recommendation_reason"]


def test_카드_상세는_저장된_위치로_거리를_준다(client):
    lat, lon = YEOKSAM
    uid = _user(client, location=YEOKSAM)
    cid = client.get("/cards?limit=1").json()[0]["card_id"]
    assert client.get(f"/cards/{cid}").json()["distance_m"] is None
    detail = client.get(f"/cards/{cid}?user_id={uid}").json()
    s = _store_of(cid)
    assert detail["distance_m"] == round(
        geo.haversine_m(lat, lon, s["lat"], s["lon"]))
    assert "menus" in detail and "store" in detail             # 기존 계약
    assert detail["store"]["category_group"] in filters.CAT_CHOICES


def test_위치_응답과_검증(client):
    # 등록하지 않은 id 도 받는다 — 이름 없는 레코드를 만든다
    rec = client.put("/users/anon_filter_test/location",
                     json={"lat": 37.5, "lon": 127.0}).json()
    assert rec["user_id"] == "anon_filter_test" and rec["name"] is None
    assert rec["location"] == {"lat": 37.5, "lon": 127.0, "at": rec["location"]["at"]}
    got = client.get("/users/anon_filter_test").json()
    assert got["location"]["lat"] == 37.5 and got["registered"] is True
    # 필터도 미등록 id 에 걸 수 있다
    rec = client.put("/users/anon_filter_test2/filter", json={"cat": ["카페"]}).json()
    assert rec["name"] is None and rec["filter"]["cat"] == ["카페"]
    # 발급 직후에는 위치도 필터도 null 이다
    fresh = client.post("/users").json()
    assert fresh["location"] is None and fresh["filter"] is None
    for bad in ({"lat": 91.0, "lon": 127.0}, {"lat": 37.5, "lon": 181.0},
                {"lat": 37.5}, {"lat": "여기", "lon": 127.0}):
        assert client.put("/users/anon_filter_test/location",
                          json=bad).status_code == 422


def test_설정은_파일에_남고_재시작하면_복원된다(client):
    """`state/users.jsonl` 에 **병합된 전체 레코드**가 쌓이고, 복원은 마지막 줄이 이긴다."""
    import json

    from app import state
    from app import users as registry

    uid = client.post("/users", json={"name": "설정테스트"}).json()["user_id"]
    client.put(f"/users/{uid}/location", json={"lat": 37.4, "lon": 127.0})
    client.put(f"/users/{uid}/location", json={"lat": YEOKSAM[0], "lon": YEOKSAM[1]})
    client.put(f"/users/{uid}/filter", json={"cat": ["일식"], "within_m": 500})
    client.delete(f"/users/{uid}/filter")
    client.put(f"/users/{uid}/filter", json={"tag": ["혼밥"]})

    rows = [json.loads(x) for x in state.USERS.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    mine = [r for r in rows if r.get("user_id") == uid]
    assert len(mine) == 6                       # 발급 + 위치 2 + 필터 2 + 삭제 1
    last = mine[-1]
    assert last["name"] == "설정테스트"           # 병합된 전체 레코드다
    assert last["location"]["lat"] == YEOKSAM[0]
    assert last["filter"]["tag"] == ["혼밥"] and last["filter"]["cat"] == []
    assert mine[-2]["filter"] is None           # 지운 것도 한 줄로 남는다

    # 재시작 — 새 등록부에 파일만으로 복원한다(선호는 이 테스트의 관심이 아니다)
    fresh: dict[str, dict] = {}
    state.restore(fresh, [], lambda *a: None)
    assert fresh[uid]["name"] == "설정테스트"
    assert fresh[uid]["location"]["lat"] == YEOKSAM[0]     # 마지막 줄이 이긴다
    assert fresh[uid]["filter"]["tag"] == ["혼밥"]
    assert registry.location_of(uid) == YEOKSAM
    assert registry.filter_of(uid) == {"cat": [], "tag": ["혼밥"], "within_m": None}
