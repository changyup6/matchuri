"""Project client measurements from full server analysis; never simplify the analysis."""
from itertools import combinations
from math import floor

from . import content, decision, decision_metrics, taste


# Only personalization gates: candidate availability and location must not alter it.
# `ready` 판정은 여덟 개 전부다 — 이 묶음은 줄이지 않는다.
PERSONALIZATION_GATES = frozenset({
    'rated_count', 'liked_count', 'positive_predictions', 'negative_predictions',
    'validation_auc', 'checkpoint_count', 'probe_count', 'stability',
})

# 진행 점수에서는 `stability` 를 뺀다. 이 값은 미평가 상위 20곳 목록을 **한 번 전·세 번 전**
# 학습 상태와 비교해 **겹침이 작은 쪽**을 쓴다. 세 번 전 비교는 정의상 한 번 전보다 어렵다 —
# 세 걸음 움직인 모델을 한 걸음 기준으로 재기 때문이다. 그래서 학습이 진행되는 동안 내려가고,
# "얼마나 남았나" 가 아니라 "방금 얼마나 배웠나" 를 재게 된다. 운영 산출물 재생에서 번갈아
# 답한 30장은 이 게이트에 막혀 진행 점수가 한 번도 80(앱 화면 100%)에 닿지 못했고, 최약
# 게이트가 stability 인 장수가 15/30 이었다. 진행 척도로 남길 명분은 "선호 추출 정도를
# 반영한다" 였는데, 잠재 취향이 있는 가상 사용자로 잰 화면 퍼센트 대 미래 AUC 순위상관이
# +0.08 이었다(동전 라벨 사용자는 -0.16). 값은 `personalization.stability_score` 와
# `/decision/metrics` 의 gates 로 그대로 나가고, `ready` 조건에서도 그대로다.
PROGRESS_GATES = PERSONALIZATION_GATES - {'stability'}


def _percent(value):
    return round(100 * value, 1) if value is not None else None


def _readiness_score(full):
    """Progress toward existing AND gates, not accuracy or remaining swipe count.

    Rank stability is excluded here and kept for `ready`; see PROGRESS_GATES.
    """
    gates = [g for g in full['gates'] if g['name'] in PROGRESS_GATES]
    if len(gates) != len(PROGRESS_GATES):
        return None
    # Use the weakest required condition: a strong prediction diagnostic cannot
    # hide missing rating evidence. Unknown evidence contributes no readiness yet.
    return min(100 if g['passed'] is True else
               0 if g['value'] is None else
               min(99, max(0, floor(100 * g['value'] / g['threshold'])))
               for g in gates)


def _strict_gates(full):
    """Keep original evidence criteria even when automatic suggestion gates ease."""
    strict = []
    for g in full['gates']:
        target = g.get('baseline_threshold', g['threshold'])
        passed = None if g['value'] is None else (g['value'] >= target if g['operator'] == '>=' else g['value'] == target)
        strict.append({**g, 'threshold': target, 'passed': passed})
    return strict


def _decision_readiness_score(full, config):
    """Strict evidence progress score, exposed as `personalization.readiness_score`.

    Requesting a decision is independent of this score. 앱에 나가는 최상위 점수는
    **이 값이 아니다** — 그쪽은 적중 누적이고(`advance_display`), 여기는 "완화 전
    기준에 얼마나 가까운가" 의 진행률이다. 상세 분석과 `/admin` 이 쓰는 값이다.
    Candidate/location effects and collection floors are not part of either.
    """
    if full['personalization'] is None:
        return None
    return _readiness_score({**full, 'gates': _strict_gates(full)})


# 세션 필드 이름은 `taste` 가 갖는다(세션 dict 의 주인). 여기서 다시 내보내는 것은
# 계산 규칙이 이 모듈에 있기 때문이다 — 예전 이름(`decision_summary.DISPLAY_SCORE_KEY`)
# 을 쓰는 코드·테스트가 그대로 돈다.
DISPLAY_SCORE_KEY = taste.DISPLAY_SCORE_KEY
HIT_SCORE_KEY = taste.HIT_SCORE_KEY
HIT_STREAK_KEY = taste.HIT_STREAK_KEY
HIT_BOOST_KEY = taste.HIT_BOOST_KEY

