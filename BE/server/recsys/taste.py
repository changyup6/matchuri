# -*- coding: utf-8 -*-
"""취향 정책 — 구성요소 점수 위의 사용자별 가중치 (PLAN 6절, Step 4).

`swipe.py` 와 **공개 API 가 같다**(`session_for` · `pick_many` · `record` · `peek` ·
`learn_state` · `sessions` · `in_pool`). 라우터(`policy.py`)가 둘을 나란히 돌리며
인터리빙할 수 있는 이유다. 바뀐 것은 특성(feature)뿐이다.

    legacy   x = [절편, 품질z, 리뷰수, 업종 11, 가격대 5]              19개, 카테고리 기반
    taste    z = [인기, 평판, 공방문, 메뉴, 요약문, 리뷰, 음식사진, 기피]  8개, 콘텐츠 기반

갱신 골격은 legacy 를 그대로 물려받는다 — 로지스틱 + 라플라스 근사 대각 가우시안 +
완화된 Thompson(σ×0.4). 오프라인에서 확인된 것은 '어떤 성분을 어떤 가중치로 섞는가'
이지 '어떤 온라인 갱신이 좋은가' 가 아니고, 갱신 골격은 이미 실측으로 튜닝된 것이
있기 때문이다(README 의 페르소나 replay).

**쌍대(pairwise) 갱신.** PLAN 3절 원칙 4번 — "dislike 직후 like 는 'B > A' 제약이다".
새 피드백이 오면 **반대 라벨의 최근 5건**과 짝지어 σ(w·(z_like − z_dislike)) 위에서
한 걸음씩 간다. 점별(pointwise) 갱신은 '좋아요를 많이 받는 성분' 을 배우는데 그건
인기의 다른 이름이고, 쌍대는 '이 사람이 A 보다 B 를 고른 이유' 를 배운다. Step 2 에서
λ 를 격자 탐욕 대신 쌍대 로지스틱으로 학습하자 16칸 중 3칸 → 9칸으로 뛰었다.
반대 라벨이 아직 없으면 점별로 대신한다(첫 몇 장).

**leave-one-out — 쌍의 두 가게는 자기 근거가 될 수 없다 (Step 3d).** 성분 z 는 그
피드백을 **넣기 전** 의 like/dislike 집합으로 계산한다. 그것만으로는 부족했다: 쌍의
**상대쪽** 가게는 이미 집합 안에 있고, 유사도 행렬의 **대각선이 최댓값**이다
(cooc(x,x)=1, sim(x,x)=1). 그래서 z(상대)가 자기 자신과의 유사도로 부풀고 Δz 가
그 대각선 하나에 지배된다. 실서버에서 `like 65 → dislike 65 → like 741` 한 사용자의
z_cf(65) 는 **+256** 이었고 한 걸음에 w_cf 가 0.46 → **−63.63** 으로 밀렸다.

고치는 방법은 하나뿐이다 — **쌍을 채점할 때 그 쌍의 두 가게를 L·D 에서 모두 뺀다**
(`_loo_idx`). 그러면 z(A)·z(B) 는 둘 다 '나머지 이력으로 본 이 후보' 가 되고, Δz 는
'이 사람이 A 보다 B 를 고른 이유' 만 담는다. 값이 쌍마다 다르므로 성분 계산도 쌍마다
한 번씩 돈다(|L| = 300 에서 한 번 9ms × 최대 5쌍).

**같은 가게를 다시 평가하면 마지막 라벨만 남는다.** 두 집합에 동시에 들어가 있으면
그 가게가 like 이유이자 dislike 이유가 되고, 위의 대각선 문제가 되살아난다. 그래서
새 피드백을 넣기 전에 그 가게를 **like·dislike 양쪽에서 지운다** — 지운 뒤 새 라벨의
집합에 한 번만 붙인다(중복도 사라진다).

**재생 가능해야 한다.** 학습에는 난수를 한 톨도 쓰지 않는다. 그래서
`preferences.jsonl` 을 순서대로 다시 먹이면 m·v 가 소수점까지 복원된다
(`tests/test_replay.py`).

**산출물이 없으면 아무것도 하지 않는다.** `content.load()` 가 None 이면 세션은 비고
`pick_many` 는 빈 목록을 준다 — 라우터가 legacy 로 물러난다. 서버는 죽지 않는다.
"""
from __future__ import annotations

import copy
import threading
import time

import numpy as np

from . import content
from . import store as svc
from . import swipe

#: 새 피드백 하나를 짝지을 반대 라벨의 최대 개수 (PLAN 6절 "최근 dislike 카드 ≤ 5쌍").
MAX_PAIRS = 5

#: Thompson 완화 계수. legacy 와 같은 값이다(σ×0.4 가 페르소나 5종에서 전부 우세).
TS_DAMP = 0.4

