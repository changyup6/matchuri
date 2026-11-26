"""The evaluation must not reward target leakage or change paired inputs."""
import copy
import json
from pathlib import Path

import pytest

from .bench import validate_scenarios


def tiny_case():
    return {"schema_version": 1, "runs": [{
        "id": "case", "past": [["old", True]],
        "phases": [{"events": [["today", False]],
                    "groups": {"a": ["held-a"], "b": ["held-b"]}}],
    }]}


@pytest.mark.parametrize("mutation", ["past_target", "later_target", "duplicate_target",
                                      "unknown", "nonboolean", "duplicate_run"])
def test_invalid_evaluation_is_rejected(mutation):
    document = tiny_case()
    run = document["runs"][0]
    if mutation == "past_target":
        run["past"].append(["held-a", True])
    elif mutation == "later_target":
        phase = copy.deepcopy(run["phases"][0])
        phase["events"] = [["held-a", False]]
        run["phases"].append(phase)
    elif mutation == "duplicate_target":
        run["phases"][0]["groups"]["b"] = ["held-a"]
    elif mutation == "unknown":
        run["past"] = [["outside", True]]
    elif mutation == "nonboolean":
        run["past"] = [["old", "false"]]
    else:
        document["runs"].append(copy.deepcopy(run))
    with pytest.raises(ValueError):
        validate_scenarios(document, ["old", "today", "held-a", "held-b"])


@pytest.mark.parametrize("filename,expected_runs,expected_states", [
    ("scenarios.json", 144, 180),
    ("scenarios_salad_shift.json", 18, 18),
])
def test_frozen_counterfactuals_share_current_feedback_and_targets(filename, expected_runs, expected_states):
    document = json.loads(Path(__file__).with_name(filename).read_text())
    runs = document["runs"]
    ids = {sid for r in runs for sid, _ in r["past"]}
    for run in runs:
        for phase in run["phases"]:
            ids.update(s for s, _ in phase["events"])
            ids.update(s for group in phase["groups"].values() for s in group)
    validate_scenarios(document, ids)
    assert len(runs) == expected_runs
    assert sum(len(r["phases"]) for r in runs) == expected_states
    paired = {}
    for run in runs:
        key = run["scope"], run["scenario"], run["seed"]
        if key in paired:
            assert run["phases"] == paired[key]
        paired[key] = run["phases"]
