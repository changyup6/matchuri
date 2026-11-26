"""Layout classification metadata: aligned, cached, read-only source values."""
import copy
import json
import os
import pickle

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import admin, state
from recsys import policy, store, swipe, taste


def write_rows(path, rows):
    old = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    # Deliberately advance the file timestamp rather than relying on clock precision.
    if old:
        os.utime(path, ns=(old + 1_000_000_000, old + 1_000_000_000))


@pytest.fixture
def hierarchy(tmp_path, monkeypatch):
    ids = ["s2", "s0", "s1", "unknown"]
    base = {"count": len(ids), "ids": ids, "x": [.1, .3, .5, .9],
            "y": [.2, .4, .6, .8], "cats": ["한식", "중식", "기타"],
            "cat": [0, 1, 0, 2], "nbr": [[1], [0], [1], [2]],
            "names": ["둘", "영", "하나", "누락"]}
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(json.dumps(base, ensure_ascii=False))
    rows = [{"store_id": "s0", "cat2": "중식", "cat3": "국수"},
            {"store_id": "s1", "cat2": "한식", "cat3": None},
            {"store_id": "s2", "cat2": "한식", "cat3": "국수"}]
    stores_path = tmp_path / "stores.jsonl"
    write_rows(stores_path, rows)
    monkeypatch.setattr(admin, "LAYOUT_PATH", layout_path)
    monkeypatch.setattr(admin, "_layout_cache", {"key": None, "bytes": None})
    monkeypatch.setattr(store, "STORES", stores_path)
    for field in ("REVIEWS", "MENUS", "PHOTOS"):
        path = tmp_path / (field.lower() + ".jsonl")
        path.write_text("")
        monkeypatch.setattr(store, field, path)
    for field in ("_mt", "_stores", "_reviews", "_menus", "_photos"):
        monkeypatch.setattr(store, field, {})
    monkeypatch.setattr(swipe, "pool", lambda: {"ids": ids.copy()})
    app = FastAPI()
    app.include_router(admin.router)
    return {"client": TestClient(app), "base": base, "rows": rows,
            "layout_path": layout_path, "stores_path": stores_path}


def test_layout_adds_only_aligned_original_category_arrays(hierarchy):
    response = hierarchy["client"].get("/admin/api/map/layout")
    assert response.status_code == 200
    result = response.json()
    assert set(result) == set(hierarchy["base"]) | {"cat2", "cat3"}
    assert all(result[key] == value for key, value in hierarchy["base"].items())
    assert result["cat2"] == ["한식", "중식", "한식", None]
    assert result["cat3"] == ["국수", "국수", None, None]
    assert len(result["cat2"]) == len(result["cat3"]) == len(result["ids"])
    # Identical child names retain their distinct parents; no server-side tree.
    assert list(zip(result["cat2"], result["cat3"]))[:2] == [("한식", "국수"), ("중식", "국수")]


def test_repeated_layout_requests_reuse_bytes_without_metadata_reads(hierarchy, monkeypatch):
    original = store.get_store
    reads = []

    def tracked(sid):
        reads.append(sid)
        return original(sid)

    monkeypatch.setattr(store, "get_store", tracked)
    first = admin._layout_payload()
    second = admin._layout_payload()
    response = hierarchy["client"].get("/admin/api/map/layout")
    assert first is second
    assert response.content == first
    assert reads == hierarchy["base"]["ids"]


def test_store_metadata_refresh_invalidates_layout_bytes_without_moving_points(hierarchy):
    before = hierarchy["client"].get("/admin/api/map/layout").json()
    rows = copy.deepcopy(hierarchy["rows"])
    rows[0]["cat3"] = "딤섬"
    rows[1]["cat3"] = "백반"
    write_rows(hierarchy["stores_path"], rows)
    after = hierarchy["client"].get("/admin/api/map/layout").json()
    assert after["cat3"] == ["국수", "딤섬", "백반", None]
    assert before["cat3"] != after["cat3"]
    assert all(before[key] == after[key] for key in hierarchy["base"])


def test_rebuilt_layout_invalidates_cached_coordinates(hierarchy):
    first = admin._layout_payload()
    changed = copy.deepcopy(hierarchy["base"])
    changed["x"][0] = .12345
    hierarchy["layout_path"].write_text(json.dumps(changed))
    second = admin._layout_payload()
    assert first != second
    assert json.loads(second)["x"] == changed["x"]


def test_missing_or_invalid_category_metadata_stays_null(hierarchy):
    rows = copy.deepcopy(hierarchy["rows"])
    rows[0].update(cat2="", cat3="   ")
    rows[1].update(cat2=None, cat3=42)
    write_rows(hierarchy["stores_path"], rows)
    result = hierarchy["client"].get("/admin/api/map/layout").json()
    assert result["cat2"] == ["한식", None, None, None]
    assert result["cat3"] == ["국수", None, None, None]


def test_layout_lookup_never_changes_sessions_or_writes_source_files(hierarchy, monkeypatch):
    model = {"n": 2, "like_ids": ["s0"], "dislike_ids": ["s1"], "shown": {"s0", "s1"}}
    monkeypatch.setattr(taste, "_sessions", {"example": model})
    monkeypatch.setattr(swipe, "_sessions", {"example": copy.deepcopy(model)})
    monkeypatch.setattr(policy, "_sessions", {"example": {"taste": model, "batch": 3}})
    before = pickle.dumps((taste._sessions, swipe._sessions, policy._sessions))
    files = {path: path.read_bytes() for path in hierarchy["layout_path"].parent.iterdir()}

    def forbidden(*args, **kwargs):
        pytest.fail("Layout lookup must not record feedback, exposure or state")

    monkeypatch.setattr(state, "_append", forbidden)
    monkeypatch.setattr(policy, "record", forbidden)
    monkeypatch.setattr(policy, "pick_many", forbidden)
    for _ in range(2):
        assert hierarchy["client"].get("/admin/api/map/layout").status_code == 200
    assert pickle.dumps((taste._sessions, swipe._sessions, policy._sessions)) == before
    assert {path: path.read_bytes() for path in files} == files


def test_layout_id_mismatch_still_fails_instead_of_misaligning_metadata(hierarchy):
    changed = copy.deepcopy(hierarchy["base"])
    changed["ids"] = list(reversed(changed["ids"]))
    hierarchy["layout_path"].write_text(json.dumps(changed))
    assert hierarchy["client"].get("/admin/api/map/layout").status_code == 503
