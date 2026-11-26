"""Compact client responses must preserve full analysis and avoid misleading scores."""
import pickle
from itertools import combinations
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.decision_summary_model import DecisionSummaryOut
from recsys import decision, decision_metrics, decision_summary, taste


def test_summary_matches_full_analysis_and_is_read_only(art, sess):
    for sid, label in [('s0', True), ('s2', True), ('s3', False), ('s4', False)]:
        taste.record(sess, sid, label)
    before = pickle.dumps(sess)
    full = decision_metrics.export(sess)
    data = decision_summary.summarize(sess, list(enumerate(art.ids)))
    DecisionSummaryOut.model_validate(data)
    p = full['personalization']
    assert data['personalization']['stability_score'] == (
        round(p['stability'] * 100, 1) if p['stability'] is not None else None)
    assert data['fit_count'] == full['fit']['qualified_count']
    pairs = [(a, b, decision_metrics.pair_metrics(art, a, b, decision.DEFAULT))
             for a, b in combinations(range(len(art.ids)), 2)]
    a, b, closest = max(pairs, key=lambda row: row[2]['raw_mean_similarity'])
    assert data['diversity']['card_ids'] == (a, b)
    assert data['diversity']['similarity_score'] == round(50 * (1 + closest['raw_mean_similarity']), 1)
    assert data['diversity']['passed'] == all(p['passes_current_rule'] for _, _, p in pairs)
    assert pickle.dumps(sess) == before


@pytest.mark.parametrize('cards', [[], [(0, 's0')]])
def test_small_response_runs_full_analysis_with_same_scope(art, sess, monkeypatch, cards):
    for sid, label in [('s0', True), ('s2', True), ('s3', False), ('s4', False)]:
        taste.record(sess, sid, label)
    scope = dict(allow={'s0', 's1', 's5'}, tiers={'s1': 2}, bonus={'s5': .1},
                 context={'location_required': False})
    expected = decision_metrics.export(sess, **scope)
    calls = []
    captured = []
    for module, name in [(decision, 'evaluate'), (decision, '_select'), (decision, '_scores')]:
        original = getattr(module, name)
        def counted(*args, _original=original, _name=name, **kwargs):
            calls.append((_name, kwargs))
            return _original(*args, **kwargs)
        monkeypatch.setattr(module, name, counted)
    original_export = decision_metrics.export
    def export(*args, **kwargs):
        assert all(kwargs[k] == v for k, v in scope.items())
        result = original_export(*args, **kwargs)
        captured.append(result)
        return result
    monkeypatch.setattr(decision_metrics, 'export', export)
    out = decision_summary.summarize(sess, cards, **scope)
    assert len(captured) == 1
    assert {'evaluate', '_select', '_scores'} <= {name for name, _ in calls}
    assert any(name == '_scores' and kw.get('loo') is True for name, kw in calls)
    for key in ('decision', 'personalization', 'fit', 'gates', 'slate', 'model'):
        assert captured[0][key] == expected[key]
    assert captured[0]['candidates'] == []
    assert out['fit_count'] == expected['fit']['qualified_count']
    assert out['diversity']['similarity_score'] is None


def test_summary_unknown_vectors_and_scope(art, sess, monkeypatch):
    with pytest.raises(ValueError):
        decision_summary.summarize(sess, [(999, 'missing')])
    data = decision_summary.summarize(sess, [(0, 's0')], allow=set(), context={'location_required': True})
    assert data['status'] == 'location_required' and data['fit_count'] is None
    monkeypatch.setattr(decision_summary.content, 'load', lambda: None)
    unavailable = decision_summary.summarize(sess, [])
    assert unavailable['status'] == 'unavailable' and unavailable['fit_count'] is None
    DecisionSummaryOut.model_validate(unavailable)


