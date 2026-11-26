# -*- coding: utf-8 -*-
"""성분 z 점수 (`recsys/content.py`) — 작은 합성 풀에서 손으로 검산한다."""
from __future__ import annotations

import numpy as np
import pytest

from recsys import content

I = {c: i for i, c in enumerate(content.COMPONENTS)}


def _z(art, like=(), dislike=(), exclude=None):
    like = np.array(like, dtype=int)
    dislike = np.array(dislike, dtype=int)
    ex = (np.concatenate([like, dislike]) if exclude is None
          else np.array(exclude, dtype=int))
    return art.component_scores(like, dislike, ex)


def _expect(raw, mask):
    """참조 구현 — z 정규화하고 ±`Z_CLIP` 에서 자른다 (`component_scores` 와 같은 순서)."""
    return np.clip(content.zscore_masked(raw, mask), -content.Z_CLIP, content.Z_CLIP)


def test_z_는_후보_안에서_평균0_표준편차1(art):
    """눈금 맞추기의 정의. 기준은 **유한한 후보**이고 NaN 은 그 평균(=0)으로 간다.

    그래서 NaN 이 섞인 modality 는 '전체' 표준편차가 1 보다 작아진다 — 그것이
    'modality 가 없는 가게는 그 성분에서 아무 주장도 하지 않는다' 의 다른 표현이다.
    """
    z = _z(art, like=[0])
    mask = np.ones(6, dtype=bool)
    mask[0] = False                       # 이력은 정규화 기준에서 빠진다
    for name, sim in (("pop", None), ("quality", None), ("menu", art.sim_menu),
                      ("text", art.sim_text), ("review", art.sim_review),
                      ("food", art.sim_food)):
        ok = mask.copy()
        if sim is not None:
            ok &= np.isfinite(np.asarray(sim[0], dtype=float))
        row = z[I[name]][ok]
        assert abs(float(row.mean())) < 1e-9
        assert abs(float(row.std()) - 1.0) < 1e-9
        if sim is not None and not ok[mask].all():
            assert float(z[I[name]][mask].std()) < 1.0


def test_like_가_없으면_콘텐츠_성분은_전부_0(art):
    z = _z(art)
    for name in ("cf", "menu", "text", "review", "food", "neg"):
        assert np.allclose(z[I[name]], 0.0)
    assert z[I["pop"]].std() > 0          # 인기·평판은 콜드스타트에서도 산다


def test_z_pop_은_리뷰수_순서다(art):
    z = _z(art, like=[0])
    order = np.argsort(-z[I["pop"]])
    assert list(order) == [0, 1, 2, 3, 4, 5]


def test_z_menu_는_like_와의_최대유사도다(art):
    z = _z(art, like=[0])
    raw = np.asarray(art.sim_menu[0], dtype=float)
    finite = np.isfinite(raw)
    # 순서가 원본 유사도 순서와 같아야 한다 (z 는 단조변환이다)
    assert np.argmax(z[I["menu"]][finite]) == np.argmax(raw[finite])
    assert z[I["menu"]][4] == 0.0         # 메뉴가 없는 s4 는 z 정규화 뒤 0(중립)


def test_z_menu_는_여러_like_중_최대를_쓴다(art):
    z = _z(art, like=[0, 3])
    raw = np.maximum(np.asarray(art.sim_menu[0], float),
                     np.asarray(art.sim_menu[3], float))
    ok = np.isfinite(raw)
    assert np.allclose(np.argsort(-z[I["menu"]][ok]), np.argsort(-raw[ok]))


def test_z_text_는_이력이_하나면_그_행_그대로다(art):
    """lse 는 τ·log(Σexp(s/τ)/H) 라 H=1 이면 항등이다 — 집계 식의 성질 검사."""
    rows = np.asarray(art.sim_text[[0]], dtype=np.float32)
    assert np.allclose(content._lse(rows), rows[0], atol=1e-4, equal_nan=True)


def test_z_text_lse_는_max_와_mean_사이다(art):
    rows = np.asarray(art.sim_text[[0, 2]], dtype=np.float32)
    lse = content._lse(rows)
    ok = np.isfinite(lse)
    assert (lse[ok] <= rows.max(axis=0)[ok] + 1e-4).all()
    assert (lse[ok] >= rows.mean(axis=0)[ok] - 1e-4).all()


def test_z_review_는_평균집계다(art):
    z = _z(art, like=[0, 2])
    raw = np.asarray(art.sim_review[[0, 2]], dtype=float).mean(axis=0)
    mask = np.ones(6, dtype=bool)
    mask[[0, 2]] = False
    assert np.allclose(z[I["review"]], _expect(raw, mask))


def test_z_food_는_lse_집계다(art):
    """음식 사진은 요약문과 같은 집계(logsumexp τ=0.1)를 쓴다 — 온라인 전용 성분이라
    오프라인 λ 를 물려받지 않고, 같은 τ 를 쓰는 z_text 와 눈금 만드는 방식을 맞췄다."""
    z = _z(art, like=[0, 2])
    rows = np.asarray(art.sim_food[[0, 2]], dtype=np.float32)
    mask = np.ones(6, dtype=bool)
    mask[[0, 2]] = False
    assert np.allclose(z[I["food"]], _expect(content._lse(rows), mask),
                       equal_nan=True)