#: 한 걸음에 성분 가중치가 움직일 수 있는 최대치 (Step 3d 의 안전망).
#: 진짜 원인은 z 였고(leave-one-out + `content.Z_CLIP` 이 그것을 고친다) 이건 **뚜껑**
#: 이다 — 앞으로 어떤 성분이 또 긴 꼬리를 갖더라도 한 장의 카드가 모델을 뒤집지
#: 못하게 한다. 라플라스 갱신 자체는 그대로다(난수 없음, 재생 가능).
MAX_STEP = 0.5

#: 10장에 1장은 불확실성이 가장 큰 카드(탐색).
EXPLORE_EVERY = 10

#: 배치 안 근접 중복 금지 임계 — 메뉴 MaxSim 이 이보다 크면 같은 배치에 안 넣는다.
DUP_MENU_SIM = 0.9

#: 프로토타입(설명용 군집) 상한과, 군집에 쓸 최근 like 수.
MAX_PROTOTYPES = 5
PROTOTYPE_WINDOW = 60

# One meal decision commonly takes 20–30 swipes. Ten new observations halve
# old evidence; no elapsed wall time is used, so restart/replay stay identical.
RECENCY_HALF_LIFE = 10.0
RECENCY_DECAY = 2.0 ** (-1.0 / RECENCY_HALF_LIFE)
MEMORY_SHARE = 0.20

# 표시 점수(앱 화면의 "취향 파악 %")는 학습 상태와 **같은 dict** 에 산다 — 스냅샷과
# `preferences.jsonl` 재생이 한 덩어리로 실어야 하기 때문이다. 계산 규칙은
# `decision_summary` 에 있고, 여기 있는 것은 **세션 필드의 이름과 초기값**뿐이다.
# (`decision_summary` 가 이 이름들을 다시 내보낸다 — import 고리를 만들지 않으려고
#  낮은 층인 여기에 둔다. `decision` → `taste` 방향은 이미 있다.)
DISPLAY_SCORE_KEY = "decision_readiness_score"   # 앱에 나가는 정수 0~80
HIT_SCORE_KEY = "decision_hit_score"             # 적중 누적 실수 0~79 (반올림 전)
HIT_STREAK_KEY = "hit_streak"                    # 연속 적중 수
HIT_BOOST_KEY = "hit_carry_boost"                # 승계 배속 1.0~2.0 (적중한 장에만 곱한다)

_lock = threading.RLock()
_sessions: dict[str, dict] = {}


# ────────────────────────────────────────────────────────── 세션
def _new_session() -> dict:
    art = content.load()
    if art is None:
        m = np.zeros(content.N_COMPONENTS)
        sd = np.full(content.N_COMPONENTS, 0.5)
    else:
        m, sd = art.prior_mean(), art.prior_sd()
    return {"m": np.asarray(m, dtype=float), "v": np.asarray(sd, dtype=float) ** 2,
            "like_ids": [], "dislike_ids": [],
            "shown": set(), "last_cats": [], "last_brands": [],
            "n": 0, "likes": 0, "served": 0,
            "rng": np.random.default_rng(), "hist": [], "decision_checkpoints": [],
            "created": time.time(), "evidence_at": {}, "memory": [],
            "prior_m": np.asarray(m, dtype=float).copy(),
            "prior_v": np.asarray(sd, dtype=float) ** 2,
            # 적중 누적·연속 적중수·앱에 나가는 점수. 아직 맞힌 것이 없으니 전부 0 이다.
            # 배속은 물려받은 것이 없으면 1.0 — "남들과 같은 속도로 오른다".
            DISPLAY_SCORE_KEY: 0, HIT_SCORE_KEY: 0.0, HIT_STREAK_KEY: 0,
            HIT_BOOST_KEY: 1.0}


def start_from(previous: dict | None) -> dict:
    """A new decision session: weak user memory, fresh exposure and confidence."""
    sess = _new_session()
    if not previous:
        return sess
    if not previous.get('n'):
        # Reopening an empty session must not erase the user's memory.
        # 배속도 **기억**이다 — 여기서 다시 계산하면 이 세션의 누적이 0 이라 1.0 으로
        # 리셋된다. 스와이프 없이 두 번 리셋하면 이전 세션에서 쌓은 속도를 통째로
        # 잃던 자리다(옛 규칙 실측: reset1 hit=24.155 → reset2 hit=0.000). 그대로 복사한다.
        for key in ('m', 'v', 'prior_m', 'prior_v', 'memory', HIT_BOOST_KEY):
            if key in previous:
                sess[key] = copy.deepcopy(previous[key])
        return sess
    sess['m'] += MEMORY_SHARE * (previous['m'] - sess['m'])
    sess['prior_m'] = sess['m'].copy()
    # Prior history cannot carry over current-session validation or confidence.
    memory = {row['store_id']: {**row, 'weight': row['weight'] * MEMORY_SHARE}
              for row in previous.get('memory', [])}
    for like, key in ((True, 'like_ids'), (False, 'dislike_ids')):
        for sid in previous[key]:
            at = previous.get('evidence_at', {}).get(sid, previous['n'])
            memory[sid] = {'store_id': sid, 'like': like,
                          'weight': RECENCY_DECAY ** max(0, previous['n'] - at)}
    # Keep every distinct store reference; recency changes weight, not inclusion.
    # The catalog bounds this set. Reassessment still replaces the old label.
    sess['memory'] = sorted(memory.values(), key=lambda row: -row['weight'])
    # 적중 누적은 **물려주지 않는다**. 새 세션의 누적도 화면도 0 에서 시작한다 —
    # 이 세션에서 아직 한 장도 맞히지 않았는데 30% 가 적혀 있으면 그 숫자가 거짓이다.
    # 대신 이전 세션에서 쌓은 만큼 **증가 속도**를 물려준다: 적중한 장의 증가폭에
    # 배속을 곱한다(`decision_summary.advance_display`). 같은 답을 먹이면 승계받은
    # 세션이 같은 장수에서 더 높은 점수에 닿는다 — "처음부터 있다" 가 아니라 "빨리 간다".
    # 연속 적중수도 물려주지 않는다 — 이번 세션에서 아직 한 장도 맞히지 않았다.
    from . import decision_summary      # 계산 규칙은 저쪽에 산다(import 고리를 피해 지연)
    sess[HIT_BOOST_KEY] = decision_summary.carry_boost(previous.get(HIT_SCORE_KEY, 0.0))
    return sess


