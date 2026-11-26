# -*- coding: utf-8 -*-
"""`/openapi.json` 계약 — 프론트가 여기서 타입을 생성한다.

`npx openapi-typescript http://host/openapi.json -o api.d.ts` 한 줄이 프론트의 타입이
되므로, **응답 스키마가 없는 클라이언트 엔드포인트가 하나라도 생기면** 그 자리는 `any` 가
된다(예전에는 전부 `any` 였다). 그래서 오퍼레이션 목록을 여기 손으로 적어 두고 빠짐을
잡는다 — 새 엔드포인트를 만들면 이 목록에도 한 줄 추가하게 된다.

허용 값 목록(업종·태그·prefer)도 잠근다. 스키마의 `enum` 이 곧 프론트의 유니온 타입이고,
서버 어휘가 바뀌면 그쪽 타입도 같이 바뀌어야 한다.
"""
from __future__ import annotations

import pytest

from app import filters

#: 클라이언트가 부르는 오퍼레이션 전부 → 200 응답 스키마의 이름.
#: `list[X]` 는 배열, 그 밖은 `$ref` 하나다.
CLIENT_OPS = {
    ("get", "/"): "StatusOut",
    ("post", "/users"): "UserRecordOut",
    ("get", "/users/{user_id}"): "UserOut",
    ("put", "/users/{user_id}/location"): "UserRecordOut",
    ("put", "/users/{user_id}/filter"): "UserRecordOut",
    ("delete", "/users/{user_id}/filter"): "UserRecordOut",
    ("put", "/users/{user_id}/bookmarks/{card_id}"): "BookmarkOut",
    ("delete", "/users/{user_id}/bookmarks/{card_id}"): "BookmarkOut",
    ("get", "/users/{user_id}/bookmarks"): "list[BookmarkCardOut]",
    ("get", "/filters"): "FiltersOut",
    ("get", "/cards"): "list[CardOut]",
    ("get", "/cards/{card_id}"): "CardDetailOut",
    ("get", "/cards/{card_id}/reviews"): "list[Review]",
    ("post", "/preferences"): "PreferenceOut",
    ("get", "/preferences/{user_id}"): "list[CardBaseOut]",
    ("get", "/taste/map"): "TasteMapOut",
    ("get", "/taste/map/children"): "TasteMapChildrenOut",
    ("get", "/users/{user_id}/taste"): "UserTasteOut",
    ("get", "/users/{user_id}/decision"): "DecisionOut",
}

#: 타입을 붙이지 않는 것들 — 관리자 화면 전용이라 계약이 아니다(화면과 함께 바뀐다).
ADMIN_PREFIX = "/admin/"


@pytest.fixture(scope="module")
def spec():
    import main
    return main.app.openapi()


def _ok_schema(spec: dict, method: str, path: str) -> dict:
    op = spec["paths"][path][method]
    body = op["responses"]["200"]["content"]["application/json"]
    return body["schema"]


def _name(schema: dict) -> str:
    if schema.get("type") == "array":
        return f"list[{_name(schema['items'])}]"
    return schema["$ref"].split("/")[-1]


def test_클라이언트_오퍼레이션은_모두_응답_스키마가_있다(spec):
    for (method, path), want in CLIENT_OPS.items():
        assert path in spec["paths"], f"{method} {path} 가 없다"
        assert method in spec["paths"][path], f"{method} {path} 가 없다"
        schema = _ok_schema(spec, method, path)
        assert _name(schema) == want, f"{method} {path} -> {schema}"


def test_스키마는_실제_필드를_담고_있다(spec):
    """`$ref` 만 있고 속성이 비면 프론트가 얻는 것은 없다."""
    comp = spec["components"]["schemas"]
    for want in CLIENT_OPS.values():
        name = want[5:-1] if want.startswith("list[") else want
        props = comp[name]["properties"]
        assert props, name
        assert all(p.get("description") for p in props.values()), name


