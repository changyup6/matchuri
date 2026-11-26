# -*- coding: utf-8 -*-
"""취향 정책 (`recsys/taste.py`) — 쌍대 갱신·콜드스타트·다양성·추천 이유."""
from __future__ import annotations

import numpy as np
import pytest

from recsys import content, taste

I = {c: i for i, c in enumerate(content.COMPONENTS)}


# ────────────────────────────────────────────────── 학습
def test_첫_피드백은_점별_갱신이다(art, sess):
    before = sess["m"].copy()
    taste.record(sess, "s0", True)
    assert sess["n"] == 1 and sess["likes"] == 1
    assert sess["hist"][-1]["pairs"] == 0            # 짝지을 반대 라벨이 없었다
    assert not np.allclose(before, sess["m"])


def test_쌍대_갱신은_반대_라벨과_짝짓는다(art, sess):
    taste.record(sess, "s5", False)                  # dislike 먼저
    taste.record(sess, "s0", True)
    assert sess["hist"][-1]["pairs"] == 1


def test_쌍은_최근_5건까지만(art, sess):
    for sid in ("s0", "s1", "s2", "s3", "s4", "s5"):
        taste.record(sess, sid, False)
    taste.record(sess, "s0", True)                   # 이미 shown 이어도 학습은 된다
    assert sess["hist"][-1]["pairs"] == taste.MAX_PAIRS


def test_가중치는_고른_쪽이_강한_성분으로_움직인다(art, sess):
    """s2 를 dislike 하고 s3 을 like 하면, 그 둘을 가른 성분의 가중치가 올라야 한다.

    쌍대 로지스틱은 '왜 B 를 골랐나' 를 배운다. z(s3) − z(s2) 가 양수인 성분은
    올라가고 음수인 성분은 내려간다 — 그 방향을 직접 잰다. z 는 **그 쌍의 두 가게를
    이력에서 뺀 채**(leave-one-out) 낸 것이다 — s0 만 like 로 남고 dislike 는 빈다.
    """
    taste.record(sess, "s0", True)                   # 이력이 있어야 콘텐츠 성분이 산다
    taste.record(sess, "s2", False)
    before = sess["m"].copy()
    z = art.component_scores(np.array([0]), np.array([], dtype=int), np.array([0, 2]))
    taste.record(sess, "s3", True)
    d = z[:, 3] - z[:, 2]
    moved = sess["m"] - before
    assert np.abs(d).max() > 1e-9                    # 검사가 공회전하지 않는다
    for j in range(content.N_COMPONENTS):
        if abs(d[j]) > 1e-9:
            assert np.sign(moved[j]) == np.sign(d[j]), content.COMPONENTS[j]


def test_leave_one_out_은_양쪽_집합에서_뺀다(art, sess):
    """쌍의 두 가게는 like 에서도 dislike 에서도 빠진다. 정규화 마스크는 그대로다."""
    sess["like_ids"] = ["s0", "s1"]
    sess["dislike_ids"] = ["s2"]
    like, dis, rated = taste._loo_idx(sess, art, {"s1", "s2"})
    assert list(like) == [0] and list(dis) == []
    assert sorted(int(i) for i in rated) == [0, 1, 2]


def test_같은_가게를_되평가하면_마지막_라벨만_남는다(art, sess):
    """`like A → dislike A` 뒤 A 는 dislike 에만 있어야 한다.

    양쪽에 동시에 있으면 그 가게가 자기 자신의 근거가 되고(cooc(x,x)=1, sim(x,x)=1)
    다음 쌍의 Δz 가 그 대각선에 지배된다 — 실서버 폭주의 첫 단추였다.
    """
    taste.record(sess, "s0", True)
    taste.record(sess, "s0", False)
    assert sess["like_ids"] == [] and sess["dislike_ids"] == ["s0"]
    taste.record(sess, "s0", True)
    assert sess["like_ids"] == ["s0"] and sess["dislike_ids"] == []


