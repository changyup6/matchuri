# -*- coding: utf-8 -*-
"""구성요소 블렌드 — z 정규화·가중합·격자 탐욕 선택·쌍대 로지스틱.

손으로 답을 낼 수 있는 아주 작은 경우로만 검증한다. 실데이터가 없어도 돈다.
"""
from __future__ import annotations

import numpy as np
import pytest

from analysis.backtest import config
from analysis.backtest.scorers import ScoreContext, Scorer
from analysis.backtest.split import UserSplit
from analysis.content.blend import (BlendScorer, ComponentBank, UserRow,
                                    component_correlations, fit_grid, fit_pairwise,
                                    logistic_pairwise, pair_features,
                                    per_user_spearman, spearman_matrix,
                                    stack_candidate_rows, subset_bank, zscore_masked)

from .conftest import make_dataset


# ────────────────────────────────────────────── z 정규화
def test_z는_마스크_안에서_평균0_표준편차1():
    v = np.array([1.0, 2.0, 3.0, 100.0])
    mask = np.array([True, True, True, False])
    z = zscore_masked(v, mask)
    assert z[mask].mean() == pytest.approx(0.0, abs=1e-12)
    assert z[mask].std() == pytest.approx(1.0, abs=1e-12)
    assert z[3] > 10                                # 마스크 밖도 같은 눈금을 쓴다


def test_NaN은_z_정규화_뒤_0():
    v = np.array([1.0, np.nan, 3.0])
    z = zscore_masked(v, np.ones(3, dtype=bool))
    assert z[1] == 0.0
    assert z[0] == pytest.approx(-1.0) and z[2] == pytest.approx(1.0)


def test_상수_구성요소는_전부_0():
    """표준편차가 0 이면 그 modality 는 이 사용자에게 아무 말도 하지 않는다."""
    z = zscore_masked(np.full(4, 7.0), np.ones(4, dtype=bool))
    np.testing.assert_allclose(z, np.zeros(4))


def test_전부_NaN이면_전부_0():
    z = zscore_masked(np.full(3, np.nan), np.ones(3, dtype=bool))
    np.testing.assert_allclose(z, np.zeros(3))


def test_자르기는_기본이_꺼져_있다():
    """Step 1~3c 의 결과가 그대로 재현돼야 하므로 **기본은 자르지 않는다** (Step 3d)."""
    v = np.array([1.0, 2.0, 3.0, 100.0])
    mask = np.array([True, True, True, False])
    assert zscore_masked(v, mask)[3] > 10
    np.testing.assert_allclose(zscore_masked(v, mask, clip=None),
                               zscore_masked(v, mask))


def test_자르기를_켜면_양쪽_꼬리를_자른다():
    v = np.array([1.0, 2.0, 3.0, 100.0, -100.0])
    mask = np.array([True, True, True, False, False])
    z = zscore_masked(v, mask, clip=3.0)
    assert z.max() == pytest.approx(3.0) and z.min() == pytest.approx(-3.0)
    # 자르기 전에 이미 3 안쪽인 값은 그대로다 — 눈금을 다시 만들지 않는다.
    np.testing.assert_allclose(z[:3], zscore_masked(v, mask)[:3])


# ────────────────────────────────────────────── 손으로 만든 통
def make_bank(names, z_by_user, labels_by_user) -> ComponentBank:
    bank = ComponentBank(names=list(names), n_history=5)
    for uid, z in z_by_user.items():
        z = np.asarray(z, dtype=float)
        lab = np.asarray(labels_by_user[uid], dtype=bool)
        cand = np.ones(z.shape[1], dtype=bool)
        bank.rows[uid] = UserRow(user_id=uid, z=z, cand=cand, zc=z, labels=lab,
                                 tiebreak=np.arange(z.shape[1]),
                                 n_pos=int(lab.sum()))
        bank.order.append(uid)
    return bank


def test_가중치_벡터는_기준_구성요소를_1로_고정한다():
    bank = make_bank(["popularity", "m"], {"u": [[0, 1], [1, 0]]}, {"u": [True, False]})
    np.testing.assert_allclose(bank.weight_vector({"m": 2.0}), [1.0, 2.0])
    np.testing.assert_allclose(bank.weight_vector({"popularity": 0.5, "m": 2.0}),
                               [0.5, 2.0])


def test_블렌드_점수는_손으로_계산한_가중합():
    z = np.array([[1.0, 0.0, -1.0], [0.0, 2.0, 1.0]])
    bank = make_bank(["popularity", "m"], {"u": z}, {"u": [True, False, False]})
    scorer = BlendScorer(bank, {"m": 0.5})
    np.testing.assert_allclose(scorer.score([], None, user_id="u"),
                               [1.0, 1.0, -0.5])