def _memory_components(sess, art, hold, rated):
    """Memory features depend on evidence/mask, never on learned m/v."""
    answered = set(sess['like_ids'] + sess['dislike_ids']) | set(hold)
    memory = [row for row in sess.get('memory', []) if row['store_id'] in art.pos and row['store_id'] not in answered]
    if not memory:
        return None
    li = [row for row in memory if row['like']]
    di = [row for row in memory if not row['like']]
    return art.component_scores(np.array([art.pos[row['store_id']] for row in li], int),
        np.array([art.pos[row['store_id']] for row in di], int), exclude=rated,
        like_weights=np.array([row['weight'] for row in li]),
        dislike_weights=np.array([row['weight'] for row in di]))


def components(sess, art, hold=frozenset(), *, memory_prior=None):
    """Single scoring path for serving, decisions, comments, map and replay."""
    liked, disliked, rated = _loo_idx(sess, art, set(hold))
    evidence = sess.get('evidence_at')
    kw = {}
    if evidence:
        def weights(indices):
            return np.array([RECENCY_DECAY ** max(0, sess.get('n', 0) - evidence.get(art.ids[i], sess.get('n', 0))) for i in indices])
        kw = {'like_weights': weights(liked), 'dislike_weights': weights(disliked)}
    z = art.component_scores(liked, disliked, exclude=rated, **kw)
    prior = memory_prior if memory_prior is not None else _memory_components(sess, art, hold, rated)
    if prior is not None:
        share = MEMORY_SHARE * RECENCY_DECAY ** sess.get('n', 0)
        personal = [content.IDX[k] for k in ('cf', 'menu', 'text', 'review', 'food', 'neg')]
        z[personal] = (1 - share) * z[personal] + share * prior[personal]
    return z


def has_positive(sess):
    answered = set(sess['like_ids'] + sess['dislike_ids'])
    return bool(sess['like_ids'] or any(row['like'] and row['store_id'] not in answered
                                      for row in sess.get('memory', [])))


def _forget(sess):
    # Discount accumulated likelihood precision towards the session prior.
    # This prevents 80 old swipes from making today's contradictory swipe inert.
    base_m = sess.get('prior_m', np.zeros_like(sess['m']))
    base_v = sess.get('prior_v', np.ones_like(sess['v']))
    precision = 1 / base_v + RECENCY_DECAY * (1 / sess['v'] - 1 / base_v)
    information = base_m / base_v + RECENCY_DECAY * (sess['m'] / sess['v'] - base_m / base_v)
    sess['v'] = 1 / precision
    sess['m'] = information / precision


def session_for(user_id: str) -> dict:
    with _lock:
        sess = _sessions.get(user_id)
        if sess is None:
            sess = _new_session()
            _sessions[user_id] = sess
            # These are durable users, not disposable anonymous sessions.
            # policy also owns these objects: removing only this index never
            # freed the model, but silently reset it on the next lookup.
        return sess


def sessions() -> dict:
    return _sessions


def replay(feedback: list[tuple[str, bool]]) -> dict:
    """세션 하나를 **등록하지 않고** 만들어 피드백을 순서대로 먹인다.

    `session_for` 와 달리 `_sessions` 에 넣지 않는다 — 살아 있는 세션을 건드리지 않고
    '스와이프 n 장 시점의 모델' 만 되살리는 자리다(관리자 지형도의 이력 스크러버).
    학습에 난수가 없으므로 같은 입력이면 같은 상태가 나온다 — 전체를 다시 먹이면
    살아 있는 세션과 소수점까지 같다(`tests/test_replay.py` 와 같은 성질).
    """
    sess = _new_session()
    for store_id, like in feedback:
        record(sess, store_id, like)
    return sess


def in_pool(store_id: str) -> bool:
    art = content.load()
    return bool(art and store_id in art.pos)