def test_되평가_순서가_가중치를_폭주시키지_않는다(art, sess):
    """실서버에서 터진 순서 그대로 — `like A → dislike A → like B` (PLAN 7절 Step 3d).

    고치기 전에는 세 번째 걸음에서 z_cf(A) = +256(자기 자신과의 공방문)이 Δz 를 먹어
    w_cf 가 0.46 → **−63.63** 으로 밀렸다. 지금은 어느 성분도 사전값에서 ±1.5 밖으로
    나가지 못하고, A 는 dislike 집합에만 남는다.
    """
    prior = sess["m"].copy()
    for sid, like in (("s0", True), ("s0", False), ("s2", True)):
        taste.record(sess, sid, like)
    assert (np.abs(sess["m"] - prior) <= 1.5).all(), sess["m"]
    assert sess["like_ids"] == ["s2"] and sess["dislike_ids"] == ["s0"]


def test_되평가_뒤에도_피드백이_모델을_움직인다(art, sess):
    """포화 검사 — 폭주하면 σ(w·Δz) 가 0/1 에 붙어 그 뒤 어떤 피드백도 안 먹힌다."""
    for sid, like in (("s0", True), ("s0", False), ("s2", True)):
        taste.record(sess, sid, like)
    before = sess["m"].copy()
    taste.record(sess, "s3", True)
    assert float(np.abs(sess["m"] - before).max()) > 1e-3


def test_한_걸음은_MAX_STEP_을_넘지_않는다(art, sess):
    """안전망 — 어떤 z 가 와도 한 장의 카드가 성분 가중치를 0.5 넘게 못 민다."""
    before = sess["m"].copy()
    taste._logistic_step(sess, np.full(content.N_COMPONENTS, 50.0), 0.0)
    moved = np.abs(sess["m"] - before)
    assert (moved <= taste.MAX_STEP + 1e-12).all()
    assert np.isclose(moved.max(), taste.MAX_STEP)   # 실제로 뚜껑에 닿았다


# ────────────────────────────────────────────────── 희소 성분 (Step 3d)
SPARSE_P = 300


def _sparse_cf_artifacts() -> content.Artifacts:
    """`cf` 만 극단적으로 희소한 풀 300곳 — 실서버 `cf` 의 모양을 축소한 것이다.

    공방문이 있는 쌍이 (t0, t1) 하나뿐이라 t0 을 like 하면 후보 298곳 중 297곳의
    점수가 0 이다. 표준편차가 0.058 이라 z(t1) 은 **+17** 이 된다 — 자르지 않으면
    한 걸음이 가중치를 수 단위로 민다. 나머지 modality 는 완만한 외적이라 z 가 작고
    쌍 안에서 거의 상쇄된다 — 이 검사가 `cf` 하나를 보게 하려는 것이다.
    """
    ids = [f"t{i}" for i in range(SPARSE_P)]
    base = np.linspace(0.2, 0.5, SPARSE_P, dtype=np.float32)
    flat = np.outer(base, base).astype(np.float32)      # 대칭·완만 (상수 아님)
    np.fill_diagonal(flat, 1.0)
    cooc = np.zeros((SPARSE_P, SPARSE_P), dtype=np.float32)
    np.fill_diagonal(cooc, 1.0)
    cooc[0, 1] = cooc[1, 0] = 1.0
    has = {k: np.ones(SPARSE_P, dtype=bool) for k in ("menu", "text", "review", "food")}
    return content.Artifacts(
        ids=ids, pos={s: i for i, s in enumerate(ids)},
        pop=np.log1p(np.arange(SPARSE_P, dtype=float)),
        quality=np.zeros(SPARSE_P),
        sim_menu=flat, sim_text=flat.copy(), sim_review=flat.copy(),
        sim_food=flat.copy(), cooc=cooc, has=has, brand=list(ids),
        prior={"source": "test", "mean": {c: 0.0 for c in content.COMPONENTS}
               | {"pop": 1.0, "cf": 0.5},
               "sd": {c: 0.5 for c in content.COMPONENTS}})