def test_카드_계약(spec):
    """카드는 세 모양이다 — 목록·상세·선호 목록. 필드가 서로 어긋나면 안 된다."""
    comp = spec["components"]["schemas"]
    base = set(comp["CardBaseOut"]["properties"])
    assert base == {"card_id", "store", "image", "images", "bookmarked",
                    "open_now"}
    assert set(comp["CardOut"]["properties"]) == base | {
        "recommendation_reason", "policy", "attribution", "distance_m"}
    # 상세에만 사용자 후기가 붙는다 — 목록은 카드 수만큼 곱해지는 자리다(`main.py: _card_out`).
    assert set(comp["CardDetailOut"]["properties"]) == base | {
        "menus", "distance_m", "user_reviews"}
    assert set(comp["BookmarkCardOut"]["properties"]) == base | {
        "bookmarked_at", "distance_m"}
    assert set(comp["BookmarkOut"]["properties"]) == {"card_id", "bookmarked",
                                                      "at", "count"}
    assert comp["CardOut"]["properties"]["policy"]["anyOf"][0]["enum"] == \
        ["legacy", "taste", "trend"]
    store = comp["Store"]["properties"]
    assert store["category_group"]["enum"] == filters.CAT_CHOICES
    assert comp["CardDetailOut"]["properties"]["menus"]["items"]["$ref"].endswith("Food")
    assert comp["CardDetailOut"]["properties"]["user_reviews"]["items"]["$ref"]         .endswith("UserReviewOut")
    # **`user_id` 는 후기에 실리지 않는다.** 이 서버에서 그 값은 그 사람의 열쇠다.
    assert "user_id" not in comp["UserReviewOut"]["properties"]


def test_어휘가_스키마에_들어_있다(spec):
    """`/docs` 와 생성된 타입이 허용 값을 알아야 한다(`GET /filters` 와 같은 목록)."""
    comp = spec["components"]["schemas"]
    for model in ("FilterIn", "FilterOut"):
        props = comp[model]["properties"]
        assert props["cat"]["items"]["enum"] == filters.CAT_CHOICES
        assert props["tag"]["items"]["enum"] == list(filters.TAGS)
    assert comp["Log"]["properties"]["prefer"]["enum"] == ["like", "dislike"]
    q = [p for p in spec["paths"]["/preferences/{user_id}"]["get"]["parameters"]
         if p["name"] == "prefer"]
    assert q[0]["schema"]["enum"] == ["like", "dislike", "all"]


def test_사용자_설정은_중첩_모델이다(spec):
    """`location`·`filter` 가 `null` 가능한 객체로 보여야 프론트가 분기할 수 있다."""
    props = spec["components"]["schemas"]["UserRecordOut"]["properties"]
    for key, model in (("location", "LocationOut"), ("filter", "FilterOut")):
        refs = [x.get("$ref", "") for x in props[key]["anyOf"]]
        types = [x.get("type") for x in props[key]["anyOf"]]
        assert any(r.endswith(model) for r in refs) and "null" in types
    user = spec["components"]["schemas"]["UserOut"]["properties"]
    assert {"registered", "swipes", "likes", "bookmarks", "learned"} <= set(user)
    assert "null" in [x.get("type") for x in user["learned"]["anyOf"]]


def test_취향_응답은_중첩까지_타입이_있다(spec):
    comp = spec["components"]["schemas"]
    taste = comp["UserTasteOut"]["properties"]
    assert taste["map"]["$ref"].endswith("/UserTasteMapOut")
    assert taste["trail"]["items"]["$ref"].endswith("TrailPointOut")
    assert comp["TasteMapOut"]["properties"]["regions"]["items"]["$ref"] \
        .endswith("RegionOut")
    assert set(comp["RegionOut"]["properties"]) == {"name", "x", "y", "r", "count"}
    background = comp["UserTasteMapOut"]["properties"]
    assert set(background) == {"version", "level", "regions"}
    assert background["version"]["const"] == 3
    assert background["level"]["enum"] == [1, 2, 3]
    assert background["level"]["type"] == "integer"
    assert background["regions"]["items"]["$ref"].endswith("/TasteMapNodeOut")
    assert set(comp["DriftOut"]["properties"]) == {"from_region", "to_region",
                                                   "distance", "window"}
    assert comp["NowOut"]["properties"]["near"]["items"]["$ref"].endswith("NearOut")


