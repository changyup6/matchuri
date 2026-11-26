"""Export must reconcile with scoring/selection, preserve unknowns and exact feedback state."""
import csv
import copy
import importlib.util
import json
import pickle
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.decision_model import DecisionMetricsOut
from app.decision_summary_model import DecisionSummaryOut
from recsys import content, decision, decision_metrics, decision_summary, policy, taste


def test_cold_export_measures_candidates_even_when_gate_stops(art, sess):
    original = pickle.dumps(sess)
    data = decision_metrics.export(sess)
    assert data["decision"] == decision.evaluate(sess)
    assert not data["decision"]["ready"] and len(data["candidates"]) == len(art.ids)
    assert data["personalization"]["validation_auc"] is None
    assert next(g for g in data["gates"] if g["name"] == "validation_auc")["passed"] is None
    assert data["pair_scope"] == "top_eight_diagnostic_candidates_not_a_proposal"
    assert len(data["pairs"]) == 15
    assert pickle.dumps(sess) == original
    json.dumps(data, allow_nan=False)
    json.dumps(DecisionMetricsOut.model_validate(data).model_dump(), allow_nan=False)


def test_export_components_reconstruct_actual_scores_and_uncertainty(art, sess):
    for sid, label in [("s0", True), ("s2", True), ("s3", False)]:
        taste.record(sess, sid, label)
    data = decision_metrics.export(sess)
    total, personal, sd = decision._scores(art, sess, loo=True)
    for row in data["candidates"]:
        i = art.pos[row["store_id"]]
        assert row["total_score"] == pytest.approx(total[i])
        assert sum(c["contribution"] for c in row["components"].values()) == pytest.approx(total[i])
        used = [row["components"][content.COMPONENTS[j]] for j in decision.PERSONAL]
        assert sum(c["contribution"] for c in used) == pytest.approx(personal[i])
        assert sum(c["variance_contribution"] for c in used) == pytest.approx(sd[i]**2)
        assert row["conservative_score"] == pytest.approx(personal[i]-.5*sd[i])
    s0 = next(c for c in data["candidates"] if c["store_id"] == "s0")
    assert s0["self_excluded_from_features"]
    assert s0["evidence"]["menu"]["like_references"]["total_count"] == 1


def test_pair_export_preserves_vector_values_and_missingness(art, sess):
    data = decision_metrics.export(sess, pair_store_ids=["s0", "s1", "s4", "s5"])
    for row in data["pairs"]:
        a, b = [art.pos[s] for s in row["store_ids"]]
        assert row["passes_current_rule"] == decision._diverse(art, a, b, decision.DEFAULT)
        assert row["passes_current_rule"] == (not row["blocking_reasons"])
        for k, value in row["similarities"].items():
            actual = getattr(art, "sim_"+k)[a, b]
            assert (value is None and np.isnan(actual)) or value == float(actual)
    unknown = next(p for p in data["pairs"] if p["store_ids"] == ["s4", "s5"])
    assert unknown["similarities"]["menu"] is None
    assert unknown["similarities"]["text"] is None
    assert unknown["known_modalities"] == 2
    assert "same_brand" in data["pairs"][0]["blocking_reasons"]


def _reference_pair_percentiles(art, a, b):
    """Independent, small-pool count definition of the empirical midrank."""
    result = {}
    for kind in decision_metrics.MODALITIES:
        matrix = getattr(art, "sim_" + kind)
        value = matrix[a, b]
        reference = matrix[np.triu_indices(len(art.ids), 1)]
        reference = reference[np.isfinite(reference)]
        result[kind] = (float(np.mean(reference < value) + .5 * np.mean(reference == value))
                        if np.isfinite(value) and reference.size else None)
    return result


def test_relative_pair_export_matches_pair_distribution_without_replacing_raw_values(art, sess):
    before = pickle.dumps(sess)
    raw_before = {kind: getattr(art, "sim_" + kind).copy() for kind in decision_metrics.MODALITIES}
    data = decision_metrics.export(sess, pair_store_ids=art.ids)
    for pair in data["pairs"]:
        a, b = [art.pos[sid] for sid in pair["store_ids"]]
        expected = _reference_pair_percentiles(art, a, b)
        assert set(pair["relative_similarities"]) == set(decision_metrics.MODALITIES)
        for kind, percentile in expected.items():
            actual = pair["relative_similarities"][kind]
            assert actual is None if percentile is None else actual == pytest.approx(percentile)
            raw = raw_before[kind][a, b]
            assert pair["similarities"][kind] == (float(raw) if np.isfinite(raw) else None)
        observed = [value for value in expected.values() if value is not None]
        assert pair["redundancy_percentile"] == pytest.approx(max(observed))
        raw_values = [value for value in pair["similarities"].values() if value is not None]
        assert pair["raw_mean_similarity"] == float(np.mean(raw_values))
    DecisionMetricsOut.model_validate(data)
    json.dumps(data, allow_nan=False)
    assert pickle.dumps(sess) == before
    for kind, matrix in raw_before.items():
        np.testing.assert_array_equal(getattr(art, "sim_" + kind), matrix)
    assert "diversity.py" in data["provenance"]["code_sha256"]


