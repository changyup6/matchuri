"""Public map drill-down: complete membership, small responses and no learning writes."""
import copy
import json
import math
import os
import pickle

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import card_ids, identities, loader, state, taste_api, taste_hierarchy, user_api
from recsys import policy, store, swipe, taste


def write(path, value, *, lines=False):
    old = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in value)
                    if lines else json.dumps(value, ensure_ascii=False))
    if old:
        os.utime(path, ns=(old + 1_000_000_000, old + 1_000_000_000))


@pytest.fixture
def hierarchy(tmp_path, monkeypatch):
    ids = [f"s{i}" for i in range(6)]
    layout = {"ids": ids, "name": ["가게" + sid for sid in ids],
              "x": [.1, .3, .8, .5, .9, .2], "y": [.2, .4, .7, .6, .1, .8],
              "cats": ["한식", "중식", "기타"], "cat": [0, 0, 1, 0, 2, 0]}
    rows = [{"store_id": sid, "name": "실명" + sid, "cat2": cat2, "cat3": cat3}
            for sid, cat2, cat3 in zip(ids,
                ["한식", "한식", "중식", "한식", None, "한식"],
                ["국수", "국수", "국수", None, None, "세부분류 없음"])]
    layout_path, stores_path = tmp_path / "layout.json", tmp_path / "stores.jsonl"
    write(layout_path, layout)
    write(stores_path, rows, lines=True)
    monkeypatch.setattr(taste_hierarchy, "LAYOUT_PATH", layout_path)
    monkeypatch.setattr(taste_hierarchy, "_cache", {"key": None, "payloads": {}})
    monkeypatch.setattr(store, "STORES", stores_path)
    for name in ("REVIEWS", "MENUS", "PHOTOS"):
        path = tmp_path / (name.lower() + ".jsonl")
        path.write_text("")
        monkeypatch.setattr(store, name, path)
    for name in ("_mt", "_stores", "_reviews", "_menus", "_photos"):
        monkeypatch.setattr(store, name, {})
    monkeypatch.setattr(loader, "card_id_by_store", {sid: 200 + i for i, sid in enumerate(ids)})
    monkeypatch.setattr(taste_api, "_load_layout", lambda: taste_api._fold(layout))
    app = FastAPI()
    app.include_router(taste_api.router)
    app.include_router(user_api.router)
    return {"client": TestClient(app), "layout": layout, "rows": rows,
            "layout_path": layout_path, "stores_path": stores_path, "app": app}


def get(hierarchy, parent=None):
    result = hierarchy["client"].get("/taste/map/children",
                                     params={} if parent is None else {"parent": parent})
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-cache"
    return result.json()


def leaves(hierarchy):
    result = []
    root = get(hierarchy)
    assert root["level"] == "cat2"
    for cat2 in root["nodes"]:
        children = get(hierarchy, cat2["key"])
        assert children["level"] == "cat3"
        assert sum(row["count"] for row in children["nodes"]) == cat2["count"]
        for cat3 in children["nodes"]:
            stores = get(hierarchy, cat3["key"])
            assert stores["level"] == "store"
            assert len(stores["nodes"]) == cat3["count"]
            result.extend(stores["nodes"])
    return result


def test_all_members_are_reachable_once_with_real_detail_ids(hierarchy):
    nodes = leaves(hierarchy)
    assert len(nodes) == len({node["key"] for node in nodes}) == 6
    assert {node["card_id"] for node in nodes} == set(loader.card_id_by_store.values())
    for node in nodes:
        index = node["card_id"] - 200
        assert node == {"key": node["key"], "name": "실명s" + str(index),
                        "x": hierarchy["layout"]["x"][index],
                        "y": hierarchy["layout"]["y"][index],
                        "r": 0, "count": 1, "card_id": 200 + index}


def test_same_child_names_keep_parents_and_missing_categories_are_accessible(hierarchy):
    root = {node["name"]: node for node in get(hierarchy)["nodes"]}
    korean = get(hierarchy, root["한식"]["key"])["nodes"]
    chinese = get(hierarchy, root["중식"]["key"])["nodes"]
    noodle = next(node for node in korean if node["name"] == "국수")
    assert noodle["key"] != chinese[0]["key"]
    assert (noodle["x"], noodle["y"], noodle["r"]) == (.2, .3, round(math.sqrt(.02), 4))
    missing = [node for node in korean if node["name"] == "세부분류 없음"]
    assert len(missing) == 2 and len({node["key"] for node in missing}) == 2
    unknown = get(hierarchy, root["분류 없음"]["key"])["nodes"]
    assert unknown[0]["name"] == "세부분류 없음"
    assert get(hierarchy, unknown[0]["key"])["nodes"][0]["card_id"] == 204