def test_http_summary_is_small_separate_and_validates_ids(monkeypatch):
    import main
    client = TestClient(main.app)
    uid = client.post('/users', json={}).json()['user_id']
    cards = client.get('/cards?limit=4').json()
    def forbidden(*args, **kwargs):
        pytest.fail('Summary must not build the detailed HTTP response')
    monkeypatch.setattr(main, '_decision_metrics_out', forbidden)
    url = f'/users/{uid}/decision/metrics/summary'
    params = [('card_ids', c['card_id']) for c in cards]
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert len(response.content) < 512
    data = response.json()
    assert set(data) == {'status', 'decision_readiness_score', 'personalization', 'fit_count', 'diversity'}
    assert set(data['personalization']) == {'ready', 'readiness_score', 'discrimination_score', 'stability_score'}
    assert set(data['diversity']) == {'passed', 'similarity_score', 'card_ids'}
    default = client.get(url).json()
    assert default['diversity'] == dict(passed=None, similarity_score=None, card_ids=None)
    assert client.get(url, params=params+params).json() == data
    assert client.get(url, params=list(reversed(params))).json() == data
    assert client.get(url, params=[params[0]]*21).status_code == 422
    assert client.get(url, params={'card_ids': -1}).status_code == 422
    assert client.get('/users/missing-summary-user/decision/metrics/summary').status_code == 404


def test_high_auc_without_enough_labels_is_unknown_not_a_high_score(art, sess):
    sess['hist'] = [{'personal_rank': .9, 'like': True}, {'personal_rank': .1, 'like': False}]
    assert decision_metrics.export(sess)['personalization']['validation_auc'] == 1
    summary = decision_summary.summarize(sess, [])
    assert summary['personalization'] == dict(ready=False, readiness_score=0, discrimination_score=None, stability_score=None)


@pytest.mark.parametrize('rank,expected', [(.9, 100.), (.1, 50.), (0., 0.)])
def test_discrimination_scale_and_no_false_personalization(art, sess, rank, expected):
    sess['hist'] = ([{'personal_rank': rank, 'like': True}] * 5
                    + [{'personal_rank': .1, 'like': False}] * 5)
    p = decision_summary.summarize(sess, [])['personalization']
    assert p['discrimination_score'] == expected
    assert p['ready'] is False  # a high score alone cannot establish personalization


def test_personalization_readiness_is_independent_of_candidate_availability(art, sess):
    for sid, label in [('s0', True), ('s1', True), ('s2', False), ('s3', False)]:
        taste.record(sess, sid, label)
    sess['hist'] = ([{'personal_rank': .9, 'like': True}] * 5
                    + [{'personal_rank': .1, 'like': False}] * 5)
    config = replace(decision.DEFAULT, min_rated=4, min_likes=2, stable_top=2)
    out = decision_summary.summarize(sess, [], allow=set(), config=config)
    assert out['personalization'] == dict(ready=True, readiness_score=100, discrimination_score=100., stability_score=100.)
    assert out['fit_count'] == 0
    assert not decision.evaluate(sess, allow=set(), config=config)['ready']
    location = decision_summary.summarize(sess, [], allow=set(), config=config,
                                         context={'location_required': True})
    assert location['personalization'] == out['personalization']
    assert location['fit_count'] is None
    sess['decision_checkpoints'] = []
    p = decision_summary.summarize(sess, [], config=config)['personalization']
    assert not p['ready'] and p['stability_score'] is None


def test_progress_score_drops_stability_while_ready_still_requires_it():
    """진행 점수는 순위 안정성을 빼고 재고, ready 는 여덟 조건을 그대로 요구한다."""
    assert decision_summary.PROGRESS_GATES == decision_summary.PERSONALIZATION_GATES - {'stability'}
    gates = [{'name': name, 'value': 1, 'threshold': 1, 'passed': True}
             for name in sorted(decision_summary.PROGRESS_GATES)]
    gates.append({'name': 'stability', 'value': .1, 'threshold': .6, 'passed': False})
    assert decision_summary._readiness_score({'gates': gates}) == 100
    # 안정성이 빠졌으므로 그 항목 없는 목록으로도 점수가 나온다.
    assert decision_summary._readiness_score({'gates': gates[:-1]}) == 100
    # 진행 점수에 쓰이는 조건 하나가 없으면 여전히 측정 불가다.
    assert decision_summary._readiness_score({'gates': gates[1:]}) is None


