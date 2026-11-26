# -*- coding: utf-8 -*-
"""분석 로더가 서버와 같은 추천 후보·가격 특성 입력을 유지한다.

대표 사진은 `hero: true` 가 먼저이고, 모델 가격 입력은 기존 음식·인기·추천
우선순위를 유지한다. 화면의 원본 menu_index 정렬과 분리해야 후보 풀과 기존
임베딩 산출물의 가게 목록이 일치한다.
"""
from __future__ import annotations

import json

from analysis.backtest.data import load_dataset


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _dataset(tmp_path, menus=(), photos=()):
    _write(tmp_path / "stores.jsonl", [{"store_id": "s1", "name": "가게"}])
    _write(tmp_path / "reviews.jsonl", [])
    _write(tmp_path / "menus.jsonl", [{"store_id": "s1", **m} for m in menus])
    _write(tmp_path / "photos.jsonl", [{"store_id": "s1", **p} for p in photos])
    return load_dataset(tmp_path)


def test_대표_표시가_있으면_그_사진의_음식_여부를_본다(tmp_path):
    ds = _dataset(tmp_path, photos=[{"url": "poster", "tag": "MYSTORE", "food": False},
                                    {"url": "food", "tag": "FOOD", "food": True, "hero": True}])
    assert ds.hero_food["s1"] is True


def test_대표_표시가_없으면_태그_순서의_첫_사진이다(tmp_path):
    ds = _dataset(tmp_path, photos=[{"url": "food", "tag": "FOOD", "food": True},
                                    {"url": "poster", "tag": "MYSTORE", "food": False}])
    assert ds.hero_food["s1"] is False


def test_가격_특성은_기존_인기_순위를_따른다(tmp_path):
    ds = _dataset(tmp_path, menus=[
        {"menu_name": "추천", "menu_index": 0, "is_recommend": True, "price": 1000, "popular_rank": 3},
        {"menu_name": "인기", "menu_index": 5, "price": 9000, "popular_rank": 0},
        {"menu_name": "둘째", "menu_index": 3, "price": 8000, "popular_rank": 1},
        {"menu_name": "셋째", "menu_index": 4, "price": 7000, "popular_rank": 2},
    ])
    assert ds.card_prices["s1"] == [9000, 8000, 7000]


def test_인기_순위가_없으면_가격_특성은_기존_추천_순서를_유지한다(tmp_path):
    ds = _dataset(tmp_path, menus=[
        {"menu_name": "가", "menu_index": 1, "price": 1000},
        {"menu_name": "나", "menu_index": 0, "price": 2000},
        {"menu_name": "다", "menu_index": 2, "price": 3000, "is_recommend": True},
    ])
    assert ds.card_prices["s1"] == [3000, 2000, 1000]


def test_원본_앞쪽의_비음식_항목이_후보_가격_입력을_밀어내지_않는다(tmp_path):
    ds = _dataset(tmp_path, menus=[
        {"menu_name": "메뉴판", "menu_index": 0, "is_dish": False,
         "popular_rank": 0, "is_recommend": True, "price": None},
        {"menu_name": "첫 요리", "menu_index": 1, "price": 9000},
        {"menu_name": "둘째 요리", "menu_index": 2, "price": 8000},
        {"menu_name": "셋째 요리", "menu_index": 3, "price": 7000},
    ])
    assert ds.card_prices["s1"] == [9000, 8000, 7000]
