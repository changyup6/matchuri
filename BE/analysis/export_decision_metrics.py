"""Export the full eligible candidate pool plus scoped pairs to JSON and normalized CSV.

python analysis/export_decision_metrics.py http://localhost:8000/users/USER/decision/metrics --output-dir /tmp/export
Any changed snapshot aborts before files are written. No server state is changed.
"""
import argparse
import csv
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen


def fetch_all(url):
    parsed = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(parsed.query) if k not in
              ("candidate_offset", "candidate_limit", "snapshot_id", "download")]
    data = None
    offset = 0
    while True:
        query = params + [("candidate_offset", offset), ("candidate_limit", 200)]
        if data is not None:
            query.append(("snapshot_id", data["snapshot_id"]))
        request_url = urlunsplit(parsed._replace(query=urlencode(query)))
        with urlopen(request_url, timeout=60) as response:
            page = json.load(response)
        if data is None:
            data = page
        else:
            if data["snapshot_id"] != page["snapshot_id"]:
                raise RuntimeError("Snapshot changed; restart export")
            data["candidates"].extend(page["candidates"])
        next_offset = page["next_offset"]
        if next_offset is None:
            break
        if next_offset <= offset:
            raise RuntimeError("Invalid pagination cursor")
        offset = next_offset
    if len(data["candidates"]) != data["candidate_total"]:
        raise RuntimeError("Incomplete candidate export")
    data.update(candidate_offset=0, candidate_limit=len(data["candidates"]), next_offset=None)
    return data


def write_export(data, directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "decision-metrics.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    reading = ["# 판단 수치 읽는 법", "", "## 현재 결과의 의미", ""]
    reading += [f"- {line}" for line in data["reading"]]
    reading += ["", "## 조건별 판단", ""]
    reading += [f"- {g['explanation']}" for g in data["gates"]]
    reading += ["", "## 지표 설명", ""]
    for g in data["metric_guide"]:
        reading += [f"### {g['name']} — `{g['path']}`", "", g["meaning"], "",
                    f"- 단위: {g['unit']}", f"- 읽는 법: {g['reading']}",
                    f"- 해석 한계: {g['limitation']}", f"- 결측: {g['missing']}", ""]
    (directory / "READ-ME.md").write_text("\n".join(reading)+"\n")
    def table(name, fields, rows):
        with (directory / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["snapshot_id", "feedback_count", *fields], extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({"snapshot_id": data["snapshot_id"], "feedback_count": data["feedback_count"], **row})
    table("candidates.csv", ["store_id", "rank", "qualified", "selected", "total_score", "personal_score",
                             "model_sd", "conservative_score", "fit_margin", "reference_percentile",
                             "tier", "distance_bonus", "rank_score", "self_excluded_from_features"], data["candidates"])
    table("components.csv", ["store_id", "component", "z", "contribution", "variance_contribution"],
          ({"store_id": c["store_id"], "component": k, **v} for c in data["candidates"] for k, v in c["components"].items()))
    table("evidence.csv", ["store_id", "modality", "candidate_has_feature", "reference_kind", "known_count",
                           "total_count", "nearest_store_id", "nearest_similarity"],
          ({"store_id": c["store_id"], "modality": k, "candidate_has_feature": v["candidate_has_feature"],
            "reference_kind": ref, **v[ref]} for c in data["candidates"] for k, v in c["evidence"].items()
           for ref in ("like_references", "dislike_references")))
    table("pairs.csv", ["store_a", "store_b", "pair_scope", "menu", "text", "review", "food",
                        "co_visit_similarity", "known_modalities", "missing_modalities", "raw_mean_similarity",
                        "same_brand", "same_fine_category", "passes_current_rule", "blocking_reasons"],
          ({**p, "store_a": p["store_ids"][0], "store_b": p["store_ids"][1], **p["similarities"],
            "pair_scope": data["pair_scope"], "missing_modalities": "|".join(p["missing_modalities"]),
            "blocking_reasons": "|".join(p["blocking_reasons"])} for p in data["pairs"]))
    table("gates.csv", ["name", "value", "operator", "threshold", "passed", "explanation"], data["gates"])
    table("metric-guide.csv", ["path", "name", "meaning", "unit", "reading", "limitation", "missing"], data["metric_guide"])
    table("model.csv", ["component", "weight", "variance", "sd", "used_for_personal_fit"], data["model"])
    table("predictions.csv", ["feedback_index", "store_id", "like", "predicted_rank"],
          (data["personalization"] or {}).get("validation_window", []))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="GET /users/{id}/decision/metrics URL; repeated pair_store_ids allowed")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    data = fetch_all(args.url)
    write_export(data, args.output_dir)
    print(f"Exported {len(data['candidates'])} candidates, {len(data['pairs'])} scoped pairs to {args.output_dir}")


if __name__ == "__main__":
    main()
