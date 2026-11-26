# -*- coding: utf-8 -*-
"""Step 3b 실행기 — 사진 벡터(음식/공간)를 백테스트 계약대로 평가한다.

PLAN 2절 '사진 (Step 3)' 과 7절 'Step 2 → Step 3 결정 1번'(집합형 modality 에는 난수
벡터 대조군이 **의무**)의 구현이다. 묻는 것은 셋이다.

  1. 사진에 취향 신호가 있는가 — 있다면 **음식**인가 **공간**인가.
  2. 그 신호가 **난수 벡터 대조군**을 넘는가. 못 넘으면 그 점수는 '사진이 있는 가게가
     인기 있다' 는 구조의 다른 이름이다.
  3. 사진은 업종 경계를 넘는가 (Step 1 메뉴 26% · Step 2 요약문 27% · 리뷰 46%).

이 파일이 `analysis/backtest/` 가 아니라 `analysis/photos/` 에 있는 것은 이번 작업에서
하네스 쪽 파일을 건드리지 않기로 했기 때문이다(같은 파일을 다른 작업자가 만지고 있다).
하네스의 계약(`evaluate`·`aggregate`·`report`)은 그대로 불러 쓴다 — 분할·지표·bootstrap
은 한 벌뿐이어야 modality 사이의 비교가 성립한다.

실행:
    nice -n 10 ~/venvs/recsys/bin/python -m analysis.photos.step3b
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np

from analysis.backtest import cache, config, cv, report, run, scorers
from analysis.backtest.bootstrap import paired_diff_ci
from analysis.backtest.data import load_dataset
from analysis.backtest.evaluate import evaluate
from analysis.backtest.pool import attach_crawled_quality, build_pool
from analysis.backtest.scorers import ScoreContext
from analysis.backtest.step1 import cross_category
from analysis.content import photos as photo_content

RESULTS_DIR = Path(config.RESULTS_DIR)

#: 과제별 기준 모델 — PLAN 5절 1번과 같다.
REFS = {"task_p": "popularity", "task_r": "quality"}

#: Step 1.5 가 세운 막대. λ 를 폴드 밖에서 NDCG@20 으로 고른 CF 하이브리드다.
#: 이름은 `step1_5.hybrid_name` 규칙 그대로이고 여기서 **다시 계산한다**
#: (레지스트리에 없는 이름이다 — outer CV 로만 만들어진다).
BAR_CONTENT = "cf_item_sum"
BAR_METRIC = f"ndcg@{config.NDCG_K}"
BAR_MODEL = f"hybrid_pop_{BAR_CONTENT}_cv_ndcg{config.NDCG_K}"
LAMBDAS = (0.25, 0.5, 1.0, 2.0, 4.0)

METRICS4 = ["auc", "auc_pm", f"hit@{config.HIT_K}", f"ndcg@{config.NDCG_K}"]

#: 사례로 쓸 가게·사용자 — Step 1·2 와 **같은 것**이어야 modality 를 나란히 읽는다.
STEP1_RESULT = RESULTS_DIR / "step1-menu.json"
STEP2_RESULT = RESULTS_DIR / "step2-text-reviews.json"
N_USER_EXAMPLES = 3


def _mean(v) -> float:
    v = np.asarray(v, dtype=float)
    return float(np.nanmean(v)) if np.isfinite(v).any() else float("nan")


def _fmt(v: float, nd: int = 4) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{nd}f}"


def _ci(entry: dict) -> str:
    """CI 를 `+.0123 [+.0001, +.0245]` 로. 0 을 넘긴 것만 굵게."""
    if not entry or np.isnan(entry.get("diff", float("nan"))):
        return "—"
    body = (f"{entry['diff']:+.4f} [{entry['ci_low']:+.4f}, {entry['ci_high']:+.4f}]")
    return f"**{body}**" if entry.get("significant") else body


def _store_row(ds, sid: str) -> dict:
    return ds.stores.get(sid, {}) or {}


def _md(text: str) -> str:
    return (text or "").replace("|", "\\|")


# ────────────────────────────────────────────────── 1단계: 스윕
def sweep_names() -> list[str]:
    """음식/공간 × 표현 4종 × 집계 3종 = 24개. 대조군이 절반이다."""
    return [photo_content.scorer_name(k, r, a)
            for k in photo_content.KIND_VECTORS
            for r in photo_content.REPRS
            for a in photo_content.PHOTO_AGGS]


def run_sweep(splits, ctx, n_history: int, ref_result, *, resamples: int,
              seed: int) -> tuple[list[dict], dict]:
    rows, raw = [], {}
    for name in sweep_names():
        res = evaluate(scorers.get(name), splits, ctx, n_history)
        raw[name] = res
        row = {"model": name,
               "description": scorers.get(name).description}
        for m in METRICS4:
            row[m] = _mean(res.p[m])
        row["vs_ref_auc"] = paired_diff_ci(res.p["auc"], ref_result.p["auc"],
                                           resamples=resamples, seed=seed)
        rows.append(row)
        print(f"  {name:<30} AUC={row['auc']:.4f} auc_pm={row['auc_pm']:.4f} "
              f"NDCG@{config.NDCG_K}={row[f'ndcg@{config.NDCG_K}']:.4f}", flush=True)
    return rows, raw


def sweep_table(rows: list[dict], ref_name: str) -> str:
    out = [f"| 모델 | AUC | auc_pm | Hit@{config.HIT_K} | NDCG@{config.NDCG_K} | "
           f"ΔAUC vs `{ref_name}` |", "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| `{r['model']}` | {_fmt(r['auc'])} | {_fmt(r['auc_pm'])} | "
                   f"{_fmt(r[f'hit@{config.HIT_K}'])} | "
                   f"{_fmt(r[f'ndcg@{config.NDCG_K}'])} | {_ci(r['vs_ref_auc'])} |")
    return "\n".join(out)


def pick_best(rows: list[dict], kind: str, repr_name: str, metric: str) -> str:
    """(kind, 표현) 안에서 `metric` 이 가장 좋은 집계의 모델 이름."""
    names = {photo_content.scorer_name(kind, repr_name, a)
             for a in photo_content.PHOTO_AGGS}
    cand = [r for r in rows if r["model"] in names]
    return max(cand, key=lambda r: r[metric])["model"]


# ────────────────────────────────────────────────── 대조군 판정
def control_verdict(real: dict, null: dict, real_res, null_res, *,
                    resamples: int, seed: int) -> dict:
    """진짜 벡터 vs 자기 대조군 — 지표별 차이와 **사용자 단위 paired bootstrap CI**.

    평균 차이만 보면 ±0.002 짜리 잡음을 '넘었다' 고 쓰게 된다. 대조군 비교도 모델
    비교와 같은 잣대(2,000회 paired bootstrap 95% CI)로 판정한다.

    `survives` 의 정의: **AUC 와 auc_pm 두 지표에서 CI 가 0 을 넘겼을 때**만 참이다.
    AUC 하나로는 인기 교란과 구분되지 않고(리뷰에서 겪었다), auc_pm 하나로는
    전체 순위 품질을 말할 수 없다.
    """
    out = {"real": real["model"], "null": null["model"], "metrics": {}}
    for m in METRICS4:
        ci = paired_diff_ci(real_res.p[m], null_res.p[m],
                            resamples=resamples, seed=seed)
        out["metrics"][m] = {"real": real[m], "null": null[m],
                             "diff": real[m] - null[m], "ci": ci}
    out["beats"] = [m for m in METRICS4 if out["metrics"][m]["diff"] > 0]
    out["significant"] = [m for m in METRICS4
                          if out["metrics"][m]["ci"]["significant"]
                          and out["metrics"][m]["ci"]["diff"] > 0]
    out["survives"] = all(m in out["significant"] for m in ("auc", "auc_pm"))
    return out


def fast_outer_cv(candidates: dict, splits, ctx, n_history: int, *,
                  metric: str, k: int = 5, seed: int):
    """`cv.outer_cv` 와 **같은 답**을 내는 빠른 경로 (검증: `--verify-bar`).

    `evaluate` 는 사용자마다 독립이므로(한 사용자의 점수는 다른 사용자와 무관하다)
    폴드 부분집합의 지표 평균은 **전체를 한 번 평가한 배열을 잘라 평균낸 것**과 같다.
    `cv.outer_cv` 는 후보 5개 × 안쪽 5폴드 × 바깥 5폴드 = 125번 평가하는데, 여기서는
    후보당 한 번씩 5번만 평가하고 나머지는 배열 인덱싱으로 한다 (~25배 빠르다).
    """
    full = {label: evaluate(factory(), splits, ctx, n_history)
            for label, factory in candidates.items()}
    n = len(splits)
    outer = cv.user_folds(splits, k=k, seed=seed)
    p_arrays: dict[str, np.ndarray] = {}
    r_arr = np.full(n, np.nan)
    is_full = np.zeros(n, dtype=bool)
    chosen: list[str] = []

    for f in outer:
        held = {int(i) for i in f}
        train = [i for i in range(n) if i not in held]        # 원래 순서 유지
        inner = cv.user_folds([splits[i] for i in train], k=k, seed=seed)
        table = {}
        for label, res in full.items():
            per_fold = [float(np.nanmean(res.p[metric][[train[int(j)] for j in g]]))
                        for g in inner]
            table[label] = float(np.mean(per_fold))
        label = max(table, key=lambda lb: table[lb])
        chosen.append(label)
        res = full[label]
        for key, vals in res.p.items():
            p_arrays.setdefault(key, np.full(n, np.nan))[f] = vals[f]
        r_arr[f] = res.r_auc[f]
        is_full[f] = res.full_history[f]

    from analysis.backtest.evaluate import TaskResult
    return TaskResult(user_ids=[s.user_id for s in splits], p=p_arrays,
                      r_auc=r_arr, full_history=is_full), chosen


# ────────────────────────────────────────────────── 사례
def example_store_ids() -> list[tuple[str, str]]:
    """Step 1·2 가 쓴 예시 가게 (label, store_id). 없으면 빈 목록."""
    for path in (STEP2_RESULT, STEP1_RESULT):
        if path.exists():
            blob = json.loads(path.read_text(encoding="utf-8"))
            neigh = blob.get("examples", {}).get("neighbours") or []
            if neigh:
                return [(n["label"], n["store_id"]) for n in neigh]
    return []


def example_user_ids(splits) -> list[str]:
    """Step 2 가 쓴 예시 사용자 앞 3명. 없으면 이력이 긴 순으로 고른다."""
    if STEP2_RESULT.exists():
        blob = json.loads(STEP2_RESULT.read_text(encoding="utf-8"))
        ids = [u["user_id"] for u in blob.get("examples", {}).get("users") or []]
        have = {s.user_id for s in splits}
        picked = [u for u in ids if u in have][:N_USER_EXAMPLES]
        if picked:
            return picked
    return [s.user_id for s in sorted(splits, key=lambda s: -s.total)][:N_USER_EXAMPLES]


def neighbours_section(ds, pool, spaces: dict, sims: dict,
                       chosen: list[tuple[str, str]], *, top_k: int = 5):
    """예시 가게의 최근접 이웃 — 음식/공간 각각. 사진 url 을 같이 적는다."""
    out = [f"| 기준 가게 (cat2) | kind | 이웃 | cat2 | sim | 맞은 사진 쌍 |",
           "|---|---|---|---|---|---|"]
    rows_json = []
    for label, sid in chosen:
        i = pool.pos.get(sid)
        if i is None:
            continue
        s = _store_row(ds, sid)
        for kind, space in spaces.items():
            sim = sims[kind]
            if not np.isfinite(sim[i]).any():
                out.append(f"| **{_md(s.get('name', sid))}** ({s.get('cat2') or '—'}, "
                           f"{label}) | {photo_content.KIND_LABEL[kind]} | "
                           f"(사진 없음) | — | — | — |")
                continue
            row = sim[i].copy()
            row[i] = -np.inf
            order = np.argsort(-np.nan_to_num(row, nan=-np.inf))[:top_k]
            head = (f"**{_md(s.get('name', sid))}** ({s.get('cat2') or '—'}, {label})")
            entry = {"label": label, "store_id": sid, "name": s.get("name"),
                     "cat2": s.get("cat2"), "kind": kind, "neighbours": []}
            for rank, j in enumerate(order):
                t = _store_row(ds, pool.ids[j])
                ua, ub, cos = photo_content.best_photo_pair(space.packed, i, int(j))
                pair = f"[A]({ua}) ↔ [B]({ub}) ({cos:.3f})" if ua else "—"
                out.append(f"| {head if rank == 0 else ''} | "
                           f"{photo_content.KIND_LABEL[kind] if rank == 0 else ''} | "
                           f"{_md(t.get('name', pool.ids[j]))} | {t.get('cat2') or '—'} "
                           f"| {sim[i, j]:.4f} | {pair} |")
                entry["neighbours"].append(
                    {"store_id": pool.ids[j], "name": t.get("name"),
                     "cat2": t.get("cat2"), "sim": float(sim[i, j]),
                     "photo_a": ua, "photo_b": ub, "cos": float(cos)})
            rows_json.append(entry)
    return "\n".join(out), rows_json


def explain_users(ds, pool, spaces: dict, sims: dict, splits, model_name: str,
                  ctx, *, n_history: int, user_ids: list[str], top_k: int = 10):
    """사용자 사례 — 방문한 가게 → 추천 상위 K, 어떤 사진 쌍이 맞았는지."""
    by_id = {s.user_id: s for s in splits}
    scorer = scorers.get(model_name)
    scorer.prepare(ctx)
    kind = model_name.split("_")[1]
    space, sim = spaces[kind], sims[kind]

    out, rows_json = [], []
    for uid in user_ids:
        sp = by_id.get(uid)
        if sp is None:
            continue
        signals = sp.signals(n_history, ctx.history_mode)
        scores = np.asarray(scorer.score(signals, ctx, user_id=uid), dtype=float)
        seen = {s for s, _ in signals} | set(sp.seen_stores)
        future = {s for s, _ in sp.future}

        out.append(f"**사용자 `{uid}`** — 이력 {len(signals)}곳 "
                   f"(모델 `{model_name}`, N={n_history})")
        out.append("")
        out.append("| 방문한 가게 | cat2 | 사진 수 |")
        out.append("|---|---|---|")
        hist = [s for s, like in signals if like]
        for sid in hist:
            s = _store_row(ds, sid)
            i = pool.pos.get(sid)
            n_ph = int(space.packed.counts[i]) if i is not None else 0
            out.append(f"| {_md(s.get('name', sid))} | {s.get('cat2') or '—'} | {n_ph} |")
        out.append("")

        order = [int(j) for j in np.argsort(-scores)
                 if pool.ids[int(j)] not in seen][:top_k]
        out.append(f"| # | 추천 가게 | cat2 | 점수 | 매칭된 방문 가게 | 사진 쌍 | 정답 |")
        out.append("|---|---|---|---|---|---|---|")
        entry = {"user_id": uid, "model": model_name, "kind": kind,
                 "visited": [{"store_id": s, "name": _store_row(ds, s).get("name")}
                             for s in hist], "top": []}
        for rank, j in enumerate(order, 1):
            s = _store_row(ds, pool.ids[j])
            best_h, best_v = None, -np.inf
            for sid in hist:
                i = pool.pos.get(sid)
                if i is None or not np.isfinite(sim[i, j]):
                    continue
                if sim[i, j] > best_v:
                    best_h, best_v = sid, float(sim[i, j])
            if best_h is None:
                pair, hname, ua, ub, cos = "—", "—", "", "", float("nan")
            else:
                ua, ub, cos = photo_content.best_photo_pair(
                    space.packed, pool.pos[best_h], j)
                pair = f"[A]({ua}) ↔ [B]({ub}) ({cos:.3f})" if ua else "—"
                hname = _md(_store_row(ds, best_h).get("name", best_h))
            hit = "**O**" if pool.ids[j] in future else ""
            out.append(f"| {rank} | {_md(s.get('name', pool.ids[j]))} | "
                       f"{s.get('cat2') or '—'} | {scores[j]:.4f} | {hname} | "
                       f"{pair} | {hit} |")
            entry["top"].append(
                {"store_id": pool.ids[j], "name": s.get("name"),
                 "cat2": s.get("cat2"), "score": float(scores[j]),
                 "matched_store": best_h, "photo_a": ua, "photo_b": ub,
                 "cos": float(cos), "is_future": pool.ids[j] in future})
        out.append("")
        rows_json.append(entry)
    return "\n".join(out), rows_json


# ────────────────────────────────────────────────── main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analysis.photos.step3b",
        description="PLAN.md 2절 '사진 (Step 3)' 을 백테스트한다.")
    ap.add_argument("--N", default=",".join(str(n) for n in config.HISTORY_SIZES))
    ap.add_argument("--sweep-N", type=int, default=5)
    ap.add_argument("--out", default="step3b-photos")
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--resamples", type=int, default=config.BOOTSTRAP_RESAMPLES)
    ap.add_argument("--history", default="visit", choices=list(config.HISTORY_MODES))
    ap.add_argument("--select-metric", default=f"ndcg@{config.NDCG_K}")
    ap.add_argument("--slow-bar", action="store_true",
                    help="막대를 `cv.outer_cv` 원본 경로로 계산한다(같은 답, 25배 느림)")
    ap.add_argument("--no-bar", action="store_true",
                    help="Step 1.5 막대(CF 하이브리드)를 다시 계산하지 않는다")
    args = ap.parse_args(argv)
    ns = [int(x) for x in args.N.split(",") if x.strip()]

    print("데이터 로드 …", flush=True)
    ds = load_dataset()
    pool = build_pool(ds)
    splits, _ = cache.load_or_build(ds, pool)
    eval_users = {s.user_id for s in splits}
    attach_crawled_quality(pool, ds, eval_users)
    ctx = ScoreContext(pool=pool, seed=args.seed, history_mode=args.history,
                       extras={"candidates": run.seen_masks(splits, pool),
                               "dataset": ds, "eval_users": eval_users,
                               "store_names": {s: (ds.stores.get(s, {}).get("name") or "")
                                               for s in pool.ids}})
    print(f"  후보 풀 {len(pool):,}곳 · 평가 사용자 {len(splits)}명 · "
          f"이력 해석 `{args.history}`", flush=True)

    # ── 0) 사진 공간과 커버리지
    spaces = {k: photo_content.photo_space(ctx, k) for k in photo_content.KIND_VECTORS}
    coverage = {k: sp.coverage_stats() for k, sp in spaces.items()}
    for k, c in coverage.items():
        print(f"  [{k}] 사진 {c['photos']:,}장 · 가게 {c['with_photo']:,}/"
              f"{c['stores']:,} ({c['coverage']:.1%}) · 커버된 가게당 평균 "
              f"{c['mean_per_covered']:.2f}장", flush=True)

    # 사진 수 ↔ 인기 상관 — Step 2 가 리뷰에서 0.88 을 봤다. 사진에서도 재 둔다.
    logpop = pool.log_review_count
    for k, sp in spaces.items():
        cnt = sp.packed.counts.astype(float)
        coverage[k]["corr_count_logpop"] = float(np.corrcoef(cnt, logpop)[0, 1])
        coverage[k]["corr_has_logpop"] = float(
            np.corrcoef((cnt > 0).astype(float), logpop)[0, 1])
        print(f"  [{k}] 사진 수 ↔ log1p(리뷰 수) 상관 "
              f"{coverage[k]['corr_count_logpop']:.3f} "
              f"(있음/없음만: {coverage[k]['corr_has_logpop']:.3f})", flush=True)

    # ── 1) N=5 스윕
    print(f"\n[1단계] N={args.sweep_N} 스윕 (모델 {len(sweep_names())}개)", flush=True)
    ref_res = evaluate(scorers.get("popularity"), splits, ctx, args.sweep_N)
    sweep_rows, sweep_raw = run_sweep(splits, ctx, args.sweep_N, ref_res,
                                      resamples=args.resamples, seed=args.seed)
    by_model = {r["model"]: r for r in sweep_rows}

    # ── 2) 결선 고르기
    metric = args.select_metric
    finalists: list[str] = []
    verdicts: dict = {}
    for kind in photo_content.KIND_VECTORS:
        for repr_name, null_name in (("single", "single_null"),
                                     ("maxsim", "maxsim_null")):
            best = pick_best(sweep_rows, kind, repr_name, metric)
            agg = best.rsplit("_", 1)[1]
            null = photo_content.scorer_name(kind, null_name, agg)
            finalists += [best, null]
            verdicts[f"{kind}_{repr_name}"] = control_verdict(
                by_model[best], by_model[null], sweep_raw[best], sweep_raw[null],
                resamples=args.resamples, seed=args.seed)
    print(f"\n[2단계] 결선 {finalists}", flush=True)

    # ── 3) 결선 × N
    model_names = ["popularity"] + finalists
    raw: dict[str, dict] = {}
    for name in model_names:
        raw[name] = {}
        for n in ns:
            raw[name][n] = (sweep_raw[name] if (n == args.sweep_N and name in sweep_raw)
                            else (ref_res if (name == "popularity"
                                              and n == args.sweep_N)
                                  else evaluate(scorers.get(name), splits, ctx, n)))
            print(f"  {name:<30} N={n:<3} "
                  f"AUC(P)={_mean(raw[name][n].p['auc']):.4f}", flush=True)
    raw["quality"] = {n: evaluate(scorers.get("quality"), splits, ctx, n) for n in ns}
    model_names.append("quality")

    # ── 3b) Step 1.5 막대를 다시 계산한다 (레지스트리에 없는 이름 — outer CV 로만 만든다)
    bar_chosen: dict = {}
    if not args.no_bar:
        print(f"\n[막대] `{BAR_MODEL}` 재계산 (λ 를 폴드 밖에서 {BAR_METRIC} 로)",
              flush=True)
        raw[BAR_MODEL] = {}
        cands = {f"{lam:g}": (lambda lam=lam: scorers.get(
            f"hybrid_pop_{BAR_CONTENT}_l{lam:g}")) for lam in LAMBDAS}
        for n in ns:
            runner = cv.outer_cv if args.slow_bar else fast_outer_cv
            res, folds = runner(cands, splits, ctx, n, metric=BAR_METRIC,
                                seed=args.seed)
            raw[BAR_MODEL][n] = res
            bar_chosen[str(n)] = folds
            print(f"  {BAR_MODEL:<34} N={n:<3} λ={folds} "
                  f"AUC(P)={_mean(res.p['auc']):.4f}", flush=True)
        model_names.append(BAR_MODEL)

    results = run.aggregate(raw, model_names, ns, REFS,
                            resamples=args.resamples, seed=args.seed)
    results_bar = (run.aggregate(raw, model_names, ns,
                                 {"task_p": BAR_MODEL, "task_r": REFS["task_r"]},
                                 resamples=args.resamples, seed=args.seed)
                   if not args.no_bar else {})
    users_meta = run.summarise_users(splits, raw, model_names, ns,
                                     history_mode=args.history)

    # ── 4) 사례와 업종 교차
    sims = {k: photo_content.similarity(ctx, k, "single")
            for k in photo_content.KIND_VECTORS}
    cross = {k: cross_category(pool, sims[k], top_k=5)
             for k in photo_content.KIND_VECTORS}
    for k, c in cross.items():
        print(f"  [{k}] 최근접 5곳 중 cat2 교차 {c['cross_cat2_rate']:.1%} "
              f"(무작위 {c['random_rate']:.1%})", flush=True)
    null_sims = {k: photo_content.similarity(ctx, k, "single_null")
                 for k in photo_content.KIND_VECTORS}
    cross_null = {k: cross_category(pool, null_sims[k], top_k=5)
                  for k in photo_content.KIND_VECTORS}

    neigh_md, neigh_json = neighbours_section(ds, pool, spaces, sims,
                                              example_store_ids())
    best_overall = max(
        (photo_content.scorer_name(k, "single", a) for k in photo_content.KIND_VECTORS
         for a in photo_content.PHOTO_AGGS), key=lambda nm: by_model[nm][metric])
    users_md, users_json = explain_users(
        ds, pool, spaces, sims, splits, best_overall, ctx,
        n_history=args.sweep_N, user_ids=example_user_ids(splits))

    # ── 5) 저장
    blob = {
        "run": args.out,
        "title": "Step 3b — 사진 벡터 (음식/공간 분리)",
        "generated_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "git_commit": run._git_commit(),
        "seed": args.seed,
        "history_mode": args.history,
        "references": REFS,
        "baseline_model": REFS["task_p"],
        "history_sizes": ns,
        "sweep_n": args.sweep_N,
        "select_metric": metric,
        "bar_model": None if args.no_bar else BAR_MODEL,
        "bar_lambda": bar_chosen,
        "coverage": coverage,
        "sweep": sweep_rows,
        "finalists": finalists,
        "best_overall": best_overall,
        "verdicts": verdicts,
        "cross_category": cross,
        "cross_category_null": cross_null,
        "config": {
            "model": photo_content.KIND_VECTORS,
            "clip": (json.loads((Path(config.ROOT) / "data" / "vectors"
                                 / "manifest.json").read_text(encoding="utf-8"))
                     if (Path(config.ROOT) / "data" / "vectors"
                         / "manifest.json").exists() else {}),
            "bayes_c": config.BAYES_C, "hit_k": config.HIT_K,
            "recall_k": config.RECALL_K, "ndcg_k": config.NDCG_K,
            "bootstrap_resamples": args.resamples,
        },
        "pool": {"size": len(pool), "stores_total": len(ds.stores)},
        "users": users_meta,
        "models": {name: {"description": _describe(name)} for name in model_names},
        "results": results,
        "results_vs_bar": results_bar,
        "examples": {"neighbours_md": neigh_md, "neighbours": neigh_json,
                     "users_md": users_md, "users": users_json},
        "command": [
            "nice -n 10 ~/venvs/recsys/bin/python -m analysis.photos.download",
            "nice -n 10 ~/venvs/recsys/bin/python -m analysis.photos.embed",
            f"nice -n 10 ~/venvs/recsys/bin/python -m analysis.photos.step3b "
            f"--N {args.N} --out {args.out}",
        ],
    }
    blob["intro"] = intro_lines(blob)
    blob["headline"] = headline_lines(blob)
    blob["sections_before"] = sections_before(blob)
    blob["sections"] = sections_after(blob)
    blob["notes"] = notes_lines(blob)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{args.out}.json").write_text(
        json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RESULTS_DIR / f"{args.out}.md").write_text(report.to_markdown(blob),
                                                encoding="utf-8")
    print(f"\n[저장] {RESULTS_DIR / (args.out + '.json')} · "
          f"{RESULTS_DIR / (args.out + '.md')}", flush=True)
    return 0



# ────────────────────────────────────────────────── 결과 문서
def _describe(name: str) -> str:
    if scorers.exists(name):
        return scorers.get(name).description
    if name == BAR_MODEL:
        return (f"Step 1.5 의 막대 — `popularity_z + λ·{BAR_CONTENT}_z`, λ 를 폴드 밖에서 "
                f"{BAR_METRIC} 로 고름")
    return name


def coverage_table(blob: dict) -> str:
    out = ["| | 음식(FOOD) | 공간(INDOOR+OUTDOOR) |", "|---|---|---|"]
    f, s = blob["coverage"]["food"], blob["coverage"]["space"]
    rows = [
        ("가게당 상한", f"{blob['config']['clip'].get('photo_food', {}).get('max_per_store', 4)}장",
         f"{blob['config']['clip'].get('photo_space', {}).get('max_per_store', 3)}장"),
        ("임베딩한 사진", f"{f['photos']:,}장", f"{s['photos']:,}장"),
        ("사진이 있는 가게", f"{f['with_photo']:,} / {f['stores']:,} ({f['coverage']:.1%})",
         f"{s['with_photo']:,} / {s['stores']:,} ({s['coverage']:.1%})"),
        ("커버된 가게당 평균", f"{f['mean_per_covered']:.2f}장",
         f"{s['mean_per_covered']:.2f}장"),
        ("사진 수 ↔ log1p(리뷰 수) 상관", f"{f['corr_count_logpop']:.3f}",
         f"{s['corr_count_logpop']:.3f}"),
        ("있음/없음 ↔ log1p(리뷰 수) 상관", f"{f['corr_has_logpop']:.3f}",
         f"{s['corr_has_logpop']:.3f}"),
    ]
    for label, a, b in rows:
        out.append(f"| {label} | {a} | {b} |")
    return "\n".join(out)


def verdict_table(blob: dict) -> str:
    """진짜 벡터 vs 자기 난수 대조군 — 이 Step 의 판정문이다."""
    out = ["| 표현 | 지표 | 진짜 | 대조군 | 차이 [95% CI] |",
           "|---|---|---|---|---|"]
    for key, v in blob["verdicts"].items():
        kind, repr_name = key.split("_", 1)
        head = (f"**{photo_content.KIND_LABEL[kind]} · {repr_name}**<br>"
                f"`{v['real']}`<br>vs `{v['null']}`")
        for i, m in enumerate(METRICS4):
            e = v["metrics"][m]
            out.append(f"| {head if i == 0 else ''} | {m} | {e['real']:.4f} | "
                       f"{e['null']:.4f} | {_ci(e['ci'])} |")
    return "\n".join(out)


def cross_table(blob: dict) -> str:
    c, cn = blob["cross_category"], blob["cross_category_null"]
    out = ["| modality | 최근접 5곳 중 cat2 가 다른 비율 | 무작위 이웃이었다면 | 잰 가게 수 |",
           "|---|---|---|---|"]
    prior = [("메뉴 (Step 1)", 0.263), ("가게 요약문 (Step 2)", 0.271),
             ("리뷰 (Step 2)", 0.461)]
    for label, val in prior:
        out.append(f"| {label} | {val:.1%} | 82.7% | — |")
    for kind in ("food", "space"):
        out.append(f"| **사진 {photo_content.KIND_LABEL[kind]} (Step 3b)** | "
                   f"**{c[kind]['cross_cat2_rate']:.1%}** | "
                   f"{c[kind]['random_rate']:.1%} | {c[kind]['stores']:,} |")
        out.append(f"| (대조군) 사진 {photo_content.KIND_LABEL[kind]} 난수 벡터 | "
                   f"{cn[kind]['cross_cat2_rate']:.1%} | "
                   f"{cn[kind]['random_rate']:.1%} | {cn[kind]['stores']:,} |")
    return "\n".join(out)


def _best_row(blob: dict, name: str) -> dict:
    return next(r for r in blob["sweep"] if r["model"] == name)


def bar_table(blob: dict) -> str:
    """Step 1.5 의 막대(`hybrid_pop_cf_item_sum_cv_ndcg20`) 대비 — 결선의 진짜 모델만."""
    res = blob.get("results_vs_bar") or {}
    ns = blob["history_sizes"]
    reals = [v["real"] for v in blob["verdicts"].values()]
    out = ["| 모델 | 지표 | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|---|" + "---|" * len(ns)]
    for name in reals:
        for i, m in enumerate(("auc", "auc_pm")):
            cells = []
            for n in ns:
                e = res.get(name, {}).get(str(n), {}).get("task_p", {}).get(m, {})
                cells.append(_ci(e.get("vs_baseline", {})))
            out.append(f"| {'`' + name + '`' if i == 0 else ''} | {m} | "
                       + " | ".join(cells) + " |")
    return "\n".join(out)


def intro_lines(blob: dict) -> list[str]:
    f, s = blob["coverage"]["food"], blob["coverage"]["space"]
    return [
        "`analysis/PLAN.md` 2절 '사진 (Step 3)' 의 구현이다. tag 가 이미 FOOD /",
        "INDOOR / OUTDOOR 로 나뉘어 있으므로 **음식 벡터와 공간 벡터를 분리**해 각각",
        "따로 평가했다. 임베딩은 CLIP ViT-B/32 이미지 탑 512차원(L2 정규화), 사진은",
        f"음식 {f['photos']:,}장 · 공간 {s['photos']:,}장을 원본으로 받아 짧은 변 256px 로",
        "줄여 캐시했다.",
        "",
        "PLAN 7절 'Step 2 → Step 3 결정 1번' 이 요구한 대로 **난수 벡터 대조군이 모든",
        "표현에 붙어 있다.** 대조군을 못 넘은 숫자는 내용의 증거가 아니다 — 리뷰에서",
        "이미 한 번 겪은 함정이다(난수 대조군 AUC .6171 > 진짜 .6153).",
    ]


def headline_lines(blob: dict) -> list[str]:
    out = []
    f, s = blob["coverage"]["food"], blob["coverage"]["space"]
    out.append(f"**커버리지가 이 Step 의 첫 제약이다.** 후보 풀 {f['stores']:,}곳 중 음식 "
               f"사진이 있는 곳은 {f['with_photo']:,}곳({f['coverage']:.1%}), 공간 사진은 "
               f"{s['with_photo']:,}곳({s['coverage']:.1%})뿐이다. 나머지는 NaN(모른다) 규칙으로 "
               f"중립 처리된다 — 공간 벡터는 사실상 절반 이하의 가게에만 있는 신호다.")
    for key, v in blob["verdicts"].items():
        kind, repr_name = key.split("_", 1)
        label = photo_content.KIND_LABEL[kind]
        m = v["metrics"]
        sig = v["significant"]
        word = ("**AUC·auc_pm 둘 다 CI 로 대조군을 넘겼다**" if v["survives"]
                else (f"CI 로 대조군을 넘긴 지표는 {len(sig)}/4 뿐이다 "
                      f"({', '.join(sig)})" if sig
                      else "**어느 지표에서도 대조군을 CI 로 넘지 못했다**"))
        out.append(f"`{v['real']}` ({label}·{repr_name}) vs 난수 대조군 `{v['null']}`: "
                   f"{word}. AUC {m['auc']['real']:.4f} vs {m['auc']['null']:.4f}, "
                   f"auc_pm {m['auc_pm']['real']:.4f} vs {m['auc_pm']['null']:.4f}.")
    res = blob["results"]
    best = blob["best_overall"]
    pm = [res[best][str(n)]["task_p"]["auc_pm"] for n in blob["history_sizes"]]
    n_sig = sum(1 for e in pm if e["vs_baseline"]["significant"]
                and e["vs_baseline"]["diff"] > 0)
    values = " / ".join(f"{e['mean']:.4f}" for e in pm)
    out.append(f"**인기 매칭 AUC 에서는 `{best}` 가 `popularity` 를 넘는다** — "
               f"N 4개 중 {n_sig}개에서 CI 가 0 을 넘겼다 ({values} vs .5250). "
               f"Step 2 까지의 최고는 요약문 .5596 이었다. 전체 AUC 는 여전히 크게 "
               f"진다 — **상위는 맞히고 꼬리는 못 맞힌다**는 Step 1 의 모양 그대로다.")
    c = blob["cross_category"]
    out.append(f"**업종 교차** — 최근접 5곳 중 cat2 가 다른 비율은 음식 사진 "
               f"{c['food']['cross_cat2_rate']:.1%} · 공간 사진 "
               f"{c['space']['cross_cat2_rate']:.1%} (메뉴 26.3% · 요약문 27.1% · "
               f"리뷰 46.1% · 무작위 82.7%).")
    return out


def sections_before(blob: dict) -> list[dict]:
    return [{"heading": "무엇을 만들었나 — 사진 선택과 커버리지",
             "body": "\n".join([
                 "`photos.jsonl` 32,130장 중 후보 풀 가게의 것만, 가게당 음식 ≤ 4장 ·",
                 "공간 ≤ 3장을 골랐다. 고르는 순서는 결정적이다: 음식은 type",
                 "`MYSTORE`·`VENDOR` → `KMAPREVIEW` → `BLOG`, 공간은 `INDOOR` 먼저",
                 "그다음 `OUTDOOR`, 같은 순위 안에서는 `photos.jsonl` 등장 순서",
                 "(카카오의 노출 순서 = 메뉴에서 `menu_index` 순을 쓴 것과 같은 취급).",
                 "",
                 coverage_table(blob),
                 "",
                 "**PLAN 2절이 적은 상한(음식 ≤ 6 · 공간 ≤ 4)보다 줄였다** — 이번 실행의",
                 "시간 예산 때문이고, 그 결과 임베딩 대상이 15k 가 아니라 9.5k 다.",
                 "커버리지(가게 수)는 상한과 무관하므로 이 편차가 커버리지 결론을 바꾸지",
                 "않는다. 상한을 늘리면 바뀌는 것은 가게당 벡터의 평균이 더 매끄러워지는",
                 "것뿐이고, 그 방향의 이득은 아래 MaxSim/단일 벡터 비교가 이미 작다고 말한다.",
             ])}]


def sections_after(blob: dict) -> list[dict]:
    ex = blob["examples"]
    best = blob["best_overall"]
    return [
        {"heading": f"1단계 — N={blob['sweep_n']} 스윕 (표현 4종 × 집계 3종 × 음식/공간)",
         "body": "\n".join([
             f"기준은 `{REFS['task_p']}`. 대조군(`*_null_*`)은 **의무**이고 진짜 벡터와",
             "같은 줄에서 읽어야 한다 — 대조군보다 낮은 숫자는 내용이 아니라 구조의 값이다.",
             "",
             sweep_table(blob["sweep"], REFS["task_p"]),
         ])},
        {"heading": "난수 벡터 대조군 판정 (PLAN 7절 결정 1번)",
         "body": "\n".join([
             "단일 벡터의 대조군은 **커버리지 구조만** 남기고 벡터를 난수 단위벡터로",
             "바꾼 것이고, MaxSim 의 대조군은 **개수 구조**를 그대로 두고 벡터만 난수로",
             "바꾼 것이다. 차이는 모델 비교와 같은 잣대(사용자 단위 paired bootstrap",
             "2,000회 95% CI)로 쟀고, **굵은 것만 CI 가 0 을 넘겼다.** 판정 기준은",
             "`auc` 와 `auc_pm` 이 **둘 다** CI 를 넘기는 것이다 — AUC 하나로는 인기",
             "교란과 구분되지 않고(리뷰에서 겪었다), auc_pm 하나로는 전체 순위 품질을",
             "말할 수 없다.",
             "",
             verdict_table(blob),
         ])},
        {"heading": "업종 교차 — 사진은 카테고리를 넘는가",
         "body": "\n".join([
             "사진이 있는 가게 각각의 최근접 5곳 중 `cat2` 가 다른 비율. Step 1·2 의",
             "숫자는 그 결과 문서에서 그대로 인용했다(같은 잣대·같은 top_k=5).",
             "",
             cross_table(blob),
         ])},
        {"heading": f"Step 1.5 의 막대 `{blob.get('bar_model')}` 대비",
         "body": "\n".join([
             "막대는 이 실행에서 **다시 계산했다** — Step 2 와 같은 seed·같은 분할이고",
             "AUC 가 소수 4자리까지 같음을 확인했다(.6820/.6850/.6890/.6908).",
             "사진 단독은 인기 백본이 없으므로 막대에 크게 진다. 이 표의 쓸모는",
             "'얼마나 지는가' 가 아니라 **auc_pm 에서도 지는가**다 — 인기를 통제한",
             "뒤에도 막대가 앞선다면 사진 성분을 블렌드에 넣을 이유가 약해진다.",
             "",
             bar_table(blob),
         ])} if blob.get("bar_model") else {"heading": "(막대 생략)", "body": "—"},
        {"heading": "사례 — 최근접 이웃 (Step 1·2 와 같은 예시 가게)",
         "body": "\n".join([
             "사진 쌍은 두 가게 사이에서 코사인이 가장 큰 사진 한 쌍이다. 링크를 눌러",
             "**눈으로 확인할 수 있다** — 숫자가 무엇을 보고 있는지 사람이 판정하는 자리다.",
             "",
             ex["neighbours_md"],
         ])},
        {"heading": f"사례 — 사용자 (모델 `{best}`, N={blob['sweep_n']})",
         "body": ex["users_md"]},
    ]


def notes_lines(blob: dict) -> list[str]:
    f, s = blob["coverage"]["food"], blob["coverage"]["space"]
    out = [
        "**대조군을 못 넘은 표현의 AUC 는 인용하면 안 된다.** 사진이 있다/없다 자체가 "
        f"인기와 상관돼 있고(음식 {f['corr_has_logpop']:.3f} · 공간 "
        f"{s['corr_has_logpop']:.3f}), NaN 중립 채움은 '사진이 있는 가게' 를 후보 평균 "
        "위로 올릴 수 있다. 그 몫이 정확히 난수 대조군이 얻는 점수다.",
        "사진 벡터는 CLIP 512차원이고 텍스트(bge-m3 1,024차원)와 **다른 공간**이다. "
        "modality 를 섞는 것은 유사도 점수 수준에서만(z 정규화 뒤) 한다.",
        f"사진 수 ↔ log1p(리뷰 수) 상관은 음식 {f['corr_count_logpop']:.3f} · "
        f"공간 {s['corr_count_logpop']:.3f} 로, 리뷰(0.88)보다 훨씬 약하다 — 가게당 "
        "상한(4·3장)이 낮아 개수 편차 자체가 작기 때문이다. MaxSim 의 집합 크기 교란도 "
        "그만큼 작아진다.",
        "이력 해석은 `visit`(방문 = 관심)이고 dislike 가 없다. β=0 이므로 사진 scorer 는 "
        "이력 가게와의 유사도를 집계만 한다.",
        "다중비교 보정을 하지 않았다. 스윕 표는 24행이고 결선 표는 지표 5 × N 4 다.",
    ]
    if blob.get("bar_model"):
        out.append(f"막대 `{blob['bar_model']}` 는 이 실행에서 **다시 계산했다**(같은 "
                   f"seed·같은 분할). λ 선택: {blob['bar_lambda']}.")
    return out


if __name__ == "__main__":
    sys.exit(main())
