# -*- coding: utf-8 -*-
"""사진은 hero·태그 우선순위, 메뉴는 원본 menu_index 순서를 따른다.

실데이터 없이 몇 줄짜리 jsonl 로 원본 순서와 API 응답 계약을 검증한다.
"""
from __future__ import annotations

import json

import pytest

from recsys import store


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _reset():
    store._mt.clear()
    for d in (store._stores, store._reviews, store._menus, store._photos):
        d.clear()


@pytest.fixture
def data(tmp_path, monkeypatch):
    """임시 폴더의 jsonl 을 읽게 바꾸고, 끝나면 캐시를 비워 다음 테스트가 다시 읽게 한다."""
    def load(menus=(), photos=(), reviews=()):
        _write(tmp_path / "stores.jsonl", [{"store_id": "s1", "name": "가게"}])
        _write(tmp_path / "reviews.jsonl", list(reviews))
        _write(tmp_path / "menus.jsonl", list(menus))
        _write(tmp_path / "photos.jsonl", list(photos))
        monkeypatch.setattr(store, "DATA", tmp_path)
        for name in ("STORES", "REVIEWS", "MENUS", "PHOTOS"):
            monkeypatch.setattr(store, name, tmp_path / (name.lower() + ".jsonl"))
        _reset()
        store.load()
        return store
    yield load
    _reset()


def menu(name, **kw):
    return {"store_id": "s1", "menu_name": name, **kw}


def photo(url, tag, **kw):
    return {"store_id": "s1", "url": url, "tag": tag, **kw}


def names(st):
    return [m["menu_name"] for m in st.get_menus("s1")]


@pytest.mark.parametrize('fields', [
    {'photo_urls': ['https://example.com/a.jpg']},
    {'photos': [{'url': 'https://example.com/a.jpg'}]},
    {'photos': [], 'photo_urls': ['https://example.com/a.jpg']},
])
def test_review_images_survive_raw_and_runtime_formats(data, fields):
    from app.loader import _review
    st = data(reviews=[{'store_id': 's1', 'review_id': 1, **fields}])
    assert _review(st.get_reviews('s1')[0]).images == ['https://example.com/a.jpg']


# ────────────────────────────────────────────── 메뉴
def test_추천_표시와_관계없이_원본_인덱스_순이다(data):
    st = data(menus=[menu("가", menu_index=1), menu("나", menu_index=0),
                     menu("다", menu_index=2, is_recommend=True)])
    assert names(st) == ["나", "가", "다"]


def test_인기_순위가_원본_순서를_덮어쓰지_않는다(data):
    st = data(menus=[menu("추천", menu_index=0, is_recommend=True, popular_rank=2),
                     menu("인기", menu_index=5, popular_rank=0),
                     menu("둘째", menu_index=3, popular_rank=1),
                     menu("순위없음", menu_index=1)])
    assert names(st) == ["추천", "순위없음", "둘째", "인기"]


def test_요리가_아닌_줄도_원본_위치를_유지한다(data):
    st = data(menus=[menu("김치찌개", menu_index=1, popular_rank=0),
                     menu("메뉴판", menu_index=0, is_dish=False, popular_rank=1)])
    assert names(st) == ["메뉴판", "김치찌개"]


@pytest.mark.parametrize("bad", [True, False, "0", -1, 1.5, None])
def test_잘못된_원본_인덱스는_끝에서_파일_순서를_유지한다(data, bad):
    st = data(menus=[menu("잘못된순번", menu_index=bad), menu("둘째", menu_index=2),
                     menu("순번없음"), menu("첫째", menu_index=0)])
    assert names(st) == ["첫째", "둘째", "잘못된순번", "순번없음"]


def test_같은_인덱스는_파일_순서를_유지하고_제한은_정렬_후_적용한다(data):
    st = data(menus=[menu("먼저", menu_index=4),
                     menu("나중", menu_index=4, popular_rank=0, is_recommend=True),
                     menu("첫째", menu_index=0)])
    assert [m["menu_name"] for m in st.get_menus("s1", limit=2)] == ["첫째", "먼저"]


def test_상세_메뉴는_원본_순서이고_id는_응답_순번이다(data, monkeypatch):
    from app import loader
    data(menus=[menu("추천", menu_index=7, is_recommend=True, price=20000),
                menu("원본첫째", menu_index=2, price=10000)])
    monkeypatch.setattr(loader, "load_all", lambda: None)
    monkeypatch.setattr(loader, "store_id_by_card", {42: "s1"})
    result = loader.get_menus(42)
    assert [(m.id, m.name, m.is_recommend) for m in result] == [
        (0, "원본첫째", False), (1, "추천", True)]


def test_화면_메뉴는_원본_순서여도_기존_후보_가격_기준은_유지한다(data):
    from recsys import swipe
    st = data(menus=[menu("원본첫째", menu_index=0, price=None),
                     menu("원본둘째", menu_index=1, price=None),
                     menu("원본셋째", menu_index=2, price=None),
                     menu("인기메뉴", menu_index=3, price=15000, popular_rank=0)])
    assert [m["menu_name"] for m in swipe._card_menus("s1")] == [
        "원본첫째", "원본둘째", "원본셋째"]
    assert st.get_feature_menus("s1", limit=1)[0]["menu_name"] == "인기메뉴"
    assert swipe._card_price("s1") is True
    assert names(st) == ["원본첫째", "원본둘째", "원본셋째", "인기메뉴"]


# ────────────────────────────────────────────── 사진
def test_대표_표시가_없으면_가게_제공_사진이_먼저다(data):
    st = data(photos=[photo("food", "FOOD", food=True), photo("poster", "MYSTORE", food=True)])
    assert st.hero_photo("s1")["url"] == "poster"


def test_대표_표시가_태그_순서보다_먼저다(data):
    st = data(photos=[photo("poster", "MYSTORE", food=False),
                      photo("food", "FOOD", food=True, hero=True)])
    assert st.hero_photo("s1")["url"] == "food"
    assert st.get_photos("s1", limit=2) == ["food", "poster"]


@pytest.mark.parametrize("bad", ["true", 1, "yes"])
def test_대표_표시는_True_만_인정한다(data, bad):
    st = data(photos=[photo("poster", "MYSTORE"), photo("food", "FOOD", hero=bad)])
    assert st.hero_photo("s1")["url"] == "poster"


def test_known_poster_hero_cannot_beat_a_food_photo(data):
    st = data(photos=[photo("poster", "MYSTORE", food=False, hero=True),
                      photo("food", "FOOD", food=True)])
    assert st.hero_photo("s1", for_card=True)["url"] == "food"
    assert st.hero_photo("s1")["url"] == "poster"  # pool/catalogue unchanged


def test_menu_only_store_has_no_representative_image(data):
    st = data(photos=[photo("menu", "MENU", hero=True)])
    assert st.hero_photo("s1", for_card=True) is None
    assert st.get_photos("s1") == ["menu"]  # original gallery retained


def test_non_food_store_interior_is_a_valid_fallback(data):
    st = data(photos=[photo("poster", "MYSTORE", food=False),
                      photo("interior", "INDOOR", food=False)])
    assert st.hero_photo("s1", for_card=True)["url"] == "interior"


def test_unclassified_official_hero_does_not_hide_known_food(data):
    st = data(photos=[photo("unknown", "MYSTORE", hero=True),
                      photo("food", "FOOD", food=True)])
    assert st.hero_photo("s1", for_card=True)["url"] == "food"
    assert st.hero_photo("s1")["url"] == "unknown"
