"""Hit accumulation follows learned feedback, never HTTP polling or raw diagnostics."""
import copy
import pickle
from dataclasses import replace

import numpy as np
import pytest

from app import recommendation_sessions, snapshot, state, users
from recsys import content, decision, decision_metrics, decision_summary, policy, taste
from test_decision_manual_api import manual_api, session
from test_user_sessions import api, new, path, person, react, replay


DISPLAY = decision_summary.DISPLAY_SCORE_KEY
HIT_SCORE = decision_summary.HIT_SCORE_KEY
HIT_STREAK = decision_summary.HIT_STREAK_KEY
BOOST = decision_summary.HIT_BOOST_KEY
SCALARS = (DISPLAY, HIT_SCORE, HIT_STREAK, BOOST)


def display_rule(sess, config=decision.DEFAULT):
    """규칙이 지시하는 표시값. 세션에 적힌 값이 이것과 어긋나면 화면과 판정이 갈라진다.

    예전에는 여기서 `round(누적)` 을 직접 썼다. 누적이 양수인데 0.5 미만이면 `round` 가
    0 을 돌려주는데 그 0 은 "아직 아무것도 맞히지 못했다" 와 구분되지 않는다 — 그래서
    변환 규칙을 `decision_summary.shown_score` 로 옮겼고 여기서도 그것을 부른다.
    """
    return (decision_summary.READY_SCORE if decision_summary.measure_ready(sess, config)
            else decision_summary.shown_score(sess[HIT_SCORE]))


@pytest.mark.parametrize('stable_top', [1, 2, 20])
def test_incremental_raw_measure_matches_full_analysis(art, sess, stable_top):
    config = replace(decision.DEFAULT, min_rated=3, min_likes=1,
                     stable_top=stable_top, relax_after=2, relax_until=4)
    # Covers ordinary feedback, opposite reassessment, duplicate cards and fatigue.
    for sid, label in [('s0', True), ('s1', False), ('s2', True), ('s3', False),
                       ('s0', False), ('s1', True), ('s1', True), ('s5', False)]:
        taste.record(sess, sid, label)
        full = decision_metrics.export(sess, config=config)
        assert decision_summary.measure_readiness(sess, config) == decision_summary._decision_readiness_score(full, config)


@pytest.mark.parametrize('stable_top', [1, 2, 20])
def test_incremental_ready_matches_full_analysis_inside_and_outside_relaxation(art, sess, stable_top):
    """스와이프마다 도는 경량 `ready` 와 `summarize` 의 `ready` 가 모든 입력에서 같다.

    이 등가성이 깨지면 앱 화면의 100%(API 80)와 서버의 `personalization.ready` 가
    갈라진다 — 이 브랜치가 없애려는 바로 그 상태다. 완화 구간(relax_after 이후)은
    `export` 가 기준을 낮춘 뒤 `_strict_gates` 로 되살리는 자리라 특히 어긋나기 쉽다.
    """
    config = replace(decision.DEFAULT, min_rated=3, min_likes=1, min_validation_each=1,
                     stable_top=stable_top, relax_after=2, relax_until=6)
    seen = set()
    for sid, label in [('s0', True), ('s1', False), ('s2', True), ('s3', False),
                       ('s0', False), ('s1', True), ('s4', True), ('s5', False),
                       ('s2', False), ('s3', True), ('s4', False), ('s5', True)]:
        taste.record(sess, sid, label)
        light = decision_summary.measure_ready(sess, config)
        assert light is decision_summary.summarize(sess, [], config=config)['personalization']['ready']
        seen.add((light, decision.relaxation(sess, config) > 0))
    # 완화 전·후 양쪽을 실제로 지났다 — 기준이 낮아지는 구간도 같이 쟀다는 증거다.
    assert {relaxed for _, relaxed in seen} == {False, True}


def test_incremental_ready_matches_full_analysis_when_every_condition_passes(art, sess):
    """`ready` 가 참이 되는 순간에도 두 경로가 같다 — 거짓만 보고 등가라 할 수 없다."""
    config = replace(decision.DEFAULT, min_rated=3, min_likes=1, min_validation_each=1,
                     min_validation_auc=0., stable_top=1, min_overlap=0.)
    seen = set()
    for sid, label in [('s0', True), ('s1', False), ('s2', True), ('s3', False)]:
        taste.record(sess, sid, label)
        light = decision_summary.measure_ready(sess, config)
        assert light is decision_summary.summarize(sess, [], config=config)['personalization']['ready']
        seen.add(light)
    assert seen == {False, True}


