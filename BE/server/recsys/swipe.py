# -*- coding: utf-8 -*-
"""스와이프 추천 — reco_proto 의 HybridTS(품질->개인화 + Thompson)를 실데이터에 이식.

시뮬레이터 결론을 그대로 반영한다:
  - 백본은 베이지안 품질 정렬 (replay 검증에서 무개인화 최강)
  - 개인화는 카테고리·가격 로지스틱 재가중. 초반 가중치가 품질에서 출발해
    스와이프가 쌓일수록 이동한다 (0에서 출발시키면 초반 수업료를 낸다 — 실측)
  - MF 전이는 이득이 확인되지 않아 보류
  - 같은 카테고리 연속 3장 금지 (탐색 + UX)

세션은 메모리에만 산다. 프로토 용도라 재시작하면 초기화된다.
"""
from __future__ import annotations

import statistics
import threading
import time
import uuid

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import store as svc

router = APIRouter(prefix="/swipe", tags=["swipe"])

CATS = ["한식", "카페", "술집", "일식", "양식", "중식",
        "간식", "분식", "치킨", "샐러드"]          # 역삼동 상위 10 (실측)
PRICE_EDGES = [8000, 13000, 21000]                 # 메뉴 중앙가 4분위 (실측)
PRICE_NAMES = ["~8천", "8천~1.3만", "1.3~2.1만", "2.1만+", "가격미상"]
BAYES_C = 10.0

# 카드 자격 — 대표 사진이 음식인 곳만 낸다.
#
# 사진 1장 + 리뷰 1건이면 카드가 되던 때는 2,216곳이었고, 그중 9.2%는 대표
# 사진이 음식이 아니었다 (간판·인테리어·메뉴판 글자). "이 가게 뭘 파는지"를
# 보여줘야 할 자리다. 역삼동 한 동네에 2,800곳이고 사용자는 한 세션에 30장을
# 넘긴다 — **수를 줄여 질을 올리는 쪽이 남는 장사다.** 2,011곳이면 67바퀴다.
#
# `food` 는 photos.jsonl 이 사진마다 들고 있다 (reco/export_photos.py 가 넣는다).
# **없는 것과 false 는 다르다** — 없으면 그 사진을 받아 본 적이 없어 모르는
# 것이다. 모르는 것은 내보내지 않는다. 지금 자료에서 대표 사진이 '모름'인
# 가게는 1곳뿐이라 이 엄격함으로 잃는 것이 없다.
FOOD_HERO_ONLY = True

# 두 번째 기준 — 산출물을 만들 때 사용한 대표 메뉴 3개 중 가격이 있는가.
# 화면은 원본 menu_index 순서이고 모델 입력은 기존 음식·인기·추천 우선순위다.
# 따라서 표시 앞 3개의 가격을 보장하는 기준은 아니다. 메뉴 표시 순서를 바꾸는
# 것만으로 후보 풀과 이미 만든 유사도 산출물이 어긋나지 않도록 유지한다.
MENU_PRICE_REQUIRED = True
CARD_MENUS = 3           # 카드에 나가는 메뉴 수. _card 와 같아야 한다
MIN_POOL = 100           # 이보다 적게 남으면 자료가 잘못된 것으로 본다

# RLock 이어야 한다: _session() 이 잠근 상태에서 _new_session() -> pool() 이
# 같은 락을 다시 잡는다 (일반 Lock 이면 데드락 — 실제로 겪었다)
_lock = threading.RLock()
_pool: dict | None = None
_sessions: dict[str, dict] = {}


# ────────────────────────────────────────────────────── 후보 풀
def _price_bucket(sid: str) -> int:
    prices = [m["price"] for m in svc.get_feature_menus(sid, limit=300)
              if isinstance(m.get("price"), int) and m["price"] > 0]
    if not prices:
        return 4                                   # 미상
    med = statistics.median(prices)
    for i, e in enumerate(PRICE_EDGES):
        if med < e:
            return i
    return 3


def _featurize(sid: str, s: dict, qz: float) -> np.ndarray:
    x = np.zeros(3 + len(CATS) + 1 + 5)
    x[0] = 1.0
    x[1] = qz
    x[2] = np.log1p(s.get("kakao_review_count") or 0) / 6.0
    c = s.get("cat2")
    x[3 + (CATS.index(c) if c in CATS else len(CATS))] = 1.0
    x[3 + len(CATS) + 1 + _price_bucket(sid)] = 1.0
    return x


