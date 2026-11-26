# -*- coding: utf-8 -*-
"""레지스트리·캐시·CLI — 하네스가 결정적으로 돌고 산출물을 남기는지."""
from __future__ import annotations

import json

import numpy as np
import pytest

from analysis.backtest import cache, config, run, scorers
from analysis.backtest.pool import build_pool
from analysis.backtest.rng import stable_seed
from analysis.backtest.scorers import ScoreContext
from analysis.backtest.split import build_splits
from analysis.backtest.tests.conftest import linear_history, make_dataset

STORES = [(f"s{i}", 10, 4.0, "한식", [10000], 1) for i in range(20)]


def _tiny():
    reviews = []
    for u in range(6):
        reviews += linear_history(f"u{u}", [f"s{(u + i) % 20}" for i in range(12)],
                                  [5, 5, 1, 4, 5, 2, 5, 5, 4, 5, 1, 5])
    ds = make_dataset(STORES, reviews)
    pool = build_pool(ds)
    return ds, pool, build_splits(ds, pool)


def test_베이스라인_4종이_전부_등록돼_있다():
    for name in ("random", "popularity", "quality", "current"):
        assert name in scorers.REGISTRY
        assert scorers.get(name).description


def test_모르는_모델은_이름을_알려주며_거절한다():
    with pytest.raises(KeyError, match="모르는 모델"):
        scorers.get("없는모델")


def test_scorer는_풀_길이의_점수를_돌려준다():
    _, pool, _ = _tiny()
    ctx = ScoreContext(pool=pool)
    for name in ("random", "popularity", "quality", "current"):
        s = scorers.get(name).score([("s0", True)], ctx, user_id="u0")
        assert np.asarray(s).shape == (len(pool),)


def test_난수_seed는_프로세스가_달라도_같다():
    # 파이썬 hash() 를 쓰면 실행마다 달라진다. 값을 못 박아 회귀를 막는다.
    assert stable_seed(20260909, "random", "u1") == 9336519804217200749


def test_random_scorer는_같은_사용자에게_같은_점수():
    _, pool, _ = _tiny()
    ctx = ScoreContext(pool=pool)
    a = scorers.get("random").score([], ctx, user_id="u1")
    b = scorers.get("random").score([], ctx, user_id="u1")
    c = scorers.get("random").score([], ctx, user_id="u2")
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)


def test_분할_캐시는_읽고_쓴_결과가_같다(tmp_path):
    ds, pool, expected = _tiny()
    first, hit = cache.load_or_build(ds, pool, cache_dir=tmp_path)
    assert not hit and first == expected
    second, hit2 = cache.load_or_build(ds, pool, cache_dir=tmp_path)
    assert hit2 and second == expected


def test_상수를_바꾸면_캐시_지문이_바뀐다(monkeypatch):
    before = cache.fingerprint()
    monkeypatch.setattr(config, "MIN_USER_REVIEWS", 99)
    assert cache.fingerprint() != before


def test_과제별_참조_모델을_따로_고를_수_있다():
    assert run.parse_refs("p=popularity,r=quality") == {"task_p": "popularity",
                                                        "task_r": "quality"}
    # 이름 하나만 주면 두 과제에 같이 쓴다 (옛 --baseline 과 같은 뜻)
    assert run.parse_refs("quality") == {"task_p": "quality", "task_r": "quality"}
    with pytest.raises(ValueError, match="모르는 과제 키"):
        run.parse_refs("x=quality,r=quality")
    with pytest.raises(ValueError, match="참조 모델이 빠졌다"):
        run.parse_refs("p=quality")


def test_후보_마스크는_이미_간_가게를_뺀다():
    _, pool, splits = _tiny()
    masks = run.seen_masks(splits, pool)
    sp = splits[0]
    m = masks[sp.user_id]
    assert m.shape == (len(pool),)
    for sid in sp.seen_stores:
        assert not m[pool.pos[sid]]
    assert int((~m).sum()) == len(sp.seen_stores)


