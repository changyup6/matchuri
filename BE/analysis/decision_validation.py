"""선택 제안 검증: production router, frozen label bank, complete 30-swipe runs.

All state is temporary. Synthetic users are not real-user efficacy evidence.
Run from BE: python analysis/decision_validation.py --split development --output /tmp/dev.json
Then freeze the implementation before running --split holdout.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
BE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE / "server"))

import numpy as np
from recsys import content, decision, policy, store, swipe, taste

FAMILIES = ("korean", "japanese_chinese", "western", "coffee_sweets", "broad",
            "budget_meal", "random", "all_dislike", "all_like")
CONTROLS = {"random", "all_dislike", "all_like"}
FLOWS = ("one_at_a_time", "app_10_plus_50", "batch_10", "batch_5")
CONFIGS = {"initial_thresholds": replace(decision.DEFAULT, min_rated=12, min_validation_auc=0.7),
           "revised": decision.DEFAULT}


def uniform(family, seed, sid):
    raw = hashlib.sha256(f"decision-v2|{family}|{seed}|{sid}".encode()).digest()
    return (int.from_bytes(raw[:8], "big") + 0.5) / 2**64


def utility(family, sid):
    s = store.get_store(sid) or {}
    cat = s.get("cat2")
    wanted = {"korean": {"한식"}, "japanese_chinese": {"일식", "중식"},
              "western": {"양식"}, "coffee_sweets": {"카페", "간식"},
              "broad": {"한식", "일식", "중식", "양식"}}
    if family == "random":
        return 0.5
    if family == "all_dislike":
        return 0.0
    if family == "all_like":
        return 1.0
    if family == "budget_meal":
        prices = [m.get("price") for m in store.get_menus(sid, limit=1000)]
        prices = [p for p in prices if isinstance(p, (int, float)) and p > 0]
        fits = cat in {"한식", "일식", "중식", "분식"} and prices and min(prices) <= 10000
    else:
        fits = cat in wanted[family]
    return 0.85 if fits else 0.15


def protocol_batches(flow):
    if flow == "app_10_plus_50":
        return [10, 50]
    return [{"one_at_a_time": 1, "batch_10": 10, "batch_5": 5}[flow]]


def _init_worker():
    # Even accidental calls into persistence must go to the temporary directory.
    global _temporary, _art, _utilities
    _temporary = tempfile.TemporaryDirectory(prefix="decision-validation-")
    os.environ["MATCHURI_STATE"] = _temporary.name
    os.environ["MATCHURI_POLICY"] = "interleave"
    _art = content.load()
    _utilities = {family: np.array([utility(family, sid) for sid in _art.ids])
                  for family in FAMILIES}


def run_case(task):
    family, seed, flow = task
    art = _art
    policy._sessions.clear()
    policy._exposed.clear()
    swipe._sessions.clear()
    taste._sessions.clear()
    uid = f"validation-{family}-{seed}"  # same seed/user across protocols
    sess = policy.session_for(uid)
    sess["legacy"]["rng"] = np.random.default_rng(seed)
    sess["taste"]["rng"] = np.random.default_rng(seed)
    p = _utilities[family]
    labels = {sid: uniform(family, seed, sid) < p[i] for i, sid in enumerate(art.ids)}
    queue, batches, trace = [], [], []
    first = {name: None for name in CONFIGS}
    first_quality = {name: None for name in CONFIGS}
    rule_violations = 0
    timings = {"serve_ms": [], "feedback_ms": [], "decision_full_ms": [], "decision_wait_ms": []}

    def fetch(size, at):
        started = time.perf_counter()
        batch = policy.pick_many(sess, size)
        timings["serve_ms"].append((time.perf_counter() - started) * 1000)
        batches.append({"after_swipes": at, "requested": size, "received": len(batch)})
        queue.extend(batch)

    for size in protocol_batches(flow):
        fetch(size, 0)
    for n in range(1, 31):
        if not queue:
            fetch(protocol_batches(flow)[0], n - 1)
        sid = queue.pop(0)
        started = time.perf_counter()
        policy.record(sess, sid, labels[sid])
        timings["feedback_ms"].append((time.perf_counter() - started) * 1000)
        row = {"swipes": n, "liked": bool(labels[sid]), "offered_fit": bool(p[art.pos[sid]] >= .8),
               "queued": len(queue), "decisions": {}}
        for name, config in CONFIGS.items():
            started = time.perf_counter()
            result = decision.evaluate(sess["taste"], config=config)
            elapsed = (time.perf_counter() - started) * 1000
            if name == "revised":
                timings["decision_full_ms" if result["stability"] is not None else "decision_wait_ms"].append(elapsed)
            chosen = [art.pos[s] for s in result["store_ids"]]
            quality = None
            if chosen:
                quality = {"fit_count": int(sum(p[chosen] >= .8)),
                           "mean_utility": round(float(p[chosen].mean()), 4),
                           "liked_support": sum(art.ids[i] in sess["taste"]["like_ids"] for i in chosen)}
                valid = (len(set(chosen)) == 4 and
                         all(decision._diverse(art, a, b, config) for j, a in enumerate(chosen) for b in chosen[j+1:]))
                rule_violations += not valid
                if first[name] is None:
                    first[name], first_quality[name] = n, quality
            row["decisions"][name] = {k: result[k] for k in ("ready", "status", "stability", "validation_auc", "qualified_count")}
            row["decisions"][name]["quality"] = quality
        trace.append(row)

    # Fixed-horizon baselines are evaluated for everyone, not only successful triggers.
    popular = sorted(range(len(art.ids)), key=lambda i: -(art.pop[i] + .5 * art.quality[i]))
    pop_four = decision._select(art, popular, decision.DEFAULT)
    liked = [art.pos[s] for s in sess["taste"]["like_ids"]]
    liked_four = decision._select(art, list(reversed(liked)), decision.DEFAULT)
    def quality_of(indices):
        return {"available": len(indices) == 4,
                "fit_count": int(sum(p[indices] >= .8)) if indices else None,
                "mean_utility": round(float(p[indices].mean()), 4) if indices else None}
    return {"family": family, "seed": seed, "flow": flow, "first_at": first,
            "first_quality": first_quality, "batches": batches, "trace": trace,
            "baselines_at_30": {"popularity_diverse": quality_of(pop_four), "liked_diverse": quality_of(liked_four)},
            "rule_violations": rule_violations, "timings": timings}


def summary(rows):
    groups = []
    for flow in sorted({r["flow"] for r in rows}):
        for family in FAMILIES:
            selected = [r for r in rows if r["flow"] == flow and r["family"] == family]
            if not selected:
                continue
            for version in CONFIGS:
                hits = [r for r in selected if r["first_at"][version] is not None]
                groups.append({"flow": flow, "family": family, "version": version, "n": len(selected),
                               "at20": sum(r["first_at"][version] <= 20 for r in hits),
                               "at25": sum(r["first_at"][version] <= 25 for r in hits), "at30": len(hits),
                               "all4_fit_at30": sum(r["first_quality"][version]["fit_count"] == 4 for r in hits),
                               "atleast3_fit_at30": sum(r["first_quality"][version]["fit_count"] >= 3 for r in hits),
                               "none_fit_at30": sum(r["first_quality"][version]["fit_count"] == 0 for r in hits),
                               "trigger_ci95": policy.wilson(len(hits), len(selected))})
    return groups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "holdout"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--flows", nargs="+", choices=FLOWS, default=list(FLOWS))
    parser.add_argument("--seeds", type=int)
    parser.add_argument("--seed-start", type=int)
    args = parser.parse_args()
    n = args.seeds or (8 if args.split == "development" else 32)
    start = args.seed_start if args.seed_start is not None else (0 if args.split == "development" else 1000)
    tasks = [(family, seed, flow) for family in FAMILIES for seed in range(start, start+n) for flow in args.flows]
    rows = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker) as executor:
        for i, row in enumerate(executor.map(run_case, tasks), 1):
            rows.append(row)
            if i % 24 == 0:
                print(f"{i}/{len(tasks)} cases completed", flush=True)
    files = [Path(__file__), BE / "server/recsys/decision.py", BE / "server/recsys/taste.py",
             BE / "server/recsys/policy.py", BE / "data/kakao/serving/manifest.json",
             BE.parent / "FE/app/lib/screens/home_screen.dart"]
    output = {"split": args.split, "seed_start": start, "seeds_per_family": n,
              "configs": {k: asdict(v) for k, v in CONFIGS.items()},
              "python": platform.python_version(), "numpy": np.__version__,
              "code_and_data_sha256": {str(p.relative_to(BE.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              "elapsed_s": round(time.perf_counter()-t0, 1), "summary": summary(rows), "runs": rows,
              "limitations": ["Synthetic metadata oracles; not real-user satisfaction.",
                              "No off-policy claim: observational logs lack randomized propensities.",
                              "Confidence intervals describe independent synthetic runs, not real users.",
                              "app_10_plus_50 assumes the second fetch completes before the first swipe; refill flows are counterfactuals."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"output": str(args.output), "runs": len(rows), "elapsed_s": output["elapsed_s"]}))


if __name__ == "__main__":
    main()
