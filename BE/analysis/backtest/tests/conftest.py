# -*- coding: utf-8 -*-
"""테스트 공용 도구 — 손으로 만든 작은 데이터셋.

실데이터로 도는 테스트는 `test_current_model.py` 와 `test_pool_real.py` 뿐이고,
규칙 검증은 전부 여기서 만든 몇 줄짜리 데이터로 한다. 규칙이 틀렸는지 데이터가
이상한지 헷갈리지 않게 하기 위해서다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.backtest.data import Dataset, Review          # noqa: E402
from analysis.backtest.pool import build_pool               # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: 실데이터를 끝까지 도는 테스트(수 초)")


def make_dataset(stores, reviews) -> Dataset:
    """stores: [(store_id, 리뷰수, 평균, cat2, 메뉴가격들, 사진수)]
       reviews: [(store_id, author_id, rating, date)]"""
    s_map, order = {}, []
    menu_prices, photo_count = {}, {}
    for sid, cnt, avg, cat2, prices, photos in stores:
        s_map[sid] = {"store_id": sid, "review_count": cnt, "review_avg": avg,
                      "kakao_review_count": cnt, "kakao_avg_score": avg, "cat2": cat2}
        order.append(sid)
        menu_prices[sid] = list(prices)
        photo_count[sid] = photos
    rv = [Review(store_id=st, review_id=i + 1, author_id=au, rating=rt, date=dt)
          for i, (st, au, rt, dt) in enumerate(reviews)]
    return Dataset(stores=s_map, store_order=order, reviews=rv,
                   menu_prices=menu_prices, photo_count=photo_count,
                   hero_food={sid: count > 0 for sid, count in photo_count.items()},
                   card_prices={sid: list(prices) for sid, prices in menu_prices.items()})


def linear_history(user: str, store_ids, ratings, start_day: int = 1):
    """한 사용자의 리뷰를 하루씩 띄워 만든다. 날짜 정렬이 곧 입력 순서가 된다."""
    return [(sid, user, rt, f"2026-01-{start_day + i:02d} 12:00:00")
            for i, (sid, rt) in enumerate(zip(store_ids, ratings))]


@pytest.fixture(scope="session")
def real_pool():
    """실데이터 후보 풀. 무거우므로 세션 하나만 만든다."""
    from analysis.backtest.data import load_dataset
    ds = load_dataset()
    return ds, build_pool(ds)