def _idx(sess: dict, art: content.Artifacts) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(like, dislike, 이미 답한 것) 을 풀 인덱스 배열로. 풀 밖 가게는 빠진다."""
    like = np.array([art.pos[s] for s in sess["like_ids"] if s in art.pos], dtype=int)
    dis = np.array([art.pos[s] for s in sess["dislike_ids"] if s in art.pos], dtype=int)
    rated = np.concatenate([like, dis]) if like.size or dis.size else np.zeros(0, int)
    return like, dis, rated


def _loo_idx(sess: dict, art: content.Artifacts,
             hold: set[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`hold` 의 가게를 **like·dislike 양쪽에서** 뺀 (like, dislike, 이미 답한 것).

    leave-one-out 의 전부다(모듈 문서 참조). 정규화 마스크(`rated`)는 **그대로 둔다** —
    후보는 '아직 안 답한 가게' 이고 그 정의는 채점 대상이 무엇이냐와 무관하다.
    """
    like = np.array([art.pos[s] for s in sess["like_ids"]
                     if s in art.pos and s not in hold], dtype=int)
    dis = np.array([art.pos[s] for s in sess["dislike_ids"]
                    if s in art.pos and s not in hold], dtype=int)
    return like, dis, _idx(sess, art)[2]


# ────────────────────────────────────────────────────────── 학습
def _logistic_step(sess: dict, x: np.ndarray, y: float, weight: float = 1.0) -> None:
    """로지스틱 1스텝 + 라플라스 근사 대각 갱신. `swipe.record` 와 같은 수식이다.

    다른 것은 하나뿐 — 성분별 이동폭을 `MAX_STEP` 으로 자른다. 로짓도 ±60 에서
    자르는데 σ 는 그 밖에서 배정밀도로 이미 0·1 이라 **값이 달라지지 않고**
    지수 오버플로 경고만 사라진다.
    """
    m, v = sess["m"], sess["v"]
    prob = 1.0 / (1.0 + np.exp(-float(np.clip(x @ m, -60.0, 60.0))))
    g = weight * (prob - y) * x
    sess["v"] = 1.0 / (1.0 / v + weight * prob * (1.0 - prob) * x * x)
    sess["m"] = m - np.clip(sess["v"] * g, -MAX_STEP, MAX_STEP)


def record(sess: dict, store_id: str, like: bool) -> None:
    """스와이프 결과를 반영. 난수를 쓰지 않으므로 재생하면 정확히 같은 상태가 된다."""
    art = content.load()
    if art is None:
        return
    i = art.pos.get(store_id)
    if i is None:                     # 추천 풀 밖의 가게는 학습에서 뺀다 (legacy 와 같다)
        return

    # 이번 답을 보기 전의 개인화 순위. 선택 제안은 이 과거 예측과 실제 응답의
    # 구분력을 확인한다. 이미 평가한 카드는 학습 누출이므로 검증에서 제외한다.
    personal_rank = None
    if sess["like_ids"] and store_id not in sess["like_ids"] + sess["dislike_ids"]:
        z_before = components(sess, art)
        personal = [content.IDX[c] for c in ("cf", "menu", "text", "review", "food", "neg")]
        values = sess["m"][personal] @ z_before[personal]
        personal_rank = float((np.count_nonzero(values < values[i]) +
                               0.5 * np.count_nonzero(values == values[i])) / len(values))

    # 같은 가게를 다시 평가했다면 **마지막 라벨만 남긴다** — 양쪽 집합에 동시에 들어간
    # 가게는 자기 자신의 근거가 되고(대각선), 중복도 이력을 두 번 세게 만든다.
    for key in ("like_ids", "dislike_ids"):
        if store_id in sess[key]:
            sess[key] = [s for s in sess[key] if s != store_id]

    _forget(sess)
    opposite = sess["dislike_ids"] if like else sess["like_ids"]
    pairs = [s for s in opposite[-MAX_PAIRS:] if s in art.pos]
    if pairs:
        # Every pair holds out the new store and an already answered opponent.
        # Their memory evidence and normalization mask are therefore identical.
        # Reuse only within this event; the next swipe/session gets fresh features.
        memory_prior = _memory_components(sess, art, {store_id}, _idx(sess, art)[2]) if len(pairs) > 1 else None
        for other in reversed(pairs):                 # 최근 것부터
            # 쌍마다 z 를 다시 낸다 — 두 가게를 뺀 이력이 쌍마다 다르기 때문이다.
            z = components(sess, art, {store_id, other}, memory_prior=memory_prior)
            d = (z[:, i] - z[:, art.pos[other]]) if like else \
                (z[:, art.pos[other]] - z[:, i])
            _logistic_step(sess, d, 1.0, RECENCY_DECAY ** max(0, sess["n"] - sess.get("evidence_at", {}).get(other, sess["n"])))  # 항상 '앞의 것이 낫다'
    else:
        # 반대 라벨이 아직 없다 — 쌍을 만들 수 없으니 점별로 간다(첫 몇 장).
        z = components(sess, art, {store_id})
        _logistic_step(sess, z[:, i], 1.0 if like else 0.0)

    (sess["like_ids"] if like else sess["dislike_ids"]).append(store_id)
    sess["shown"].add(store_id)
    sess["n"] += 1
    sess.setdefault("evidence_at", {})[store_id] = sess["n"]
    sess["likes"] += 1 if like else 0
    cat = swipe.pool()["cat"].get(store_id)
    sess["hist"].append({"n": sess["n"], "like": like, "store_id": store_id,
                         "cat": cat, "pairs": len(pairs), "personal_rank": personal_rank})
    # 최근 네 학습 상태만 보관한다. 조회 빈도와 무관하게 n-1/n-3와 비교하고,
    # preferences 재생 시 같은 체크포인트를 복원한다(난수/파일 추가 없음).
    sess.setdefault("decision_checkpoints", []).append({
        "m": sess["m"].copy(), "v": sess["v"].copy(),
        "like_ids": list(sess["like_ids"]), "dislike_ids": list(sess["dislike_ids"]),
        "n": sess["n"], "evidence_at": dict(sess.get("evidence_at", {})),
        "memory": copy.deepcopy(sess.get("memory", []))})
    del sess["decision_checkpoints"][:-4]


