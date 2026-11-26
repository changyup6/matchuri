# -*- coding: utf-8 -*-
"""리뷰 다중 벡터 — **누수 규칙**, 선택 변종, NaN, 캐시 지문.

임베딩 산출물은 git 에 없으므로 손으로 만든 2차원 단위벡터와 가짜 인덱스로 돈다.
맨 아래에 실데이터가 있을 때만 도는 테스트를 하나 더 둔다 — 이 저장소에서 실제로
쓰인 review_id 에 평가 사용자의 것이 하나도 없음을 직접 확인하는 것이다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from analysis.content.menus import maxsim_directed, similarity_matrix
from analysis.content.reviews import (MAX_REVIEWS, ReviewSpace, Variant, collect_rows,
                                      store_mean_vectors)

ROOT = Path(__file__).resolve().parents[3]

ANGLES = [0.0, 90.0, 60.0, 180.0, 30.0, 120.0]
VEC = np.array([[math.cos(math.radians(a)), math.sin(math.radians(a))] for a in ANGLES],
               dtype=np.float32)

LONGEST = Variant("longest", 2, "longest")
RECENT = Variant("recent", 2, "recent")
RANDOM = Variant("random", 2, "random")


def index_row(row, rid, sid, author, rating=5):
    return {"row": row, "review_id": rid, "store_id": sid, "author_id": author,
            "rating": rating}


#: (row, review_id, store_id, author) — 저자 `평가자` 가 평가 사용자다.
INDEX = [
    index_row(0, 100, "s1", "일반1"),
    index_row(1, 101, "s1", "평가자"),
    index_row(2, 102, "s1", "일반2"),
    index_row(3, 103, "s2", "일반3"),
    index_row(4, 104, "s2", "평가자"),
    index_row(5, 105, "풀밖", "일반4"),
]
#: review_id -> (날짜, 길이, 스니펫)
META = {
    100: ("2026-01-01", 30, "짧은 리뷰"),
    101: ("2026-05-01", 99, "평가 사용자의 아주 긴 리뷰"),
    102: ("2026-03-01", 50, "중간 리뷰"),
    103: ("2026-02-01", 10, "s2 리뷰"),
    104: ("2026-06-01", 80, "평가 사용자 s2 리뷰"),
    105: ("2026-01-01", 40, "풀 밖 가게"),
}
POOL = ["s1", "s2", "리뷰없음"]


def make_space(excluded=("평가자",), seed=0) -> ReviewSpace:
    rows = collect_rows(INDEX, POOL, set(excluded), META)
    return ReviewSpace(POOL, rows, VEC, excluded_authors=set(excluded), seed=seed)


# ────────────────────────────────────────────── 누수 규칙 (PLAN 1절)
def test_평가_사용자의_리뷰는_후보에서_아예_빠진다():
    space = make_space()
    ids = {r.review_id for sid in POOL for r in space.candidates(sid)}
    assert 101 not in ids and 104 not in ids
    assert ids == {100, 102, 103}


def test_선택된_review_id에_평가_사용자의_것이_하나도_없다():
    """이 Step 의 누수 규칙 그 자체 — 어떤 변종을 써도 성립해야 한다."""
    space = make_space()
    leaked = {101, 104}
    for variant in (LONGEST, RECENT, RANDOM):
        assert space.used_review_ids(variant) & leaked == set()


def test_제외하지_않으면_평가_사용자_리뷰가_실제로_들어온다():
    """위 테스트가 '원래 없던 것' 을 확인하는 게 아님을 보이는 대조군."""
    space = make_space(excluded=())
    assert space.used_review_ids(LONGEST) & {101, 104}


def test_후보_풀_밖_가게는_들어오지_않는다():
    space = make_space()
    assert "풀밖" not in space.rows_by_store


# ────────────────────────────────────────────── 선택 변종
def test_longest는_본문이_긴_순():
    space = make_space()
    assert [r.review_id for r in space.selection(LONGEST, "s1")] == [102, 100]


def test_recent는_최근_날짜_순():
    space = make_space()
    assert [r.review_id for r in space.selection(RECENT, "s1")] == [102, 100]
    # s1 은 두 순서가 같으므로 날짜만 뒤집은 경우로 한 번 더 본다.
    meta = dict(META) | {100: ("2026-09-01", 30, "짧은 리뷰")}
    rows = collect_rows(INDEX, POOL, {"평가자"}, meta)
    other = ReviewSpace(POOL, rows, VEC, excluded_authors={"평가자"})
    assert [r.review_id for r in other.selection(RECENT, "s1")] == [100, 102]
    assert [r.review_id for r in other.selection(LONGEST, "s1")] == [102, 100]


def test_random은_씨앗이_같으면_같은_선택():
    a, b = make_space(seed=7), make_space(seed=7)
    assert ([r.review_id for r in a.selection(RANDOM, "s1")]
            == [r.review_id for r in b.selection(RANDOM, "s1")])


def test_K를_넘게_고르지_않는다():
    space = make_space()
    one = Variant("one", 1, "longest")
    assert len(space.selection(one, "s1")) == 1
    assert len(space.selection(LONGEST, "s1")) == 2


def test_리뷰가_없는_가게는_빈_선택():
    space = make_space()
    assert space.selection(LONGEST, "리뷰없음") == []
    assert space.counts(LONGEST).tolist() == [2, 1, 0]


def test_usable_stats는_0과_K미만과_K이상을_센다():
    space = make_space()
    st = space.usable_stats()
    assert st["stores"] == 3 and st["zero"] == 1
    assert st["under_k"] == 2 and st["at_least_k"] == 0     # K=12 는 아무도 못 채운다
    assert st["max"] == 2 and st["total_used"] == 3
    assert MAX_REVIEWS == 12


# ────────────────────────────────────────────── MaxSim 과 NaN
def test_MaxSim은_메뉴와_같은_함수로_계산된다():
    space = make_space()
    packed = space.packed(LONGEST)
    sim = similarity_matrix(packed)
    want = (maxsim_directed(packed, 0, 1) + maxsim_directed(packed, 1, 0)) / 2
    assert sim[0, 1] == pytest.approx(want, abs=1e-6)
    assert sim[0, 0] == pytest.approx(1.0, abs=1e-6)


def test_리뷰가_없는_가게는_행과_열이_NaN():
    space = make_space()
    sim = similarity_matrix(space.packed(LONGEST))
    assert np.isnan(sim[2]).all() and np.isnan(sim[:, 2]).all()
    assert np.isfinite(sim[0, 1])


def test_리뷰_가중치는_전부_1():
    """메뉴는 IDF 가중을 쓰지만 리뷰는 무가중이다(문장에 df 를 정의할 수 없다)."""
    space = make_space()
    assert np.all(space.packed(LONGEST).weights == 1.0)


def test_단일_벡터_대조군은_평균을_정규화한_것():
    space = make_space()
    V = store_mean_vectors(space, LONGEST)
    picked = space.selection(LONGEST, "s1")
    want = VEC[[r.row for r in picked]].mean(axis=0)
    want = want / np.linalg.norm(want)
    np.testing.assert_allclose(V[0], want, atol=1e-6)
    assert np.isnan(V[2]).all()                    # 리뷰가 없으면 NaN 행


# ────────────────────────────────────────────── 캐시 지문
def test_지문은_제외집합과_변종과_임베딩에_달려_있다():
    a = make_space()
    assert a.fingerprint(LONGEST) == make_space().fingerprint(LONGEST)
    assert a.fingerprint(LONGEST) != a.fingerprint(RECENT)
    assert a.fingerprint(LONGEST) != make_space(excluded=()).fingerprint(LONGEST)
    b = make_space()
    b.manifest_sha = "다른임베딩"
    assert a.fingerprint(LONGEST) != b.fingerprint(LONGEST)


# ────────────────────────────────────────────── 실데이터 (있을 때만)
REAL_INDEX = ROOT / "data" / "vectors" / "reviews.index.jsonl"


@pytest.mark.slow
@pytest.mark.skipif(not REAL_INDEX.exists(),
                    reason="리뷰 임베딩 산출물이 없다 (git 에 넣지 않는다)")
def test_실데이터에서도_평가_사용자_리뷰는_한_건도_쓰이지_않는다(real_pool):
    from analysis.backtest import cache
    from analysis.content import reviews as review_content

    ds, pool = real_pool
    splits, _ = cache.load_or_build(ds, pool)
    eval_users = {s.user_id for s in splits}
    with REAL_INDEX.open(encoding="utf-8") as fh:
        index = [json.loads(line) for line in fh if line.strip()]
    leaked = {int(r["review_id"]) for r in index
              if str(r.get("author_id")) in eval_users}
    assert leaked, "평가 사용자의 리뷰가 코퍼스에 실제로 있어야 이 테스트가 의미가 있다"

    space = review_content.ReviewSpace.build(pool.ids, eval_users)
    for variant in review_content.VARIANTS.values():
        assert space.used_review_ids(variant) & leaked == set()
