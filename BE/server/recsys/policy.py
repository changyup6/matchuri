# -*- coding: utf-8 -*-
"""정책 라우터 — 신·구 정책을 나란히 돌리고 **팀-드래프트 인터리빙**으로 비교한다.

PLAN 6절 '온라인 검증: shadow 가 아니라 팀-드래프트 인터리빙':

> 사용자 수가 적어 A/B 는 검정력이 없다. 10장 배치를 신·구 정책이 번갈아 뽑아 섞고,
> 각 카드에 정책 태그를 로그에 남긴다. 정책별 like 율 비교가 곧 온라인 검정이다.

shadow(구 정책으로 주고 신 정책 점수를 로그만)는 신 정책이 **실제로 보여줬을 카드**에
대한 반응을 영원히 못 본다. 인터리빙은 같은 사용자·같은 세션 안에서 두 정책을 붙이므로
사용자 간 차이가 상쇄되고, 10명 × 100 스와이프로도 신호가 잡힌다.

    MATCHURI_POLICY=interleave   (기본) 배치마다 번갈아 드래프트
    MATCHURI_POLICY=legacy       구 정책만
    MATCHURI_POLICY=taste        신 정책만

**두 정책 모두 모든 피드백을 받는다.** 어느 쪽이 뽑은 카드든 like/dislike 는 양쪽
모델에 그대로 먹인다 — 그래야 나중에 정책을 바꿔도 그 사용자의 학습이 이어지고,
정책별 성적을 '같은 이력을 본 두 모델' 로 비교할 수 있다.

**드래프트 순서는 씨앗 고정 난수다.** 사용자 id + 배치 번호로 만든 씨앗이라 같은
사용자의 같은 배치는 항상 같은 정책이 먼저 뽑는다(재생·디버깅). 선발이 한쪽으로
쏠리면 like 율 비교가 위치 편향(위쪽 카드가 더 많이 like 된다)에 오염된다.

노출은 `state/exposures.jsonl` 에 한 줄씩 남는다
(`{user_id, card_id, policy, batch, rank, at}`). 이 로그와 `preferences.jsonl` 을
조인한 것이 곧 온라인 실험 결과다(`outcome_stats`).

**트렌드 슬롯**(`MATCHURI_TREND_EVERY=N`, 기본 0 = 꺼짐)은 세 번째 드래프터가 아니라
**자리**다. N장마다 한 장을 트렌드 상위(`recsys/trend.py: slot_pick`)에서 뽑아 그 자리에
넣고 `policy="trend"` 로 라벨을 붙인다. 나머지 자리는 위의 인터리빙이 그대로 채운다.
트렌드를 무게(`MATCHURI_TREND_WEIGHT`)로 켜면 두 정책이 같이 밀려 인터리빙으로는 효과를
못 재는데, 슬롯은 라벨이 따로 붙어 **같은 사용자·같은 화면 안에서** 두 정책과 like 율을
견줄 수 있다. 슬롯 카드의 피드백도 두 정책이 똑같이 배운다(`record` 는 누가 뽑았는지
묻지 않는다). 슬롯 자리는 배치마다 씨앗 고정 난수로 정한다 — 늘 마지막 칸이면
위치 편향이 트렌드 성적에 얹힌다.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
import copy

import numpy as np

from . import content, swipe, taste

POLICIES = ("legacy", "taste", "interleave")
DEFAULT_POLICY = "interleave"
ENV = "MATCHURI_POLICY"

#: 카드를 실제로 뽑는 두 정책. 성적표의 행이고, 인터리빙의 두 팀이다.
POLICY_NAMES = ("legacy", "taste")
#: 트렌드 슬롯의 라벨. 정책이 아니라 자리지만 노출 로그에서는 같은 자리에 적힌다.
SLOT = "trend"
#: 노출 로그에 적힐 수 있는 라벨 전부. 집계·복원은 이 밖의 값을 무시한다.
LABELS = POLICY_NAMES + (SLOT,)

#: Wilson 구간의 z (95%).
WILSON_Z = 1.96

_lock = threading.RLock()
_sessions: dict[str, dict] = {}
_feedback_locks: dict[str, threading.RLock] = {}
_warned = False

#: `(user_id, store_id) -> 그 카드를 처음 노출한 정책`. 온라인 실험의 라벨을 **메모리에서**
#: 즉시 답하기 위한 것이다 — `outcome_stats` 는 파일 두 개를 조인해 사후에 계산하지만,
#: 지표(`matchuri_preferences_total{policy}`)는 선호가 들어오는 그 순간에 라벨이 필요하다.
#: **첫 노출이 이긴다**(`outcome_stats` 와 같은 규칙 — 선호는 카드 단위로 한 번 남는다).
_exposed: dict[tuple[str, str], str] = {}

#: 그 지도의 상한. 넘으면 **가장 오래 들어온 것부터** 버린다(dict 는 삽입 순서를 지킨다).
#: 20만이면 사용자 1,000명 × 카드 200장이고, 버려진 쌍은 `none` 으로 세어진다 —
#: 지표가 조금 덜 정확해지는 것이 메모리가 무한히 크는 것보다 낫다.
MAX_EXPOSED = 200_000


# ────────────────────────────────────────────────────────── 설정
def configured() -> str:
    """환경변수가 요구하는 정책. 모르는 값이면 기본값."""
    want = (os.environ.get(ENV) or DEFAULT_POLICY).strip().lower()
    return want if want in POLICIES else DEFAULT_POLICY


def active() -> str:
    """실제로 돌 정책. 서빙 산출물이 없으면 **무조건 legacy** 다.

    산출물이 없다고 죽지 않는다 — 경고 한 줄을 남기고 예전 추천을 계속 준다.
    """
    global _warned
    want = configured()
    if want == "legacy":
        return "legacy"
    if content.available():
        return want
    if not _warned:
        # `recsys` 가 `app` 을 아는 것은 층위가 뒤집힌 것이라 **부를 때만** import 한다
        # (`_log_exposures` 와 같은 규칙).
        from app import observe
        observe.log("policy.fallback", level=logging.WARNING, wanted=want,
                    using="legacy",
                    hint="python -m analysis.build_serving 로 산출물을 만드세요")
        _warned = True
    return "legacy"


# ────────────────────────────────────────────────────────── 세션
def session_for(user_id: str) -> dict:
    """정책 두 벌을 한 봉투에 담은 세션. 봉투는 얇게 두고 상태는 각 정책이 갖는다."""
    with _lock:
        sess = _sessions.get(user_id)
        if sess is None:
            # `served` 는 이 사람에게 실제로 나간 카드 수다 — 트렌드 슬롯이 "N장에 한 장" 을
            # 배치 크기와 무관하게 세는 기준(`_slot_ranks`). 재시작 때 `restore_exposures`
            # 가 노출 줄 수로 되살린다.
            sess = {"user_id": user_id, "batch": 0, "served": 0}
            _sessions[user_id] = sess
        sess.setdefault("served", 0)
        sess["legacy"] = swipe.session_for(user_id)
        sess["taste"] = taste.session_for(user_id)
        return sess


def sessions() -> dict:
    return _sessions


def in_pool(store_id: str) -> bool:
    return swipe.in_pool(store_id)


def _feedback_lock(sess):
    with _lock:
        return _feedback_locks.setdefault(sess["user_id"], threading.RLock())


def _taste_snapshot(sess):
    # No RNG or shown set: export never changes sampling/exposure state.
    # 표시 점수는 네 스칼라가 한 벌이다 — 앱에 나간 값(decision_readiness_score),
    # 반올림 전 적중 누적(decision_hit_score), 연속 적중수(hit_streak), 이전 세션에서
    # 물려받은 증가 배속(hit_carry_boost). 하나만 실으면 이 떼어낸 상태로는 "점수가
    # 어디에 있고 어떻게 거기 왔나" 를 다시 못 잰다 — 배속이 빠지면 같은 답을 재생해도
    # 궤적이 달라진다.
    # 조회 쪽(export·summarize·evaluate)은 모르는 키를 무시하고, `snapshot_id` 는
    # 명시한 학습 필드 목록으로만 만들므로 지문도 바뀌지 않는다.
    keys = ("m", "v", "like_ids", "dislike_ids", "n", "hist", "decision_checkpoints", "evidence_at", "memory",
            "decision_readiness_score", "decision_hit_score", "hit_streak", "hit_carry_boost")
    return copy.deepcopy({k: sess["taste"][k] for k in keys if k in sess["taste"]})


def taste_snapshot(sess):
    """One completed feedback state, copied under the same lock as record()."""
    with _feedback_lock(sess):
        return _taste_snapshot(sess)


def record(sess: dict, store_id: str, like: bool, *, snapshot=False):
    """**두 정책 모두** 배운다. 재생(`preferences.jsonl`)도 이 길로 들어온다."""
    with _feedback_lock(sess):
        swipe.record(sess["legacy"], store_id, like)
        sess["legacy"]["shown"].add(store_id)     # 답한 카드는 다시 내보내지 않는다
        before = sess["taste"]["n"]
        taste.record(sess["taste"], store_id, like)
        if sess["taste"]["n"] > before:
            from . import decision_summary
            decision_summary.advance_display(sess["taste"])
        sess["taste"]["shown"].add(store_id)
        return _taste_snapshot(sess) if snapshot else None


# ────────────────────────────────────────────────────────── 카드 뽑기
def _first_drafter(user_id: str, batch: int) -> str:
    """배치마다 먼저 뽑는 정책. 사용자·배치로 씨앗을 고정해 재현 가능하게 한다."""
    h = hashlib.blake2b(f"{user_id}|{batch}".encode("utf-8"), digest_size=8).digest()
    seed = int.from_bytes(h, "big")
    return "legacy" if np.random.default_rng(seed).integers(2) == 0 else "taste"


def _draft_one(name: str, sess: dict, reasons: dict | None,
               allow: set[str] | None = None, tiers: dict | None = None,
               bonus: dict | None = None, explain: dict | None = None) -> str | None:
    mod = swipe if name == "legacy" else taste
    got = mod.pick_many(sess[name], 1, reasons=reasons, allow=allow, tiers=tiers,
                        bonus=bonus, explain=explain)
    return got[0] if got else None


def _mark_other(name: str, sess: dict, store_id: str) -> None:
    """상대 정책에도 '보여줬다' 고 알린다 — 중복 카드와 연속 규칙을 함께 막는다."""
    other = "taste" if name == "legacy" else "legacy"
    (swipe if other == "legacy" else taste).mark_served(sess[other], store_id)


def _mark_both(sess: dict, store_id: str) -> None:
    """두 정책 모두에 '보여줬다' 고 알린다 — 트렌드 슬롯이 뽑은 카드는 어느 쪽 것도 아니다."""
    swipe.mark_served(sess["legacy"], store_id)
    taste.mark_served(sess["taste"], store_id)


def _seed(*parts) -> int:
    h = hashlib.blake2b("|".join(str(p) for p in parts).encode("utf-8"),
                        digest_size=8).digest()
    return int.from_bytes(h, "big")


def _slot_ranks(user_id: str, batch: int, served: int, k: int) -> list[int]:
    """이번 배치에서 트렌드 슬롯이 차지할 자리(rank). 꺼져 있으면 빈 리스트.

    **몇 장인가**는 누적 카드 수로 센다 — 이 사람에게 나간 카드가 `every` 의 배수를 지날
    때마다 한 장. 배치 크기로 나누면 `limit=5` 인 클라이언트는 영원히 슬롯을 못 받고,
    배치마다 한 장이면 `limit=3` 에서 셋에 하나가 트렌드가 된다. 누적으로 세면 배치 크기와
    무관하게 정확히 N장에 한 장이다.

    **어느 자리인가**는 씨앗 고정 난수다(`_first_drafter` 와 같은 이유). 늘 마지막 칸이면
    위치 편향(위쪽 카드가 더 많이 like 된다)이 트렌드 성적에 그대로 얹힌다.
    """
    from . import trend
    every = trend.slot_every()
    if every <= 0 or k <= 0:
        return []
    n = sum(1 for i in range(served, served + k) if i % every == every - 1)
    if n == 0:
        return []
    rng = np.random.default_rng(_seed(user_id, batch, SLOT))
    return sorted(int(r) for r in rng.choice(k, size=min(n, k), replace=False))


def _draft_trend(sess: dict, n: int, batch: int, reasons: dict | None,
                 allow: set[str] | None, tiers: dict | None,
                 explain: dict | None = None) -> list[str]:
    """트렌드 슬롯 카드 n장. 후보는 (추천 후보 풀 ∩ 허용 집합 − 두 정책이 보여준 카드) 이다.

    **풀에서 출발한다.** 허용 집합(`filters.allow_ids`)은 전체 가게(2,795곳)에서 만들어서 풀
    (1,498곳) 밖 가게도 들어 있다. 두 정책은 풀을 돌며 집합으로 거르기만 하니 상관없지만, 집합을
    그대로 후보로 쓰면 카드 자격(`swipe._build_pool` — 리뷰 · 음식 대표 사진 · 가격)이 없는 가게가
    '뜨는 가게' 로 나간다. 메뉴가 없는 가게는 업종 평균을 트렌드 값으로 받아서 업종이 오르면 z 가
    양수로 뜬다(2026-09-22 실측: 필터를 건 사용자에게 메뉴·사진 없는 술집이 트렌드 카드로 나갔다).

    뽑은 카드는 **두 정책 모두에** 보여줬다고 알린다 — 그래야 뒤이은 드래프트가 같은
    카드를 또 뽑지 않고, '같은 업종 3연속 금지' 같은 규칙도 실제 화면을 본다.
    후보가 없으면 빈 리스트고, 그 자리는 두 정책이 채운다(`trend.slot_pick` 참조).

    슬롯 카드끼리는 다양성 규칙을 안 건다 — 한 배치에 슬롯이 둘이면(`every=5`) 같은 업종이
    나란히 올 수 있다. 10장에 1장(`every=10`)이 기본 권장값이라 실제로는 한 장이고,
    규칙을 걸면 '오르는 가게' 라는 슬롯의 뜻이 흐려진다. 필요해지면 `slot_pick` 에 건다.
    """
    from . import trend
    if n <= 0:
        return []
    shown = sess["legacy"]["shown"] | sess["taste"]["shown"]
    ids = [sid for sid in swipe.pool()["ids"]
           if (allow is None or sid in allow) and sid not in shown]
    got = trend.slot_pick(ids, n, seed=_seed(sess["user_id"], batch, SLOT, "pick"),
                          tiers=tiers)
    parts = trend.breakdown(ids) if (got and explain is not None) else {}
    for sid in got:
        _mark_both(sess, sid)
        if reasons is not None:
            reasons[sid] = "요즘 찾는 사람이 늘고 있는 메뉴를 파는, 뜨는 가게예요."
        if explain is not None:
            ex = trend.explain(sid) or {}
            explain[sid] = {"sources": {k: round(v, 4) for k, v in (parts.get(sid) or {}).items()},
                            "menu": [m["keyword"] for m in ex.get("menu") or []]}
    return got


def _fresh_allow(sess, allow):
    """Try alternatives before the exact stores rejected in earlier sessions.

    This is a shared serving priority, not a taste-vector penalty or a permanent
    exclusion. Both drafters first use this pool, then the original allowed pool
    if alternatives run out. Current-session labels supersede inherited labels.
    """
    model = sess["taste"]
    answered = set(model["like_ids"] + model["dislike_ids"])
    deferred = {row["store_id"] for row in model.get("memory", ())
                if not row["like"] and row["store_id"] not in answered}
    if not deferred:
        return allow
    return (set(swipe.pool()["ids"]) if allow is None else allow) - deferred


def pick_many(sess: dict, k: int, *, reasons: dict[str, str] | None = None,
              tags: dict[str, str] | None = None,
              allow: set[str] | None = None,
              tiers: dict | None = None, bonus: dict | None = None,
              card_id_of=None, explain: dict | None = None) -> list[str]:
    """카드 k장. `tags` 를 주면 카드마다 어느 정책이 뽑았는지 채워 준다.

    `explain` 을 주면 카드마다 **점수가 어디서 왔는지**(성분별 기여·탐색 여부·트렌드 출처)를
    채워 준다 — 응답의 `attribution` 재료다. 정책마다 모양이 다르고(`taste._explain` ·
    `swipe.contributions` · 트렌드는 `sources`), 합치는 일은 `main` 이 한다.

    인터리빙은 **한 장씩 번갈아** 드래프트한다. 정책마다 다양성 규칙(같은 브랜드
    연속 금지 등)이 다르므로 각자 자기 규칙으로 한 장을 고르게 하고, 고른 카드는
    양쪽에 '보여줬다' 고 알린다. 그래서 중복이 나올 수 없다.

    `allow`(허용 집합)와 `tiers`·`bonus`(정렬 보정, `app/ranking.py`)는 **두 정책에 그대로
    내려간다** — 한쪽만 보정을 받으면 '같은 조건에서 누가 더 잘 맞히나' 라는 실험이 무의미해진다.
    한쪽이 집합 안에서 더 못 뽑으면 상대에게 넘기지만, 상대도 없으면 거기서 끝낸다 —
    필터 때문에 k장을 못 채우는 것은 정상이다. 노출·`shown` 의 의미는 그대로다
    (실제로 뽑힌 카드만 기록된다).
    """
    mode = active()
    preferred = _fresh_allow(sess, allow)
    k = max(0, k)
    batch = sess["batch"]
    sess["batch"] = batch + 1
    served = sess.get("served", 0)

    # 트렌드 슬롯을 **먼저** 뽑는다 — 두 정책이 드래프트하기 전에 자리를 잡아야 같은 카드가
    # 겹치지 않는다(뽑은 카드는 양쪽에 '보여줬다' 고 알린다). 후보가 없으면 빈 리스트고,
    # 그만큼 두 정책이 채운다 — 슬롯은 채워야 하는 자리가 아니다.
    slot_ranks = _slot_ranks(sess["user_id"], batch, served, k)
    slot_picks = _draft_trend(sess, len(slot_ranks), batch, reasons, preferred, tiers, explain)
    k_main = k - len(slot_picks)

    main: list[tuple[str, str]] = []
    if mode in POLICY_NAMES:
        mod = swipe if mode == "legacy" else taste
        got = mod.pick_many(sess[mode], k_main, reasons=reasons, allow=preferred,
                            tiers=tiers, bonus=bonus, explain=explain)
        if len(got) < k_main and preferred is not allow:
            continuation = {"batch_ids": got} if mode == "taste" else {}
            got += mod.pick_many(sess[mode], k_main - len(got), reasons=reasons,
                                 allow=allow, tiers=tiers, bonus=bonus, explain=explain,
                                 **continuation)
        for sid in got:
            _mark_other(mode, sess, sid)
        main = [(sid, mode) for sid in got]
    else:
        first = _first_drafter(sess["user_id"], batch)
        order = [first, "taste" if first == "legacy" else "legacy"]
        for rank in range(k_main):
            name = order[rank % 2]
            sid = _draft_one(name, sess, reasons, preferred, tiers, bonus, explain)
            if sid is None:                      # 그 정책이 (허용 집합 안에서) 다 썼다
                name = order[(rank + 1) % 2]
                sid = _draft_one(name, sess, reasons, preferred, tiers, bonus, explain)
                if sid is None and preferred is not allow:
                    # An inherited dislike must not make a narrow filter empty.
                    name = order[rank % 2]
                    sid = _draft_one(name, sess, reasons, allow, tiers, bonus, explain)
                    if sid is None:
                        name = order[(rank + 1) % 2]
                        sid = _draft_one(name, sess, reasons, allow, tiers, bonus, explain)
                if sid is None:
                    break
            _mark_other(name, sess, sid)
            main.append((sid, name))

    picked = _place_slots(main, slot_picks, slot_ranks)
    out = [sid for sid, _ in picked]
    if tags is not None:
        for sid, name in picked:
            tags[sid] = name
    sess["served"] = served + len(out)
    _log_exposures(sess, batch, picked, card_id_of)
    return out


def _place_slots(main: list[tuple[str, str]], slot_picks: list[str],
                 slot_ranks: list[int]) -> list[tuple[str, str]]:
    """두 정책이 뽑은 카드 사이에 트렌드 카드를 정해진 자리(rank)에 끼운다.

    필터 때문에 두 정책이 k장을 못 채웠으면 화면이 짧아지고, 슬롯 자리가 그 끝을 넘으면
    트렌드 카드는 남은 자리 중 마지막으로 밀린다 — 버리지 않는다(이미 '보여줬다' 고
    표시했으므로 버리면 그 카드는 이 사람에게 영원히 안 나온다).
    """
    total = len(main) + len(slot_picks)
    want = set(slot_ranks[:len(slot_picks)])
    out: list[tuple[str, str]] = []
    mi = si = 0
    for rank in range(total):
        take_slot = si < len(slot_picks) and (rank in want or mi >= len(main))
        if take_slot:
            out.append((slot_picks[si], SLOT))
            si += 1
        else:
            out.append(main[mi])
            mi += 1
    return out


def remember_exposure(user_id: str, store_id: str, name: str) -> None:
    """'이 사람에게 이 카드를 보여준 정책' 을 기억한다. 첫 노출이 이긴다."""
    with _lock:
        key = (user_id, store_id)
        if key in _exposed:
            return
        _exposed[key] = name
        while len(_exposed) > MAX_EXPOSED:
            _exposed.pop(next(iter(_exposed)))


def exposed_by(user_id: str, store_id: str) -> str | None:
    """그 카드를 보여준 정책. 노출 기록이 없으면 None(찜 목록에서 바로 평가한 경우 등)."""
    return _exposed.get((user_id, store_id))


def exposures_known() -> int:
    return len(_exposed)


def restore_exposures(to_store_id) -> int:
    """`state/exposures.jsonl` 로 노출 지도를 다시 만든다(기동 1회).

    재시작 때 이걸 안 하면 그 뒤의 선호가 전부 `policy="none"` 으로 세어져 인터리빙
    비교가 배포마다 끊긴다. `to_store_id(card_id)` 는 main 이 넘겨주는 변환 함수다 —
    파일에는 `card_id` 가 있고 지도의 키는 `store_id` 이기 때문이다.
    """
    from app import state, recommendation_sessions as rec_sessions, identities
    n = 0
    for e in state.read_exposures():
        uid, cid, name = e.get("user_id"), e.get("card_id"), e.get("policy")
        if uid is None or cid is None or name not in LABELS:
            continue
        if (e.get('session_id') or rec_sessions.legacy_id(uid)) != rec_sessions.current_id(uid):
            old_sid = e.get('session_id') or rec_sessions.legacy_id(uid)
            if identities.owner(old_sid) != identities.owner(uid) or not identities.owner(uid):
                continue
            uid = old_sid
        sid = to_store_id(int(cid))
        if sid is None:
            continue
        remember_exposure(uid, sid, name)
        model = session_for(uid)
        model['legacy']['shown'].add(sid)
        model['taste']['shown'].add(sid)
        model['batch'] = max(model['batch'], int(e.get('batch', 0)) + 1)
        model['served'] = model.get('served', 0) + 1     # 트렌드 슬롯의 "N장에 한 장" 기준
        n += 1
    return n


def _log_exposures(sess: dict, batch: int, picked: list[tuple[str, str]],
                   card_id_of) -> None:
    """노출 기록. 실패해도 추천은 계속돼야 하므로 조용히 넘어간다.

    `recsys` 가 `app` 을 아는 것은 층위가 뒤집힌 것이라 **호출 시점에만** import
    한다(모듈 로딩 순서에 의존하지 않게). 기록이 없으면 인터리빙 실험만 못 잰다.
    """
    if not picked:
        return
    for sid, name in picked:                     # 지도는 파일과 무관하게 늘 채운다
        remember_exposure(sess["user_id"], sid, name)
    try:
        from app import loader, state, recommendation_sessions as rec_sessions
        for rank, (sid, name) in enumerate(picked):
            cid = card_id_of(sid) if card_id_of else loader.card_id_by_store.get(sid)
            state.record_exposure(sess["user_id"], cid, name, batch, rank, rec_sessions.current_id(sess["user_id"]))
    except Exception:                              # noqa: BLE001
        pass


def peek(sess: dict, k: int) -> list[dict]:
    return swipe.peek(sess["legacy"], k)


def learn_state(sess: dict, *, detail: bool = True) -> dict:
    """legacy 응답 **위에** 새 정책을 얹는다 — 기존 계약의 필드는 하나도 안 없앤다."""
    base = swipe.learn_state(sess["legacy"], detail=detail)
    return {**base, "policy": active(), "configured_policy": configured(),
            "policies": {"legacy": base,
                         "taste": taste.learn_state(sess["taste"], detail=detail)}}


def recommendation_reason(sess: dict | None, store_id: str) -> str:
    """카드 하나의 이유(정책 밖에서 물어볼 때). 개인화 정보가 없으면 품질 설명이다."""
    if sess is None:
        return swipe.recommendation_reason(None, store_id)
    if active() == "taste":
        return taste.recommendation_reason(sess["taste"], store_id)
    return swipe.recommendation_reason(sess["legacy"], store_id)


# ────────────────────────────────────────────────────────── 온라인 실험 결과
def wilson(k: int, n: int, z: float = WILSON_Z) -> tuple[float, float] | None:
    """이항 비율의 Wilson 95% 구간. n 이 작을 때 정규근사보다 정직하다."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def outcome_stats() -> dict:
    """노출 로그 × 선호 기록 — 정책별 노출·like·dislike·무응답과 like 율.

    같은 카드가 여러 번 노출됐다면 **첫 노출**을 그 카드의 정책으로 본다(선호는 카드
    단위로 한 번 남는다). 판단은 like 율 하나로 하지 않는다 — Wilson 구간이 겹치면
    '아직 모른다' 이다.

    `by_policy` 의 셋째 행은 트렌드 슬롯(`trend`)이다. `verdict` 는 예전처럼 legacy 대
    taste 만 본다 — 슬롯은 그 실험을 방해하지 않아야 하므로 판정도 따로다(`slot.verdict`).
    슬롯의 비교 상대는 **두 정책을 합친 것**이다: 슬롯 카드가 밀어낸 자리는 어느 한 정책의
    자리가 아니라 인터리빙의 자리이기 때문이다.
    """
    from app import state
    from app.recommendation_sessions import legacy_id
    from . import trend
    rated: dict[tuple[str, str, int], str] = {}
    for p in state.read_preferences():
        uid, cid, pref = p.get("user_id"), p.get("card_id"), p.get("prefer")
        if uid is None or cid is None or pref not in ("like", "dislike"):
            continue
        rated[(uid, p.get("session_id") or legacy_id(uid), int(cid))] = pref

    per: dict[str, dict] = {}
    seen: set[tuple[str, str, int]] = set()
    n_exposures = 0
    for e in state.read_exposures():
        uid, cid, name = e.get("user_id"), e.get("card_id"), e.get("policy")
        if uid is None or cid is None or name not in LABELS:
            continue
        n_exposures += 1
        key = (uid, e.get("session_id") or legacy_id(uid), int(cid))
        if key in seen:
            continue
        seen.add(key)
        row = per.setdefault(name, {"shown": 0, "liked": 0, "disliked": 0,
                                    "unrated": 0, "users": set()})
        row["shown"] += 1
        row["users"].add(uid)
        pref = rated.get(key)
        if pref == "like":
            row["liked"] += 1
        elif pref == "dislike":
            row["disliked"] += 1
        else:
            row["unrated"] += 1

    def _row(name: str, row: dict | None) -> dict:
        if row is None:
            return {"policy": name, "shown": 0, "liked": 0, "disliked": 0,
                    "unrated": 0, "rated": 0, "users": 0,
                    "like_rate": None, "ci95": None}
        rated_n = row["liked"] + row["disliked"]
        ci = wilson(row["liked"], rated_n)
        return {"policy": name, "shown": row["shown"], "liked": row["liked"],
                "disliked": row["disliked"], "unrated": row["unrated"],
                "rated": rated_n, "users": len(row["users"]),
                "like_rate": round(row["liked"] / rated_n, 4) if rated_n else None,
                "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None}

    out = [_row(name, per.get(name)) for name in LABELS]

    verdict = "노출이 없습니다"
    a, b, t = out[0], out[1], out[2]
    if a["ci95"] and b["ci95"]:
        if a["ci95"][0] > b["ci95"][1]:
            verdict = "legacy 가 앞섭니다 (구간이 겹치지 않음)"
        elif b["ci95"][0] > a["ci95"][1]:
            verdict = "taste 가 앞섭니다 (구간이 겹치지 않음)"
        else:
            verdict = "아직 모릅니다 (95% 구간이 겹칩니다)"
    elif n_exposures:
        verdict = "응답이 더 필요합니다"

    # 슬롯 판정 — 상대는 legacy+taste 를 **합친** 인터리빙 카드다(함수 문서 참조).
    pooled = _row("interleaved", None if (per.get("legacy") is None and per.get("taste") is None)
                  else {"shown": a["shown"] + b["shown"], "liked": a["liked"] + b["liked"],
                        "disliked": a["disliked"] + b["disliked"],
                        "unrated": a["unrated"] + b["unrated"],
                        "users": set().union(*(per[n]["users"] for n in POLICY_NAMES
                                               if n in per))})
    if t["ci95"] and pooled["ci95"]:
        if t["ci95"][0] > pooled["ci95"][1]:
            slot_verdict = "트렌드 슬롯이 앞섭니다 (구간이 겹치지 않음)"
        elif pooled["ci95"][0] > t["ci95"][1]:
            slot_verdict = "트렌드 슬롯이 뒤집니다 (구간이 겹치지 않음)"
        else:
            slot_verdict = "아직 모릅니다 (95% 구간이 겹칩니다)"
    elif t["shown"]:
        slot_verdict = "응답이 더 필요합니다"
    elif trend.slot_enabled():
        slot_verdict = "슬롯이 켜져 있지만 아직 노출이 없습니다"
    else:
        slot_verdict = "슬롯이 꺼져 있습니다 (MATCHURI_TREND_EVERY=0)"
    return {"policy": active(), "configured_policy": configured(),
            "artifacts": content.available(), "exposures": n_exposures,
            "by_policy": out, "verdict": verdict,
            "slot": {"enabled": trend.slot_enabled(), "every": trend.slot_every(),
                     "trend": t, "interleaved": pooled, "verdict": slot_verdict}}

#: 설문의 세부 문항(1~3점). 없는 키는 평균에서 빠진다 — 옛 응답에는 이 문항이 없다.
#:
#: **2026-09-16 이후 응답에는 이 수치가 없다.** 설문이 점수 다섯 개에서 '만족도 + 후기
#: 세 줄'(맛·가격·분위기, 글)로 바뀌었다(`app/model.py: SurveyIn`). 그래서 새 응답의
#: `detail_avg` 는 전부 `None` 이다 — 버그가 아니다. 목록을 그대로 두는 이유는 그 전에
#: 쌓인 응답을 계속 집계해야 하고, 문자열이 섞여 들어와도 아래 `isinstance` 검사가
#: 걸러 내기 때문이다.
#:
#: 후기가 따로 세어지지 않는 이유: 방문했으면 후기가 **필수**이므로 `visited` 수가
#: 곧 후기 수다. 같은 것을 두 번 세지 않는다.
SURVEY_DETAILS = ("taste", "hygiene", "price", "mood", "wait")

#: 이보다 빨리 답한 것은 배너를 통하지 않은 응답이다(`app/journeys.py: SURVEY_AFTER`).
#: 버리지 않고 `answered_early` 로 세어 둔다 — 많으면 그 표본을 의심해야 한다.
SURVEY_AFTER_HOURS = 3.0


def _rate(num: int, den: int):
    """`num/den` 을 소수 넷째 자리로. 분모가 0 이면 `None` — **0 이 아니다.**

    0 으로 주면 "아무도 안 갔다" 로 읽히지만 실제로는 "아직 아무도 안 왔다" 다.
    콜드스타트에 그 둘을 섞으면 화면이 거짓말을 한다.
    """
    return round(num / den, 4) if den else None


def _dropoff(policy_of: dict, directed: set, answers: dict) -> dict:
    """**카드를 확인했는데 방문까지 안 간 비율** — 단계별 이탈.

        노출 → 좋아요 → 길찾기 → 설문 응답 → 방문

    **'방문하지 않음' 은 두 종류다.** 답을 하고 "안 갔다" 고 말한 것(`not_visited`)과,
    답을 안 해서 모르는 것(`unanswered`)이다. 합치면 이탈이 과대 계상된다 — 설문에
    답하지 않은 사람 대부분은 귀찮아서 안 한 것이고 안 간 것이 아니다. 그래서 두 수를
    따로 주고, 비율의 분모도 각각 다르게 둔다.

    비율 이름에 **분모를 박아 둔다**(`_of_` 뒤가 분모다). 이탈률은 어느 단계를 분모로
    잡느냐로 값이 열 배씩 달라져서, 이름이 모호하면 반드시 잘못 읽힌다.
    """
    from app import state, identities

    exposed: set[tuple[str, int]] = set(policy_of)
    liked: set[tuple[str, int]] = set()
    for pref in state.read_preferences():
        uid, cid = pref.get("user_id"), pref.get("card_id")
        if uid is None or cid is None or pref.get("prefer") != "like":
            continue
        # 노출·방문과 같은 사람 기준으로 합쳐야 여러 세션의 좋아요가 중복되지 않는다.
        liked.add((identities.bookmark_key(uid), int(cid)))

    # 노출 기록이 없는 좋아요·길찾기도 분모에 넣는다 — `user_id` 없이 받은 카드는
    # 정책이 `popular` 라 노출에 라벨이 없다(그래도 확인한 카드인 것은 맞다).
    exposed |= liked | directed
    answered = directed & set(answers)
    visited = {k for k in answered if answers[k].get("visited") is True}
    not_visited = answered - visited

    return {
        "exposed": len(exposed),
        "liked": len(liked),
        "directed": len(directed),
        "answered": len(answered),
        "visited": len(visited),
        # 답을 했고 "안 갔다" 고 말한 것. 확실한 이탈이다.
        "not_visited": len(not_visited),
        # 길찾기했는데 답이 없는 것. **이탈이 아니라 '모름' 이다.**
        "unanswered": len(directed) - len(answered),
        # 좋아요했는데 길찾기까지 안 간 것.
        "liked_not_directed": len(liked - directed),
        "rates": {
            "liked_of_exposed": _rate(len(liked), len(exposed)),
            "directed_of_liked": _rate(len(directed & liked), len(liked)),
            "answered_of_directed": _rate(len(answered), len(directed)),
            # **이게 '확인했음에도 방문하지 않은' 비율이다** — 답한 것 중에서만 센다.
            "not_visited_of_answered": _rate(len(not_visited), len(answered)),
            # 길찾기까지 갔는데 방문을 확인하지 못한 비율(안 간 것 + 모르는 것).
            # 위 값과 달리 분모가 길찾기라 **훨씬 크다.** 둘을 섞지 말 것.
            "unconfirmed_of_directed": _rate(len(directed) - len(visited),
                                             len(directed)),
        },
    }


def survey_stats() -> dict:
    """노출 로그 × 설문 — 정책별 **방문율과 만족도**. 길찾기는 중간 단계다.

    `outcome_stats` 의 like 율은 대리 지표다(카드를 보고 오른쪽으로 넘긴 것뿐이다).
    이 함수가 재는 것은 **실제로 가서 어땠는지**다. 조인 키가 `(user_id, card_id)` 로
    같으므로 정책 라벨이 퍼널 끝까지 따라온다:

        노출 → (길찾기) → 설문 → 방문 → 만족

    **길찾기는 선택이다.** 앱이 방문 판단을 스스로 하고 설문만 보내면 그 단계가 비고,
    `directions` 는 0 이지만 `surveyed` 이후는 그대로 나온다. 정책 비교에 필요한 것은
    노출과 설문뿐이다 — 둘 다 서버가 자기 로그로 갖고 있다.

    규칙 셋은 `outcome_stats` 와 맞춘다.
      * 같은 카드가 여러 번 노출됐으면 **첫 노출**의 정책을 그 카드의 정책으로 본다.
      * 길찾기를 여러 번 눌렀어도 `(사람, 카드)` 단위로 **한 번** 센다(`directions` 총계는
        줄 수 그대로라서 '몇 번 눌렀나' 와 '몇 곳을 눌렀나' 가 둘 다 보인다).
      * 같은 카드에 응답이 여러 줄이면 **마지막 줄**이 이긴다(추가 전용 로그의 관례).

    `user_id` 없이 받은 카드는 정책이 `popular` 라 라벨이 없다. 그런 길찾기는 `none`
    버킷에 담는다 — 버리면 총계가 맞지 않아 퍼널을 못 믿는다.

    만족은 `satisfaction == "good"` 만 센다. `soso` 를 만족으로 치면 지표가 무뎌진다 —
    화면에는 😐 로 보이지만 만족 쪽에 넣지 않는다. `satisfied_rate` 를 읽을 때
    분모는 `rated`(good+soso+bad) 이고 분자는 `good` 뿐임을 기억할 것.

    `by_source` 는 응답이 어디서 왔나다 — `direction`(배너를 거쳤다) · `app`(앱이 직접
    판단해 보냈다) · `unknown`(이 필드가 없던 시절). 두 경로의 만족도가 크게 다르면
    한쪽 표본을 의심해야 한다.
    """
    from app import state, identities

    # Recommendation exposure is session-scoped; visits belong to the person.
    # Preserve the existing first-exposure attribution across that boundary.
    policy_of: dict[tuple[str, int], str] = {}
    for e in state.read_exposures():
        uid, cid, name = e.get("user_id"), e.get("card_id"), e.get("policy")
        if uid is None or cid is None or name not in LABELS:
            continue
        policy_of.setdefault((identities.bookmark_key(uid), int(cid)), name)

    # 길찾기
    directed: set[tuple[str, int]] = set()
    n_direction_rows = 0
    for d in state.read_directions():
        uid, cid = d.get("user_id"), d.get("card_id")
        if uid is None or cid is None:
            continue
        n_direction_rows += 1
        directed.add((uid, int(cid)))

    # 설문 — 마지막 줄이 이긴다
    answers: dict[tuple[str, int], dict] = {}
    for a in state.read_surveys():
        uid, cid = a.get("user_id"), a.get("card_id")
        if uid is None or cid is None:
            continue
        answers[(uid, int(cid))] = a

    def _blank() -> dict:
        return {"directions": 0, "surveyed": 0, "visited": 0, "not_visited": 0,
                "satisfaction": {"good": 0, "soso": 0, "bad": 0},
                "detail_sum": {k: 0.0 for k in SURVEY_DETAILS},
                "detail_n": {k: 0 for k in SURVEY_DETAILS},
                "hours_sum": 0.0, "hours_n": 0, "early": 0,
                "users": set()}

    # 셋째 버킷 `trend` 는 트렌드 슬롯이 보여준 카드다(`outcome_stats` 와 같은 라벨).
    buckets = LABELS + ("none",)
    per: dict[str, dict] = {b: _blank() for b in buckets}
    reasons: dict[str, int] = {}
    #: 설문 후보가 어디서 나왔나. 이 필드가 없던 시절의 응답은 `unknown` 이다.
    by_source: dict[str, int] = {}

    # **앵커는 길찾기가 아니라 (노출 ∪ 설문) 이다.**
    #
    # 길찾기만 순회하면 길찾기 기록 없는 설문이 지표에서 통째로 사라진다. 앱이 방문
    # 판단을 스스로 하고 설문만 보내는 경로(`app/journeys.py: answer`)가 기본이 됐으므로
    # 그대로 두면 새 응답이 하나도 안 세어진다. 조인 키 `(user_id, card_id)` 는 노출
    # 로그에 이미 있고 그건 서버가 자동으로 남기므로, 클라이언트 호출에 의존하지 않는다.
    #
    # 합집합인 이유: 길찾기만 하고 답하지 않은 것도 세어야 `directions` 대비 응답률이
    # 나오고, 노출 없이 들어온 설문(`popular` 로 받은 카드)도 `none` 버킷에 담긴다.
    for key in set(directed) | set(answers):
        name = policy_of.get(key, "none")
        row = per[name]
        row["users"].add(key[0])
        if key in directed:
            row["directions"] += 1
        ans = answers.get(key)
        if ans is None:
            continue
        row["surveyed"] += 1
        src = str(ans.get("source") or "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        if ans.get("visited") is True:
            row["visited"] += 1
        else:
            row["not_visited"] += 1
        detail = ans.get("answers") or {}
        why = detail.get("not_visited_reason")
        if ans.get("visited") is not True and why:
            reasons[str(why)] = reasons.get(str(why), 0) + 1
        sat = detail.get("satisfaction")
        if sat in row["satisfaction"]:
            row["satisfaction"][sat] += 1
        # 길찾기 후 몇 시간 뒤에 답했나. 3시간 전 응답은 배너를 통하지 않은 것이므로
        # `answered_early` 로 따로 세어 둔다 — 표본을 버리지 않고 의심만 표시한다.
        ha = ans.get("hours_after")
        if isinstance(ha, (int, float)) and not isinstance(ha, bool):
            row["hours_sum"] += float(ha)
            row["hours_n"] += 1
            if ha < SURVEY_AFTER_HOURS:
                row["early"] += 1
        for k in SURVEY_DETAILS:
            v = detail.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                row["detail_sum"][k] += float(v)
                row["detail_n"][k] += 1

    out = []
    for name in buckets:
        row = per[name]
        sat = row["satisfaction"]
        rated = sat["good"] + sat["soso"] + sat["bad"]
        v_ci = wilson(row["visited"], row["surveyed"])
        s_ci = wilson(sat["good"], rated)
        out.append({
            "policy": name,
            "directions": row["directions"],
            "surveyed": row["surveyed"],
            "visited": row["visited"],
            "not_visited": row["not_visited"],
            "visit_rate": (round(row["visited"] / row["surveyed"], 4)
                           if row["surveyed"] else None),
            "visit_ci95": [round(v_ci[0], 4), round(v_ci[1], 4)] if v_ci else None,
            "satisfaction": dict(sat),
            "rated": rated,
            "satisfied_rate": round(sat["good"] / rated, 4) if rated else None,
            "satisfied_ci95": [round(s_ci[0], 4), round(s_ci[1], 4)] if s_ci else None,
            "detail_avg": {k: (round(row["detail_sum"][k] / row["detail_n"][k], 2)
                               if row["detail_n"][k] else None)
                           for k in SURVEY_DETAILS},
            "hours_after_avg": (round(row["hours_sum"] / row["hours_n"], 1)
                                if row["hours_n"] else None),
            "answered_early": row["early"],
            "users": len(row["users"]),
        })

    a, b = out[0], out[1]                       # legacy, taste
    if a["satisfied_ci95"] and b["satisfied_ci95"]:
        if a["satisfied_ci95"][0] > b["satisfied_ci95"][1]:
            verdict = "legacy 가 앞섭니다 (구간이 겹치지 않음)"
        elif b["satisfied_ci95"][0] > a["satisfied_ci95"][1]:
            verdict = "taste 가 앞섭니다 (구간이 겹치지 않음)"
        else:
            verdict = "아직 모릅니다 (95% 구간이 겹칩니다)"
    elif not directed:
        verdict = "길찾기가 없습니다"
    elif not answers:
        verdict = "설문 응답이 없습니다"
    else:
        verdict = "응답이 더 필요합니다"

    total_surveyed = sum(r["surveyed"] for r in out)
    #: 길찾기 기록이 있는 것 중 답이 온 것. **응답률의 분모는 길찾기여야 한다** —
    #: 앱이 직접 판단해 보낸 설문까지 분자에 넣으면 비율이 1 을 넘는다(그런 응답에는
    #: 대응하는 길찾기가 없다).
    answered_directed = len(directed & set(answers))
    return {
        "direction_rows": n_direction_rows,
        "dropoff": _dropoff(policy_of, directed, answers),
        "funnel": {"directed_cards": len(directed),
                   "surveyed_cards": total_surveyed,
                   # 길찾기 없이 들어온 설문. 앱이 방문 판단을 스스로 한 경로다 —
                   # 이 값이 곧 '배너를 안 거친 응답' 의 수이므로 표본 성격을 가른다.
                   "surveyed_without_direction": total_surveyed - answered_directed,
                   "visited": sum(r["visited"] for r in out),
                   "satisfied": sum(r["satisfaction"]["good"] for r in out)},
        "response_rate": (round(answered_directed / len(directed), 4)
                          if directed else None),
        "by_policy": out,
        "by_source": by_source,
        "not_visited_reasons": reasons,
        "verdict": verdict,
    }