def test_없는_구성요소의_λ는_0으로_읽는다():
    z = np.array([[1.0, 0.0], [0.0, 1.0]])
    bank = make_bank(["popularity", "m"], {"u": z}, {"u": [True, False]})
    np.testing.assert_allclose(BlendScorer(bank, {}).score([], None, user_id="u"),
                               [1.0, 0.0])


def test_부분집합은_행만_고른다():
    z = np.array([[1.0, 0.0], [0.0, 1.0], [2.0, 2.0]])
    bank = make_bank(["popularity", "m", "n"], {"u": z}, {"u": [True, False]})
    sub = subset_bank(bank, ["popularity", "n"])
    assert sub.names == ["popularity", "n"]
    np.testing.assert_allclose(sub.rows["u"].z, z[[0, 2]])
    np.testing.assert_allclose(bank.rows["u"].z, z)      # 원본은 그대로다


# ────────────────────────────────────────────── 격자 탐욕 선택
def test_격자는_정답을_아는_구성요소를_붙인다():
    """`good` 은 정답을 정확히 가리키고 `noise` 는 반대로 가리킨다.

    정답을 **뒤쪽**에 둔다 — 앞쪽에 두면 동점을 깨는 순열(여기서는 인덱스 순)이
    이미 정답을 위로 올려 인기만으로 NDCG 가 1 이 되고, 붙일 이득이 없어진다.
    """
    n = 20
    labels = np.zeros(n, dtype=bool)
    labels[-3:] = True
    pop = np.zeros(n)
    good = np.where(labels, 3.0, 0.0)
    noise = np.where(labels, -3.0, 0.0)
    bank = make_bank(["popularity", "good", "noise"],
                     {"u": np.stack([pop, good, noise])}, {"u": labels})
    fit = fit_grid(bank, ["u"])
    assert fit["weights"]["good"] > 0
    assert fit["weights"]["noise"] == 0
    assert fit["trace"][1]["added"] == "good"


def test_격자는_더_오르지_않으면_멈춘다():
    n = 10
    labels = np.zeros(n, dtype=bool)
    labels[0] = True
    pop = np.where(labels, 5.0, 0.0)               # 인기만으로 이미 완벽하다
    flat = np.zeros(n)
    bank = make_bank(["popularity", "flat"],
                     {"u": np.stack([pop, flat])}, {"u": labels})
    fit = fit_grid(bank, ["u"])
    assert fit["weights"]["flat"] == 0
    assert len(fit["trace"]) == 1                  # 아무 것도 붙이지 않았다


# ────────────────────────────────────────────── 쌍대 로지스틱
def test_로지스틱은_경사가_0인_점을_찾는다():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 3))
    for alpha in (0.01, 1.0):
        w = logistic_pairwise(X, alpha)
        s = 1.0 / (1.0 + np.exp(X @ w))
        grad = -(X * s[:, None]).mean(axis=0) + alpha * w
        assert np.abs(grad).max() < 1e-8


def test_로지스틱은_1차원에서_해석해와_같다():
    """x 가 전부 1 이면 최적 w 는 mean σ(−w) = αw 를 푸는 값이다."""
    X = np.ones((10, 1))
    alpha = 0.5
    w = float(logistic_pairwise(X, alpha)[0])
    assert 1.0 / (1.0 + np.exp(w)) == pytest.approx(alpha * w, abs=1e-9)


def test_L2가_클수록_가중치가_작아진다():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(200, 3)) + 0.5
    norms = [np.linalg.norm(logistic_pairwise(X, a)) for a in (0.01, 0.1, 1.0, 10.0)]
    assert norms == sorted(norms, reverse=True)


def test_쌍_특성은_양성마다_음성_수만큼_만들어진다():
    n = 12
    labels = np.zeros(n, dtype=bool)
    labels[:2] = True
    z = np.arange(2 * n, dtype=float).reshape(2, n)
    bank = make_bank(["popularity", "m"], {"u": z}, {"u": labels})
    X = pair_features(bank, ["u"], seed=1, n_neg=4)
    assert X.shape == (2 * 4, 2)
    again = pair_features(bank, ["u"], seed=1, n_neg=4)
    np.testing.assert_allclose(X, again)           # 씨앗이 같으면 같은 표본
    diff = pair_features(bank, ["u"], seed=2, n_neg=4)
    assert not np.allclose(X, diff)