def _card_menus(sid: str, *, for_features: bool = False) -> list[dict]:
    """카드에 실제로 나가는 메뉴 CARD_MENUS 개.

    같은 이름을 두 번 띄우지 않는다. 한 가게가 같은 메뉴를 값만 달리해 여러
    줄로 올려 두는 일이 있다 (고우가 역삼역점: "메뉴판 (런치)" 가 99,000 과
    189,000 으로 두 줄). 카드에는 세 칸뿐이라 한 칸을 그냥 버리는 셈이어서,
    이름이 겹치면 뒤쪽 메뉴로 채운다.

    화면은 원본 순서다. 후보 자격의 가격 검사는 기존 산출물과 같은 메뉴 순서를
    사용한다(for_features=True). 표시 순서만 바꿔 기존 풀·취향 모델을 유지한다.
    """
    out, seen = [], set()
    get_menus = svc.get_feature_menus if for_features else svc.get_menus
    for m in get_menus(sid, limit=CARD_MENUS * 4):
        name = m.get("menu_name")
        if name in seen:
            continue
        seen.add(name)
        out.append({"menu_name": name, "price": m.get("price")})
        if len(out) >= CARD_MENUS:
            break
    return out


def _card_price(sid: str) -> bool:
    """기존 산출물의 후보 자격: 모델 입력 메뉴 안에 가격이 하나라도 있나."""
    return any(isinstance(m.get("price"), int) and m["price"] > 0
               for m in _card_menus(sid, for_features=True))


def _eligible(food_only: bool, need_price: bool = False) -> list[tuple[str, dict]]:
    """카드가 될 자격이 있는 가게. 리뷰 1건 이상 + 사진 1장 이상이 바닥이다."""
    out = []
    for sid in svc.all_store_ids():
        s = svc.get_store(sid)
        if not s or (s.get("kakao_review_count") or 0) < 1:
            continue
        hero = svc.hero_photo(sid)
        if not hero:
            continue
        if food_only and hero.get("food") is not True:
            continue
        if need_price and not _card_price(sid):
            continue
        out.append((sid, s))
    return out


def _build_pool() -> dict:
    """카드 자격: 리뷰 1+ · 대표 사진이 음식 · 가격이 보임 (1,617곳 실측)."""
    svc.load()
    rows = _eligible(FOOD_HERO_ONLY, MENU_PRICE_REQUIRED)
    if len(rows) < MIN_POOL:
        # 자료가 기대한 모양이 아니다 — `food` 가 없는 옛 photos.jsonl 이거나
        # menus.jsonl 이 비었거나. 카드가 한 장도 안 나가는 것보다는 기준을
        # 내리는 쪽이 낫다. 조용히 넘어가지는 않는다.
        rows = _eligible(False, False)
        print("[swipe] 자격을 갖춘 가게가 %d곳뿐이라 기준을 전부 껐다 (%d곳)."
              " photos.jsonl 의 food · menus.jsonl 의 price 를 확인할 것."
              % (len(_eligible(FOOD_HERO_ONLY, MENU_PRICE_REQUIRED)), len(rows)))

    mu_num = sum((s.get("kakao_avg_score") or 0) * (s.get("kakao_review_count") or 0)
                 for _, s in rows)
    mu_den = sum(s.get("kakao_review_count") or 0 for _, s in rows)
    mu = mu_num / max(mu_den, 1)
    bayes = np.array([
        (BAYES_C * mu + (s.get("kakao_avg_score") or mu) * (s.get("kakao_review_count") or 0))
        / (BAYES_C + (s.get("kakao_review_count") or 0)) for _, s in rows])
    qz = (bayes - bayes.mean()) / (bayes.std() or 1.0)

    ids = [sid for sid, _ in rows]
    X = np.stack([_featurize(sid, s, qz[i]) for i, (sid, s) in enumerate(rows)])
    cat = {sid: s.get("cat2") for sid, s in rows}
    return {"ids": ids, "pos": {s: i for i, s in enumerate(ids)},
            "X": X, "qz": qz, "cat": cat}


def pool() -> dict:
    global _pool
    with _lock:
        if _pool is None:
            _pool = _build_pool()
        return _pool