def test_relative_pair_export_keeps_insufficient_evidence_unknown(art, sess):
    for kind in ("menu", "text", "food"):
        matrix = getattr(art, "sim_" + kind)
        matrix[0, 2] = matrix[2, 0] = np.nan
    data = decision_metrics.export(sess, pair_store_ids=["s0", "s2"])
    pair = data["pairs"][0]
    assert pair["known_modalities"] == 1
    assert pair["relative_similarities"]["review"] is not None
    assert all(pair["relative_similarities"][kind] is None for kind in ("menu", "text", "food"))
    assert pair["redundancy_percentile"] is None
    assert pair["raw_mean_similarity"] == float(art.sim_review[0, 2])
    assert not pair["passes_current_rule"]
    assert "insufficient_modalities" in pair["blocking_reasons"]
    json.dumps(DecisionMetricsOut.model_validate(data).model_dump(), allow_nan=False)


def test_slate_redundancy_summarizes_actual_diagnostic_pairs_and_empty_slates(art, sess, monkeypatch):
    # Isolate pair reporting while allowing the real selector to form a slate.
    monkeypatch.setattr(decision, "_scores", lambda *args, **kwargs:
                        (np.ones(len(art.ids)), np.ones(len(art.ids)), np.zeros(len(art.ids))))
    data = decision_metrics.export(sess)
    slate = data["slate"]
    assert len(slate["diagnostic_store_ids"]) == 4 and len(slate["pairs"]) == 6
    expected = []
    for pair in slate["pairs"]:
        a, b = [art.pos[sid] for sid in pair["store_ids"]]
        expected.append(max(value for value in _reference_pair_percentiles(art, a, b).values()
                            if value is not None))
    assert slate["max_pair_redundancy_percentile"] == pytest.approx(max(expected))
    assert slate["max_pair_mean_similarity"] == max(pair["raw_mean_similarity"] for pair in slate["pairs"])
    for kwargs in [{"allow": set()}, {"config": replace(decision.DEFAULT, count=1)}]:
        empty = decision_metrics.export(sess, **kwargs)["slate"]
        assert empty["pairs"] == []
        assert empty["max_pair_mean_similarity"] is None
        assert empty["max_pair_redundancy_percentile"] is None


def test_detailed_percentiles_do_not_reinterpret_summary_schema_or_similarity_score(art, sess, monkeypatch):
    # The same raw pair has opposite relative positions in different catalogues.
    # The existing summary scale must still report 50*(1+raw_mean), not percentile.
    summaries, percentiles = [], []
    for background in [.1, .9]:
        current = copy.deepcopy(art)
        for kind in decision_metrics.MODALITIES:
            matrix = getattr(current, "sim_" + kind)
            matrix[:] = background
            np.fill_diagonal(matrix, 1)
            matrix[0, 2] = matrix[2, 0] = .4
        monkeypatch.setattr(content, "load", lambda current=current: current)
        pair = decision_metrics.export(sess, pair_store_ids=["s0", "s2"])["pairs"][0]
        percentiles.append(pair["redundancy_percentile"])
        summary = decision_summary.summarize(sess, [(10, "s0"), (12, "s2")])
        DecisionSummaryOut.model_validate(summary)
        summaries.append(summary)
    assert percentiles[0] > .9 and percentiles[1] < .1
    assert summaries[0] == summaries[1]
    assert summaries[0]["diversity"] == {"passed": True, "similarity_score": 70., "card_ids": (10, 12)}
    schema = DecisionSummaryOut.model_json_schema()
    assert set(schema["properties"]) == {"status", "decision_readiness_score", "personalization", "fit_count", "diversity"}
    assert set(schema["$defs"]["SummaryPersonalization"]["properties"]) == {
        "ready", "readiness_score", "discrimination_score", "stability_score"}
    assert set(schema["$defs"]["SummaryDiversity"]["properties"]) == {"passed", "similarity_score", "card_ids"}
    assert all(field not in json.dumps(schema) for field in [
        "relative_similarities", "redundancy_percentile", "max_pair_redundancy_percentile"])


