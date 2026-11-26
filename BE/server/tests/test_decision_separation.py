"""Detail export must remain independent from existing preference/proposal calls."""
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from recsys import policy


def test_existing_save_and_proposal_do_not_call_detailed_export(monkeypatch):
    client = TestClient(main.app)
    uid = client.post('/users', json={}).json()['user_id']
    card = client.get(f'/cards?user_id={uid}&limit=1').json()[0]

    def forbidden(*args, **kwargs):
        raise AssertionError('Existing API must not require detailed metrics')

    monkeypatch.setattr(main, '_decision_metrics_out', forbidden)
    body = {'user_id': uid, 'card_id': card['card_id'], 'prefer': 'like'}
    saved = client.post('/preferences', json=body)
    assert saved.status_code == 200
    assert saved.json()['decision'] is None
    assert saved.json()['decision_metrics'] is None
    combined = client.post('/preferences?include_decision=true', json=body)
    assert combined.status_code == 200
    assert combined.json()['decision_metrics'] is None
    proposal = client.get(f'/users/{uid}/decision')
    assert proposal.status_code == 200
    assert 'assessment' not in proposal.json() and 'next_action' not in proposal.json()


def test_detail_is_read_only_and_keeps_meanings():
    client = TestClient(main.app)
    uid = client.post('/users', json={}).json()['user_id']
    sess = policy.session_for(uid)
    before = sess['taste']['n']
    response = client.get(f'/users/{uid}/decision/metrics?candidate_limit=2')
    assert response.status_code == 200
    data = response.json()
    assert data['reading'] and data['metric_guide']
    assert all(g['explanation'] for g in data['gates'])
    assert len(data['candidates']) == 2
    assert sess['taste']['n'] == before


def test_detail_failure_does_not_prevent_existing_save_or_proposal(monkeypatch):
    client = TestClient(main.app)
    uid = client.post('/users', json={}).json()['user_id']
    card = client.get('/cards?limit=1').json()[0]

    def unavailable(*args, **kwargs):
        raise HTTPException(503, 'detail unavailable')

    monkeypatch.setattr(main, '_decision_metrics_out', unavailable)
    assert client.get(f'/users/{uid}/decision/metrics').status_code == 503
    assert client.post('/preferences?include_decision=true', json={
        'user_id': uid, 'card_id': card['card_id'], 'prefer': 'like'}).status_code == 200
    assert client.get(f'/users/{uid}/decision').status_code == 200


def test_openapi_keeps_original_proposal_contract():
    schemas = TestClient(main.app).get('/openapi.json').json()['components']['schemas']
    assert 'DecisionAssessmentOut' not in schemas
    assert 'assessment' not in schemas['DecisionOut']['properties']
    assert 'next_action' not in schemas['DecisionOut']['properties']
    assert 'fit' not in schemas['DecisionCardOut']['properties']
    assert 'reading' in schemas['DecisionMetricsOut']['properties']