def test_z_food_의_NaN_규칙은_요약문과_같다(art):
    """음식 사진이 없는 s3 은 z 정규화 뒤 0(중립)이고 기준통계에도 안 들어간다."""
    z = _z(art, like=[0])
    assert z[I["food"]][3] == 0.0
    mask = np.ones(6, dtype=bool)
    mask[0] = False
    finite = mask & np.isfinite(np.asarray(art.sim_food[0], dtype=float))
    assert abs(float(z[I["food"]][finite].mean())) < 1e-9
    # 음식 사진이 없는 가게만 like 했으면 이 성분은 아무 말도 하지 않는다.
    assert np.allclose(_z(art, like=[3])[I["food"]], 0.0)


def test_z_food_는_다른_성분과_다른_가게를_세운다(art):
    """s0 을 like 했을 때 메뉴는 s1 을, 음식 사진은 s2 를 가장 높이 든다.

    성분이 서로 다른 것을 본다는 주장의 최소 검사다 — 같은 순서를 낸다면 성분을
    하나 더 둘 이유가 없다.
    """
    z = _z(art, like=[0])
    order_menu = [i for i in np.argsort(-z[I["menu"]]) if i != 0]
    order_food = [i for i in np.argsort(-z[I["food"]]) if i != 0]
    assert order_menu[0] == 1 and order_food[0] == 2


def test_z_cf_는_합집계다(art):
    z = _z(art, like=[0, 2])
    raw = np.asarray(art.cooc[[0, 2]], dtype=float).sum(axis=0)
    mask = np.ones(6, dtype=bool)
    mask[[0, 2]] = False
    assert np.allclose(z[I["cf"]], _expect(raw, mask))


def test_z_는_ZCLIP_에서_잘린다(art):
    """희소·긴꼬리 성분의 z 는 ±3 을 넘지 못한다 (Step 3d).

    합성 풀의 `cf` 는 s0 을 like 하면 s0 자신(cooc=1)만 크고 나머지가 거의 0 이라
    자르지 않으면 z 가 2 를 훌쩍 넘는다. 이 한 줄이 온라인 갱신을 폭주에서 지킨다.
    """
    for like, dislike in (([0], []), ([0, 2], []), ([0], [2]), ([], [0])):
        z = _z(art, like=like, dislike=dislike)
        assert float(np.abs(z).max()) <= content.Z_CLIP + 1e-12
    # 자르기 전에는 실제로 넘는다 — 검사가 공회전하지 않음을 보인다.
    raw = np.asarray(art.cooc[[0]], dtype=float).sum(axis=0)
    mask = np.ones(6, dtype=bool)
    mask[0] = False
    assert float(np.abs(content.zscore_masked(raw, mask)).max()) > content.Z_CLIP
    assert np.isclose(_z(art, like=[0])[I["cf"]][0], content.Z_CLIP)


def test_z_neg_는_dislike_와_세_유사도의_평균의_최대다(art):
    z = _z(art, like=[0], dislike=[2])
    parts = []
    for sim, kind in ((art.sim_menu, "menu"), (art.sim_text, "text"),
                      (art.sim_review, "review")):
        row = np.asarray(sim[2], dtype=float)
        parts.append(np.where(np.isfinite(row), row, np.nan))
    stack = np.vstack(parts)
    cnt = np.isfinite(stack).sum(axis=0)
    avg = np.where(cnt > 0, np.nansum(stack, axis=0) / np.maximum(cnt, 1), np.nan)
    mask = np.ones(6, dtype=bool)
    mask[[0, 2]] = False
    assert np.allclose(z[I["neg"]], content.zscore_masked(avg, mask))


def test_NaN_은_z_정규화_뒤_0_이고_기준통계에_안_들어간다(art):
    z = _z(art, like=[0])
    assert z[I["menu"]][4] == 0.0         # 메뉴 없는 가게
    assert z[I["text"]][5] == 0.0         # 요약문 없는 가게
    # NaN 을 0 으로 먼저 채우고 z 를 냈다면 평균이 0 이 아니게 된다 — 그렇지 않음을 본다
    mask = np.ones(6, dtype=bool)
    mask[0] = False
    finite = mask & np.isfinite(np.asarray(art.sim_menu[0], dtype=float))
    assert abs(float(z[I["menu"]][finite].mean())) < 1e-9


def test_이력이_전부_NaN_인_modality_는_침묵한다(art):
    """메뉴가 없는 가게만 like 했으면 메뉴 성분은 아무 말도 하지 않는다(전부 0)."""
    z = _z(art, like=[4])
    assert np.allclose(z[I["menu"]], 0.0)
    assert not np.allclose(z[I["review"]], 0.0)      # 리뷰는 s4 에도 있다


def test_결정적이다(art):
    a = _z(art, like=[0, 2], dislike=[3])
    b = _z(art, like=[0, 2], dislike=[3])
    assert np.array_equal(a, b)


def test_설명_조회(art):
    like = np.array([0, 3])
    assert art.best_like("menu", 1, like) == 0        # s1 은 s0 과 가장 닮았다
    assert art.best_like("menu", 2, like) == 3        # s2 는 s3 과 가장 닮았다
    assert art.best_like("menu", 0, np.array([], dtype=int)) is None
    a, b, cos = art.review_pair(0, 1)
    assert a.startswith("s0 리뷰") and b.startswith("s1 리뷰") and cos > 0.5


def test_사전값_읽기(art):
    m = art.prior_mean()
    assert m[I["pop"]] == 1.0 and m[I["neg"]] < 0
    assert (art.prior_sd() > 0).all()


@pytest.mark.parametrize("n_like", [1, 3, 5])
def test_후보가_거의_없으면_풀_전체로_정규화한다(art, n_like):
    """전부 답한 극단에서도 0 으로 나누거나 죽지 않는다."""
    z = art.component_scores(np.arange(n_like), np.array([], dtype=int),
                             np.arange(6))
    assert np.isfinite(z).all()
