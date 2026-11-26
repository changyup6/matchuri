# -*- coding: utf-8 -*-
"""메뉴 다중 벡터 — MaxSim 수식·IDF·NaN 처리·집계 변종.

손으로 답을 낼 수 있는 2차원 단위벡터로 검증한다. 실데이터가 없어도 도는 테스트다
(임베딩 산출물은 git 에 없으므로 여기서 실데이터를 요구하면 안 된다).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from analysis.backtest.scorers_menu import aggregate, content_score, zscore_on
from analysis.content.menus import (MenuSpace, Variant, best_menu_pair,
                                    maxsim_directed, mean_vectors,
                                    random_unit_packed, similarity_matrix)

# 2차원 단위벡터 4개. 각도로 답을 손으로 낼 수 있다.
#   0°(A), 90°(B), 60°(C), 180°(D)
ANGLES = [0.0, 90.0, 60.0, 180.0]
VEC = np.array([[math.cos(math.radians(a)), math.sin(math.radians(a))]
                for a in ANGLES], dtype=np.float32)
NAMES = ["a0", "b90", "c60", "d180"]
INDEX = [{"row": i, "text": n} for i, n in enumerate(NAMES)]


def make_space(names_by_store: dict[str, list[str]], store_ids=None) -> MenuSpace:
    ids = list(store_ids or names_by_store)
    return MenuSpace(ids, names_by_store, INDEX, VEC)


UNW = Variant("unw", 8, False)
IDF = Variant("idf", 8, True)


# ────────────────────────────────────────────── IDF
def test_IDF는_풀_가게_문서빈도로_계산한다():
    space = make_space({"s1": ["a0", "b90"], "s2": ["a0"], "s3": ["a0", "c60"]})
    assert space.df["a0"] == 3 and space.df["b90"] == 1
    # S_pool = 3 -> 모든 가게에 있는 이름은 idf 0, 한 곳뿐이면 log 3
    assert space.idf["a0"] == pytest.approx(0.0)
    assert space.idf["b90"] == pytest.approx(math.log(3))


def test_IDF는_풀_밖_가게를_세지_않는다():
    """가중치는 '후보 풀 안에서의 변별력' 이어야 한다."""
    names = {"s1": ["a0"], "s2": ["a0"], "밖": ["a0", "b90"]}
    space = make_space(names, store_ids=["s1", "s2"])
    assert space.df["a0"] == 2 and "b90" not in space.df


def test_범용_메뉴_목록은_df_내림차순():
    space = make_space({"s1": ["a0", "b90"], "s2": ["a0"], "s3": ["a0", "b90"]})
    assert [n for n, _, _ in space.generic_names(2)] == ["a0", "b90"]


def test_IDF가_전부_0이면_무가중으로_되돌린다():
    """모든 가게가 같은 메뉴 하나뿐이면 가중치 합이 0 이 되어 나눗셈이 깨진다."""
    space = make_space({"s1": ["a0"], "s2": ["a0"]})
    packed = space.packed(IDF)
    assert packed.weights.tolist() == [1.0, 1.0]


def test_K는_menu_index_순으로_앞에서_자른다():
    space = make_space({"s1": ["a0", "b90", "c60", "d180"]})
    assert space.selection(Variant("k2", 2, False), "s1") == ["a0", "b90"]


def test_벡터가_없는_메뉴명은_건너뛴다():
    space = make_space({"s1": ["없는메뉴", "a0", "b90"]})
    assert space.selection(Variant("k2", 2, False), "s1") == ["a0", "b90"]


# ────────────────────────────────────────────── MaxSim
def test_MaxSim은_손으로_계산한_값과_같다():
    # A = {0°, 90°}, B = {60°}
    #   MaxSim(A->B) = (cos60 + cos30)/2 = (0.5 + 0.8660)/2
    #   MaxSim(B->A) = max(cos60, cos30) = 0.8660
    space = make_space({"A": ["a0", "b90"], "B": ["c60"]})
    packed = space.packed(UNW)
    ab = (math.cos(math.radians(60)) + math.cos(math.radians(30))) / 2
    ba = math.cos(math.radians(30))
    assert maxsim_directed(packed, 0, 1) == pytest.approx(ab, abs=1e-6)
    assert maxsim_directed(packed, 1, 0) == pytest.approx(ba, abs=1e-6)

    sim = similarity_matrix(packed)
    assert sim[0, 1] == pytest.approx((ab + ba) / 2, abs=1e-6)
    assert sim[0, 1] == pytest.approx(sim[1, 0], abs=1e-7)   # 대칭


def test_MaxSim_자기자신은_1():
    space = make_space({"A": ["a0", "b90"], "B": ["c60", "d180"]})
    sim = similarity_matrix(space.packed(UNW))
    np.testing.assert_allclose(np.diag(sim), 1.0, atol=1e-6)


def test_MaxSim은_IDF_가중_평균이다():
    # A = {a0(df 2 -> idf log(3/2)), b90(df 1 -> idf log 3)}, B = {c60}
    space = make_space({"A": ["a0", "b90"], "B": ["c60"], "C": ["a0"]})
    packed = space.packed(IDF)
    w0, w1 = math.log(3 / 2), math.log(3)
    want = (w0 * math.cos(math.radians(60)) + w1 * math.cos(math.radians(30))) / (w0 + w1)
    assert maxsim_directed(packed, 0, 1) == pytest.approx(want, abs=1e-6)


def test_행렬은_한_쌍씩_계산한_참조구현과_같다():
    space = make_space({"A": ["a0", "b90"], "B": ["c60"], "C": ["d180", "a0"],
                        "D": ["c60", "b90", "a0"]})
    packed = space.packed(IDF)
    sim = similarity_matrix(packed)
    for i in range(4):
        for j in range(4):
            want = (maxsim_directed(packed, i, j) + maxsim_directed(packed, j, i)) / 2
            assert sim[i, j] == pytest.approx(want, abs=1e-6)


def test_청크를_잘게_잘라도_결과가_같다():
    """청크 경계가 가게를 쪼개면 안 된다 — 경계를 바꿔가며 같은 답이 나오는지."""
    space = make_space({"A": ["a0", "b90"], "B": ["c60"], "C": ["d180", "a0"],
                        "D": ["c60", "b90", "a0"], "E": ["b90"]})
    packed = space.packed(UNW)
    full = similarity_matrix(packed, chunk_rows=1000)
    for chunk in (1, 2, 3, 4, 7):
        np.testing.assert_allclose(similarity_matrix(packed, chunk_rows=chunk),
                                   full, atol=1e-6)


def test_가장_잘_맞는_메뉴_쌍():
    space = make_space({"A": ["a0", "b90"], "B": ["c60"]})
    a, b, cos = best_menu_pair(space.packed(UNW), 0, 1)
    assert (a, b) == ("b90", "c60")            # 90° 와 60° 가 30° 차이로 제일 가깝다
    assert cos == pytest.approx(math.cos(math.radians(30)), abs=1e-6)


# ────────────────────────────────────────────── NaN (메뉴 없는 가게)
def test_메뉴가_없는_가게는_행렬에서_NaN():
    space = make_space({"A": ["a0"], "빈가게": [], "B": ["b90"]})
    sim = similarity_matrix(space.packed(UNW))
    assert np.isnan(sim[1]).all() and np.isnan(sim[:, 1]).all()
    assert np.isfinite(sim[0, 2])              # 나머지는 멀쩡하다


def test_메뉴가_하나도_없으면_전부_NaN():
    space = make_space({"A": [], "B": []})
    assert np.isnan(similarity_matrix(space.packed(UNW))).all()


def test_단일_벡터_대조군도_메뉴가_없으면_NaN_행():
    space = make_space({"A": ["a0", "b90"], "빈가게": []})
    V = mean_vectors(space.packed(UNW))
    assert np.isnan(V[1]).all()
    assert float(np.linalg.norm(V[0])) == pytest.approx(1.0, abs=1e-6)   # L2 정규화


def test_NaN_후보는_사용자_평균_점수로_채운다():
    """0 으로 채우면 '메뉴를 안 올린 가게' 가 구조적으로 순위 바닥에 깔린다."""
    sim = np.array([[1.0, 0.2, np.nan],
                    [0.2, 1.0, np.nan],
                    [np.nan, np.nan, np.nan]])
    s = content_score(sim, {"A": 0, "B": 1, "C": 2}, [("A", True)], "mean", 0.0)
    assert s[2] == pytest.approx((s[0] + s[1]) / 2)


def test_NaN_이력은_집계에서_뺀다():
    sim = np.array([[1.0, 0.2, np.nan],
                    [0.2, 1.0, np.nan],
                    [np.nan, np.nan, np.nan]])
    both = content_score(sim, {"A": 0, "B": 1, "C": 2},
                         [("A", True), ("C", True)], "mean", 0.0)
    only_a = content_score(sim, {"A": 0, "B": 1, "C": 2}, [("A", True)], "mean", 0.0)
    np.testing.assert_allclose(both, only_a)


def test_like가_하나도_없으면_상수_점수():
    sim = np.array([[1.0, 0.2], [0.2, 1.0]])
    s = content_score(sim, {"A": 0, "B": 1}, [("A", False)], "mean", 1.0)
    np.testing.assert_allclose(s, 0.0)


# ────────────────────────────────────────────── 집계 변종
def test_mean과_max():
    rows = np.array([[0.1, 0.9], [0.5, 0.2]])
    np.testing.assert_allclose(aggregate(rows, "mean"), [0.3, 0.55])
    np.testing.assert_allclose(aggregate(rows, "max"), [0.5, 0.9])


def test_logsumexp는_tau가_작을수록_max에_가깝다():
    rows = np.array([[0.1, 0.9], [0.5, 0.2]])
    lo = aggregate(rows, "lse005")
    hi = aggregate(rows, "lse02")
    mx, mn = aggregate(rows, "max"), aggregate(rows, "mean")
    assert np.all(lo <= mx + 1e-9) and np.all(lo >= mn - 1e-9)
    assert np.all(np.abs(lo - mx) <= np.abs(hi - mx) + 1e-12)


def test_logsumexp는_이력_길이로_눈금을_맞춘다():
    """같은 값이 여러 번 들어와도 점수가 커지면 안 된다(H 로 나눈 정의)."""
    one = aggregate(np.array([[0.3, 0.7]]), "lse01")
    three = aggregate(np.array([[0.3, 0.7]] * 3), "lse01")
    np.testing.assert_allclose(one, three, atol=1e-9)


def test_logsumexp는_큰_값에서도_넘치지_않는다():
    rows = np.array([[50.0, -50.0], [-50.0, 50.0]])
    assert np.isfinite(aggregate(rows, "lse005")).all()


def test_dislike는_따로_접어서_뺀다():
    sim = np.array([[1.0, 0.8, 0.1], [0.8, 1.0, 0.2], [0.1, 0.2, 1.0]])
    pos = {"A": 0, "B": 1, "C": 2}
    hist = [("A", True), ("C", False)]
    base = content_score(sim, pos, hist, "mean", 0.0)
    half = content_score(sim, pos, hist, "mean", 0.5)
    full = content_score(sim, pos, hist, "mean", 1.0)
    np.testing.assert_allclose(base - 0.5 * sim[2], half, atol=1e-9)
    np.testing.assert_allclose(base - sim[2], full, atol=1e-9)


def test_z정규화는_후보_마스크_안에서_잰다():
    v = np.array([0.0, 1.0, 100.0])
    mask = np.array([True, True, False])           # 100 은 이미 가본 가게
    z = zscore_on(v, mask)
    assert z[:2].mean() == pytest.approx(0.0)
    assert z[:2].std() == pytest.approx(1.0)


# ────────────────────────────────────────────── 하이브리드와 λ 선택
def _tiny_ctx():
    """작은 손 데이터셋 + 손으로 만든 메뉴 유사도."""
    from analysis.backtest.pool import build_pool
    from analysis.backtest.scorers import ScoreContext
    from analysis.backtest.split import build_splits
    from analysis.backtest.tests.conftest import linear_history, make_dataset

    stores = [(f"s{i}", 10 + i, 4.0, "한식", [10000], 1) for i in range(20)]
    reviews = []
    for u in range(8):
        reviews += linear_history(f"u{u}", [f"s{(u + i) % 20}" for i in range(12)],
                                  [5, 5, 1, 4, 5, 2, 5, 5, 4, 5, 1, 5])
    ds = make_dataset(stores, reviews)
    pool = build_pool(ds)
    rng = np.random.default_rng(0)
    sim = rng.random((len(pool), len(pool))).astype(np.float32)
    sim = (sim + sim.T) / 2
    np.fill_diagonal(sim, 1.0)
    ctx = ScoreContext(pool=pool, extras={"menu": {"sim": {"top8_idf": sim}}})
    return ds, pool, build_splits(ds, pool), ctx, sim


def test_하이브리드_이름은_규칙으로_풀린다():
    from analysis.backtest import scorers
    assert scorers.exists("hybrid_pop_menu_top8_idf_max_l0.5")
    assert not scorers.exists("hybrid_pop_없는모델_l1")
    assert not scorers.exists("hybrid_pop_menu_top8_idf_max_lx")


def test_하이브리드는_pop_z_더하기_람다_content_z():
    from analysis.backtest import scorers
    from analysis.backtest.scorers_menu import zscore_on
    _, pool, splits, ctx, sim = _tiny_ctx()
    hist = splits[0].signals(5)
    uid = splits[0].user_id
    content = scorers.get("menu_top8_idf_mean")
    hybrid = scorers.get("hybrid_pop_menu_top8_idf_mean_l2")
    mask = np.ones(len(pool), dtype=bool)
    want = (zscore_on(pool.log_review_count, mask)
            + 2.0 * zscore_on(content.score(hist, ctx, user_id=uid), mask))
    np.testing.assert_allclose(hybrid.score(hist, ctx, user_id=uid), want, atol=1e-9)


def test_하이브리드는_후보_마스크가_있으면_그_안에서_z를_잰다():
    from analysis.backtest import run, scorers
    _, pool, splits, ctx, _ = _tiny_ctx()
    hist, uid = splits[0].signals(5), splits[0].user_id
    plain = scorers.get("hybrid_pop_menu_top8_idf_mean_l1").score(hist, ctx, user_id=uid)
    ctx.extras["candidates"] = run.seen_masks(splits, pool)
    masked = scorers.get("hybrid_pop_menu_top8_idf_mean_l1").score(hist, ctx, user_id=uid)
    assert not np.allclose(plain, masked)        # 후보가 줄면 눈금이 달라진다


def test_outer_cv는_폴드마다_밖에서_고르고_안에서_잰다():
    """모든 사용자가 값을 받고, 폴드마다 고른 라벨이 기록돼야 한다."""
    from analysis.backtest import cv, scorers
    _, _, splits, ctx, _ = _tiny_ctx()
    cands = {f"{lam:g}": (lambda lam=lam: scorers.get(
        f"hybrid_pop_menu_top8_idf_mean_l{lam:g}")) for lam in (0.5, 2.0)}
    res, chosen = cv.outer_cv(cands, splits, ctx, 5, k=2)
    assert len(chosen) == 2 and set(chosen) <= set(cands)
    assert res.user_ids == [s.user_id for s in splits]
    assert np.isfinite(res.p["auc"]).all()       # 빠진 사용자가 없다


# ────────────────────────────────────────────── 난수 벡터 대조군 (Step 3a)
def test_난수_대조군은_개수와_가중치_구조를_그대로_둔다():
    """대조군의 뜻이 여기 있다 — '내용만 지우고 집합 크기는 남긴다'."""
    space = make_space({"s1": ["a0", "b90", "c60"], "s2": ["a0"], "s3": []})
    base = space.packed(IDF)
    null = random_unit_packed(base, seed=1)
    np.testing.assert_array_equal(null.counts, base.counts)
    np.testing.assert_array_equal(null.starts, base.starts)
    np.testing.assert_allclose(null.weights, base.weights)
    assert null.vecs.shape == base.vecs.shape
    assert null.names == ["(난수 벡터)"] * len(base.names)


def test_난수_대조군의_벡터는_단위벡터다():
    """코사인이 곧 내적이라는 전제(메뉴 벡터는 L2 정규화됨)를 대조군도 지켜야 한다."""
    space = make_space({"s1": ["a0", "b90"], "s2": ["c60", "d180"]})
    null = random_unit_packed(space.packed(UNW), seed=2)
    np.testing.assert_allclose(np.linalg.norm(null.vecs, axis=1), 1.0, atol=1e-6)


def test_난수_대조군은_seed가_같으면_같고_다르면_다르다():
    space = make_space({"s1": ["a0", "b90"], "s2": ["c60"]})
    base = space.packed(UNW)
    np.testing.assert_allclose(random_unit_packed(base, 5).vecs,
                               random_unit_packed(base, 5).vecs)
    assert not np.allclose(random_unit_packed(base, 5).vecs,
                           random_unit_packed(base, 6).vecs)


def test_난수_대조군은_메뉴_없는_가게를_NaN으로_남긴다():
    """개수 0 은 개수 0 이다 — 대조군에서도 '모른다' 가 '안 닮았다' 가 되면 안 된다."""
    space = make_space({"s1": ["a0", "b90"], "s2": ["c60"], "없음": []})
    sim = similarity_matrix(random_unit_packed(space.packed(UNW), seed=3))
    assert np.isnan(sim[2]).all() and np.isnan(sim[:, 2]).all()
    assert np.isfinite(sim[0, 1])


def test_공간의_난수묶음은_공간의_seed를_쓴다():
    space = make_space({"s1": ["a0", "b90"], "s2": ["c60"]})
    space.seed = 11
    np.testing.assert_allclose(space.null_packed(UNW).vecs,
                               random_unit_packed(space.packed(UNW), 11).vecs)
    # 같은 변종을 두 번 물어도 같은 묶음(캐시)이어야 실험이 재현된다.
    assert space.null_packed(UNW) is space.null_packed(UNW)


def test_진짜_쪽_캐시_키는_난수_인자가_생겨도_그대로다():
    """이미 만들어 둔 유사도 캐시를 무효화하지 않기 위한 회귀 테스트다."""
    space = make_space({"s1": ["a0", "b90"], "s2": ["c60"]})
    assert space.fingerprint(UNW) == space.fingerprint(UNW, null=False)
    assert space.fingerprint(UNW) != space.fingerprint(UNW, null=True)


def test_난수_캐시_키는_seed를_섞는다():
    space = make_space({"s1": ["a0", "b90"], "s2": ["c60"]})
    other = make_space({"s1": ["a0", "b90"], "s2": ["c60"]})
    other.seed = 99
    assert space.fingerprint(UNW, null=True) != other.fingerprint(UNW, null=True)
    assert space.fingerprint(UNW) == other.fingerprint(UNW)