def test_failing_only_stability_keeps_progress_full_but_not_ready(art, sess):
    """학습 중 흔들리는 순위 안정성이 진행 막대를 잡지 않는다. 자동 권유 자격은 그대로 막힌다."""
    for sid, label in [('s0', True), ('s1', True), ('s2', False), ('s3', False)]:
        taste.record(sess, sid, label)
    sess['hist'] = ([{'personal_rank': .9, 'like': True}] * 5
                    + [{'personal_rank': .1, 'like': False}] * 5)
    config = replace(decision.DEFAULT, min_rated=4, min_likes=2, stable_top=2)
    passing = decision_summary.summarize(sess, [], config=config)['personalization']
    assert passing['ready'] is True and passing['readiness_score'] == 100
    # 관측된 안정성보다 높은 기준을 줘서 **안정성만** 미달로 만든다.
    strict = replace(config, min_overlap=passing['stability_score'] / 100 + .01)
    out = decision_summary.summarize(sess, [], config=strict)['personalization']
    assert out['ready'] is False
    assert out['readiness_score'] == 100
    assert out['stability_score'] == passing['stability_score']
    # 상세 분석에는 안정성 게이트가 기준·완화 전 기준과 함께 그대로 남는다.
    gate = next(g for g in decision_metrics.export(sess, config=strict)['gates']
                if g['name'] == 'stability')
    assert gate['passed'] is False and gate['baseline_threshold'] == strict.min_overlap
    # 스와이프마다 도는 빠른 경로도 상세 분석과 같은 값을 낸다.
    assert decision_summary.measure_readiness(sess, strict) == 100


def test_unavailable_fit_is_not_zero(art, sess):
    for sid in art.ids:
        taste.record(sess, sid, True)
    assert decision_summary.summarize(sess, [])['fit_count'] is None


def test_closest_pair_is_not_hidden_by_many_different_pairs(art, sess):
    for name in decision_metrics.MODALITIES:
        matrix = getattr(art, 'sim_' + name)
        matrix[:] = .05
        matrix[0, 1] = matrix[1, 0] = .98
    d = decision_summary.summarize(sess, list(enumerate(art.ids)))['diversity']
    assert d == dict(passed=False, similarity_score=99., card_ids=(0, 1))


def test_unknown_pair_prevents_a_misleading_best_measured_pair(art, sess):
    for name in ('menu', 'text', 'food'):
        matrix = getattr(art, 'sim_' + name)
        matrix[0, 2] = matrix[2, 0] = np.nan
    d = decision_summary.summarize(sess, [(0, 's0'), (1, 's1'), (2, 's2')])['diversity']
    assert d == dict(passed=False, similarity_score=None, card_ids=None)


@pytest.mark.parametrize('mean,expected', [(-1., 0.), (0., 50.), (1., 100.)])
def test_similarity_scale_preserves_negative_cosines(art, sess, mean, expected):
    for name in decision_metrics.MODALITIES:
        matrix = getattr(art, 'sim_' + name)
        matrix[0, 2] = matrix[2, 0] = mean
    d = decision_summary.summarize(sess, [(0, 's0'), (2, 's2')])['diversity']
    assert d['similarity_score'] == expected


def test_low_similarity_does_not_override_brand_exclusion(art, sess):
    for name in decision_metrics.MODALITIES:
        matrix = getattr(art, 'sim_' + name)
        matrix[0, 1] = matrix[1, 0] = .01
    d = decision_summary.summarize(sess, [(0, 's0'), (1, 's1')])['diversity']
    assert d['similarity_score'] == pytest.approx(50.5)
    assert d['passed'] is False


