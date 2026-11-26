"""Prepared catalogue JPEGs replace delivery URLs without changing source data.

Read a small raw catalogue through the real store adapter. Only response assembly
may change: image selection, menu order and the recommender's raw URLs stay intact.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from PIL import Image
import pytest

from app import admin, loader, media
from recsys import store


PUBLIC_BASE = "https://images.example.test"
SOURCES = {
    name: f"https://source.example.test/{name}.png?original=1"
    for name in ("poster", "food", "interior", "outdoor", "menu", "review")
}


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    records = {
        "stores": [
            {"store_id": "s1", "name": "테스트 식당", "cat2": "한식",
             "cat3": "육류,고기", "review_avg": 4.5, "review_count": 23,
             "address": "테스트 주소", "lat": 37.5, "lon": 127.0,
             "source_platform": "kakao"},
            {"store_id": "empty", "name": "사진 없는 식당", "cat2": "한식"},
        ],
        "photos": [
            {"store_id": "s1", "url": SOURCES["poster"], "tag": "MYSTORE",
             "hero": True, "food": False},
            {"store_id": "s1", "url": SOURCES["interior"], "tag": "INDOOR",
             "food": False},
            {"store_id": "s1", "url": SOURCES["food"], "tag": "FOOD",
             "food": True},
            {"store_id": "s1", "url": SOURCES["food"], "tag": "FOOD",
             "food": True},
            {"store_id": "s1", "url": SOURCES["outdoor"], "tag": "OUTDOOR"},
        ],
        "menus": [
            {"store_id": "s1", "menu_name": "나중 메뉴", "menu_index": 9,
             "photo_url": SOURCES["menu"], "price": 20000,
             "is_recommend": True, "source_platform": "naver"},
            {"store_id": "s1", "menu_name": "첫 메뉴", "menu_index": 2,
             "photo_url": SOURCES["food"], "price": 12000,
             "description": "메뉴 설명", "price_text": "12,000원"},
            {"store_id": "s1", "menu_name": "사진 없는 메뉴", "menu_index": 5,
             "photo_url": None, "price": None},
        ],
        "reviews": [
            {"store_id": "s1", "review_id": 91, "rating": 5,
             "content": "첫 번째 후기 내용은 그대로 유지합니다.",
             "photo_urls": [SOURCES["review"], SOURCES["food"]],
             "author_id": "author-1", "date": "2026-09-18",
             "strength_tags": ["맛", "친절"], "like_count": 3,
             "source_platform": "naver"},
            {"store_id": "s1", "review_id": 92, "rating": 3,
             "content": "두 번째 후기는 원본 photos 형식을 사용합니다.",
             "photos": [{"url": SOURCES["interior"]},
                        {"url": SOURCES["outdoor"]}]},
        ],
    }
    paths = {}
    for name, rows in records.items():
        path = tmp_path / f"{name}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                for row in rows), encoding="utf-8")
        paths[name] = path
        monkeypatch.setattr(store, name.upper(), path)
        monkeypatch.setattr(store, f"_{name}", {})
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "_mt", {})
    monkeypatch.setattr(media, "DIR", tmp_path / "media")
    monkeypatch.setenv("MATCHURI_MEDIA_PUBLIC_BASE_URL", PUBLIC_BASE)
    monkeypatch.setattr(loader, "load_all", lambda: None)
    monkeypatch.setattr(loader, "store_id_by_card", {42: "s1", 43: "empty"})
    monkeypatch.setattr(loader, "card_id_by_store", {"s1": 42, "empty": 43})
    store.load()
    return paths


def _public(source, variant="card"):
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"{PUBLIC_BASE}/media/catalog/v2/{variant}/{digest[:2]}/{digest}.jpg"


def _prepare(source, variant="card"):
    """Publish a real small JPEG, as the independent downloader does atomically."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    path = media.DIR / "catalog" / "v2" / variant / digest[:2] / f"{digest}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 12), (180, 70, 30)).save(path, "JPEG", progressive=True)
    return path


