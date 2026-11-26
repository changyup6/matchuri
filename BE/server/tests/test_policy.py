# -*- coding: utf-8 -*-
"""정책 라우터 (`recsys/policy.py`) — 인터리빙·태깅·산출물 없을 때의 후퇴·성적 집계."""
from __future__ import annotations

import numpy as np
import pytest

from recsys import content, policy, swipe, taste


@pytest.fixture
def router(art, monkeypatch):
    """합성 풀 위의 라우터. legacy 세션도 합성 풀을 보게 만든다."""
    monkeypatch.setenv(policy.ENV, "interleave")
    monkeypatch.setattr(policy, "_warned", False)
    policy._sessions.clear()
    swipe._sessions.clear()
    taste._sessions.clear()
    # legacy 정책이 쓰는 것은 pool()["X"]·["qz"]·["cat"] 뿐이다. 특성 차원은
    # swipe 의 실제 배치(절편·품질z·리뷰수 + 업종 11 + 가격 5 = 19)를 그대로 쓴다 —
    # `learn_state` 가 그 자리를 이름으로 읽기 때문이다.
    n = len(art.ids)
    cats = ["한식", "한식", "일식", "일식", "카페", "중식"]
    d = 3 + len(swipe.CATS) + 1 + 5
    X = np.zeros((n, d))
    X[:, 0] = 1.0
    X[:, 1] = art.quality
    X[:, 2] = art.pop / 6.0
    for i, c in enumerate(cats):
        X[i, 3 + swipe.CATS.index(c)] = 1.0
        X[i, 3 + len(swipe.CATS) + 1 + (i % 4)] = 1.0
    monkeypatch.setattr(swipe, "pool", lambda: {
        "ids": list(art.ids), "pos": dict(art.pos),
        "cat": dict(zip(art.ids, cats)),
        "qz": np.array(art.quality), "X": X})
    monkeypatch.setattr(swipe, "_new_session", lambda: {
        "m": np.zeros(d), "v": np.full(d, 1.0), "shown": set(), "last_cats": [],
        "n": 0, "likes": 0, "pending": None, "rng": np.random.default_rng(1),
        "cat_stats": {}, "hist": [], "created": 0.0})
    return policy


def test_기본은_인터리빙(router, monkeypatch):
    monkeypatch.delenv(policy.ENV, raising=False)
    assert policy.configured() == "interleave"
    assert policy.active() == "interleave"


def test_모르는_값은_기본값으로(router, monkeypatch):
    monkeypatch.setenv(policy.ENV, "이상한값")
    assert policy.configured() == policy.DEFAULT_POLICY


def test_산출물이_없으면_legacy_로_후퇴한다(router, monkeypatch):
    monkeypatch.setattr(content, "load", lambda *a, **k: None)
    monkeypatch.setattr(policy, "_warned", False)
    assert policy.active() == "legacy"
    sess = policy.session_for("u1")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 3, tags=tags)
    assert got and set(tags.values()) == {"legacy"}
    policy.record(sess, got[0], True)                # 죽지 않는다
    assert sess["legacy"]["n"] == 1
    assert policy.learn_state(sess)["policies"]["taste"]["available"] is False


def test_인터리빙은_번갈아_뽑고_중복이_없다(router):
    sess = policy.session_for("u2")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 4, tags=tags)
    assert len(got) == len(set(got)) == 4            # 중복 없음
    seq = [tags[s] for s in got]
    assert seq[0] != seq[1] and seq[1] != seq[2] and seq[2] != seq[3]
    assert set(seq) == {"legacy", "taste"}


def test_드래프트_순서는_사용자_배치별로_고정이다(router):
    a = [policy._first_drafter("u2", b) for b in range(20)]
    b = [policy._first_drafter("u2", b) for b in range(20)]
    assert a == b                                    # 재현 가능
    assert set(a) == {"legacy", "taste"}             # 한쪽으로 고정되지 않는다
    assert a != [policy._first_drafter("u3", b) for b in range(20)]


def test_두_정책_모두_피드백을_받는다(router):
    sess = policy.session_for("u4")
    got = policy.pick_many(sess, 2)
    policy.record(sess, got[0], True)
    assert sess["legacy"]["n"] == 1 and sess["taste"]["n"] == 1
    assert sess["taste"]["like_ids"] == [got[0]]
    assert got[0] in sess["legacy"]["shown"] and got[0] in sess["taste"]["shown"]


def test_상대_정책도_노출을_안다(router):
    """한 정책이 뽑은 카드를 상대도 '보여줬다' 고 알아야 같은 카드가 두 번 안 나온다."""
    sess = policy.session_for("u5")
    got = policy.pick_many(sess, 2)
    for sid in got:
        assert sid in sess["legacy"]["shown"] and sid in sess["taste"]["shown"]


def test_단일_정책_모드(router, monkeypatch):
    monkeypatch.setenv(policy.ENV, "taste")
    sess = policy.session_for("u6")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 3, tags=tags)
    assert set(tags.values()) == {"taste"} and len(got) == 3