def test_음성에는_양성이_섞이지_않는다():
    n = 6
    labels = np.array([True, True, False, False, False, False])
    z = np.array([np.arange(n, dtype=float)])
    bank = make_bank(["popularity"], {"u": z}, {"u": labels})
    X = pair_features(bank, ["u"], seed=3, n_neg=4)
    # z[0] = 위치 그 자체이므로 (양성 − 음성) 이 0 이면 양성을 음성으로 뽑았다는 뜻이다.
    assert not np.any(np.isclose(X, 0.0))


def test_쌍대_학습은_정답을_가리키는_구성요소에_양의_가중치를_준다():
    n = 30
    labels = np.zeros(n, dtype=bool)
    labels[:5] = True
    pop = np.zeros(n)
    good = np.where(labels, 1.0, -1.0)
    bank = make_bank(["popularity", "good"],
                     {"u": np.stack([pop, good])}, {"u": labels})
    fit = fit_pairwise(bank, ["u"], seed=config.SEED)
    assert fit["weights_raw"]["good"] > 0
    assert fit["n_pairs"] == 5 * 20


# ────────────────────────────────────────────── 통 만들기 (하네스와의 접점)
class ConstScorer(Scorer):
    def __init__(self, values):
        self.values = np.asarray(values, dtype=float)
        self.name = "const"

    def score(self, history, ctx, *, user_id):
        return self.values


def test_통은_이미_간_가게를_빼고_z를_낸다():
    from analysis.backtest.pool import build_pool

    ds = make_dataset(
        stores=[(f"s{i}", 5, 4.0, "한식", [10000], 1) for i in range(1, 5)],
        reviews=[])
    pool = build_pool(ds)
    ctx = ScoreContext(pool=pool, history_mode="visit")
    sp = UserSplit(user_id="u", total=4, past=(("s1", 5),), future=(("s2", 5),),
                   n_future_raw=1, n_future_dup=0, n_future_seen=0,
                   n_future_seen_recent={})
    values = {"popularity": [0.0, 1.0, 2.0, 3.0], "m": [1.0, 1.0, 1.0, 1.0]}
    bank = ComponentBank.build({"popularity": "popularity", "m": "m"}, [sp], ctx, 5,
                               scorer_of=lambda name: ConstScorer(values[name]))
    row = bank.rows["u"]
    assert row.cand.tolist() == [False, True, True, True]   # s1 은 이미 갔다
    assert row.labels.tolist() == [True, False, False]      # 후보 안에서의 정답
    # 후보 {1,2,3} 의 평균 2, 표준편차 sqrt(2/3)
    assert row.z[0][1] == pytest.approx((1.0 - 2.0) / np.sqrt(2 / 3))
    np.testing.assert_allclose(row.z[1], 0.0)               # 상수 modality
    assert row.zc.shape == (2, 3)


def test_통은_clip_을_z에_그대로_넘긴다():
    """`ComponentBank.build(clip=…)` 이 `zscore_masked` 까지 닿는지 (Step 3d)."""
    from analysis.backtest.pool import build_pool

    ds = make_dataset(
        stores=[(f"s{i}", 5, 4.0, "한식", [10000], 1) for i in range(1, 6)],
        reviews=[])
    ctx = ScoreContext(pool=build_pool(ds), history_mode="visit")
    sp = UserSplit(user_id="u", total=5, past=(), future=(("s2", 5),),
                   n_future_raw=1, n_future_dup=0, n_future_seen=0,
                   n_future_seen_recent={})
    values = {"popularity": [0.0, 0.0, 0.0, 0.0, 100.0], "m": [1.0] * 5}
    build = lambda clip: ComponentBank.build(                      # noqa: E731
        {"popularity": "popularity", "m": "m"}, [sp], ctx, 5,
        scorer_of=lambda name: ConstScorer(values[name]), clip=clip)
    assert build(None).rows["u"].z[0].max() > 1.9                  # 안 자르면 1.99…
    bank = build(1.0)
    assert bank.clip == 1.0
    assert bank.rows["u"].z[0].max() == pytest.approx(1.0)
    assert subset_bank(bank, ["popularity"]).clip == 1.0           # ablation 도 따라간다


def test_통은_기준_구성요소가_첫번째가_아니면_거부한다():
    from analysis.backtest.pool import build_pool

    ds = make_dataset(stores=[("s1", 5, 4.0, "한식", [10000], 1)], reviews=[])
    ctx = ScoreContext(pool=build_pool(ds))
    with pytest.raises(ValueError, match="popularity"):
        ComponentBank.build({"m": "m", "popularity": "popularity"}, [], ctx, 5,
                            scorer_of=lambda name: ConstScorer([0.0]))