def test_display_updates_after_learning_and_does_not_mutate_analysis(manual_api):
    uid, sid, base = session(manual_api)
    previous = 0.
    for i, label in enumerate([True, False, True, False, True]):
        react(manual_api, uid, sid, i, label)
        live = policy.session_for(sid)['taste']
        snap = policy.taste_snapshot(policy.session_for(sid))
        before = decision_metrics.export(snap)
        model = copy.deepcopy(snap)
        for key in SCALARS:
            model.pop(key, None)
        # Removing the display fields cannot alter prediction, gates or decisions.
        assert decision_metrics.export(model) == before
        result = manual_api.get(base + '/decision/metrics/summary').json()
        # 화면 값은 규칙이 지시하는 값 그대로다 — 완충도, 조회로 생기는 차이도 없다.
        assert result['decision_readiness_score'] == display_rule(live) == live[DISPLAY]
        assert result['personalization']['ready'] is decision_summary.measure_ready(live)
        assert result['personalization']['readiness_score'] == decision_summary._decision_readiness_score(before, decision.DEFAULT)
        # 적중 누적은 그 답의 credit 부호를 따라 움직인다(천장·바닥에 붙어 있지 않은 한).
        row = live['hist'][-1]
        rank = row['personal_rank']
        if rank is not None and 0 < live[HIT_SCORE] < decision_summary.CEILING_BELOW_READY:
            credit = (2 * rank - 1) if row['like'] else (1 - 2 * rank)
            assert (live[HIT_SCORE] > previous) is (credit > 0)
        previous = live[HIT_SCORE]
    # Cross-session user memory is not a second UI counter, but it now keeps the score.
    # 예전에는 이 자리가 "기억 모델은 끝까지 0" 을 잠그고 있었다. 그 전제는 점수가
    # 게이트 진행률이던 시절 것이다 — 그때는 세션마다 다시 재는 값이라 계정 기억이
    # 들고 있을 이유가 없었다. 지금은 새 세션(`apply_created`, 앱의 `POST /user/{uid}/session`)이
    # 이 모델의 누적에서 **배속을 계산해** 물려받는 승계 원본이므로, 0 으로 잠그면
    # 앱이 실제로 타는 경로에서만 승계가 죽는다(새 세션의 점수는 0 에서 시작하지만,
    # 그 0 이 얼마나 빨리 오르는지를 이 누적이 정한다).
    # "UI 카운터가 아니다" 는 부분은 그대로다 — 이 모델은 정책 세션이 아니라
    # 어느 엔드포인트도 이 값을 내보내지 않는다.
    memory = recommendation_sessions._user_memory[uid]['taste']
    # 표시값은 `shown_score(누적)` 이다 — 이 모델은 `judge_ready=False` 로 접으므로
    # 여덟 조건을 재지 않고, 따라서 80(=`ready`)을 주장하지 않는다.
    #
    # 예전에는 여기가 `round(누적)` 이었다. 이 답 순서의 누적은 0.2 라 `round` 가 0 을
    # 돌려주는데, 그 0 은 "아직 아무것도 맞히지 못했다" 와 구분되지 않는다 — 실제로
    # 이 사용자는 맞힌 것이 있다. 양수 누적은 최소 1 로 적는다.
    assert memory[HIT_SCORE] > 0 and memory[DISPLAY] == decision_summary.shown_score(memory[HIT_SCORE])
    assert 0 < memory[HIT_SCORE] < .5 and memory[DISPLAY] == 1
    assert uid not in policy.sessions()      # 기억 모델은 조회되는 세션이 아니다