# ────────────────────────────────────────────────────── 세션 = 정책 상태
def _new_session() -> dict:
    d = pool()["X"].shape[1]
    return {"m": np.zeros(d), "v": np.full(d, 1.0),
            "shown": set(), "last_cats": [], "n": 0, "likes": 0,
            "pending": None, "rng": np.random.default_rng(),
            "cat_stats": {}, "hist": [],
            "created": time.time()}


def _session(sid: str | None) -> tuple[str, dict]:
    with _lock:
        if sid and sid in _sessions:
            return sid, _sessions[sid]
        new_id = sid or uuid.uuid4().hex[:12]
        _sessions[new_id] = _new_session()
        # Keep durable user learning until an explicit recommendation-session
        # boundary. Evicting just this index loses history on the next request.
        return new_id, _sessions[new_id]


def _mix_personal(sess: dict, learned: np.ndarray,
                  prior: np.ndarray | None = None) -> tuple[float, np.ndarray]:
    """Keep inherited evidence active while this session is still collecting.

    The model already contains its shrunken prior. Adding only the quality
    share of that prior counts it once: prior + (1-w_q) * (learned-prior).
    Without inherited evidence the original cold-start score is unchanged.
    Inputs can be whole-candidate scores or per-feature contributions.
    """
    w_q = 1 / (1 + sess["n"] / 15)
    personal = (1 - w_q) * learned
    if prior is not None:
        personal = personal + w_q * prior
    return w_q, personal


def recommendation_reason(
    sess: dict | None, store_id: str, *,
    sampled_weights: np.ndarray | None = None, diverse: bool = False,
) -> str:
    """선택에 쓰인 양의 기여를 한 줄로 설명한다. 난수·학습 상태는 바꾸지 않는다."""
    if diverse:
        return "비슷한 종류가 이어져 이번에는 다른 메뉴를 골랐어요."
    quality_reason = "평점과 리뷰 수를 함께 고려해 골랐어요."
    if sess is None or (sess["n"] == 0 and not np.any(sess.get("prior_m", 0))):
        return quality_reason

    p = pool()
    i = p["pos"][store_id]
    x = p["X"][i]
    m = sess["m"]
    w = m if sampled_weights is None else sampled_weights
    prior = sess.get("prior_m")
    w_q, contributions = _mix_personal(
        sess, x * w, None if prior is None else x * prior)
    _, expected_weights = _mix_personal(sess, m, prior)
    candidates = []
    quality = w_q * p["qz"][i] + contributions[1]
    if p["qz"][i] > 0 and quality > 0:
        candidates.append((float(quality), quality_reason))

    # 탐색 난수만 양수인 특성을 이미 좋아하는 취향이라고 설명하지 않는다.
    cat = p["cat"].get(store_id)
    if cat in CATS:
        j = 3 + CATS.index(cat)
        if expected_weights[j] > 0.05 and contributions[j] > 0:
            candidates.append((float(contributions[j]),
                               f"지금까지의 취향과 잘 맞는 {cat} 가게예요."))
    price_start = 3 + len(CATS) + 1
    for j in range(price_start, price_start + 4):  # 가격 미상은 설명에서 제외
        if expected_weights[j] > 0.05 and contributions[j] > 0:
            candidates.append((float(contributions[j]),
                               "메뉴 가격대가 지금까지 보여주신 취향과 잘 맞아요."))

    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return "새로운 취향을 찾아볼 수 있도록 골라봤어요."


def contributions(sess: dict, store_id: str, w: np.ndarray | None = None) -> dict[str, float]:
    """카드 응답의 `attribution` 재료 — legacy 점수를 항별로 가른다.

    `recommendation_reason` 과 같은 계산(`_mix_personal`)이다. 그쪽은 가장 큰 양의 기여 하나를
    문장으로 바꾸고, 여기는 숫자 전부를 준다:

        quality   평점 항 (콜드스타트 무게 w_q × 평점 z + 학습된 평점 특성)
        reviews   리뷰 수 특성
        category  그 가게 업종에 대한 학습된 취향
        price     가격대에 대한 학습된 취향 (가격 미상 포함)
        cold_weight  w_q — 1 에 가까우면 아직 평점이 다 정하는 구간이다
    """
    p = pool()
    i = p["pos"][store_id]
    x = p["X"][i]
    w = sess["m"] if w is None else w
    prior = sess.get("prior_m")
    w_q, c = _mix_personal(sess, x * w, None if prior is None else x * prior)
    cat = p["cat"].get(store_id)
    cat_c = float(c[3 + CATS.index(cat)]) if cat in CATS else 0.0
    ps = 3 + len(CATS) + 1
    return {"quality": round(float(w_q * p["qz"][i] + c[1]), 4),
            "reviews": round(float(c[2]), 4),
            "category": round(cat_c, 4),
            "price": round(float(c[ps:ps + 5].sum()), 4),
            "cold_weight": round(float(w_q), 4)}


