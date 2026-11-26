# -*- coding: utf-8 -*-
"""백테스트 상수 — PLAN.md 1절을 코드로 고정한 것.

여기 있는 값이 곧 '계약' 이다. 바꾸면 결과가 달라지므로 PLAN.md 와 결과 .md 양쪽에
근거를 남긴다.
"""
from __future__ import annotations

from pathlib import Path

# analysis/backtest/config.py -> parents[2] = 저장소 루트.
# server/recsys/store.py 가 parents[2]/data 를 보는 것과 같은 배치다.
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "kakao"
PKG_DIR = Path(__file__).resolve().parent
RESULTS_DIR = PKG_DIR / "results"
CACHE_DIR = PKG_DIR / "cache"
SERVER_DIR = ROOT / "server"

SEED = 20260909                 # 난수는 전부 여기서 파생시킨다

# ── 후보 풀 / 품질 ─────────────────────────────────────────────
BAYES_C = 10.0                  # swipe.BAYES_C 와 같아야 한다 (테스트로 고정)
MIN_POOL_REVIEWS = 1            # 카드 자격: 카카오 리뷰 1+
MIN_POOL_PHOTOS = 1             # 카드 자격: 사진 1+

# 카드 자격 두 가지가 더 있다. **서버 `recsys/swipe.py` 와 같은 값이어야 한다** —
# `build_serving` 이 두 경로를 대조하고 다르면 빌드를 거부한다.
#
#   사진 1+ 리뷰 1+ 만          2,294곳
#   + 대표 사진이 음식           2,089곳   간판·인테리어·메뉴판 글자가 앞면에 깔리던 것
#   + 카드 3칸에 가격이 보임      1,629곳   뭘 파는지 얼마인지 없는 카드는 넘길 근거가 없다
FOOD_HERO_ONLY = True           # 대표 사진이 음식이라고 확인된 곳만
MENU_PRICE_REQUIRED = True      # 카드에 나가는 메뉴 3칸에 가격이 하나라도
CARD_MENUS = 3                  # 카드에 나가는 메뉴 수. 서버 `swipe.CARD_MENUS` 와 같다

# ── 평가 사용자와 시간 분할 ────────────────────────────────────
MIN_USER_REVIEWS = 10           # 평가 사용자 = 후보 풀 안 리뷰 T >= 10
FUTURE_RATIO = 0.3              # F = max(3, ceil(0.3*T))
FUTURE_MIN = 3
HISTORY_SIZES = (3, 5, 10, 20)  # 이력 N. 미래 직전의 최근 N 건(중첩)

# ── 이력의 해석 (제품이 아는 입력만) ───────────────────────────
# rating >= 4 -> like, <= 2 -> dislike, 3 -> 중립(모델에 넣지 않는다).
LIKE_MIN_RATING = 4
DISLIKE_MAX_RATING = 2

# Step 1.5 계약 수정 (PLAN 5절 'Step 1 -> Step 1.5' 1번): 과제 P 의 이력 해석은
# **방문 = 관심** 이다. 방문한 가게 전부가 가중 1 의 양성 신호이고 dislike 는 없다.
#   visit         이력의 모든 방문을 like 로 (별점 무관). 과제 P 의 기본.
#   like_dislike  rating >= 4 like / <= 2 dislike / 3 중립 — Step 0·1 의 해석.
HISTORY_MODES = ("visit", "like_dislike")
DEFAULT_HISTORY_MODE = "visit"

# ── 과제 R: 방문 가게 중 만족 예측 ─────────────────────────────
# 이쪽은 3 이 음성이다(과제 정의가 다르다 — like 매핑과 헷갈리지 말 것).
SATISFIED_MIN_RATING = 4

# ── 지표 ───────────────────────────────────────────────────────
HIT_K = 10
RECALL_K = 50
NDCG_K = 20

# ── 유의성 ─────────────────────────────────────────────────────
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_ALPHA = 0.05
BASELINE_MODEL = "quality"      # paired bootstrap 의 기준

# ── 하이퍼파라미터 선택 (PLAN 5절 'Step 1 -> Step 1.5' 3번) ────
# λ 를 고르는 잣대. 스와이프 제품은 상위 10~20장이 전부라 AUC 만으로 고르면
# 꼬리를 잘 세우는 모델이 뽑힐 수 있다. 두 잣대의 선택을 나란히 보고한다.
SELECT_METRICS = ("auc", f"ndcg@{NDCG_K}")
DEFAULT_SELECT_METRIC = "auc"
