"""Measure history-depth scenarios, actual latent vectors and four-card slates.

Run in an isolated CLI process. This is an offline study, not an API endpoint.
Original catalogues and hypothetical preference vectors stay in the explicitly
chosen output directory; neither live users nor serving state are touched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import patch

import numpy as np

from .bench import digest, offline_runtime, validate_scenarios
from .diagnostics import geometry, latent_state, reference_contrast
from .metrics import pairwise_win_rate, recommendation_counts
from .model import SpectralProfiles, TemporalTasteModel
from .regularized import RegularizedTemporalTasteModel

CONFIRMED = {"principal", "co_main"}


def clean(value):
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def audit_groups(food, salad, wanted):
    if wanted == "salad_poke":
        rows = {sid: {"food": r["salad_poke"]["status"], "style": "not_applicable"}
                for sid, r in salad["annotations"].items()}
        groups = {"current_food": [s for s, r in rows.items() if r["food"] in CONFIRMED],
                  "previous_fried_food": sorted(
                      {s for s, r in salad["annotations"].items() if r["fried_chicken"]["status"] in CONFIRMED}
                      | {s for s, r in food["masks"]["cutlet"].items() if r["food"] in CONFIRMED})}
    else:
        rows = food["masks"][wanted]
        groups = {"current_food": [s for s, r in rows.items() if r["food"] in CONFIRMED]}
        for style in ("japanese", "western" if wanted == "cutlet" else "korean", "both_or_mixed", "not_established"):
            groups[style] = [s for s, r in rows.items() if r["food"] in CONFIRMED and r["style"] == style]
    for status in sorted({r["food"] for r in rows.values()}):
        groups["food_status_" + status] = [s for s, r in rows.items() if r["food"] == status]
    return rows, groups


def full_order(ids, values):
    values = np.nan_to_num(values, nan=-np.inf, neginf=-np.inf, posinf=-np.inf)
    order = sorted(range(len(ids)), key=lambda i: (-values[i], ids[i]))
    return order, [ids[i] for i in order]


def source_checks(document, data_dir, profiles):
    if document.get("catalog_sources") != profiles.sources:
        raise ValueError("Serving matrices differ from frozen scenario inputs")
    if document.get("pool_ids_sha256") != digest(data_dir / "serving/pool_ids.json"):
        raise ValueError("Serving ID order differs from frozen inputs")
    for name, expected in document.get("label_source_sha256", {}).items():
        if digest(data_dir / name) != expected:
            raise ValueError(f"Label source changed: {name}")
    for name, expected in profiles.sources.items():
        if digest(data_dir / f"serving/sim_{name}.npy") != expected:
            raise ValueError(f"Serving matrix changed: {name}")


def audit_source_checks(audits, data_dir):
    """Verify original catalogue inputs, even when an audit recorded absolute paths."""
    expected_names = {"stores.jsonl", "menus.jsonl", "reviews.jsonl", "reviews_naver.jsonl",
                      "pool_ids.json", "brand_key.json"}
    for audit in audits:
        for name, expected in audit["source_sha256"].items():
            path = Path(name)
            if path.name not in expected_names:
                continue  # Audit build scripts are provenance, not current catalogue inputs.
            target = data_dir / ("serving" if path.name in {"pool_ids.json", "brand_key.json"} else "") / path.name
            if digest(target) != expected:
                raise ValueError(f"Audited catalogue source changed: {name}")


def evaluation_brands(document, ids, serving_brands):
    aliases = document.get("brand_aliases", {})
    actual = {s: aliases.get(b, b) for s, b in zip(ids, serving_brands)}
    if actual != document["observed_brand_keys"]:
        raise ValueError("Frozen evaluation brand mapping differs from serving brands plus declared aliases")
    return actual


def validate_depth_scenarios(document, ids):
    validate_scenarios(document, ids)
    for run in document["runs"]:
        if len(run["past"]) != run["history_n"] or len({s for s, _ in run["past"]}) != run["history_n"]:
            raise ValueError("History N must count distinct observed stores")
        for phase in run["phases"]:
            if any(s in {p for p, _ in run["past"]} for s, _ in phase["events"]):
                raise ValueError("Study current responses overlap the frozen history")


def run_study(args):
    started = time.perf_counter()
    document = json.loads(args.fixtures.read_text())
    food = json.loads(args.food_audit.read_text())
    salad = json.loads(args.salad_audit.read_text())
    files = [args.fixtures, args.food_audit, args.salad_audit,
             *[Path(__file__).with_name(n) for n in ("model.py", "regularized.py", "diagnostics.py", "metrics.py", "depth_bench.py", "bench.py")]]
    hashes = {str(p): digest(p) for p in files}
    serving = args.serving.resolve()
    data_dir = serving.parent
    profiles = SpectralProfiles.from_serving(serving)
    source_checks(document, data_dir, profiles)
    audit_source_checks((food, salad), data_dir)
    validate_depth_scenarios(document, profiles.ids)
    if set(food["catalog_ids"]) != set(profiles.ids) or set(salad["annotations"]) != set(profiles.ids):
        raise ValueError("Audited and scored catalogues differ")
    outputs, full_vectors, references = [], {}, {}
    scalar_comparisons = []
    args.output.mkdir(parents=True, exist_ok=True)
    with offline_runtime(data_dir, serving) as (art, decision, taste):
        from recsys import content, diversity, store, swipe
        if art.ids != list(profiles.ids):
            raise ValueError("Selector and latent coordinate ID order differs")
        brands = evaluation_brands(document, art.ids, art.brand)
        names = {s: (store.get_store(s) or {}).get("name", s) for s in art.ids}
        code_paths = [Path(m.__file__) for m in (content, decision, diversity, store, swipe, taste)]
        code_hashes = {p.name: digest(p) for p in code_paths}
        for ri, run in enumerate(document["runs"]):
            models = {"temporal_v1": TemporalTasteModel(profiles),
                      "regularized": RegularizedTemporalTasteModel(profiles)}
            for m in models.values():
                for sid, like in run["past"]:
                    m.record(sid, like)
                m.start_session()
            baseline = taste.start_from(taste.replay(run["past"]) if run["past"] else None)
            current_events = []
            previous_vectors = {}
            for pi, phase in enumerate(run["phases"]):
                audit, groups = audit_groups(food, salad, phase["wanted"])
                positions = sorted({len(phase["events"]), *[k for k in (0, 4, 8, 12, 20) if k <= len(phase["events"])]})
                # A boundary belongs to the preceding phase except at the very
                # beginning; do not count the same unchanged state twice.
                if pi:
                    positions.remove(0)
                done = 0
                for checkpoint in positions:
                    for sid, like in phase["events"][done:checkpoint]:
                        taste.record(baseline, sid, like)
                        for m in models.values():
                            m.record(sid, like)
                        current_events.append([sid, like])
                    done = checkpoint
                    seen = {s for s, _ in run["past"] + current_events}
                    latest = {s: {"like": like, "age": len(run["past"]) + len(current_events) - i,
                                  "source": "history" if i <= run["history_n"] else "current"}
                              for i, (s, like) in enumerate(run["past"] + current_events, 1)}
                    z = decision._components(art, baseline, loo=True)
                    _, base_mean, base_sd = decision._scores(art, baseline, z=z)
                    base_slate = decision.recommend(baseline)
                    evidence = bool(seen)
                    cold_order = taste._cold(z, baseline["m"], False)
                    predictions = {"operational": (base_mean, base_sd, base_slate, None)}
                    states = {}
                    for model_name, m in models.items():
                        # Self deletion tests generalization. Actual v2 serving
                        # retains explicit feedback; unseen scores are identical.
                        prediction = m.predict(leave_store_out=model_name == "temporal_v1")
                        with patch.object(decision, "_components", return_value=z), patch.object(
                                decision, "_scores", return_value=(prediction.mean, prediction.mean, prediction.latent_std)):
                            slate = decision.recommend(baseline)
                        predictions[model_name] = (prediction.mean, prediction.latent_std, slate, prediction.identified)
                        states[model_name] = latent_state(m, profiles, history_end_clock=run["history_n"])
                        if model_name == "regularized":
                            loo = m.predict(leave_store_out=True)
                            unseen_indices = [i for i, s in enumerate(art.ids) if s not in seen]
                            if not (np.array_equal(loo.mean[unseen_indices], prediction.mean[unseen_indices])
                                    and np.array_equal(loo.latent_std[unseen_indices], prediction.latent_std[unseen_indices])):
                                raise AssertionError("Self deletion changed an unseen candidate")
                            with patch.object(decision, "_components", return_value=z), patch.object(
                                    decision, "_scores", return_value=(loo.mean, loo.mean, loo.latent_std)):
                                loo_slate = decision.recommend(baseline)
                            predictions["regularized_loo"] = (loo.mean, loo.latent_std, loo_slate, loo.identified)
                            states["regularized_loo"] = states[model_name]
                    row_index = len(outputs)
                    row = {"row": row_index, "id": run["id"], "scenario": run["scenario"],
                           "seed": run["seed"], "prior": run["prior"], "history_n": run["history_n"],
                           "phase": phase["name"], "wanted": phase["wanted"],
                           "intent": run.get("intent", "seek"),
                           "current_responses": len(current_events), "phase_responses": checkpoint,
                           "current_distinct_stores": len({s for s, _ in current_events}),
                           "current_like_events": sum(like for _, like in current_events),
                           "current_active_likes": sum(row["like"] for row in latest.values() if row["source"] == "current"),
                           "reference_groups": phase["groups"], "models": {}}
                    current_signature = hashlib.sha256(json.dumps(current_events).encode()).hexdigest()
                    for model_name, (mean, sd, slate, identified) in predictions.items():
                        actual_rank_values = mean - decision.DEFAULT.uncertainty_weight * sd if evidence else cold_order
                        order, ranked_ids = full_order(art.ids, actual_rank_values)
                        rank = {s: i + 1 for i, s in enumerate(ranked_ids)}
                        selected = slate["store_ids"]
                        if len(selected) != 4:
                            raise AssertionError("Existing selector failed to return four cards")
                        counts = recommendation_counts(art.ids, ranked_ids, selected, groups,
                                                       observed=list(seen), brands=brands)
                        reference = list(phase["groups"].values())
                        ia, ib = [[art.pos[s] for s in group] for group in reference]
                        item = {"ranking_source": "model" if evidence else "operational_cold_start",
                                "qualified_count": slate["qualified_count"], "counts": counts,
                                "reference_mean_pair_win": pairwise_win_rate(mean, ia, ib),
                                "reference_conservative_pair_win": pairwise_win_rate(actual_rank_values, ia, ib),
                                "reference_mean_rank": {k: float(np.mean([rank[s] for s in group])) for k, group in phase["groups"].items()},
                                "top20": [], "cards": [], "card_food_status_counts": dict(Counter(audit[s]["food"] for s in selected)),
                                "current_disliked_cards": sum(s in latest and latest[s]["source"] == "current" and not latest[s]["like"] for s in selected),
                                "current_liked_cards": sum(s in latest and latest[s]["source"] == "current" and latest[s]["like"] for s in selected),
                                "history_disliked_cards": sum(s in latest and latest[s]["source"] == "history" and not latest[s]["like"] for s in selected),
                                "history_liked_cards": sum(s in latest and latest[s]["source"] == "history" and latest[s]["like"] for s in selected),
                                "unseen_cards": sum(s not in seen and (not brands[s] or brands[s] not in {brands[x] for x in seen}) for s in selected)}
                        pair_values = [diversity.pair_redundancy(art, art.pos[a], art.pos[b])
                                       for i, a in enumerate(selected) for b in selected[i + 1:]]
                        known_pairs = [v for v in pair_values if v is not None]
                        item["diversity"] = {"pair_redundancy_percentiles": pair_values,
                                             "known_pair_count": len(known_pairs),
                                             "unknown_pair_count": len(pair_values) - len(known_pairs),
                                             "strict_conflicts": sum(not decision._diverse(art, art.pos[a], art.pos[b], decision.DEFAULT)
                                                                     for i, a in enumerate(selected) for b in selected[i + 1:]),
                                             "same_evaluation_brand_pairs": sum(bool(brands[a]) and brands[a] == brands[b]
                                                                                for i, a in enumerate(selected) for b in selected[i + 1:]),
                                             "max_pair_percentile": max(known_pairs) if known_pairs else None,
                                             "mean_pair_percentile": float(np.mean(known_pairs)) if known_pairs else None}
                        for key, store_ids in (("top20", ranked_ids[:20]), ("cards", selected)):
                            for sid in store_ids:
                                i = art.pos[sid]
                                item[key].append({"store_id": sid, "name": names[sid], "rank": rank[sid],
                                                  "mean": mean[i], "sd": sd[i], "rank_score": actual_rank_values[i],
                                                  "food_status": audit[sid]["food"], "style": audit[sid]["style"],
                                                  "already_observed": sid in seen, "latest_feedback": latest.get(sid)})
                        if model_name == "operational":
                            item["component_weights"] = baseline["m"].tolist()
                            item["component_variances"] = baseline["v"].tolist()
                        else:
                            state = states[model_name]
                            vector = state.total
                            report = {"dimensions": len(vector), "intercept": state.intercept,
                                      "observations": state.observations, "history_observations": state.history_observations,
                                      "reconstruction_max_error": state.reconstruction_max_error,
                                      "norms": {k: float(np.linalg.norm(getattr(state, k))) for k in ("total", "slow", "fast", "history", "current")},
                                      "history_current_geometry": geometry(state.history, state.current),
                                      "versus_previous_checkpoint": geometry(vector, previous_vectors[model_name]) if model_name in previous_vectors else None,
                                      "reference": reference_contrast(state, profiles, phase["groups"])}
                            previous_vectors[model_name] = vector.copy()
                            control_key = (model_name, current_signature)
                            if not run["history_n"]:
                                references[control_key] = vector.copy()
                            scalar_comparisons.append((row_index, model_name, control_key, vector.copy()))
                            vector_model = "regularized" if model_name == "regularized_loo" else model_name
                            report["vector_key_prefix"] = f"row{row_index}_{vector_model}_"
                            if model_name != "regularized_loo":
                                full_vectors[report["vector_key_prefix"] + "total"] = vector
                                if pi == len(run["phases"]) - 1 and checkpoint == len(phase["events"]):
                                    for part in ("slow", "fast", "history", "current"):
                                        full_vectors[report["vector_key_prefix"] + part] = getattr(state, part)
                            item["vector"] = report
                        row["models"][model_name] = item
                    outputs.append(clean(row))
            if (ri + 1) % 6 == 0:
                print(f"measured {ri + 1}/{len(document['runs'])} trajectories, {len(outputs)} checkpoints", flush=True)
    for row_index, model_name, control_key, vector in scalar_comparisons:
        control = references.get(control_key)
        outputs[row_index]["models"][model_name]["vector"]["versus_no_history"] = geometry(vector, control) if control is not None else None
    if any(digest(Path(p)) != expected for p, expected in hashes.items()):
        raise RuntimeError("Study inputs/code changed during measurement")
    if any(digest(p) != code_hashes[p.name] for p in code_paths):
        raise RuntimeError("Operational scoring code changed during measurement")
    source_checks(document, data_dir, profiles)
    audit_source_checks((food, salad), data_dir)
    result = {
        "research_only": True, "production_changed": False,
        "catalogue_size": len(profiles.ids), "dimensions": profiles.x.shape[1],
        "trajectory_count": len(document["runs"]), "checkpoint_count": len(outputs),
        "source_sha256": hashes, "operational_code_sha256": code_hashes,
        "model_parameters": asdict(models["regularized"].parameters),
        "feature_diagnostics": profiles.diagnostics,
        "score_policy": {"operational": "Existing feature-LOO serving",
                         "temporal_v1": "Free-intercept v1, candidate self-LOO serving",
                         "regularized_loo": "Proper-intercept v2, self-LOO ablation",
                         "regularized": "Proper-intercept v2, full posterior serving; unseen scores equal self-LOO"},
        "seconds": time.perf_counter() - started, "rows": outputs,
        "definitions": {
            "history_n": "Distinct earlier restaurant responses; same latest history suffix and identical current responses in paired cases.",
            "card_share": "Number of audited matching restaurants in the four actual cards / 4. Not a satisfaction probability.",
            "unseen_top_recall": "Matching top20 restaurants never observed and not sharing an observed brand / all matching available unseen restaurants.",
            "vector": "Actual 4317-dimensional common posterior mean coefficients, excluding the scalar intercept. It reconstructs full posterior and unseen predictions; self-LOO variants use a different posterior for each rated candidate.",
            "vector_reference": "Exact mean-score difference between fixed audited reference restaurants; not a semantic name for an individual latent dimension.",
            "history_component": "Partition of jointly fitted posterior coefficients, not a causal share. Use the paired no-history difference for history effect.",
            "rates": "Synthetic scenario proportions and finite catalogue coverage; neither calibrated probabilities nor real-user accuracy.",
        },
    }
    (args.output / "results.json").write_text(json.dumps(clean(result), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    np.savez_compressed(args.output / "preference-vectors.npz", **full_vectors)
    print(json.dumps({k: result[k] for k in ("trajectory_count", "checkpoint_count", "seconds")}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--food-audit", type=Path, required=True)
    parser.add_argument("--salad-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    run_study(args)


if __name__ == "__main__":
    main()