def test_learn_state_는_legacy_계약을_보존한다(router):
    sess = policy.session_for("u7")
    st = policy.learn_state(sess)
    for key in ("quality_weight", "top", "cats", "price", "base", "hist"):
        assert key in st                             # 기존 필드는 하나도 안 없앤다
    assert set(st["policies"]) == {"legacy", "taste"}
    assert st["policies"]["taste"]["policy"] == "taste"


# ────────────────────────────────────────────────── 성적 집계
def test_wilson_구간():
    lo, hi = policy.wilson(5, 10)
    assert 0 < lo < 0.5 < hi < 1
    assert policy.wilson(0, 0) is None
    lo2, hi2 = policy.wilson(50, 100)
    assert (hi2 - lo2) < (hi - lo)                   # 표본이 크면 좁아진다


def test_노출과_선호를_조인한다(router, monkeypatch, tmp_path):
    """`exposures.jsonl` × `preferences.jsonl` — 카드 단위로 정책별 성적을 낸다."""
    from app import state as app_state
    monkeypatch.setattr(app_state, "DIR", tmp_path)
    monkeypatch.setattr(app_state, "EXPOSURES", tmp_path / "exposures.jsonl")
    monkeypatch.setattr(app_state, "PREFS", tmp_path / "preferences.jsonl")
    for rank, (cid, name) in enumerate([(1, "legacy"), (2, "taste"), (3, "legacy"),
                                        (4, "taste"), (5, "taste")]):
        app_state.record_exposure("u9", cid, name, 0, rank)
    app_state.record_exposure("u9", 1, "legacy", 1, 0)      # 같은 카드 재노출
    app_state.record_exposure("u9", 6, "trend", 1, 1)       # 트렌드 슬롯 카드
    app_state.record_preference("u9", 1, "like")
    app_state.record_preference("u9", 2, "dislike")
    app_state.record_preference("u9", 4, "like")
    app_state.record_preference("u9", 6, "like")

    out = policy.outcome_stats()
    rows = {r["policy"]: r for r in out["by_policy"]}
    assert out["exposures"] == 7
    assert rows["legacy"]["shown"] == 2               # 재노출은 한 번만 센다
    assert rows["legacy"]["liked"] == 1 and rows["legacy"]["unrated"] == 1
    assert rows["taste"]["shown"] == 3
    assert rows["taste"]["liked"] == 1 and rows["taste"]["disliked"] == 1
    assert rows["taste"]["like_rate"] == 0.5
    assert out["verdict"].startswith("아직 모릅니다")   # legacy 대 taste 판정은 그대로
    # 슬롯은 셋째 행이고, 판정 상대는 legacy+taste 합산이다
    assert [r["policy"] for r in out["by_policy"]] == ["legacy", "taste", "trend"]
    assert rows["trend"]["shown"] == 1 and rows["trend"]["liked"] == 1
    slot = out["slot"]
    assert slot["interleaved"]["shown"] == 5 and slot["interleaved"]["rated"] == 3
    assert slot["trend"] == rows["trend"]
    assert slot["verdict"].startswith("아직 모릅니다")


# ────────────────────────────────────────────────── 트렌드 슬롯
from recsys import trend  # noqa: E402


@pytest.fixture
def slot(router, monkeypatch):
    """5장에 1장. 트렌드 z 는 합성이다 — s0·s1 만 오르고 나머지는 내린다."""
    monkeypatch.setenv(trend.SLOT_ENV, "5")
    monkeypatch.setattr(trend, "scores", lambda ids: {
        sid: {"s0": 2.0, "s1": 1.0}.get(sid, -1.0) for sid in ids})
    return policy


def test_슬롯이_꺼져_있으면_아무것도_안_바뀐다(router, monkeypatch):
    """기본은 꺼짐. 인터리빙 실험이 도는 동안 라벨 집합은 legacy·taste 그대로다."""
    monkeypatch.delenv(trend.SLOT_ENV, raising=False)
    assert trend.slot_every() == 0
    assert policy._slot_ranks("u", 0, 0, 10) == []
    sess = policy.session_for("u10")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 4, tags=tags)
    assert len(got) == 4 and set(tags.values()) <= {"legacy", "taste"}
    assert sess["served"] == 4                       # 누적 카운터는 늘 센다


def test_슬롯은_N장에_한_장이고_오르는_가게만_뽑는다(slot):
    sess = policy.session_for("u11")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 5, tags=tags)
    assert len(got) == len(set(got)) == 5            # 중복 없음
    trend_cards = [s for s in got if tags[s] == "trend"]
    assert len(trend_cards) == 1 and trend_cards[0] in ("s0", "s1")
    assert set(tags.values()) == {"legacy", "taste", "trend"}
    sid = trend_cards[0]
    assert sid in sess["legacy"]["shown"] and sid in sess["taste"]["shown"]
    assert policy.exposed_by("u11", sid) == "trend"  # 지표 라벨도 trend