def test_pagination_filtering_and_snapshot_identity(art, sess):
    a = decision_metrics.export(sess, candidate_limit=2)
    b = decision_metrics.export(sess, candidate_limit=2, candidate_offset=2)
    assert a["snapshot_id"] == b["snapshot_id"] and a["next_offset"] == 2
    assert not ({c["store_id"] for c in a["candidates"]} & {c["store_id"] for c in b["candidates"]})
    filtered = decision_metrics.export(sess, allow={"s0"})
    assert filtered["candidate_total"] == 1 and filtered["snapshot_id"] != a["snapshot_id"]
    with pytest.raises(ValueError):
        decision_metrics.export(sess, allow={"s0"}, pair_store_ids=["s1"])
    taste.record(sess, "s0", True)
    assert decision_metrics.export(sess)["snapshot_id"] != a["snapshot_id"]


def test_export_unavailable_and_location_required(art, sess, monkeypatch):
    data = decision_metrics.export(sess, allow=set(), context={"location_required": True})
    assert data["decision"]["status"] == "location_required" and data["candidates"] == []
    monkeypatch.setattr(content, "load", lambda: None)
    data = decision_metrics.export(sess)
    assert data["decision"]["status"] == "unavailable" and data["personalization"] is None
    DecisionMetricsOut.model_validate(data)


def test_atomic_snapshot_waits_for_feedback_and_is_detached(art, monkeypatch):
    sess = policy.session_for("export-concurrent")
    original_record = taste.record
    entered, release, attempting = Event(), Event(), Event()
    def slow_record(*args):
        entered.set()
        assert release.wait(3)
        return original_record(*args)
    monkeypatch.setattr(taste, "record", slow_record)
    def snapshot():
        attempting.set()
        return policy.taste_snapshot(sess)
    with ThreadPoolExecutor(2) as executor:
        write = executor.submit(policy.record, sess, "s0", True, snapshot=True)
        assert entered.wait(3)
        read = executor.submit(snapshot)
        assert attempting.wait(3)
        assert not read.done()
        release.set()
        a, b = write.result(), read.result()
    assert a["n"] == b["n"] == 1
    sess["taste"]["m"][0] += 1
    assert a["m"][0] != sess["taste"]["m"][0]