def _responses(*, original_images=False):
    return {
        "card": loader.get_card(42, n_reviews=2, original_images=original_images).model_dump(),
        "menus": [row.model_dump() for row in loader.get_menus(42, original_images=original_images)],
        "reviews": [row.model_dump() for row in loader.get_reviews(42, original_images=original_images)],
        "admin": admin._card_brief("s1"),
    }


def _expected(before, ready):
    expected = deepcopy(before)

    def resolved(url, variant="card"):
        return _public(url, variant) if url in ready else url

    card = expected["card"]
    card["image"] = resolved(card["image"])
    card["images"] = [resolved(url) for url in card["images"]]
    for review in card["store"]["reviews"] + expected["reviews"]:
        review["images"] = [resolved(url, "thumb") for url in review["images"]]
    for menu in expected["menus"]:
        menu["image"] = resolved(menu["image"], "thumb")
    expected["admin"]["image"] = resolved(expected["admin"]["image"])
    return expected


def _raw_snapshot(paths):
    return {
        "files": {name: path.read_bytes() for name, path in paths.items()},
        "memory": deepcopy({name: getattr(store, f"_{name}") for name in paths}),
    }


def test_uncached_images_keep_original_urls_and_existing_selection(catalogue):
    raw = _raw_snapshot(catalogue)
    result = _responses()

    assert result["card"]["card_id"] == 42
    assert result["card"]["image"] == SOURCES["food"]
    assert result["card"]["images"] == [SOURCES[key] for key in
                                       ("food", "poster", "interior", "outdoor")]
    assert result["admin"]["image"] == SOURCES["poster"]
    assert [(row["id"], row["name"]) for row in result["menus"]] == [
        (0, "첫 메뉴"), (1, "사진 없는 메뉴"), (2, "나중 메뉴")]
    assert [row["image"] for row in result["menus"]] == [
        SOURCES["food"], None, SOURCES["menu"]]
    assert [row["id"] for row in result["reviews"]] == [91, 92]
    assert [row["images"] for row in result["reviews"]] == [
        [SOURCES["review"], SOURCES["food"]],
        [SOURCES["interior"], SOURCES["outdoor"]]]
    assert _raw_snapshot(catalogue) == raw


@pytest.mark.parametrize("ready_names", [
    ("poster", "food", "interior", "outdoor", "menu", "review"),
    ("food", "review"),
])
def test_ready_jpegs_change_only_delivery_urls(catalogue, ready_names):
    before = _responses()
    raw = _raw_snapshot(catalogue)
    ready = {SOURCES[name] for name in ready_names}
    for url in ready:
        for variant in ("card", "thumb"):
            _prepare(url, variant)

    assert _responses() == _expected(before, ready)
    assert _raw_snapshot(catalogue) == raw


def test_completed_cache_becomes_visible_without_reloading_catalogue(catalogue):
    before = _responses()
    raw = _raw_snapshot(catalogue)
    mtime = deepcopy(store._mt)
    ready = {SOURCES["food"]}
    paths = [_prepare(SOURCES["food"], variant) for variant in ("card", "thumb")]

    assert _responses() == _expected(before, ready)
    assert store._mt == mtime
    assert _raw_snapshot(catalogue) == raw

    # A missing file must not leave a stale, broken local URL in future responses.
    for path in paths:
        path.unlink()
    assert _responses() == before


def test_missing_images_remain_null_or_empty_and_unknown_ids_stay_missing(catalogue):
    card = loader.get_card(43)
    assert card.image is None and card.images == []
    assert card.store.reviews == []
    assert admin._card_brief("empty")["image"] is None
    assert loader.get_menus(43) == [] and loader.get_reviews(43) == []
    assert loader.get_card(-1) is None
    assert loader.get_menus(-1) == [] and loader.get_reviews(-1) == []


def test_original_images_bypass_both_ready_profiles_without_changing_source(catalogue):
    before = _responses()
    raw = _raw_snapshot(catalogue)
    for url in SOURCES.values():
        for variant in ("card", "thumb"):
            _prepare(url, variant)

    original = _responses(original_images=True)
    for key in ("card", "menus", "reviews"):
        assert original[key] == before[key]
    assert _responses() == _expected(before, set(SOURCES.values()))
    assert _raw_snapshot(catalogue) == raw