@pytest.mark.parametrize('minimum,expected', [(4, 100), (6, 66), (8, 50)])
def test_additional_readiness_uses_existing_requirements_and_preserves_measurements(art, sess, minimum, expected):
    for sid, label in [('s0', True), ('s1', True), ('s2', False), ('s3', False)]:
        taste.record(sess, sid, label)
    sess['hist'] = ([{'personal_rank': .9, 'like': True}] * 5
                    + [{'personal_rank': .1, 'like': False}] * 5)
    config = replace(decision.DEFAULT, min_rated=minimum, min_likes=2, stable_top=2)
    p = decision_summary.summarize(sess, [], config=config)['personalization']
    assert p == dict(ready=(minimum == 4), readiness_score=expected,
                    discrimination_score=100., stability_score=100.)
    # A weak required measure cannot be compensated by perfect stability.
    for row in sess['hist']:
        row['personal_rank'] = .5
    p = decision_summary.summarize(sess, [], config=config)['personalization']
    # 진행률은 가장 덜 찬 조건이 정한다. AUC 0.5 를 문턱으로 나눈 값이 그 조건이고,
    # 문턱이 0.80 -> 0.70 으로 내려갔으므로 상한도 62 -> 71 로 올라간다(숫자를 그대로
    # 적어 두면 `Config` 를 바꿔도 이 테스트가 잡지 못한다 — 문턱에서 계산한다).
    cap = int(100 * .5 / decision.DEFAULT.min_validation_auc)
    assert cap == 71
    assert p['readiness_score'] == min(expected, cap) and not p['ready']
    assert p['discrimination_score'] == 50. and p['stability_score'] == 100.


def test_readiness_does_not_round_a_failed_gate_to_ready(art, sess):
    for sid, label in [('s0', True), ('s1', True), ('s2', False), ('s3', False)]:
        taste.record(sess, sid, label)
    sess['hist'] = ([{'personal_rank': .5, 'like': True}] * 5
                    + [{'personal_rank': .5, 'like': False}] * 5)
    config = replace(decision.DEFAULT, min_rated=4, min_likes=2, stable_top=2, min_validation_auc=.500001)
    p = decision_summary.summarize(sess, [], config=config)['personalization']
    assert p['readiness_score'] == 99 and not p['ready']


def test_readiness_unknown_analysis_differs_from_unprepared(art, sess, monkeypatch):
    assert decision_summary.summarize(sess, [])['personalization']['readiness_score'] == 0
    monkeypatch.setattr(decision_summary.content, 'load', lambda: None)
    assert decision_summary.summarize(sess, [])['personalization']['readiness_score'] is None


def test_compatibility_score_does_not_award_unvalidated_swipes(art, sess):
    assert decision_summary.summarize(sess, [])['decision_readiness_score'] == 0
    taste.record(sess, 's0', True)
    out = decision_summary.summarize(sess, [])
    assert out['personalization']['readiness_score'] == 0
    assert out['decision_readiness_score'] == out['personalization']['readiness_score'] == 0
    # Repeated answers cannot replace distinct swipe evidence.
    sess['n'] = 1000
    assert decision_summary.summarize(sess, [])['decision_readiness_score'] == 0