# ────────────────────────────────────────────── 성분 상관 (Step 3a)
def test_스피어만은_단조_변환에_1을_준다():
    """피어슨과 갈리는 자리 — x 와 exp(x) 는 선형이 아니지만 순위는 같다."""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    m = spearman_matrix(np.stack([x, np.exp(x)], axis=1))
    assert m[0, 1] == pytest.approx(1.0)
    assert m[1, 0] == pytest.approx(1.0)
    np.testing.assert_allclose(np.diag(m), [1.0, 1.0])


def test_스피어만은_뒤집힌_순서에_마이너스1을_준다():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    m = spearman_matrix(np.stack([x, -x], axis=1))
    assert m[0, 1] == pytest.approx(-1.0)


def test_스피어만은_동점을_평균_순위로_다룬다():
    """[1,1,2] 의 순위는 [1.5, 1.5, 3] — 동점을 순서대로 매기면 상관이 부풀려진다."""
    a = np.array([1.0, 1.0, 2.0])
    b = np.array([5.0, 3.0, 9.0])
    m = spearman_matrix(np.stack([a, b], axis=1))
    ra, rb = np.array([1.5, 1.5, 3.0]), np.array([2.0, 1.0, 3.0])
    ra, rb = ra - ra.mean(), rb - rb.mean()
    assert m[0, 1] == pytest.approx(
        float(ra @ rb / np.sqrt((ra @ ra) * (rb @ rb))))


def test_상수_열의_상관은_NaN():
    """분산이 0 이면 '같은 순서로 세우는가' 라는 질문 자체가 성립하지 않는다."""
    x = np.stack([np.array([1.0, 2.0, 3.0]), np.full(3, 7.0)], axis=1)
    m = spearman_matrix(x)
    assert np.isnan(m[0, 1]) and np.isnan(m[1, 1])
    assert m[0, 0] == pytest.approx(1.0)


def test_상관_행렬은_2차원만_받는다():
    with pytest.raises(ValueError, match="2차원"):
        spearman_matrix(np.zeros(5))


def test_후보_행_쌓기는_마스크_안만_가져온다():
    """z 는 풀 전체 눈금이지만 상관은 **그 사용자의 후보 안에서만** 물어야 한다."""
    z = np.array([[0.0, 1.0, 2.0], [9.0, 8.0, 7.0]])
    bank = make_bank(["popularity", "m"], {"u": z}, {"u": [True, False, False]})
    bank.rows["u"].cand = np.array([True, False, True])
    bank.rows["u"].zc = z[:, [0, 2]]
    names, x = stack_candidate_rows(bank, {"pop_raw": np.array([10.0, 20.0, 30.0])})
    assert names == ["popularity", "m", "pop_raw"]
    np.testing.assert_allclose(x, [[0.0, 9.0, 10.0], [2.0, 7.0, 30.0]])


def test_사용자_내_상관과_풀링_상관은_반대_부호일_수_있다():
    """심프슨 역설 — 두 사용자 안에서는 −1 인데 쌓으면 +0.54 다.

    상관을 **두 벌** 내는 이유가 이것이다. 우리가 쓰는 것은 한 사용자의 후보 안
    순위이므로 판단은 사용자 내 상관으로 한다.
    """
    low = np.stack([np.array([0.0, 1.0, 2.0]), np.array([2.0, 1.0, 0.0])])
    high = np.stack([np.array([10.0, 11.0, 12.0]), np.array([12.0, 11.0, 10.0])])
    bank = make_bank(["popularity", "m"], {"u1": low, "u2": high},
                     {"u1": [True, False, False], "u2": [True, False, False]})
    names, mean, sd = per_user_spearman(bank)
    assert names == ["popularity", "m"]
    assert mean[0, 1] == pytest.approx(-1.0)
    assert sd[0, 1] == pytest.approx(0.0)
    _, x = stack_candidate_rows(bank)
    assert spearman_matrix(x)[0, 1] == pytest.approx(9.5 / 17.5)


def test_성분_상관_묶음은_이름과_행수를_함께_준다():
    z = np.array([[0.0, 1.0, 2.0], [2.0, 1.0, 0.0]])
    bank = make_bank(["popularity", "m"], {"u": z}, {"u": [True, False, False]})
    out = component_correlations(bank, {"pop_raw": np.array([1.0, 2.0, 3.0])})
    assert out["names"] == ["popularity", "m", "pop_raw"]
    assert out["n_rows"] == 3 and out["n_users"] == 1
    assert out["pooled"][0][1] == pytest.approx(-1.0)
    assert out["per_user_mean"][0][2] == pytest.approx(1.0)