@pytest.mark.slow
def test_CLI가_json과_md를_남긴다(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(run.config, "RESULTS_DIR", tmp_path)
    rc = run.main(["--models", "random,popularity,quality", "--N", "3",
                   "--resamples", "50", "--out", "smoke"])
    assert rc == 0
    blob = json.loads((tmp_path / "smoke.json").read_text(encoding="utf-8"))
    assert blob["pool"]["size"] == 2216            # README 와 같은 수
    assert blob["users"]["eval_users"] == 227
    assert set(blob["results"]) == {"random", "popularity", "quality"}
    # 기본 참조는 과제 P = popularity, R = quality (PLAN 5절 1번)
    assert blob["references"] == {"task_p": "popularity", "task_r": "quality"}
    p3 = blob["results"]["popularity"]["3"]
    assert p3["task_p"]["auc"]["vs_baseline"]["diff"] == 0.0
    assert "auc_pm" in p3["task_p"]                # 인기 매칭 AUC 가 json 에 있다
    assert blob["results"]["quality"]["3"]["task_r"]["auc"]["vs_baseline"]["diff"] == 0.0
    md = (tmp_path / "smoke.md").read_text(encoding="utf-8")
    assert "과제 P" in md and "과제 R" in md and "`quality`" in md
    assert "auc_pm" in md


# ────────────────────────────────────────── Step 1.5: λ 선택 기준
def test_λ_선택_기준을_바꿔_고를_수_있다():
    """PLAN 5절 'Step 1 → Step 1.5' 3번 — AUC 외에 NDCG@20 으로도 고른다.

    상위만 세는 잣대와 꼬리까지 세는 잣대는 **다른 λ 를 고를 수 있다.** 두 잣대가
    실제로 다른 답을 낼 수 있다는 것을 못 박아 둔다(같은 답이 나오는 것은 데이터의
    성질이지 코드가 잣대를 무시해도 된다는 뜻이 아니다).
    """
    from analysis.backtest import cv
    from analysis.backtest.evaluate import P_METRICS

    assert set(config.SELECT_METRICS) <= set(P_METRICS)

    class Fixed(scorers.Scorer):
        """라벨마다 고정된 순위를 내는 가짜 모델."""

        def __init__(self, name, weights):
            self.name, self.weights = name, weights

        def score(self, history, ctx, *, user_id):
            return np.asarray(self.weights, dtype=float)[:len(ctx.pool)]

    _, pool, splits = _tiny()
    ctx = ScoreContext(pool=pool)
    n = len(pool)
    # A: 앞쪽 몇 곳만 아주 높게 (상위는 좋고 꼬리는 무작위)
    a = np.zeros(n); a[:3] = 10.0
    # B: 완만한 기울기 (꼬리 순서는 좋고 상위는 평범)
    b = np.linspace(1.0, 0.0, n)
    cands = {"a": (lambda: Fixed("a", a)), "b": (lambda: Fixed("b", b))}
    picks = {m: cv.select_hyperparam(cands, splits, ctx, 5, metric=m, k=2)[1]
             for m in config.SELECT_METRICS}
    for m in config.SELECT_METRICS:
        assert set(picks[m]) == {"a", "b"}           # 두 잣대 모두 표를 채웠다
    # 잣대마다 점수표가 다르다 — 같은 표를 그대로 쓰고 있지 않다는 증거
    assert picks["auc"] != picks[f"ndcg@{config.NDCG_K}"]


def test_이력_해석은_CLI가_고른다(tmp_path, monkeypatch):
    """`--history like_dislike` 로 Step 0 의 해석을 그대로 재현할 수 있어야 한다."""
    import json as _json
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(run.config, "RESULTS_DIR", tmp_path)
    # 중립 별점(3)이 섞여 있어야 두 해석의 신호 수가 갈린다.
    reviews = []
    for u in range(6):
        reviews += linear_history(f"u{u}", [f"s{(u + i) % 20}" for i in range(12)],
                                  [5, 3, 1, 4, 5, 3, 5, 5, 3, 5, 1, 5])
    ds = make_dataset(STORES, reviews)
    pool = build_pool(ds)
    splits = build_splits(ds, pool)
    monkeypatch.setattr(run, "load_dataset", lambda: ds)
    monkeypatch.setattr(run, "build_pool", lambda _ds: pool)
    monkeypatch.setattr(run.cache, "load_or_build",
                        lambda *a, **k: (splits, False))
    for mode, out in (("visit", "v"), ("like_dislike", "l")):
        assert run.main(["--models", "random,popularity,quality", "--N", "5",
                         "--resamples", "20", "--history", mode, "--out", out]) == 0
        blob = _json.loads((tmp_path / f"{out}.json").read_text(encoding="utf-8"))
        assert blob["history_mode"] == mode
        assert blob["users"]["history_mode"] == mode
    v = _json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))["users"]
    l = _json.loads((tmp_path / "l.json").read_text(encoding="utf-8"))["users"]
    assert v["signal_mean"]["5"] > l["signal_mean"]["5"]      # visit 이 신호가 많다
    assert v["signal_mean_full"]["5"] == 5.0                  # 버리는 신호가 없다
