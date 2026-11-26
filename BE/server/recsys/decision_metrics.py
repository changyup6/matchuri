"""Versioned, read-only decision measurements; diagnostic values are not calibrated probabilities.

Measure even when the production decision gate returns early. Expose that distinction.
No category labels replace vector-derived similarities, and no raw vector files are exported.
"""
from dataclasses import asdict
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np

from . import content, decision, decision_meanings, diversity, taste

MODALITIES = ("menu", "text", "review", "food")


def number(value):
    return float(value) if value is not None and np.isfinite(value) else None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def provenance(art):
    manifest = Path(art.path) / "manifest.json" if art and art.path else None
    raw = manifest.read_bytes() if manifest and manifest.is_file() else None
    data = json.loads(raw) if raw else {}
    here = Path(__file__).parent
    return {"manifest_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
            "vectors": data.get("vectors_manifest", {}),
            "built_at": data.get("built_at"),
            "code_sha256": {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                            for name in ("decision.py", "decision_metrics.py", "decision_meanings.py", "diversity.py", "content.py", "taste.py")}}


def pair_metrics(art, a, b, config):
    values = {k: number(getattr(art, "sim_" + k)[a, b]) for k in MODALITIES}
    known = [v for v in values.values() if v is not None]
    groups = decision._fine_groups(art)
    brands = [art.brand[a], art.brand[b]]
    cats = [groups[art.ids[a]], groups[art.ids[b]]]
    same_brand = brands[0] == brands[1] if all(brands) else None
    same_cat = cats[0] == cats[1] if all(cats) else None
    mean = float(np.mean(known)) if known else None
    reasons = []
    if same_brand:
        reasons.append("same_brand")
    if values["menu"] is not None and values["menu"] >= config.max_menu_similarity:
        reasons.append("menu_similarity")
    if len(known) < 2:
        reasons.append("insufficient_modalities")
    if mean is not None and mean >= config.max_mean_similarity:
        reasons.append("mean_similarity")
    return {"store_ids": [art.ids[a], art.ids[b]], "similarities": values,
            "relative_similarities": diversity.pair_values(art, a, b),
            "redundancy_percentile": diversity.pair_redundancy(art, a, b),
            "co_visit_similarity": number(art.cooc[a, b]),
            "known_modalities": len(known), "missing_modalities": [k for k, v in values.items() if v is None],
            "raw_mean_similarity": mean, "same_brand": same_brand, "same_fine_category": same_cat,
            "passes_current_rule": bool(decision._diverse(art, a, b, config)), "blocking_reasons": reasons}


def personalization(art, sess, config, *, personal=None):
    """The same evidence diagnostics for full export and per-feedback display state.

    No candidate filtering, pair search or leave-one-out candidate scoring is needed
    when only the unrated ranking stability is requested.
    """
    liked, disliked, rated = taste._idx(sess, art)
    answered = set(sess["like_ids"] + sess["dislike_ids"])
    probe = [i for i, sid in enumerate(art.ids) if sid not in answered]
    validation = [r for r in sess.get("hist", []) if r.get("personal_rank") is not None][-30:]
    positive = [r["personal_rank"] for r in validation if r["like"]]
    negative = [r["personal_rank"] for r in validation if not r["like"]]
    delta = np.subtract.outer(positive, negative)
    auc = float(np.mean((delta > 0) + .5*(delta == 0))) if delta.size else None
    checkpoints = sess.get("decision_checkpoints", [])
    overlaps = []
    if len(checkpoints) >= 4 and len(probe) >= config.stable_top:
        def top(values):
            return set(sorted(probe, key=lambda i: (-values[i], art.ids[i]))[:config.stable_top])
        if personal is None:
            # Only unrated probes are compared, so leave-one-out is unnecessary.
            personal = decision._scores(art, sess)[1]
        current = top(personal)
        for offset in (-2, -4):
            old = decision._scores(art, checkpoints[offset])[1]
            overlaps.append({"feedback_lag": -offset-1,
                             "overlap": len(current & top(old))/len(current)})
    stability = min(r["overlap"] for r in overlaps) if overlaps else None
    return {
        "rated_count": len(rated), "liked_count": len(liked), "disliked_count": len(disliked),
        "positive_predictions": len(positive), "negative_predictions": len(negative),
        "validation_auc": auc, "stability": stability, "checkpoint_overlaps": overlaps,
        "validation_window": [{"feedback_index": r.get("n"), "store_id": r.get("store_id"),
                               "like": r["like"], "predicted_rank": r["personal_rank"]} for r in validation],
        "probe_count": len(probe), "checkpoint_count": len(checkpoints)}