# ────────────────────────────────────────────────────────── 선택
def _cold(z: np.ndarray, m: np.ndarray, has_dislike: bool) -> np.ndarray:
    """콜드스타트 점수 — `z_pop + 0.5·z_q` (PLAN 6절).

    like 가 없으면 콘텐츠 성분이 전부 0 이라 학습된 가중치가 할 말이 없다. 다만
    dislike 는 like 없이도 계산되므로 기피 항만 얹는다 — 첫 10장에서 배운 것을
    버리지 않기 위해서다.
    """
    total = z[content.IDX["pop"]] + 0.5 * z[content.IDX["quality"]]
    if has_dislike:
        j = content.IDX["neg"]
        total = total + m[j] * z[j]
    return total


def _uncertainty(z: np.ndarray, v: np.ndarray) -> np.ndarray:
    """후보 점수의 사후 표준편차 sqrt(Σ_c v_c z_c²) — 탐색 카드는 이게 가장 큰 것."""
    return np.sqrt((v[:, None] * (z ** 2)).sum(axis=0))


def _batch_state(sess: dict, art: content.Artifacts):
    like_idx, dis_idx, rated = _idx(sess, art)
    z = components(sess, art)
    return like_idx, dis_idx, z


def _explain(explain: dict | None, sess: dict, sid: str, zi: np.ndarray,
             w: np.ndarray, explore: bool) -> None:
    """카드 응답의 `attribution` 재료 — 성분마다 `가중치 × z` 기여.

    이유 문장(`recommendation_reason`)이 가장 큰 기여 **하나**를 말로 바꾼 것이라면, 이건 그
    숫자 전부다. 점수를 실제로 만든 표본 가중치 `w` 로 잰다(설명이 선택과 어긋나지 않게).
    `explore` 카드는 점수와 무관하게 뽑혔으니 그 표시를 같이 싣는다.
    """
    if explain is None:
        return
    contrib = np.nan_to_num(np.asarray(w, dtype=float) * np.asarray(zi, dtype=float))
    explain[sid] = {"components": {c: round(float(contrib[j]), 4)
                                   for j, c in enumerate(content.COMPONENTS)},
                    "explore": bool(explore), "cold": not has_positive(sess),
                    "swipes": int(sess.get("n", 0))}


