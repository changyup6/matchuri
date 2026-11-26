# -*- coding: utf-8 -*-
"""정렬 보정 — 겹치는 태그 수(계층)와 거리 보너스.

필터가 '누가 후보인가' 를 정하고 여기서 '누가 먼저인가' 가 정해진다. 잠글 것은 셋이다.

  ① **계층이 점수를 이긴다.** 고른 태그를 더 많이 가진 가게가 점수가 낮아도 앞에 온다.
  ② **거리는 점수에 섞인다.** 같은 계층 안에서 가까운 곳이 앞에 오고, 좌표를 모르는 곳은
     후보 평균(0)이라 중간에 앉는다. 위치가 없으면 보정 자체가 없다.
  ③ **학습과 화면은 건드리지 않는다.** `record`·재생·`peek`·관리자 화면·취향 지형도는
     보정을 모른다 — 위치는 요청마다 바뀌는데 학습 상태는 `preferences.jsonl` 하나로
     복원돼야 하고(`tests/test_replay.py`), 관리자 화면이 묻는 것은 '모델이 무엇을 믿는가' 다.
"""
from __future__ import annotations

import copy

import numpy as np
import pytest

from app import filters, geo, ranking
from recsys import policy, swipe, taste

BASE = (37.5, 127.0)
DEG_LAT_M = 111_195.0
#: s0 이 기준점, s1~s4 는 200m 씩 멀어진다. s5 는 **좌표가 없다**(모르는 가게).
COORDS = {f"s{i}": (BASE[0] + i * 200 / DEG_LAT_M, BASE[1]) for i in range(5)}
CAT2 = {"s0": "한식", "s1": "한식", "s2": "일식", "s3": "일식", "s4": "카페", "s5": "중식"}
#: 태그는 계층을 잴 수 있게 겹침을 달리 둔다(고르는 태그는 혼밥·가성비·조용한).
TAGS = {"s0": ["혼밥"], "s1": ["혼밥", "가성비"], "s2": ["혼밥", "가성비", "조용한"],
        "s3": [], "s4": ["가성비"], "s5": ["조용한"]}
REVIEW_TAGS = {"s0": {}, "s1": {}, "s2": {}, "s3": {"혼밥": 2}, "s4": {}, "s5": {}}


@pytest.fixture
def pool6(art, monkeypatch):
    """합성 풀 위의 정책 라우터 + 합성 가게 테이블(태그·좌표). `test_filter.py` 와 같은 틀."""
    monkeypatch.setenv(policy.ENV, "interleave")
    monkeypatch.setattr(policy, "_warned", False)
    policy._sessions.clear()
    swipe._sessions.clear()
    taste._sessions.clear()
    n = len(art.ids)
    cats = [CAT2[s] for s in art.ids]
    d = 3 + len(swipe.CATS) + 1 + 5
    X = np.zeros((n, d))
    X[:, 0] = 1.0
    X[:, 1] = art.quality
    for i, c in enumerate(cats):
        X[i, 3 + swipe.CATS.index(c)] = 1.0
    # 점수는 s0 > s1 > … > s4 순으로 **또박또박** 내려간다(계층이 이걸 뒤집는지 보려고).
    qz = np.array([3.0, 2.0, 1.0, 0.0, -1.0, -2.0])
    monkeypatch.setattr(swipe, "pool", lambda: {
        "ids": list(art.ids), "pos": dict(art.pos), "cat": dict(zip(art.ids, cats)),
        "qz": qz, "X": X})
    monkeypatch.setattr(swipe, "_new_session", lambda: {
        "m": np.zeros(d), "v": np.full(d, 1.0), "shown": set(), "last_cats": [],
        "n": 0, "likes": 0, "pending": None, "rng": np.random.default_rng(1),
        "cat_stats": {}, "hist": [], "created": 0.0})
    monkeypatch.setattr(filters.svc, "all_store_ids", lambda: list(art.ids))
    monkeypatch.setattr(filters.svc, "get_store", lambda sid: None if sid not in CAT2 else {
        "store_id": sid, "name": sid, "cat2": CAT2[sid], "tags": list(TAGS[sid]),
        "review_tags": dict(REVIEW_TAGS[sid]),
        "kakao_review_count": 10, "kakao_avg_score": 4.5})
    monkeypatch.setattr(geo, "coords", lambda: dict(COORDS))
    return policy