def align(ids: list[str], mapping: dict | None) -> np.ndarray | None:
    """`{store_id: 값}` 을 후보 순서의 배열로. 비었으면 None(= 보정 없음).

    **두 정책이 같이 쓴다**(`taste` 는 자기 `art.ids` 순서로 부른다) — 정렬 보정은 한쪽만
    받으면 인터리빙 비교가 무의미해지므로 같은 값을 같은 방식으로 펴야 한다. 없는 키는
    0 이다: 태그를 하나도 안 가진 가게의 계층은 0, 좌표를 모르는 가게의 보너스는 0(평균).
    """
    if not mapping:
        return None
    return np.array([float(mapping.get(sid, 0.0)) for sid in ids], dtype=float)


def _pick(sess: dict, *, reasons: dict[str, str] | None = None,
          allow: set[str] | None = None, tier: np.ndarray | None = None,
          bonus: np.ndarray | None = None,
          explain: dict | None = None) -> str | None:
    """점수 순으로 첫 후보. `allow` 를 주면 그 집합 밖은 **fallback 으로도** 안 쓴다.

    필터(업종·태그·거리)가 여기 들어오는 이유는 `app/filters.py` 에 적었다 — 뽑고 나서
    버리면 그 카드가 `shown` 에 남아 다시 안 나온다.

    `tier`·`bonus` 는 `app/ranking.py` 가 만든 정렬 보정이다(후보 순서로 펴 놓은 배열).
    계층이 1순위 키, 점수+보너스가 2순위다 — **다양성 규칙과 `shown`·`allow` 는 그대로**
    이 새 순서 위에서 돈다.
    """
    p = pool()
    # 완화된 Thompson (sigma x0.4): 원 sigma 는 탐색 노이즈가 이득보다 컸다.
    # 페르소나 5종 실측에서 x0.4 가 전부 우세 (한식러 후반 +4.8%p 등).
    w = sess["rng"].normal(sess["m"], 0.4 * np.sqrt(sess["v"]))
    prior = sess.get("prior_m")
    w_q, personal = _mix_personal(
        sess, p["X"] @ w, None if prior is None else p["X"] @ prior)
    total = w_q * p["qz"] + personal
    if bonus is not None:
        total = total + bonus                      # 거리는 점수에 섞인다(벽이 아니다)
    # 계층이 있으면 그것이 1순위다. lexsort 는 **마지막 키가 1순위**라 (-total, -tier) 다.
    order = np.argsort(-total) if tier is None else np.lexsort((-total, -tier))
    lc = sess["last_cats"]
    ban = len(lc) >= 2 and lc[-1] is not None and lc[-1] == lc[-2]
    fallback = None
    for i in order:
        sid = p["ids"][int(i)]
        if sid in sess["shown"]:
            continue
        if allow is not None and sid not in allow:
            continue
        if fallback is None:
            fallback = sid
        if ban and p["cat"].get(sid) == lc[-1]:
            continue
        if reasons is not None:
            reasons[sid] = recommendation_reason(
                sess, sid, sampled_weights=w, diverse=sid != fallback)
        if explain is not None:
            explain[sid] = {**contributions(sess, sid, w), "diverse": sid != fallback}
        return sid
    if fallback is not None and reasons is not None:
        reasons[fallback] = recommendation_reason(sess, fallback, sampled_weights=w)
    if fallback is not None and explain is not None:
        explain[fallback] = {**contributions(sess, fallback, w), "diverse": False}
    return fallback


