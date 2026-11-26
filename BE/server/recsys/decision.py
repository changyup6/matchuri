"""스와이프 중 선택 제안. 임계값은 초기 제품 가설이며 만족 확률이 아니다.

학습·난수·노출을 변경하지 않는 결정적 조회다. 최종 후보는 과거 선호/서빙 여부와
무관한 전체 자격 풀이다. 평가한 후보는 자신의 응답을 근거에서 빼고 채점한다.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from itertools import combinations, islice
from math import ceil

import numpy as np

from . import content, diversity, taste, store


@dataclass(frozen=True)
class Config:
    count: int = 4
    min_rated: int = 16
    min_likes: int = 3
    # 좋아요·싫어요 각각의 최소 검증 수. 3 이면 교환 비교가 3×3 = 9쌍이라 8쌍만
    # 맞혀도 AUC 0.8 을 넘는다 — 운으로 넘을 수 있는 크기다. 5 면 25쌍이다.
    min_validation_each: int = 5
    # 0.8 은 합성 사용자(seed 0~7)로 정한 제품 가설이었다. 운영 기록 10,384줄
    # (16장 이상 답한 72명) 재생으로 다시 골랐다 — 후보 0.60/0.65/0.70/0.75/0.80 중 0.70.
    # · 0.80 에서는 `ready` 를 본 사람이 14명, 마지막이 100% 인 사람 2명뿐이었다.
    #   0.70 에서 각각 29명 / 5명이 되고 79점 정체 스텝이 2,066 -> 1,878 로 준다.
    # · 대가는 "덜 맞는 추천" 이다. `ready` 인 동안 답한 카드의 예측 적중률이
    #   70.8%(0.80) -> 66.1%(0.70) 로 내려간다. 그래도 전체 평균 58.3% 보다 높다.
    # · **0.70 을 0.65 위에 놓는 논거는 아직 없다.** 처음에 "완화 끝에서도 실효 문턱
    #   0.525 라 동전(0.5) 위" 라고 적었는데 틀렸다 — `requirements()` 가 progress==1 에서
    #   `quality_required=False` 를 돌려주므로(:70) AUC 관문은 낮아지는 게 아니라 **꺼진다**.
    #   0.525 는 어느 사용자에게도 적용되지 않는 값이다. 운영 기록에서 평가 60장 이상
    #   (관문 OFF)이 72명 중 34명·9,274스텝 중 5,535스텝(59.7%)이다.
    #   "25쌍에서 우연 통과" 논거도 규모를 섞었다 — 완화가 라벨 최소치를 5→2 로 같이 깎아서
    #   평가 50장이면 문턱 0.5833·9쌍·우연통과 35.0% 로, 0.60 을 배척한 34.5% 보다 높다.
    #   그래서 0.70 은 **위 두 줄의 실측(사람 수·적중률)만으로** 고른 값이다. 0.65 재검토는
    #   열려 있다(실측 33명 / 6명 / 적중률 64.7%).
    min_validation_auc: float = 0.70
    stable_top: int = 20
    min_overlap: float = 0.6
    fit_quantile: float = 0.75
    uncertainty_weight: float = 0.5
    max_menu_similarity: float = 0.85
    max_mean_similarity: float = 0.80
    search_limit: int = 60
    relax_after: int = 30
    relax_until: int = 60


DEFAULT = Config()
MAX_COUNT = 20  # API response/pair-work bound, not a preference threshold.
PERSONAL = [content.IDX[c] for c in ("cf", "menu", "text", "review", "food", "neg")]
_category_cache = (None, {})


def relaxation(sess, config=DEFAULT):
    """Fatigue follows distinct valid feedback, never repeated taps on one card."""
    count = min(sess.get("n", 0), len(set(sess["like_ids"] + sess["dislike_ids"])))
    return float(np.clip((count - config.relax_after) /
                         max(1, config.relax_until - config.relax_after), 0, 1))


def requirements(sess, config=DEFAULT):
    progress = relaxation(sess, config)
    if progress == 0:
        return config, True
    # Gradually ease prediction evidence; at the long-session limit these are
    # diagnostics only. Candidate fit, diversity and user constraints stay exact.
    active = replace(config,
        min_validation_each=max(1, ceil(config.min_validation_each * (1 - 2 * progress / 3) - 1e-12)),
        min_validation_auc=round(config.min_validation_auc * (1 - .25 * progress), 10),
        min_overlap=round(config.min_overlap * (1 - .5 * progress), 10))
    return active, progress < 1


def _fine_groups(art):
    """현재 산출물 풀별 세부 업종. 일치 여부를 설명하는 진단 정보다."""
    global _category_cache
    if _category_cache[0] is not art:
        groups = {sid: (store.get_store(sid) or {}).get("cat3") or "" for sid in art.ids}
        _category_cache = (art, groups)
    return _category_cache[1]


# ── 성분 z 캐시 ────────────────────────────────────────────────────────────────
# `loo=True` 는 답한 가게마다 '그 가게를 뺀' 성분을 풀 전체로 다시 낸다(답 100장이면 components 100번).
# 실서비스(2026-09-23)에서 판단 수치 요약 한 번이 2.4~2.8초였고 그중 90%가 이것이었다 — 같은 요청 안에서도
# evaluate 와 decision_metrics.export 가 같은 세션으로 한 번씩 두 번 돌았고, 위치 변경(스와이프만큼 잦다)마다
# 학습 상태가 그대로인데도 처음부터 다시 돌았다. z 는 학습 상태(좋아요·싫어요·n·근거 시점·기억)와 산출물로만
# 정해진다(m·v 와 무관 — taste.components 문서). 그래서 그 둘을 열쇠로 결과를 기억해 둔다. 답은 같다.
_Z_CACHE_MAX = 128                 # z 한 벌 = 성분 8 × 풀(1,498) ≈ 96KB — 128벌이면 12MB
_z_lock = threading.Lock()
_z_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_z_owner: list = [None]            # (산출물, 성분 함수, 최근성 상수) — 하나라도 바뀌면 비운다(테스트가 갈아끼운다)


def _z_key(state, loo):
    return (bool(loo), tuple(state.get("like_ids") or ()), tuple(state.get("dislike_ids") or ()),
            int(state.get("n") or 0), tuple(sorted((state.get("evidence_at") or {}).items())),
            tuple((row["store_id"], bool(row["like"]), float(row["weight"])) for row in state.get("memory") or ()))


def _compute_components(art, state, *, loo=False):
    liked, disliked, rated = taste._idx(state, art)
    z = taste.components(state, art)
    if loo:
        # 유사도 집계에서만 자기 반응 제외. m/v의 완전한 LOO 재학습은 아니다.
        for i in rated:
            z[:, i] = taste.components(state, art, {art.ids[i]})[:, i]
    return z


def _components(art, state, *, loo=False):
    owner = (art, taste.components, taste.RECENCY_DECAY, taste.MEMORY_SHARE)
    key = _z_key(state, loo)
    with _z_lock:
        if _z_owner[0] is None or any(a is not b for a, b in zip(_z_owner[0], owner)):
            _z_cache.clear()
            _z_owner[0] = owner
        hit = _z_cache.get(key)
        if hit is not None:
            _z_cache.move_to_end(key)
            return hit.copy()
    z = _compute_components(art, state, loo=loo)
    with _z_lock:
        if _z_owner[0] is not None and all(a is b for a, b in zip(_z_owner[0], owner)):
            _z_cache[key] = z.copy()
            while len(_z_cache) > _Z_CACHE_MAX:
                _z_cache.popitem(last=False)
    return z


def _scores(art, state, *, loo=False, z=None):
    if z is None:
        z = _components(art, state, loo=loo)
    score = state["m"] @ z
    personal = state["m"][PERSONAL] @ z[PERSONAL]
    uncertainty = np.sqrt((state["v"][PERSONAL, None] * z[PERSONAL] ** 2).sum(axis=0))
    return score, personal, uncertainty


def _diverse(art, a, b, config):
    if art.brand[a] and art.brand[a] == art.brand[b]:
        return False
    # Category labels are diagnostics, not a substitute for meal similarity.
    # Two restaurants can offer different options within the same cuisine.
    menu = float(art.sim_menu[a, b])
    if np.isfinite(menu) and menu >= config.max_menu_similarity:
        return False
    values = [float(m[a, b]) for m in (art.sim_menu, art.sim_text,
                                      art.sim_review, art.sim_food)]
    known = [v for v in values if np.isfinite(v)]
    # 결측을 '다르다'는 증거로 취급하지 않는다.
    return len(known) >= 2 and np.mean(known) < config.max_mean_similarity


def _refine(art, ranked, chosen, config, declined=(), *, tiers=None, strict=True):
    """Improve the worst overlap inside the existing relevance search window.

    Preserve the best feasible choice and the chosen tier composition. No new
    relevance/diversity mixing weight or acceptance threshold is introduced.
    This is bounded local improvement, not a claim of global slate optimality.
    Missing evidence never makes a pair an attractive 'different' alternative.
    """
    if len(chosen) < 2:
        return chosen
    window = list(dict.fromkeys([*ranked[:config.search_limit], *chosen]))
    position = {index: rank for rank, index in enumerate(ranked)}
    local = {index: rank for rank, index in enumerate(window)}
    indices = np.asarray(window, dtype=int)
    redundancy = diversity.redundancy_matrix(art)[np.ix_(indices, indices)]
    unknown = ~np.isfinite(redundancy)
    redundancy = np.where(unknown, 1., redundancy)
    missing = sum(~np.isfinite(matrix[np.ix_(indices, indices)])
                  for matrix in diversity.matrices(art).values())
    size = len(window)
    conflict = np.zeros((size, size), dtype=bool)
    brand = np.zeros_like(conflict)
    for a, i in enumerate(window):
        for b in range(a + 1, size):
            j = window[b]
            conflict[a, b] = conflict[b, a] = not _diverse(art, i, j, config)
            brand[a, b] = brand[b, a] = bool(art.brand[i] and art.brand[i] == art.brand[j])
    rejected = {frozenset(ids) for ids in declined if len(ids) == config.count}
    pair_a, pair_b = np.triu_indices(len(chosen), 1)

    def objective(selection):
        loc = np.asarray([local[i] for i in selection])
        a, b = loc[pair_a], loc[pair_b]
        costs = (int(conflict[a, b].sum()), int(brand[a, b].sum()),
                 int(unknown[a, b].sum()))
        return (*costs, tuple(sorted(redundancy[a, b], reverse=True)),
                tuple(sorted(position[i] for i in selection)))

    def missing_count(selection):
        loc = np.asarray([local[i] for i in selection])
        return int(missing[loc[pair_a], loc[pair_b]].sum())

    chosen = list(chosen)
    current = objective(chosen)
    remaining_work = config.search_limit ** 2
    # At most n exchanges over at most search_limit+n candidates: bounded also
    # for manual n=20 and homogeneous/filter-limited catalogues.
    for _ in range(config.count):
        best, best_value = None, current
        members = set(chosen)
        max_missing = missing_count(chosen)
        for slot in range(1, len(chosen)):  # keep the first feasible ranked anchor
            old_tier = (tiers or {}).get(art.ids[chosen[slot]], 0)
            for candidate in window:
                if candidate in members or (tiers or {}).get(art.ids[candidate], 0) != old_tier:
                    continue
                if remaining_work <= 0:
                    break
                remaining_work -= 1
                proposal = chosen.copy()
                proposal[slot] = candidate
                if missing_count(proposal) > max_missing:
                    continue
                if frozenset(art.ids[i] for i in proposal) in rejected:
                    continue
                value = objective(proposal)
                if strict and value[0]:
                    continue
                if value < best_value:
                    best, best_value = proposal, value
            if remaining_work <= 0:
                break
        if best is None:
            break
        chosen, current = best, best_value
        if remaining_work <= 0:
            break
    return sorted(chosen, key=position.__getitem__)


def _select(art, ranked, config, declined=(), *, max_nodes=None, tiers=None):
    """상위 search_limit곳에서 적합 조합을 찾고, 가장 큰 쌍 중복부터 줄인다."""
    ranked = ranked[:config.search_limit]
    if len(ranked) < config.count:
        return []
    declined = {frozenset(ids) for ids in declined if len(ids) == config.count}
    def usable(chosen):
        return not declined or frozenset(art.ids[i] for i in chosen) not in declined
    if config.count == 1:
        return next(([i] for i in ranked if usable([i])), [])
    compatible = [0] * len(ranked)
    neighbors = [0] * len(ranked)
    for a, i in enumerate(ranked):
        for b in range(a + 1, len(ranked)):
            if _diverse(art, i, ranked[b], config):
                compatible[a] |= 1 << b
                neighbors[a] |= 1 << b
                neighbors[b] |= 1 << a

    def enough_colors(remaining, needed):
        # A proper coloring bounds clique size. Prune only impossible branches;
        # never change the first feasible result or relax suitability rules.
        colors = 0
        while remaining:
            colors += 1
            if colors >= needed:
                return True
            available = remaining
            while available:
                bit = available & -available
                remaining ^= bit
                available &= ~(bit | neighbors[bit.bit_length() - 1])
        return False

    visited = 0

    def search(chosen, remaining):
        nonlocal visited
        if max_nodes is not None and visited >= max_nodes:
            return []
        visited += 1
        if len(chosen) == config.count:
            return chosen if usable(chosen) else []
        if not enough_colors(remaining, config.count - len(chosen)):
            return []
        while remaining.bit_count() >= config.count - len(chosen):
            bit = remaining & -remaining
            remaining ^= bit
            i = bit.bit_length() - 1
            result = search(chosen + [ranked[i]], remaining & compatible[i])
            if result:
                return result
        return []

    chosen = search([], (1 << len(ranked)) - 1)
    return _refine(art, ranked, chosen, config, declined, tiers=tiers) if chosen else []


def evaluate(sess, *, allow=None, tiers=None, bonus=None, config=DEFAULT):
    """정확히 count곳 또는 빈 목록. 20~30회는 목표이며 강제 종료 조건은 아니다."""
    config, quality_required = requirements(sess, config)
    art = content.load()
    likes = set(sess["like_ids"])
    dislikes = set(sess["dislike_ids"])
    rated = likes | dislikes
    out = {"ready": False, "status": "learning", "store_ids": [],
           "rated_count": len(rated), "liked_count": len(likes),
           "stability": None, "validation_auc": None, "qualified_count": 0}
    if art is None:
        return {**out, "status": "unavailable"}
    if len(rated) < config.min_rated or len(likes) < config.min_likes:
        return out
    validation = [r for r in sess.get("hist", []) if r.get("personal_rank") is not None][-30:]
    positive = [r["personal_rank"] for r in validation if r["like"]]
    negative = [r["personal_rank"] for r in validation if not r["like"]]
    if quality_required and min(len(positive), len(negative)) < config.min_validation_each:
        return out
    delta = np.subtract.outer(positive, negative)
    auc = float(np.mean((delta > 0) + 0.5 * (delta == 0))) if delta.size else None
    out["validation_auc"] = round(auc, 4) if auc is not None else None
    if quality_required and (auc is None or auc < config.min_validation_auc):
        return out
    checkpoints = sess.get("decision_checkpoints", [])
    if quality_required and len(checkpoints) < 4:
        return out
    candidate = [i for i, sid in enumerate(art.ids) if allow is None or sid in allow]
    if len(candidate) < config.count:
        return {**out, "status": "insufficient_candidates"}

    # 동일한 미평가 풀에서 n, n-1, n-3의 순위를 비교한다. 현재 필터가 만든
    # 작은 후보 집합의 우연한 일치를 개인화 안정성으로 오인하지 않는다.
    probe = [i for i, sid in enumerate(art.ids) if sid not in rated]
    if quality_required and len(probe) < config.stable_top:
        return out
    _, personal, uncertainty = _scores(art, sess, loo=True)
    def top(scores):
        return set(sorted(probe, key=lambda i: (-scores[i], art.ids[i]))[:config.stable_top])
    overlaps = []
    if len(checkpoints) >= 4 and len(probe) >= config.stable_top:
        now = top(personal)
        overlaps = [len(now & top(_scores(art, checkpoints[j])[1])) / len(now)
                    for j in (-2, -4)]
    out["stability"] = round(min(overlaps), 4) if overlaps else None
    if quality_required and (not overlaps or min(overlaps) < config.min_overlap):
        return out

    conservative = personal - config.uncertainty_weight * uncertainty
    # 점수는 전체 추천 풀 기준으로 비교한다. 필터 안에서 4위라는 이유만으로
    # 낮은 적합도의 가게가 자격을 얻지 않는다.
    reference = probe or list(range(len(art.ids)))
    threshold = float(np.quantile(conservative[reference], config.fit_quantile))
    qualified = [i for i in candidate if np.isfinite(conservative[i])
                 and conservative[i] >= threshold and personal[i] > 0]
    out["qualified_count"] = len(qualified)
    ranked = sorted(qualified, key=lambda i: (
        -(tiers or {}).get(art.ids[i], 0),
        -(conservative[i] + (bonus or {}).get(art.ids[i], 0)), art.ids[i]))
    chosen = _select(art, ranked, config, sess.get('declined_slates', ()), tiers=tiers)
    if len(chosen) != config.count:
        return {**out, "status": "insufficient_candidates"}
    return {**out, "ready": True, "status": "ready",
            "store_ids": [art.ids[i] for i in chosen]}


def _soft_select(art, ranked, config, declined=(), *, tiers=None):
    """Bounded fallback: prefer distinct choices without withholding a request.

    Pair conflicts, repeated brands and measured similarity are
    lexicographic preferences, not new weights added to the learned model.
    Compatible choices retain the existing score order. Missing modalities
    are never rewarded as proof that two restaurants differ.
    """
    if len(ranked) < config.count:
        return []
    indices = np.asarray(ranked, dtype=int)
    brands = np.asarray([art.brand[i] for i in ranked], dtype=object)
    conflicts = np.zeros(len(ranked), dtype=int)
    brand_repeats = np.zeros(len(ranked), dtype=int)
    missing_evidence = np.zeros(len(ranked), dtype=int)
    similarity = np.full(len(ranked), -np.inf)
    relative = diversity.redundancy_matrix(art)
    remaining = set(range(len(ranked)))
    chosen = []
    alternatives = []
    for _ in range(config.count):
        def priority(j):
            return (conflicts[j], brand_repeats[j], missing_evidence[j],
                    similarity[j] if conflicts[j] else 0, j)
        order = sorted(remaining, key=priority)
        j = order[0]
        chosen.append(j)
        remaining.remove(j)
        alternatives = order[1:]
        if len(chosen) == config.count:
            break
        i = ranked[j]
        values = np.stack([matrix[i, indices] for matrix in
                           (art.sim_menu, art.sim_text, art.sim_review, art.sim_food)])
        known = np.isfinite(values)
        count = known.sum(axis=0)
        mean = np.divide(np.where(known, values, 0).sum(axis=0), count,
                         out=np.ones(len(ranked)), where=count > 0)
        same_brand = (brands == art.brand[i]) & bool(art.brand[i])
        compatible = (~same_brand &
                      ~(values[0] >= config.max_menu_similarity) &
                      (count >= 2) & (mean < config.max_mean_similarity))
        conflicts += ~compatible
        brand_repeats += same_brand
        missing_evidence += len(diversity.MODALITIES) - count
        measured = relative[i, indices]
        similarity = np.maximum(similarity, np.where(np.isfinite(measured), measured, 1.0))

    rejected = {frozenset(ids) for ids in declined if len(ids) == config.count}
    # The API retains at most 100 proposals. With D rejected sets, inspecting
    # D+1 different combinations guarantees a new set whenever one exists.
    # The first alternatives preserve the selected prefix and its diversity.
    order = chosen + alternatives
    for choice in islice(combinations(order, config.count), len(rejected) + 1):
        result = [ranked[j] for j in choice]
        if frozenset(art.ids[i] for i in result) not in rejected:
            return _refine(art, ranked, result, config, declined, tiers=tiers, strict=False)
    return []


def recommend(sess, *, allow=None, tiers=None, bonus=None, config=DEFAULT):
    """Explicit decision request, independent of automatic suggestion gates.

    Reuse the learned high-dimensional fit, uncertainty and distance ranking.
    Prefer an originally qualified, diverse slate; if evidence or diversity is
    insufficient, return the best available eligible choices. Filters and
    rejected combinations remain hard constraints. No learning/exposure occurs.
    """
    art = content.load()
    likes = set(sess["like_ids"])
    rated = likes | set(sess["dislike_ids"])
    out = {"ready": False, "status": "insufficient_candidates", "store_ids": [],
           "rated_count": len(rated), "liked_count": len(likes),
           "stability": None, "validation_auc": None, "qualified_count": 0}
    if art is None:
        return {**out, "status": "unavailable"}
    candidate = [i for i, sid in enumerate(art.ids) if allow is None or sid in allow]
    if len(candidate) < config.count:
        return out
    z = _components(art, sess, loo=True)
    _, personal, uncertainty = _scores(art, sess, z=z)
    conservative = personal - config.uncertainty_weight * uncertainty
    evidence = any(sid in art.pos for sid in rated) or any(
        row['store_id'] in art.pos and row.get('weight', 0) > 0
        for row in sess.get('memory', ()))
    ranking = conservative if evidence else taste._cold(z, sess['m'], False)
    # A corrupt/non-finite model value must not make a user filter disappear.
    ranking = np.nan_to_num(ranking, nan=-np.inf, neginf=-np.inf, posinf=-np.inf)
    def order(indices):
        return sorted(indices, key=lambda i: (
            -(tiers or {}).get(art.ids[i], 0),
            -(ranking[i] + (bonus or {}).get(art.ids[i], 0)), art.ids[i]))
    reference = [i for i, sid in enumerate(art.ids) if sid not in rated]
    reference = reference or list(range(len(art.ids)))
    finite = conservative[reference][np.isfinite(conservative[reference])]
    threshold = float(np.quantile(finite, config.fit_quantile)) if len(finite) else np.inf
    qualified = [i for i in candidate if evidence and np.isfinite(conservative[i])
                 and conservative[i] >= threshold and personal[i] > 0]
    out['qualified_count'] = len(qualified)
    declined = sess.get('declined_slates', ())
    # Reuse the original exact selector, with a manual-only work budget before
    # a bounded full-catalogue greedy pass. The automatic path remains unchanged.
    budget = config.search_limit ** 2
    chosen = _select(art, order(qualified), config, declined, max_nodes=budget, tiers=tiers)
    ranked = order(candidate)
    if not chosen:
        chosen = _select(art, ranked, config, declined, max_nodes=budget, tiers=tiers)
    if not chosen:
        chosen = _soft_select(art, ranked, config, declined, tiers=tiers)
    if len(chosen) != config.count:
        return out
    return {**out, 'ready': True, 'status': 'ready',
            'store_ids': [art.ids[i] for i in chosen]}