@pytest.mark.parametrize('n', [1, 2, 4, 6, 20])
def test_taste_evidence_is_independent_of_slate_availability_and_count(art, sess, monkeypatch, n):
    for sid, label in [('s0', True), ('s1', True), ('s2', False), ('s3', False)]:
        taste.record(sess, sid, label)
    sess['hist'] = ([{'personal_rank': .9, 'like': True}] * 5
                    + [{'personal_rank': .1, 'like': False}] * 5)
    scores = np.arange(len(art.ids), dtype=float)
    monkeypatch.setattr(decision, '_scores', lambda *a, **kw: (scores, scores, np.zeros_like(scores)))
    config = replace(decision.DEFAULT, count=n, min_rated=4, min_likes=2, stable_top=2, fit_quantile=0)
    full = decision_metrics.export(sess, config=config)
    out = decision_summary.summarize(sess, [], config=config)
    assert out['personalization']['readiness_score'] == 100
    # 최상위 점수의 80 은 `personalization.ready` 전용이다(앱 화면 100%).
    assert out['personalization']['ready'] and out['decision_readiness_score'] == 80
    assert full['decision']['ready'] == (n <= 2)
    assert out['decision_readiness_score'] == decision_summary.summarize(
        sess, [(0, 's0'), (1, 's1')], config=config)['decision_readiness_score']
    # Personalized, but no suitable candidates: not decision-ready.
    none = decision_summary.summarize(sess, [], allow=set(), config=config)
    assert none['personalization'] == out['personalization']
    assert none['decision_readiness_score'] == out['decision_readiness_score'] == 80
    # Duplicate brands prevent a slate, but do not change taste evidence.
    art.brand[:] = ['same'] * len(art.ids)
    duplicate = decision_summary.summarize(sess, [], config=config)
    if n == 2:
        assert duplicate['fit_count'] == 2
        assert duplicate['decision_readiness_score'] == 80
        assert not decision.evaluate(sess, config=config)['ready']


def test_taste_evidence_survives_missing_location_but_not_missing_analysis(art, sess, monkeypatch):
    for sid, label in [('s0', True), ('s1', True), ('s2', False), ('s3', False)]:
        taste.record(sess, sid, label)
    sess['hist'] = ([{'personal_rank': .9, 'like': True}] * 5
                    + [{'personal_rank': .1, 'like': False}] * 5)
    config = replace(decision.DEFAULT, min_rated=4, min_likes=2, stable_top=2)
    out = decision_summary.summarize(sess, [], config=config, context={'location_required': True})
    assert out['status'] == 'location_required'
    assert out['personalization']['readiness_score'] == 100
    assert out['personalization']['ready'] and out['decision_readiness_score'] == 80
    monkeypatch.setattr(decision_summary.content, 'load', lambda: None)
    assert decision_summary.summarize(sess, [])['decision_readiness_score'] is None


def test_http_final_progress_count_and_existing_fields(monkeypatch):
    import main
    client = TestClient(main.app)
    uid = client.post('/users', json={}).json()['user_id']
    url = f'/users/{uid}/decision/metrics/summary'
    default = client.get(url).json()
    assert default['decision_readiness_score'] == 0
    assert client.get(url+'?n=4').json() == default
    seen = []
    original = decision_summary.summarize
    def capture(*args, **kwargs):
        seen.append(kwargs['config'].count)
        return original(*args, **kwargs)
    monkeypatch.setattr(decision_summary, 'summarize', capture)
    for n in (1, 6, 20):
        response = client.get(url, params={'n': n})
        assert response.status_code == 200 and len(response.content) < 512
        assert response.json()['personalization'] == default['personalization']
    assert seen == [1, 6, 20]
    for n in (0, 21, 'bad'):
        assert client.get(url, params={'n': n}).status_code == 422
    schema = client.get('/openapi.json').json()
    prop = schema['components']['schemas']['DecisionSummaryOut']['properties']['decision_readiness_score']
    assert any(t.get('type') == 'integer' and t['minimum'] == 0 and t['maximum'] == 100 for t in prop['anyOf'])


@pytest.fixture
def expressive_progress_case(art, sess, monkeypatch):
    for sid, label in [('s0', True), ('s1', True), ('s2', False), ('s3', False)]:
        taste.record(sess, sid, label)
    sess['hist'] = ([{'personal_rank': .9, 'like': True} for _ in range(5)]
                    + [{'personal_rank': .5, 'like': False} for _ in range(5)])
    values = np.ones(len(art.ids))
    monkeypatch.setattr(decision, '_scores', lambda *a, **kw: (values, values, np.zeros_like(values)))
    monkeypatch.setattr(decision, '_fine_groups', lambda a: {sid: sid for sid in a.ids})
    art.brand[:] = art.ids
    for name in decision_metrics.MODALITIES:
        getattr(art, 'sim_' + name)[:] = 0
    config = replace(decision.DEFAULT, count=4, min_rated=4, min_likes=2, stable_top=2, fit_quantile=0)
    return art, sess, config