# ────────────────────────────────────────────────── ① 계층
def test_계층은_고른_태그_중_몇_개를_가졌나다(pool6):
    got = ranking.tiers([], ["혼밥", "가성비", "조용한"])
    assert got == {"s0": 1, "s1": 2, "s2": 3, "s3": 1, "s4": 1, "s5": 1}
    assert "s3" in got                        # 리뷰 태그(혼밥)로만 걸린 가게도 센다

    two = ranking.tiers([], ["혼밥", "조용한"])
    assert two == {"s0": 1, "s1": 1, "s2": 2, "s3": 1, "s5": 1}   # 0 은 담지 않는다


def test_태그를_하나만_골랐으면_계층이_없다(pool6):
    """OR 필터라 후보는 전부 그 태그를 가졌다 — 계층이 모두 1 이면 순서에 할 말이 없다."""
    assert ranking.tiers([], ["혼밥"]) == {}
    assert ranking.tiers([], []) == {}
    assert ranking.tiers(["한식", "일식"], []) == {}       # 업종은 계층을 만들지 않는다
    assert ranking.tiers(["한식"], ["혼밥"]) == {}


@pytest.mark.parametrize("mode", ["legacy", "taste", "interleave"])
def test_계층이_점수를_이긴다(pool6, monkeypatch, mode):
    """점수는 s0 > s1 > s2 인데, 고른 태그를 더 많이 가진 s2 가 먼저 와야 한다."""
    monkeypatch.setenv(policy.ENV, mode)
    tiers = ranking.tiers([], ["혼밥", "가성비", "조용한"])
    sess = policy.session_for(f"u_tier_{mode}")
    got = policy.pick_many(sess, 3, allow={"s0", "s1", "s2"}, tiers=tiers)
    assert got[0] == "s2"                     # 3개 일치
    assert got[1] == "s1"                     # 2개 일치
    assert got[2] == "s0"                     # 1개 일치 — 점수 1위인데 마지막이다


def test_계층이_없으면_점수_순_그대로다(pool6):
    sess = policy.session_for("u_plain")
    got = policy.pick_many(sess, 3, allow={"s0", "s1", "s2"})
    assert got == ["s0", "s1", "s2"]


# ────────────────────────────────────────────────── ② 거리
def test_거리_보너스는_후보_안의_z_점수다(pool6):
    ids = ["s0", "s1", "s2", "s3", "s4", "s5"]
    bonus = ranking.distance_bonus(*BASE, ids)
    assert set(bonus) == set(ids)
    assert bonus["s0"] > bonus["s1"] > bonus["s2"] > bonus["s3"] > bonus["s4"]
    assert bonus["s0"] > 0 > bonus["s4"]      # 가까우면 +, 멀면 −
    assert bonus["s5"] == 0.0                 # 좌표를 모르면 후보 평균(0)
    assert bonus["s2"] == pytest.approx(0.0, abs=1e-9)   # 중앙(400m) = 평균
    # 무게는 z 에 곱해진다 — 0 이면 보정이 없는 것과 같다
    assert bonus["s0"] == pytest.approx(ranking.weight() * 1.4142, abs=1e-3)


def test_무게는_환경변수로_바꾼다(pool6, monkeypatch):
    ids = ["s0", "s1", "s2", "s3", "s4"]
    base = ranking.distance_bonus(*BASE, ids)["s0"]
    monkeypatch.setenv(ranking.DIST_WEIGHT_ENV, "1.6")
    assert ranking.weight() == 1.6
    assert ranking.distance_bonus(*BASE, ids)["s0"] == pytest.approx(base * 4, rel=1e-6)
    monkeypatch.setenv(ranking.DIST_WEIGHT_ENV, "이상한값")
    assert ranking.weight() == ranking.DEFAULT_DIST_WEIGHT      # 못 읽으면 기본값
    monkeypatch.setenv(ranking.DIST_WEIGHT_ENV, "0")
    assert all(v == 0.0 for v in ranking.distance_bonus(*BASE, ids).values())


