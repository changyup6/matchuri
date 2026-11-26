# -*- coding: utf-8 -*-
"""사진 modality (Step 3b) — 사진 고르기 규칙 · NaN 규칙 · 난수 대조군.

임베딩 산출물(`data/vectors/photo_*.npy`)도 캐시(`data/photos_cache/`)도 git 에 없다.
그러니 여기서 실데이터를 요구하면 안 된다 — 손으로 만든 2차원 단위벡터와 몇 줄짜리
사진 목록으로 규칙을 직접 찌른다(`test_store_text.py` / `test_menu_content.py` 와 같은 방식).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from analysis.backtest.scorers_menu import content_score
from analysis.content.menus import Packed
from analysis.content.photos import (PhotoSpace, build_similarity, scorer_name,
                                     single_similarity)
from analysis.photos.download import PhotoRow, cache_path, select_for_store, select_photos


# ────────────────────────────────────────────── 고르기 규칙
def row(line, sid, tag, type_, url=None) -> PhotoRow:
    return PhotoRow(line=line, store_id=sid, tag=tag, type=type_,
                    url=url or f"http://x/{line}")


def test_음식은_type_우선순위대로_고른다():
    """MYSTORE/VENDOR → KMAPREVIEW → BLOG. 등장 순서보다 type 이 먼저다."""
    rows = [row(0, "s", "FOOD", "BLOG"),
            row(1, "s", "FOOD", "KMAPREVIEW"),
            row(2, "s", "FOOD", "MYSTORE"),
            row(3, "s", "FOOD", "VENDOR")]
    food, _ = select_for_store(rows, max_food=4, max_space=3)
    assert [r.type for r in food] == ["MYSTORE", "VENDOR", "KMAPREVIEW", "BLOG"]


def test_같은_우선순위_안에서는_등장_순서다():
    rows = [row(7, "s", "FOOD", "BLOG"), row(2, "s", "FOOD", "BLOG"),
            row(5, "s", "FOOD", "BLOG")]
    food, _ = select_for_store(rows, max_food=4, max_space=3)
    assert [r.line for r in food] == [2, 5, 7]        # line 오름차순


def test_음식은_4장_공간은_3장에서_잘린다():
    rows = ([row(i, "s", "FOOD", "BLOG") for i in range(10)]
            + [row(100 + i, "s", "INDOOR", "BLOG") for i in range(10)])
    food, space = select_for_store(rows, max_food=4, max_space=3)
    assert len(food) == 4 and len(space) == 3


def test_공간은_INDOOR_가_OUTDOOR_보다_먼저다():
    rows = [row(0, "s", "OUTDOOR", "BLOG"), row(1, "s", "INDOOR", "BLOG"),
            row(2, "s", "OUTDOOR", "BLOG")]
    _, space = select_for_store(rows, max_food=4, max_space=3)
    assert [r.tag for r in space] == ["INDOOR", "OUTDOOR", "OUTDOOR"]


def test_음식과_공간이_아닌_태그는_버린다():
    """MENU·MYSTORE(tag) 는 음식도 공간도 아니다 — 메뉴판·간판 사진이다."""
    rows = [row(0, "s", "MENU", "MENU"), row(1, "s", "MYSTORE", "MYSTORE"),
            row(2, "s", "FOOD", "BLOG")]
    food, space = select_for_store(rows, max_food=4, max_space=3)
    assert [r.line for r in food] == [2] and space == []


def test_같은_url_은_한_번만():
    rows = [row(0, "s", "FOOD", "BLOG", url="http://x/dup"),
            row(1, "s", "FOOD", "BLOG", url="http://x/dup"),
            row(2, "s", "FOOD", "BLOG", url="http://x/other")]
    food, _ = select_for_store(rows, max_food=4, max_space=3)
    assert [r.url for r in food] == ["http://x/dup", "http://x/other"]


def test_풀_밖_가게는_고르지_않고_풀_순서를_따른다():
    rows = [row(0, "밖", "FOOD", "BLOG"), row(1, "B", "FOOD", "BLOG"),
            row(2, "A", "FOOD", "BLOG")]
    picked = select_photos(rows, ["A", "B"])
    assert [r.store_id for r in picked] == ["A", "B"]      # 풀 순서(A 먼저)


def test_캐시_경로는_url_의_sha1_이다():
    p = cache_path("http://x/1", cache_dir="/tmp/c")
    assert p.name == "c24252f3b3ae71b9084cac9cfb6fa9765baf79b8.jpg"
    assert cache_path("http://x/1") != cache_path("http://x/2")


# ────────────────────────────────────────────── 손으로 만든 사진 공간
def unit(deg: float) -> np.ndarray:
    return np.array([math.cos(math.radians(deg)), math.sin(math.radians(deg))],
                    dtype=np.float32)


def make_space(store_angles: dict[str, list[float]], *, kind: str = "food",
               seed: int = 0) -> PhotoSpace:
    """가게 -> 사진 각도 목록. 빈 목록이면 사진이 없는 가게다."""
    ids = list(store_angles)
    counts = np.array([len(store_angles[s]) for s in ids], dtype=np.int64)
    starts = np.zeros(len(counts), dtype=np.int64)
    np.cumsum(counts[:-1], out=starts[1:])
    vecs = np.array([unit(d) for s in ids for d in store_angles[s]],
                    dtype=np.float32).reshape(-1, 2)
    packed = Packed(starts=starts, counts=counts, vecs=vecs,
                    weights=np.ones(len(vecs), dtype=np.float32),
                    names=[f"http://p/{s}/{i}" for s in ids
                           for i in range(len(store_angles[s]))])
    return PhotoSpace(kind=kind, store_ids=ids,
                      vecs=PhotoSpace._store_vectors(packed, 2), packed=packed,
                      tags=[["FOOD"] * len(store_angles[s]) for s in ids], seed=seed)


def test_단일_벡터는_그_가게_사진의_평균이다():
    space = make_space({"A": [0.0, 90.0]})            # 평균 방향 = 45°
    np.testing.assert_allclose(space.vecs[0], unit(45.0), atol=1e-6)
    assert float(np.linalg.norm(space.vecs[0])) == pytest.approx(1.0, abs=1e-6)


def test_유사도는_손으로_계산한_코사인이다():
    space = make_space({"A": [0.0], "B": [90.0], "C": [60.0]})
    sim = single_similarity(space.vecs)
    assert sim[0, 1] == pytest.approx(0.0, abs=1e-6)
    assert sim[0, 2] == pytest.approx(math.cos(math.radians(60)), abs=1e-6)
    np.testing.assert_allclose(np.diag(sim), 1.0, atol=1e-6)
    np.testing.assert_allclose(sim, sim.T, atol=1e-7)


# ────────────────────────────────────────────── NaN 규칙 (메뉴·리뷰·요약문과 같다)
def test_사진이_없는_가게는_행과_열이_전부_NaN():
    space = make_space({"A": [0.0], "없음": [], "C": [60.0]})
    for repr_name in ("single", "maxsim"):
        sim = build_similarity(space, repr_name)
        assert np.isnan(sim[1]).all() and np.isnan(sim[:, 1]).all(), repr_name
        assert np.isfinite(sim[0, 2]), repr_name


def test_사진이_없는_후보는_0_이_아니라_중립값으로_채운다():
    """0 으로 채우면 '아무와도 안 닮은 가게' 가 되어 구조적으로 밀려난다."""
    space = make_space({"A": [0.0], "B": [60.0], "없음": [], "C": [90.0]})
    sim = single_similarity(space.vecs)
    pos = {s: i for i, s in enumerate(space.store_ids)}
    score = content_score(sim, pos, [("A", True)], "max", 0.0)
    finite = [score[pos[s]] for s in ("A", "B", "C")]
    assert score[pos["없음"]] == pytest.approx(float(np.mean(finite)))
    assert score[pos["없음"]] > score[pos["C"]]        # 90° 인 가게보다는 위다


def test_사진이_없는_이력_가게는_집계에서_빠진다():
    space = make_space({"A": [0.0], "없음": [], "C": [90.0]})
    sim = single_similarity(space.vecs)
    pos = {s: i for i, s in enumerate(space.store_ids)}
    only_a = content_score(sim, pos, [("A", True)], "max", 0.0)
    with_missing = content_score(sim, pos, [("A", True), ("없음", True)], "max", 0.0)
    np.testing.assert_allclose(only_a, with_missing)


def test_커버리지_통계():
    space = make_space({"A": [0.0, 10.0], "없음": [], "C": [90.0]})
    stats = space.coverage_stats()
    assert stats["stores"] == 3 and stats["with_photo"] == 2
    assert stats["photos"] == 3
    assert stats["coverage"] == pytest.approx(2 / 3)
    assert stats["hist"]["0"] == 1 and stats["hist"]["2"] == 1


# ────────────────────────────────────────────── 난수 대조군 (PLAN 7절 결정 1번)
def test_단일벡터_대조군은_커버리지_구조를_그대로_둔다():
    space = make_space({"A": [0.0], "없음": [], "C": [90.0]})
    null = space.null_vectors()
    assert np.isnan(null[1]).all()                       # 없는 가게는 그대로 없다
    assert np.isfinite(null[0]).all() and np.isfinite(null[2]).all()
    np.testing.assert_allclose(np.linalg.norm(null[[0, 2]], axis=1), 1.0, atol=1e-6)


def test_대조군은_내용을_하나도_안_남긴다():
    """진짜 벡터와 난수 벡터의 유사도가 같으면 대조군이 대조군이 아니다."""
    space = make_space({"A": [0.0], "B": [1.0], "C": [90.0]})
    real = build_similarity(space, "single")
    null = build_similarity(space, "single_null")
    assert real[0, 1] > 0.999                            # 1° 차이 = 거의 같은 가게
    assert abs(float(null[0, 1])) < 0.999                # 난수는 그럴 이유가 없다


def test_대조군은_seed_에_결정적이다():
    a = make_space({"A": [0.0], "C": [90.0]}, seed=7).null_vectors()
    b = make_space({"A": [0.0], "C": [90.0]}, seed=7).null_vectors()
    c = make_space({"A": [0.0], "C": [90.0]}, seed=8).null_vectors()
    np.testing.assert_allclose(a, b)
    assert not np.allclose(a, c)


def test_MaxSim_대조군은_개수_구조를_그대로_둔다():
    space = make_space({"A": [0.0, 30.0, 60.0], "B": [10.0], "없음": []})
    null = space.null_packed()
    np.testing.assert_array_equal(null.counts, space.packed.counts)
    np.testing.assert_array_equal(null.starts, space.packed.starts)
    np.testing.assert_allclose(np.linalg.norm(null.vecs, axis=1), 1.0, atol=1e-6)


def test_MaxSim_대조군은_집합이_클수록_점수가_높다():
    """이 Step 이 대조군을 두는 이유 그 자체 — `max_{b∈B}` 는 |B| 에 단조증가한다.

    사진 수는 곧 인기이므로, 대조군이 이기면 그 modality 의 AUC 는 내용의 증거가 아니다.
    """
    rng = np.random.default_rng(0)
    many = list(rng.uniform(0, 360, size=8))
    space = make_space({"기준": [0.0], "사진많음": many, "사진하나": [123.0]})
    null = build_similarity(space, "maxsim_null")
    assert float(null[0, 1]) > float(null[0, 2])


# ────────────────────────────────────────────── 이름 규칙
def test_모델_이름_규칙():
    assert scorer_name("food", "single", "max") == "photo_food_max"
    assert scorer_name("food", "single_null", "max") == "photo_food_null_max"
    assert scorer_name("space", "maxsim", "mean") == "photo_space_maxsim_mean"
    assert (scorer_name("space", "maxsim_null", "lse01")
            == "photo_space_maxsim_null_lse01")


def test_모델이_레지스트리에_등록됐다():
    from analysis.backtest import scorers

    for name in ("photo_food_max", "photo_food_null_max", "photo_space_mean",
                 "photo_space_maxsim_lse01", "photo_food_maxsim_null_mean"):
        assert scorers.exists(name), name
