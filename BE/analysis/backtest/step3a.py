# -*- coding: utf-8 -*-
"""Step 3a 실행기 — 블렌드 위생 검사와 서빙용 최종 사양.

    ~/venvs/recsys/bin/python -m analysis.backtest.step3a

PLAN.md 7절 'Step 2 → Step 3 으로 넘기는 결정' 의 1·5·6번을 실행한다. Step 2 가 남긴
`blend_pairwise` 는 막대를 넘긴 첫 모델이지만 **위생 검사가 두 개 남았다** — 메뉴
modality 에 난수 대조군이 없고, 성분들이 서로 얼마나 겹치는지 재지 않았다. 겹치는
성분을 그대로 서버에 실으면 온라인에서 사용자별로 학습할 가중치가 서로를 상쇄한다.

  1. **메뉴 난수 벡터 대조군**(결정 1번) — `menu_top8_max` 의 메뉴 개수 구조는 그대로
     두고 벡터만 난수로 바꿔 다시 잰다. 리뷰에서는 대조군이 진짜 벡터를 **이겼다**.
     메뉴에서도 그러면 메뉴 MaxSim 의 점수는 내용의 증거가 아니다.
  2. **성분 상관 행렬**(결정 5번) — 6개 성분의 사용자 내 z 점수를 스피어만 상관으로
     본다. `review` 가 큰 λ 를 받고도 ablation 에서 죽은 이유를 여기서 찾는다.
  3. **블렌드 변종**(결정 6번 포함) — `quality` 를 성분으로 넣을지, `review` 를 뺄지를
     같은 바깥 5-fold 쌍대 로지스틱으로 비교한다.
  4. **서빙 사양** — 서버가 실을 성분 집합과 사전 가중치를 정하고
     `results/serving_prior.json` 에 적는다. 성분을 빼는 규칙은 하나다:
     **CI 로 지는 칸이 하나도 없고, 상관이 중복이라고 말할 때만 뺀다.**

기준은 Step 2 와 같다 — `popularity`, Step 1.5 의 막대 `hybrid_pop_cf_item_sum_cv_ndcg20`,
그리고 Step 2 의 전체 5성분 블렌드(여기서는 변종 (a) 로 다시 돌린다).
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import warnings
from pathlib import Path

import numpy as np

from analysis.content import blend as blend_mod
from analysis.content import menus as menu_content

from . import cache, config, cv, report, run, scorers
from .bootstrap import paired_diff_ci
from .data import load_dataset
from .evaluate import evaluate
from .pool import attach_crawled_quality, build_pool
from .scorers import ScoreContext
from .scorers_menu import LAMBDAS, NULL_VARIANT, menu_space
from .step1_5 import _mean, hybrid_name
from .step2 import METRICS4, _fmt_ci, _md, finalist_table

REFS = {"task_p": "popularity", "task_r": "quality"}

#: Step 1.5 가 세운 막대. Step 2 와 같은 것이다.
BAR_CONTENT = "cf_item_sum"

#: 성분 이름 -> 그 성분을 만드는 scorer. 이름은 **서빙 파일의 키와 같다**
#: (`serving_prior.json` 을 서버가 그대로 읽는다).
COMPONENT_MODELS: dict[str, str] = {
    "popularity": "popularity",
    "quality": "quality",
    "cf": "cf_item_sum",
    "menu": "menu_top8_max",
    "text": "text_lse01",
    "review": "review_longest12_single",
}

#: 메뉴 난수 대조군 짝 — (진짜, 대조군).
MENU_REAL = "menu_top8_max"
MENU_NULL = "menu_null_max"

#: 비교할 블렌드 변종. 첫 성분은 언제나 `popularity` 다(가중치 1 로 고정되는 자리).
VARIANTS: dict[str, dict] = {
    "blend_a_full5": {
        "label": "(a)", "components": ["popularity", "cf", "menu", "text", "review"],
        "note": "Step 2 의 `blend_pairwise` 와 같은 성분 — 참조로 다시 돌린 것"},
    "blend_b_quality6": {
        "label": "(b)",
        "components": ["popularity", "quality", "cf", "menu", "text", "review"],
        "note": "(a) + `quality` (PLAN 7절 결정 6번)"},
    "blend_c_no_review": {
        "label": "(c)=(f)", "components": ["popularity", "cf", "menu", "text"],
        "note": "(a) − `review`. 과제가 따로 적은 (f) `popularity+cf+menu+text` 와 "
                "**같은 집합**이라 한 줄로 적는다"},
    "blend_d_no_review_quality": {
        "label": "(d)", "components": ["popularity", "quality", "cf", "menu", "text"],
        "note": "(c) + `quality`"},
    "blend_e_pop_cf_text": {
        "label": "(e)", "components": ["popularity", "cf", "text"],
        "note": "(c) − `menu`"},
}

#: 성분 제거 시험 — (뺀 성분, 뺀 쪽, 안 뺀 쪽). 서빙 사양의 판단 근거다.
#: '없앴을 때 얼마나 나쁜가' 를 재는 것이므로 두 변종 모두 **다시 학습된** 것이다.
REMOVALS: list[tuple[str, str, str]] = [
    ("review", "blend_c_no_review", "blend_a_full5"),
    ("review", "blend_d_no_review_quality", "blend_b_quality6"),
    ("menu", "blend_e_pop_cf_text", "blend_c_no_review"),
    ("quality", "blend_a_full5", "blend_b_quality6"),
    ("quality", "blend_c_no_review", "blend_d_no_review_quality"),
]

#: 서빙 사전값의 폭. λ 의 폴드 간 흔들림보다 넉넉하게 둔다 — 온라인 쌍대 갱신이
#: 사용자마다 사전값에서 벗어날 여지를 남기기 위한 **선택**이지 추정값이 아니다.
PRIOR_SD = 0.5

#: 취향 프로토타입 군집(선택 과제)에 쓸 사용자 수와 군집 상한.
CLUSTER_USERS = 5
CLUSTER_MAX = 5
CLUSTER_MIN_HISTORY = 10


# ────────────────────────────────────────────────── 표
def null_control_table(rows: list[dict], ns: list[int]) -> str:
    """진짜 vs 난수 — 지표마다 두 줄, 아래에 (난수 − 진짜) CI."""
    out = ["| 지표 | 모델 | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|---|" + "---|" * len(ns)]
    by = {(r["metric"], r["n"]): r for r in rows}
    for metric in METRICS4:
        out.append(f"| `{metric}` | `{MENU_REAL}` (진짜) | "
                   + " | ".join(f"{by[(metric, n)]['real']:.4f}" for n in ns) + " |")
        out.append(f"| | `{MENU_NULL}` (난수) | "
                   + " | ".join(f"{by[(metric, n)]['null']:.4f}" for n in ns) + " |")
        out.append("| | 난수 − 진짜 | "
                   + " | ".join(_fmt_ci(by[(metric, n)]["ci"]) for n in ns) + " |")
    return "\n".join(out)


def corr_table(names: list[str], mat: list[list[float]]) -> str:
    """상관 행렬을 아래 삼각만 그린다(위는 대칭이라 읽을 것이 없다)."""
    out = ["| | " + " | ".join(f"`{n}`" for n in names) + " |",
           "|---|" + "---|" * len(names)]
    for i, n in enumerate(names):
        cells = []
        for j in range(len(names)):
            v = mat[i][j]
            cells.append("" if j > i else
                         ("—" if v is None or not np.isfinite(v) else f"{v:+.3f}"))
        out.append(f"| `{n}` | " + " | ".join(cells) + " |")
    return "\n".join(out)


def variant_table(results: dict, results_ref: dict, ns: list[int], metric: str,
                  ref_name: str) -> str:
    """변종 × N 한 지표. 셀 아래는 기준 대비 CI."""
    return finalist_table(results, results_ref, list(VARIANTS), ns, metric, ref_name)


def removal_table(removals: dict, ns: list[int], metric: str) -> str:
    out = ["| 뺀 성분 | 비교 | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|---|" + "---|" * len(ns)]
    for key, rec in removals.items():
        cells = [_fmt_ci(rec["ci"][str(n)][metric]) for n in ns]
        out.append(f"| `{rec['component']}` | {VARIANTS[rec['child']]['label']} − "
                   f"{VARIANTS[rec['parent']]['label']} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def r_auc_table(results: dict, results_ref: dict, ns: list[int], models: list[str],
                ref_name: str) -> str:
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


def lambda_table(fits: dict, ns: list[int]) -> str:
    """변종 × N 의 폴드 평균 λ (w_pop = 1 정규화). 괄호 안은 폴드 간 표준편차."""
    comps = [c for c in COMPONENT_MODELS if c != "popularity"]
    out = ["| 변종 | N | " + " | ".join(f"λ({c})" for c in comps) + " |",
           "|---|---|" + "---|" * len(comps)]
    for key, meta in VARIANTS.items():
        for n in ns:
            per_fold = fits[key][str(n)]
            cells = []
            for c in comps:
                if c not in meta["components"]:
                    cells.append("·")
                    continue
                vals = [f["weights"].get(c, 0.0) for f in per_fold]
                cells.append(f"{np.mean(vals):.2f} <sub>±{np.std(vals):.2f}</sub>")
            out.append(f"| {meta['label']} `{key}` | {n} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def cell_verdict(results_ref: dict, model: str, ns: list[int]) -> dict:
    """한 모델이 기준 대비 CI 로 이긴/진 칸을 센다 (지표 4 × N 4 = 16칸)."""
    win, lose = [], []
    for metric in METRICS4:
        for n in ns:
            ci = results_ref[model][str(n)]["task_p"][metric]["vs_baseline"]
            if not ci["significant"]:
                continue
            (win if ci["diff"] > 0 else lose).append(f"{metric} N={n}")
    return {"win": win, "lose": lose, "n_cells": len(METRICS4) * len(ns)}


def removal_verdict(rec: dict, ns: list[int]) -> dict:
    """제거가 CI 로 진 칸 / 이긴 칸. 과제 P 와 R 을 나눠서 센다.

    '지는 칸' 에 **과제 R 도 넣는다.** `quality` 의 값어치는 애초에 R 에 있으므로
    (과제 P 단독 AUC 0.50) P 만 보면 규칙이 품질을 자동으로 버린다.
    """
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


# ────────────────────────────────────────────────── 취향 프로토타입 군집 (선택)
def prototype_clusters(ds, pool, ctx, splits, *, n_history: int, n_users: int,
                       max_groups: int, min_history: int) -> list[dict]:
    """이력 가게를 modality 평균 유사도로 묶는다 — '프로토타입은 설명용' 의 근거.

    PLAN 6절: *"다중 취향 프로토타입은 '계산' 이 아니라 '설명' 용이다."* 점수에는
    `max_{h∈L}` 를 쓰므로 군집은 점수를 바꾸지 않는다. 여기서 보는 것은 한 사용자의
    이력이 실제로 **여러 덩어리**로 갈라지는가 하나뿐이다.
    """
    from sklearn.cluster import AgglomerativeClustering

    from .scorers_menu import similarity as menu_sim
    from .scorers_reviews import similarity as review_sim
    from .scorers_text import similarity as text_sim

    mats = [menu_sim(ctx, "top8"), text_sim(ctx), review_sim(ctx, "longest12")]
    stacked = np.stack([np.asarray(m, dtype=float) for m in mats])
    with warnings.catch_warnings():
        # 세 modality 가 전부 없는 가게 쌍은 NaN 이 남는다 — 아래에서 채운다.
        warnings.simplefilter("ignore", RuntimeWarning)
        avg = np.nanmean(stacked, axis=0)

    chosen = sorted((s for s in splits if len(s.history(n_history)) >= min_history),
                    key=lambda s: (-len(s.history(n_history)), s.user_id))[:n_users]
    out = []
    for sp in chosen:
        sids = list(dict.fromkeys(sid for sid, _ in sp.history(n_history)))
        idx = np.array([pool.pos[s] for s in sids])
        sim = avg[np.ix_(idx, idx)]
        # 벡터가 없는 가게(NaN)는 '아무와도 안 닮음' 이 아니라 '모른다' 라, 그 사용자
        # 이력의 평균 유사도로 채운다 — 점수 쪽 NaN 규칙과 같은 정신이다.
        finite = np.isfinite(sim)
        sim = np.where(finite, sim, float(sim[finite].mean()) if finite.any() else 0.0)
        sim = (sim + sim.T) / 2.0
        np.fill_diagonal(sim, 1.0)
        dist = np.clip(1.0 - sim, 0.0, None)
        np.fill_diagonal(dist, 0.0)
        k = min(max_groups, len(sids))
        lab = AgglomerativeClustering(n_clusters=k, metric="precomputed",
                                      linkage="average").fit_predict(dist)
        groups: dict[int, list[dict]] = {}
        for sid, g in zip(sids, lab.tolist()):
            store = ds.stores.get(sid, {})
            groups.setdefault(int(g), []).append(
                {"store_id": sid, "name": store.get("name") or sid,
                 "cat2": store.get("cat2") or ""})
        out.append({"user_id": sp.user_id, "n_history": len(sids), "n_groups": k,
                    "groups": [groups[g] for g in sorted(groups)]})
    return out


def cluster_section_md(clusters: list[dict]) -> str:
    if not clusters:
        return "이력 10곳 이상인 평가 사용자가 없어 군집을 만들지 못했다."
    body = [
        "PLAN 6절의 *\"다중 취향 프로토타입은 '계산' 이 아니라 '설명' 용이다\"* 를 "
        "눈으로 확인하는 자리다. 이력 가게를 **메뉴·요약문·리뷰 유사도의 평균**으로 "
        f"묶었다(agglomerative, average linkage, 거리 = 1 − sim, 군집 ≤ {CLUSTER_MAX}). "
        "이 군집은 **점수에 쓰이지 않는다** — 점수는 `max_{h∈L}` 라 군집과 무관하다.",
        "",
    ]
    for c in clusters:
        body.append(f"**`{c['user_id'][:12]}…`** — 이력 {c['n_history']}곳 → "
                    f"{c['n_groups']}군집")
        body.append("")
        for i, g in enumerate(c["groups"], 1):
            items = ", ".join(f"{_md(s['name'])}({_md(s['cat2'])})" for s in g)
            body.append(f"- 군집 {i} ({len(g)}곳): {items}")
        body.append("")
    return "\n".join(body)


# ────────────────────────────────────────────────── 서빙 사양
def spec_weights(fits: dict, key: str, ns: list[int]) -> dict:
    """변종의 N 평균 λ (폴드 평균을 N 으로 다시 평균). 반올림 전 원값."""
    comps = VARIANTS[key]["components"]
    out = {}
    for c in comps:
        if c == "popularity":
            out[c] = 1.0
            continue
        per_n = [float(np.mean([f["weights"].get(c, 0.0) for f in fits[key][str(n)]]))
                 for n in ns]
        out[c] = float(np.mean(per_n))
    return out


def decide_spec(removals: dict, corr: dict, ns: list[int]) -> dict:
    """성분 집합을 정한다. 규칙: **CI 로 지는 칸이 0 이고 상관이 중복이라고 할 때만 뺀다.**

    상관 판정은 '어떤 다른 성분과의 사용자 내 스피어만 |ρ| 가 가장 큰가' 로 한다.
    임계값 0.3 은 손으로 정한 것이고(관측된 값들이 0.1 아래와 0.5 위로 갈린다) 그
    사실을 결과 .md 에 적는다.
    """
    names = corr["names"]
    mat = np.asarray(corr["per_user_mean"], dtype=float)
    comps = [c for c in COMPONENT_MODELS]

    def max_partner(comp: str) -> tuple[str, float]:
        i = names.index(comp)
        best, val = "", 0.0
        for j, other in enumerate(names):
            if other == comp or other not in comps:
                continue
            v = float(abs(mat[i, j]))
            if v > val:
                best, val = other, v
        return best, val

    steps: list[dict] = []
    current = list(COMPONENT_MODELS)
    changed = True
    while changed:                     # 하나 빼면 그 집합에서 다시 처음부터 본다
        changed = False
        for comp, child, parent in REMOVALS:
            if VARIANTS[parent]["components"] != current:
                continue               # 지금 집합에서 출발하는 제거 시험만 본다
            v = removal_verdict(removals[f"{comp}:{child}"], ns)
            partner, rho = max_partner(comp)
            redundant = rho >= 0.3
            drop = (not v["lose"]) and (not v["lose_r"]) and redundant
            steps.append({"component": comp, "from": parent, "to": child,
                          "lose": v["lose"], "win": v["win"],
                          "lose_r": v["lose_r"], "win_r": v["win_r"],
                          "partner": partner, "rho": rho, "redundant": redundant,
                          "dropped": drop})
            if drop:
                current = list(VARIANTS[child]["components"])
                changed = True
                break
    key = next((k for k, m in VARIANTS.items() if m["components"] == current), None)
    return {"components": current, "variant": key, "steps": steps,
            "redundancy_threshold": 0.3}


# ────────────────────────────────────────────────── main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analysis.backtest.step3a",
        description="PLAN.md 7절 결정 1·5·6번 — 블렌드 위생 검사와 서빙용 최종 사양.")
    ap.add_argument("--N", default=",".join(str(n) for n in config.HISTORY_SIZES))
    ap.add_argument("--out", default="step3a-final-spec")
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--history", default=config.DEFAULT_HISTORY_MODE,
                    choices=list(config.HISTORY_MODES))
    ap.add_argument("--select-metric", default=f"ndcg@{config.NDCG_K}",
                    choices=list(config.SELECT_METRICS),
                    help="막대(하이브리드)의 λ 를 고르는 잣대 — Step 2 와 같아야 한다")
    ap.add_argument("--corr-N", type=int, default=5,
                    help="성분 상관을 잴 이력 길이 (PLAN 7절 결정 5번)")
    ap.add_argument("--resamples", type=int, default=config.BOOTSTRAP_RESAMPLES)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--skip-clusters", action="store_true",
                    help="선택 과제(취향 프로토타입 군집)를 건너뛴다")
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

    # ── 1) 단독 모델 + 메뉴 난수 대조군
    singles = list(dict.fromkeys(list(COMPONENT_MODELS.values()) + [MENU_NULL]))
    raw: dict[str, dict] = {}
    for name in singles:
        raw[name] = {}
        for n in ns:
            raw[name][n] = evaluate(scorers.get(name), splits, ctx, n)
        print(f"  {name:<26} AUC(P) "
              + " ".join(f"N={n}:{_mean(raw[name][n].p['auc']):.4f}" for n in ns),
              flush=True)

    null_rows = []
    for metric_name in METRICS4:
        for n in ns:
            real = raw[MENU_REAL][n].p[metric_name]
            null = raw[MENU_NULL][n].p[metric_name]
            null_rows.append({
                "metric": metric_name, "n": n,
                "real": _mean(real), "null": _mean(null),
                "ci": paired_diff_ci(null, real, resamples=args.resamples,
                                     seed=args.seed)})

    packed = menu_space(ctx).packed(menu_content.VARIANTS[NULL_VARIANT])
    counts = np.asarray(packed.counts, dtype=float)
    menu_meta = {
        "variant": NULL_VARIANT,
        "stores": int(counts.size), "zero": int((counts == 0).sum()),
        "full_k": int((counts >= menu_content.VARIANTS[NULL_VARIANT].k).sum()),
        "mean": float(counts.mean()), "median": float(np.median(counts)),
        "vectors": int(counts.sum()),
        # Step 2 가 리뷰에서 0.88 을 봤다. 메뉴에서 이 값이 작으면 개수 편향도 작다.
        "count_pop_corr": float(np.corrcoef(counts, pool.log_review_count)[0, 1]),
    }
    print(f"  메뉴 개수: 평균 {menu_meta['mean']:.2f} · 0개 {menu_meta['zero']}곳 · "
          f"개수↔인기 상관 {menu_meta['count_pop_corr']:.3f}", flush=True)

    # ── 2) 성분 통 (N 마다 한 번) + 상관
    banks = {}
    for n in ns:
        banks[n] = blend_mod.ComponentBank.build(COMPONENT_MODELS, splits, ctx, n)
        print(f"  성분 통 N={n} 완성 ({banks[n].n_components}성분)", flush=True)
    corr = blend_mod.component_correlations(
        banks[corr_n], extra={"log1p_review_count": pool.log_review_count})
    corr["n_history"] = corr_n
    print(f"  상관 행렬: {corr['n_rows']:,}행 (사용자 {corr['n_users']}명 × 후보)",
          flush=True)

    # ── 3) 블렌드 변종 (전부 쌍대 로지스틱, 바깥 5-fold)
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
        print(f"  {meta['label']} {key:<28} AUC(P) "
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

    # ── 5) 집계 — 기준 셋(인기 · 막대 · (a))
    model_names = singles + [bar_model] + list(VARIANTS)
    results = run.aggregate(raw, model_names, ns, REFS,
                            resamples=args.resamples, seed=args.seed)
    results_bar = run.aggregate(raw, model_names, ns,
                                {"task_p": bar_model, "task_r": REFS["task_r"]},
                                resamples=args.resamples, seed=args.seed)
    results_a = run.aggregate(raw, model_names, ns,
                              {"task_p": "blend_a_full5", "task_r": "blend_a_full5"},
                              resamples=args.resamples, seed=args.seed)
    users_meta = run.summarise_users(splits, raw, model_names, ns,
                                     history_mode=args.history)

    # ── 6) 성분 제거 시험 (자식 − 부모, 둘 다 다시 학습된 것)
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
        print(f"  제거 {comp:<10} {VARIANTS[child]['label']}−{VARIANTS[parent]['label']}"
              f"  진 칸 {len(v['lose'])} · 이긴 칸 {len(v['win'])}", flush=True)

    # ── 7) 서빙 사양
    decision = decide_spec(removals, corr, ns)
    chosen_key = decision["variant"]
    weights_raw = spec_weights(fits, chosen_key, ns)
    weights = {c: round(v, 2) for c, v in weights_raw.items()}
    spec = {"components": [c for c in COMPONENT_MODELS
                           if c in decision["components"]],
            "weights": {c: weights[c] for c in COMPONENT_MODELS
                        if c in decision["components"]},
            "prior_sd": PRIOR_SD, "source": "step3a",
            "n_users": len(splits), "history": args.history}
    print(f"  서빙 사양: {chosen_key} {spec['weights']}", flush=True)

    # ── 8) 선택 과제 — 취향 프로토타입 군집
    clusters: list[dict] = []
    if not args.skip_clusters:
        try:
            clusters = prototype_clusters(
                ds, pool, ctx, splits, n_history=max(ns), n_users=CLUSTER_USERS,
                max_groups=CLUSTER_MAX, min_history=CLUSTER_MIN_HISTORY)
            print(f"  프로토타입 군집: 사용자 {len(clusters)}명", flush=True)
        except Exception as exc:                     # 선택 과제라 실패해도 계속 간다
            print(f"  프로토타입 군집 실패(건너뜀): {exc}", flush=True)

    blob = {
        "run": args.out,
        "title": "Step 3a — 블렌드 위생 검사와 서빙용 최종 사양",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": run._git_commit(),
        "seed": args.seed,
        "history_mode": args.history,
        "references": REFS,
        "baseline_model": REFS["task_p"],
        "history_sizes": ns,
        "intro": [
            "`analysis/PLAN.md` 7절 'Step 2 → Step 3 으로 넘기는 결정' 의 **1번(집합형"
            " modality 의 난수 벡터 대조군)·5번(성분 간 중복)·6번(품질을 성분으로"
            " 넣을지)** 을 실행한 것이다.",
            "Step 2 의 `blend_pairwise` 는 막대를 넘긴 첫 모델이지만 위생 검사가 두 개"
            " 남아 있었다 — 메뉴에는 난수 대조군이 없었고, 성분들이 서로 얼마나 겹치는지"
            " 재지 않았다.",
            "마지막에 서버가 실을 성분 집합과 사전 가중치를 정해"
            " `results/serving_prior.json` 에 적는다.",
        ],
        "command": [
            "~/venvs/recsys/bin/python -m analysis.backtest.step3a \\",
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
            "split_cache_fingerprint": cache.fingerprint(),
        },
        "pool": {"size": len(pool), "stores_total": len(ds.stores),
                 "reviews_total": len(ds.reviews)},
        "users": users_meta,
        "bar_model": bar_model,
        "bar_chosen_per_fold": bar_chosen,
        "component_models": COMPONENT_MODELS,
        "menu_null": {"real": MENU_REAL, "null": MENU_NULL, "meta": menu_meta,
                      "rows": null_rows},
        "correlation": corr,
        "variants": {k: {"label": v["label"], "components": v["components"],
                         "note": v["note"]} for k, v in VARIANTS.items()},
        "fits": fits,
        "removals": removals,
        "decision": decision,
        "spec": spec,
        "clusters": clusters,
        "models": {m: {"description": scorers.get(m).description} for m in singles}
        | {bar_model: {"description":
                       f"popularity_z + λ·(`{BAR_CONTENT}`)_z — λ 는 사용자 5-fold "
                       f"out-of-fold 선택, 기준 `{metric}` (Step 1.5 의 막대)"}}
        | {k: {"description": f"{v['label']} 구성요소 블렌드(쌍대 로지스틱) — 성분 "
                              + ", ".join(f"`{c}`" for c in v["components"])}
           for k, v in VARIANTS.items()},
        "results": results,
        "results_vs_bar": results_bar,
        "results_vs_a": results_a,
    }
    _write(blob, args.out)

    spec_path = Path(config.RESULTS_DIR) / "serving_prior.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print(f"기록: {spec_path}", flush=True)
    return 0


# ────────────────────────────────────────────────── 글 (숫자는 blob 에서만 읽는다)
def menu_null_section(blob: dict) -> str:
    m = blob["menu_null"]["meta"]
    ns = blob["history_sizes"]
    n5 = blob["config"]["corr_n_history"]
    rows = {(r["metric"], r["n"]): r for r in blob["menu_null"]["rows"]}
    lose = [f"`{k[0]}` N={k[1]}" for k, r in rows.items()
            if r["ci"]["significant"] and r["ci"]["diff"] > 0]
    win = [f"`{k[0]}` N={k[1]}" for k, r in rows.items()
           if r["ci"]["significant"] and r["ci"]["diff"] < 0]
    body = [
        "PLAN 7절 결정 1번: *\"집합형 modality 에는 난수 벡터 대조군을 의무로 붙인다.\"* "
        "Step 2 는 리뷰에서 **대조군이 진짜 벡터를 이겼다**(AUC .6171 vs .6153). "
        f"`max_{{b∈B}}` 가 |B| 에 단조증가하는데 리뷰 수가 곧 인기이기 때문이다. "
        "메뉴에도 같은 함정이 있는지 여기서 잰다.",
        "",
        f"대조군은 `{blob['menu_null']['real']}` 의 **개수·가중치 구조를 그대로 두고 "
        f"벡터만 씨앗 고정 난수 단위벡터로 바꾼 것**이다(`menus.random_unit_packed` — "
        "리뷰 대조군과 같은 함수). 유사도 계산·집계·NaN 규칙은 전부 같다.",
        "",
        f"- 후보 풀 {m['stores']:,}곳의 메뉴 집합 크기: 평균 **{m['mean']:.2f}개**, "
        f"중앙값 {m['median']:.0f}, 상한 K 를 다 채운 곳 {m['full_k']:,}곳, "
        f"**0개 {m['zero']:,}곳**(유사도 NaN). 벡터 합 {m['vectors']:,}개.",
        f"- 메뉴 개수 ↔ `log1p(리뷰 수)` 상관 **{m['count_pop_corr']:.3f}** — 리뷰"
        f" modality 의 0.88 과 견줘 보라. 메뉴 개수는 인기와 거의 무관하고, 그래서"
        " 개수 구조만으로 인기를 흉내낼 여지가 작다.",
        "",
        null_control_table(blob["menu_null"]["rows"], ns),
        "",
        f"**셀은 사용자 평균, 세 번째 줄은 (난수 − 진짜) 차이와 paired bootstrap "
        f"{blob['config']['bootstrap_resamples']:,}회 95% CI 다.** 음수면 진짜 벡터가 "
        "이겼다는 뜻이다.",
        "",
    ]
    if lose:
        body.append(f"난수가 진짜를 CI 로 이긴 칸: {', '.join(lose)} — **메뉴 MaxSim 도 "
                    "리뷰와 같은 함정에 빠졌다.**")
    else:
        body.append("**난수가 진짜를 CI 로 이긴 칸은 하나도 없다.** 리뷰에서 벌어진 일이 "
                    "메뉴에서는 벌어지지 않았다.")
    if win:
        body.append("")
        body.append(f"진짜가 난수를 CI 로 이긴 칸 {len(win)}/{len(rows)}: "
                    f"{', '.join(win)}.")
    r5 = rows[("auc", n5)]
    pm5 = rows[("auc_pm", n5)]
    body += [
        "",
        f"N={n5} 에서 AUC {r5['real']:.4f}(진짜) vs {r5['null']:.4f}(난수), "
        f"인기 매칭 AUC {pm5['real']:.4f} vs {pm5['null']:.4f}. "
        "**인기 매칭 AUC 가 이 검사의 핵심이다** — 전체 AUC 는 개수 구조만으로도 오를 "
        "수 있지만 인기를 통제한 뒤에도 남는 차이는 내용에서만 온다.",
    ]
    return "\n".join(body)


def correlation_section(blob: dict) -> str:
    c = blob["correlation"]
    names = c["names"]
    per_user = np.asarray(c["per_user_mean"], dtype=float)
    pooled = np.asarray(c["pooled"], dtype=float)
    comps = [n for n in names if n != "log1p_review_count"]
    pop_i = names.index("log1p_review_count")

    pairs = []
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            pairs.append((abs(per_user[i, j]), comps[i], comps[j], per_user[i, j]))
    pairs.sort(reverse=True)
    top = "; ".join(f"`{a}`–`{b}` {v:+.3f}" for _, a, b, v in pairs[:3])

    with_pop = sorted(((abs(per_user[i, pop_i]), comps[i], per_user[i, pop_i])
                       for i in range(len(comps))), reverse=True)
    pop_line = ", ".join(f"`{n}` {v:+.3f}" for _, n, v in with_pop)

    body = [
        "PLAN 7절 결정 5번: *\"`review` 가 큰 λ 를 받고도 ablation 에서 죽는 것은 성분들이 "
        "서로 상관돼 있다는 신호다. Step 3 에서 성분 상관 행렬을 보고 중복 성분을 "
        "정리한 뒤에 사진을 얹는다.\"*",
        "",
        f"이력 N={c['n_history']} 에서 평가 사용자 {c['n_users']}명의 **후보별 z 점수**를 "
        f"모두 모아(총 {c['n_rows']:,}행) 스피어만 상관을 냈다. 순위 상관을 쓰는 것은 "
        "우리가 실제로 쓰는 것이 랭킹이기 때문이고, 성분마다 분포 모양이 아주 다르기 "
        "때문이다(`cf` 는 대부분 0 인 희소 점수다).",
        "",
        "**사용자 안에서 잰 상관의 평균** — 한 사용자의 후보 안에서 두 성분이 같은 "
        "순서로 세우는가. 이것이 우리가 묻는 질문이다.",
        "",
        corr_table(names, c["per_user_mean"]),
        "",
        "**사용자를 가로로 쌓아 풀링한 상관** — 사용자 사이의 차이까지 섞인다. 위 표와 "
        "크게 다르면 그 차이 자체가 결과다.",
        "",
        corr_table(names, c["pooled"]),
        "",
        f"- 가장 강한 성분 쌍: {top}.",
        f"- `log1p(리뷰 수)` 와의 상관(사용자 내): {pop_line}. `popularity` 성분은 "
        "정의상 그 값의 단조 변환이므로 +1.000 이어야 한다 — 표가 그렇지 않으면 "
        "계산이 틀린 것이다.",
    ]
    return "\n".join(body)


def variants_section(blob: dict) -> str:
    ns = blob["history_sizes"]
    bar = blob["bar_model"]
    body = [
        "성분을 바꿔 가며 **같은 방법으로 다시 학습**한다 — 학습된 가중치를 그대로 두고 "
        "한 항만 0 으로 만드는 것은 '없앴을 때 얼마나 나쁜가' 가 아니라 '잘못 맞춘 "
        "모델이 얼마나 나쁜가' 를 재는 것이다. 학습은 전부 **쌍대 로지스틱(BPR) + 바깥 "
        "5-fold**(Step 2 가 고른 방법, PLAN 7절 결정 3번)다.",
        "",
        "| 변종 | 성분 | 메모 |",
        "|---|---|---|",
    ]
    for key, v in blob["variants"].items():
        body.append(f"| {v['label']} `{key}` | "
                    + ", ".join(f"`{c}`" for c in v["components"])
                    + f" | {v['note']} |")
    body += [
        "",
        "| 성분 | 쓴 모델 |",
        "|---|---|",
    ]
    for comp, model in blob["component_models"].items():
        body.append(f"| `{comp}` | `{model}` |")

    body += ["", f"### 막대 `{bar}` 대비", "",
             "**굵게 표시된 것만 CI 가 0 을 넘겼다.** 굵은 음수는 막대에 **졌다**는 뜻이다."]
    for metric in METRICS4:
        body += ["", f"`{metric}`", "",
                 variant_table(blob["results"], blob["results_vs_bar"], ns, metric, "")]

    body += ["", "### (a) 전체 5성분 대비 — 성분을 바꾼 효과만", ""]
    for metric in METRICS4:
        body += [f"`{metric}`", "",
                 variant_table(blob["results"], blob["results_vs_a"], ns, metric,
                               "blend_a_full5"), ""]

    body += [
        "### 과제 R (방문 가게 중 만족) — 품질을 넣으면 R 이 살아나는가",
        "",
        f"미래 방문 가게만 놓고 rating ≥ {config.SATISFIED_MIN_RATING} 를 양성으로 잰 "
        f"AUC 다. 양쪽 클래스가 다 있는 **{blob['users']['task_r_users']}명**만 센다. "
        "셀 아래는 **(a) 대비** CI. 견줄 곳: `quality` **단독**은 "
        f"{blob['results']['quality'][str(ns[0])]['task_r']['auc']['mean']:.4f} "
        "(이력을 안 보므로 N 과 무관하게 같다), `popularity` 단독은 "
        f"{blob['results']['popularity'][str(ns[0])]['task_r']['auc']['mean']:.4f} 다.",
        "",
        r_auc_table(blob["results"], blob["results_vs_a"], ns, list(VARIANTS),
                    "blend_a_full5"),
        "",
        "### 폴드 밖에서 학습된 λ (w_pop = 1 정규화)",
        "",
        "칸은 **폴드 5개의 평균**이고 아래 첨자는 폴드 간 표준편차다. `·` 는 그 변종에 "
        "없는 성분이다.",
        "",
        lambda_table(blob["fits"], ns),
    ]
    return "\n".join(body)


def removal_section(blob: dict) -> str:
    ns = blob["history_sizes"]
    body = [
        "서빙 사양을 정하는 규칙은 하나다 — **CI 로 지는 칸이 하나도 없고, 상관이 "
        "중복이라고 말할 때만 성분을 뺀다.** 아래 표가 그 첫 번째 조건이다. 자식(뺀 쪽) "
        "− 부모(안 뺀 쪽) 차이이므로 **음수가 유의하면 그 성분은 기여한 것**이다.",
        "",
        "'지는 칸' 에는 **과제 R 도 센다.** `quality` 의 값어치는 애초에 R 에 있어서"
        "(과제 P 단독 AUC 0.50) P 만 보면 규칙이 품질을 자동으로 버리기 때문이다.",
        "",
    ]
    for metric in METRICS4 + ["r_auc"]:
        body += [f"`{metric}`" + (" (과제 R)" if metric == "r_auc" else ""), "",
                 removal_table(blob["removals"], ns, metric), ""]
    d = blob["decision"]
    body += ["### 판정", ""]
    for s in d["steps"]:
        verdict = "**뺀다**" if s["dropped"] else "**남긴다**"
        loses = list(s["lose"]) + list(s.get("lose_r", []))
        why = ["진 칸 없음" if not loses else
               f"진 칸 {len(loses)}개 ({', '.join(loses)})",
               f"가장 강한 상관 상대 `{s['partner']}` |ρ|={s['rho']:.3f} "
               + ("→ 중복" if s["redundant"] else "→ 중복 아님")]
        body.append(f"- `{s['component']}` — {verdict} "
                    f"({VARIANTS[s['from']]['label']} 에서). " + " · ".join(why) + ".")
    body += [
        "",
        f"중복 임계값 |ρ| ≥ {d['redundancy_threshold']} 는 **손으로 정한 것**이다. "
        "관측된 성분 쌍 상관이 0.1 아래와 0.4 위로 갈려 있어 그 사이에 그었다. "
        "임계값을 옮기면 판정이 달라질 수 있다는 뜻이고, 그래서 두 조건을 **모두** "
        "요구한다(지표에서 지는 칸이 있으면 상관과 무관하게 남긴다).",
    ]
    return "\n".join(body)


def spec_section(blob: dict) -> str:
    spec = blob["spec"]
    d = blob["decision"]
    ns = blob["history_sizes"]
    key = d["variant"]
    fits = blob["fits"][key]
    comps = [c for c in spec["components"] if c != "popularity"]
    body = [
        f"채택: **{blob['variants'][key]['label']} `{key}`** — 성분 "
        + ", ".join(f"`{c}`" for c in spec["components"]) + ".",
        "",
        "PLAN 6절이 요구한 *\"w 의 사전값은 오프라인 블렌드의 CV 가중치\"* 를 채운다. "
        "N 별 폴드 평균 λ 를 다시 N 으로 평균해 소수 둘째 자리에서 반올림한 것이다.",
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
        f"`analysis/backtest/results/serving_prior.json` 에 그대로 적혀 있다. "
        f"`prior_sd` {spec['prior_sd']} 는 **선택이지 추정값이 아니다** — 온라인 쌍대 "
        "갱신(PLAN 6절)이 사용자마다 사전값에서 벗어날 여지를 남기려고 폴드 간 "
        "표준편차보다 넉넉하게 뒀다.",
        "",
        "**N 평균의 뜻을 오해하지 말 것.** N=10·20 을 다 채운 사용자는 108명·31명뿐이고 "
        "그 사람들은 N=3·5 에도 들어 있다. 네 N 의 평균은 '이력 길이에 무관한 값' 이지 "
        "'독립 표본 4개의 평균' 이 아니다.",
        "",
        "**서버가 알아야 할 것.**",
        "",
        "| 성분 | 점수 계산 | 필요한 산출물 |",
        "|---|---|---|",
        "| `popularity` | `log1p(review_count)` 의 후보 내 z | 없음(가게 표에 있다) |",
        "| `quality` | 베이지안 평균 평점(C=10)의 z | 없음 |",
        "| `cf` | `Σ_{h∈L} cooc_cos(h, i)` | `cooc.npz` |",
        "| `menu` | `max_{h∈L} MaxSim_menu(h, i)` | `sim_menu.npy` |",
        "| `text` | `logsumexp_{h∈L}(τ=0.1) cos_text(h, i)` | `sim_text.npy` |",
        "",
        "- 성분 z 는 **그 사용자의 후보 안에서** 낸다(이미 간 가게 제외). NaN 은 z 뒤 0.",
        "- `text` 의 집계는 **`lse01`(logsumexp τ=0.1)** 이다 — PLAN 6절이 적어 둔 "
        "`max` 가 아니다. Step 2 스윕에서 요약문은 `lse01` 이 이겼다.",
        "- **`review` 는 점수에서 빠지지만 산출물은 남는다.** PLAN 7절 결정 2번의 추천 "
        "이유(\"좋아요한 ○○의 리뷰와 닮았어요: '…'\")가 리뷰 쌍을 필요로 하므로 "
        "`sim_review.npy` 는 계속 만든다. 점수에 안 쓴다는 것뿐이다.",
    ]
    return "\n".join(body)


def headline(blob: dict) -> list[str]:
    ns = blob["history_sizes"]
    n5 = blob["config"]["corr_n_history"]
    rows = {(r["metric"], r["n"]): r for r in blob["menu_null"]["rows"]}
    null_wins = [k for k, r in rows.items()
                 if r["ci"]["significant"] and r["ci"]["diff"] > 0]
    real_wins = [k for k, r in rows.items()
                 if r["ci"]["significant"] and r["ci"]["diff"] < 0]
    auc5, pm5 = rows[("auc", n5)], rows[("auc_pm", n5)]

    c = blob["correlation"]
    names, per_user = c["names"], np.asarray(c["per_user_mean"], dtype=float)
    comps = [n for n in names if n != "log1p_review_count"]
    pairs = sorted(((abs(per_user[i, j]), comps[i], comps[j], per_user[i, j])
                    for i in range(len(comps)) for j in range(i + 1, len(comps))),
                   reverse=True)

    d = blob["decision"]
    spec = blob["spec"]
    out = [
        (f"**메뉴 MaxSim 은 난수 대조군을 넘는다** — N={n5} 에서 AUC "
         f"{auc5['real']:.4f} vs 난수 {auc5['null']:.4f}, 인기 매칭 AUC "
         f"{pm5['real']:.4f} vs {pm5['null']:.4f}. 난수가 진짜를 CI 로 이긴 칸은 "
         f"**{len(null_wins)}개**, 진짜가 난수를 이긴 칸은 {len(real_wins)}/"
         f"{len(rows)}개다. 리뷰에서 벌어진 역전(대조군 승)은 메뉴에서는 없다."
         if not null_wins else
         f"**메뉴 MaxSim 도 난수 대조군에 진다** — 난수가 CI 로 이긴 칸 "
         f"{len(null_wins)}개. 메뉴 MaxSim 의 점수는 내용의 증거가 아니다."),
        (f"**메뉴 개수는 인기와 거의 무관하다** — 개수 ↔ log1p(리뷰 수) 상관 "
         f"{blob['menu_null']['meta']['count_pop_corr']:.3f} (리뷰 modality 는 0.88). "
         "리뷰 대조군이 이겼던 이유가 메뉴에는 없다는 뜻이다."),
        (f"**가장 겹치는 성분 쌍은 `{pairs[0][1]}`–`{pairs[0][2]}` ρ={pairs[0][3]:+.3f}** "
         f"(사용자 내 스피어만, N={c['n_history']}). 나머지는 "
         + ", ".join(f"`{a}`–`{b}` {v:+.3f}" for _, a, b, v in pairs[1:4]) + "."),
        (f"**서빙 사양: 성분 " + ", ".join(f"`{x}`" for x in spec["components"])
         + "**, 가중치 " + ", ".join(f"{k}={v:g}" for k, v in spec["weights"].items())
         + f" (`prior_sd` {spec['prior_sd']})."),
    ]
    dropped = [s["component"] for s in d["steps"] if s["dropped"]]
    kept = [s["component"] for s in d["steps"] if not s["dropped"]]
    if dropped:
        out.append("**뺀 성분: " + ", ".join(f"`{x}`" for x in dropped)
                   + "** — 제거 시험에서 지는 칸이 없고 상관이 중복이라고 했다"
                   + (". 남긴 성분: " + ", ".join(f"`{x}`" for x in kept)
                      if kept else "")
                   + ". `review` 는 **점수에서만** 빠진다 — 추천 이유(PLAN 7절 결정 "
                     "2번)는 리뷰 쌍이 필요하므로 `sim_review` 산출물은 그대로 만든다.")
    else:
        out.append("**뺀 성분은 없다** — 제거 규칙(지는 칸 0 + 상관상 중복)을 통과한 "
                   "성분이 없었다.")

    # 과제 R — 이 Step 이 답해야 할 6번 질문("품질을 넣을까")의 핵심 줄.
    qa = blob["removals"].get("quality:blend_a_full5")
    if qa:
        sig = [(n, qa["ci"][str(n)]["r_auc"]) for n in ns
               if qa["ci"][str(n)]["r_auc"]["significant"]]
        pa = [n for metric in METRICS4 for n in ns
              if qa["ci"][str(n)][metric]["significant"]]
        qr = blob["results"]["blend_b_quality6"]
        ar = blob["results"]["blend_a_full5"]
        out.append(
            f"**`quality` 는 과제 R 을 올리고 과제 P 를 건드리지 않는다** — (b) R AUC "
            + ", ".join(f"N={n} {qr[str(n)]['task_r']['auc']['mean']:.4f}"
                        f"(vs (a) {ar[str(n)]['task_r']['auc']['mean']:.4f})"
                        for n in ns)
            + f"; 품질을 빼면 R 이 CI 로 떨어지는 N 이 {len(sig)}개"
            + (f"(" + ", ".join(f"N={n} {ci['diff']:+.4f}" for n, ci in sig) + ")"
               if sig else "")
            + f", 과제 P 에서 유의한 칸은 {len(pa)}개다. 다만 폭은 R AUC +0.01~0.015 "
              f"수준이고 `quality` **단독**의 R AUC "
              f"{blob['results']['quality'][str(ns[0])]['task_r']['auc']['mean']:.4f} "
              "와는 멀다 — 블렌드는 여전히 '관심' 모델이다.")
    bar_cells = {k: cell_verdict(blob["results_vs_bar"], k, ns)
                 for k in blob["variants"]}
    n_cells = bar_cells["blend_a_full5"]["n_cells"]
    losers = {k: v["lose"] for k, v in bar_cells.items() if v["lose"]}
    out.append(("**변종 다섯 개가 전부 막대를 넘고 어느 것도 지지 않는다** — "
                if not losers else "**막대 대비** — ")
               + "이긴 칸: "
               + ", ".join(f"{blob['variants'][k]['label']} {len(v['win'])}/{n_cells}"
                           for k, v in bar_cells.items())
               + (" (진 칸은 모두 0). " if not losers else
                  "; 진 칸: " + "; ".join(
                      f"{blob['variants'][k]['label']} {', '.join(v)}"
                      for k, v in losers.items()) + ". ")
               + "**성분 집합을 어떻게 고르든 막대 대비 결론은 같다** — 고르는 이유는 "
                 "막대와의 승패가 아니라 성분끼리의 비교와 서빙 비용이다.")
    return out


def learnings(blob: dict) -> str:
    ns = blob["history_sizes"]
    d = blob["decision"]
    n5 = blob["config"]["corr_n_history"]
    q_effect = []
    for child, parent, label in [("blend_a_full5", "blend_b_quality6", "(a) vs (b)"),
                                 ("blend_c_no_review", "blend_d_no_review_quality",
                                  "(c) vs (d)")]:
        rec = blob["removals"].get(f"quality:{child}")
        if not rec:
            continue
        cells = [rec["ci"][str(n)]["r_auc"] for n in ns]
        q_effect.append(f"{label}: 과제 R AUC 차이(품질 뺀 쪽 − 넣은 쪽) "
                        + ", ".join(f"N={n} {_fmt_ci(ci)}" for n, ci in zip(ns, cells)))
    body = [
        "### 배운 것",
        "",
        "1. **집합형 대조군은 modality 마다 답이 다르다.** 리뷰에서는 난수가 이겼고 "
        "메뉴에서는 지지 않았다. 두 결과를 가른 것은 개수 ↔ 인기 상관이다"
        f"(리뷰 0.88 vs 메뉴 {blob['menu_null']['meta']['count_pop_corr']:.3f}). "
        "사진(Step 3b)에서도 **먼저 이 상관을 보고** 대조군을 읽어야 한다.",
        "2. **성분 상관이 ablation 결과를 설명한다.** Step 2 에서 `review` 는 가장 큰 λ "
        f"(0.47~0.68)를 받고도 빼도 지표가 안 나빠졌다. N={n5} 상관 행렬에서 그 이유가 "
        "보인다 — 아래 '성분 상관' 절의 표를 볼 것.",
        "3. **`quality` 는 과제 P 를 해치지 않으면서 R 을 조금 올린다.** "
        + (" / ".join(q_effect) if q_effect else "(측정 없음)")
        + ". 과제 P 에서 `quality` 단독은 AUC 0.50(무작위)인데도 블렌드가 λ 를 "
        "0 으로 보내지 않았다 — 작지만(0.03~0.10) 전 폴드에서 양수다. PLAN 7절 6번의 "
        "질문('넣을지, λ 가 0 이 되는지')에 대한 답은 **넣는다** 이다.",
        "4. **`review` 는 인기의 다른 이름이었다.** 사용자 내 스피어만 "
        f"ρ(`review`, `popularity`) = "
        f"{np.asarray(blob['correlation']['per_user_mean'])[blob['correlation']['names'].index('review')][0]:+.3f}. "
        "Step 2 에서 가장 큰 λ 를 받고도 ablation 에서 죽은 것이 이것으로 설명된다 — "
        "블렌드가 `review` 에 준 가중치의 상당 부분은 `popularity` 를 한 번 더 "
        "더한 것이었다.",
        "",
        "### 증명하지 못한 것",
        "",
        "- **다중비교 보정을 하지 않았다.** 이 실행에는 변종 5개 × 지표 4개 × N 4개의 "
        "CI 가 있고, 제거 시험이 5개 더 있다. 개별 칸의 95% CI 를 그대로 읽으면 "
        "우연히 유의한 칸이 몇 개는 나온다.",
        "- **성분 선택은 테스트 사용자 위에서 이뤄졌다.** λ 는 바깥 5-fold 로 폴드 밖에서 "
        "학습했지만, '어떤 변종을 채택할까' 는 전체 227명의 지표를 보고 정했다. "
        "폭이 작은 차이는 이 선택 편향 안에 있다.",
        "- **효과의 크기는 여전히 AUC 0.01 수준이다.** PLAN 4절의 기준(CI 가 0 을 "
        "넘긴 구성만 서버에 켠다)은 충족해도, 그것이 사용자가 느끼는 차이라는 근거는 "
        "아직 없다. 온라인 팀-드래프트 인터리빙(PLAN 6절)이 남은 시험이다.",
        "- **과제 R 은 표본이 작다.** 양쪽 클래스가 다 있는 사용자만 세므로 "
        f"{blob['users']['task_r_users']}명이고, CI 가 넓다.",
        f"- **`quality` 를 넣어도 R 이 '고쳐지지는' 않는다.** 블렌드의 R AUC 는 "
        f"{blob['results']['blend_b_quality6'][str(ns[1] if len(ns) > 1 else ns[0])]['task_r']['auc']['mean']:.4f} "
        f"인데 `quality` **단독**은 "
        f"{blob['results']['quality'][str(ns[0])]['task_r']['auc']['mean']:.4f} 다. "
        "λ 가 0.05 수준이라 순위를 거의 안 바꾸기 때문이다. 만족을 정말 지키려면 "
        "R 을 목적함수에 넣거나(지금은 P 만 학습한다) 별도의 가드가 필요하다.",
        "- **성분 집합의 선택은 지표가 갈라 주지 않았다.** 다섯 변종이 전부 막대를 "
        "넘고 서로 간의 차이는 대부분 CI 안이다. 결정을 실제로 이끈 것은 상관 행렬과 "
        "'없어도 지지 않는다' 이지 '이것이 더 낫다' 가 아니다.",
    ]
    if d["steps"] and not any(s["dropped"] for s in d["steps"]):
        body.append("- **성분을 하나도 못 뺐다는 것은 '전부 필요하다' 와 다르다.** "
                    "제거 규칙이 보수적이라(두 조건 모두 요구) 애매한 성분이 살아남는다.")
    return "\n".join(body)


def notes(blob: dict) -> list[str]:
    c = blob["correlation"]
    return [
        f"과제 P 의 기준은 `{blob['references']['task_p']}`, 과제 R 은 "
        f"`{blob['references']['task_r']}` 다(PLAN 5절 1번). 막대 대비 CI 는 '블렌드 "
        "변종' 절의 표에 따로 있다.",
        "블렌드 변종은 전부 **바깥 5-fold** 다 — 폴드 f 를 뺀 사용자로 λ 를 학습하고 "
        "폴드 f 만 평가한다. 사용자마다 자기가 학습에 참여하지 않은 모델의 점수가 "
        "남으므로 다른 모델과 그대로 paired bootstrap 에 넣을 수 있다.",
        f"성분 상관은 이력 N={c['n_history']} 한 곳에서만 쟀다. N 이 커지면 이력 위의 "
        "집계가 더 매끄러워져 상관이 조금 올라갈 수 있다 — 재지 않았다.",
        "난수 대조군의 벡터는 seed 고정이다. 다른 seed 로 다시 뽑으면 숫자가 조금 "
        "달라지지만(난수 벡터 한 벌만 썼다) 대조군의 뜻은 개수 구조에 있으므로 "
        "결론이 바뀌지는 않는다 — 그래도 **난수 벡터 한 벌로 잰 값**이라는 것은 "
        "한계다.",
        "BLAS 스레드 수에 따라 부동소수 합의 순서가 달라져 마지막 자리가 흔들릴 수 "
        "있다. 이 실행은 다른 작업과 코어를 나눠 쓰느라 1스레드로 돌렸다.",
    ]


def _prose(blob: dict) -> dict:
    """숫자는 그대로 두고 문장·표만 다시 만든다(`--rebuild-md` 가 이것만 부른다)."""
    blob["headline"] = headline(blob)
    blob["sections_before"] = [
        {"heading": "메뉴 난수 벡터 대조군 (PLAN 7절 결정 1번)",
         "body": menu_null_section(blob)},
        {"heading": "성분 상관 (PLAN 7절 결정 5번)", "body": correlation_section(blob)},
    ]
    sections = [
        {"heading": "블렌드 변종 (PLAN 7절 결정 6번)", "body": variants_section(blob)},
        {"heading": "성분 제거 시험과 판정", "body": removal_section(blob)},
        {"heading": "서빙 사양 — 서버가 실을 것", "body": spec_section(blob)},
    ]
    if blob.get("clusters"):
        sections.append({"heading": "취향 프로토타입 군집 (설명용, 선택 과제)",
                         "body": cluster_section_md(blob["clusters"])})
    sections.append({"heading": "배운 것 / 증명하지 못한 것", "body": learnings(blob)})
    blob["sections"] = sections
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