def test_z_는_잘린다(pool6, monkeypatch):
    """이상점 하나가 정렬 전체를 정하지 못하게 — `content.Z_CLIP` 과 같은 이유다.

    후보가 적으면 z 자체가 √(n−1) 을 넘지 못하므로(4곳이면 최대 1.73) 자르기를 재려면
    '거의 다 붙어 있고 하나만 멀다' 는 실제 모양이 필요하다 — 20곳 + 1곳으로 만든다.
    """
    coords = {f"n{i}": (BASE[0] + i * 1e-6, BASE[1]) for i in range(20)}
    coords["far"] = (BASE[0] + 1.0, BASE[1])              # 약 111km
    monkeypatch.setattr(geo, "coords", lambda: coords)
    monkeypatch.setenv(ranking.DIST_WEIGHT_ENV, "1")
    bonus = ranking.distance_bonus(*BASE, list(coords))
    assert bonus["far"] == pytest.approx(-ranking.Z_CLIP)      # 아래쪽이 잘렸다
    assert all(abs(v) <= ranking.Z_CLIP + 1e-9 for v in bonus.values())


def test_위치가_없으면_보정이_없다(pool6):
    assert ranking.distance_bonus(None, None, ["s0"]) == {}
    assert ranking.distance_bonus(*BASE, []) == {}
    tiers, bonus = ranking.build({"cat": [], "tag": ["혼밥"], "within_m": None},
                                 None, {"s0", "s1"})
    assert tiers == {} and bonus == {}


def test_build_는_후보_안에서만_잰다(pool6):
    """500m 안만 보는 사람에게도 그 안에서의 가까움이 순서를 만들어야 한다."""
    whole = ranking.build(None, BASE, None)[1]
    narrow = ranking.build(None, BASE, {"s3", "s4"})[1]
    assert set(narrow) == {"s3", "s4"} and narrow["s3"] > 0 > narrow["s4"]
    assert whole["s3"] < 0                    # 전체 후보에서는 s3 이 평균보다 멀다


#: 거리만 남기고 재는 후보 셋 — 업종·브랜드가 서로 달라서 다양성 규칙이 끼어들지 않는다
#: (같은 브랜드 연속 금지·같은 업종 3연속 금지가 순서를 흔들면 무엇을 잰 건지 알 수 없다).
FLAT = {"s0": 800.0, "s2": 400.0, "s4": 0.0}


@pytest.fixture
def flat_scores(pool6, monkeypatch):
    """점수를 평평하게, 거리는 **인덱스 순서와 반대로** 둔다 — 남는 것은 거리뿐이다."""
    monkeypatch.setattr(swipe, "pool", lambda p=swipe.pool(): {**p, "qz": np.zeros(6)})
    monkeypatch.setattr(taste, "_cold", lambda z, m, has: np.zeros(z.shape[1]))
    monkeypatch.setattr(geo, "coords", lambda: {
        sid: (BASE[0] + m / DEG_LAT_M, BASE[1]) for sid, m in FLAT.items()})
    monkeypatch.setenv(ranking.DIST_WEIGHT_ENV, "1")
    return policy


@pytest.mark.parametrize("mode", ["legacy", "taste", "interleave"])
def test_거리가_같은_계층_안의_순서를_바꾼다(flat_scores, monkeypatch, mode):
    monkeypatch.setenv(policy.ENV, mode)
    allow = set(FLAT)
    sess = policy.session_for(f"u_dist_{mode}")
    plain = policy.pick_many(sess, 3, allow=allow)
    assert plain == ["s0", "s2", "s4"]                    # 보정이 없으면 인덱스(점수) 순
    sess2 = policy.session_for(f"u_dist2_{mode}")
    bonus = ranking.distance_bonus(*BASE, sorted(allow))
    got = policy.pick_many(sess2, 3, allow=allow, bonus=bonus)
    assert got == ["s4", "s2", "s0"]                      # 가까운 순으로 뒤집힌다


def test_좌표를_모르는_가게는_중간에_앉는다(flat_scores, monkeypatch):
    """'모른다' 를 벌하지도 봐주지도 않는다 — 후보 평균(0)이라 가운데에 앉는다."""
    allow = set(FLAT) | {"s5"}                            # s5 는 좌표가 없다
    bonus = ranking.distance_bonus(*BASE, sorted(allow))
    assert bonus["s5"] == 0.0
    got = policy.pick_many(policy.session_for("u_unknown"), 4, allow=allow,
                           bonus=bonus)
    assert got.index("s4") < got.index("s5") < got.index("s0")


