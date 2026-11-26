"""Verify deployed contracts; writes only with explicit --isolated-write on a staging instance.

python scripts/verify_decision_deploy.py --base-url http://127.0.0.1:8000 --user-id ID
python scripts/verify_decision_deploy.py --base-url http://staging:8000 --isolated-write
Output contains checks/counts only, never user IDs or preference history.
"""
import argparse
import json
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def verify(base_url, user_id=None, isolated_write=False):
    base_url = base_url.rstrip("/")
    checks = {}
    def request(path, method="GET", payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(base_url+path, data=data, method=method, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw), {k.lower(): v for k, v in response.headers.items()}
    for path in ("/healthz", "/readyz"):
        request(path)
        checks[path] = 200
    schema, _ = request("/openapi.json")
    route = "/users/{user_id}/decision/metrics"
    assert route in schema["paths"]
    query = {p["name"] for p in schema["paths"]["/preferences"]["post"]["parameters"]}
    assert {"include_decision", "include_decision_metrics"} <= query
    model = schema["components"]["schemas"]["DecisionMetricsOut"]["properties"]
    assert {"reading", "metric_guide", "gates", "candidates", "pairs"} <= set(model)
    checks["schema_and_meaning_fields"] = True
    if isolated_write:
        user, _ = request("/users", "POST", {})
        user_id = user["user_id"]
        request(f"/users/{quote(user_id)}/filter", "PUT", {"cat": ["한식"], "within_m": 500})
        missing, _ = request(f"/users/{quote(user_id)}/decision/metrics")
        assert missing["decision"]["status"] == "location_required"
        request(f"/users/{quote(user_id)}/location", "PUT", {"lat": 37.5006, "lon": 127.0366})
        cards, _ = request("/cards?"+urlencode({"user_id": user_id, "limit": 2}))
        assert len(cards) == 2
        for i, card in enumerate(cards, 1):
            result, _ = request("/preferences?include_decision=true&include_decision_metrics=true", "POST", {
                "user_id": user_id, "card_id": card["card_id"], "prefer": "like" if i == 1 else "dislike"})
            metrics = result["decision_metrics"]
            assert metrics["feedback_count"] == i
            assert result["decision"]["status"] == metrics["decision"]["status"]
            assert result["decision"]["ready"] == metrics["decision"]["ready"]
            assert metrics["context"]["within_m"] == 500
            assert metrics["reading"] and metrics["metric_guide"]
        checks["isolated_existing_card_filter_location_preference_flow"] = True
    if user_id:
        path = f"/users/{quote(user_id)}/decision/metrics?candidate_limit=2&download=true"
        data, headers = request(path)
        assert data["reading"] and len(data["metric_guide"]) >= 27
        assert all(g["explanation"] for g in data["gates"])
        assert "attachment" in headers.get("content-disposition", "")
        checks.update(metrics_http=200, restored_feedback_count=data["feedback_count"],
                      candidate_total=data["candidate_total"], meaning_entries=len(data["metric_guide"]),
                      current_reading_present=True, download_attachment=True)
        before = data["snapshot_id"]
        again, _ = request(path)
        assert again["snapshot_id"] == before, "Feedback or scope changed during verification; retry read-only probe"
        checks["stable_read_only_snapshot"] = True
    return checks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--user-id")
    parser.add_argument("--isolated-write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.base_url, args.user_id, args.isolated_write), ensure_ascii=False))
