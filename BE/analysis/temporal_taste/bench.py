"""Replay fixed scenarios and the existing four-card selector, without an API.

Run from BE with ``python -m analysis.temporal_taste.bench --help``.
User/session databases are never loaded; the only runtime cache lives in a
temporary directory. Input observations are hypothetical, not user records.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import numpy as np

from .model import SpectralProfiles, TemporalTasteModel


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_scenarios(document, ids):
    if document.get("schema_version") != 1:
        raise ValueError("unsupported scenario version")
    seen = set()
    pool = set(ids)
    for run in document["runs"]:
        if run["id"] in seen:
            raise ValueError("duplicate scenario ID")
        seen.add(run["id"])
        events = run["past"] + [e for p in run["phases"] for e in p["events"]]
        if any(sid not in pool or type(like) is not bool for sid, like in events):
            raise ValueError(f"invalid observation in {run['id']}")
        trained = {sid for sid, _ in events}
        for phase in run["phases"]:
            if len(phase["groups"]) != 2 or any(not g for g in phase["groups"].values()):
                raise ValueError("each comparison requires two nonempty groups")
            targets = [sid for g in phase["groups"].values() for sid in g]
            if len(set(targets)) != len(targets) or not set(targets) <= pool:
                raise ValueError("duplicate/unknown comparison target")
            if trained.intersection(targets):
                raise ValueError("held-out restaurant occurs in training observations")


@contextmanager
def offline_runtime(data_dir, serving):
    """Import only scoring code. In particular, do not import app.main/restore."""
    server = Path(__file__).resolve().parents[2] / "server"
    sys.path.insert(0, str(server))
    with tempfile.TemporaryDirectory(prefix="matchuri-taste-research-") as state_dir:
        with patch.dict(os.environ, {"MATCHURI_STATE": state_dir,
                                     "MATCHURI_SNAPSHOT_EVERY": "0"}):
            from recsys import content, decision, store, taste
            old_data = store.DATA
            paths = {k: v for k, v in vars(store).items()
                     if isinstance(v, Path) and (v == old_data or old_data in v.parents)}
            old_serving = content.SERVING
            try:
                for name, value in paths.items():
                    setattr(store, name, data_dir / value.relative_to(old_data))
                content.SERVING = serving
                art = content.load()
                if art is None:
                    raise ValueError("complete serving artifacts are required for selector replay")
                yield art, decision, taste
            finally:
                for name, value in paths.items():
                    setattr(store, name, value)
                content.SERVING = old_serving
                sys.path.remove(str(server))


def ranking(ids, groups, mean, std):
    """Small target panels measure style; they are not full-catalogue food labels."""
    pos = {sid: i for i, sid in enumerate(ids)}
    a, b = groups.values()
    result = {}
    for name, values in (("mean", mean), ("conservative", mean - .5 * std)):
        order = sorted(range(len(ids)), key=lambda i: (-values[i], ids[i]))
        ranks = {ids[i]: k + 1 for k, i in enumerate(order)}
        av, bv = values[[pos[s] for s in a]], values[[pos[s] for s in b]]
        delta = av[:, None] - bv[None, :]
        result[name] = {
            "first_group_pair_win_rate": float(np.mean((delta > 0) + .5 * (delta == 0))),
            "mean_target_ranks": {k: float(np.mean([ranks[s] for s in group]))
                                  for k, group in groups.items()},
            "target_scores": {s: float(values[pos[s]]) for group in groups.values() for s in group},
            "top20": [{"store_id": ids[i], "score": float(values[i]),
                       "mean": float(mean[i]), "sd": float(std[i])} for i in order[:20]],
        }
    return result


def measure(art, decision, state, groups, candidate):
    z = decision._components(art, state, loo=True)
    _, base_mean, base_sd = decision._scores(art, state, z=z)
    baseline = ranking(art.ids, groups, base_mean, base_sd)
    baseline["decision"] = decision.recommend(state)
    prediction = candidate.predict(leave_store_out=True)
    if not prediction.identified.all():
        raise ValueError("fixture lacks observations needed to identify its free intercept")
    mean, sd = prediction.mean, prediction.latent_std
    proposed = ranking(art.ids, groups, mean, sd)
    # Both paths use the current production selector. Only the isolated GP path
    # receives different scores; patches never escape this local measurement.
    with patch.object(decision, "_components", return_value=z), \
            patch.object(decision, "_scores", return_value=(mean, mean, sd)):
        proposed["decision"] = decision.recommend(state)
    if len(proposed["decision"]["store_ids"]) != 4:
        raise AssertionError("the existing selector did not return four cards")
    return {"baseline": baseline, "temporal": proposed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, help="runtime catalogue, default: serving parent")
    parser.add_argument("--fixtures", type=Path, default=Path(__file__).with_name("scenarios.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-catalog-change", action="store_true",
                        help="explicitly evaluate another catalogue; result hashes distinguish it")
    args = parser.parse_args(argv)
    serving = args.serving.resolve()
    data_dir = (args.data_dir or serving.parent).resolve()
    started = time.perf_counter()
    input_paths = [args.fixtures, Path(__file__), Path(__file__).with_name("model.py")]
    frozen_inputs = {str(p): digest(p) for p in input_paths}
    document = json.loads(args.fixtures.read_text())
    features = SpectralProfiles.from_serving(serving)
    validate_scenarios(document, features.ids)
    pool_digest = digest(serving / "pool_ids.json")
    label_sources = {name: digest(data_dir / name) for name in document.get("label_source_sha256", {})}
    matches = (document.get("catalog_sources") == features.sources
               and document.get("pool_ids_sha256") == pool_digest
               and document.get("label_source_sha256", {}) == label_sources)
    if not matches and not args.allow_catalog_change:
        raise ValueError("catalogue differs from the audited fixtures; use --allow-catalog-change explicitly")
    rows = []
    with offline_runtime(data_dir, serving) as (art, decision, taste):
        if art.ids != list(features.ids):
            raise ValueError("runtime selector and latent model catalogue orders differ")
        code = {Path(m.__file__).name: digest(m.__file__) for m in (decision, taste)}
        for run in document["runs"]:
            model = TemporalTasteModel(features)
            for sid, like in run["past"]:
                model.record(sid, like)
            model.start_session()
            baseline = taste.start_from(taste.replay(run["past"]) if run["past"] else None)
            for phase in run["phases"]:
                for sid, like in phase["events"]:
                    model.record(sid, like)
                    taste.record(baseline, sid, like)
                result = measure(art, decision, baseline, phase["groups"], model)
                rows.append({"id": run["id"], "scope": run["scope"], "scenario": run["scenario"],
                             "seed": run["seed"], "prior": run["prior"], "phase": phase["name"],
                             "wanted": phase["wanted"], "clock": model.clock, **result})
            if len(rows) % 18 == 0:
                print(f"measured {len(rows)} states", flush=True)
    if any(digest(p) != expected for p, expected in frozen_inputs.items()):
        raise RuntimeError("scenario/model/runner changed during replay; discard this run")
    if (digest(serving / "pool_ids.json") != pool_digest
            or any(digest(data_dir / n) != value for n, value in label_sources.items())
            or any(digest(serving / f"sim_{n}.npy") != value for n, value in features.sources.items())):
        raise RuntimeError("catalogue changed during replay; discard this run")
    report = {
        "research_only": True, "production_policy_changed": False,
        "fixture_sha256": digest(args.fixtures), "catalogue_matches_audited_fixture": matches,
        "matrix_sha256": features.sources, "feature_diagnostics": features.diagnostics,
        "pool_ids_sha256": pool_digest, "label_source_sha256": label_sources,
        "source_sha256": {**code, "model.py": digest(Path(__file__).with_name("model.py")),
                          "bench.py": digest(__file__)},
        "pool_size": len(features.ids), "state_count": len(rows), "runs": rows,
        "seconds": time.perf_counter() - started,
        "interpretation": [
            "Synthetic feedback on real restaurant vectors; seeds share restaurants, not independent users.",
            "The two strict groups only assess style/food-relative ordering; actual top20 and four cards require menu audit.",
            "Temporal scores exclude each candidate's entire latest observation; baseline uses its existing feature-only LOO.",
            "Gaussian mean/std and the baseline logistic feature uncertainty have different units; neither is a calibrated satisfaction probability.",
            "The unchanged personal>0 qualification and0.5uncertainty penalty are stress tests, not a production compatibility claim.",
            "No user databases, live sessions, exposure state, HTTP requests or API schema are modified.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"states": len(rows), "seconds": report["seconds"],
                      "output": str(args.output)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