@pytest.fixture
def mounted_api():
    # Import the actual application before replacing its catalogue in the next
    # fixture; startup runs against the isolated test state from conftest.
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app)


@pytest.fixture
def delivery_api(mounted_api, request, monkeypatch):
    paths = request.getfixturevalue("catalogue")
    from app import user_reviews
    uploaded = [{
        "name": "테스트 사용자", "satisfaction": "good", "taste": "고소해요",
        "price": "합리적이에요", "mood": "조용해요", "at": "2026-09-18T12:00:00+09:00",
        "images": ["/media/ab/" + "ab" * 16 + ".jpg"],
    }]
    monkeypatch.setattr(user_reviews, "for_store", lambda sid: deepcopy(uploaded) if sid == "s1" else [])
    return mounted_api, paths


def test_detail_http_original_images_is_opt_in_and_preserves_existing_response(delivery_api):
    api, paths = delivery_api
    original = api.get("/cards/42?reviews=2").json()
    raw = _raw_snapshot(paths)
    for url in SOURCES.values():
        for variant in ("card", "thumb"):
            _prepare(url, variant)

    small = api.get("/cards/42?reviews=2")
    assert small.status_code == 200, small.text
    expected = _expected({"card": original, "menus": original["menus"],
                          "reviews": [], "admin": {"image": None}}, set(SOURCES.values()))
    expected["card"]["menus"] = expected["menus"]
    assert small.json() == expected["card"]
    assert api.get("/cards/42?reviews=2&original_images=false").json() == small.json()
    full = api.get("/cards/42?reviews=2&original_images=true")
    assert full.status_code == 200 and full.json() == original
    assert small.json()["user_reviews"] == original["user_reviews"]
    assert small.json()["user_reviews"][0]["images"][0].startswith("/media/ab/")
    assert _raw_snapshot(paths) == raw


def test_review_http_original_images_keeps_order_limit_and_raw_data(delivery_api):
    api, paths = delivery_api
    original = api.get("/cards/42/reviews").json()
    raw = _raw_snapshot(paths)
    for url in SOURCES.values():
        _prepare(url, "thumb")
    small = api.get("/cards/42/reviews")
    assert small.status_code == 200, small.text
    expected = deepcopy(original)
    for review in expected:
        review["images"] = [_public(url, "thumb") for url in review["images"]]
    assert small.json() == expected
    assert api.get("/cards/42/reviews?original_images=true").json() == original
    assert api.get("/cards/42/reviews?limit=1&original_images=true").json() == original[:1]
    assert api.get("/cards/42/reviews?limit=1").json() == expected[:1]
    assert _raw_snapshot(paths) == raw


@pytest.mark.parametrize("suffix", ["", "/reviews"])
def test_original_images_query_validation_and_missing_card_are_unchanged(delivery_api, suffix):
    api, _ = delivery_api
    assert api.get(f"/cards/42{suffix}?original_images=invalid").status_code == 422
    normal = api.get(f"/cards/-1{suffix}")
    original = api.get(f"/cards/-1{suffix}?original_images=true")
    assert normal.status_code == original.status_code == 404
    assert normal.json() == original.json()


def test_original_images_is_optional_boolean_with_existing_response_schema(mounted_api):
    spec = mounted_api.get("/openapi.json").json()
    for route in ("/cards/{card_id}", "/cards/{card_id}/reviews"):
        operation = spec["paths"][route]["get"]
        flag = next(p for p in operation["parameters"] if p["name"] == "original_images")
        assert flag["in"] == "query" and not flag["required"]
        assert flag["schema"]["type"] == "boolean" and flag["schema"]["default"] is False
    schemas = spec["components"]["schemas"]
    for name in ("CardDetailOut", "Food", "Review"):
        assert not any("original" in key for key in schemas[name]["properties"])
    assert spec["paths"]["/cards/{card_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CardDetailOut"}