def test_계층과_거리를_함께_주면_계층이_1순위다(pool6, monkeypatch):
    monkeypatch.setenv(ranking.DIST_WEIGHT_ENV, "3")      # 거리를 아주 세게 줘도
    tiers = ranking.tiers([], ["혼밥", "가성비", "조용한"])
    bonus = ranking.distance_bonus(*BASE, ["s0", "s1", "s2"])
    sess = policy.session_for("u_both")
    got = policy.pick_many(sess, 3, allow={"s0", "s1", "s2"}, tiers=tiers, bonus=bonus)
    assert got == ["s2", "s1", "s0"]          # 계층 3 > 2 > 1 (s0 이 가장 가깝지만 마지막)


# ────────────────────────────────────────────────── ③ 건드리지 않는 것
def _snapshot(sess: dict) -> dict:
    legacy, tst = sess["legacy"], sess["taste"]
    return {"legacy_m": legacy["m"].tolist(), "legacy_v": legacy["v"].tolist(),
            "legacy_hist": copy.deepcopy(legacy["hist"]), "legacy_n": legacy["n"],
            "taste_m": tst["m"].tolist(), "taste_v": tst["v"].tolist(),
            "taste_hist": copy.deepcopy(tst["hist"]), "taste_n": tst["n"],
            "like_ids": list(tst["like_ids"]), "dislike_ids": list(tst["dislike_ids"])}


def test_보정은_학습에_들어가지_않는다(pool6):
    """거리는 **선택 시점**의 보정이다 — 같은 피드백이면 같은 `m`·`v` 가 나와야 한다."""
    tiers = ranking.tiers([], ["혼밥", "가성비", "조용한"])
    bonus = ranking.distance_bonus(*BASE, ["s0", "s1", "s2", "s3", "s4"])

    plain = policy.session_for("u_learn_plain")
    policy.pick_many(plain, 2)
    policy.record(plain, "s2", True)
    policy.record(plain, "s0", False)
    after_plain = _snapshot(plain)

    boosted = policy.session_for("u_learn_boost")
    policy.pick_many(boosted, 2, tiers=tiers, bonus=bonus)
    policy.record(boosted, "s2", True)
    policy.record(boosted, "s0", False)
    after_boost = _snapshot(boosted)

    for key in ("legacy_m", "legacy_v", "taste_m", "taste_v", "like_ids",
                "dislike_ids", "legacy_n", "taste_n"):
        assert after_boost[key] == after_plain[key], key


def test_재생은_그대로다(pool6):
    """`preferences.jsonl` 만으로 복원되는 성질 — 보정은 복원 경로에 없다."""
    live = policy.session_for("u_replay")
    policy.pick_many(live, 3, bonus=ranking.distance_bonus(*BASE, ["s0", "s1", "s2"]))
    for sid, like in (("s0", True), ("s1", False), ("s2", True)):
        policy.record(live, sid, like)
    replayed = taste.replay([("s0", True), ("s1", False), ("s2", True)])
    assert np.allclose(replayed["m"], live["taste"]["m"])
    assert np.allclose(replayed["v"], live["taste"]["v"])


def test_peek_은_보정을_모른다(pool6):
    """관리자 화면이 묻는 것은 '모델이 무엇을 믿는가' 다 — 지금 위치가 섞이면 답이 흐려진다."""
    sess = policy.session_for("u_peek")
    before = policy.peek(sess, 4)
    policy.pick_many(policy.session_for("u_other"), 3,
                     tiers=ranking.tiers([], ["혼밥", "가성비", "조용한"]),
                     bonus=ranking.distance_bonus(*BASE, ["s0", "s1", "s2"]))
    assert policy.peek(sess, 4) == before
    state = taste.score_state(sess["taste"])
    assert state is not None and "총점" not in state    # 점수는 성분 가중치만으로 낸다
    assert np.allclose(state["total"], taste.score_state(sess["taste"])["total"])


# ────────────────────────────────────────────────── HTTP (실데이터)
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


YEOKSAM = (37.5006, 127.0366)


def _matched(card_id: int, want: set[str]) -> int:
    from app import loader
    from recsys import store as svc
    return len(want & filters.client_tags(svc.get_store(loader.to_store_id(card_id))))