def _pick_one(sess: dict, art: content.Artifacts, z: np.ndarray,
              like_idx: np.ndarray, batch: list[int],
              reasons: dict | None, allow: set[str] | None = None,
              tier: np.ndarray | None = None,
              bonus: np.ndarray | None = None,
              explain: dict | None = None) -> str | None:
    """카드 한 장. 다양성 규칙을 어기지 않는 최고점, 없으면 최고점 그대로.

    `allow` 는 필터(업종·태그·거리)가 만든 허용 집합이다 — 다양성 규칙과 달리 **양보하지
    않는다.** 규칙은 어겨도 카드를 주는 게 낫지만(fallback), 필터는 사용자가 명시한
    조건이라 못 채우면 안 주는 것이 맞다.

    `tier`·`bonus` 는 `app/ranking.py` 의 정렬 보정이고 **legacy 와 같은 값**이다(인터리빙
    비교가 공정하려면 두 정책이 같은 조건에서 뽑아야 한다). 거리 보너스는 점수에 더해지고
    계층은 그 위의 1순위 키다 — 탐색 카드(10장에 1장)도 같은 순서를 쓴다.
    """
    p = swipe.pool()
    explore = (sess["served"] % EXPLORE_EVERY) == EXPLORE_EVERY - 1
    w = sess["rng"].normal(sess["m"], TS_DAMP * np.sqrt(sess["v"]))
    if explore:
        total = _uncertainty(z, sess["v"])
    elif not has_positive(sess):
        total = _cold(z, sess["m"], bool(sess["dislike_ids"] or any(not row["like"] for row in sess.get("memory", []))))
    else:
        total = w @ z
    if bonus is not None:
        total = total + bonus

    lc = sess["last_cats"]
    ban_cat = len(lc) >= 2 and lc[-1] is not None and lc[-1] == lc[-2]
    last_brand = sess["last_brands"][-1] if sess["last_brands"] else None
    fallback = None
    order = np.argsort(-total) if tier is None else np.lexsort((-total, -tier))
    for i in order:
        i = int(i)
        sid = art.ids[i]
        if sid in sess["shown"]:
            continue
        if allow is not None and sid not in allow:
            continue
        if fallback is None:
            fallback = (i, sid)
        if art.brand[i] and art.brand[i] == last_brand:
            continue                                  # 같은 브랜드 연속 금지
        if ban_cat and p["cat"].get(sid) == lc[-1]:
            continue                                  # 같은 cat2 3연속 금지
        if batch:
            near = art.sim_menu[i, batch]
            if np.isfinite(near).any() and float(np.nanmax(near)) > DUP_MENU_SIM:
                continue                              # 배치 안 근접 중복 금지
        if reasons is not None:
            reasons[sid] = recommendation_reason(sess, sid, art=art, z=z[:, i],
                                                 weights=w, explore=explore)
        _explain(explain, sess, sid, z[:, i], w, explore)
        return sid
    if fallback is None:
        return None
    i, sid = fallback
    if reasons is not None:
        reasons[sid] = recommendation_reason(sess, sid, art=art, z=z[:, i],
                                             weights=w, explore=explore)
    _explain(explain, sess, sid, z[:, i], w, explore)
    return sid


def _mark_served(sess: dict, art: content.Artifacts, store_id: str) -> None:
    sess["shown"].add(store_id)
    sess["last_cats"].append(swipe.pool()["cat"].get(store_id))
    i = art.pos.get(store_id)
    sess["last_brands"].append(art.brand[i] if i is not None else None)
    sess["served"] += 1


def mark_served(sess: dict, store_id: str) -> None:
    """상대 정책이 뽑은 카드를 이 정책에도 '보여줬다' 고 알린다 (인터리빙)."""
    art = content.load()
    if art is not None:
        _mark_served(sess, art, store_id)
    else:
        sess["shown"].add(store_id)


def pick_many(sess: dict, k: int, *, reasons: dict[str, str] | None = None,
              allow: set[str] | None = None, tiers: dict | None = None,
              bonus: dict | None = None, batch_ids=(),
              explain: dict | None = None) -> list[str]:
    """추천 카드 k장. 성분 z 는 배치 동안 한 번만 계산한다(피드백이 없으니 안 변한다).

    `allow` 를 주면 그 집합 안에서만 뽑고, 못 채우면 있는 만큼만 준다(`swipe` 와 같다).
    정렬 보정(`tiers`·`bonus`)도 배치 시작에 한 번만 배열로 펴 둔다.
    `batch_ids` 는 같은 요청의 fallback에서 이미 고른 카드와의 메뉴 다양성을 유지한다.

    **z 는 `allow` 로 좁히지 않는다 — 일부러 그렇게 뒀다.** 필터가 걸렸으니 후보 열도
    줄이는 것이 자연스러워 보이지만, 재 보면 역삼동에서는 손해다(1km 필터가 풀의 88%를
    남기고, 열을 좁히면 fancy indexing 비용이 아끼는 연산보다 크다). 측정치와 다시 볼
    시점은 저장소 루트 `explainer.md` 의 **6.1** 에 있다. 손대기 전에 그것부터 읽을 것 —
    특히 **필터가 학습(`record`)에 새면 재생 결정성이 깨진다**는 부분.
    """
    art = content.load()
    if art is None:
        return []
    like_idx, _dis, z = _batch_state(sess, art)
    tier_arr = swipe.align(art.ids, tiers)
    bonus_arr = swipe.align(art.ids, bonus)
    out: list[str] = []
    batch: list[int] = [art.pos[sid] for sid in batch_ids if sid in art.pos]
    for _ in range(max(0, k)):
        sid = _pick_one(sess, art, z, like_idx, batch, reasons, allow,
                        tier_arr, bonus_arr, explain)
        if sid is None:
            break
        _mark_served(sess, art, sid)
        out.append(sid)
        batch.append(art.pos[sid])
    return out