def _learn_state(sess: dict, *, detail: bool = True) -> dict:
    """시각화 패널용: 전체 가중치 + 사후 표준편차 + 카테고리별 실적 + 이력."""
    m = sess["m"]
    sd = np.sqrt(sess["v"])
    stats = sess.get("cat_stats", {})

    def item(name: str, i: int) -> dict:
        return {"name": name, "w": round(float(m[i]), 2),
                "sd": round(float(sd[i]), 2)}

    cats = []
    for j, cname in enumerate(CATS + ["기타"]):
        it = item(cname, 3 + j)
        sh, lk = stats.get(cname, (0, 0))
        it["shown"], it["liked"] = sh, lk
        cats.append(it)
    price = [item(n, 3 + len(CATS) + 1 + j) for j, n in enumerate(PRICE_NAMES)]

    names = (["품질", "리뷰수"] + CATS + ["기타"] + PRICE_NAMES)
    ws = [(n, float(w)) for n, w in zip(names, m[1:]) if abs(w) > 0.05]
    ws.sort(key=lambda t: -abs(t[1]))
    state = {"quality_weight": round(1 / (1 + sess["n"] / 15), 3),
             "top": [{"name": n, "w": round(w, 2)} for n, w in ws[:6]],
             "cats": cats, "price": price,
             "base": [item("품질", 1), item("리뷰수", 2)]}
    # `hist` 는 최대 300줄이라 스와이프마다 수십 KB 가 나간다. 관리자 화면만 받는다
    # (taste.learn_state 의 같은 판단과 짝이다).
    if detail:
        state["hist"] = sess.get("hist", [])[-300:]
    return state


# ─────────────────────────────────────────── 공개 API (목서버 main.py 가 쓴다)
def _mark_served(sess: dict, store_id: str) -> None:
    """보여준 카드로 표시. 다양성 제약은 '보여준 순서'를 기준으로 한다."""
    sess["shown"].add(store_id)
    sess["last_cats"].append(pool()["cat"].get(store_id))


def mark_served(sess: dict, store_id: str) -> None:
    """정책 라우터(`policy.py`)가 **상대 정책이 뽑은 카드**를 알려줄 때 쓴다.

    인터리빙에서 두 정책은 같은 화면을 나눠 채운다. 상대가 뽑은 카드를 모르면 같은
    카드를 또 내보내고, '같은 카테고리 3연속 금지' 같은 규칙도 자기가 뽑은 것만 보게
    되어 실제 화면과 어긋난다.
    """
    _mark_served(sess, store_id)


def session_for(user_id: str) -> dict:
    """user_id 하나에 세션 하나. /swipe 데모와 같은 저장소를 쓴다."""
    _, sess = _session(user_id)
    return sess


def pick_many(sess: dict, k: int, *, reasons: dict[str, str] | None = None,
              allow: set[str] | None = None, tiers: dict | None = None,
              bonus: dict | None = None, explain: dict | None = None) -> list[str]:
    """추천 카드 k장. 노출을 기록하고, 요청하면 선택 시점의 이유도 채운다.

    `allow` 로 후보를 좁히면 k장을 못 채울 수 있다(빈 목록도 정상이다) — 필터가 촘촘하면
    있는 만큼만 주는 것이 맞다. 없는 카드를 채우려고 필터를 무시하지 않는다.

    `tiers`·`bonus` 는 배치 시작에 **한 번만** 배열로 펴 둔다(카드마다 dict 를 훑으면
    2,216번 × k 가 된다).
    """
    p = pool()
    tier_arr = align(p["ids"], tiers)
    bonus_arr = align(p["ids"], bonus)
    out = []
    for _ in range(max(0, k)):
        sid = _pick(sess, reasons=reasons, allow=allow, tier=tier_arr,
                    bonus=bonus_arr, explain=explain)
        if sid is None:
            break
        _mark_served(sess, sid)
        out.append(sid)
    return out


def record(sess: dict, store_id: str, like: bool) -> None:
    """스와이프 결과를 취향 모델에 반영(온라인 로지스틱 1스텝)."""
    p = pool()
    i = p["pos"].get(store_id)
    if i is None:                      # 추천 풀 밖의 가게(사진·리뷰 없음)는 학습에서 뺀다
        return
    from .taste import _forget
    _forget(sess)
    x = p["X"][i]
    prob = 1 / (1 + np.exp(-x @ sess["m"]))
    g = (prob - (1.0 if like else 0.0)) * x
    sess["v"] = 1 / (1 / sess["v"] + prob * (1 - prob) * x * x)
    sess["m"] = sess["m"] - sess["v"] * g
    sess["n"] += 1
    sess["likes"] += 1 if like else 0
    cat = p["cat"].get(store_id)
    key = cat if cat in CATS else "기타"
    cs = sess.setdefault("cat_stats", {}).setdefault(key, [0, 0])
    cs[0] += 1
    cs[1] += 1 if like else 0
    sess.setdefault("hist", []).append(
        {"n": sess["n"], "like": like, "store_id": store_id, "cat": cat,
         "wq": round(1 / (1 + sess["n"] / 15), 3)})