def test_low_discrimination_cannot_hide_behind_other_passed_conditions(expressive_progress_case):
    _, sess, config = expressive_progress_case
    sess['hist'] = ([{'personal_rank': .9, 'like': True}] * 2
                    + [{'personal_rank': .1, 'like': True}] * 5
                    + [{'personal_rank': .5, 'like': True}]
                    + [{'personal_rank': .5, 'like': False}] * 16)
    full = decision_metrics.export(sess, config=config)
    assert full['personalization']['validation_auc'] == .3125
    assert full['fit']['qualified_count'] >= config.count
    assert len(full['slate']['diagnostic_store_ids']) == config.count
    result = decision_summary.summarize(sess, [], config=config)
    # 39 -> 44: AUC 0.3125 를 문턱으로 나눈 값이 가장 덜 찬 조건이고, 문턱이
    # 0.80 -> 0.70 으로 내려갔다. "낮은 구분력이 다른 조건 뒤에 숨지 못한다" 는 이
    # 테스트의 뜻은 그대로다 — 44 도 통과선이 아니고 `ready` 는 여전히 거짓이다.
    assert result['personalization']['readiness_score'] == int(100 * .3125 / decision.DEFAULT.min_validation_auc) == 44
    # 최상위 점수는 진행률이 아니라 적중 누적이다. 이 세션은 `advance_display` 를 한 번도
    # 거치지 않았으므로 누적이 0 이고, `ready` 가 거짓이라 80 도 아니다.
    assert not result['personalization']['ready']
    assert result['decision_readiness_score'] == 0
    assert not full['decision']['ready']


@pytest.mark.parametrize('n', [2, 4, 6])
def test_taste_evidence_does_not_require_spare_candidates_or_swipe_target(expressive_progress_case, n):
    _, sess, config = expressive_progress_case
    config = replace(config, count=n)
    assert decision.evaluate(sess, config=config)['ready']
    assert decision_summary.summarize(sess, [], config=config)['decision_readiness_score'] == 80
    # Moving a fatigue milestone cannot inflate or suppress taste evidence.
    config = replace(config, relax_after=4)
    assert decision_summary.summarize(sess, [], config=config)['decision_readiness_score'] == 80


def test_taste_evidence_tracks_the_strict_quality_threshold(expressive_progress_case):
    _, sess, config = expressive_progress_case
    # 예전에는 좋아요 한 장을 rank .1 로 내려 AUC 0.80(= 당시 문턱)을 만들었다. 문턱을
    # 실측값 0.70 으로 내렸으므로 그 자리도 옮긴다 — 좋아요 세 장을 싫어요와 같은
    # 순위(.5)로 두면 동점이 쌍당 0.5 로 세어져 25쌍 중 17.5쌍, 정확히 AUC 0.70 이다.
    for row in sess['hist'][:3]:
        row['personal_rank'] = .5
    assert decision.DEFAULT.min_validation_auc == .70
    assert decision_metrics.export(sess, config=config)['personalization']['validation_auc'] == pytest.approx(.70)
    out = decision_summary.summarize(sess, [], config=config)
    assert decision.evaluate(sess, config=config)['ready']
    assert out['decision_readiness_score'] == 80
    strict = replace(config, min_validation_auc=.700001)
    assert not decision.evaluate(sess, config=strict)['ready']
    # 기준을 머리카락만큼 올리면 `ready` 가 거짓이 되고, 화면은 더 이상 100% 가 아니다.
    tight = decision_summary.summarize(sess, [], config=strict)
    assert tight['personalization']['readiness_score'] == 99
    assert not tight['personalization']['ready'] and tight['decision_readiness_score'] < 80
    for row in sess['hist'][:3]:
        row['personal_rank'] = .9
    assert decision_summary.summarize(sess, [], config=config)['decision_readiness_score'] == 80