def score_state(sess: dict, weights: np.ndarray | None = None) -> dict | None:
    """후보 **전부**의 점수와 성분 z 를 한 번에. 세션을 하나도 건드리지 않는다.

    `peek` 는 상위 k장만 주지만 지형도(`/admin/map`)는 2,216곳 전체의 점수가 필요하다.
    `weights` 를 주면 그 가중치로 다시 계산한다 — 관리자 화면의 슬라이더가 '이 성분을
    더 믿었다면 지형이 어떻게 달라지나' 를 묻는 자리이고, **모델은 그대로다**
    (m·v 를 읽기만 한다). Thompson 난수도 쓰지 않는다 — 같은 입력이면 같은 그림이다.
    """
    art = content.load()
    if art is None:
        return None
    like_idx, dis_idx, rated = _idx(sess, art)
    z = components(sess, art)
    w = sess["m"] if weights is None else np.asarray(weights, dtype=float)
    cold = not has_positive(sess)
    total = _cold(z, w, bool(sess["dislike_ids"] or any(not row["like"] for row in sess.get("memory", [])))) if cold else w @ z
    return {"art": art, "z": z, "w": np.asarray(w, dtype=float), "total": total,
            "cold": cold, "like": like_idx, "dislike": dis_idx, "rated": rated}


def peek(sess: dict, k: int) -> list[dict]:
    """다음 카드 k장을 세션을 건드리지 않고 미리 본다 (관리자 화면용).

    Thompson 난수 없이 **사후 평균**으로 점수를 낸다 — '모델이 지금 무엇을 믿는가'
    를 보는 화면이라 매번 같은 답이 나와야 한다. 성분별 기여를 같이 준다.
    """
    art = content.load()
    if art is None:
        return []
    like_idx, _dis, z = _batch_state(sess, art)
    m = sess["m"]
    total = (_cold(z, m, bool(sess["dislike_ids"] or any(not row["like"] for row in sess.get("memory", [])))) if not has_positive(sess) else m @ z)
    out = []
    for i in np.argsort(-total):
        i = int(i)
        sid = art.ids[i]
        if sid in sess["shown"]:
            continue
        out.append({"store_id": sid, "total": round(float(total[i]), 3),
                    "parts": {c: round(float(m[j] * z[j, i]), 3)
                              for j, c in enumerate(content.COMPONENTS)}})
        if len(out) >= k:
            break
    return out


# ────────────────────────────────────────────────────────── 추천 이유
def _particle(name: str, with_final: str, without_final: str) -> str:
    """받침에 따라 조사를 고른다 ('삼겹살과' / '스시와')."""
    if not name:
        return without_final
    last = name[-1]
    if "가" <= last <= "힣":
        return with_final if (ord(last) - 0xAC00) % 28 else without_final
    return without_final


def _name(store_id: str) -> str:
    return (svc.get_store(store_id) or {}).get("name") or store_id


def _popular_reason(store_id: str) -> str:
    s = svc.get_store(store_id) or {}
    cnt = s.get("kakao_review_count") or 0
    avg = s.get("kakao_avg_score")
    if avg:
        return f"리뷰 {cnt}개·평점 {float(avg):.1f}점을 참고해 골랐어요."
    if cnt:
        return f"리뷰 {cnt}개를 참고해 골랐어요."
    return "가게의 기본 정보를 바탕으로 골랐어요."


def recommendation_reason(sess: dict | None, store_id: str, *,
                          art: content.Artifacts | None = None,
                          z: np.ndarray | None = None,
                          weights: np.ndarray | None = None,
                          explore: bool = False) -> str:
    """선택에 가장 크게 기여한 **양의** 성분 하나를 한 줄로 옮긴다.

    난수만으로 양수가 된 성분을 '이미 좋아하는 취향' 이라고 말하지 않는다 —
    사후 평균 m 도 양수인 성분만 후보로 둔다(legacy 와 같은 원칙).
    """
    if explore:
        return "새로운 취향을 찾아보려고 골랐어요."
    art = art or content.load()
    if art is None or sess is None or not sess.get("like_ids"):
        return _popular_reason(store_id)
    i = art.pos.get(store_id)
    if i is None:
        return _popular_reason(store_id)
    if z is None:
        like_idx, dis_idx, rated = _idx(sess, art)
        z = components(sess, art)[:, i]
    w = sess["m"] if weights is None else weights
    contrib = np.asarray(w) * np.asarray(z)

    like_idx = np.array([art.pos[s] for s in sess["like_ids"] if s in art.pos], dtype=int)
    best: tuple[float, str] | None = None
    for kind in ("cf", "menu", "text", "review", "food"):
        j = content.IDX[kind]
        if contrib[j] <= 0 or sess["m"][j] <= 0:
            continue
        h = art.best_like(kind, i, like_idx)
        if h is None:
            continue
        line = _reason_line(art, kind, h, i)
        if line and (best is None or contrib[j] > best[0]):
            best = (float(contrib[j]), line)
    if best is not None:
        return best[1]
    if contrib[content.IDX["pop"]] > 0 or contrib[content.IDX["quality"]] > 0:
        return _popular_reason(store_id)
    return "새로운 취향을 찾아보려고 골랐어요."