def test_polling_scope_changes_and_rejected_feedback_cannot_move_the_display(manual_api):
    uid, sid, base = session(manual_api)
    react(manual_api, uid, sid, 0)
    react(manual_api, uid, sid, 1, False)
    summary = base + '/decision/metrics/summary'
    before = pickle.dumps(policy.taste_snapshot(policy.session_for(sid)))
    settled = policy.session_for(sid)['taste'][DISPLAY]
    durable = state.PREFS.read_bytes()
    for n in (1, 4, 20, 4):
        response = manual_api.get(summary, params={'n': n, 'card_ids': [0, 1]})
        assert response.status_code == 200
        assert response.json()['decision_readiness_score'] == settled
    assert manual_api.put(base + '/filter', json={'cat': ['일식']}).status_code == 200
    assert manual_api.put(base + '/location', json={'lat': 37.5, 'lon': 127.}).status_code == 200
    assert manual_api.get(summary).json()['decision_readiness_score'] == settled
    for body in ({'card_id': 9999, 'prefer': 'like'}, {'card_id': 0, 'prefer': 'bad'}):
        assert manual_api.post(base + '/preferences', json=body).status_code in (400, 404, 422)
    other = person(manual_api)
    assert manual_api.post(path(other, sid) + '/preferences', json={'card_id': 2, 'prefer': 'like'}).status_code == 404
    # A known card outside the recommendation vector pool cannot advance it.
    policy.record(policy.session_for(sid), 'outside-pool', True)
    assert pickle.dumps(policy.taste_snapshot(policy.session_for(sid))) == before
    assert state.PREFS.read_bytes() == durable
    # Opposite feedback is a real accepted update, not a poll or rejected request.
    react(manual_api, uid, sid, 0, False)
    live = policy.session_for(sid)['taste']
    assert manual_api.get(summary).json()['decision_readiness_score'] == display_rule(live)


def test_new_sessions_start_at_zero_with_an_inherited_boost_and_full_replay_restores_it(manual_api):
    """새 세션의 점수는 0 에서 시작하고, 물려받는 것은 **증가 속도(배속)** 다.

    예전에는 이 자리가 "계정 기억 누적의 절반을 물려받는다" 였다. 그 규칙에서는 한 장도
    답하지 않은 세션의 화면에 퍼센트가 이미 적혀 있었다 — 이 세션이 맞힌 것이 아직 없는데
    30% 라고 말하는 것이라 거짓이다. 지금은 0 에서 시작하고, 대신 적중한 장의 증가폭에
    배속이 곱해져 같은 답으로 더 빨리 오른다.
    """
    uid, a, base_a = session(manual_api)
    for card in (0, 1, 2, 3):
        react(manual_api, uid, a, card, card % 2 == 0)
    memory = recommendation_sessions._user_memory[uid]['taste']
    b = new(manual_api, uid)
    carried = policy.session_for(b)['taste']
    assert carried[HIT_SCORE] == 0.          # 누적은 물려주지 않는다
    assert carried[HIT_STREAK] == 0
    assert carried[BOOST] == pytest.approx(decision_summary.carry_boost(memory[HIT_SCORE]))
    # 화면도 0% 에서 시작한다 — 새 세션이 아직 맞힌 것은 없다.
    assert (manual_api.get(path(uid, b) + '/decision/metrics/summary')
            .json()['decision_readiness_score']) == 0
    score_a = policy.session_for(a)['taste'][DISPLAY]
    assert manual_api.get(base_a + '/decision/metrics/summary').json()['decision_readiness_score'] == score_a
    react(manual_api, uid, b, 4)
    react(manual_api, uid, a, 5)
    before = {sid: policy.taste_snapshot(policy.session_for(sid)) for sid in (a, b)}
    raw = state.PREFS.read_bytes()
    replay()
    assert state.PREFS.read_bytes() == raw
    # 재생 결정성 — 같은 입력이면 네 스칼라(표시·누적·연속·배속)가 모두 되돌아온다.
    # 배속이 빠지면 같은 답을 재생해도 이후 궤적이 달라지므로 한 벌로 묶어 잠근다.
    for sid, expected in before.items():
        actual = policy.taste_snapshot(policy.session_for(sid))
        for key in SCALARS:
            assert actual[key] == expected[key], key
        np.testing.assert_array_equal(actual['m'], expected['m'])
        np.testing.assert_array_equal(actual['v'], expected['v'])
    assert before[a][DISPLAY] == display_rule(policy.session_for(a)['taste'])
    assert before[b][DISPLAY] == display_rule(policy.session_for(b)['taste'])


# 상위 품질 카드에 좋아요, 하위에 싫어요 — 검증 카탈로그에서 예측과 응답이
# 또렷하게 같은 방향이 되어 적중 누적이 실제로 쌓이는 순서다(20장에 1.0).
CONSISTENT = [(39 - i, True) if half else (i, False)
              for i in range(10) for half in (True, False)]


