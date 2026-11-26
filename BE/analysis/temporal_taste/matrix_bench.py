"""Offline paired preference matrix; labels describe outcomes, never ranking.

Real catalogue vectors receive hypothetical feedback. No API, user database,
production model, or serving policy is modified. Overlapping audit groups are
reported separately; their shares need not sum to one.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import itertools
import json
from pathlib import Path
import shutil
import tempfile
import time
from unittest.mock import patch

import numpy as np

from .bench import digest, offline_runtime
from .context_floor import select_context_floor
from .depth_bench import clean, full_order
from .diagnostics import latent_state
from .metrics import pairwise_win_rate
from .model import SpectralProfiles
from .regularized import RegularizedTemporalTasteModel


def validate(document, ids):
    """Check matched arms and brand-disjoint fixed evaluation before scoring."""
    if document.get("schema_version") != 1 or document.get("catalog_ids") != list(ids):
        raise ValueError("Unsupported fixture or different catalogue order")
    pool, brands, seen, matched = set(ids), document["brand_keys"], set(), {}
    if set(brands) != pool or any(not isinstance(v, str) for v in brands.values()):
        raise ValueError("Canonical brands must cover the catalogue")
    for r in document["runs"]:
        if not isinstance(r["id"], str) or not r["id"] or r["id"] in seen:
            raise ValueError("Invalid/duplicate run ID")
        seen.add(r["id"])
        if r["family"] not in {"cutlet_cuisine", "cuisine_form"}:
            raise ValueError("Unknown scenario family")
        events = r["past"] + r["current"]
        if not r["current"] or any(s not in pool or type(v) is not bool for s, v in events):
            raise ValueError("Unknown restaurant or invalid feedback")
        past, current = {s for s, _ in r["past"]}, {s for s, _ in r["current"]}
        if (len(past) != len(r["past"]) or len(past) != r["history_n"]
                or len(current) != len(r["current"]) or past & current):
            raise ValueError("History must be distinct and separate from current events")
        if r["prior"] == "none" and past:
            raise ValueError("No-history arm has observations")
        context, heldout = set(r["context_ids"]), set(r["heldout_ids"])
        for values in [r["context_ids"], r["heldout_ids"], *r["evaluation_groups"].values()]:
            if len(set(values)) != len(values) or not set(values) <= pool:
                raise ValueError("Duplicate or unknown audit member")
        if not heldout <= context or any(not set(g) <= context for g in r["evaluation_groups"].values()):
            raise ValueError("Evaluation groups must belong to the current context")
        known_brands = {brands[s] for s in past | current if brands[s]}
        if heldout & (past | current) or any(brands[s] and brands[s] in known_brands for s in heldout):
            raise ValueError("Heldout restaurant/brand occurs in observations")
        if {brands[s] for s in past if brands[s]} & {brands[s] for s in current if brands[s]}:
            raise ValueError("Prior/current brands overlap")
        key = (r["family"], r["context"], r["seed"])
        signature = (r["current"], r["context_ids"], r["heldout_ids"], r["evaluation_groups"])
        group = matched.setdefault(key, {"signature": signature, "past": None, "arms": set()})
        if signature != group["signature"] or r["prior"] in group["arms"]:
            raise ValueError("Matched arms have different current/evaluation data or repeat")
        group["arms"].add(r["prior"])
        if past:
            order = [s for s, _ in r["past"]]
            if group["past"] is not None and order != group["past"]:
                raise ValueError("Prior arms have different observation IDs/order")
            group["past"] = order
    if not seen or any(not {"none", "neutral"} <= g["arms"] for g in matched.values()):
        raise ValueError("Every comparison requires no-history and neutral controls")


@contextmanager
def runtime(data, serving, review_cache):
    """Seed only the new temporary state; normal cache integrity checks remain."""
    original = tempfile.TemporaryDirectory
    def prepared(*args, **kwargs):
        temporary = original(*args, **kwargs)
        try:
            if review_cache:
                target = Path(temporary.name) / "review-ranks.cache"
                shutil.copyfile(review_cache, target)
                if digest(target) != digest(review_cache):
                    raise RuntimeError("Review cache copy differs")
            return temporary
        except Exception:
            temporary.cleanup()
            raise
    with patch("analysis.temporal_taste.bench.tempfile.TemporaryDirectory", prepared):
        with offline_runtime(data, serving) as loaded:
            yield loaded


def describe(r, art, names, brands, mean, std, slate, allowed, decision, diversity):
    """Common and policy-eligible populations; fixed heldout means stay fixed."""
    ids, pos = art.ids, art.pos
    score = mean - decision.DEFAULT.uncertainty_weight * std
    if (not np.isfinite(mean).all() or not np.isfinite(std).all()
            or not np.isfinite(score).all() or np.any(std < 0)):
        raise ValueError("Nonfinite score or invalid uncertainty")
    _, ranked = full_order(ids, score)
    rank = {s: i + 1 for i, s in enumerate(ranked)}
    eligible = [s for s in ranked if s in allowed]
    context, heldout = set(r["context_ids"]), set(r["heldout_ids"])
    latest = {s: {"like": like, "source": source} for source, events in
              (("history", r["past"]), ("current", r["current"])) for s, like in events}
    known_brands = {brands[s] for s in latest if brands[s]}
    unseen = {s for s in ids if s not in latest and (not brands[s] or brands[s] not in known_brands)}
    chosen = slate["store_ids"]
    if len(chosen) != 4 or len(set(chosen)) != 4 or not set(chosen) <= set(ids) & set(allowed):
        raise ArithmeticError("Existing selector did not return four unique catalogue cards")
    lists = {"cards": chosen, "top20": eligible[:20], "raw_global_top20": ranked[:20],
             "context_top10": [s for s in eligible if s in context][:10],
             "heldout_context_top10": [s for s in eligible if s in heldout][:10]}
    def card(s):
        i = pos[s]
        return {"store_id": s, "name": names[s], "mean": mean[i], "std": std[i],
                "score": score[i], "global_rank": rank[s], "current_context": s in context,
                "heldout": s in heldout, "unseen_store_and_brand": s in unseen,
                "latest_feedback": latest.get(s),
                "groups": [g for g, members in r["evaluation_groups"].items() if s in members]}
    result = {k: [card(s) for s in values] for k, values in lists.items()}
    result.update(qualified_count=slate["qualified_count"], eligible_count=len(allowed),
                  context_count=len(context), heldout_count=len(heldout),
                  context_card_hits=len(context & set(chosen)),
                  context_unseen_card_hits=len(context & unseen & set(chosen)),
                  unseen_cards=len(unseen & set(chosen)),
                  disliked_cards=sum(s in latest and not latest[s]["like"] for s in chosen), groups={})
    hit_sets = {**{k: set(v) for k, v in lists.items()}, "top10": set(eligible[:10]),
                "raw_global_top10": set(ranked[:10])}
    for name, values in r["evaluation_groups"].items():
        g, h = set(values), set(values) & heldout
        def stats(members):
            ordered = [s for s in ids if s in members]
            ii = [pos[s] for s in ordered]
            return {"count": len(ii), "eligible_count": len(members & allowed),
                    "unseen_count": len(members & unseen),
                    "mean": float(np.mean(mean[ii])) if ii else None,
                    "conservative_mean": float(np.mean(score[ii])) if ii else None,
                    "global_mean_rank": float(np.mean([rank[s] for s in ordered])) if ii else None,
                    "hits": {k: len(members & v) for k, v in hit_sets.items()},
                    "unseen_hits": {k: len(members & unseen & v) for k, v in hit_sets.items()}}
        result["groups"][name] = {"all": stats(g), "heldout": stats(h)}
    result["group_comparisons"] = []
    for a, b in itertools.combinations(r["evaluation_groups"], 2):
        for scope, permitted in (("all", set(ids)), ("heldout", heldout)):
            aa = [pos[s] for s in r["evaluation_groups"][a] if s in permitted]
            bb = [pos[s] for s in r["evaluation_groups"][b] if s in permitted]
            result["group_comparisons"].append({"a": a, "b": b, "scope": scope,
                "a_count": len(aa), "b_count": len(bb),
                "mean_gap": float(np.mean(mean[aa]) - np.mean(mean[bb])) if aa and bb else None,
                "conservative_gap": float(np.mean(score[aa]) - np.mean(score[bb])) if aa and bb else None,
                "mean_pair_win": pairwise_win_rate(mean, aa, bb),
                "conservative_pair_win": pairwise_win_rate(score, aa, bb)})
    pairs = list(itertools.combinations(chosen, 2))
    redundancy = [diversity.pair_redundancy(art, pos[a], pos[b]) for a, b in pairs]
    result["diversity"] = {"pair_percentiles": redundancy,
        "strict_conflicts": sum(not decision._diverse(art, pos[a], pos[b], decision.DEFAULT) for a, b in pairs),
        "same_brand_pairs": sum(bool(brands[a]) and brands[a] == brands[b] for a, b in pairs)}
    return result


def run(args):
    started = time.perf_counter()
    serving = args.serving.resolve()
    document = json.loads(args.fixtures.read_text())
    frozen = dict(document["source_sha256"])
    code = Path(__file__).parent
    paths = [args.fixtures, *serving.iterdir(), *[code / n for n in
             ("matrix_bench.py", "bench.py", "model.py", "regularized.py", "context_floor.py",
              "depth_bench.py", "diagnostics.py", "metrics.py")],
             *(code.parents[1] / "server/recsys").glob("*.py")]
    if args.review_cache:
        paths.append(args.review_cache)
    for name, expected in frozen.items():
        if not Path(name).is_absolute() or digest(name) != expected:
            raise ValueError("Fixture source changed: " + name)
    frozen.update({str(p.resolve()): digest(p) for p in paths if p.is_file()})
    profiles = SpectralProfiles.from_serving(serving)
    validate(document, profiles.ids)
    arrays, rows, invariants = {"ids": np.array(profiles.ids)}, [], {}
    with runtime(serving.parent, serving, args.review_cache) as (art, decision, taste):
        from recsys import content, diversity, store
        if art.ids != list(profiles.ids):
            raise ValueError("Operational and research catalogue order differs")
        names = {s: (store.get_store(s) or {}).get("name", s) for s in art.ids}
        zero = np.zeros((len(content.IDX), len(art.ids)))
        for r in document["runs"]:
            full, current = RegularizedTemporalTasteModel(profiles), RegularizedTemporalTasteModel(profiles)
            for sid, like in r["past"]:
                full.record(sid, like)
            operational = taste.start_from(taste.replay(r["past"]) if r["past"] else None)
            for sid, like in r["current"]:
                full.record(sid, like); current.record(sid, like); taste.record(operational, sid, like)
            z = decision._components(art, operational, loo=True)
            _, om, os = decision._scores(art, operational, z=z)
            op_slate = decision.recommend(operational)
            fp, cp = full.predict(), current.predict()
            latest = dict(r["current"])
            state = {"like_ids": [s for s, v in latest.items() if v],
                     "dislike_ids": [s for s, v in latest.items() if not v],
                     "n": len(latest), "m": np.zeros(len(content.IDX))}
            with patch.object(decision, "_components", return_value=zero):
                with patch.object(decision, "_scores", return_value=(cp.mean, cp.mean, cp.latent_std)):
                    current_slate = decision.recommend(state)
                with patch.object(decision, "_scores", return_value=(fp.mean, fp.mean, fp.latent_std)):
                    full_slate = decision.recommend(state)
                    floor = select_context_floor(art.ids, cp.mean, cp.latent_std, config=decision.DEFAULT,
                        full_slate=full_slate, current_slate=current_slate,
                        select_allowed=lambda allowed: decision.recommend(state, allow=allowed),
                        is_diverse=lambda a, b: decision._diverse(art, art.pos[a], art.pos[b], decision.DEFAULT))
            key = (r["family"], r["context"], r["seed"])
            old = invariants.setdefault(key, {"mean": cp.mean, "std": cp.latent_std,
                    "cards": current_slate["store_ids"], "floor": floor.floor, "full_std": None})
            if (not np.array_equal(old["mean"], cp.mean) or not np.array_equal(old["std"], cp.latent_std)
                    or old["cards"] != current_slate["store_ids"] or old["floor"] != floor.floor):
                raise ArithmeticError("Current-only results changed across matched arms")
            if r["past"]:
                if old["full_std"] is not None and not np.array_equal(old["full_std"], fp.latent_std):
                    raise ArithmeticError("Label-only arm changed GP uncertainty")
                old["full_std"] = fp.latent_std
            vector = latent_state(full, profiles, history_end_clock=len(r["past"]))
            if vector.reconstruction_max_error >= 1e-10:
                raise ArithmeticError("Preference vector reconstruction failed")
            row = {k: r[k] for k in ("id", "family", "context", "seed", "prior", "history_n")}
            row.update(models={}, current_responses=len(r["current"]),
                current_slate_ids=current_slate["store_ids"], context_floor=floor.floor,
                used_context=floor.used_context, fallback_reason=floor.fallback_reason,
                vector={"dimensions": len(vector.total), "intercept": vector.intercept,
                    "reconstruction_max_error": vector.reconstruction_max_error,
                    "norms": {k: float(np.linalg.norm(getattr(vector, k))) for k in ("total", "history", "current")}})
            for name, mu, sd, slate, allowed in (
                    ("operational", om, os, op_slate, set(art.ids)),
                    ("regularized", fp.mean, fp.latent_std, full_slate, set(art.ids)),
                    ("regularized_context_floor", fp.mean, fp.latent_std, floor.slate,
                     floor.qualified_ids if floor.used_context else set(art.ids))):
                row["models"][name] = describe(r, art, names, document["brand_keys"], mu, sd,
                                               slate, allowed, decision, diversity)
            for name, value in {"research_mean": fp.mean, "research_std": fp.latent_std,
                    "current_mean": cp.mean, "current_std": cp.latent_std,
                    "operational_mean": om, "operational_std": os,
                    **{"research_" + k: getattr(vector, k) for k in ("total", "history", "current")}}.items():
                arrays[r["id"] + "__" + name] = value
            rows.append(clean(row))
            if len(rows) % 6 == 0:
                print(f"measured {len(rows)}/{len(document['runs'])} matrix states", flush=True)
    if any(digest(p) != expected for p, expected in frozen.items()):
        raise RuntimeError("Source/data changed during measurement; discard results")
    result = {"research_only": True, "production_changed": False, "state_count": len(rows),
        "catalogue_size": len(profiles.ids), "dimensions": profiles.x.shape[1],
        "source_sha256": frozen, "invariants_passed": True, "matched_groups": len(invariants),
        "seconds": time.perf_counter() - started, "rows": rows,
        "definitions": {"scores": "Mean and uncertainty have model-specific units, not probabilities.",
            "groups": "Audit groups can overlap; fixed heldout means do not condition on policy eligibility.",
            "ranking": "top20 uses policy-eligible candidates; raw_global_top20 keeps unfiltered ranking. Context top10 is conditional, not the actual four-card recommendation.",
            "vectors": "4317-dimensional jointly fitted coefficients, not semantic coordinates or causal shares.",
            "evidence": "Hypothetical feedback on real vectors; shared restaurants/seeds are not independent users."}}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(clean(result), ensure_ascii=False, allow_nan=False) + "\n")
    np.savez_compressed(args.output / "predictions.npz", **arrays)
    print(json.dumps({"states": len(rows), "seconds": result["seconds"]}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-cache", type=Path)
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