@pytest.mark.parametrize("parent", ["", "not-a-key", "cat2.invalid", "store.InMwIg"])
def test_invalid_missing_and_leaf_parent_are_consistently_404(hierarchy, parent):
    assert hierarchy["client"].get("/taste/map/children", params={"parent": parent}).status_code == 404


def test_payload_bytes_are_reused_without_reloading_metadata(hierarchy, monkeypatch):
    calls = []
    original = store.all_stores
    monkeypatch.setattr(store, "all_stores", lambda: calls.append(True) or original())
    first = taste_hierarchy._payload(None)
    assert taste_hierarchy._payload(None) is first
    leaves(hierarchy)
    assert calls == [True]


def test_metadata_and_layout_refresh_keep_identity_and_refresh_payload(hierarchy):
    before = {node["card_id"]: node for node in leaves(hierarchy)}
    rows = copy.deepcopy(hierarchy["rows"])
    rows[0].update(name="바뀐 실명", cat3="백반")
    write(hierarchy["stores_path"], rows, lines=True)
    after = {node["card_id"]: node for node in leaves(hierarchy)}
    assert after[200]["name"] == "바뀐 실명"
    assert before[200]["key"] == after[200]["key"]
    changed = copy.deepcopy(hierarchy["layout"])
    changed["x"][0] = .12345
    write(hierarchy["layout_path"], changed)
    assert next(node for node in leaves(hierarchy) if node["card_id"] == 200)["x"] == .12345


def test_keys_do_not_depend_on_source_order_and_mapping_refresh_is_observed(hierarchy):
    before = {node["key"] for node in leaves(hierarchy)}
    changed = copy.deepcopy(hierarchy["layout"])
    for field in ("ids", "name", "x", "y", "cat"):
        changed[field].reverse()
    write(hierarchy["layout_path"], changed)
    assert {node["key"] for node in leaves(hierarchy)} == before
    loader.card_id_by_store["s0"] = 800
    assert 800 in {node["card_id"] for node in leaves(hierarchy)}


def test_no_member_is_silently_dropped_when_catalogue_is_unavailable(hierarchy):
    loader.card_id_by_store.pop("s0")
    assert hierarchy["client"].get("/taste/map/children").status_code == 503


def test_missing_layout_returns_503(hierarchy):
    hierarchy["layout_path"].unlink()
    assert hierarchy["client"].get("/taste/map/children").status_code == 503


def test_read_only_and_existing_default_taste_responses_unchanged(hierarchy, monkeypatch):
    sess = {"hist": [], "n": 0, "likes": 0, "like_ids": [], "shown": {"s0"}}
    monkeypatch.setattr(taste, "_sessions", {"session_test": sess})
    monkeypatch.setattr(swipe, "_sessions", {"session_test": copy.deepcopy(sess)})
    monkeypatch.setattr(policy, "_sessions", {"session_test": {"taste": sess, "batch": 3}})
    monkeypatch.setattr(taste_api.user_registry, "name_of", lambda _: "검증")
    monkeypatch.setattr(identities, "require_session", lambda *_: None)
    monkeypatch.setattr(taste_api.observe, "inc_taste_view", lambda: None)
    monkeypatch.setattr(taste_api.observe, "log", lambda *a, **kw: None)
    routes = ["/taste/map", "/users/session_test/taste", "/user/person_test/session/session_test/taste"]
    before = [hierarchy["client"].get(route).json() for route in routes]
    model_before = pickle.dumps((taste._sessions, swipe._sessions, policy._sessions))
    files = {path: path.read_bytes() for path in hierarchy["layout_path"].parent.iterdir()}

    def forbidden(*a, **kw):
        pytest.fail("Hierarchy reads must not allocate cards, record feedback/exposure or write state")

    for module, name in ((loader, "load_all"), (card_ids, "save"), (state, "_append"),
                         (policy, "record"), (policy, "pick_many")):
        monkeypatch.setattr(module, name, forbidden)
    leaves(hierarchy)
    assert [hierarchy["client"].get(route).json() for route in routes] == before
    assert before[2] == {**before[1], "user_id": "person_test", "session_id": "session_test"}
    assert pickle.dumps((taste._sessions, swipe._sessions, policy._sessions)) == model_before
    assert {path: path.read_bytes() for path in files} == files