def test_rotated_session_starts_at_zero_and_carries_the_boost(manual_api):
    """세션 회전(`apply_boundary`) 경로 — 직전 살아 있던 세션에서 물려받는다.

    물려받는 것은 누적이 아니라 **배속**이다. 회전 직후 화면은 0% 이고, 이후 적중한
    장의 증가폭이 커진다.

    이쪽은 원래 도던 길이다. 계정 기억 쪽을 고치면서 이 길이 깨지지 않는지 잠그는다.
    레거시 사용자는 `identities.owner()` 가 None 이라 `remember()` 가 일찍 빠진다 —
    그래서 이 경로는 계정 기억이 아니라 정책 세션을 그대로 읽는다.
    """
    uid = manual_api.post('/users', json={}).json()['user_id']
    old = manual_api.get(f'/users/{uid}/session').json()['session_id']
    for card, like in CONSISTENT:
        assert manual_api.post('/preferences', json={
            'user_id': uid, 'session_id': old, 'card_id': card,
            'prefer': 'like' if like else 'dislike'}).status_code == 200
    earned = policy.session_for(uid)['taste'][HIT_SCORE]
    assert earned > 0
    assert uid not in recommendation_sessions._user_memory   # 레거시 사용자는 계정이 없다
    assert manual_api.post(f'/users/{uid}/sessions',
                           json={'previous_session_id': old}).status_code == 200
    fresh = policy.session_for(uid)['taste']
    assert fresh[HIT_SCORE] == 0. and fresh[DISPLAY] == 0   # 화면은 0% 에서 시작한다
    assert fresh[HIT_STREAK] == 0            # 연속 적중도 물려주지 않는다
    assert fresh[BOOST] == pytest.approx(decision_summary.carry_boost(earned))
    assert fresh[BOOST] > 1.                 # 실제로 빨라졌다


def test_created_session_starts_at_zero_and_carries_the_boost_from_account_memory(manual_api):
    """새 세션 생성(`apply_created`) 경로 — **앱이 실제로 타는 길**이다.

    `bootstrap_gate.dart` 와 `frame_screen.dart` 는 둘 다 `POST /user/{uid}/session` 을
    부른다. 이 경로는 계정 기억(`_user_memory`)에서 물려받으므로, 기억이 적중을
    접지 않으면 승계할 것이 없어 실사용자는 항상 0 에서 다시 시작했다.
    """
    uid, a, base_a = session(manual_api)
    for card, like in CONSISTENT:
        react(manual_api, uid, a, card, like)
    memory = recommendation_sessions._user_memory[uid]['taste']
    assert memory[HIT_SCORE] > 0, '계정 기억이 적중을 접지 않으면 승계할 것이 없다'
    b = new(manual_api, uid)
    fresh = policy.session_for(b)['taste']
    # 누적은 0 에서 시작하고, 계정 기억의 누적은 **배속의 재료**로만 쓰인다.
    assert fresh[HIT_SCORE] == 0. and fresh[HIT_STREAK] == 0
    assert fresh[BOOST] == pytest.approx(decision_summary.carry_boost(memory[HIT_SCORE]))
    assert fresh[BOOST] > 1.                 # 실제로 빨라졌다
    served = manual_api.get(path(uid, b) + '/decision/metrics/summary').json()
    assert served['decision_readiness_score'] == 0
    # 직전 세션은 그대로 남는다 — 승계는 복사지 이동이 아니다.
    assert policy.session_for(a)['taste'][HIT_SCORE] == pytest.approx(memory[HIT_SCORE])


def test_a_long_lived_account_gives_the_new_session_a_bigger_boost_but_still_zero_on_screen(manual_api):
    """오래 쓴 계정일수록 배속이 크다. 그래도 화면은 0% 에서 시작한다.

    예전에는 이 자리가 "누적 60 -> 새 세션 30, 화면 30%" 를 잠갔다. 그 30% 가 이번에
    없애는 거짓말이다. 검증 카탈로그는 40곳뿐이라 실제 누적이 낮게 멈추므로, 수백 장을
    답한 계정을 흉내 내려 기억의 누적만 올려 놓고 배속을 잰다(학습은 건드리지 않는다).
    """
    uid, a, base_a = session(manual_api)
    for card, like in [(0, True), (1, False), (2, True)]:
        react(manual_api, uid, a, card, like)
    memory = recommendation_sessions._user_memory[uid]['taste']
    memory[HIT_SCORE] = 60.
    b = new(manual_api, uid)
    fresh = policy.session_for(b)['taste']
    assert fresh[HIT_SCORE] == 0.
    # 60/79 만큼 온 사람이라 배속도 그만큼이다 — 짧게 쓴 계정보다 크고, 2.0 배보다 작다.
    assert fresh[BOOST] == pytest.approx(1 + decision_summary.CARRY_BOOST * 60 / 79)
    assert 1. < fresh[BOOST] < 1 + decision_summary.CARRY_BOOST
    assert (manual_api.get(path(uid, b) + '/decision/metrics/summary')
            .json()['decision_readiness_score']) == 0
    # 천장까지 간 계정은 상한(2.0 배)에서 멈춘다.
    memory[HIT_SCORE] = float(decision_summary.CEILING_BELOW_READY)
    c = new(manual_api, uid)
    assert policy.session_for(c)['taste'][BOOST] == pytest.approx(1 + decision_summary.CARRY_BOOST)