# ── 적중 누적의 상수 ─────────────────────────────────────────────────────────
# 앱은 API 0~80 을 화면 0~100% 로 환산하고 `>= 80` 을 "준비됨" 으로 쓴다. 그래서
# **80 은 `personalization.ready` 전용**이고, 0~79 는 "얼마나 파악했나" 의 누적이다.
#
# 한 장의 기여는 `credit = 2·personal_rank - 1`(좋아요) / `1 - 2·personal_rank`(싫어요)
# 로 -1~+1 이다. 답을 보기 **전** 예측이 맞은 방향이면 양수다. 완전히 맞힌 한 장이
# BASE_GAIN 점을 준다.
#
# 4.0 은 재생으로 정했다(`~/.claude/specs/matchuri/feat__be-readiness-hit-score.replay.py`).
# 6.0 이면 **한 장도 안 틀리는 가상 사용자**가 9장 만에 천장(79 · 화면 98.8%)에 닿는데
# `ready` 는 22장에서야 참이 된다 — 화면이 "거의 다 됐다" 로 13장을 서 있다. 같은 재생에서
# 싫어요가 셋뿐이라 `ready` 가 영원히 불가능한 '최속 경로' 도 28장에서 98.8% 에 못 박혔다.
# 그 두 자리가 이 계약이 없애려던 "화면과 서버 판정이 갈라진다" 는 그림이다. 4.0 에서는
# 천장 도달이 13장(빈 구간 9장)으로 줄고, 최속 경로 36장은 77.5% 로 끝나 못 박히지 않는다.
# 더 낮추면(3.0) 빈 구간은 6장까지 줄지만 30장을 일관되게 답한 사람이 58.8% 에 머문다.
BASE_GAIN = 4.0
#: 연속 적중 한 번당 증가폭 배율. 2연속 1.25배, 3연속 1.5배 … 로 커진다.
STREAK_STEP = 0.25
#: 배율 상한. 5연속(=cap 4)에서 2.0배로 멈춘다 — 운 좋은 연속이 무한히 커지지 않게.
STREAK_CAP = 4
#: `ready` 전에는 여기까지만 오른다. 화면 98.8% — "거의 다 왔다, 아직은 아니다".
CEILING_BELOW_READY = 79
#: `ready` 인 순간의 값. 앱 화면 100%.
READY_SCORE = 80
#: 승계 배속의 최대 증가분. 1.0 이면 배속은 1.0~2.0 배다(이전 누적이 천장이면 2.0).
#: 새 세션은 누적을 물려받지 않는다 — 물려받는 것은 **속도**뿐이다.
CARRY_BOOST = 1.0
# 빗나간 한 장이 **이제까지 쌓은 것의 이 비율까지만** 깎는다. 하락 자체는 그대로다
# (예측이 틀리면 내려가야 한다) — 묶는 것은 "한 장이 전부를 날린다" 는 자리뿐이다.
#
# 운영 기록 재생(16장+ 72명, 9,549스텝)에서 0 추락은 140회·39명이었다. 왜 다른 형태로는
# 0 이 안 되는지 재봤다:
#  · 하락 감쇠(`-delta * k`)만: k=0.75 -> 71회, 0.5 -> 39회, 0.35 -> 19회, 0.25 -> 10회.
#    끝까지 0 이 안 되고, k=0.25 에서는 최종 AUC 0.5 미만 집단의 마지막 화면%가 70.0% 로
#    옛 규칙의 거짓말(63.7%)을 넘는다.
#  · 한 장당 절대 상한(옛 `MAX_SCORE_DROP` 형태)만: 3.0 -> 106회, 1.0 -> 20회,
#    0.5 -> 8회. 역시 0 이 안 되고 0.5 에서 같은 집단이 71.2% 가 된다.
# 둘 다 실패하는 이유는 같다 — 추락이 일어나는 자리는 누적이 작은 구간인데, 고정폭은
# 그 구간에서 항상 크다. 그래서 **누적에 비례해** 묶는다. 0.5 는 누적 8점(화면 10%)
# 아래에서만 실제로 걸리므로, 이미 오른 사람의 하락은 하나도 바꾸지 않는다:
# 최종 AUC 구간별 마지막 화면%가 32.5 / 46.2 / 91.2 / 98.8 로 기준선과 같다.
MAX_MISS_SHARE = 0.5
#: 누적이 0 보다 크면 화면은 최소 이 값이다. `round(0.4)` 가 0 이 되는 자리를 막는다.
#: 0 은 "이 세션에서 아직 아무것도 맞히지 못했다" 전용으로 남는다 — 재생에서 0 인
#: 298스텝은 **전부** 첫 순적중 이전 구간이다(그 이후 0 은 0회).
MIN_SHOWN_ONCE_POSITIVE = 1