def _reason_line(art: content.Artifacts, kind: str, h: int, i: int) -> str:
    name = _name(art.ids[h])
    if kind == "review":
        snippet, _mine, _cos = art.review_pair(h, i)
        if snippet:
            return f"좋아요한 {name}의 리뷰와 닮았어요: '{snippet}'"
        return f"좋아요한 {name}의 리뷰와 닮았어요."
    if kind == "menu":
        return f"메뉴가 좋아요한 {name}{_particle(name, '과', '와')} 비슷해요."
    if kind == "cf":
        return f"{name}에 간 사람들이 자주 가는 곳이에요."
    if kind == "text":
        return f"분위기·특징이 좋아요한 {name}{_particle(name, '과', '와')} 비슷해요."
    if kind == "food":
        return f"음식 사진이 좋아요한 {name}{_particle(name, '과', '와')} 비슷해요."
    return ""


# ────────────────────────────────────────────────────────── 학습 상태
def _prototypes(art: content.Artifacts, like_ids: list[str]) -> list[dict]:
    """like 가게를 유사도로 묶은 대표 ≤ 5개 (PLAN 6절 — 계산이 아니라 **설명**용).

    세 modality 의 평균 유사도로 평균연결(average linkage) 병합을 하고, 군집마다
    나머지와 가장 가까운 가게(메도이드)를 이름으로 내세운다. 최근 60개만 본다 —
    화면에 쓰는 값이라 O(n³) 을 무한정 키울 이유가 없다.
    """
    ids = [s for s in like_ids[-PROTOTYPE_WINDOW:] if s in art.pos]
    if not ids:
        return []
    idx = np.array([art.pos[s] for s in ids], dtype=int)
    n = idx.size
    total = np.zeros((n, n), dtype=np.float32)
    cnt = np.zeros((n, n), dtype=np.float32)
    for kind, sim in (("menu", art.sim_menu), ("text", art.sim_text),
                      ("review", art.sim_review)):
        has = art.has[kind][idx]
        block = np.asarray(sim[np.ix_(idx, idx)], dtype=np.float32)
        ok = has[:, None] & has[None, :]
        total += np.where(ok, np.nan_to_num(block), 0.0)
        cnt += ok
    S = np.where(cnt > 0, total / np.maximum(cnt, 1.0), 0.0)
    np.fill_diagonal(S, 0.0)

    clusters = [[k] for k in range(n)]
    while len(clusters) > MAX_PROTOTYPES:
        best, pair = -np.inf, None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                v = float(S[np.ix_(clusters[a], clusters[b])].mean())
                if v > best:
                    best, pair = v, (a, b)
        if pair is None:
            break
        a, b = pair
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]

    out = []
    for members in clusters:
        if len(members) == 1:
            lead = members[0]
        else:
            sub = S[np.ix_(members, members)]
            lead = members[int(np.argmax(sub.sum(axis=1)))]
        out.append({"name": _name(ids[lead]), "size": len(members),
                    "stores": [_name(ids[k]) for k in members]})
    out.sort(key=lambda d: -d["size"])
    return out


def learn_state(sess: dict, *, detail: bool = True) -> dict:
    """관리자 화면용 — 가중치 m ± sd, like/dislike 수, 프로토타입, 이력.

    `detail=False` 면 **`prototypes` 와 `hist` 를 빼고, 계산도 하지 않는다.**
    이 함수는 이름대로 관리자 화면을 위한 것인데 사용자 응답(`POST /preferences`,
    `GET /users/{id}`)에도 통째로 실려 나가고 있었다. 2026-09-22 계측:

        누적선호   record ms   learn_state ms   learn 비중
             25        10.3              7.8        43%
            150        46.5            259.9        85%
            300        83.2            265.7        76%

    그 260ms 는 거의 전부 `_prototypes` 다(평균연결 병합, O(n³), 윈도 60 — 비용이 150
    부근에서 평평해지는 이유). `hist` 는 느리지 않고 **크다** — 300줄이면 약 75KB 가
    스와이프마다 휴대폰으로 나간다. 나머지 여덟 항목은 합쳐서 1ms 미만이라 그냥 둔다.

    앱은 이 값을 `debugPrint` 로 찍고 버린다(FE/app/lib/screens/home_screen.dart:363 —
    유일한 소비처). 그래서 사용자 응답에서는 빼고 관리자 경로(app/admin.py)만 그대로 받는다.
    """
    art = content.load()
    m, sd = sess["m"], np.sqrt(sess["v"])
    weights = [{"key": c, "name": content.LABELS[c],
                "w": round(float(m[j]), 3), "sd": round(float(sd[j]), 3)}
               for j, c in enumerate(content.COMPONENTS)]
    top = sorted(weights, key=lambda d: -abs(d["w"]))[:6]
    state = {
        "policy": "taste",
        "available": art is not None,
        "weights": weights,
        "top": top,
        "likes": len(sess["like_ids"]),
        "dislikes": len(sess["dislike_ids"]),
        "swipes": sess["n"],
    }
    # 이 두 항목이 이 함수 비용의 거의 전부다. detail=False 면 **부르지도 않는다** —
    # 키만 빼고 계산은 그대로 하면 얻는 것이 없다.
    if detail:
        state["prototypes"] = _prototypes(art, sess["like_ids"]) if art else []
    state["prior"] = art.prior.get("source") if art else None
    if detail:
        state["hist"] = sess.get("hist", [])[-300:]
    return state