def learn_state(sess: dict, *, detail: bool = True) -> dict:
    return _learn_state(sess, detail=detail)


def peek(sess: dict, k: int) -> list[dict]:
    """다음에 나올 카드 k장을 세션을 건드리지 않고 미리 본다(관리자 화면용).

    Thompson 샘플링 없이 평균 가중치로 점수를 낸다 — '모델이 지금 무엇을 믿는가'를
    보는 화면이므로 탐색 노이즈를 빼야 매번 같은 답이 나온다.
    점수를 품질/개인화로 나눠 돌려주므로 '왜 추천됐는지'를 볼 수 있다.
    """
    p = pool()
    prior = sess.get("prior_m")
    w_q, personal = _mix_personal(
        sess, p["X"] @ sess["m"], None if prior is None else p["X"] @ prior)
    quality = w_q * p["qz"]
    total = quality + personal
    out = []
    for i in np.argsort(-total):
        sid = p["ids"][int(i)]
        if sid in sess["shown"]:
            continue
        out.append({"store_id": sid,
                    "quality": round(float(quality[i]), 3),
                    "personal": round(float(personal[i]), 3),
                    "total": round(float(total[i]), 3)})
        if len(out) >= k:
            break
    return out


def sessions() -> dict:
    """살아 있는 세션 전부. 관리자 화면이 읽는다."""
    return _sessions


def store_category(store_id: str) -> str | None:
    return pool()["cat"].get(store_id)


def in_pool(store_id: str) -> bool:
    return store_id in pool()["pos"]


# ────────────────────────────────────────────────────── 응답 조립
def _card(sid: str) -> dict:
    s = svc.get_store(sid) or {}
    menus = _card_menus(sid)
    rv = None
    for r in svc.get_reviews(sid, limit=3):
        c = (r.get("content") or "").strip()
        if len(c) >= 10:
            rv = {"rating": r.get("rating"), "content": c[:140]}
            break
    return {
        "store_id": sid,
        "name": s.get("name"),
        "cat2": s.get("cat2"), "cat3": s.get("cat3"),
        "address": s.get("address"),
        "kakao_avg_score": s.get("kakao_avg_score"),
        "kakao_review_count": s.get("kakao_review_count") or 0,
        "review_tags": s.get("review_tags") or {},
        "ai_title": s.get("ai_title"),
        "subway": (f"{s['subway_station']} {s.get('subway_exit') or ''}번 출구 "
                   f"{s.get('subway_minutes')}분" if s.get("subway_station") else None),
        "tags": (s.get("tags") or [])[:4],
        "open_headline": s.get("open_headline"),
        "photo_urls": svc.get_photos(sid, limit=5),
        "menus": menus,
        "review": rv,
    }


def _resp(session_id: str, sess: dict) -> dict:
    if sess["pending"] is None:
        sid = _pick(sess)
        if sid is None:
            raise HTTPException(410, "카드를 다 소진했습니다")
        sess["pending"] = sid
        _mark_served(sess, sid)
    return {
        "session_id": session_id,
        "card": _card(sess["pending"]),
        "progress": {"n": sess["n"], "likes": sess["likes"],
                     "remaining": len(pool()["ids"]) - len(sess["shown"])},
        "learn": _learn_state(sess),
    }


# ────────────────────────────────────────────────────── 엔드포인트
class Feedback(BaseModel):
    session_id: str
    store_id: str
    like: bool = Field(description="오른쪽 스와이프 여부")


@router.get("/next", summary="다음 카드 (세션 없으면 생성)")
def next_card(session_id: str | None = None):
    sid, sess = _session(session_id)
    return _resp(sid, sess)


@router.post("/feedback", summary="스와이프 기록 -> 다음 카드")
def feedback(fb: Feedback):
    sid, sess = _session(fb.session_id)
    if sess["pending"] != fb.store_id:
        # 새로고침 등으로 어긋난 경우: 학습 없이 현재 카드를 다시 준다
        return _resp(sid, sess)
    record(sess, fb.store_id, fb.like)
    sess["pending"] = None
    return _resp(sid, sess)