def carry_boost(previous_score):
    """이전 세션의 적중 누적 -> 새 세션의 증가폭 배속(1.0~1.0+CARRY_BOOST).

    누적을 그대로 물려주면 한 장도 답하지 않은 세션의 화면에 30% 가 적힌다 — 이 사람에
    대해 **이 세션이** 맞힌 것은 아직 없으므로 거짓이다. 그래서 물려주는 것을 시작점이
    아니라 속도로 옮겼다: 이전에 많이 맞힌 사람일수록 같은 답에서 더 빨리 오른다.

    천장(CEILING_BELOW_READY)까지 간 사람이 최대 배속이고, 그 사이는 비례한다.
    1.0(=최대 2.0배)은 재생으로 정했다 — 같은 답 순서에서 천장 도달이 26장에서 20장으로
    줄고(1.5 로 올려도 18장으로 두 장 더 줄 뿐), 최대 배속의 실효 증가폭은 8.0 이라
    BASE_GAIN 을 6.0 으로 올렸을 때 계약이 거부한 "화면이 먼저 차버린다" 를 넘지 않는다.
    """
    ratio = min(1., max(0., float(previous_score) / CEILING_BELOW_READY))
    return 1. + CARRY_BOOST * ratio


def _baseline_gates(sess, config=decision.DEFAULT):
    """개인화 근거 여덟 조건을 **완화 전 기준**으로 직접 잰다. 산출물이 없으면 None.

    후보 적합도·다양성·슬레이트 탐색을 돌리지 않는다 — 스와이프마다 도는 경로다.

    `summarize()` 는 `decision_metrics.export()` 가 **완화된** config 로 만든 게이트에
    `_strict_gates` 로 `baseline_threshold` 를 되씌워 같은 목록을 만든다. 완화되는 세
    기준(예측 수·AUC·안정성)의 baseline 은 export 에 넘긴 **원래** config 값이고,
    나머지 네 개(평가 수·좋아요 수·체크포인트·미평가 풀)는 애초에 완화되지 않는다.
    그래서 여기서 완화 전 config 로 직접 재면 완화 구간(스와이프 30장 이상)에서도
    같은 판정이 나온다 — `tests/test_decision_progress.py` 가 그 등가성을 잠근다.

    `personalization()` 에 넘기는 config 도 완화 여부와 무관하다: 이 함수가 config 에서
    쓰는 것은 `stable_top` 하나이고 그 값은 완화되지 않는다.
    """
    art = content.load()
    if art is None:
        return None
    p = decision_metrics.personalization(art, sess, config)
    values = {
        'rated_count': (len(set(sess['like_ids'] + sess['dislike_ids'])), config.min_rated),
        'liked_count': (len(set(sess['like_ids'])), config.min_likes),
        'positive_predictions': (p['positive_predictions'], config.min_validation_each),
        'negative_predictions': (p['negative_predictions'], config.min_validation_each),
        'validation_auc': (p['validation_auc'], config.min_validation_auc),
        'checkpoint_count': (p['checkpoint_count'], 4),
        'probe_count': (p['probe_count'], config.stable_top),
        'stability': (p['stability'], config.min_overlap),
    }
    return [{'name': name, 'value': value, 'threshold': target,
             'passed': None if value is None else bool(value >= target)}
            for name, (value, target) in values.items()]


def measure_readiness(sess, config=decision.DEFAULT):
    """Exact strict progress score without running candidate fit/diversity analysis.

    `personalization.readiness_score`(일곱 조건의 진행률) 와 같은 값이다. 뜻은 이번에
    바뀌지 않았다 — 앱에 나가는 최상위 점수만 적중 누적으로 바뀐다.

    예전에는 최근 30장에 한쪽 라벨이 없으면 여기서 0 을 조기 반환했다. 그 줄은
    없앴다: 라벨이 한쪽뿐이면 그 예측 근거 게이트의 값이 0 이라 최솟값도 0 이므로
    **결과가 같고**, 남겨 두면 "좋아요만 계속하면 0 으로 추락한다" 는 규칙이 코드
    두 곳에 사는 것처럼 보인다. 누적 점수는 그 창에 매이지 않는다.
    """
    gates = _baseline_gates(sess, config)
    return None if gates is None else _readiness_score({'gates': gates})