def export(sess, *, allow=None, tiers=None, bonus=None, context=None,
           candidate_offset=0, candidate_limit=60, pair_store_ids=None,
           config=decision.DEFAULT):
    """Export from a detached policy.taste_snapshot. Pagination is tied to snapshot_id.

    Explicit pair IDs must be eligible. Default pairs use selected four, or top eight
    diagnostic candidates if no proposal. Pair scope is independent of candidate pages.
    """
    art = content.load()
    base_config = config
    config, quality_required = decision.requirements(sess, base_config)
    context = context or {}
    prov = provenance(art)
    def state_key(s):
        return {"m": np.asarray(s["m"]).tolist(), "v": np.asarray(s["v"]).tolist(),
                "likes": s["like_ids"], "dislikes": s["dislike_ids"],
                "n": s.get("n", 0), "evidence_at": s.get("evidence_at", {}), "memory": s.get("memory", [])}
    key = {**state_key(sess), "n": sess["n"], "history": sess.get("hist", [])[-30:],
           "checkpoints": [state_key(s) for s in sess.get("decision_checkpoints", [])],
           "allow": sorted(allow) if allow is not None else None, "tiers": tiers or {},
           "bonus": bonus or {}, "context": context, "config": asdict(base_config), "provenance": prov}
    if sess.get('declined_slates'):
        key['declined_slates'] = sess['declined_slates']
    out = {"schema_version": 1, "snapshot_id": digest(key), "feedback_count": sess["n"],
           "context": context, "provenance": prov, "config": {**asdict(config),
               "relaxation_progress": decision.relaxation(sess, base_config)},
           "interpretation": {
               "personalization": "prequential rank AUC and rank stability are diagnostics, not confidence",
               "fit": "relative z-score contribution minus model SD penalty; not acceptance probability",
               "uncertainty": "diagonal model approximation; not calibrated; correlated feedback/features omitted",
               "similarity": "vector-derived raw modality scores; scales differ; null means unknown",
               "candidate_history": "self feedback excluded only from feature aggregation, not trained weights",
               "selection": (f"feasible {config.count} inside search_limit, then bounded exchanges reduce "
                             "descending pair redundancy percentiles; keep feasible anchor and tier composition; "
                             "not global optimality or calibrated choice satisfaction"),
               "scope": "decision/gates/slate assess automatic suggestions; explicit manual decisions are independent"},
           "gates": [], "model": [], "personalization": None, "fit": None,
           "candidates": [], "candidate_total": 0, "candidate_offset": candidate_offset,
           "candidate_limit": candidate_limit, "next_offset": None,
           "pairs": [], "pair_store_ids": [], "pair_scope": "unavailable", "slate": None}
    result = decision.evaluate(sess, allow=allow, tiers=tiers, bonus=bonus, config=base_config)
    if sess.get('declined_slates'):
        out['interpretation']['selection'] += '; excludes explicitly continued slates in this scope'
    if context.get("location_required"):
        result = {**result, "ready": False, "status": "location_required", "store_ids": []}
    out["decision"] = result
    if art is None:
        out["gates"] = [{"name": "artifacts_available", "value": False, "operator": "==",
                         "threshold": True, "passed": False}]
        return decision_meanings.attach(out)

    liked, disliked, rated = taste._idx(sess, art)
    eligible = [i for i, sid in enumerate(art.ids) if allow is None or sid in allow]
    eligible_ids = {art.ids[i] for i in eligible}
    if pair_store_ids and not set(pair_store_ids) <= eligible_ids:
        raise ValueError("pair_store_ids must belong to the current eligible pool")
    probe = [i for i, sid in enumerate(art.ids) if sid not in set(sess["like_ids"] + sess["dislike_ids"])]
    z = decision._components(art, sess, loo=True)
    scores, personal, sd = decision._scores(art, sess, loo=True, z=z)
    conservative = personal - config.uncertainty_weight * sd
    reference = probe or ([] if quality_required else list(range(len(art.ids))))
    threshold = number(np.quantile(conservative[reference], config.fit_quantile)) if reference else None
    qualified = [i for i in eligible if threshold is not None and np.isfinite(conservative[i])
                 and conservative[i] >= threshold and personal[i] > 0]
    qualified_set = set(qualified)
    def order(i):
        return (-(tiers or {}).get(art.ids[i], 0),
                -(conservative[i] + (bonus or {}).get(art.ids[i], 0)), art.ids[i])
    ranked = sorted(qualified, key=order)
    # Show failed candidates too, after qualified candidates, in the same deterministic order.
    all_ranked = ranked + sorted([i for i in eligible if i not in qualified_set], key=order)
    chosen = decision._select(art, ranked, config, sess.get('declined_slates', ()), tiers=tiers)
    p = out["personalization"] = personalization(art, sess, config, personal=personal)
    def gate(name, value, operator, target, *, required=True, baseline=None):
        passed = None if value is None else (value >= target if operator == ">=" else value == target)
        out["gates"].append({"name": name, "value": value, "operator": operator,
                             "threshold": target, "passed": passed,
                             "required": required, "baseline_threshold": target if baseline is None else baseline})
    gate("location_available_if_required", not context.get("location_required", False), "==", True)
    gate("rated_count", len(set(sess["like_ids"] + sess["dislike_ids"])), ">=", config.min_rated)
    gate("liked_count", len(set(sess["like_ids"])), ">=", config.min_likes)
    gate("positive_predictions", p["positive_predictions"], ">=", config.min_validation_each, required=quality_required, baseline=base_config.min_validation_each)
    gate("negative_predictions", p["negative_predictions"], ">=", config.min_validation_each, required=quality_required, baseline=base_config.min_validation_each)
    gate("validation_auc", p["validation_auc"], ">=", config.min_validation_auc, required=quality_required, baseline=base_config.min_validation_auc)
    gate("checkpoint_count", p["checkpoint_count"], ">=", 4, required=quality_required)
    gate("eligible_count", len(eligible), ">=", config.count)
    gate("probe_count", len(probe), ">=", config.stable_top, required=quality_required)
    gate("stability", p["stability"], ">=", config.min_overlap, required=quality_required, baseline=base_config.min_overlap)
    gate("diverse_fit_count", len(chosen), "==", config.count)
    out["fit"] = {"eligible_count": len(eligible), "qualified_count": len(qualified),
                  "reference_pool": "currently_unrated_catalog" if probe else "catalog_when_unrated_empty", "reference_count": len(reference),
                  "threshold": threshold, "quantile": config.fit_quantile,
                  "score_quantiles": {str(q): number(np.quantile(conservative[reference], q)) if reference else None
                                      for q in (0, .25, .5, .75, .9, 1)},
                  "searched_count": min(len(ranked), config.search_limit),
                  "search_truncated": len(ranked) > config.search_limit}
    out["model"] = [{"component": c, "weight": number(sess["m"][j]),
                     "variance": number(sess["v"][j]), "sd": number(np.sqrt(sess["v"][j])),
                     "used_for_personal_fit": j in decision.PERSONAL}
                    for j, c in enumerate(content.COMPONENTS)]
    def evidence(i, kind):
        sim = getattr(art, "sim_" + kind)
        def refs(indices):
            rows = [(art.ids[j], number(sim[j, i])) for j in indices if j != i]
            known = [(s, v) for s, v in rows if v is not None]
            nearest = max(known, key=lambda r: r[1]) if known else None
            return {"known_count": len(known), "total_count": len(rows),
                    "nearest_store_id": nearest[0] if nearest else None,
                    "nearest_similarity": nearest[1] if nearest else None}
        return {"candidate_has_feature": bool(art.has[kind][i]),
                "like_references": refs(liked), "dislike_references": refs(disliked)}
    page = all_ranked[candidate_offset:candidate_offset+candidate_limit]
    selected = set(result["store_ids"])
    for rank, i in enumerate(page, candidate_offset+1):
        sid = art.ids[i]
        out["candidates"].append({
            "store_id": sid, "rank": rank, "qualified": i in qualified_set,
            "selected": sid in selected, "total_score": number(scores[i]),
            "personal_score": number(personal[i]), "model_sd": number(sd[i]),
            "conservative_score": number(conservative[i]),
            "fit_margin": number(conservative[i]-threshold) if threshold is not None else None,
            "reference_percentile": float(np.mean(conservative[reference] <= conservative[i])) if reference else None,
            "tier": (tiers or {}).get(sid, 0), "distance_bonus": (bonus or {}).get(sid, 0),
            "rank_score": number(conservative[i]+(bonus or {}).get(sid, 0)),
            "self_excluded_from_features": i in set(rated),
            "components": {c: {"z": number(z[j, i]), "contribution": number(sess["m"][j]*z[j, i]),
                               "variance_contribution": number(sess["v"][j]*z[j, i]**2)}
                           for j, c in enumerate(content.COMPONENTS)},
            "evidence": {k: evidence(i, k) for k in MODALITIES}})
    out["candidate_total"] = len(all_ranked)
    if candidate_offset+len(page) < len(all_ranked):
        out["next_offset"] = candidate_offset+len(page)
    if pair_store_ids:
        pair_ids = list(dict.fromkeys(pair_store_ids))
        pair_scope = "requested_eligible_stores"
    elif result["ready"]:
        pair_ids = result["store_ids"]
        pair_scope = "proposed_four" if config.count == 4 else "proposed_cards"
    else:
        pair_ids = [art.ids[i] for i in all_ranked[:8]]
        pair_scope = "top_eight_diagnostic_candidates_not_a_proposal"
    out["pair_store_ids"], out["pair_scope"] = pair_ids, pair_scope
    out["pairs"] = [pair_metrics(art, art.pos[a], art.pos[b], config)
                    for a, b in itertools.combinations(pair_ids, 2)]
    chosen_pairs = [pair_metrics(art, a, b, config) for a, b in itertools.combinations(chosen, 2)]
    out["slate"] = {"diagnostic_store_ids": [art.ids[i] for i in chosen],
                    "is_proposal": result["ready"],
                    "min_fit_margin": min(float(conservative[i]-threshold) for i in chosen) if chosen else None,
                    "max_pair_mean_similarity": max((p["raw_mean_similarity"] for p in chosen_pairs
                                                      if p["raw_mean_similarity"] is not None), default=None),
                    "max_pair_redundancy_percentile": max((p["redundancy_percentile"] for p in chosen_pairs
                                                           if p["redundancy_percentile"] is not None), default=None),
                    "pairs": chosen_pairs}
    return decision_meanings.attach(out)
