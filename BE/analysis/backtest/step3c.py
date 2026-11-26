# -*- coding: utf-8 -*-
"""Step 3c 실행기 — 음식 사진 성분을 블렌드에 얹고 서빙 사양을 갱신한다.

    ~/venvs/recsys/bin/python -m analysis.backtest.step3c

Step 3a 는 서버가 실을 성분 집합을 정했지만 **사진을 보기 전**이었다. Step 3b 가
음식 사진(CLIP 단일 벡터)이 난수 대조군을 넘고 인기 매칭 AUC 에서 `popularity` 까지
넘는다는 것을 보였으니, 남은 질문은 하나다 — **이미 있는 성분들 위에 음식 사진이
더 얹을 것이 남아 있는가.** 단독으로 좋은 것과 블렌드에 보태는 것은 다른 이야기다
(Step 2 의 `review` 가 그 예다: 가장 큰 λ 를 받고도 ablation 에서 죽었다).

변종 네 개를 **Step 3a 와 똑같은 방법**(쌍대 로지스틱 + 바깥 5-fold)으로 비교한다.

  (d) `pop+quality+cf+menu+text`  — Step 3a 가 고른 서빙 사양. **참조**이자 재현 검사다
                                     (숫자가 소수 4자리까지 같지 않으면 뭔가 어긋난 것이다).
  (g) (d) + `food`               — 이 Step 의 주인공.
  (h) (g) − `menu`               — 음식 사진이 메뉴를 대신하는가(둘 다 '무엇을 파는가' 다).
  (i) (g) − `text`               — 음식 사진이 요약문을 대신하는가.

**채택 규칙은 미리 못 박는다** — (g) 가 (d) 대비 CI 로 **이긴 칸이 하나 이상**이고
**진 칸이 하나도 없을 때만**(과제 P 16칸 + 과제 R 4칸을 함께 센다) `food` 를 넣는다.
Step 3a 의 제거 규칙("지는 칸 0 + 상관상 중복")과 방향만 반대인 같은 잣대다.

`food` 는 **음식 사진만** 쓴다. Step 3b 에서 공간 사진은 어느 지표에서도 난수 대조군을
CI 로 넘지 못했다 — 점수에 넣을 근거가 없으므로 성분 후보에도 올리지 않는다.

사진이 없는 가게(후보 풀의 15.5%)는 다른 modality 와 **같은 NaN 규칙**이다 — 유사도가
NaN 이고, 후보 안 z 정규화 뒤 0(그 사용자 후보의 평균 = '모른다')이 된다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np

from analysis.content import blend as blend_mod
from analysis.content import photos as photo_content   # import 만으로 scorer 가 등록된다

from . import cache, config, cv, report, run, scorers
from .bootstrap import paired_diff_ci
from .data import load_dataset
from .evaluate import evaluate
from .pool import attach_crawled_quality, build_pool
from .scorers import ScoreContext
from .scorers_menu import LAMBDAS
from .step1_5 import _mean, hybrid_name
from .step2 import METRICS4, _fmt_ci, finalist_table
from .step3a import cell_verdict, corr_table

REFS = {"task_p": "popularity", "task_r": "quality"}

#: Step 1.5 가 세운 막대. Step 2·3a 와 같은 것이다.
BAR_CONTENT = "cf_item_sum"

#: 성분 이름 -> 그 성분을 만드는 scorer. Step 3a 의 표에 `food` 한 줄을 더한 것이고,
#: 이름은 **서빙 파일의 키와 같다**(`serving_prior.json` 을 서버가 그대로 읽는다).
#: `review` 는 어느 변종에도 안 들어가지만 **상관 표를 Step 3a 와 나란히 읽으려고**
#: 성분 통에는 남겨 둔다.
COMPONENT_MODELS: dict[str, str] = {
    "popularity": "popularity",
    "quality": "quality",
    "cf": "cf_item_sum",
    "menu": "menu_top8_max",
    "text": "text_lse01",
    "review": "review_longest12_single",
    "food": "photo_food_max",
}

#: 이 Step 이 얹는 성분과 그 modality(Step 3b 의 사진 공간).
FOOD_COMPONENT = "food"
FOOD_KIND = "food"

#: 블렌드 λ 표에 그리는 성분(가중치 1 로 고정되는 `popularity` 와 어느 변종에도 없는
#: `review` 는 뺀다).
BLEND_COMPS = [c for c in COMPONENT_MODELS if c not in ("popularity", "review")]

#: 비교할 블렌드 변종. 첫 성분은 언제나 `popularity` 다(가중치 1 로 고정되는 자리).
D_BASE = ["popularity", "quality", "cf", "menu", "text"]
G_FOOD = D_BASE + ["food"]
VARIANTS: dict[str, dict] = {
    "blend_d_no_review_quality": {
        "label": "(d)", "components": list(D_BASE),
        "note": "Step 3a 가 고른 서빙 사양 — **참조**이자 재현 검사(소수 4자리까지 "
                "Step 3a 와 같아야 한다). 이름도 Step 3a 의 것을 그대로 쓴다"},
    "blend_g_food": {
        "label": "(g)", "components": list(G_FOOD),
        "note": "(d) + `food`(`photo_food_max`) — 이 Step 의 주인공"},
    "blend_h_food_no_menu": {
        "label": "(h)", "components": [c for c in G_FOOD if c != "menu"],
        "note": "(g) − `menu`. 음식 사진이 메뉴를 대신하는지 본다"},
    "blend_i_food_no_text": {
        "label": "(i)", "components": [c for c in G_FOOD if c != "text"],
        "note": "(g) − `text`. 음식 사진이 요약문을 대신하는지 본다"},
}

#: 참조 변종 — 채택 판정의 기준이자 재현 검사의 대상.
REF_VARIANT = "blend_d_no_review_quality"
FOOD_VARIANT = "blend_g_food"

#: 성분 제거 시험 — (뺀 성분, 뺀 쪽, 안 뺀 쪽). 둘 다 **다시 학습된** 것이다.
REMOVALS: list[tuple[str, str, str]] = [
    ("menu", "blend_h_food_no_menu", "blend_g_food"),
    ("text", "blend_i_food_no_text", "blend_g_food"),
]

#: 서빙 사전값의 폭. Step 3a 와 같은 값이고 **선택이지 추정값이 아니다.**
PRIOR_SD = 0.5

#: 재현 검사의 허용 오차. BLAS 스레드 수에 따른 마지막 자리 흔들림만 허용한다.
REPRO_TOL = 1e-4

#: Step 3a 결과 파일 — 재현 검사가 읽는다.
STEP3A_RESULT = "step3a-final-spec"


# ────────────────────────────────────────────────── 표
def variant_table(results: dict, results_ref: dict, ns: list[int], metric: str,
                  ref_name: str) -> str:
    """변종 × N 한 지표. 셀 아래는 기준 대비 CI."""
    return finalist_table(results, results_ref, list(VARIANTS), ns, metric, ref_name)


def r_auc_table(results: dict, results_ref: dict, ns: list[int], models: list[str],
                ref_name: str) -> str:
    """과제 R 의 AUC — 셀 아래는 기준 변종 대비 CI."""
    out = ["| 모델 | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|" + "---|" * len(ns)]
    for m in models:
        cells = []
        for n in ns:
            e = results[m][str(n)]["task_r"]["auc"]
            if m == ref_name:
                cells.append(f"{e['mean']:.4f} <br><sub>(기준)</sub>")
            else:
                ci = results_ref[m][str(n)]["task_r"]["auc"]["vs_baseline"]
                cells.append(f"{e['mean']:.4f} <br><sub>{_fmt_ci(ci)}</sub>")
        out.append(f"| `{m}` | " + " | ".join(cells) + " |")
    return "\n".join(out)


def removal_table(removals: dict, ns: list[int], metric: str) -> str:
    """제거 시험 — 자식(뺀 쪽) − 부모(안 뺀 쪽). 음수가 유의하면 그 성분이 기여한 것."""
    out = ["| 뺀 성분 | 비교 | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|---|" + "---|" * len(ns)]
    for rec in removals.values():
        cells = [_fmt_ci(rec["ci"][str(n)][metric]) for n in ns]
        out.append(f"| `{rec['component']}` | {VARIANTS[rec['child']]['label']} − "
                   f"{VARIANTS[rec['parent']]['label']} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def lambda_table(fits: dict, ns: list[int]) -> str:
    """변종 × N 의 폴드 평균 λ (w_pop = 1 정규화). 아래 첨자는 폴드 간 표준편차."""
    out = ["| 변종 | N | " + " | ".join(f"λ({c})" for c in BLEND_COMPS) + " |",
           "|---|---|" + "---|" * len(BLEND_COMPS)]
    for key, meta in VARIANTS.items():
        for n in ns:
            per_fold = fits[key][str(n)]
            cells = []
            for c in BLEND_COMPS:
                if c not in meta["components"]:
                    cells.append("·")
                    continue
                vals = [f["weights"].get(c, 0.0) for f in per_fold]
                cells.append(f"{np.mean(vals):.2f} <sub>±{np.std(vals):.2f}</sub>")
            out.append(f"| {meta['label']} `{key}` | {n} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def lambda_fold_table(fits: dict, ns: list[int]) -> str:
    """폴드 하나하나의 λ. 다섯 줄이 비슷해야 '학습이 안정적이다' 를 말할 수 있다."""
    out = ["| 변종 | N | 폴드 | "
           + " | ".join(f"λ({c})" for c in BLEND_COMPS) + " | α(L2) |",
           "|---|---|---|" + "---|" * (len(BLEND_COMPS) + 1)]
    for key, meta in VARIANTS.items():
        for n in ns:
            for i, f in enumerate(fits[key][str(n)], 1):
                cells = [(f"{f['weights'][c]:.3f}" if c in meta["components"] else "·")
                         for c in BLEND_COMPS]
                out.append(f"| {meta['label']} | {n} | {i} | " + " | ".join(cells)
                           + f" | {f['alpha']:g} |")
    return "\n".join(out)


def addition_verdict(results_ref: dict, model: str, ns: list[int]) -> dict:
    """참조 변종 대비 CI 로 이긴/진 칸. **과제 P 와 R 을 함께 센다.**

    과제 R 을 같이 세는 이유는 Step 3a 의 제거 판정과 같다 — 성분의 값어치가 R 에만
    있을 수 있고(`quality` 가 그랬다), P 만 보면 규칙이 그것을 못 본다. 여기서는
    방향이 반대로 쓰인다: R 에서 **지면** `food` 를 안 넣는다.
    """
    v = cell_verdict(results_ref, model, ns)          # 과제 P 16칸
    win_r, lose_r = [], []
    for n in ns:
        ci = results_ref[model][str(n)]["task_r"]["auc"]["vs_baseline"]
        if ci["significant"]:
            (win_r if ci["diff"] > 0 else lose_r).append(f"r_auc N={n}")
    return v | {"win_r": win_r, "lose_r": lose_r, "n_cells_r": len(ns)}


def removal_verdict(rec: dict, ns: list[int]) -> dict:
    """제거가 CI 로 진 칸 / 이긴 칸. 과제 P 와 R 을 나눠서 센다(Step 3a 와 같다)."""
    win, lose, win_r, lose_r = [], [], [], []
    for metric in METRICS4:
        for n in ns:
            ci = rec["ci"][str(n)][metric]
            if ci["significant"]:
                (win if ci["diff"] > 0 else lose).append(f"{metric} N={n}")
    for n in ns:
        ci = rec["ci"][str(n)]["r_auc"]
        if ci["significant"]:
            (win_r if ci["diff"] > 0 else lose_r).append(f"r_auc N={n}")
    return {"win": win, "lose": lose, "win_r": win_r, "lose_r": lose_r,
            "n_cells": len(METRICS4) * len(ns), "n_cells_r": len(ns)}


# ────────────────────────────────────────────────── 서빙 사양
def spec_weights(fits: dict, key: str, ns: list[int]) -> dict:
    """변종의 N 평균 λ (폴드 평균을 N 으로 다시 평균). 반올림 전 원값."""
    out = {}
    for c in VARIANTS[key]["components"]:
        if c == "popularity":
            out[c] = 1.0
            continue
        per_n = [float(np.mean([f["weights"].get(c, 0.0) for f in fits[key][str(n)]]))
                 for n in ns]
        out[c] = float(np.mean(per_n))
    return out


def decide_food(results_vs_ref: dict, ns: list[int]) -> dict:
    """`food` 를 넣을지 정한다 — 이긴 칸 ≥ 1 이고 진 칸 0 일 때만 넣는다.

    Step 3a 의 제거 규칙("지는 칸 0 + 상관상 중복")과 짝을 이루는 잣대다. 다른 점
    하나: **상관 조건을 걸지 않는다.** 상관은 '빼도 되나' 를 판단할 때 쓰는 보조
    근거이고(중복이면 빼는 쪽에 힘을 싣는다), 넣을 때는 지표가 직접 답한다.
    """
    v = addition_verdict(results_vs_ref, FOOD_VARIANT, ns)
    win = list(v["win"]) + list(v["win_r"])
    lose = list(v["lose"]) + list(v["lose_r"])
    adopt = bool(win) and not lose
    return {"component": FOOD_COMPONENT, "from": REF_VARIANT, "to": FOOD_VARIANT,
            "win": v["win"], "lose": v["lose"],
            "win_r": v["win_r"], "lose_r": v["lose_r"],
            "n_cells": v["n_cells"], "n_cells_r": v["n_cells_r"],
            "adopted": adopt,
            "variant": FOOD_VARIANT if adopt else REF_VARIANT,
            "components": list(VARIANTS[FOOD_VARIANT if adopt else REF_VARIANT]
                               ["components"]),
            "rule": "이긴 칸 ≥ 1 이고 진 칸 0 (과제 P 16칸 + 과제 R 4칸)"}


# ────────────────────────────────────────────────── 재현 검사 (Step 3a 대비)
def reproduction_check(results: dict, fits: dict, bar_model: str, ns: list[int],
                       corr: dict, *, results_dir: Path | None = None,
                       tol: float = REPRO_TOL) -> dict:
    """(d) 와 막대가 Step 3a 의 숫자를 다시 내는지 본다.

    같은 seed·같은 분할·같은 코드이므로 **자리 하나까지 같아야 한다.** 다르면
    이 Step 의 나머지 비교가 전부 의미를 잃으므로 결과 .md 의 맨 앞에 적는다.
    성분 상관도 같이 본다 — `food` 열을 하나 더 붙였을 뿐 나머지 칸은 그대로여야 한다.
    """
    path = Path(results_dir or config.RESULTS_DIR) / f"{STEP3A_RESULT}.json"
    if not path.exists():
        return {"ok": None, "reason": f"{path.name} 이 없다 — 재현 검사를 건너뛴다"}
    old = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict] = []

    def add(what: str, a: float, b: float) -> None:
        rows.append({"what": what, "step3a": float(a), "step3c": float(b),
                     "delta": float(b) - float(a)})

    for n in ns:
        add(f"막대 auc N={n}",
            old["results"][bar_model][str(n)]["task_p"]["auc"]["mean"],
            results[bar_model][str(n)]["task_p"]["auc"]["mean"])
    for metric in METRICS4:
        for n in ns:
            add(f"(d) {metric} N={n}",
                old["results"][REF_VARIANT][str(n)]["task_p"][metric]["mean"],
                results[REF_VARIANT][str(n)]["task_p"][metric]["mean"])
    for n in ns:
        add(f"(d) r_auc N={n}",
            old["results"][REF_VARIANT][str(n)]["task_r"]["auc"]["mean"],
            results[REF_VARIANT][str(n)]["task_r"]["auc"]["mean"])
    old_w = spec_weights_of(old["fits"], REF_VARIANT,
                            old["variants"][REF_VARIANT]["components"], ns)
    new_w = spec_weights(fits, REF_VARIANT, ns)
    for c in VARIANTS[REF_VARIANT]["components"]:
        if c != "popularity":
            add(f"(d) λ({c}) N 평균", old_w[c], new_w[c])

    # 성분 상관 — Step 3a 의 이름들만 골라 같은 칸을 견준다.
    old_names, new_names = old["correlation"]["names"], corr["names"]
    old_mat = np.asarray(old["correlation"]["per_user_mean"], dtype=float)
    new_mat = np.asarray(corr["per_user_mean"], dtype=float)
    corr_delta = 0.0
    for i, a in enumerate(old_names):
        for j, b in enumerate(old_names):
            if a in new_names and b in new_names:
                d = abs(new_mat[new_names.index(a), new_names.index(b)] - old_mat[i, j])
                corr_delta = max(corr_delta, float(np.nan_to_num(d)))

    worst = max(rows, key=lambda r: abs(r["delta"]))
    max_delta = float(abs(worst["delta"]))
    return {"ok": max_delta <= tol and corr_delta <= tol, "tol": tol,
            "rows": rows, "max_delta": max_delta, "worst": worst["what"],
            "corr_max_delta": corr_delta, "n_checked": len(rows),
            "source": f"{STEP3A_RESULT}.json"}


def spec_weights_of(fits: dict, key: str, components: list[str],
                    ns: list[int]) -> dict:
    """`spec_weights` 와 같은 계산이되 성분 목록을 밖에서 받는다(옛 blob 용)."""
    out = {}
    for c in components:
        if c == "popularity":
            out[c] = 1.0
            continue
        per_n = [float(np.mean([f["weights"].get(c, 0.0) for f in fits[key][str(n)]]))
                 for n in ns]
        out[c] = float(np.mean(per_n))
    return out


# ────────────────────────────────────────────────── main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analysis.backtest.step3c",
        description="Step 3c — 음식 사진 성분을 블렌드에 얹고 서빙 사양을 갱신한다.")
    ap.add_argument("--N", default=",".join(str(n) for n in config.HISTORY_SIZES))
    ap.add_argument("--out", default="step3c-food-blend")
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--history", default=config.DEFAULT_HISTORY_MODE,
                    choices=list(config.HISTORY_MODES))
    ap.add_argument("--select-metric", default=f"ndcg@{config.NDCG_K}",
                    choices=list(config.SELECT_METRICS),
                    help="막대(하이브리드)의 λ 를 고르는 잣대 — Step 3a 와 같아야 한다")
    ap.add_argument("--corr-N", type=int, default=5,
                    help="성분 상관을 잴 이력 길이 (Step 3a 와 같아야 표가 이어진다)")
    ap.add_argument("--resamples", type=int, default=config.BOOTSTRAP_RESAMPLES)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-spec", action="store_true",
                    help="`serving_prior.json` 을 건드리지 않는다(예행 연습)")
    ap.add_argument("--rebuild-md", action="store_true",
                    help="실험을 다시 돌리지 않고 저장된 json 에서 .md 만 다시 쓴다")
    args = ap.parse_args(argv)

    if args.rebuild_md:
        path = Path(config.RESULTS_DIR) / f"{args.out}.json"
        _write(json.loads(path.read_text(encoding="utf-8")), args.out)
        return 0

    ns = [int(x) for x in args.N.split(",") if x.strip()]
    corr_n = args.corr_N if args.corr_N in ns else ns[0]
    metric = args.select_metric

    print("데이터 로드 …", flush=True)
    ds = load_dataset()
    pool = build_pool(ds)
    splits, _ = cache.load_or_build(ds, pool, use_cache=not args.no_cache)
    eval_users = {s.user_id for s in splits}
    attach_crawled_quality(pool, ds, eval_users)
    store_names = {s: (ds.stores.get(s, {}).get("name") or "") for s in pool.ids}
    ctx = ScoreContext(pool=pool, seed=args.seed, history_mode=args.history,
                       extras={"candidates": run.seen_masks(splits, pool),
                               "dataset": ds, "eval_users": eval_users,
                               "store_names": store_names})
    print(f"  후보 풀 {len(pool):,}곳 · 평가 사용자 {len(splits)}명 · "
          f"이력 해석 `{args.history}`", flush=True)

    # ── 0) 음식 사진 커버리지 (Step 3b 의 표를 여기서 다시 잰다 — 성분의 전제다)
    space = photo_content.photo_space(ctx, FOOD_KIND)
    counts = np.asarray(space.packed.counts, dtype=float)
    covered = np.isfinite(space.vecs).all(axis=1)
    food_meta = {
        "kind": FOOD_KIND, "model": COMPONENT_MODELS[FOOD_COMPONENT],
        "stores": int(counts.size), "covered": int(covered.sum()),
        "missing": int((~covered).sum()),
        "covered_ratio": float(covered.mean()),
        "missing_ratio": float((~covered).mean()),
        "photos": int(counts.sum()),
        "per_covered": float(counts[covered].mean()) if covered.any() else float("nan"),
        "count_pop_corr": float(np.corrcoef(counts, pool.log_review_count)[0, 1]),
        "cover_pop_corr": float(np.corrcoef(covered.astype(float),
                                            pool.log_review_count)[0, 1]),
    }
    print(f"  음식 사진: {food_meta['covered']:,}/{food_meta['stores']:,}곳 "
          f"({100 * food_meta['covered_ratio']:.1f}%) · 없는 곳 "
          f"{food_meta['missing']:,}곳({100 * food_meta['missing_ratio']:.1f}%) · "
          f"개수↔인기 {food_meta['count_pop_corr']:.3f}", flush=True)

    # ── 1) 단독 모델 (성분 하나하나가 혼자서는 무엇인지)
    singles = list(dict.fromkeys(COMPONENT_MODELS.values()))
    raw: dict[str, dict] = {}
    for name in singles:
        raw[name] = {}
        for n in ns:
            raw[name][n] = evaluate(scorers.get(name), splits, ctx, n)
        print(f"  {name:<26} AUC(P) "
              + " ".join(f"N={n}:{_mean(raw[name][n].p['auc']):.4f}" for n in ns),
              flush=True)

    # ── 2) 성분 통 (N 마다 한 번) + 상관 (Step 3a 표의 `food` 확장)
    banks = {}
    for n in ns:
        banks[n] = blend_mod.ComponentBank.build(COMPONENT_MODELS, splits, ctx, n)
        print(f"  성분 통 N={n} 완성 ({banks[n].n_components}성분)", flush=True)
    corr = blend_mod.component_correlations(
        banks[corr_n], extra={"log1p_review_count": pool.log_review_count})
    corr["n_history"] = corr_n
    print(f"  상관 행렬: {corr['n_rows']:,}행 (사용자 {corr['n_users']}명 × 후보)",
          flush=True)

    # ── 3) 블렌드 변종 (전부 쌍대 로지스틱, 바깥 5-fold — Step 3a 와 같은 설정)
    fits: dict = {}
    for key, meta in VARIANTS.items():
        fits[key] = {}
        raw[key] = {}
        for n in ns:
            res, per_fold = blend_mod.outer_cv_blend(
                banks[n], splits, ctx, fitter="pairwise", seed=args.seed, name=key,
                components=meta["components"])
            raw[key][n] = res
            fits[key][str(n)] = per_fold
        print(f"  {meta['label']} {key:<26} AUC(P) "
              + " ".join(f"N={n}:{_mean(raw[key][n].p['auc']):.4f}" for n in ns),
              flush=True)

    # ── 4) 막대 (Step 1.5 의 CF 하이브리드, λ 를 폴드 밖에서 고른다)
    bar_model = hybrid_name(BAR_CONTENT, metric)
    raw[bar_model] = {}
    bar_chosen: dict = {}
    candidates = {f"{lam:g}": (lambda lam=lam: scorers.get(
        f"hybrid_pop_{BAR_CONTENT}_l{lam:g}")) for lam in LAMBDAS}
    for n in ns:
        res, folds = cv.outer_cv(candidates, splits, ctx, n, metric=metric,
                                 seed=args.seed)
        raw[bar_model][n] = res
        bar_chosen[str(n)] = folds
        print(f"  {bar_model:<34} N={n:<3} λ={folds} "
              f"AUC(P)={_mean(res.p['auc']):.4f}", flush=True)

    # ── 5) 집계 — 기준 셋(인기 · 막대 · (d))
    model_names = singles + [bar_model] + list(VARIANTS)
    results = run.aggregate(raw, model_names, ns, REFS,
                            resamples=args.resamples, seed=args.seed)
    results_bar = run.aggregate(raw, model_names, ns,
                                {"task_p": bar_model, "task_r": REFS["task_r"]},
                                resamples=args.resamples, seed=args.seed)
    results_d = run.aggregate(raw, model_names, ns,
                              {"task_p": REF_VARIANT, "task_r": REF_VARIANT},
                              resamples=args.resamples, seed=args.seed)
    users_meta = run.summarise_users(splits, raw, model_names, ns,
                                     history_mode=args.history)

    # ── 6) 성분 제거 시험 ((h)·(i) — 사진이 메뉴/요약문을 대신하는가)
    removals: dict = {}
    for comp, child, parent in REMOVALS:
        rec = {"component": comp, "child": child, "parent": parent, "ci": {}}
        for n in ns:
            rec["ci"][str(n)] = {
                m: paired_diff_ci(raw[child][n].p[m], raw[parent][n].p[m],
                                  resamples=args.resamples, seed=args.seed)
                for m in METRICS4}
            rec["ci"][str(n)]["r_auc"] = paired_diff_ci(
                raw[child][n].r_auc, raw[parent][n].r_auc,
                resamples=args.resamples, seed=args.seed)
        removals[f"{comp}:{child}"] = rec
        v = removal_verdict(rec, ns)
        print(f"  제거 {comp:<6} {VARIANTS[child]['label']}−{VARIANTS[parent]['label']}"
              f"  진 칸 {len(v['lose']) + len(v['lose_r'])} · "
              f"이긴 칸 {len(v['win']) + len(v['win_r'])}", flush=True)

    # ── 7) 재현 검사와 판정
    repro = reproduction_check(results, fits, bar_model, ns, corr)
    if repro.get("ok") is None:
        print(f"  재현 검사 건너뜀 — {repro['reason']}", flush=True)
    else:
        print(f"  재현 검사: 최대 편차 {repro['max_delta']:.2e} "
              f"(상관 {repro['corr_max_delta']:.2e}) · "
              f"{'통과' if repro['ok'] else '실패'}", flush=True)

    decision = decide_food(results_d, ns)
    chosen_key = decision["variant"]
    weights_raw = spec_weights(fits, chosen_key, ns)
    weights = {c: round(v, 2) for c, v in weights_raw.items()}
    spec = {"components": [c for c in COMPONENT_MODELS
                           if c in decision["components"]],
            "weights": {c: weights[c] for c in COMPONENT_MODELS
                        if c in decision["components"]},
            "prior_sd": PRIOR_SD, "source": "step3c",
            "n_users": len(splits), "history": args.history}
    print(f"  판정: `{FOOD_COMPONENT}` {'채택' if decision['adopted'] else '기각'} "
          f"(이긴 칸 {len(decision['win']) + len(decision['win_r'])} · "
          f"진 칸 {len(decision['lose']) + len(decision['lose_r'])})", flush=True)
    print(f"  서빙 사양: {chosen_key} {spec['weights']}", flush=True)

    blob = {
        "run": args.out,
        "title": "Step 3c — 음식 사진 성분과 서빙 사양 갱신",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": run._git_commit(),
        "seed": args.seed,
        "history_mode": args.history,
        "references": REFS,
        "baseline_model": REFS["task_p"],
        "history_sizes": ns,
        "intro": [
            "Step 3a 는 서버가 실을 성분 집합을 정했지만 **사진을 보기 전**이었다."
            " Step 3b 가 음식 사진(CLIP 단일 벡터)이 난수 대조군을 넘고 인기 매칭"
            " AUC 에서 `popularity` 까지 넘는다는 것을 보였으니, 남은 질문은 하나다 —"
            " **이미 있는 성분들 위에 음식 사진이 더 얹을 것이 남아 있는가.**",
            "단독으로 좋은 것과 블렌드에 보태는 것은 다른 이야기다 — Step 2 의"
            " `review` 는 가장 큰 λ 를 받고도 ablation 에서 죽었다(인기와 스피어만"
            " 0.67).",
            "학습 방법·분할·bootstrap 은 Step 3a 와 **완전히 같다**. 참조 변종 (d) 를"
            " 다시 돌려 Step 3a 의 숫자를 소수 4자리까지 재현하는지 먼저 확인한다.",
        ],
        "command": [
            "~/venvs/recsys/bin/python -m analysis.backtest.step3c \\",
            f"    --N {','.join(str(n) for n in ns)} --history {args.history} \\",
            f"    --out {args.out}",
        ],
        "config": {
            "hit_k": config.HIT_K, "recall_k": config.RECALL_K, "ndcg_k": config.NDCG_K,
            "bootstrap_resamples": args.resamples,
            "history_mode": args.history, "select_metric": metric,
            "corr_n_history": corr_n,
            "blend_l2_grid": list(blend_mod.L2_GRID),
            "blend_negatives": blend_mod.N_NEGATIVES,
            "lambdas": list(LAMBDAS),
            "prior_sd": PRIOR_SD,
            "repro_tol": REPRO_TOL,
            "split_cache_fingerprint": cache.fingerprint(),
        },
        "pool": {"size": len(pool), "stores_total": len(ds.stores),
                 "reviews_total": len(ds.reviews)},
        "users": users_meta,
        "bar_model": bar_model,
        "bar_chosen_per_fold": bar_chosen,
        "component_models": COMPONENT_MODELS,
        "food": food_meta,
        "correlation": corr,
        "variants": {k: {"label": v["label"], "components": v["components"],
                         "note": v["note"]} for k, v in VARIANTS.items()},
        "fits": fits,
        "removals": removals,
        "reproduction": repro,
        "decision": decision,
        "spec": spec,
        "models": {m: {"description": scorers.get(m).description} for m in singles}
        | {bar_model: {"description":
                       f"popularity_z + λ·(`{BAR_CONTENT}`)_z — λ 는 사용자 5-fold "
                       f"out-of-fold 선택, 기준 `{metric}` (Step 1.5 의 막대)"}}
        | {k: {"description": f"{v['label']} 구성요소 블렌드(쌍대 로지스틱) — 성분 "
                              + ", ".join(f"`{c}`" for c in v["components"])}
           for k, v in VARIANTS.items()},
        "results": results,
        "results_vs_bar": results_bar,
        "results_vs_d": results_d,
    }
    _write(blob, args.out)

    if args.no_spec:
        print("서빙 사양은 쓰지 않았다(--no-spec)", flush=True)
    else:
        spec_path = Path(config.RESULTS_DIR) / "serving_prior.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print(f"기록: {spec_path}", flush=True)
    return 0


# ────────────────────────────────────────────────── 글 (숫자는 blob 에서만 읽는다)
def reproduction_section(blob: dict) -> str:
    r = blob["reproduction"]
    if r.get("ok") is None:
        return f"재현 검사를 하지 못했다 — {r['reason']}."
    ns = blob["history_sizes"]
    body = [
        f"(d) `{REF_VARIANT}` 와 막대 `{blob['bar_model']}` 는 Step 3a 에서 이미 잰 "
        "것이다. seed·분할·코드가 같으므로 **자리 하나까지 같아야 한다** — 다르면 이 "
        "Step 의 나머지 비교가 전부 의미를 잃는다. 성분 통에 `food` 열이 하나 늘었을 "
        "뿐 (d) 의 계산에는 그 열이 들어가지 않는다(`subset_bank` 가 행을 골라낸다).",
        "",
        f"- 검사한 값 **{r['n_checked']}개** (막대 AUC {len(ns)} + (d) 지표 "
        f"{len(METRICS4)}×{len(ns)} + (d) 과제 R {len(ns)} + (d) λ "
        f"{len(VARIANTS[REF_VARIANT]['components']) - 1}).",
        f"- **최대 편차 {r['max_delta']:.2e}** (가장 큰 곳: {r['worst']}), 허용치 "
        f"{r['tol']:.0e} → **{'통과' if r['ok'] else '실패'}**.",
        f"- 성분 상관 표의 겹치는 칸 최대 편차 **{r['corr_max_delta']:.2e}**.",
        f"- 출처: `analysis/backtest/results/{r['source']}`.",
        "",
        "| 값 | Step 3a | Step 3c | 차이 |",
        "|---|---|---|---|",
    ]
    for row in r["rows"]:
        body.append(f"| {row['what']} | {row['step3a']:.4f} | {row['step3c']:.4f} | "
                    f"{row['delta']:+.1e} |")
    return "\n".join(body)


def food_section(blob: dict) -> str:
    m = blob["food"]
    return "\n".join([
        f"성분 `{FOOD_COMPONENT}` 는 Step 3b 의 `{m['model']}` 다 — **가게당 CLIP 벡터 "
        "하나**(그 가게 음식 사진 벡터의 평균을 L2 정규화)의 코사인을 이력 위에서 "
        "`max_{h∈L}` 로 접는다. 집합(MaxSim)이 아니라 단일 벡터인 것은 Step 2 의 리뷰 "
        "결론(집합은 개수 편향을 타고 인기를 흉내낸다)을 사진에도 적용한 것이고, Step "
        "3b 에서 두 표현의 차이가 지표로 갈리지 않았다.",
        "",
        f"**공간 사진은 성분 후보에 올리지 않았다.** Step 3b 에서 `photo_space_*` 는 "
        "어느 지표에서도 난수 대조군을 CI 로 넘지 못했다 — 점수에 넣을 근거가 없다.",
        "",
        f"- 후보 풀 {m['stores']:,}곳 중 음식 사진이 있는 곳 **{m['covered']:,}곳"
        f"({100 * m['covered_ratio']:.1f}%)**, 없는 곳 {m['missing']:,}곳"
        f"(**{100 * m['missing_ratio']:.1f}%**). 사진 합 {m['photos']:,}장, 커버된 "
        f"가게당 평균 {m['per_covered']:.2f}장.",
        f"- 사진 개수 ↔ `log1p(리뷰 수)` 상관 {m['count_pop_corr']:.3f}, "
        f"있음/없음 ↔ `log1p(리뷰 수)` 상관 {m['cover_pop_corr']:.3f}.",
        "",
        f"**없는 {100 * m['missing_ratio']:.1f}% 는 0 이 아니라 '모른다' 로 다룬다.** "
        "유사도가 NaN 이고, 후보 안에서 z 정규화한 뒤 0(= 그 사용자 후보의 평균)이 "
        "된다 — 메뉴·요약문과 **같은 규칙**이다(`scorers_menu.content_score` 의 중립 "
        "채움 + `blend.zscore_masked` 의 NaN→0). 0 으로 먼저 채우면 '아무와도 안 닮은 "
        "가게' 가 되어 블렌드가 구조적으로 밀어낸다.",
    ])


def correlation_section(blob: dict) -> str:
    c = blob["correlation"]
    names = c["names"]
    per_user = np.asarray(c["per_user_mean"], dtype=float)
    comps = [n for n in names if n != "log1p_review_count"]
    pop_i = names.index("log1p_review_count")
    food_i = names.index(FOOD_COMPONENT)

    others = sorted(((abs(per_user[food_i, j]), comps[j], per_user[food_i, j])
                     for j in range(len(comps)) if comps[j] != FOOD_COMPONENT),
                    reverse=True)
    food_line = ", ".join(f"`{n}` {v:+.3f}" for _, n, v in others)

    return "\n".join([
        "Step 3a 의 표(PLAN 7절 결정 5번)에 `food` 열을 하나 더한 것이다. 나머지 칸은 "
        "Step 3a 와 **같은 값**이어야 한다 — 스피어만은 쌍끼리의 순위 상관이라 열을 "
        "더해도 다른 칸이 변하지 않는다(위 '재현 확인' 절이 그것도 잰다).",
        "",
        f"이력 N={c['n_history']} 에서 평가 사용자 {c['n_users']}명의 후보별 z 점수를 "
        f"모두 모아(총 {c['n_rows']:,}행) 냈다.",
        "",
        "**사용자 안에서 잰 상관의 평균** — 한 사용자의 후보 안에서 두 성분이 같은 "
        "순서로 세우는가.",
        "",
        corr_table(names, c["per_user_mean"]),
        "",
        "**사용자를 가로로 쌓아 풀링한 상관**",
        "",
        corr_table(names, c["pooled"]),
        "",
        f"- `food` 와 다른 성분: {food_line}.",
        f"- `food` ↔ `log1p(리뷰 수)` **{per_user[food_i, pop_i]:+.3f}** "
        f"(풀링 {np.asarray(c['pooled'])[food_i][pop_i]:+.3f}). "
        "`review` 의 +0.673 과 견줘 보라 — 음식 사진 점수는 인기의 다른 이름이 아니다.",
    ])


def variants_section(blob: dict) -> str:
    ns = blob["history_sizes"]
    bar = blob["bar_model"]
    food_model = COMPONENT_MODELS[FOOD_COMPONENT]
    food_res = blob["results"][food_model]
    body = [
        "성분을 바꿔 가며 **같은 방법으로 다시 학습**한다 — 학습된 가중치를 그대로 두고 "
        "한 항만 0 으로 만드는 것은 '없앴을 때 얼마나 나쁜가' 가 아니라 '잘못 맞춘 "
        "모델이 얼마나 나쁜가' 를 재는 것이다. 학습은 전부 **쌍대 로지스틱(BPR) + 바깥 "
        "5-fold** 이고 Step 3a 와 같은 설정이다.",
        "",
        "| 변종 | 성분 | 메모 |",
        "|---|---|---|",
    ]
    for key, v in blob["variants"].items():
        body.append(f"| {v['label']} `{key}` | "
                    + ", ".join(f"`{c}`" for c in v["components"])
                    + f" | {v['note']} |")
    body += ["", "| 성분 | 쓴 모델 |", "|---|---|"]
    for comp, model in blob["component_models"].items():
        tail = " — 상관 표에만 쓴다(어느 변종에도 없다)" if comp == "review" else ""
        body.append(f"| `{comp}` | `{model}`{tail} |")

    body += ["", f"### 막대 `{bar}` 대비", "",
             "**굵게 표시된 것만 CI 가 0 을 넘겼다.** 굵은 음수는 막대에 **졌다**는 뜻이다."]
    for metric in METRICS4:
        body += ["", f"`{metric}`", "",
                 variant_table(blob["results"], blob["results_vs_bar"], ns, metric, "")]

    body += ["", "### (d) 대비 — 이 Step 이 실제로 묻는 것", "",
             "`food` 를 얹은 효과만 남는 비교다. 채택 규칙이 읽는 표이기도 하다.", ""]
    for metric in METRICS4:
        body += [f"`{metric}`", "",
                 variant_table(blob["results"], blob["results_vs_d"], ns, metric,
                               REF_VARIANT), ""]

    body += [
        "### 과제 R (방문 가게 중 만족)",
        "",
        f"미래 방문 가게만 놓고 rating ≥ {config.SATISFIED_MIN_RATING} 를 양성으로 잰 "
        f"AUC 다. 양쪽 클래스가 다 있는 **{blob['users']['task_r_users']}명**만 센다. "
        "셀 아래는 **(d) 대비** CI. 견줄 곳: `quality` **단독**은 "
        f"{blob['results']['quality'][str(ns[0])]['task_r']['auc']['mean']:.4f}, "
        "`popularity` 단독은 "
        f"{blob['results']['popularity'][str(ns[0])]['task_r']['auc']['mean']:.4f}, "
        f"`{food_model}` 단독은 "
        f"{food_res[str(ns[0])]['task_r']['auc']['mean']:.4f} 다.",
        "",
        r_auc_table(blob["results"], blob["results_vs_d"], ns, list(VARIANTS),
                    REF_VARIANT),
        "",
        "### 폴드 밖에서 학습된 λ (w_pop = 1 정규화)",
        "",
        "칸은 **폴드 5개의 평균**이고 아래 첨자는 폴드 간 표준편차다. `·` 는 그 변종에 "
        "없는 성분이다.",
        "",
        lambda_table(blob["fits"], ns),
        "",
        "폴드 하나하나:",
        "",
        lambda_fold_table(blob["fits"], ns),
        "",
        "`α(L2)` 는 학습 폴드 안에서 NDCG@20 으로 고른 정규화 계수다(격자 "
        f"{{{', '.join(f'{a:g}' for a in blob['config']['blend_l2_grid'])}}}). "
        + alpha_note(blob),
    ]
    return "\n".join(body)


def alpha_note(blob: dict) -> str:
    """고른 α 가 격자 경계에 얼마나 붙었는지 — 격자가 좁았다면 그것도 결과다."""
    ns = blob["history_sizes"]
    picks = [f["alpha"] for k in blob["fits"] for n in ns
             for f in blob["fits"][k][str(n)]]
    grid = list(blob["config"]["blend_l2_grid"])
    top = sum(1 for a in picks if a >= max(grid))
    bottom = sum(1 for a in picks if a <= min(grid))
    return (f"**폴드 {len(picks)}개 중 {top}개가 격자의 위 끝({max(grid):g})을 골랐다** "
            f"(아래 끝 {min(grid):g} 은 {bottom}개). 위 끝에 붙은 폴드가 있다는 것은 "
            "더 센 정규화를 시험해 보지 않았다는 뜻이고, Step 2·3a 도 같은 격자를 "
            "썼으므로 **이 Step 에 새로 생긴 문제는 아니다** — 다만 격자를 넓히면 "
            "λ 가 조금 작아질 수 있다는 것은 남는 한계다.")


def decision_section(blob: dict) -> str:
    ns = blob["history_sizes"]
    d = blob["decision"]
    spec = blob["spec"]
    win = list(d["win"]) + list(d["win_r"])
    lose = list(d["lose"]) + list(d["lose_r"])
    body = [
        f"**규칙(미리 정한 것): {d['rule']}.** Step 3a 의 제거 규칙('지는 칸 0 + 상관상 "
        "중복')과 짝을 이루는 잣대이고, 방향만 반대다. 상관 조건을 넣지 않은 것은 "
        "상관이 '빼도 되나' 의 보조 근거이지 '넣어도 되나' 의 근거는 아니기 때문이다 —"
        " 넣을 때는 지표가 직접 답한다.",
        "",
        f"- (g) 가 (d) 를 CI 로 **이긴 칸 {len(win)}개** / "
        f"{d['n_cells'] + d['n_cells_r']}칸"
        + (f": {', '.join(win)}." if win else "."),
        f"- (g) 가 (d) 에 CI 로 **진 칸 {len(lose)}개**"
        + (f": {', '.join(lose)}." if lose else " — 하나도 없다."),
        "",
        f"→ **`{d['component']}` 를 " + ("넣는다" if d["adopted"] else "넣지 않는다")
        + f".** 서빙 성분은 {blob['variants'][d['variant']]['label']} "
        f"`{d['variant']}` 의 것이 된다.",
        "",
    ]
    if not d["adopted"] and lose:
        gm, dm = blob["results"][FOOD_VARIANT], blob["results"][REF_VARIANT]
        body += [
            f"**막은 것은 {', '.join(lose)} 한 칸이다.** 오르는 칸(`auc_pm`)과 내리는 "
            "칸(`ndcg@20`)이 **다른 지표**라는 것이 이 판정의 실체다 — 음식 사진은 "
            "인기를 통제한 랭킹을 올리면서 맨 위 20장을 흐린다. 점 추정으로도 같은 "
            "방향이다: `hit@10` 은 "
            + ", ".join(f"N={n} {gm[str(n)]['task_p']['hit@10']['mean']:.4f} vs "
                        f"{dm[str(n)]['task_p']['hit@10']['mean']:.4f}" for n in ns)
            + " ((g) vs (d)) 로 네 N 중 세 곳에서 내려간다(CI 는 못 넘겼다).",
            "",
            "**규칙을 결과를 보고 고치지 않는다.** '`auc_pm` 만 보고 켠다' 로 바꾸면 "
            "이번에는 켜지지만, 그 규칙은 앞으로 어떤 성분도 못 막는다. 이 칸을 "
            "뒤집고 싶으면 성분을 바꾸는 것(커버리지·집계·표현)이지 잣대를 바꾸는 "
            "것이 아니다.",
            "",
        ]
    body += [
        "### 제거 시험 — 음식 사진이 메뉴·요약문을 대신하는가",
        "",
        "(h)·(i) 는 `food` 를 넣은 뒤에 `menu`/`text` 를 빼 본 것이다. 자식(뺀 쪽) − "
        "부모(안 뺀 쪽)이므로 **음수가 유의하면 그 성분은 여전히 기여한다**(사진이 "
        "대신하지 못한다).",
        "",
    ]
    for metric in METRICS4 + ["r_auc"]:
        body += [f"`{metric}`" + (" (과제 R)" if metric == "r_auc" else ""), "",
                 removal_table(blob["removals"], ns, metric), ""]
    for key, rec in blob["removals"].items():
        v = removal_verdict(rec, ns)
        loses = list(v["lose"]) + list(v["lose_r"])
        body.append(
            f"- `{rec['component']}` 를 빼면 "
            + (f"**{len(loses)}칸에서 진다** ({', '.join(loses)}) — 여전히 필요하다."
               if loses else
               "**지는 칸이 없다** — 다만 이 Step 은 성분을 빼는 자리가 아니다(뺄지는 "
               "상관과 서빙 비용까지 보고 Step 3a 규칙으로 따로 판단한다)."))

    comps = [c for c in spec["components"] if c != "popularity"]
    fits = blob["fits"][d["variant"]]
    body += [
        "",
        "### 서빙 사양",
        "",
        f"채택: **{blob['variants'][d['variant']]['label']} `{d['variant']}`** — 성분 "
        + ", ".join(f"`{c}`" for c in spec["components"]) + ".",
        "",
        "N 별 폴드 평균 λ 를 다시 N 으로 평균해 소수 둘째 자리에서 반올림한 것이다"
        "(Step 3a 와 같은 규칙).",
        "",
        "| N | " + " | ".join(f"λ({c})" for c in comps) + " |",
        "|---|" + "---|" * len(comps),
    ]
    for n in ns:
        cells = [f"{np.mean([f['weights'].get(c, 0.0) for f in fits[str(n)]]):.3f}"
                 for c in comps]
        body.append(f"| {n} | " + " | ".join(cells) + " |")
    body.append("| **N 평균 (반올림)** | "
                + " | ".join(f"**{spec['weights'][c]:.2f}**" for c in comps) + " |")
    body += [
        "",
        "```json",
        json.dumps(spec, ensure_ascii=False, indent=2),
        "```",
        "",
        "`analysis/backtest/results/serving_prior.json` 에 그대로 적혀 있다. 키 배치는 "
        "Step 3a 와 같고 `source` 만 `step3c` 로 올렸다 — 서버(`analysis/build_serving.py`)"
        "가 읽는 형식이 바뀌면 안 된다.",
        "",
        f"`prior_sd` {spec['prior_sd']} 는 **선택이지 추정값이 아니다** — 온라인 쌍대 "
        "갱신(PLAN 6절)이 사용자마다 사전값에서 벗어날 여지를 남기려고 폴드 간 "
        "표준편차보다 넉넉하게 뒀다.",
        "",
        "**서버가 알아야 할 것.**",
        "",
        "| 성분 | 점수 계산 | 필요한 산출물 |",
        "|---|---|---|",
        "| `popularity` | `log1p(review_count)` 의 후보 내 z | 없음(가게 표에 있다) |",
        "| `quality` | 베이지안 평균 평점(C=10)의 z | 없음 |",
        "| `cf` | `Σ_{h∈L} cooc_cos(h, i)` | `cooc` |",
        "| `menu` | `max_{h∈L} MaxSim_menu(h, i)` | `sim_menu` |",
        "| `text` | `logsumexp_{h∈L}(τ=0.1) cos_text(h, i)` | `sim_text` |",
    ]
    if FOOD_COMPONENT in spec["components"]:
        body += [
            "| `food` | `max_{h∈L} cos_food(h, i)` | **`sim_food` (새로 필요하다)** |",
            "",
            f"- **`sim_food` 산출물이 새로 필요하다.** 가게당 CLIP 벡터 하나의 코사인 "
            f"({blob['pool']['size']:,}² float16 ≈ 9.8MB, 다른 `sim_*` 와 같은 크기·"
            "같은 dtype). 음식 사진이 없는 가게는 행·열이 NaN 이고, 서버는 그것을 "
            "다른 modality 와 같은 규칙(후보 평균으로 중립 채움 → z)으로 다룬다.",
            "- 사진 벡터 자체는 서버가 실을 필요가 없다 — 점수는 가게-가게 유사도만 "
            "쓴다. 설명 레이어에서 '이 사진과 닮았어요' 를 하려면 그때 사진 url 이 "
            "필요하지만, 이 Step 의 범위가 아니다.",
        ]
    else:
        body += ["", f"- `{FOOD_COMPONENT}` 는 채택되지 않았으므로 새 산출물이 필요 없다."]
    body += [
        "- 성분 z 는 **그 사용자의 후보 안에서** 낸다(이미 간 가게 제외). NaN 은 z 뒤 0.",
        "- **`review` 는 여전히 점수에서 빠진다**(Step 3a 판정). 추천 이유가 리뷰 쌍을 "
        "필요로 하므로 `sim_review` 산출물은 계속 만든다.",
    ]
    return "\n".join(body)


def headline(blob: dict) -> list[str]:
    ns = blob["history_sizes"]
    d = blob["decision"]
    spec = blob["spec"]
    m = blob["food"]
    c = blob["correlation"]
    per_user = np.asarray(c["per_user_mean"], dtype=float)
    food_i = c["names"].index(FOOD_COMPONENT)
    pop_i = c["names"].index("log1p_review_count")
    comps = [n for n in c["names"] if n != "log1p_review_count"]
    others = sorted(((abs(per_user[food_i, j]), comps[j], per_user[food_i, j])
                     for j in range(len(comps)) if comps[j] != FOOD_COMPONENT),
                    reverse=True)
    win = list(d["win"]) + list(d["win_r"])
    lose = list(d["lose"]) + list(d["lose_r"])

    gm = blob["results"][FOOD_VARIANT]
    dm = blob["results"][REF_VARIANT]
    out = []
    r = blob["reproduction"]
    if r.get("ok") is not None:
        out.append(
            f"**(d) 재현 {'확인' if r['ok'] else '실패'}** — Step 3a 의 (d)·막대 숫자 "
            f"{r['n_checked']}개를 다시 내는지 봤고 최대 편차 {r['max_delta']:.1e} "
            f"(허용치 {r['tol']:.0e}). 아래 비교는 전부 같은 잣대 위에 있다.")
    out += [
        (f"**`food` 를 " + ("넣는다" if d["adopted"] else "넣지 않는다")
         + f"** — 미리 정한 규칙은 '이긴 칸 ≥ 1 이고 진 칸 0'(과제 P 16칸 + 과제 R "
         f"4칸)이었다. (g) 가 (d) 를 CI 로 이긴 칸 {len(win)}"
         + (f"({', '.join(win)})" if win else "")
         + f", 진 칸 {len(lose)}"
         + (f"(**{', '.join(lose)}**)" if lose else "개 — 하나도 없다") + "."),
        ("**(g) vs (d) AUC** — "
         + ", ".join(f"N={n} {gm[str(n)]['task_p']['auc']['mean']:.4f} vs "
                     f"{dm[str(n)]['task_p']['auc']['mean']:.4f}" for n in ns)
         + "; 인기 매칭 AUC "
         + ", ".join(f"N={n} {gm[str(n)]['task_p']['auc_pm']['mean']:.4f} vs "
                     f"{dm[str(n)]['task_p']['auc_pm']['mean']:.4f}"
                     for n in ns) + "."),
        (f"**`food` 는 다른 성분과 거의 겹치지 않는다** — 사용자 내 스피어만: "
         + ", ".join(f"`{n}` {v:+.3f}" for _, n, v in others[:3])
         + f"; `log1p(리뷰 수)` 와 {per_user[food_i, pop_i]:+.3f}"
         " (`review` 는 +0.673 이었다)."),
        (f"**음식 사진이 없는 가게가 {100 * m['missing_ratio']:.1f}%** "
         f"({m['missing']:,}/{m['stores']:,}곳)다. 이 성분은 그만큼의 후보에게 "
         "'모른다'(z 뒤 0)를 말한다 — 커버리지가 이 성분의 상한이다."),
        ("**서빙 사양: 성분 " + ", ".join(f"`{x}`" for x in spec["components"])
         + "**, 가중치 " + ", ".join(f"{k}={v:g}" for k, v in spec["weights"].items())
         + f" (`prior_sd` {spec['prior_sd']}, `source` `{spec['source']}`)."),
    ]
    for key, rec in blob["removals"].items():
        v = removal_verdict(rec, ns)
        loses = list(v["lose"]) + list(v["lose_r"])
        out.append(
            f"**`{rec['component']}` 는 사진이 대신하지 못한다** — (g) 에서 빼면 "
            f"{len(loses)}칸에서 CI 로 진다 ({', '.join(loses)})."
            if loses else
            f"**`{rec['component']}` 를 (g) 에서 빼도 지는 칸이 없다** — 음식 사진과 "
            "역할이 겹칠 수 있다는 뜻이지만, 이 Step 은 성분을 빼는 자리가 아니다.")
    bar_cells = {k: cell_verdict(blob["results_vs_bar"], k, ns)
                 for k in blob["variants"]}
    n_cells = bar_cells[REF_VARIANT]["n_cells"]
    losers = {k: v["lose"] for k, v in bar_cells.items() if v["lose"]}
    out.append("**막대 대비** — 이긴 칸: "
               + ", ".join(f"{blob['variants'][k]['label']} {len(v['win'])}/{n_cells}"
                           for k, v in bar_cells.items())
               + (" (진 칸은 모두 0)." if not losers else
                  "; 진 칸: " + "; ".join(
                      f"{blob['variants'][k]['label']} {', '.join(v)}"
                      for k, v in losers.items()) + "."))
    return out


def learnings(blob: dict) -> str:
    ns = blob["history_sizes"]
    d = blob["decision"]
    m = blob["food"]
    c = blob["correlation"]
    per_user = np.asarray(c["per_user_mean"], dtype=float)
    food_i = c["names"].index(FOOD_COMPONENT)
    text_i = c["names"].index("text")
    pop_i = c["names"].index("log1p_review_count")
    food_res = blob["results"][COMPONENT_MODELS[FOOD_COMPONENT]]
    gm = blob["results"][FOOD_VARIANT]
    dm = blob["results"][REF_VARIANT]
    lam = {n: float(np.mean([f["weights"].get(FOOD_COMPONENT, 0.0)
                             for f in blob["fits"][FOOD_VARIANT][str(n)]])) for n in ns}
    n_mid = str(ns[1] if len(ns) > 1 else ns[0])
    win = list(d["win"]) + list(d["win_r"])
    lose = list(d["lose"]) + list(d["lose_r"])
    auc_span = [gm[str(n)]["task_p"]["auc"]["mean"]
                - dm[str(n)]["task_p"]["auc"]["mean"] for n in ns]
    pm_span = [gm[str(n)]["task_p"]["auc_pm"]["mean"]
               - dm[str(n)]["task_p"]["auc_pm"]["mean"] for n in ns]
    body = [
        "### 배운 것",
        "",
        "1. **`food` 는 신호가 있지만 방향이 갈렸다 — 그래서 안 넣는다.** "
        f"CI 로 오른 칸은 {len(win)}개({', '.join(win)}; 인기 매칭 AUC 는 "
        f"+{min(pm_span):.4f}~+{max(pm_span):.4f}), 내린 칸은 {len(lose)}개"
        f"({', '.join(lose)})다. 미리 정한 규칙이 '진 칸 0' 을 요구하므로 판정은 "
        "기각이다. **한 지표에서 이겼다고 성분을 켜지 않는다** — 규칙을 결과를 보고 "
        "고치면 그 규칙은 아무 것도 막지 못한다.",
        "2. **오르는 곳과 내리는 곳이 다르다.** 오르는 것은 인기를 통제한 랭킹"
        "(`auc_pm`)이고 내리는 것은 **맨 위 20장**(`ndcg@20`·`hit@10`)이다. 음식 "
        f"사진은 꼬리에서 인기가 아닌 이유로 가게를 세우지만(단독 auc_pm "
        f"{food_res[n_mid]['task_p']['auc_pm']['mean']:.4f} — Step 2 까지의 최고였다) "
        "상위 몇 장을 바꿀 만큼 정확하지는 않다. 스와이프 "
        "제품이 실제로 보여 주는 것은 상위 10~20장이므로 이 방향의 손해가 더 비싸다.",
        f"3. **학습된 λ(food) 는 " + ", ".join(f"N={n} {v:.2f}" for n, v in lam.items())
        + "** — `menu`·`text` 와 같은 자릿수다. **λ 가 크다는 것은 기여의 증거가 "
        "아니다** — Step 2 의 `review` 가 가장 큰 λ 를 받고도 ablation 에서 죽었고, "
        "여기서도 같은 일이 (덜한 정도로) 벌어졌다. 학습이 성분에 무게를 주는 것과 "
        "그 무게가 폴드 밖 지표를 올리는 것은 다른 사건이다.",
        "4. **`food` 가 죽은 이유는 `review` 와 다르다.** `review` 는 인기의 다른 "
        f"이름이었다(스피어만 +0.673). `food` 는 인기와 {per_user[food_i, pop_i]:+.3f} "
        f"로 거의 무관하고 가장 겹치는 상대가 `text` {per_user[food_i, text_i]:+.3f} 다 "
        "— 중복이라 죽은 것이 아니라 **상위를 세우는 정확도가 모자라** 죽었다.",
        f"5. **커버리지가 이 성분의 상한이다.** 음식 사진이 없는 "
        f"{100 * m['missing_ratio']:.1f}% 의 가게에게 이 성분은 아무 말도 하지 않는다"
        "(z 뒤 0). 다음에 다시 시도한다면 표현을 바꾸는 것보다 사진을 더 받아 "
        "커버리지를 올리는 쪽이 먼저다.",
        "",
        "### 증명하지 못한 것",
        "",
        "- **기각은 '사진에 신호가 없다' 가 아니다.** Step 3b 에서 단독으로 난수 "
        "대조군을 넘었고 여기서도 인기 매칭 AUC 를 CI 로 올렸다. 기각의 뜻은 "
        "**'지금 있는 다섯 성분 위에, 지는 칸 없이 얹히지는 않는다'** 하나뿐이다.",
        "- **다중비교 보정을 하지 않았다.** 이 실행에 변종 4개 × 지표 4개 × N 4개의 "
        "CI 와 제거 시험 2개가 있다. 개별 칸의 95% CI 를 그대로 읽으면 우연히 유의한 "
        f"칸이 몇 개는 나온다 — 이긴 칸 {len(win)}개도 진 칸 {len(lose)}개도 그 안에 "
        "있다. 채택 규칙을 **미리** 정해 둔 것이 유일한 방어이고, 충분한 방어는 아니다.",
        "- **판정이 칸 하나에 걸려 있다.** 진 칸이 "
        + (f"{', '.join(lose)} 뿐이다" if lose else "없다")
        + ". bootstrap 표본을 다시 뽑거나 N 을 하나 빼면 뒤집힐 수 있는 폭이고, "
        "그래서 이 결과는 '사진은 아직 아니다' 이지 '사진은 안 된다' 가 아니다.",
        "- **성분 채택은 테스트 사용자 227명의 지표를 보고 했다.** λ 는 바깥 5-fold 로 "
        "폴드 밖에서 학습했지만 '어떤 변종을 채택할까' 는 전체를 보고 정했다.",
        f"- **효과의 크기는 AUC {min(auc_span):+.4f}~{max(auc_span):+.4f} · 인기 매칭 "
        f"AUC {min(pm_span):+.4f}~{max(pm_span):+.4f} 다.** 채택했더라도 사용자가 "
        "느끼는 차이라는 근거는 아니었을 폭이다.",
        "- **음식 사진의 '취향' 이 무엇인지는 안 봤다.** CLIP 벡터가 무엇으로 가게를 "
        "묶는지(요리 종류인지, 접시 색인지, 사진 밝기인지)는 이 Step 이 답하지 않는다. "
        "Step 3b 의 업종 교차 29.5% 가 유일한 단서다.",
        "- **사진이 없는 가게에 대한 편향은 재지 않았다.** z 뒤 0 은 '중립' 이지만 "
        "다른 성분이 그 가게를 밀어올릴 때 사진 성분만 침묵하는 것이 순위에 어떤 "
        "체계적 효과를 내는지는 따로 재야 한다.",
        "- **`max` 말고 다른 집계는 안 재 봤다.** 블렌드 안에서는 `photo_food_max` "
        "하나만 얹었다. Step 3b 는 단독으로 `mean`·`lse01` 도 쟀지만 블렌드 위에서 "
        "다시 고르지는 않았다 — 여기서 집계를 스윕했으면 채택 규칙이 그만큼 더 "
        "느슨해졌을 것이다.",
    ]
    return "\n".join(body)


def notes(blob: dict) -> list[str]:
    c = blob["correlation"]
    return [
        f"과제 P 의 기준은 `{blob['references']['task_p']}`, 과제 R 은 "
        f"`{blob['references']['task_r']}` 다(PLAN 5절 1번). 막대 대비·(d) 대비 CI 는 "
        "'블렌드 변종' 절의 표에 따로 있다.",
        "블렌드 변종은 전부 **바깥 5-fold** 다 — 폴드 f 를 뺀 사용자로 λ 를 학습하고 "
        "폴드 f 만 평가한다. 사용자마다 자기가 학습에 참여하지 않은 모델의 점수가 "
        "남으므로 다른 모델과 그대로 paired bootstrap 에 넣을 수 있다.",
        f"성분 상관은 이력 N={c['n_history']} 한 곳에서만 쟀다(Step 3a 와 같은 N). "
        "N 이 커지면 이력 위의 집계가 더 매끄러워져 상관이 조금 올라갈 수 있다.",
        "`food` 는 **음식 사진만**이다. 공간 사진은 Step 3b 에서 난수 대조군을 못 "
        "넘겼으므로 성분 후보에 올리지 않았다 — '재 봤더니 졌다' 가 아니라 '잴 근거가 "
        "없다' 이다.",
        "BLAS 스레드 수에 따라 부동소수 합의 순서가 달라져 마지막 자리가 흔들릴 수 "
        "있다. 이 실행은 다른 작업과 코어를 나눠 쓰느라 1스레드로 돌렸다 — 재현 검사의 "
        f"허용치 {blob['config']['repro_tol']:.0e} 는 그 흔들림만 허용하는 폭이다.",
    ]


def _prose(blob: dict) -> dict:
    """숫자는 그대로 두고 문장·표만 다시 만든다(`--rebuild-md` 가 이것만 부른다)."""
    blob["headline"] = headline(blob)
    blob["sections_before"] = [
        {"heading": "재현 확인 — Step 3a 의 (d) 와 막대", "body": reproduction_section(blob)},
        {"heading": "성분 `food` — 무엇을 얹는가", "body": food_section(blob)},
        {"heading": "성분 상관 (Step 3a 표에 `food` 를 더한 것)",
         "body": correlation_section(blob)},
    ]
    blob["sections"] = [
        {"heading": "블렌드 변종 (d)·(g)·(h)·(i)", "body": variants_section(blob)},
        {"heading": "판정과 서빙 사양", "body": decision_section(blob)},
        {"heading": "배운 것 / 증명하지 못한 것", "body": learnings(blob)},
    ]
    blob["notes"] = notes(blob)
    return blob


def _write(blob: dict, out: str) -> None:
    blob = _prose(blob)
    out_dir = Path(config.RESULTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{out}.json").write_text(
        json.dumps(blob, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8")
    (out_dir / f"{out}.md").write_text(report.to_markdown(blob), encoding="utf-8")
    print(f"기록: {out_dir / (out + '.json')}\n      {out_dir / (out + '.md')}",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