def measure_ready(sess, config=decision.DEFAULT):
    """`summarize()` 의 `personalization.ready` 와 **모든 입력에서 같은** 참/거짓.

    피드백마다 불리므로 `decision_metrics.export()` 전체(후보 채점·쌍 탐색·슬레이트)를
    돌리지 않는다. 산출물이 없으면 None — 판정할 근거가 없다는 뜻이다.
    """
    gates = _baseline_gates(sess, config)
    if gates is None:
        return None
    return _ready(gates)


def _ready(gates):
    """여덟 조건이 **전부** 통과인가. 미확보(None)는 통과가 아니다."""
    passed = {g['name']: g['passed'] for g in gates}
    return all(passed.get(name) is True for name in PERSONALIZATION_GATES)


def advance_display(sess, *, judge_ready=True):
    """Called once after accepted learning, under policy's feedback lock.

    점수는 "남은 조건이 얼마나 채워졌나" 가 아니라 **답을 보기 전 예측이 맞았나** 를
    누적한다. 그래서 좋아요만 계속해도 내려가지 않고, 취향이 쌓이면 올라간다.

    한 번에 정확히 한 번 불린다(난수 없음) — `preferences.jsonl` 재생이 같은 값을 낸다.
    GET 은 이 값을 움직이지 않는다. 스냅샷은 네 스칼라를 그대로 싣는다(누적·연속·표시값
    과 함께 이전 세션에서 물려받은 배속도 — 빠지면 재생 궤적이 달라진다).

    `judge_ready=False` 는 계정 기억 모델(`app.recommendation_sessions._user_memory`)
    전용이다. 그 모델에서 새 세션이 물려받는 것은 **증가 속도(배속)뿐**이고 — 그 재료가
    이 모델의 누적이다 — 표시값은 어느 엔드포인트도 내보내지 않는다. 그런데
    `_baseline_gates` 는 미평가 상위 20곳 목록을
    체크포인트마다 줄 세우므로 이 경로 비용의 대부분을 차지한다 — 결과가 버려지는
    계산이다. 그래서 건너뛰고, 표시값은 `round(누적)` 으로 둔다: 80(=`ready`)을
    주장하지 않는다. 판정이 필요한 정책 세션은 기본값 그대로 여덟 조건을 다 잰다.
    """
    gates = _baseline_gates(sess) if judge_ready else None
    if content.load() is None:
        # 산출물이 없으면 학습도 일어나지 않았다(`taste.record` 가 먼저 돌아선다).
        # 아무것도 건드리지 않는다 — 이전 값이 남는다.
        return
    total = float(sess.get(HIT_SCORE_KEY, 0.))
    streak = int(sess.get(HIT_STREAK_KEY, 0))
    row = sess['hist'][-1] if sess.get('hist') else None
    rank = row.get('personal_rank') if row else None
    # 첫 좋아요 이전에는 개인화 순위가 없다(예측이 없으니 적중도 없다). 누적을 유지한다.
    if rank is not None:
        credit = (2 * rank - 1) if row['like'] else (1 - 2 * rank)
        hit = credit > 0
        streak = streak + 1 if hit else 0
        # 배율은 적중일 때만 1 보다 커진다. 빗나간 장은 내려가되, 한 번에 이제까지
        # 쌓은 것의 MAX_MISS_SHARE 보다 많이 잃지는 않는다.
        boost = (1 + STREAK_STEP * min(streak - 1, STREAK_CAP)) if hit else 1
        # 승계 배속(`taste.start_from` 이 이전 세션 누적으로 정해 둔 값)도 **적중한 장에만**
        # 곱한다. 빗나간 장에도 곱하면 오르내림이 같이 커져 점수가 진동만 할 뿐
        # 빨리 가지 않는다 — 물려받는 것은 "속도" 이지 "민감도" 가 아니다.
        carry = float(sess.get(HIT_BOOST_KEY, 1.)) if hit else 1.
        change = BASE_GAIN * credit * boost * carry
        if change < 0:
            # 하락은 그대로 일어나되 이제까지 쌓은 것의 MAX_MISS_SHARE 를 넘지 않는다.
            # 누적이 0 이면 깎을 것도 없다 — 이미 바닥이다.
            change = -min(-change, total * MAX_MISS_SHARE)
        total = min(max(total + change, 0.), float(CEILING_BELOW_READY))
    sess[HIT_SCORE_KEY] = total
    sess[HIT_STREAK_KEY] = streak
    # 80 은 `ready` 전용이다 — 앱의 `score >= 80` 판정과 서버 판정이 같아진다.
    sess[DISPLAY_SCORE_KEY] = READY_SCORE if (gates and _ready(gates)) else shown_score(total)


