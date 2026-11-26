"""Replay the fixed current-context eligibility policy at scenario endpoints.

This isolated research CLI uses real vectors and hypothetical feedback. It does
not restore user databases, import the API, or change the deployed selector.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time
from unittest.mock import patch

import numpy as np

from .bench import digest, offline_runtime, validate_scenarios
from .context import select_context
from .depth_bench import (audit_groups, audit_source_checks, clean,
                          evaluation_brands, full_order, source_checks)
from .metrics import pairwise_win_rate, recommendation_counts
from .model import SpectralProfiles
from .regularized import RegularizedTemporalTasteModel


def filtered_counts(ids, ranked, served_rank, slate, groups, seen, brands):
    """Keep catalogue denominators and add eligible denominators explicitly."""
    result = recommendation_counts(ids, ranked, slate, groups,
                                   observed=list(seen), brands=brands)
    known_brands = {brands[s] for s in seen if brands[s]}
    unseen = {s for s in ids if s not in seen and (not brands[s] or brands[s] not in known_brands)}
    eligible, top = set(served_rank), set(served_rank[:20])
    result.update(eligible_count=len(eligible), top_count=len(top))
    for name, members in groups.items():
        group = set(members)
        available = group & unseen
        allowed = available & eligible
        new_hits = len(top & available)
        result["groups"][name].update(
            eligible_count=len(group & eligible), eligible_unseen_count=len(allowed),
            top_hits=len(top & group), top_share=len(top & group) / len(top) if top else None,
            unseen_top_hits=new_hits,
            unseen_top_recall=new_hits / len(available) if available else None,
            eligible_unseen_top_recall=new_hits / len(allowed) if allowed else None)
    return result


def reference_rows(document):
    """Recognize the frozen development replay or the full depth study."""
    if "rows" in document:
        return {(r["id"], r["phase"], r["current_responses"]): r for r in document["rows"]}, "depth"
    return {(r["id"], r["phase"]): r for r in document["runs"]}, "development"


def study_brands(document, ids, serving_brands):
    """Depth fixtures define a full canonical map; legacy audits may list a subset.

    Legacy development replay used serving brands. Preserve that protocol when
    reproducing it instead of interpreting a partial fixture audit as a new
    catalogue-wide normalization rule.
    """
    if any("history_n" in fixture for fixture in document["runs"]):
        return evaluation_brands(document, ids, serving_brands)
    return dict(zip(ids, serving_brands))


def context_origin_mean(mean, intercept, origin):
    """Choose the zero for eligibility, without changing any model prediction.

    The learned common intercept describes overall response propensity. Relative
    preference is Xw = mean - intercept. Subtracting this common scalar preserves
    every candidate ordering and shifts the quantile by the same amount; only
    the existing greater-than-zero qualification acquires a relative origin.
    """
    mean = np.asarray(mean, dtype=float)
    if not np.isfinite(mean).all() or not np.isfinite(intercept):
        raise ValueError("Context scores and intercept must be finite")
    if origin == "absolute":
        return mean.copy()
    if origin == "intercept":
        return mean - float(intercept)
    raise ValueError("Unknown context score origin")


def run(args):
    started = time.perf_counter()
    documents = [json.loads(p.read_text()) for p in args.fixtures]
    food, salad = json.loads(args.food_audit.read_text()), json.loads(args.salad_audit.read_text())
    reference = json.loads(args.reference_results.read_text()) if args.reference_results else None
    refs, ref_kind = reference_rows(reference) if reference else ({}, None)
    files = [*args.fixtures, args.food_audit, args.salad_audit,
             *[Path(__file__).with_name(n) for n in ("model.py", "regularized.py", "context.py",
                                                   "context_bench.py", "depth_bench.py", "metrics.py", "bench.py")]]
    if args.reference_results:
        files.append(args.reference_results)
    frozen = {str(p): digest(p) for p in files}
    profiles = SpectralProfiles.from_serving(args.serving.resolve())
    data_dir = args.serving.resolve().parent
    for document in documents:
        source_checks(document, data_dir, profiles)
        validate_scenarios(document, profiles.ids)
    audit_source_checks((food, salad), data_dir)
    rows = []
    with offline_runtime(data_dir, args.serving.resolve()) as (art, decision, taste):
        from recsys import content, diversity, store
        if art.ids != list(profiles.ids):
            raise ValueError("Selector and latent catalogue orders differ")
        code_paths = [Path(m.__file__) for m in (content, decision, diversity, store, taste)]
        code_hashes = {str(p): digest(p) for p in code_paths}
        names = {s: (store.get_store(s) or {}).get("name", s) for s in art.ids}
        z = np.zeros((len(content.IDX), len(art.ids)))
        for document in documents:
            brands = study_brands(document, art.ids, art.brand)
            for fixture in document["runs"]:
                model, current_model = RegularizedTemporalTasteModel(profiles), RegularizedTemporalTasteModel(profiles)
                for sid, like in fixture["past"]:
                    model.record(sid, like)
                current, current_events = {}, []
                past = dict(fixture["past"])
                for phase in fixture["phases"]:
                    for sid, like in phase["events"]:
                        model.record(sid, like)
                        current_model.record(sid, like)
                        current[sid] = like
                        current_events.append((sid, like))
                    if not current:
                        raise ValueError("Endpoint replay requires current evidence; cold policy belongs to the existing API")
                    assert model.clock - current_model.clock == len(fixture["past"])
                    assert {s: v["like"] for s, v in current_model.evidence.items()} == current
                    # recommend() consumes these labels for its reference pool
                    # and evidence flag. Its score arrays are completely replaced
                    # below; no eight-dimensional learning or cold path is used.
                    state = {"like_ids": [s for s, v in current.items() if v],
                             "dislike_ids": [s for s, v in current.items() if not v],
                             "n": len(current), "m": np.zeros(len(content.IDX))}
                    full, today = model.predict(), current_model.predict()
                    current_intercept = current_model._solve().intercept
                    eligibility_mean = context_origin_mean(today.mean, current_intercept, args.context_origin)
                    context_key = ("regularized_context_centered" if args.context_origin == "intercept"
                                   else "regularized_context")
                    mean, sd = full.mean, full.latent_std
                    with patch.object(decision, "_components", return_value=z), patch.object(
                            decision, "_scores", return_value=(mean, mean, sd)):
                        full_slate = decision.recommend(state)
                        selected = select_context(
                            art.ids, eligibility_mean, today.latent_std, set(current),
                            config=decision.DEFAULT, full_slate=full_slate,
                            select_allowed=lambda allowed: decision.recommend(state, allow=allowed),
                            is_diverse=lambda a, b: decision._diverse(art, art.pos[a], art.pos[b], decision.DEFAULT))
                    ref = None
                    if ref_kind == "depth":
                        ref = refs[fixture["id"], phase["name"], len(current_events)]
                        assert full_slate["store_ids"] == [c["store_id"] for c in ref["models"]["regularized"]["cards"]]
                        assert full_slate["qualified_count"] == ref["models"]["regularized"]["qualified_count"]
                    elif ref_kind == "development":
                        ref = refs[fixture["id"], phase["name"]]
                        assert full_slate["store_ids"] == ref["variants"]["full"]["decision"]["store_ids"]
                        if args.context_origin == "absolute":
                            assert selected.slate["store_ids"] == ref["variants"]["current_context"]["decision"]["store_ids"]
                            old = ref["context_eligibility"]
                            assert len(selected.qualified_ids) == old["qualified_count"]
                            assert selected.cutoff == old["cutoff"]
                            assert selected.used_context == old["used_context"]
                            old_reason = {"fewer_than_four_current_qualified": "fewer_than_requested_current_qualified",
                                          "context_selector_did_not_return_strictly_diverse_four": "context_selector_did_not_return_strictly_diverse_slate"}.get(
                                              old["fallback_reason"], old["fallback_reason"])
                            assert selected.fallback_reason == old_reason
                    wanted = "ramen_korean_hot_noodle" if phase["wanted"] == "noodle" else phase["wanted"]
                    audit, groups = audit_groups(food, salad, wanted)
                    score = mean - decision.DEFAULT.uncertainty_weight * sd
                    _, ranked = full_order(art.ids, score)
                    ranks = {s: i + 1 for i, s in enumerate(ranked)}
                    seen = set(past) | set(current)
                    latest = {s: {"like": like, "source": "history" if i <= len(fixture["past"]) else "current",
                                  "age": model.clock - i}
                              for i, (s, like) in enumerate(fixture["past"] + current_events, 1)}
                    row = {k: fixture[k] for k in ("id", "scenario", "seed", "prior")}
                    row.update(history_n=len(fixture["past"]), phase=phase["name"], wanted=wanted,
                               intent=fixture.get("intent", "seek"), current_responses=len(current_events),
                               phase_responses=len(phase["events"]), current_distinct_stores=len(current),
                               models={}, context={"origin": args.context_origin, "learned_current_intercept": current_intercept,
                                                   "qualified_count": len(selected.qualified_ids), "cutoff": selected.cutoff,
                                                   "used_context": selected.used_context, "fallback_reason": selected.fallback_reason})
                    for name, slate in (("regularized", full_slate), (context_key, selected.slate)):
                        allowed = (set(selected.qualified_ids) if name == context_key and selected.used_context else set(art.ids))
                        served_rank = [s for s in ranked if s in allowed]
                        chosen = slate["store_ids"]
                        counts = filtered_counts(art.ids, ranked, served_rank, chosen, groups, seen, brands)
                        item = {"counts": counts, "qualified_count": slate["qualified_count"], "cards": [], "top20": [],
                                "card_food_status_counts": dict(Counter(audit[s]["food"] for s in chosen)),
                                "heldout_groups": {k: {"total": len(g), "current_qualified": sum(s in selected.qualified_ids for s in g),
                                                       "actual_allowed": sum(s in allowed for s in g)} for k, g in phase["groups"].items()}}
                        ia, ib = [[art.pos[s] for s in g] for g in phase["groups"].values()]
                        ea, eb = [[art.pos[s] for s in g if s in allowed] for g in phase["groups"].values()]
                        item.update(reference_mean_pair_win=pairwise_win_rate(mean, ia, ib),
                                    reference_conservative_pair_win=pairwise_win_rate(score, ia, ib),
                                    eligible_reference_mean_pair_win=pairwise_win_rate(mean, ea, eb),
                                    eligible_reference_conservative_pair_win=pairwise_win_rate(score, ea, eb))
                        for key, source, label in (("current_liked_cards", "current", True), ("current_disliked_cards", "current", False),
                                                   ("history_liked_cards", "history", True), ("history_disliked_cards", "history", False)):
                            item[key] = sum(s in latest and latest[s]["source"] == source and latest[s]["like"] == label for s in chosen)
                        known_brands = {brands[s] for s in seen if brands[s]}
                        item["unseen_cards"] = sum(s not in seen and (not brands[s] or brands[s] not in known_brands) for s in chosen)
                        for key, stores in (("cards", chosen), ("top20", served_rank[:20])):
                            for sid in stores:
                                i = art.pos[sid]
                                item[key].append({"store_id": sid, "name": names[sid], "rank": ranks[sid],
                                                  "mean": mean[i], "sd": sd[i], "rank_score": score[i],
                                                  "food_status": audit[sid]["food"], "style": audit[sid]["style"],
                                                  "already_observed": sid in seen, "latest_feedback": latest.get(sid)})
                        pairs = [(a, b) for i, a in enumerate(chosen) for b in chosen[i + 1:]]
                        values = [diversity.pair_redundancy(art, art.pos[a], art.pos[b]) for a, b in pairs]
                        known = [v for v in values if v is not None]
                        item["diversity"] = {"pair_redundancy_percentiles": values, "known_pair_count": len(known),
                                             "unknown_pair_count": len(values) - len(known),
                                             "strict_conflicts": sum(not decision._diverse(art, art.pos[a], art.pos[b], decision.DEFAULT) for a, b in pairs),
                                             "same_evaluation_brand_pairs": sum(bool(brands[a]) and brands[a] == brands[b] for a, b in pairs),
                                             "max_pair_percentile": max(known) if known else None,
                                             "mean_pair_percentile": float(np.mean(known)) if known else None}
                        if ref_kind == "depth":
                            item["vector"] = ref["models"]["regularized"]["vector"]
                            for card in ref["models"]["regularized"]["cards"]:
                                assert abs(mean[art.pos[card["store_id"]]] - card["mean"]) < 1e-12
                                assert abs(sd[art.pos[card["store_id"]]] - card["sd"]) < 1e-12
                        row["models"][name] = item
                    rows.append(clean(row))
                    if len(rows) % 18 == 0:
                        print(f"measured {len(rows)} endpoints", flush=True)
    for p, expected in {**frozen, **code_hashes}.items():
        if digest(p) != expected:
            raise RuntimeError("Input/source changed during replay: " + p)
    for document in documents:
        source_checks(document, data_dir, profiles)
    audit_source_checks((food, salad), data_dir)
    result = {"research_only": True, "production_changed": False, "state_count": len(rows),
              "catalogue_size": len(profiles.ids), "source_sha256": frozen, "operational_code_sha256": code_hashes,
              "reference_kind": ref_kind, "all_reference_slate_parity_checks_passed": True if reference else None,
              "reference_slate_checks": len(rows) if reference else 0,
              "context_origin": args.context_origin,
              "context_slate_parity_checked": ref_kind == "development" and args.context_origin == "absolute",
              "seconds": time.perf_counter() - started, "rows": rows,
              "definitions": {"context_policy": "Full posterior, within current-only qualification when the existing selector returns strict diverse4; otherwise unchanged full fallback.",
                              "context_origin": "absolute reproduces mean>0; intercept uses Xw=mean-current common intercept>0. Full model mean/std and ordering stay unchanged.",
                              "top20": "Actual policy-eligible ranking, which can contain fewer than20 stores; raw/global rank remains separately recorded.",
                              "conditional_pair_win": "Different retained target populations; interpret with eligible group counts, never as accuracy improvement against unfiltered pair rates.",
                              "vector": "The original full posterior vector is unchanged; this policy changes eligibility only.",
                              "study_scope": "Posthoc policy evaluation if original N outcomes informed the concern; not a new untouched holdout."}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(clean(result), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    print(json.dumps({"states": len(rows), "seconds": result["seconds"], "output": str(args.output)}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, nargs="+", required=True)
    parser.add_argument("--food-audit", type=Path, required=True)
    parser.add_argument("--salad-audit", type=Path, required=True)
    parser.add_argument("--reference-results", type=Path)
    parser.add_argument("--context-origin", choices=("absolute", "intercept"), default="absolute",
                        help="zero for current eligibility only; model predictions are unchanged")
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