def test_snapshot_plus_tail_preserves_display_fold(manual_api, tmp_path):
    import main
    uid, sid, base = session(manual_api)
    react(manual_api, uid, sid, 0)
    react(manual_api, uid, sid, 1, False)
    saved = tmp_path / 'progress.pkl'
    captured = snapshot.write(saved)
    assert captured and captured['watermark'] == 3  # session creation + 2 preferences
    cut = {key: policy.session_for(sid)['taste'][key] for key in SCALARS}
    react(manual_api, uid, sid, 2)
    expected = policy.taste_snapshot(policy.session_for(sid))
    snap = snapshot.load(saved)
    assert snap is not None
    snapshot.install(snap)
    restored = policy.taste_snapshot(policy.session_for(sid))
    assert {key: restored[key] for key in SCALARS} == cut
    state.restore(users.all_users(), [], main._apply, recommendation_sessions.apply_boundary,
                  skip=snap['watermark'])
    actual = policy.taste_snapshot(policy.session_for(sid))
    for key in SCALARS:
        assert actual[key] == expected[key], key
    np.testing.assert_array_equal(actual['m'], expected['m'])
    assert manual_api.get(base + '/decision/metrics/summary').json()['decision_readiness_score'] == expected[DISPLAY]


def test_unavailable_artifacts_still_return_unknown(art, sess, monkeypatch):
    sess[DISPLAY] = 83
    monkeypatch.setattr(content, 'load', lambda: None)
    assert decision_summary.measure_readiness(sess) is None
    assert decision_summary.measure_ready(sess) is None
    assert decision_summary.summarize(sess, [])['decision_readiness_score'] is None
    decision_summary.advance_display(sess)
    assert sess[DISPLAY] == 83


def feed(sess, rank, like):
    """학습을 건드리지 않고 '답을 보기 전 예측 순위' 만 정해 한 번 접는다."""
    sess['hist'].append({'n': len(sess['hist']) + 1, 'like': like,
                         'store_id': None, 'cat': None, 'pairs': 0,
                         'personal_rank': rank})
    decision_summary.advance_display(sess)
    return sess[HIT_SCORE]


def test_a_hit_raises_and_a_miss_lowers_the_score(art, sess):
    """예측이 맞은 방향이면 오르고 빗나가면 내려간다 — 0~79 구간은 단조증가가 아니다."""
    assert feed(sess, .95, True) > 0
    top = sess[HIT_SCORE]
    assert feed(sess, .05, True) < top          # 낮게 본 카드에 좋아요 = 빗나감
    low = sess[HIT_SCORE]
    assert feed(sess, .05, False) > low         # 낮게 본 카드에 싫어요 = 적중
    # 첫 좋아요 이전(순위 없음)에는 적중을 세지 않고 누적을 유지한다.
    held = sess[HIT_SCORE]
    assert feed(sess, None, True) == held
    assert sess[HIT_STREAK] == 1                # 순위가 없는 장은 연속도 끊지 않는다