def test_openapi_has_small_explicit_schema_and_preserves_region_contract(hierarchy):
    spec = hierarchy["app"].openapi()
    models = spec["components"]["schemas"]
    assert set(models["TasteMapChildrenOut"]["properties"]) == {"level", "nodes"}
    assert set(models["TasteMapNodeOut"]["properties"]) == {"key", "name", "x", "y", "r", "count", "card_id"}
    assert set(models["RegionOut"]["properties"]) == {"name", "x", "y", "r", "count"}


@pytest.fixture
def user_hierarchy(hierarchy, monkeypatch):
    history = [{"n": i + 1, "store_id": sid, "like": sid != "s4"}
               for i, sid in enumerate(["s0", "s4", "s2", "s3"] * 4)]
    sess = {"hist": history, "n": 16, "likes": 12, "like_ids": ["s0", "s2", "s3"],
            "shown": {"s0", "s2", "s3", "s4"}}
    empty = {"hist": [], "n": 0, "likes": 0, "like_ids": [], "shown": set()}
    monkeypatch.setattr(taste, "_sessions", {"session_test": sess, "session_other": empty})
    monkeypatch.setattr(identities, "_people", {
        "person_test": {"user_id": "person_test"}, "person_other": {"user_id": "person_other"}})
    monkeypatch.setattr(identities, "_sessions", {
        sid: {"user_id": "person_test", "session_id": sid} for sid in taste._sessions})
    monkeypatch.setattr(taste_api.user_registry, "name_of", lambda _: "검증")
    monkeypatch.setattr(taste_api.observe, "inc_taste_view", lambda: None)
    monkeypatch.setattr(taste_api.observe, "log", lambda *a, **kw: None)
    return hierarchy


def test_user_taste_embeds_shared_hierarchy_without_changing_trail(user_hierarchy):
    client = user_hierarchy["client"]
    path = "/user/person_test/session/session_test/taste"
    root = client.get(path).json()
    assert root["user_id"] == "person_test" and root["session_id"] == "session_test"
    assert root["map"] == {"version": 3, "level": 1, "regions": get(user_hierarchy)["nodes"]}
    assert root["swipes"] == 16 and root["likes"] == len(root["trail"]) == 12
    assert root["drift"] is not None and root["now"]["near"]
    unchanged = {k: v for k, v in root.items() if k != "map"}
    expected = {1: root["map"]["regions"], 2: [], 3: leaves(user_hierarchy)}
    for category in expected[1]:
        expected[2].extend(get(user_hierarchy, category["key"])["nodes"])
    for detail in (1, 2, 3):
        result = client.get(path, params={"detail": detail}).json()
        assert result["map"]["version"] == 3 and result["map"]["level"] == detail
        assert {n["key"]: n for n in result["map"]["regions"]} == {n["key"]: n for n in expected[detail]}
        assert {k: v for k, v in result.items() if k != "map"} == unchanged
        legacy = client.get("/users/session_test/taste", params={"detail": detail}).json()
        assert result == {**legacy, "user_id": "person_test", "session_id": "session_test"}
    # Background membership is independent of likes, dislikes, or unseen status.
    assert [node["card_id"] for node in result["map"]["regions"]] == list(range(200, 206))
    assert len(expected[2]) == 5 and sum(node["count"] for node in expected[2]) == 6
    assert len({node["key"] for node in expected[2]}) == 5
    assert client.get(path, params={"detail": 1}).json() == root
    global_map = client.get("/taste/map").json()
    assert global_map == {"version": 1, "regions": taste_api._fold(user_hierarchy["layout"])["regions"]}


def test_detail_does_not_mix_user_sessions(user_hierarchy):
    client = user_hierarchy["client"]
    result = client.get("/user/person_test/session/session_other/taste", params={"detail": 3}).json()
    assert result["map"]["level"] == 3 and len(result["map"]["regions"]) == 6
    assert result["swipes"] == result["likes"] == 0
    assert result["trail"] == [] and result["now"] is None and result["drift"] is None


@pytest.mark.parametrize("detail", [0, 4, -1, "", "bad", "2.5", "true", "NaN"])
def test_user_taste_invalid_detail_is_422(user_hierarchy, detail):
    for path in ("/users/session_test/taste", "/user/person_test/session/session_test/taste"):
        result = user_hierarchy["client"].get(path, params={"detail": detail})
        assert result.status_code == 422