def test_실데이터_계층_정렬(client):
    """태그 둘을 고르면 둘 다 맞는 카드가 먼저 나온다(겹치는 수 내림차순)."""
    uid = client.post("/users").json()["user_id"]
    client.put(f"/users/{uid}/location", json={"lat": YEOKSAM[0], "lon": YEOKSAM[1]})
    client.put(f"/users/{uid}/filter", json={"tag": ["단체모임", "분위기"]})
    cards = client.get(f"/cards?user_id={uid}&limit=10").json()
    counts = [_matched(c["card_id"], {"단체모임", "분위기"}) for c in cards]
    assert counts == sorted(counts, reverse=True)      # 계층이 내려가는 순서
    assert counts[0] == 2 and all(n >= 1 for n in counts)


def test_실데이터_거리_보정이_평균_거리를_줄인다(client, monkeypatch):
    """무게 0 과 기본값을 비교한다 — 거리가 점수에 섞였다는 증거."""
    def distances(weight: str) -> list[int]:
        monkeypatch.setenv(ranking.DIST_WEIGHT_ENV, weight)
        uid = client.post("/users").json()["user_id"]
        client.put(f"/users/{uid}/location",
                   json={"lat": YEOKSAM[0], "lon": YEOKSAM[1]})
        client.put(f"/users/{uid}/filter", json={"tag": ["혼밥"]})
        return [c["distance_m"] for c in
                client.get(f"/cards?user_id={uid}&limit=20").json()]

    off = distances("0")
    on = distances("1.2")                     # 세게 주면 효과가 분명해야 한다
    assert len(off) == len(on) == 20
    assert sum(on) / 20 < sum(off) / 20


def test_가까운_카드의_이유는_거리다(client, monkeypatch):
    """계층이 없는 요청에서, 500m 안쪽 카드는 이유가 거리 문장으로 바뀐다."""
    monkeypatch.setenv(ranking.DIST_WEIGHT_ENV, "1.5")
    uid = client.post("/users").json()["user_id"]
    client.put(f"/users/{uid}/location", json={"lat": YEOKSAM[0], "lon": YEOKSAM[1]})
    cards = client.get(f"/cards?user_id={uid}&limit=10").json()
    near = [c for c in cards if c["distance_m"] is not None
            and c["distance_m"] <= ranking.NEAR_M]
    assert near, "500m 안쪽 카드가 하나도 없다(역삼역 기준이면 있어야 한다)"
    for c in near:
        assert f"{c['distance_m']}m 거리예요" in c["recommendation_reason"]
    for c in cards:
        if c["distance_m"] is not None and c["distance_m"] > ranking.NEAR_M:
            assert "거리예요" not in c["recommendation_reason"]


def test_계층이_있으면_이유는_정책의_것_그대로다(client):
    uid = client.post("/users").json()["user_id"]
    client.put(f"/users/{uid}/location", json={"lat": YEOKSAM[0], "lon": YEOKSAM[1]})
    client.put(f"/users/{uid}/filter", json={"tag": ["단체모임", "분위기"]})
    cards = client.get(f"/cards?user_id={uid}&limit=5").json()
    assert all("거리예요" not in c["recommendation_reason"] for c in cards)


def test_위치가_없으면_이유도_거리가_아니다(client):
    cards = client.get("/cards?limit=5").json()
    assert all(c["distance_m"] is None for c in cards)
    assert all("거리예요" not in c["recommendation_reason"] for c in cards)


def test_정렬_근거가_로그와_지표에_남는다(client):
    from tests.test_observe import capture, only

    def ranked(text: str, by: str) -> int:
        for line in text.splitlines():
            if line.startswith(f'matchuri_cards_ranked_total{{by="{by}"}}'):
                return int(line.rsplit(" ", 1)[1])
        return 0

    uid = client.post("/users").json()["user_id"]
    client.put(f"/users/{uid}/location", json={"lat": YEOKSAM[0], "lon": YEOKSAM[1]})
    before = client.get("/metrics").text
    with capture() as lines:
        client.get(f"/cards?user_id={uid}&limit=3")            # 위치만 → distance
        client.put(f"/users/{uid}/filter", json={"tag": ["단체모임", "분위기"]})
        client.get(f"/cards?user_id={uid}&limit=3")            # 태그 둘 → tier
        client.get("/cards?limit=3")                           # 아무것도 → none
    after = client.get("/metrics").text

    serves = only(lines, "cards.serve")
    assert [s["tiered"] for s in serves] == [False, True, False]
    assert serves[0]["dist_weight"] == ranking.weight()
    assert serves[2]["dist_weight"] is None
    for by in ("tier", "distance", "none"):
        assert ranked(after, by) == ranked(before, by) + 1