def test_a_miss_never_takes_more_than_half_of_what_was_earned(art, sess):
    """빗나간 한 장의 하락이 **이제까지 쌓은 값의 절반**을 넘지 않는다.

    운영 기록 재생에서 0 추락 140회(16장+ 72명 중 39명)의 자리다. 하락 자체는 그대로
    일어난다 — 묶는 것은 "한 장이 쌓은 것을 통째로 날린다" 는 폭뿐이다.
    """
    assert decision_summary.MAX_MISS_SHARE == .5
    feed(sess, .95, True)
    earned = sess[HIT_SCORE]
    # 완충이 없다면 4.0 × 0.9 = 3.6 이 빠져 3.6 - 3.6 = 0 이 된다.
    raw = decision_summary.BASE_GAIN * .9
    assert raw > earned * decision_summary.MAX_MISS_SHARE, '완충이 걸리는 자리여야 비교가 된다'
    feed(sess, .05, True)                       # 낮게 본 카드에 좋아요 = 완전히 빗나감
    assert sess[HIT_SCORE] == pytest.approx(earned * (1 - decision_summary.MAX_MISS_SHARE))
    assert sess[HIT_SCORE] > 0

    # 이미 많이 쌓은 사람에게는 상한이 걸리지 않는다 — 하락 폭은 예전과 똑같다.
    high = taste._new_session()
    high[HIT_SCORE] = 40.
    high['hist'] = []
    feed(high, .05, True)
    assert high[HIT_SCORE] == pytest.approx(40. - raw)


def test_a_run_of_misses_never_returns_the_screen_to_zero(art, sess):
    """한 번이라도 순적중한 뒤에는 연속으로 빗나가도 화면이 0 으로 돌아가지 않는다."""
    feed(sess, .95, True)
    assert sess[DISPLAY] > 0
    for _ in range(50):
        feed(sess, .0, True)                    # 최하위로 본 카드에 좋아요 = 최대 하락
        assert sess[HIT_SCORE] > 0, '누적은 양수로 남는다'
        assert sess[DISPLAY] >= decision_summary.MIN_SHOWN_ONCE_POSITIVE, '화면이 0 으로 떨어졌다'
    assert sess[HIT_SCORE] < 1e-6               # 계속 빗나가면 바닥까지는 간다
    assert sess[DISPLAY] == 1                   # 다만 화면은 "아직 0" 이라고 말하지 않는다


def test_zero_on_screen_means_nothing_has_been_predicted_right_yet(art, sess):
    """0 은 "이 세션에서 아직 아무것도 맞히지 못했다" 전용으로 남는다."""
    assert sess[DISPLAY] == 0 and sess[HIT_SCORE] == 0.
    for _ in range(5):
        feed(sess, .1, True)                    # 처음부터 빗나가기만 하면 0 그대로다
    assert sess[HIT_SCORE] == 0. and sess[DISPLAY] == 0
    assert decision_summary.shown_score(0.) == 0
    # 누적이 양수인데 반올림이 0 을 만드는 구간(0.5 미만)이 0 으로 새지 않는다.
    assert decision_summary.shown_score(.4) == 1
    assert decision_summary.shown_score(1e-9) == 1
    assert decision_summary.shown_score(.6) == 1


def test_the_hit_side_rules_are_untouched_by_the_miss_buffer(art, sess):
    """완충은 **빗나간 장에만** 있다 — 적중 쪽 증가 규칙은 한 줄도 바뀌지 않았다."""
    fresh = taste._new_session()
    fresh['hist'] = []
    gains = []
    for _ in range(decision_summary.STREAK_CAP + 2):
        before = fresh[HIT_SCORE]
        feed(fresh, 1., True)                   # credit = 1.0, 완전 적중
        gains.append(fresh[HIT_SCORE] - before)
    expected = [decision_summary.BASE_GAIN *
                (1 + decision_summary.STREAK_STEP * min(i, decision_summary.STREAK_CAP))
                for i in range(decision_summary.STREAK_CAP + 2)]
    assert gains == pytest.approx(expected)
    # 승계 배속도 적중 쪽에만 그대로 곱해진다.
    fast = taste._new_session()
    fast['hist'] = []
    fast[BOOST] = 1 + decision_summary.CARRY_BOOST
    feed(fast, 1., True)
    assert fast[HIT_SCORE] == pytest.approx(decision_summary.BASE_GAIN * (1 + decision_summary.CARRY_BOOST))


def test_replaying_a_miss_heavy_sequence_gives_the_same_score(art, sess):
    """완충이 걸리는 구간을 지나는 답 순서도 재생이 같은 값을 낸다(난수·상태 없음)."""
    answers = [(.9, True), (.05, True), (.95, True), (.0, True), (.1, False),
               (.0, True), (.0, True), (.8, True), (.02, True), (.5, False)]

    def fold():
        state = taste._new_session()
        state['hist'] = []
        for rank, like in answers:
            feed(state, rank, like)
        return {key: state[key] for key in SCALARS}

    first = fold()
    assert first == fold() == fold()
    assert first[HIT_SCORE] > 0 and first[DISPLAY] >= 1


