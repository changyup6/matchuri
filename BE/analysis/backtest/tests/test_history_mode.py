# -*- coding: utf-8 -*-
"""이력 해석 두 벌 — `visit`(방문 = 관심) 과 `like_dislike`.

PLAN 5절 'Step 1 → Step 1.5' 결정 1번의 계약 수정이다. 검증할 것은 세 가지다.

  1. `visit` 은 별점을 보지 않고 방문 전부를 like 로 준다 (dislike 0건).
  2. **분할 자체는 바뀌지 않는다** — 과거·미래·N 창이 두 해석에서 완전히 같아야
     두 해석을 짝지어 비교할 수 있다.
  3. Step 0·1 은 `like_dislike` 로 재현된다 — 기본값을 바꾼 곳은 실행기뿐이다.
"""
from __future__ import annotations

import numpy as np
import pytest

from analysis.backtest import config, run, scorers
from analysis.backtest.evaluate import evaluate
from analysis.backtest.pool import build_pool
from analysis.backtest.scorers import ScoreContext
from analysis.backtest.split import build_splits
from analysis.backtest.tests.conftest import linear_history, make_dataset

STORES = [(f"s{i}", 10, 4.0, "한식", [10000], 1) for i in range(20)]
#: 별점을 섞는다 — like(5,4) · 중립(3) · dislike(1,2) 가 전부 들어 있다.
RATINGS = [5, 3, 1, 4, 5, 2, 5, 3, 4, 5, 1, 5]


def _tiny(n_users: int = 6):
    reviews = []
    for u in range(n_users):
        reviews += linear_history(f"u{u}", [f"s{(u + i) % 20}" for i in range(12)],
                                  RATINGS)
    ds = make_dataset(STORES, reviews)
    pool = build_pool(ds)
    return ds, pool, build_splits(ds, pool)


def test_visit은_방문_전부를_like로_준다():
    _, _, splits = _tiny()
    sp = splits[0]
    sig = sp.signals(8, "visit")
    assert [s for s, _ in sig] == [s for s, _ in sp.history(8)]
    assert all(like for _, like in sig)              # dislike 가 하나도 없다
    assert len(sig) == len(sp.history(8))            # 버리는 신호가 없다


def test_like_dislike는_중립을_버리고_dislike를_남긴다():
    _, _, splits = _tiny()
    sp = splits[0]
    ld = sp.signals(8, "like_dislike")
    assert len(ld) < len(sp.history(8))              # rating 3 을 버렸다
    assert any(not like for _, like in ld)           # dislike 가 남아 있다


def test_기본값은_옛_해석이라_Step0_1이_재현된다():
    """라이브러리 기본은 `like_dislike` — 실행기만 `--history` 로 바꾼다."""
    _, _, splits = _tiny()
    sp = splits[0]
    assert sp.signals(8) == sp.signals(8, "like_dislike")
    assert config.DEFAULT_HISTORY_MODE == "visit"    # 계약(실행기)의 기본은 visit


def test_모르는_해석은_이름을_알려주며_거절한다():
    _, _, splits = _tiny()
    with pytest.raises(ValueError, match="모르는 이력 해석"):
        splits[0].signals(5, "무엇이든")


def test_분할은_해석과_무관하게_같다():
    """과거·미래·N 창이 같아야 두 해석을 같은 사용자로 짝지어 비교할 수 있다."""
    _, _, splits = _tiny()
    for sp in splits:
        for n in (3, 5, 10):
            visit = [s for s, _ in sp.signals(n, "visit")]
            assert visit == [s for s, _ in sp.history(n)]
            ld = [sid for sid, _ in sp.signals(n, "like_dislike")]
            assert set(ld) <= set(visit)        # 가게 집합은 부분집합이다


def test_evaluate는_ctx의_해석을_쓴다():
    _, pool, splits = _tiny()
    seen = {"visit": [], "like_dislike": []}

    class Spy(scorers.Scorer):
        name = "spy"

        def __init__(self, mode):
            self.mode = mode

        def score(self, history, ctx, *, user_id):
            seen[self.mode].append(list(history))
            return np.zeros(len(ctx.pool))

    for mode in ("visit", "like_dislike"):
        ctx = ScoreContext(pool=pool, history_mode=mode)
        evaluate(Spy(mode), splits, ctx, 5)
    assert all(all(like for _, like in h) for h in seen["visit"])
    assert sum(len(h) for h in seen["visit"]) > sum(len(h) for h in seen["like_dislike"])


def test_visit_모드의_current는_모든_방문을_like로_먹는다():
    """서버 모델은 like/dislike 를 나눠 받는다. visit 에서는 전부 like 다."""
    from analysis.backtest.current_model import CurrentModel
    _, pool, splits = _tiny()
    sp = splits[0]
    a = CurrentModel(pool).replay(sp.signals(8, "visit")).scores()
    b = CurrentModel(pool).replay([(s, True) for s, _ in sp.history(8)]).scores()
    np.testing.assert_allclose(a, b)


def test_신호_평균은_해석마다_따로_기록된다():
    _, pool, splits = _tiny()
    ctx = ScoreContext(pool=pool, history_mode="visit")
    raw = {"popularity": {n: evaluate(scorers.get("popularity"), splits, ctx, n)
                          for n in (3, 5)}}
    meta = run.summarise_users(splits, raw, ["popularity"], [3, 5],
                               history_mode="visit")
    assert meta["history_mode"] == "visit"
    # visit 은 버리는 신호가 없다 -> 다 채운 사용자에게는 신호 수가 곧 N 이다
    assert meta["signal_mean_full"]["3"] == pytest.approx(3.0)
    assert meta["signal_mean_full"]["5"] == pytest.approx(5.0)
    # 같은 분할을 like_dislike 로 읽으면 중립(rating 3)을 버려 더 적다
    assert meta["signal_mean_like_dislike"]["5"] < meta["signal_mean"]["5"]
