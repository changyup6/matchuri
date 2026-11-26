# -*- coding: utf-8 -*-
"""Step 3c — 채택 규칙과 (d) 재현.

이 Step 의 결론은 두 다리 위에 서 있다.

  1. **채택 규칙** — '(g) 가 (d) 를 CI 로 이긴 칸 ≥ 1 이고 진 칸 0'. 규칙이 코드로
     한 줄이라 결과를 보고 슬쩍 느슨해질 수 있는 자리이므로, 손으로 만든 표로 네
     경우(이기고 안 짐 / 이기고 짐 / 아무 것도 아님 / 과제 R 에서만 짐)를 못 박는다.
  2. **(d) 재현** — 참조 변종이 Step 3a 의 숫자를 다시 내지 않으면 (g) 와의 비교가
     의미를 잃는다. 실데이터로 (d) 를 다시 돌려 소수 4자리(1e-4)까지 견준다.

실행:
    ~/venvs/recsys/bin/python -m pytest analysis/backtest/tests/test_step3c.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from analysis.backtest import config
from analysis.backtest.step2 import METRICS4
from analysis.backtest.step3c import (D_BASE, FOOD_VARIANT, REF_VARIANT, REPRO_TOL,
                                      addition_verdict, decide_food, spec_weights)

ROOT = Path(config.ROOT)
STEP3A_JSON = Path(config.RESULTS_DIR) / "step3a-final-spec.json"
NEEDED_VECS = [ROOT / "data" / "vectors" / f"{n}.npy"
               for n in ("menus", "store_text", "photo_food")]


# ────────────────────────────────────────────── 채택 규칙
def fake_vs_ref(cells: dict, ns: list[int], r_cells: dict | None = None) -> dict:
    """`run.aggregate` 가 내는 모양의 최소 표. `cells[(지표, N)] = 차이(유의하면 부호)`.

    값이 0 이면 '유의하지 않다', 0 이 아니면 그 부호로 유의하다는 뜻이다.
    """
    r_cells = dict(r_cells or {})
    out: dict = {FOOD_VARIANT: {}}
    for n in ns:
        task_p = {}
        for metric in METRICS4:
            diff = float(cells.get((metric, n), 0.0))
            task_p[metric] = {"mean": 0.5 + diff,
                              "vs_baseline": {"diff": diff, "ci_low": diff - 0.01,
                                              "ci_high": diff + 0.01,
                                              "significant": diff != 0.0}}
        rd = float(r_cells.get(n, 0.0))
        out[FOOD_VARIANT][str(n)] = {
            "task_p": task_p,
            "task_r": {"auc": {"mean": 0.5 + rd,
                               "vs_baseline": {"diff": rd, "ci_low": rd - 0.01,
                                               "ci_high": rd + 0.01,
                                               "significant": rd != 0.0}}}}
    return out


NS = [3, 5, 10, 20]


def test_이긴_칸이_있고_진_칸이_없으면_넣는다():
    d = decide_food(fake_vs_ref({("auc_pm", 5): +0.01}, NS), NS)
    assert d["adopted"] is True
    assert d["variant"] == FOOD_VARIANT
    assert d["components"][-1] == "food"
    assert d["win"] == ["auc_pm N=5"] and d["lose"] == []


def test_한_칸이라도_지면_넣지_않는다():
    """이 Step 의 실제 판정이 이 경우다 — 이긴 칸 3개, 진 칸 1개."""
    d = decide_food(fake_vs_ref({("auc_pm", 5): +0.01, ("auc_pm", 10): +0.01,
                                 ("auc_pm", 20): +0.01,
                                 ("ndcg@20", 20): -0.004}, NS), NS)
    assert d["adopted"] is False
    assert d["variant"] == REF_VARIANT
    assert d["components"] == D_BASE
    assert len(d["win"]) == 3 and d["lose"] == ["ndcg@20 N=20"]


def test_이긴_칸이_하나도_없으면_넣지_않는다():
    d = decide_food(fake_vs_ref({}, NS), NS)
    assert d["adopted"] is False and not d["win"] and not d["lose"]


def test_과제_R_에서만_져도_넣지_않는다():
    """규칙이 세는 칸에 과제 R 이 들어 있다는 것을 못 박는다(P 만 보면 통과한다)."""
    vs = fake_vs_ref({("auc", 5): +0.01}, NS, r_cells={20: -0.02})
    d = decide_food(vs, NS)
    assert d["adopted"] is False
    assert d["win"] == ["auc N=5"] and d["lose"] == [] and d["lose_r"] == ["r_auc N=20"]


def test_과제_R_에서만_이겨도_넣는다():
    vs = fake_vs_ref({}, NS, r_cells={5: +0.02})
    d = decide_food(vs, NS)
    assert d["adopted"] is True and d["win_r"] == ["r_auc N=5"]


def test_판정이_세는_칸_수는_지표4개_곱하기_N_더하기_과제R():
    v = addition_verdict(fake_vs_ref({}, NS), FOOD_VARIANT, NS)
    assert v["n_cells"] == len(METRICS4) * len(NS) == 16
    assert v["n_cells_r"] == len(NS) == 4


# ────────────────────────────────────────────── 사전 가중치
def test_사전값은_폴드평균을_N_으로_다시_평균한_것이다():
    """`popularity` 는 1 로 고정되고 나머지는 (폴드 평균 -> N 평균) 이다."""
    fits = {REF_VARIANT: {
        "3": [{"weights": {"popularity": 1.0, "quality": 0.0, "cf": 0.2,
                           "menu": 0.1, "text": 0.3}},
              {"weights": {"popularity": 1.0, "quality": 0.1, "cf": 0.4,
                           "menu": 0.3, "text": 0.5}}],
        "5": [{"weights": {"popularity": 1.0, "quality": 0.2, "cf": 0.6,
                           "menu": 0.5, "text": 0.7}}]}}
    w = spec_weights(fits, REF_VARIANT, [3, 5])
    assert w["popularity"] == 1.0
    assert w["quality"] == pytest.approx((0.05 + 0.2) / 2)
    assert w["cf"] == pytest.approx((0.3 + 0.6) / 2)
    assert w["text"] == pytest.approx((0.4 + 0.7) / 2)


# ────────────────────────────────────────────── (d) 재현 (실데이터)
@pytest.mark.slow
@pytest.mark.skipif(not (STEP3A_JSON.exists() and all(p.exists() for p in NEEDED_VECS)),
                    reason="Step 3a 결과나 임베딩 산출물이 없다 (git 에 넣지 않는다)")
def test_d_변종이_step3a_를_소수_4자리까지_재현한다():
    """(d) 를 처음부터 다시 돌려 Step 3a 의 지표·λ 와 견준다.

    Step 3c 는 성분 통에 `food` 열을 하나 더 얹고 돌린다. 열을 더해도 (d) 의 점수가
    변하지 않아야 (g) 와의 비교가 '사진을 얹은 효과' 가 된다 — 그것을 여기서 잰다.
    통을 6성분(= (d) 5개 + `food`)으로 만들고 `components` 로 (d) 만 골라 쓰는 것이
    실행기와 같은 경로다.
    """
    from analysis.backtest import cache, run
    from analysis.backtest.data import load_dataset
    from analysis.backtest.pool import attach_crawled_quality, build_pool
    from analysis.backtest.scorers import ScoreContext
    from analysis.backtest.step3c import COMPONENT_MODELS
    from analysis.content import blend as blend_mod
    from analysis.content import photos as photo_content       # noqa: F401 (scorer 등록)

    old = json.loads(STEP3A_JSON.read_text(encoding="utf-8"))
    ns = old["history_sizes"]

    ds = load_dataset()
    pool = build_pool(ds)
    splits, _ = cache.load_or_build(ds, pool)
    eval_users = {s.user_id for s in splits}
    attach_crawled_quality(pool, ds, eval_users)
    ctx = ScoreContext(
        pool=pool, seed=config.SEED, history_mode=old["history_mode"],
        extras={"candidates": run.seen_masks(splits, pool), "dataset": ds,
                "eval_users": eval_users,
                "store_names": {s: (ds.stores.get(s, {}).get("name") or "")
                                for s in pool.ids}})

    models = {c: COMPONENT_MODELS[c] for c in D_BASE + ["food"]}
    fits: dict = {REF_VARIANT: {}}
    worst = 0.0
    for n in ns:
        bank = blend_mod.ComponentBank.build(models, splits, ctx, n)
        res, per_fold = blend_mod.outer_cv_blend(
            bank, splits, ctx, fitter="pairwise", seed=config.SEED,
            name=REF_VARIANT, components=D_BASE)
        fits[REF_VARIANT][str(n)] = per_fold
        want = old["results"][REF_VARIANT][str(n)]
        for metric in METRICS4:
            got = float(np.nanmean(res.p[metric]))
            worst = max(worst, abs(got - want["task_p"][metric]["mean"]))
            assert got == pytest.approx(want["task_p"][metric]["mean"],
                                        abs=REPRO_TOL), f"{metric} N={n}"
        got_r = float(np.nanmean(res.r_auc))
        worst = max(worst, abs(got_r - want["task_r"]["auc"]["mean"]))
        assert got_r == pytest.approx(want["task_r"]["auc"]["mean"],
                                      abs=REPRO_TOL), f"r_auc N={n}"

    # 폴드 밖에서 학습된 λ 도 같아야 한다 — 지표만 맞고 가중치가 다르면 우연이다.
    got_w = spec_weights(fits, REF_VARIANT, ns)
    old_w = spec_weights(old["fits"], REF_VARIANT, ns)
    for c in D_BASE:
        assert got_w[c] == pytest.approx(old_w[c], abs=REPRO_TOL), f"λ({c})"

    # 반올림한 사전값도 Step 3a 가 적어 둔 것과 같아야 한다(그 파일이 곧 서빙 사양이다).
    for c, want_w in old["spec"]["weights"].items():
        assert round(got_w[c], 2) == pytest.approx(want_w), f"사전값 {c}"
    assert worst <= REPRO_TOL

    # 이 Step 이 실제로 낸 결과 파일도 같은 말을 해야 한다.
    out = Path(config.RESULTS_DIR) / "step3c-food-blend.json"
    if out.exists():
        blob = json.loads(out.read_text(encoding="utf-8"))
        assert blob["reproduction"]["ok"] is True
        assert blob["reproduction"]["max_delta"] <= REPRO_TOL
        assert blob["spec"]["source"] == "step3c"