def test_a_streak_grows_the_step_and_a_miss_resets_it(art, sess):
    steps = []
    for _ in range(decision_summary.STREAK_CAP + 3):
        before = sess[HIT_SCORE]
        steps.append(feed(sess, .8, True) - before)
        assert sess[HIT_SCORE] < decision_summary.CEILING_BELOW_READY, '천장에 닿으면 비교가 안 된다'
    # 연속 적중이 길수록 한 장의 증가폭이 커지고, 상한(STREAK_CAP)에서 멈춘다.
    assert steps[1] > steps[0] and steps[decision_summary.STREAK_CAP] > steps[1]
    assert steps[-1] == pytest.approx(steps[decision_summary.STREAK_CAP])
    assert sess[HIT_STREAK] == decision_summary.STREAK_CAP + 3
    feed(sess, .2, True)
    assert sess[HIT_STREAK] == 0
    restart = sess[HIT_SCORE]
    assert feed(sess, .8, True) - restart == pytest.approx(steps[0])


def test_thirty_straight_likes_do_not_drop_the_score_to_zero(art, sess):
    """최근 30장이 전부 좋아요여도 0 으로 떨어지지 않는다(예전의 '강제 0' 자리)."""
    for _ in range(6):
        feed(sess, .9, True)
    peak = sess[HIT_SCORE]
    assert peak > 0
    for _ in range(30):
        feed(sess, .9, True)
    assert decision_metrics.personalization(art, sess, decision.DEFAULT)['negative_predictions'] == 0
    assert decision_summary.measure_readiness(sess) == 0     # 진행률은 그대로 0 이다
    assert sess[HIT_SCORE] >= peak and sess[DISPLAY] > 0


def test_eighty_is_reserved_for_ready(art, sess, monkeypatch):
    for _ in range(40):
        feed(sess, .99, True)
    assert sess[HIT_SCORE] == decision_summary.CEILING_BELOW_READY
    assert not decision_summary.measure_ready(sess)
    assert sess[DISPLAY] == decision_summary.CEILING_BELOW_READY < 80
    assert decision_summary.summarize(sess, [])['decision_readiness_score'] == 79
    # 여덟 조건이 전부 통과인 순간에만 80 이 된다.
    gates = decision_summary._baseline_gates(sess)
    monkeypatch.setattr(decision_summary, '_baseline_gates',
                        lambda s, config=decision.DEFAULT: [{**g, 'passed': True} for g in gates])
    decision_summary.advance_display(sess)
    assert sess[DISPLAY] == 80


def test_a_new_session_carries_the_speed_not_the_score(art, sess):
    """승계는 '시작점' 이 아니라 '속도' 다 — 누적·표시·연속은 0, 배속만 넘어온다.

    예전에는 여기서 `CARRY_SHARE * 이전누적` 을 잠갔다. 그 규칙에서는 새 세션의 화면이
    처음부터 0 이 아닌 값이었고, 그 값은 이 세션이 맞힌 것을 하나도 담고 있지 않았다.
    """
    for _ in range(5):
        feed(sess, .9, True)
    taste.record(sess, 's0', True)      # 승계는 실제로 답한 세션에서만 일어난다
    carried = taste.start_from(sess)
    assert carried[HIT_SCORE] == 0.
    assert carried[DISPLAY] == 0
    assert carried[HIT_STREAK] == 0     # 연속 적중도 물려주지 않는다
    assert carried[BOOST] == pytest.approx(decision_summary.carry_boost(sess[HIT_SCORE]))
    assert carried[BOOST] > 1.
    assert decision_summary.summarize(carried, [])['decision_readiness_score'] == 0
    # 답이 없는 세션에서는 물려줄 속도도 없다 — 기본 배속 1.0.
    assert taste.start_from(taste._new_session())[BOOST] == 1.