@pytest.fixture
def sparse(monkeypatch) -> content.Artifacts:
    a = _sparse_cf_artifacts()
    monkeypatch.setattr(content, "load", lambda *args, **kw: a)
    monkeypatch.setattr(taste.swipe, "pool", lambda: {
        "ids": list(a.ids), "pos": dict(a.pos),
        "cat": {s: "한식" for s in a.ids}, "qz": np.array(a.quality),
        "X": np.zeros((SPARSE_P, 1))})
    return a


SPARSE_SEQ = (("t0", True), ("t2", True), ("t1", False))


def test_희소한_cf_도_가중치를_폭주시키지_않는다(sparse):
    """z(t1) = +17 인 성분을 dislike 로 뒤집어도 가중치가 한 자리를 벗어나지 않는다.

    `like t0 → like t2 → dislike t1` — 모델은 t1 을 강하게 추천했는데(공방문) 사람이
    싫다고 했다. 이 '놀람' 이 클수록 로지스틱의 한 걸음이 커진다. 실서버 폭주의
    두 번째 원인이고, 되평가가 없어도 혼자 일어난다.
    """
    sess = taste._new_session()
    prior = sess["m"].copy()
    for sid, like in SPARSE_SEQ:
        taste.record(sess, sid, like)
    moved = np.abs(sess["m"] - prior)
    assert (moved <= taste.MAX_STEP * 2 + 1e-9).all(), sess["m"]
    assert float(np.abs(sess["m"]).max()) < 2.0, sess["m"]


def test_가드를_끄면_같은_입력이_실제로_폭주한다(sparse, monkeypatch):
    """검사가 공회전하지 않음을 보인다 — `Z_CLIP` 과 `MAX_STEP` 이 없으면 터진다."""
    monkeypatch.setattr(content, "Z_CLIP", 1e9)
    monkeypatch.setattr(taste, "MAX_STEP", 1e9)
    sess = taste._new_session()
    for sid, like in SPARSE_SEQ:
        taste.record(sess, sid, like)
    assert abs(float(sess["m"][I["cf"]])) > 3.0, sess["m"]


def test_같은_순서면_같은_상태다(art):
    seq = [("s0", True), ("s5", False), ("s2", True), ("s3", False), ("s1", True)]
    a, b = taste._new_session(), taste._new_session()
    for sid, like in seq:
        taste.record(a, sid, like)
    for sid, like in seq:
        taste.record(b, sid, like)
    assert np.array_equal(a["m"], b["m"])
    assert np.array_equal(a["v"], b["v"])


def test_사후분산은_줄어든다(art, sess):
    v0 = sess["v"].copy()
    taste.record(sess, "s0", True)
    taste.record(sess, "s5", False)
    assert (sess["v"] <= v0 + 1e-12).all()


def test_풀_밖_가게는_학습에서_뺀다(art, sess):
    taste.record(sess, "없는가게", True)
    assert sess["n"] == 0 and sess["like_ids"] == []


# ────────────────────────────────────────────────── 선택
def test_콜드스타트는_인기_평판_순이다(art, sess):
    got = taste.pick_many(sess, 3)
    assert got[0] == "s0"                            # 리뷰 100개 · 평점 최고
    assert len(set(got)) == 3


def test_이미_보여준_카드는_다시_안_나온다(art, sess):
    first = taste.pick_many(sess, 3)
    second = taste.pick_many(sess, 3)
    assert not (set(first) & set(second))


def test_같은_브랜드_연속_금지(art, sess):
    """s0 과 s1 은 같은 브랜드다. 점수는 s0 → s1 순이지만 연속으로 나오면 안 된다."""
    got = taste.pick_many(sess, 5)
    for a, b in zip(got, got[1:]):
        ia, ib = art.pos[a], art.pos[b]
        assert art.brand[ia] != art.brand[ib]