def test_취향_확대_쿼리와_세션_응답형식이_실제_경로에_있다(spec):
    for path, model in (("/users/{user_id}/taste", "UserTasteOut"),
                        ("/user/{user_id}/session/{session_id}/taste", "SessionTasteOut")):
        operation = spec["paths"][path]["get"]
        assert _name(_ok_schema(spec, "get", path)) == model
        assert not any(p["name"] == "parent" for p in operation["parameters"])
        parameters = [p for p in operation["parameters"] if p["name"] == "detail"]
        assert len(parameters) == 1 and parameters[0]["in"] == "query"
        assert parameters[0]["required"] is False
        schema = parameters[0]["schema"]
        assert schema["type"] == "integer" and schema["default"] == 1
        assert schema["minimum"] == 1 and schema["maximum"] == 3
    session = spec["components"]["schemas"]["SessionTasteOut"]
    assert "session_id" in session["required"]


def test_관리자_경로는_계약이_아니다(spec):
    """화면과 함께 바뀌는 것들이라 타입을 약속하지 않는다 — 섞이지 않았는지만 본다."""
    typed = {p for (_m, p) in CLIENT_OPS}
    assert not any(p.startswith(ADMIN_PREFIX) for p in typed)
    assert any(p.startswith(ADMIN_PREFIX) for p in spec["paths"])


def test_모든_오퍼레이션에_설명이_있다(spec):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op.get("summary"), f"{method} {path}"
            assert op.get("description"), f"{method} {path}"


def test_신규_user_session_API는_기능별로_분류된다(spec):
    """신규 경로가 다시 한 태그에 몰리면 Swagger에서 필요한 API를 찾기 어렵다."""
    expected = {
        ("post", "/user"): "사용자",
        ("post", "/user/{user_id}/session"): "추천 세션",
        ("get", "/user/{user_id}/session/{session_id}/cards"): "카드",
        ("post", "/user/{user_id}/session/{session_id}/preferences"): "선호",
        ("get", "/user/{user_id}/session/{session_id}/decision"): "선택 제안",
        ("get", "/user/{user_id}/session/{session_id}/decision/metrics"): "판단 수치",
        ("get", "/user/{user_id}/session/{session_id}/taste"): "취향",
        ("get", "/user/{user_id}/bookmarks"): "찜",
        ("get", "/user/{user_id}/surveys/pending"): "설문",
    }
    for (method, path), tag in expected.items():
        assert spec["paths"][path][method]["tags"] == [tag], (method, path)
    assert all("사용자·세션" not in op.get("tags", [])
               for methods in spec["paths"].values() for op in methods.values())
    assert [row["name"] for row in spec["tags"][:9]] == [
        "사용자", "추천 세션", "카드", "선호", "선택 제안", "판단 수치",
        "취향", "찜", "설문"]
    for method, path in (("post", "/users"), ("get", "/cards"),
                         ("post", "/preferences"),
                         ("get", "/users/{user_id}/decision/metrics/summary"),
                         ("get", "/users/{user_id}/bookmarks")):
        operation = spec["paths"][path][method]
        assert operation["deprecated"] is True
        assert operation["tags"] == ["호환 API"]


def test_예시가_그대로_요청으로_들어간다(spec):
    """스키마의 `examples` 는 진짜 값이어야 한다 — `/docs` 의 'Try it out' 이 그걸 보낸다."""
    from fastapi.testclient import TestClient

    import main
    client = TestClient(main.app)
    comp = spec["components"]["schemas"]

    name = comp["UserCreate"]["properties"]["name"]["examples"][0]
    created = client.post("/users", json={"name": name})
    assert created.status_code == 200 and created.json()["name"] == name
    uid = created.json()["user_id"]

    loc = comp["LocationIn"]["properties"]
    body = {"lat": loc["lat"]["examples"][0], "lon": loc["lon"]["examples"][0]}
    assert client.put(f"/users/{uid}/location", json=body).status_code == 200

    flt = comp["FilterIn"]["properties"]
    body = {"cat": flt["cat"]["examples"][0], "tag": flt["tag"]["examples"][0],
            "within_m": flt["within_m"]["examples"][0]}
    saved = client.put(f"/users/{uid}/filter", json=body)
    assert saved.status_code == 200 and saved.json()["filter"]["cat"] == body["cat"]

    cards = client.get(f"/cards?user_id={uid}&limit=1").json()
    assert cards and cards[0]["store"]["category_group"] == body["cat"][0]
    prefer = comp["Log"]["properties"]["prefer"]["examples"][0]
    r = client.post("/preferences", json={"user_id": uid,
                                          "card_id": cards[0]["card_id"],
                                          "prefer": prefer})
    assert r.status_code == 200 and r.json()["ok"] is True
