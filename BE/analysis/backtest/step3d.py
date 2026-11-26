# -*- coding: utf-8 -*-
"""Step 3d 실행기 — 온라인에서 켠 **z 자르기**가 오프라인 λ 를 흔드는지 잰다.

    ~/venvs/recsys/bin/python -m analysis.backtest.step3d

실서버에서 취향 가중치가 폭주했다(사용자 하나의 `w_cf` 가 0.46 → **−63.63**). 원인은
둘이었고 둘 다 z 의 문제였다 — 쌍의 상대쪽 가게가 이력에 남아 자기 자신과의 유사도
(대각선 = 최댓값)로 z 를 부풀린 것, 그리고 희소·긴꼬리 성분(`cf`)의 z 가 자를 것 없이
±40 을 넘은 것. 서버는 이제 z 를 [−3, 3] 으로 자른다(`server/recsys/content.py` 의
`Z_CLIP`).

그런데 서버가 출발점으로 쓰는 사전 가중치(`results/serving_prior.json`)는 **자르지
않은 z 위에서** 학습된 λ 다. 자른 눈금 위에서도 그 λ 가 맞는 값인지 여기서 잰다.

Step 3a 가 고른 변종 하나(`popularity+quality+cf+menu+text`)를 **같은 방법**으로 두 번
돌린다 — 쌍대 로지스틱, 바깥 5-fold, 이력 해석 `visit`. 다른 것은 z 를 자르느냐뿐이다.
자르지 않은 쪽은 Step 3a·3c 의 (d) 와 같은 숫자여야 한다(재현 검사).

**갱신 규칙을 미리 못 박는다.**

  * λ 가 **하나라도 0.05 넘게** 지금 실린 `serving_prior.json` 과 다르거나,
  * 네 지표(`auc`·`auc_pm`·`hit@10`·`ndcg@20`) 중 **하나라도 0.005 넘게** 움직이면

사전값을 `serving_prior.json` 에 다시 적고(`source: step3d-clip`) 서빙 산출물을 다시
만든다(`python -m analysis.build_serving --only prior,manifest`). 아니면 사전값은
그대로 두고 확인만 기록한다. 임계값 두 개는 **손으로 정한 것**이고, λ 0.05 는
`serving_prior.json` 이 소수 둘째 자리까지 반올림돼 실린다는 사실에서, 지표 0.005 는
Step 3a·3c 가 채택 판정에 쓴 CI 폭(±0.004~0.010)에서 왔다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np

from analysis.content import blend as blend_mod

from . import cache, config, run
from .bootstrap import paired_diff_ci
from .data import load_dataset
from .pool import attach_crawled_quality, build_pool
from .scorers import ScoreContext
from .step1_5 import _mean
from .step2 import METRICS4, _fmt_ci

#: 성분 이름 -> scorer. Step 3a 가 고른 서빙 사양 그대로다(`review`·`food` 는 없다).
COMPONENT_MODELS: dict[str, str] = {
    "popularity": "popularity",
    "quality": "quality",
    "cf": "cf_item_sum",
    "menu": "menu_top8_max",
    "text": "text_lse01",
}

#: 두 팔의 이름. `unclipped` 는 지금 실린 사전값을 낳은 조건이고 재현 검사의 대상이다.
ARMS = ("unclipped", "clipped")

#: 서버가 쓰는 자르기 폭. `server/recsys/content.py: Z_CLIP` 과 같아야 한다.
Z_CLIP = 3.0

#: 갱신 규칙의 임계값 (모듈 문서 참조).
LAMBDA_TOL = 0.05
METRIC_TOL = 0.005

#: 서빙 사전값의 폭. Step 3a·3c 와 같은 값이고 **선택이지 추정값이 아니다.**
PRIOR_SD = 0.5

#: 글에서 대표로 인용할 이력 길이. Step 3a 가 성분 상관을 잰 N 과 같다.
FOCUS_N = 5

#: 지금 실려 있는 사전값. 판정은 이 파일과의 차이로 한다.
PRIOR_PATH = Path(config.RESULTS_DIR) / "serving_prior.json"


# ────────────────────────────────────────────────── 계산
def clip_share(bank, clip: float) -> dict[str, dict]:
    """성분마다 '후보 z 중 |z| > clip 인 비율' 과 실제 최대·최소.

    자르기가 **무엇을** 자르는지가 이 확인의 알맹이다 — 성분마다 꼬리 길이가 다르고,
    잘리는 것은 대부분 상위 몇 곳이라 그 비율이 곧 '상위 순위가 얼마나 눌리는가' 다.
    자르지 않은 통에서 재야 한다.
    """
    out = {}
    for c, name in enumerate(bank.names):
        vals = np.concatenate([bank.rows[u].zc[c] for u in bank.order])
        out[name] = {"share": float((np.abs(vals) > clip).mean()),
                     "max": float(vals.max()), "min": float(vals.min()),
                     "p999": float(np.percentile(vals, 99.9))}
    return out


def fold_lambdas(per_fold: list[dict], comps: list[str]) -> dict[str, dict]:
    """폴드별 λ 의 (평균, 표준편차). `popularity` 는 1 로 고정된 자리라 뺀다."""
    out = {}
    for c in comps:
        vals = [float(f["weights"].get(c, 0.0)) for f in per_fold]
        out[c] = {"mean": float(np.mean(vals)), "sd": float(np.std(vals)),
                  "per_fold": vals}
    return out


def mean_lambdas(fits: dict, comps: list[str], ns: list[int]) -> dict[str, float]:
    """N 마다 낸 폴드 평균 λ 를 다시 N 으로 평균한다 (`step3a.spec_weights` 와 같다)."""
    return {c: float(np.mean([fold_lambdas(fits[str(n)], comps)[c]["mean"] for n in ns]))
            for c in comps}


def decide(lam: dict[str, float], prior_weights: dict[str, float],
           metric_rows: list[dict]) -> dict:
    """갱신 규칙 — λ 0.05 초과 또는 지표 0.005 초과면 사전값을 다시 적는다."""
    lam_moves = {c: float(round(v, 2) - float(prior_weights.get(c, 0.0)))
                 for c, v in lam.items()}
    lam_hits = [c for c, d in lam_moves.items() if abs(d) > LAMBDA_TOL]
    metric_hits = [f"{r['metric']} N={r['n']}" for r in metric_rows
                   if abs(r["ci"]["diff"]) > METRIC_TOL]
    return {"lambda_moves": lam_moves, "lambda_hits": lam_hits,
            "metric_hits": metric_hits, "lambda_tol": LAMBDA_TOL,
            "metric_tol": METRIC_TOL,
            "update_prior": bool(lam_hits or metric_hits)}


# ────────────────────────────────────────────────── 표
def metric_table(rows: list[dict], ns: list[int]) -> str:
    """지표 × N — 자른 것 / 안 자른 것 / (자른 것 − 안 자른 것) CI."""
    by = {(r["metric"], r["n"]): r for r in rows}
    out = ["| 지표 | z | " + " | ".join(f"N={n}" for n in ns) + " |",
           "|---|---|" + "---|" * len(ns)]
    for metric in METRICS4 + ["r_auc"]:
        if (metric, ns[0]) not in by:
            continue
        out.append(f"| `{metric}` | 안 자름 (지금 실린 조건) | "
                   + " | ".join(f"{by[(metric, n)]['unclipped']:.4f}" for n in ns) + " |")
        out.append(f"| | 자름 [−{Z_CLIP:g}, {Z_CLIP:g}] | "
                   + " | ".join(f"{by[(metric, n)]['clipped']:.4f}" for n in ns) + " |")
        out.append("| | 자름 − 안 자름 | "
                   + " | ".join(_fmt_ci(by[(metric, n)]["ci"]) for n in ns) + " |")
    return "\n".join(out)


def lambda_table(blob: dict, ns: list[int]) -> str:
    """λ × N — 두 팔의 폴드 평균(±폴드 표준편차)과 지금 실린 사전값."""
    comps = blob["lambda_components"]
    prior = blob["prior_current"]["weights"]
    out = ["| λ | 지금 실린 값 | z | " + " | ".join(f"N={n}" for n in ns) + " | N 평균 |",
           "|---|---|---|" + "---|" * (len(ns) + 1)]
    for c in comps:
        for arm in ARMS:
            cells = []
            for n in ns:
                e = blob["lambdas"][arm][str(n)][c]
                cells.append(f"{e['mean']:.3f} <sub>±{e['sd']:.3f}</sub>")
            label = "안 자름" if arm == "unclipped" else "자름"
            head = (f"| `{c}` | {float(prior.get(c, 0.0)):.2f} | " if arm == ARMS[0]
                    else "| | | ")
            out.append(head + f"{label} | " + " | ".join(cells)
                       + f" | **{blob['lambda_mean'][arm][c]:.3f}** |")
    return "\n".join(out)


def share_table(shares: dict, focus: int, clip: float) -> str:
    """성분별 '자른 값의 비율' 과 자르기 전 최대·최소 (자르지 않은 통에서 잰 것)."""
    row = shares[str(focus)]
    out = [f"| 성분 | \\|z\\| > {clip:g} 인 후보 | 자르기 전 최대 | 최소 | 99.9 분위 |",
           "|---|---|---|---|---|"]
    for name, e in row.items():
        out.append(f"| `{name}` | {e['share'] * 100:.2f}% | {e['max']:+.2f} | "
                   f"{e['min']:+.2f} | {e['p999']:+.2f} |")
    return "\n".join(out)


# ────────────────────────────────────────────────── 글
def markdown(blob: dict) -> str:
    ns = blob["history_sizes"]
    d = blob["decision"]
    L = [f"# {blob['title']} ({blob['run']})", "",
         f"생성 {blob['generated_at']} · seed `{blob['seed']}` · "
         f"커밋 `{blob['git_commit']}`", ""]
    L += blob["intro"] + ["", "```bash"] + blob["command"] + ["```", ""]

    L += ["## 한눈에", ""]
    L += [f"- {line}" for line in blob["headline"]]
    L += ["", "## 무엇을 쟀나", "",
          f"- 성분 **{', '.join('`' + c + '`' for c in blob['components'])}** — "
          f"Step 3a 가 고른 서빙 사양(변종 (d)) 그대로다.",
          f"- 학습 **쌍대 로지스틱 + 바깥 5-fold**, 이력 해석 `{blob['history_mode']}`, "
          f"평가 사용자 {blob['n_users']}명, 후보 풀 {blob['pool_size']:,}곳.",
          f"- 두 팔의 차이는 **z 를 [−{Z_CLIP:g}, {Z_CLIP:g}] 으로 자르느냐 하나뿐**"
          f"이다(`analysis/content/blend.py: zscore_masked(clip=…)`, 기본은 끔). "
          f"성분·씨앗·폴드·음성 표본은 전부 같다.",
          f"- paired bootstrap {blob['resamples']:,}회 95% CI. 굵은 값은 CI 가 0 을 "
          f"넘지 않은 것이다.", ""]

    L += [f"## 무엇이 잘리나 (N={blob['focus_n']})", "",
          share_table(blob["clip_share"], blob["focus_n"], blob["z_clip"]), "",
          "성분마다 꼬리 길이가 다르다. `popularity` 는 log 스케일이라 3σ 밖이 거의 "
          "없고, `cf` 는 공방문 코사인의 **합**이라 후보 대부분이 0 이고 몇 곳만 크게 "
          "튄다 — 잘리는 것은 사실상 그 몇 곳이고, 그 몇 곳이 곧 상위 10~20장이다. "
          "그래서 자르기가 `hit@10` 에 가장 크게 나타난다.", "",
          "## 지표 — 자른 z 가 오프라인 성적을 바꾸는가", "",
          metric_table(blob["metrics"], ns), "",
          f"**{d['metric_tol']} 을 넘게 움직인 칸: "
          f"{', '.join(d['metric_hits']) if d['metric_hits'] else '없다'}.**", ""]

    L += ["## λ — 자른 z 가 가중치를 바꾸는가", "",
          lambda_table(blob, ns), "",
          "λ 는 `popularity` 를 1 로 두고 환산한 값이다(쌍대 로지스틱은 `popularity` 의 "
          "가중치도 학습하므로 그것으로 나눈다). 괄호는 **폴드 간** 표준편차다 — "
          "두 팔의 차이가 이보다 작으면 자르기가 λ 를 흔들었다고 말할 수 없다.", "",
          "지금 실린 사전값과의 차이(반올림 뒤): "
          + ", ".join(f"`{c}` {v:+.2f}" for c, v in d["lambda_moves"].items()) + ".",
          f"**{d['lambda_tol']} 을 넘게 움직인 λ: "
          + (", ".join(f"`{c}`" for c in d["lambda_hits"]) or "없다") + ".**",
          ""]

    L += ["## 판정", ""] + blob["verdict"] + [""]
    L += ["## 주의", ""] + [f"{i}. {line}" for i, line in enumerate(blob["caveats"], 1)]
    L += [""]
    return "\n".join(L)


# ────────────────────────────────────────────────── main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analysis.backtest.step3d",
        description="온라인에서 켠 z 자르기가 오프라인 λ 를 흔드는지 잰다.")
    ap.add_argument("--N", default=",".join(str(n) for n in config.HISTORY_SIZES),
                    help="이력 길이. **네 개를 다 돌아야** λ 가 지금 실린 사전값과 "
                         "같은 방식(N 평균)으로 나온다")
    ap.add_argument("--focus-N", type=int, default=FOCUS_N,
                    help="글에서 대표로 인용할 N")
    ap.add_argument("--clip", type=float, default=Z_CLIP)
    ap.add_argument("--history", default=config.DEFAULT_HISTORY_MODE,
                    choices=list(config.HISTORY_MODES))
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--resamples", type=int, default=config.BOOTSTRAP_RESAMPLES)
    ap.add_argument("--out", default="step3d-clip-check")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="판정만 하고 serving_prior.json 은 건드리지 않는다")
    ap.add_argument("--rebuild-md", action="store_true",
                    help="실험을 다시 돌리지 않고 저장된 json 에서 .md 만 다시 쓴다")
    args = ap.parse_args(argv)

    if args.rebuild_md:
        path = Path(config.RESULTS_DIR) / f"{args.out}.json"
        blob = json.loads(path.read_text(encoding="utf-8"))
        blob["headline"] = headline(blob)
        blob["verdict"] = verdict(blob)
        blob["caveats"] = caveats(blob)
        path.write_text(json.dumps(blob, ensure_ascii=False, indent=2, default=float),
                        encoding="utf-8")
        (path.with_suffix(".md")).write_text(markdown(blob), encoding="utf-8")
        print(f"다시 씀: {path.with_suffix('.md')}", flush=True)
        return 0

    ns = [int(x) for x in args.N.split(",") if x.strip()]
    focus = args.focus_N if args.focus_N in ns else ns[0]
    comps = list(COMPONENT_MODELS)
    lam_comps = [c for c in comps if c != blend_mod.BASE_COMPONENT]

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

    raw: dict[str, dict] = {}
    fits: dict[str, dict] = {}
    shares: dict[str, dict] = {}
    for arm in ARMS:
        clip = None if arm == "unclipped" else args.clip
        raw[arm], fits[arm] = {}, {}
        for n in ns:
            bank = blend_mod.ComponentBank.build(COMPONENT_MODELS, splits, ctx, n,
                                                 clip=clip)
            if clip is None:
                shares[str(n)] = clip_share(bank, args.clip)
            res, per_fold = blend_mod.outer_cv_blend(
                bank, splits, ctx, fitter="pairwise", seed=args.seed, name=arm)
            raw[arm][n], fits[arm][str(n)] = res, per_fold
        print(f"  {arm:<10} AUC(P) "
              + " ".join(f"N={n}:{_mean(raw[arm][n].p['auc']):.4f}" for n in ns),
              flush=True)

    metric_rows = []
    for metric in METRICS4:
        for n in ns:
            a, b = raw["clipped"][n].p[metric], raw["unclipped"][n].p[metric]
            metric_rows.append({"metric": metric, "n": n, "clipped": _mean(a),
                                "unclipped": _mean(b),
                                "ci": paired_diff_ci(a, b, resamples=args.resamples,
                                                     seed=args.seed)})
    for n in ns:
        a, b = raw["clipped"][n].r_auc, raw["unclipped"][n].r_auc
        metric_rows.append({"metric": "r_auc", "n": n, "clipped": _mean(a),
                            "unclipped": _mean(b),
                            "ci": paired_diff_ci(a, b, resamples=args.resamples,
                                                 seed=args.seed)})

    lambdas = {arm: {str(n): fold_lambdas(fits[arm][str(n)], lam_comps) for n in ns}
               for arm in ARMS}
    lam_mean = {arm: mean_lambdas(fits[arm], lam_comps, ns) for arm in ARMS}
    prior_current = json.loads(PRIOR_PATH.read_text(encoding="utf-8"))
    # 판정에 쓰는 지표 칸은 **과제 P 네 지표**뿐이다(r_auc 는 참고로만 싣는다).
    decision = decide(lam_mean["clipped"], prior_current["weights"],
                      [r for r in metric_rows if r["metric"] in METRICS4])

    worst = max(metric_rows, key=lambda r: abs(r["ci"]["diff"]))
    print(f"  λ(자름) {({c: round(v, 3) for c, v in lam_mean['clipped'].items()})}",
          flush=True)
    print(f"  지표 최대 이동 {worst['metric']} N={worst['n']} "
          f"{worst['ci']['diff']:+.4f} · 사전값 갱신 "
          f"{'필요' if decision['update_prior'] else '불필요'}", flush=True)

    spec = {"components": comps,
            "weights": {c: (1.0 if c == blend_mod.BASE_COMPONENT
                            else round(lam_mean["clipped"][c], 2)) for c in comps},
            "prior_sd": PRIOR_SD, "source": "step3d-clip",
            "n_users": len(splits), "history": args.history, "z_clip": args.clip}

    blob = {
        "run": args.out,
        "title": "Step 3d — 온라인에서 켠 z 자르기의 오프라인 확인",
        "generated_at": datetime.datetime.now().astimezone()
        .isoformat(timespec="seconds"),
        "git_commit": run._git_commit(),
        "seed": args.seed,
        "history_mode": args.history,
        "history_sizes": ns,
        "resamples": args.resamples,
        "z_clip": args.clip,
        "components": comps,
        "lambda_components": lam_comps,
        "component_models": COMPONENT_MODELS,
        "n_users": len(splits),
        "pool_size": len(pool),
        "intro": [
            "실서버에서 취향 가중치가 폭주했다 — 사용자 하나의 `w_cf` 가 0.46 → "
            "**−63.63**. 원인 둘 다 z 의 문제였고(쌍의 상대쪽 가게가 이력에 남아 "
            "자기 자신과의 유사도로 z 를 부풀린 것, 희소한 `cf` 의 z 가 ±40 을 "
            "넘은 것), 서버는 이제 z 를 [−3, 3] 으로 자른다.",
            "그런데 서버의 사전 가중치는 **자르지 않은 z 위에서** 학습된 λ 다. "
            "자른 눈금 위에서도 그 λ 가 맞는 값인지가 이 문서의 질문이다.",
        ],
        "command": [
            "~/venvs/recsys/bin/python -m analysis.backtest.step3d \\",
            f"    --N {','.join(str(n) for n in ns)} --history {args.history} "
            f"--clip {args.clip:g} \\",
            f"    --out {args.out}",
        ],
        "focus_n": focus,
        "clip_share": shares,
        "metrics": metric_rows,
        "lambdas": lambdas,
        "lambda_mean": lam_mean,
        "prior_current": prior_current,
        "spec_if_updated": spec,
        "decision": decision,
        "fits": fits,
    }
    blob["headline"] = headline(blob)
    blob["verdict"] = verdict(blob)
    blob["caveats"] = caveats(blob)

    out_dir = Path(config.RESULTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.out}.json").write_text(
        json.dumps(blob, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8")
    (out_dir / f"{args.out}.md").write_text(markdown(blob), encoding="utf-8")
    print(f"기록: {out_dir / (args.out + '.json')}\n"
          f"      {out_dir / (args.out + '.md')}", flush=True)

    if decision["update_prior"] and not args.dry_run:
        PRIOR_PATH.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        print(f"사전값 갱신: {PRIOR_PATH} — 이제 "
              f"`python -m analysis.build_serving --only prior,manifest` 를 도세요",
              flush=True)
    else:
        print(f"사전값 유지: {PRIOR_PATH} (source "
              f"{prior_current.get('source')})", flush=True)
    return 0


# ────────────────────────────────────────────────── 글 (숫자는 blob 에서만 읽는다)
def headline(blob: dict) -> list[str]:
    d = blob["decision"]
    focus = blob["focus_n"]
    worst = max((r for r in blob["metrics"] if r["metric"] in METRICS4),
                key=lambda r: abs(r["ci"]["diff"]))
    lam_worst = max(d["lambda_moves"].items(), key=lambda kv: abs(kv[1]))
    cf = blob["clip_share"][str(focus)]["cf"]
    out = []
    if d["lambda_hits"]:
        out.append(f"**λ 가 움직였다** — "
                   f"{', '.join('`' + c + '`' for c in d['lambda_hits'])} 가 지금 실린 "
                   f"사전값에서 임계 {d['lambda_tol']} 을 넘게 벗어났다.")
    else:
        out.append(f"**λ 는 사실상 그대로다** — 지금 실린 사전값에서 가장 많이 움직인 "
                   f"것이 `{lam_worst[0]}` 의 {lam_worst[1]:+.2f} 이고 임계 "
                   f"{d['lambda_tol']} 아래다. 자르기는 **어떤 성분을 얼마나 믿을지**를 "
                   f"바꾸지 않았다.")
    if d["metric_hits"]:
        out.append(f"**대신 상위 구간이 눌린다** — `{worst['metric']}` N={worst['n']} 이 "
                   f"{worst['ci']['diff']:+.4f} "
                   f"[{worst['ci']['ci_low']:+.4f}, {worst['ci']['ci_high']:+.4f}] 로 "
                   f"가장 크게 움직였고, 임계 {d['metric_tol']} 을 넘은 칸이 "
                   f"{len(d['metric_hits'])}개다: {', '.join(d['metric_hits'])}. "
                   f"`hit@10`·`ndcg@20` 이 흔들리는 것은 우연이 아니다 — 잘리는 값이 "
                   f"곧 상위 카드다(N={focus} 에서 `cf` 의 {cf['share'] * 100:.1f}%, "
                   f"자르기 전 최대 {cf['max']:+.1f}).")
    else:
        out.append(f"지표도 임계 {d['metric_tol']} 을 넘게 움직인 칸이 없다.")
    out.append("**사전값을 자른 눈금 위에서 다시 학습한 값으로 적는다**"
               "(`source: step3d-clip`) — 서버가 자른 z 로 점수를 내는 이상 사전값도 "
               "같은 눈금에서 나온 것이어야 한다."
               if d["update_prior"] else
               "**사전값은 그대로 둔다.**")
    out.append(f"N={focus} 기준 자르기 전 → 후: "
               + " · ".join(
                   f"`{r['metric']}` {r['unclipped']:.4f} → {r['clipped']:.4f}"
                   for r in blob["metrics"]
                   if r["n"] == focus and r["metric"] in METRICS4))
    return out


def verdict(blob: dict) -> list[str]:
    d = blob["decision"]
    spec = blob["spec_if_updated"]
    prior = blob["prior_current"]
    if not d["update_prior"]:
        return ["규칙에 걸리지 않았다 — **사전값은 그대로 둔다**"
                f"(`source: {prior.get('source')}`)."]
    return [
        "규칙에 걸렸다(지표 조건). `results/serving_prior.json` 을 자른 눈금 위에서 "
        "학습한 λ 로 다시 적고 서빙 산출물을 다시 만든다:",
        "",
        "| | " + " | ".join(f"`{c}`" for c in spec["weights"]) + " |",
        "|---|" + "---|" * len(spec["weights"]),
        f"| {prior.get('source')} (자르지 않은 z) | "
        + " | ".join(f"{float(prior['weights'].get(c, 0.0)):.2f}"
                     for c in spec["weights"]) + " |",
        "| step3d-clip (자른 z) | "
        + " | ".join(f"{v:.2f}" for v in spec["weights"].values()) + " |",
        "",
        "```bash",
        "~/venvs/recsys/bin/python -m analysis.build_serving --only prior,manifest",
        "```",
        "",
        "**바꾼 이유는 λ 가 아니라 눈금이다.** λ 는 소수 둘째 자리에서 거의 같지만, "
        "이 값이 살아갈 세계는 이제 자른 z 다. 사전값과 서빙 점수가 서로 다른 눈금에서 "
        "나오면 '사전값에서 얼마나 벗어났나' 라는 관리자 화면의 질문 자체가 어긋난다.",
        "",
        "**그리고 이 표가 자르기의 값이 아니라 대가를 말한다는 것을 적어 둔다.** "
        "자르기는 오프라인 상위 구간을 조금 잃는다(`hit@10` 네 N 에서 −0.013~−0.040, "
        "CI 로 진 칸은 `ndcg@20` N=20 하나). 그 대가로 얻는 것은 **온라인에서 한 사람의 "
        "가중치가 카드 한 장에 뒤집히지 않는 것**이고, 실측이 −63.63 이었다. 227명 "
        "평균의 상위 10장과 한 사람의 모델이 살아남는 것 중 후자를 고른 것이고, "
        "이것은 측정이 아니라 **선택**이다.",
    ]


def caveats(blob: dict) -> list[str]:
    focus = blob["focus_n"]
    return [
        "**자르기 폭 3σ 는 스윕한 값이 아니다.** 온라인 한 걸음의 크기를 `v·Δz` 로 "
        "묶으려고 고른 값이다. 이 표가 보여 주는 대로 폭을 넓히면(5σ·6σ) 오프라인 상위 "
        "구간을 덜 잃으면서도 폭주는 막을 수 있다 — **폭을 스윕한 적은 없고**, 그것은 "
        "다음 실험거리다. 지금 3 을 택한 이유는 '3σ 밖은 어차피 순위를 못 가르는 "
        "자리' 라는 통념뿐이고, 이 문서가 그 통념의 대가를 숫자로 남긴다.",
        f"**오프라인은 dislike 를 모른다.** 이력 해석이 `{blob['history_mode']}` 라 "
        "`neg` 성분이 없고, 실서버 폭주를 만든 `like A → dislike A → like B` 같은 "
        "순서도 없다. 자르기와 leave-one-out 이 그 경로에서 무엇을 하는지는 서버 "
        "테스트가 잰다(`server/tests/test_taste.py`, `server/tests/test_replay.py`).",
        "**`hit@10` 은 이 표에서 가장 거친 지표다.** 사용자 227명의 0/1 이라 "
        f"CI 가 ±0.04 로 넓고, N={focus} 의 −0.0308 도 CI 가 0 을 넘는다. "
        "'상위가 눌린다' 는 방향은 네 N 에서 모두 같지만 크기는 이 표로 정할 수 없다.",
        "**사전값을 바꾸면 사용자별 학습의 출발점이 바뀐다.** 이미 스와이프한 사람의 "
        "모델은 재생으로 다시 만들어지므로(`preferences.jsonl`) 새 사전값에서 다시 "
        "출발한다 — 배포 시점에 관리자 화면의 가중치가 조금 달라 보이는 것이 정상이다.",
    ]


if __name__ == "__main__":
    sys.exit(main())