def test_같은_cat2_3연속_금지(art, sess):
    got = taste.pick_many(sess, 5)
    cats = [taste.swipe.pool()["cat"][s] for s in got]
    for a, b, c in zip(cats, cats[1:], cats[2:]):
        assert not (a == b == c)


def test_배치_안_근접중복_금지(art, sess):
    """메뉴 MaxSim 0.95 인 s0·s1 은 같은 배치에 함께 들어가지 않는다."""
    got = taste.pick_many(sess, 5)
    idx = [art.pos[s] for s in got]
    assert not {"s0", "s1"} <= set(got)
    for a in idx:
        for b in idx:
            if a != b and np.isfinite(art.sim_menu[a, b]):
                assert art.sim_menu[a, b] <= taste.DUP_MENU_SIM


def test_전부_막히면_그래도_한_장은_준다(art, sess):
    """다양성 규칙은 **선호**이지 금지가 아니다 — 빈 화면보다 규칙을 어긴 카드가 낫다.

    합성 풀 6곳을 다 뽑으면 마지막 한 장은 어떤 규칙도 만족시키지 못한다. 그때
    legacy 와 같은 규칙으로 최고점 카드를 그대로 준다(`fallback`).
    """
    got = taste.pick_many(sess, 6)
    assert len(got) == 6 and len(set(got)) == 6
    assert taste.pick_many(sess, 1) == []            # 다 소진하면 빈 목록


def test_10장에_1장은_탐색카드다(art, sess):
    sess["served"] = taste.EXPLORE_EVERY - 1
    reasons: dict[str, str] = {}
    got = taste.pick_many(sess, 1, reasons=reasons)
    assert reasons[got[0]] == "새로운 취향을 찾아보려고 골랐어요."
    reasons2: dict[str, str] = {}
    taste.pick_many(sess, 1, reasons=reasons2)
    assert "새로운 취향" not in next(iter(reasons2.values()))


def test_peek_는_세션을_안_건드린다(art, sess):
    taste.record(sess, "s0", True)
    before = (sess["m"].copy(), set(sess["shown"]), sess["served"])
    a = taste.peek(sess, 3)
    b = taste.peek(sess, 3)
    assert [x["store_id"] for x in a] == [x["store_id"] for x in b]   # 난수 없음
    assert (np.array_equal(before[0], sess["m"]) and before[1] == sess["shown"]
            and before[2] == sess["served"])
    assert set(a[0]["parts"]) == set(content.COMPONENTS)


# ────────────────────────────────────────────────── 추천 이유
def test_이유_콜드스타트(art, sess, monkeypatch):
    monkeypatch.setattr(taste.svc, 'get_store', lambda sid:
                        {'kakao_review_count': 5, 'kakao_avg_score': 4.8})
    line = taste.recommendation_reason(sess, "s1")
    assert '리뷰 5개' in line and '평점 4.8점' in line
    assert '인기' not in line


def test_이유_성분별_문장(art, sess):
    """가장 큰 양의 기여를 한 성분이 문장을 만든다 — 성분마다 문장이 다르다."""
    sess["like_ids"] = ["s0"]
    art_z = art.component_scores(np.array([0]), np.array([], dtype=int), np.array([0]))
    # 성분마다 s0 과 가장 닮은 후보가 다르다 (cf·음식 사진은 s2, 나머지는 s1).
    for key, cand, needle in (("menu", "s1", "메뉴가 좋아요한"),
                              ("text", "s1", "분위기·특징이"),
                              ("review", "s1", "리뷰와 닮았어요"),
                              ("food", "s2", "음식 사진이 좋아요한"),
                              ("cf", "s2", "간 사람들이 자주")):
        w = np.zeros(content.N_COMPONENTS)
        w[I[key]] = 1.0
        sess["m"] = w.copy()
        line = taste.recommendation_reason(sess, cand, art=art,
                                           z=art_z[:, art.pos[cand]], weights=w)
        assert needle in line, (key, line)
        assert "가게영" in line                      # 어떤 like 가게와 닮았는지 말한다