def test_taste_evidence_is_independent_of_restaurant_pair_similarity(expressive_progress_case):
    art, sess, config = expressive_progress_case
    config = replace(config, count=2, relax_after=4)
    assert decision_summary.summarize(sess, [], config=config)['decision_readiness_score'] == 80
    for name in decision_metrics.MODALITIES:
        getattr(art, 'sim_' + name)[:] = .7
    assert decision.evaluate(sess, config=config)['ready']
    assert decision_summary.summarize(sess, [], config=config)['decision_readiness_score'] == 80
    art.sim_menu[:] = .9
    assert not decision.evaluate(sess, config=config)['ready']
    assert decision_summary.summarize(sess, [], config=config)['decision_readiness_score'] == 80


@pytest.mark.parametrize('history', [[], [{'personal_rank': .1, 'like': True}, {'personal_rank': .9, 'like': False}]])
def test_relaxed_decision_does_not_inflate_personalization_or_hide_failed_gates(expressive_progress_case, history):
    _, sess, config = expressive_progress_case
    config = replace(config, relax_after=3, relax_until=4)
    sess['hist'] = history
    full = decision_metrics.export(sess, config=config)
    summary = decision_summary.summarize(sess, [], config=config)
    assert full['decision'] == decision.evaluate(sess, config=config)
    assert full['decision']['ready']
    gate = next(g for g in full['gates'] if g['name'] == 'validation_auc')
    assert not gate['required'] and gate['passed'] is not True
    assert summary['decision_readiness_score'] == 0
    assert not summary['personalization']['ready']
    assert summary['personalization']['readiness_score'] == 0
    assert summary['personalization']['discrimination_score'] is None
    assert all(g['passed'] is True for g in full['gates'] if g['required'])
    assert decision_summary.summarize(sess, [], config=config, allow=set())['decision_readiness_score'] == 0


def test_more_feedback_alone_does_not_imply_validated_taste(art, sess):
    config = replace(decision.DEFAULT, min_rated=2, relax_after=4, relax_until=6)
    for sid in art.ids:
        taste.record(sess, sid, False)
        out = decision_summary.summarize(sess, [], config=config)
        assert not decision.evaluate(sess, config=config)['ready']
        assert out['decision_readiness_score'] == out['personalization']['readiness_score'] == 0


def test_repeated_card_and_inherited_memory_do_not_create_validation(art, sess):
    config = replace(decision.DEFAULT, min_rated=2, relax_after=4, relax_until=6)
    for _ in range(60):
        taste.record(sess, 's0', False)
    out = decision_summary.summarize(sess, [], config=config)
    assert out['decision_readiness_score'] == 0
    fresh = taste.start_from(sess)
    assert fresh['memory']
    assert decision_summary.summarize(fresh, [], config=config)['decision_readiness_score'] == 0


@pytest.mark.parametrize('rated', [16, 30, 60, 100])
def test_long_session_does_not_lift_weak_quality_score(expressive_progress_case, rated):
    _, sess, config = expressive_progress_case
    full = decision_metrics.export(sess, config=config)
    # Frozen analysis isolates the display policy from model training. Advancing
    # counts or disabling an automatic gate must never manufacture quality.
    full['feedback_count'] = full['personalization']['rated_count'] = rated
    for g in full['gates']:
        if g['name'] == 'rated_count':
            g.update(value=rated, passed=True)
        elif g['name'] == 'validation_auc':
            g.update(value=.3125, threshold=.6, baseline_threshold=.8,
                     passed=False, required=False)
    full['personalization']['validation_auc'] = .3125
    full['decision']['ready'] = True
    before = pickle.dumps(full)
    assert decision_summary._decision_readiness_score(full, config) == 39
    assert pickle.dumps(full) == before
