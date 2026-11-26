"""Fixed attribute contrasts on hypothetical feedback; never a serving policy.

This reuses the existing operational model, research GP, and four-card selectors.
Attribute labels only describe fixed evaluation populations. They never enter
learning, candidate eligibility, or ranking. Raw data and user state stay external.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time
from unittest.mock import patch

import numpy as np

from .bench import digest
from .context_floor import select_context_floor
from .depth_bench import clean
from .diagnostics import latent_state
from .matrix_bench import describe, runtime
from .model import SpectralProfiles
from .regularized import RegularizedTemporalTasteModel


METADATA = ("axis", "pair_name", "evidence_scope", "history_mode", "attribute_a",
            "attribute_b", "history_per_group", "source_groups")


def matched_key(row):
    return row["context"], row["dimension"], row["seed"]


def validate(document, ids):
    """Require fixed support/order/counts and exclude observation-brand leakage."""
    if document.get("schema_version") != 1 or document.get("catalog_ids") != list(ids):
        raise ValueError("Unsupported fixture or different catalogue order")
    if not ids or any(not isinstance(s, str) or not s for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("Catalogue IDs must be nonempty and unique")
    pool, brands = set(ids), document.get("brand_keys", {})
    if set(brands) != pool or any(not isinstance(v, str) for v in brands.values()):
        raise ValueError("Canonical brands must cover the catalogue")
    matched, seen = {}, set()
    for r in document.get("runs", []):
        if not isinstance(r.get("id"), str) or not r["id"] or r["id"] in seen:
            raise ValueError("Invalid/duplicate run ID")
        seen.add(r["id"])
        if (r.get("family") != "attribute_transfer"
                or any(not isinstance(r.get(k), str) or not r[k] for k in ("context", "dimension"))
                or type(r.get("seed")) is not int):
            raise ValueError("Invalid attribute contrast identity")
        if r.get("prior") not in {"none", "neutral", "prefer", "avoid"}:
            raise ValueError("Unknown prior arm")
        for key in ("past", "current"):
            events = r.get(key)
            if not isinstance(events, list) or any(
                    not isinstance(e, (list, tuple)) or len(e) != 2
                    or not isinstance(e[0], str) or e[0] not in pool or type(e[1]) is not bool
                    for e in events):
                raise ValueError("Unknown restaurant or invalid feedback")
        past, current = {s for s, _ in r["past"]}, {s for s, _ in r["current"]}
        if (not current or len(past) != len(r["past"]) or len(current) != len(r["current"])
                or past & current or type(r.get("history_n")) is not int
                or len(past) != r["history_n"]):
            raise ValueError("Feedback must be distinct with correct history_n and nonempty current")
        if (r["prior"] == "none") != (not past):
            raise ValueError("Only the none arm may have empty history")
        groups, labels = r.get("evaluation_groups", {}), r.get("group_labels", {})
        if (set(groups) != {"target", "comparison"} or set(labels) != set(groups)
                or any(not isinstance(v, str) or not v for v in labels.values())):
            raise ValueError("Target/comparison groups require corresponding label strings")
        for values in [r["context_ids"], r["heldout_ids"], *groups.values()]:
            if (not isinstance(values, list) or any(not isinstance(s, str) for s in values)
                    or len(set(values)) != len(values) or not set(values) <= pool):
                raise ValueError("Duplicate or unknown audit member")
        context, heldout = set(r["context_ids"]), set(r["heldout_ids"])
        if (not heldout <= context or any(not set(g) <= context for g in groups.values())
                or set(groups["target"]) & set(groups["comparison"])):
            raise ValueError("Contrast groups must be disjoint subsets of the current context")
        past_brands = {brands[s] for s in past if brands[s]}
        current_brands = {brands[s] for s in current if brands[s]}
        if past_brands & current_brands:
            raise ValueError("Prior/current brands overlap")
        if (heldout & (past | current)
                or any(brands[s] and brands[s] in past_brands | current_brands for s in heldout)):
            raise ValueError("Heldout restaurant/brand occurs in observations")
        signature = (r["current"], r["context_ids"], r["heldout_ids"], groups, labels)
        group = matched.setdefault(matched_key(r), {"signature": signature, "past": None, "arms": set()})
        if signature != group["signature"] or r["prior"] in group["arms"]:
            raise ValueError("Matched arms change current/evaluation labels or repeat")
        group["arms"].add(r["prior"])
        if past:
            history = ([s for s, _ in r["past"]], sum(v for _, v in r["past"]))
            if group["past"] is not None and history != group["past"]:
                raise ValueError("Prior arms change history IDs/order or total likes")
            group["past"] = history
    if not seen or any(not {"none", "neutral", "prefer"} <= g["arms"] for g in matched.values()):
        raise ValueError("Every comparison requires none, neutral and prefer controls")
    return len(matched)


def freeze_sources(document, paths):
    """Verify original provenance before adding runner/runtime fingerprints."""
    sources = document.get("source_sha256")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("Nonempty source_sha256 provenance is required")
    frozen = {}
    for name, expected in sources.items():
        path = Path(name)
        if not path.is_absolute() or not path.is_file() or digest(path) != expected:
            raise ValueError("Fixture source changed or unavailable: " + name)
        frozen[str(path.resolve())] = expected
    for path in paths:
        path = Path(path).resolve()
        if path.is_file():
            value = digest(path)
            if str(path) in frozen and frozen[str(path)] != value:
                raise ValueError("Conflicting source fingerprint: " + str(path))
            frozen[str(path)] = value
    return frozen


def verify_sources(frozen):
    if any(not Path(p).is_file() or digest(p) != value for p, value in frozen.items()):
        raise RuntimeError("Source/data changed during measurement; discard results")


def measure(document, profiles, art, decision, taste, diversity, names, components_count):
    """Replay all arms; cache current-only predictions only after input validation."""
    matched_count = validate(document, profiles.ids)
    if art.ids != list(profiles.ids):
        raise ValueError("Operational and research catalogue order differs")
    if decision.DEFAULT.count != 4:
        raise ValueError("This fixed benchmark requires the existing four-card default")
    arrays, rows, cache = {"ids": np.array(profiles.ids)}, [], {}
    zero = np.zeros((components_count, len(art.ids)))
    all_ids = set(art.ids)
    for r in document["runs"]:
        key = matched_key(r)
        latest = dict(r["current"])
        state = {"like_ids": [s for s, v in latest.items() if v],
                 "dislike_ids": [s for s, v in latest.items() if not v],
                 "n": len(latest), "m": np.zeros(components_count)}
        if key not in cache:
            current = RegularizedTemporalTasteModel(profiles)
            for sid, like in r["current"]:
                current.record(sid, like)
            cp = current.predict()
            with patch.object(decision, "_components", return_value=zero), \
                    patch.object(decision, "_scores", return_value=(cp.mean, cp.mean, cp.latent_std)):
                slate = decision.recommend(state)
            cache[key] = {"prediction": cp, "slate": deepcopy(slate), "full_std": None}
        current_cached = cache[key]
        cp, current_slate = current_cached["prediction"], deepcopy(current_cached["slate"])
        full = RegularizedTemporalTasteModel(profiles)
        for sid, like in r["past"] + r["current"]:
            full.record(sid, like)
        operational = taste.start_from(taste.replay(r["past"]) if r["past"] else None)
        for sid, like in r["current"]:
            taste.record(operational, sid, like)
        z = decision._components(art, operational, loo=True)
        _, om, os = decision._scores(art, operational, z=z)
        op_slate = decision.recommend(operational)
        fp = full.predict()
        if r["past"]:
            old = current_cached["full_std"]
            if old is not None and not np.array_equal(old, fp.latent_std):
                raise ArithmeticError("Label-only arm changed GP uncertainty")
            current_cached["full_std"] = fp.latent_std
        with patch.object(decision, "_components", return_value=zero), \
                patch.object(decision, "_scores", return_value=(fp.mean, fp.mean, fp.latent_std)):
            full_slate = decision.recommend(state)
            floor = select_context_floor(art.ids, cp.mean, cp.latent_std, config=decision.DEFAULT,
                full_slate=full_slate, current_slate=current_slate,
                select_allowed=lambda allowed: decision.recommend(state, allow=allowed),
                is_diverse=lambda a, b: decision._diverse(art, art.pos[a], art.pos[b], decision.DEFAULT))
        if "floor" in current_cached and current_cached["floor"] != floor.floor:
            raise ArithmeticError("Current-only floor changed across matched arms")
        current_cached["floor"] = floor.floor
        vector = latent_state(full, profiles, history_end_clock=len(r["past"]))
        if vector.reconstruction_max_error >= 1e-10:
            raise ArithmeticError("Preference vector reconstruction failed")
        row = {k: deepcopy(r[k]) for k in ("id", "family", "context", "dimension", "seed",
                "prior", "history_n", "group_labels", *METADATA) if k in r}
        row.update(models={}, current_responses=len(r["current"]),
            current_slate_ids=current_slate["store_ids"], context_floor=floor.floor,
            used_context=floor.used_context, fallback_reason=floor.fallback_reason,
            vector={"dimensions": len(vector.total), "intercept": vector.intercept,
                "reconstruction_max_error": vector.reconstruction_max_error,
                "norms": {k: float(np.linalg.norm(getattr(vector, k))) for k in ("total", "history", "current")}})
        for name, mu, sd, slate, allowed in (
                ("operational", om, os, op_slate, all_ids),
                ("regularized", fp.mean, fp.latent_std, full_slate, all_ids),
                ("regularized_context_floor", fp.mean, fp.latent_std, floor.slate,
                 floor.qualified_ids if floor.used_context else all_ids)):
            row["models"][name] = describe(r, art, names, document["brand_keys"], mu, sd,
                                           slate, allowed, decision, diversity)
        for name, value in {"research_mean": fp.mean, "research_std": fp.latent_std,
                "current_mean": cp.mean, "current_std": cp.latent_std,
                "operational_mean": om, "operational_std": os,
                **{"research_" + k: getattr(vector, k) for k in ("total", "history", "current")}}.items():
            arrays[r["id"] + "__" + name] = value
        rows.append(clean(row))
        if len(rows) % 6 == 0:
            print(f"measured {len(rows)}/{len(document['runs'])} attribute states", flush=True)
    return rows, arrays, matched_count


def run(args):
    started = time.perf_counter()
    serving = args.serving.resolve()
    document = json.loads(args.fixtures.read_text())
    code = Path(__file__).parent
    paths = [args.fixtures, *serving.iterdir(), *[code / n for n in
             ("attribute_bench.py", "matrix_bench.py", "bench.py", "model.py", "regularized.py",
              "context_floor.py", "depth_bench.py", "diagnostics.py", "metrics.py")],
             *(code.parents[1] / "server/recsys").glob("*.py")]
    if args.review_cache:
        paths.append(args.review_cache)
    frozen = freeze_sources(document, paths)
    profiles = SpectralProfiles.from_serving(serving)
    validate(document, profiles.ids)
    with runtime(serving.parent, serving, args.review_cache) as (art, decision, taste):
        from recsys import content, diversity, store
        names = {s: (store.get_store(s) or {}).get("name", s) for s in art.ids}
        rows, arrays, count = measure(document, profiles, art, decision, taste, diversity, names, len(content.IDX))
    verify_sources(frozen)
    result = {"research_only": True, "production_changed": False, "state_count": len(rows),
        "catalogue_size": len(profiles.ids), "dimensions": profiles.x.shape[1],
        "source_sha256": frozen, "invariants_passed": True, "matched_groups": count,
        "current_only_fits": count, "current_only_cache_hits": len(rows) - count,
        "seconds": time.perf_counter() - started, "rows": rows,
        "definitions": {"scores": "Model-specific mean and uncertainty, not satisfaction probabilities.",
            "groups": "Fixed disjoint attribute populations; empty groups remain unmeasurable.",
            "ranking": "Context top10 is conditional; actual four cards come from the existing selector.",
            "vectors": "Jointly fitted spectral coefficients, not semantic coordinates or causal shares.",
            "current_cache": "One current-only fit/slate per validated identical current sequence; no past data enter it.",
            "evidence": "Hypothetical matched feedback on real vectors; not independent users or online tests."}}
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