def test_api_export_download_opt_in_and_stale_page():
    import main
    client = TestClient(main.app)
    uid = client.post("/users", json={}).json()["user_id"]
    url = f"/users/{uid}/decision/metrics"
    r = client.get(url, params={"candidate_limit": 2, "download": True})
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    before = r.json()
    assert before["candidate_total"] >= 2 and len(before["candidates"]) == 2
    card = client.get("/cards?limit=1").json()[0]
    payload = {"user_id": uid, "card_id": card["card_id"], "prefer": "like"}
    r = client.post("/preferences?include_decision_metrics=true", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["decision_metrics"]["feedback_count"] == 1
    assert client.get(url, params={"snapshot_id": before["snapshot_id"]}).status_code == 409
    assert client.post("/preferences", json=payload).json()["decision_metrics"] is None
    assert client.get(url, params={"candidate_limit": 201}).status_code == 422
    assert client.get(url, params=[("pair_store_ids", "x")]*21).status_code == 422
    assert client.get(url, params={"pair_store_ids": "not-in-pool"}).status_code == 422
    assert client.get("/users/missing-metric-user/decision/metrics").status_code == 404
    schema = client.get("/openapi.json").json()
    assert "DecisionMetricsOut" in schema["components"]["schemas"]


def test_file_export_fetches_all_pages_and_keeps_csv_unknowns(art, sess, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("metric_export", Path(__file__).resolve().parents[2] / "analysis/export_decision_metrics.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    import io
    from urllib.parse import parse_qs, urlsplit
    def response(url, **kwargs):
        query = parse_qs(urlsplit(url).query)
        offset = int(query["candidate_offset"][0])
        page = decision_metrics.export(sess, candidate_offset=offset, candidate_limit=2,
                                       pair_store_ids=["s4", "s5"])
        if offset:
            assert query["snapshot_id"] == [page["snapshot_id"]]
        return io.BytesIO(json.dumps(page, allow_nan=False).encode())
    monkeypatch.setattr(exporter, "urlopen", response)
    data = exporter.fetch_all("http://test/decision/metrics")
    assert len(data["candidates"]) == data["candidate_total"] == 6
    exporter.write_export(data, tmp_path)
    assert len(list(tmp_path.glob("*.csv"))) == 8
    assert "반응 구분력" in (tmp_path / "READ-ME.md").read_text()
    with (tmp_path / "metric-guide.csv").open(encoding="utf-8-sig") as f:
        guides = list(csv.DictReader(f))
        assert all(g["meaning"] and g["unit"] and g["limitation"] and g["missing"] for g in guides)
    with (tmp_path / "components.csv").open(encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == 6*8
    with (tmp_path / "pairs.csv").open(encoding="utf-8-sig") as f:
        row = next(csv.DictReader(f))
        assert row["menu"] == row["text"] == "" and row["known_modalities"] == "2"


def test_meanings_explain_observed_denominators_and_unknowns(art, sess):
    cold = decision_metrics.export(sess)
    assert any("긍정 0건, 부정 0건" in text for text in cold["reading"])
    auc_gate = next(g for g in cold["gates"] if g["name"] == "validation_auc")
    assert "근거 부족으로 미측정" in auc_gate["explanation"]
    for sid, like in [("s0", True), ("s1", True), ("s2", False), ("s3", False)]:
        taste.record(sess, sid, like)
    result = decision_metrics.export(sess)
    p = result["personalization"]
    pairs = p["positive_predictions"]*p["negative_predictions"]
    assert any(f"{pairs}개 비교쌍" in line for line in result["reading"])
    assert any("상대적인 선별" in line for line in result["reading"])
    assert not result["decision"]["ready"]
    assert any("현재는 자동 권유를 보류합니다" in line for line in result["reading"])
    assert all("explanation" in g for g in result["gates"])


def test_combined_preference_uses_one_feedback_snapshot_and_filter_scope(monkeypatch):
    import main
    client = TestClient(main.app)
    uid = client.post("/users", json={}).json()["user_id"]
    cards = client.get("/cards?limit=2").json()
    original_scope = main._decision_scope
    calls = []
    def interleaved_scope(user_id):
        calls.append(user_id)
        # A later feedback arrives after this request's learning snapshot was captured.
        policy.record(policy.session_for(user_id), cards[1]["store"]["id"], False)
        return original_scope(user_id)
    monkeypatch.setattr(main, "_decision_scope", interleaved_scope)
    response = client.post("/preferences?include_decision=true&include_decision_metrics=true", json={
        "user_id": uid, "card_id": cards[0]["card_id"], "prefer": "like"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert calls == [uid]
    assert policy.session_for(uid)["taste"]["n"] == 2
    assert result["decision_metrics"]["decision"]["rated_count"] == 1
    assert result["decision"]["ready"] == result["decision_metrics"]["decision"]["ready"]
    assert result["decision"]["status"] == result["decision_metrics"]["decision"]["status"]
    assert result["decision_metrics"]["feedback_count"] == 1


def test_existing_location_filter_and_card_flow_share_metrics_scope():
    import main
    client = TestClient(main.app)
    uid = client.post("/users", json={}).json()["user_id"]
    assert client.put(f"/users/{uid}/filter", json={"cat": ["한식"], "within_m": 500}).status_code == 200
    missing = client.get(f"/users/{uid}/decision/metrics").json()
    assert missing["decision"]["status"] == "location_required"
    assert client.put(f"/users/{uid}/location", json={"lat": 37.5006, "lon": 127.0366}).status_code == 200
    card = client.get(f"/cards?user_id={uid}&limit=1").json()[0]
    response = client.post("/preferences?include_decision=true&include_decision_metrics=true", json={
        "user_id": uid, "card_id": card["card_id"], "prefer": "like"})
    assert response.status_code == 200, response.text
    data = response.json()["decision_metrics"]
    _, context, allow, tiers, bonus = main._decision_scope(uid)
    assert data["context"] == context
    assert data["context"]["categories"] == ["한식"] and data["context"]["within_m"] == 500
    assert data["candidates"]
    for c in data["candidates"]:
        assert c["store_id"] in allow
        assert c["tier"] == tiers.get(c["store_id"], 0)
        assert c["distance_bonus"] == pytest.approx(bonus.get(c["store_id"], 0))
