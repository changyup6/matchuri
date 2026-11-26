# -*- coding: utf-8 -*-
"""재생 복원 — `state/preferences.jsonl` 을 다시 먹이면 두 정책이 그대로 살아난다.

PLAN 3절: "재시작 시 `state/preferences.jsonl` 재생으로 정확히 복원돼야 한다
(난수는 선택에만)." 이 테스트가 그 문장의 검사다. **진짜 산출물**을 쓴다 —
합성 풀에서 되는 것과 2,216곳에서 되는 것은 다른 주장이다.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from recsys import content, policy, swipe, taste

pytestmark = pytest.mark.skipif(
    not content.available(),
    reason="data/kakao/serving 이 없다 (python -m analysis.build_serving)")

#: 재생한 실서버 사용자의 어떤 성분도 넘으면 안 되는 절댓값 (Step 3d).
#:
#: 사전값이 |1.0| 이하이고 한 걸음이 `taste.MAX_STEP` = 0.5 이므로, 정상적인 사람이
#: 여기 닿으려면 같은 방향으로 열 걸음을 연달아 가야 한다. 2026-09-09 실서버 기록
#: (사용자 32명 · 1,476건)의 최대는 **2.01** 이었다 — 고치기 전에는 **63.63** 이었다.
MAX_ABS_W = 5.0

SEQ = [("like", 0), ("dislike", 1), ("like", 2), ("like", 3), ("dislike", 4),
       ("dislike", 5), ("like", 6), ("like", 7), ("dislike", 8), ("like", 9)]


def _card_ids(n: int) -> list[int]:
    """추천 풀 안의 카드 n 장 — 재생 대상이 학습에 실제로 들어가야 한다."""
    from app import loader
    loader.load_all()
    out = []
    for sid in content.load().ids:
        cid = loader.card_id_by_store.get(sid)
        if cid is not None:
            out.append(cid)
        if len(out) >= n:
            break
    return out


def _drop(user_id: str) -> None:
    policy._sessions.pop(user_id, None)
    swipe._sessions.pop(user_id, None)
    taste._sessions.pop(user_id, None)


def test_재생하면_두_정책의_학습상태가_같다(tmp_path, monkeypatch):
    import main
    from app import state

    cards = _card_ids(len(SEQ))
    prefs = [{"user_id": "u_replay", "card_id": cid, "prefer": pref, "at": "now"}
             for (pref, _), cid in zip(SEQ, cards)]

    _drop("u_live")
    for rec, (pref, _) in zip(prefs, SEQ):
        main._apply("u_live", rec["card_id"], pref)
    live = policy.learn_state(policy.session_for("u_live"))

    # 서버를 껐다 켠 상황: 기록 파일만 있고 세션은 없다.
    path = tmp_path / "preferences.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in prefs) + "\n",
                    encoding="utf-8")
    monkeypatch.setattr(state, "PREFS", path)
    _drop("u_replay")
    restored = state.restore({}, [], main._apply)
    assert restored["preferences"] == len(prefs)
    replay = policy.learn_state(policy.session_for("u_replay"))

    # user_id 만 다르므로 학습 상태는 완전히 같아야 한다.
    def norm(state_dict: dict) -> str:
        return json.dumps(state_dict, ensure_ascii=False, sort_keys=True, default=str)

    assert norm(live["policies"]["legacy"]) == norm(replay["policies"]["legacy"])
    assert norm(live["policies"]["taste"]) == norm(replay["policies"]["taste"])


def test_재생은_난수를_쓰지_않는다(tmp_path, monkeypatch):
    """같은 기록을 두 번 재생하면 가중치가 **비트 단위로** 같다."""
    import main

    cards = _card_ids(len(SEQ))
    states = []
    for uid in ("u_r1", "u_r2"):
        _drop(uid)
        for (pref, _), cid in zip(SEQ, cards):
            main._apply(uid, cid, pref)
        sess = policy.session_for(uid)
        states.append((sess["taste"]["m"].tobytes(), sess["taste"]["v"].tobytes(),
                       sess["legacy"]["m"].tobytes()))
    assert states[0] == states[1]


def test_카드를_뽑아도_학습_상태는_안_변한다(monkeypatch):
    """추천(난수 있음)은 상태를 바꾸지 않는다 — 바꾸면 재생이 어긋난다."""
    import main

    cards = _card_ids(3)
    _drop("u_pick")
    for cid in cards:
        main._apply("u_pick", cid, "like")
    sess = policy.session_for("u_pick")
    before = (sess["taste"]["m"].copy(), sess["taste"]["v"].copy(),
              sess["legacy"]["m"].copy())
    policy.pick_many(sess, 5)
    assert (sess["taste"]["m"] == before[0]).all()
    assert (sess["taste"]["v"] == before[1]).all()
    assert (sess["legacy"]["m"] == before[2]).all()


# ────────────────────────────────────────────────── 실서버 기록 재생 (Step 3d)
def _live_feedback() -> dict[str, list[tuple[str, bool]]]:
    """`MATCHURI_STATE` 의 선호 기록을 사용자별 (store_id, like) 목록으로 (순서 유지)."""
    from app import loader, state

    loader.load_all()
    out: dict[str, list[tuple[str, bool]]] = {}
    for p in state.read_preferences():
        uid, cid, pref = p.get("user_id"), p.get("card_id"), p.get("prefer")
        if uid is None or cid is None or pref not in ("like", "dislike"):
            continue
        sid = loader.to_store_id(cid)
        if sid is not None:
            out.setdefault(uid, []).append((sid, pref == "like"))
    return out


def test_실서버_기록을_재생해도_가중치가_폭주하지_않는다():
    """온라인 폭주 회귀 검사 — 진짜 기록을 통째로 다시 먹여 max|w| 를 잰다.

    합성 풀에서 되는 것과 사용자 32명 × 후보 2,216곳에서 되는 것은 다른 주장이다.
    실행은 state 사본을 가리키게 해서 한다(**원본을 건드리지 않는다**):

        MATCHURI_STATE=/tmp/state-copy ~/venvs/recsys/bin/python \\
            -m pytest tests/test_replay.py -q -s -k 폭주

    `MATCHURI_STATE` 가 비어 있으면(기본) 건너뛴다.
    """
    feedback = _live_feedback()
    if not feedback:
        pytest.skip("MATCHURI_STATE 에 재생할 선호 기록이 없다")

    rows = []
    for uid, seq in feedback.items():
        m = taste.replay(seq)["m"]
        rows.append((uid, len(seq), float(np.abs(m).max()), m))
    rows.sort(key=lambda r: -r[2])

    print(f"\n재생 {len(rows)}명 · 피드백 {sum(r[1] for r in rows)}건")
    print(f"{'user_id':<18}{'n':>5}{'max|w|':>10}")
    for uid, n, mx, _ in rows:
        print(f"{uid:<18}{n:>5}{mx:>10.2f}")
    for uid, n, mx, m in rows:
        if uid == "u_475a743551e4":       # 폭주를 처음 드러낸 사용자
            print("u_475a743551e4: " + " ".join(
                f"{c}={v:+.3f}" for c, v in zip(content.COMPONENTS, m)))

    uid, n, mx, m = rows[0]
    detail = dict(zip(content.COMPONENTS, m.round(3)))
    assert mx < MAX_ABS_W, f"{uid} (피드백 {n}건) max|w| = {mx:.2f} — {detail}"