def test_이유_리뷰는_인용문을_붙인다(art, sess):
    sess["like_ids"] = ["s0"]
    w = np.zeros(content.N_COMPONENTS)
    w[I["review"]] = 1.0
    sess["m"] = w.copy()
    z = art.component_scores(np.array([0]), np.array([], dtype=int), np.array([0]))
    line = taste.recommendation_reason(sess, "s1", art=art, z=z[:, 1], weights=w)
    assert "s0 리뷰" in line and line.endswith("'")


def test_이유_음식사진은_사진이_없는_가게를_말하지_않는다(art, sess):
    """음식 사진이 없는 s3 은 그 성분의 z 가 0(중립)이라 이유가 되지 못한다.

    NaN 을 0 으로 채운 것이 '가장 닮았다' 로 새어 나가면 없는 근거를 말하게 된다.
    """
    sess["like_ids"] = ["s0"]
    w = np.zeros(content.N_COMPONENTS)
    w[I["food"]] = 1.0
    sess["m"] = w.copy()
    z = art.component_scores(np.array([0]), np.array([], dtype=int), np.array([0]))
    line = taste.recommendation_reason(sess, "s3", art=art, z=z[:, 3], weights=w)
    assert "음식 사진" not in line


def test_이유_탐색(art, sess):
    assert taste.recommendation_reason(sess, "s1", explore=True) == \
        "새로운 취향을 찾아보려고 골랐어요."


def test_이유는_난수로만_양수인_성분을_취향이라_말하지_않는다(art, sess):
    """사후 평균이 0 이하인 성분은 Thompson 난수가 밀어 올려도 이유가 되지 못한다."""
    sess["like_ids"] = ["s0"]
    sess["m"] = np.zeros(content.N_COMPONENTS)
    w = np.ones(content.N_COMPONENTS)
    z = art.component_scores(np.array([0]), np.array([], dtype=int), np.array([0]))
    line = taste.recommendation_reason(sess, "s1", art=art, z=z[:, 1], weights=w)
    assert "비슷해요" not in line and "닮았어요" not in line


def test_조사_선택(art):
    assert taste._particle("삼겹살", "과", "와") == "과"
    assert taste._particle("스시", "과", "와") == "와"


# ────────────────────────────────────────────────── 학습 상태
def test_learn_state(art, sess):
    for sid, like in (("s0", True), ("s5", False), ("s2", True), ("s3", True)):
        taste.record(sess, sid, like)
    st = taste.learn_state(sess)
    assert st["policy"] == "taste" and st["available"]
    assert st["likes"] == 3 and st["dislikes"] == 1 and st["swipes"] == 4
    assert [w["key"] for w in st["weights"]] == list(content.COMPONENTS)
    assert all("w" in w and "sd" in w for w in st["weights"])
    names = {p["name"] for p in st["prototypes"]}
    assert names and names <= {"가게영", "가게이", "가게삼"}
    assert len(st["prototypes"]) <= taste.MAX_PROTOTYPES
    assert len(st["hist"]) == 4


def test_프로토타입은_비슷한_가게를_묶는다(art, sess):
    """s2·s3 은 서로 닮았고 s0 은 멀다 — 5개 상한을 낮추면 그 둘이 먼저 묶인다."""
    for sid in ("s0", "s2", "s3"):
        taste.record(sess, sid, True)
    saved = taste.MAX_PROTOTYPES
    try:
        taste.MAX_PROTOTYPES = 2
        protos = taste._prototypes(art, sess["like_ids"])
    finally:
        taste.MAX_PROTOTYPES = saved
    assert len(protos) == 2
    big = max(protos, key=lambda p: p["size"])
    assert set(big["stores"]) == {"가게이", "가게삼"}