def test_the_boost_rises_with_the_previous_score_and_stops_at_double(art, sess):
    """배속은 이전 누적이 클수록 크고, 상한(2.0 배)을 넘지 않는다."""
    ceiling = float(decision_summary.CEILING_BELOW_READY)
    top = 1 + decision_summary.CARRY_BOOST
    assert top == 2.                     # 계약이 정한 상한
    assert decision_summary.carry_boost(0.) == 1.
    grades = [decision_summary.carry_boost(ceiling * f) for f in (0., .25, .5, .75, 1.)]
    assert grades == sorted(grades) and len(set(grades)) == len(grades)
    assert grades[-1] == pytest.approx(top)
    # 천장 위(있을 수 없는 값)나 음수가 들어와도 1.0~2.0 을 벗어나지 않는다.
    assert decision_summary.carry_boost(ceiling * 10) == pytest.approx(top)
    assert decision_summary.carry_boost(-5.) == 1.


def test_the_inherited_boost_reaches_a_higher_score_on_the_same_answers(art, sess):
    """**"더 빠르게 간다" 의 진짜 증거** — 같은 답 순서, 같은 학습 기억, 배속만 다르다.

    비교군은 `start_from` 으로 만든 같은 세션에서 배속만 1.0 으로 되돌린 것이다.
    그래야 취향 벡터·기억 승계의 효과가 아니라 **배속의 효과**만 남는다.
    """
    for _ in range(12):
        feed(sess, .9, True)
    taste.record(sess, 's0', True)
    carried = taste.start_from(sess)
    plain = taste.start_from(sess)
    plain[BOOST] = 1.
    assert carried[BOOST] > 1.
    answers = [(.85, True), (.15, False), (.9, True), (.25, False), (.8, True), (.95, True)]
    for i, (rank, like) in enumerate(answers, 1):
        feed(carried, rank, like)
        feed(plain, rank, like)
        # 두 세션 다 0 에서 출발했는데, 같은 장수에서 승계받은 쪽이 항상 더 높다.
        assert carried[HIT_SCORE] > plain[HIT_SCORE], f'{i}장째'
    assert carried[DISPLAY] > plain[DISPLAY]


def test_a_miss_is_not_multiplied_by_the_inherited_boost(art, sess):
    """빗나간 장에는 배속을 곱하지 않는다 — 양쪽에 곱하면 진동만 커진다."""
    fast, slow = taste._new_session(), taste._new_session()
    fast[BOOST] = 1 + decision_summary.CARRY_BOOST
    for state in (fast, slow):
        feed(state, .95, True)           # 적중 — 여기서는 배속이 붙는다
    assert fast[HIT_SCORE] > slow[HIT_SCORE]
    before = (fast[HIT_SCORE], slow[HIT_SCORE])
    for state in (fast, slow):
        feed(state, .3, True)            # 낮게 본 카드에 좋아요 = 빗나감
    # 내려간 폭이 정확히 같다. 배속이 빗나감에도 곱해졌다면 fast 쪽이 더 크게 내려간다.
    assert before[0] - fast[HIT_SCORE] == pytest.approx(before[1] - slow[HIT_SCORE])
    assert fast[HIT_SCORE] > slow[HIT_SCORE] > 0


def test_reopening_an_empty_session_twice_keeps_the_boost(art, sess):
    """빈 세션 재개는 사용자의 기억을 지우지 않는다 — 배속도 그 기억이다.

    한 장도 답하지 않은 세션에서 배속을 다시 계산하면 그 세션의 누적이 0 이라 1.0 으로
    리셋된다. 옛 규칙에서 같은 자리가 누적을 통째로 지웠다(reset1 24.155 -> reset2 0.000).
    """
    for _ in range(6):
        feed(sess, .9, True)
    taste.record(sess, 's0', True)
    first = taste.start_from(sess)
    assert first[BOOST] > 1.
    second = taste.start_from(first)     # 스와이프 없이 또 리셋
    third = taste.start_from(second)     # 그리고 또
    assert second[BOOST] == first[BOOST] == third[BOOST]
    assert second[HIT_SCORE] == third[HIT_SCORE] == 0.
    # 기억(취향 벡터)도 그대로다 — 이 분기의 원래 취지.
    np.testing.assert_array_equal(third['m'], first['m'])


def test_replaying_the_same_answers_gives_the_same_score(art, sess):
    answers = [('s0', True), ('s1', False), ('s2', True), ('s3', False),
               ('s4', True), ('s5', False), ('s0', False), ('s1', True)]

    def fold():
        state = taste._new_session()
        for sid, like in answers:
            taste.record(state, sid, like)
            decision_summary.advance_display(state)
        return {key: state[key] for key in SCALARS}

    assert fold() == fold()
