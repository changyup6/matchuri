# -*- coding: utf-8 -*-
"""네이버 리뷰는 가게 화면에서만 카카오 리뷰와 합쳐진다.

`reviews.jsonl`(카카오)은 백테스트·트렌드·서빙 행렬이 사용자 기록으로 읽는 파일이라 손대지
않는다. 네이버 리뷰는 그 옆의 `reviews_naver.jsonl` 에 따로 있고, `store.load` 가 화면용
목록을 만들 때만 합친다.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from app import loader
from recsys import store


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _reset():
    store._mt.clear()
    for d in (store._stores, store._reviews, store._menus, store._photos):
        d.clear()


KAKAO = {"store_id": "s1", "review_id": 11, "rating": 4, "content": "카카오 리뷰 본문입니다",
         "date": "2026-08-01 12:00:00", "author_id": "k1"}
NAVER = {"store_id": "s1", "review_id": -5973374670193209, "source_review_id": "6a7d37008d6b3b33c6c23db1",
         "rating": 5, "rating_raw": 4.5, "content": "네이버 방문자 리뷰 본문입니다",
         "date": "2026-08-13 12:06:23", "photo_urls": ["https://example.com/n.jpg"],
         "strength_tags": ["맛"], "author_id": "naver:2eb529776c20", "source_platform": "naver"}


@pytest.fixture
def data(tmp_path, monkeypatch):
    def load(naver=None):
        _write(tmp_path / "stores.jsonl", [{"store_id": "s1", "name": "가게"}])
        _write(tmp_path / "reviews.jsonl", [KAKAO])
        _write(tmp_path / "menus.jsonl", [])
        _write(tmp_path / "photos.jsonl", [])
        if naver is not None:
            _write(tmp_path / "reviews_naver.jsonl", naver)
        monkeypatch.setattr(store, "DATA", tmp_path)
        for name in ("STORES", "REVIEWS", "MENUS", "PHOTOS"):
            monkeypatch.setattr(store, name, tmp_path / (name.lower() + ".jsonl"))
        _reset()
        store.load()
        return tmp_path
    yield load
    _reset()


def test_네이버_리뷰가_가게_화면에_출처와_함께_보인다(data):
    data(naver=[NAVER])
    rows = store.get_reviews("s1", limit=10)
    assert sorted(r["source_platform"] for r in rows) == ["kakao", "naver"]
    n = next(r for r in rows if r["source_platform"] == "naver")
    out = loader._review(n)                     # 응답 모델을 통과해야 한다
    assert out.source_platform == "naver"
    assert out.id == -5973374670193209          # 앱이 int 로 읽는다. 네이버는 음수, 카카오는 양수
    assert out.score == 5
    assert out.reviewer_id == "naver:2eb529776c20"


def test_네이버_파일이_없으면_카카오_리뷰만(data):
    data(naver=None)
    assert [r["source_platform"] for r in store.get_reviews("s1", limit=10)] == ["kakao"]


def test_출처가_없는_네이버_파일_줄을_카카오로_적지_않는다(data):
    data(naver=[{k: v for k, v in NAVER.items() if k != "source_platform"}])
    got = sorted((r["source_platform"] or "") for r in store.get_reviews("s1", limit=10))
    assert got == ["", "kakao"]


def test_네이버_파일만_바뀌어도_다시_읽는다(data):
    d = data(naver=[])
    assert len(store.get_reviews("s1", limit=10)) == 1
    _write(d / "reviews_naver.jsonl", [NAVER])
    later = time.time() + 5                     # mtime 해상도가 거친 파일시스템에서도 달라지게
    os.utime(d / "reviews_naver.jsonl", (later, later))
    store.load()
    assert len(store.get_reviews("s1", limit=10)) == 2


def test_REVIEWS_만_옮긴_테스트에_실데이터_네이버가_섞이지_않는다(data, tmp_path):
    data(naver=None)
    # 네이버 파일은 reviews.jsonl 옆의 것만 읽는다 — 기본 위치(BE/data/kakao)는 보지 않는다
    assert all(sid == "s1" for sid in store._reviews)


def test_정수_id_가_없거나_가게_안에서_겹치는_리뷰는_싣지_않는다(data):
    """앱이 id 를 int 로 읽고 id 로 리뷰를 찾는다 — 이런 줄이 하나라도 나가면 카드 목록이 깨진다."""
    bad = [
        {**NAVER, "review_id": None, "content": "id 가 null"},
        {**NAVER, "review_id": "6a7d37008d6b3b33c6c23db1", "content": "id 가 16진수 문자열"},
        {**NAVER, "review_id": True, "content": "id 가 참거짓"},
        {**NAVER, "content": "같은 가게에서 id 가 겹친다"},
    ]
    data(naver=[NAVER, *bad])
    rows = store.get_reviews("s1", limit=10)
    assert [r["content"] for r in rows if r["source_platform"] == "naver"] == [NAVER["content"]]
    assert all(isinstance(loader._review(r).id, int) for r in rows)


def test_숫자_문자열_id_는_정수로_바꿔_싣는다(data):
    data(naver=[{**NAVER, "review_id": "-42"}])
    n = next(r for r in store.get_reviews("s1", limit=10) if r["source_platform"] == "naver")
    assert loader._review(n).id == -42



def test_리뷰_칩은_리뷰_줄의_tags_를_먼저_쓴다(data):
    """리뷰마다 단 필터 칸 태그(`tags`)가 있으면 그것이 칩이다 — 필터·카드 칩과 같은 글자.
    '맛' 만 받은 리뷰는 `tags: []` 이고 칩이 없어야 한다(`strength_tags` 로 되돌아가지 않는다)."""
    tagged = {**NAVER, "tags": ["가성비", "혼밥"], "strength_tags": ["맛", "가성비"]}
    only_taste = {**NAVER, "review_id": -7, "content": "맛만 받은 리뷰", "tags": [], "strength_tags": ["맛"]}
    data(naver=[tagged, only_taste])
    rows = {r["review_id"]: r for r in store.get_reviews("s1", limit=10)}
    assert loader._review(rows[NAVER["review_id"]]).tags == ["가성비", "혼밥"]
    assert loader._review(rows[-7]).tags == []


def test_tags_가_없는_옛_데이터는_수집_원본_강점을_칩으로(data):
    data(naver=[NAVER])                          # tags 필드가 없다
    n = next(r for r in store.get_reviews("s1", limit=10) if r["source_platform"] == "naver")
    assert loader._review(n).tags == ["맛"]