def shown_score(total):
    """적중 누적 -> 화면에 적는 정수. 0 은 "아직 아무것도 맞히지 못했다" 전용이다.

    누적이 양수인데 `round` 가 0 을 돌려주는 자리(0.5 미만)를 막는다. 하락 상한
    (`MAX_MISS_SHARE`)이 양수 누적을 0 으로 떨어뜨리지 않으므로, 이 둘이 함께
    "한 번이라도 순적중한 사람의 화면은 다시 0 이 되지 않는다" 를 만든다.
    """
    total = min(max(float(total), 0.), float(CEILING_BELOW_READY))
    return max(MIN_SHOWN_ONCE_POSITIVE, round(total)) if total > 0 else 0


def summarize(sess, cards, *, allow=None, context=None, tiers=None, bonus=None, config=decision.DEFAULT):
    art = content.load()
    if art is not None and any(sid not in art.pos for _, sid in cards):
        raise ValueError('card_ids must have recommendation vectors')
    # Skip candidate JSON only. Full-pool scoring, self-exclusion, validation and
    # slate search run exactly as in the detailed endpoint, even with no card IDs.
    full = decision_metrics.export(sess, allow=allow, context=context, tiers=tiers, bonus=bonus,
                                   config=config, candidate_limit=0)
    out = {'status': 'ok', 'decision_readiness_score': None,
           'personalization': {'ready': False, 'readiness_score': None,
                               'discrimination_score': None, 'stability_score': None},
           'fit_count': None,
           'diversity': {'passed': None, 'similarity_score': None, 'card_ids': None}}
    if art is None:
        out['status'] = 'unavailable'
        return out
    p = full['personalization']
    # Personalization keeps its original meaning even when a long session may
    # offer choices without passing every prediction diagnostic.
    strict_gates = _strict_gates(full)
    ready = _ready(strict_gates)
    enough_labels = min(p['positive_predictions'], p['negative_predictions']) >= config.min_validation_each
    # 화면 100%(API 80)는 `ready` 와 한 몸이다. 누적은 `advance_display` 가 피드백마다
    # 접어 세션에 넣어 둔 값을 그대로 쓰고(조회는 점수를 움직이지 않는다), 80 의 뜻만
    # 여기서 다시 씌운다 — 예전 판(게이트 최솟값)으로 저장된 세션을 복원했더라도
    # 화면과 `ready` 가 갈라지지 않게 하려는 것이다. `ready` 는 이 세션의 학습 상태만
    # 보는 값이므로(필터·거리·n·card_ids 와 무관) 경량 경로와 여기가 늘 같다.
    #
    # 세션에 누적이 없을 때 = **0**. 피드백 전에 GET 이 먼저 온 새 세션이거나, 산출물이
    # 없는 동안 학습해 `advance_display` 가 아무것도 적지 못한 세션이다. 두 경우 다
    # "아직 맞힌 것이 없다" 이므로 0% 가 사실이다(예전에는 원수치로 떨어졌다).
    out['decision_readiness_score'] = (
        READY_SCORE if ready else min(int(sess.get(DISPLAY_SCORE_KEY, 0)), CEILING_BELOW_READY))
    out['personalization'] = {
        'ready': ready,
        'readiness_score': _readiness_score({**full, 'gates': strict_gates}),
        'discrimination_score': _percent(p['validation_auc']) if enough_labels else None,
        'stability_score': _percent(p['stability'])}
    if context and context.get('location_required'):
        out['status'] = 'location_required'
    elif full['fit']['threshold'] is not None:
        out['fit_count'] = full['fit']['qualified_count']
    pairs = [(tuple(sorted((ca, cb))), decision_metrics.pair_metrics(art, art.pos[a], art.pos[b], config))
             for (ca, a), (cb, b) in combinations(cards, 2)]
    if pairs:
        out['diversity']['passed'] = all(p['passes_current_rule'] for _, p in pairs)
        # A measured pair cannot stand in for an unknown (potentially closer) pair.
        if all(p['known_modalities'] >= 2 for _, p in pairs):
            ids, closest = min(pairs, key=lambda row: (-row[1]['raw_mean_similarity'], row[0]))
            # Fixed display scale of the existing cosine mean, not a new model.
            mean = max(-1., min(1., closest['raw_mean_similarity']))  # float32 endpoint roundoff
            out['diversity'].update(similarity_score=round(50 * (1 + mean), 1), card_ids=ids)
    return out