def test_슬롯은_배치_크기와_무관하게_누적으로_센다(router, monkeypatch):
    """limit=3 으로 두 번 부르면 여섯 장 중 **다섯째**(누적 4) 한 장만 슬롯이다."""
    monkeypatch.setenv(trend.SLOT_ENV, "5")
    monkeypatch.setattr(trend, "scores",
                        lambda ids: {sid: 1.0 + i for i, sid in enumerate(ids)})
    sess = policy.session_for("u12")
    tags: dict[str, str] = {}
    a = policy.pick_many(sess, 3, tags=tags)          # 누적 0~2 → 슬롯 없음
    assert a and all(tags[s] != "trend" for s in a)
    b = policy.pick_many(sess, 3, tags=tags)          # 누적 3~5 → 4 가 슬롯
    assert sum(tags[s] == "trend" for s in b) == 1
    assert sess["served"] == 6


def test_슬롯_자리는_고정_난수이고_마지막_칸_붙박이가_아니다(router, monkeypatch):
    monkeypatch.setenv(trend.SLOT_ENV, "10")
    ranks = [policy._slot_ranks("u13", b, b * 10, 10) for b in range(30)]
    assert ranks == [policy._slot_ranks("u13", b, b * 10, 10) for b in range(30)]
    assert all(len(r) == 1 for r in ranks)           # 10장에 정확히 1장
    assert len({r[0] for r in ranks}) > 3            # 자리가 흩어진다(위치 편향 상쇄)
    assert ranks != [policy._slot_ranks("u14", b, b * 10, 10) for b in range(30)]


def test_오르는_가게가_없으면_슬롯을_비우고_두_정책이_채운다(router, monkeypatch):
    monkeypatch.setenv(trend.SLOT_ENV, "5")
    monkeypatch.setattr(trend, "scores", lambda ids: {})      # 자료가 낡았거나 없다
    sess = policy.session_for("u15")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 5, tags=tags)
    assert len(got) == 5 and "trend" not in tags.values()


def test_슬롯_카드도_두_정책이_배운다(slot):
    sess = policy.session_for("u16")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 5, tags=tags)
    sid = next(s for s in got if tags[s] == "trend")
    policy.record(sess, sid, True)
    assert sess["legacy"]["n"] == 1 and sess["taste"]["n"] == 1
    assert sess["taste"]["like_ids"] == [sid]


def test_슬롯도_허용_집합_안에서만_뽑는다(slot):
    sess = policy.session_for("u17")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 5, tags=tags, allow={"s1", "s2", "s3", "s4", "s5"})
    assert [s for s in got if tags[s] == "trend"] == ["s1"]   # s0 은 필터 밖


def test_슬롯은_풀_밖_가게를_안_뽑는다(slot, monkeypatch):
    """허용 집합은 전체 가게에서 만들어 풀 밖 가게도 든다. 슬롯도 풀 안에서만 뽑아야 한다
    — 집합을 그대로 후보로 썼을 때 카드 자격이 없는 가게가 트렌드 카드로 나갔다(9/22)."""
    monkeypatch.setattr(trend, "scores", lambda ids: {
        sid: {"밖": 3.0}.get(sid, -1.0) for sid in ids})   # 오르는 곳은 풀 밖 한 곳뿐
    sess = policy.session_for("u18")
    tags: dict[str, str] = {}
    got = policy.pick_many(sess, 5, tags=tags, allow={"밖", "s1", "s2", "s3"})
    assert "밖" not in got and "trend" not in tags.values()   # 비우고 두 정책이 채운다
    assert set(got) == {"s1", "s2", "s3"}                     # 필터는 그대로 걸린다

    monkeypatch.setattr(trend, "scores", lambda ids: {
        sid: {"밖": 3.0, "s2": 1.0}.get(sid, -1.0) for sid in ids})
    sess = policy.session_for("u19")
    tags = {}
    got = policy.pick_many(sess, 5, tags=tags, allow={"밖", "s1", "s2", "s3"})
    assert [s for s in got if tags[s] == "trend"] == ["s2"]   # 풀 안의 오르는 곳은 그대로 뽑힌다


def test_슬롯_자리_배치():
    main = [("a", "legacy"), ("b", "taste"), ("c", "legacy")]
    assert policy._place_slots(main, ["t"], [0]) == [("t", "trend")] + main
    assert policy._place_slots(main, ["t"], [2]) == main[:2] + [("t", "trend")] + main[2:]
    # 필터로 화면이 짧아져 자리가 끝을 넘으면 마지막으로 민다 — 버리지 않는다
    assert policy._place_slots(main, ["t"], [9]) == main + [("t", "trend")]
    assert policy._place_slots([], ["t"], [3]) == [("t", "trend")]
    assert policy._place_slots(main, [], [1]) == main