def test_wrong_owner_is_rejected_before_hierarchy_lookup(user_hierarchy, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("Ownership must be checked before reading background metadata")
    monkeypatch.setattr(taste_hierarchy, "read_level", forbidden)
    result = user_hierarchy["client"].get(
        "/user/person_other/session/session_test/taste", params={"detail": 3})
    assert result.status_code == 404 and "this user" in result.json()["detail"]


def test_user_detail_requests_reuse_flat_cache_and_do_not_write(user_hierarchy, monkeypatch):
    get(user_hierarchy)
    cached = {detail: taste_hierarchy._payload(detail) for detail in (1, 2, 3)}
    before = pickle.dumps((taste._sessions, swipe._sessions, policy._sessions, identities._sessions))
    files = {path: path.read_bytes() for path in user_hierarchy["layout_path"].parent.iterdir()}
    def forbidden(*a, **kw):
        pytest.fail("Detail selection cannot allocate cards, rebuild metadata or write learning state")
    for module, name in ((loader, "load_all"), (card_ids, "save"), (state, "_append"),
                         (policy, "record"), (policy, "pick_many"), (store, "all_stores"),
                         (taste_hierarchy, "_build")):
        monkeypatch.setattr(module, name, forbidden)
    for path in ("/users/session_test/taste", "/user/person_test/session/session_test/taste"):
        for detail in (1, 2, 3):
            assert user_hierarchy["client"].get(path, params={"detail": detail}).status_code == 200
            assert taste_hierarchy._payload(detail) is cached[detail]
    # Composing a user response must not mutate the public shared cache.
    detached = taste_hierarchy.read_level(3)
    detached["nodes"].clear()
    assert taste_hierarchy.read_level(3)["nodes"]
    assert pickle.dumps((taste._sessions, swipe._sessions, policy._sessions, identities._sessions)) == before
    assert {path: path.read_bytes() for path in files} == files


def test_user_taste_openapi_declares_detail_and_versioned_regions(user_hierarchy):
    spec = user_hierarchy["app"].openapi()
    for path in ("/users/{user_id}/taste", "/user/{user_id}/session/{session_id}/taste"):
        operation = spec["paths"][path]["get"]
        assert not any(p["name"] == "parent" for p in operation["parameters"])
        parameter = next(p for p in operation["parameters"] if p["name"] == "detail")
        assert parameter["in"] == "query" and not parameter["required"]
        assert parameter["schema"]["type"] == "integer" and parameter["schema"]["default"] == 1
        assert parameter["schema"]["minimum"] == 1 and parameter["schema"]["maximum"] == 3
        expected = "UserTasteOut" if path.startswith("/users/") else "SessionTasteOut"
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] \
            .endswith("/" + expected)
    models = spec["components"]["schemas"]
    props = models["UserTasteMapOut"]["properties"]
    assert set(props) == {"version", "level", "regions"}
    assert props["version"]["const"] == 3
    assert props["level"]["enum"] == [1, 2, 3]
    for model in ("UserTasteOut", "SessionTasteOut"):
        assert models[model]["properties"]["map"]["$ref"].endswith("/UserTasteMapOut")


def test_user_taste_direct_call_has_integer_default_and_parent_no_longer_filters(user_hierarchy):
    client = user_hierarchy["client"]
    root = client.get("/users/session_test/taste").json()
    assert taste_api.user_taste("session_test") == root
    assert user_api.get_taste("person_test", "session_test") == {
        **root, "user_id": "person_test", "session_id": "session_test"}
    parent = get(user_hierarchy)["nodes"][0]["key"]
    # Unknown query parameters follow the existing API convention: ignored.
    assert client.get("/users/session_test/taste", params={"parent": parent}).json() == root


def test_flat_levels_refresh_with_shared_source_cache(user_hierarchy):
    before = taste_hierarchy._payload(2)
    rows = copy.deepcopy(user_hierarchy["rows"])
    rows[0]["cat3"] = "새 세부 분류"
    write(user_hierarchy["stores_path"], rows, lines=True)
    assert taste_hierarchy._payload(2) != before
    assert "새 세부 분류" in {node["name"] for node in taste_hierarchy.read_level(2)["nodes"]}
    changed = copy.deepcopy(user_hierarchy["layout"])
    changed["x"][0] = .23456
    write(user_hierarchy["layout_path"], changed)
    assert taste_hierarchy.read_level(3)["nodes"][0]["x"] == .23456
