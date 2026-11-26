# -*- coding: utf-8 -*-
"""구성요소 블렌드 (Step 2) — 여러 modality 의 점수를 **가중치 몇 개**로 섞는다.

PLAN 3절의 설계 원칙 3번("온라인 학습은 1,024차원 위에서 하지 않는다. 사용자별로
학습하는 것은 구성요소 점수의 가중치 몇 개뿐이다")을 오프라인에서 먼저 재는 자리다.
여기서 λ 가 몇 개면 충분한지, 어떤 modality 가 실제로 기여하는지를 확인하고 나서야
Step 4 의 온라인 학습(사용자별 w)을 이야기할 수 있다.

    score(i) = z_pop(i) + Σ_m λ_m · z_m(i)

  * **z 는 그 사용자의 후보 안에서** 낸다(이미 간 가게를 뺀 나머지). 눈금이 modality
    마다 다르고(코사인 0.5 대 공방문 0.02) 사용자마다 다르기 때문에, 정규화 없이
    더하면 큰 눈금을 가진 하나가 전부를 결정한다.
  * **NaN → z 정규화 뒤 0.** 0 은 그 사용자 후보의 평균이라는 뜻이고, 그것이 '모른다'
    의 올바른 번역이다(Step 1 의 NaN 규칙과 같은 정신).
  * `popularity` 의 가중치는 **1 로 고정**한다. λ 는 '인기 위에 취향을 얼마나 얹을까'
    를 뜻하고, 그래야 Step 1·1.5 의 `hybrid_pop_*` 와 같은 눈금으로 읽힌다.

λ 를 고르는 방법 두 가지를 **둘 다** 돌린다.

  (a) `fit_grid` — 격자 {0, 0.25, 0.5, 1, 2} 위의 **탐욕적 전진 선택**. 학습 폴드의
      NDCG@20 을 기준으로 가장 많이 오르는 (modality, λ) 를 하나씩 붙이고, 더 오르지
      않으면 멈춘다. 전 조합 탐색(5^4 = 625)을 피하면서 '무엇이 기여하는가' 의 순서를
      그대로 남긴다.
  (b) `fit_pairwise` — BPR 식 쌍대 로지스틱. 학습 폴드의 사용자마다 (양성 = 미래 방문,
      음성 = 씨앗 고정 무작위 20곳) 쌍을 만들어 σ(w·(z⁺ − z⁻)) 를 최대화한다. 절편은
      없다(쌍의 차이라서 상쇄된다). L2 정규화 계수는 학습 폴드 안에서 고른다.
      여기서는 `popularity` 의 가중치도 **학습한다** — 순위는 w 를 상수배해도 그대로라
      비교할 때는 w_pop 으로 나눠 λ 로 환산한다.

**바깥 5-fold.** 폴드 f 를 뺀 사용자로 가중치를 학습하고 폴드 f 만 평가한다
(`analysis.backtest.cv.outer_cv` 와 같은 구조). 사용자마다 자기가 학습에 참여하지
않은 모델의 점수가 남으므로 다른 모델과 그대로 paired bootstrap 에 넣을 수 있다.

**성분 간 상관** (Step 3a, PLAN 7절 결정 5번). `review` 가 가장 큰 λ 를 받고도
ablation 에서 죽은 것은 성분들이 서로 겹친다는 신호였다. `component_correlations` 가
같은 통(`ComponentBank`)에서 성분들의 스피어만 상관을 내므로, 어느 성분이 다른
성분의 다른 이름인지를 지표를 돌리기 전에 볼 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import rankdata

from analysis.backtest import config, metrics
from analysis.backtest.cv import user_folds
from analysis.backtest.evaluate import TaskResult, _tiebreak, evaluate
from analysis.backtest.rng import generator
from analysis.backtest.scorers import History, ScoreContext, Scorer

#: λ 격자 (PLAN 5절의 λ 격자와 같은 뜻이되 0 을 넣는다 — '이 modality 를 안 쓴다').
GRID = (0.0, 0.25, 0.5, 1.0, 2.0)

#: 쌍대 학습에서 양성 하나당 뽑는 음성 수.
N_NEGATIVES = 20

#: L2 계수 후보. 학습 폴드 안에서 NDCG@20 으로 고른다. 위아래로 여유를 둬서
#: 고른 값이 격자 **경계**에 걸리지 않는지 확인할 수 있게 한다.
L2_GRID = (0.001, 0.01, 0.1, 1.0, 10.0)

#: 블렌드의 기준 구성요소 — 가중치 1 로 고정된다.
BASE_COMPONENT = "popularity"


def zscore_masked(v: np.ndarray, mask: np.ndarray,
                  clip: float | None = None) -> np.ndarray:
    """후보 마스크 안의 평균·표준편차로 z 정규화하고 NaN 을 0 으로 만든다.

    `scorers_menu.zscore_on` 과 같은 규칙이되 NaN 을 견딘다. 표준편차가 0 이면(상수
    점수) 전부 0 이 된다 — 그 modality 는 이 사용자에게 아무 말도 하지 않는다는 뜻이다.

    `clip` 을 주면 z 를 [−clip, clip] 으로 자른다. **기본은 끔** — Step 1~3c 의 결과가
    그대로 재현돼야 하기 때문이다. 서버는 이것을 켠 채 돌고(`server/recsys/content.py`
    의 `Z_CLIP`), 그 차이가 오프라인 λ 를 흔드는지는 Step 3d 가 잰다.
    """
    v = np.asarray(v, dtype=np.float64)
    sub = v[mask]
    finite = np.isfinite(sub)
    if not finite.any():
        return np.zeros_like(v)
    mu = float(sub[finite].mean())
    sd = float(sub[finite].std())
    out = (v - mu) / (sd if sd > 0 else 1.0)
    out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    return out if clip is None else np.clip(out, -float(clip), float(clip))


@dataclass
class UserRow:
    """한 사용자의 구성요소 z 점수와 채점에 필요한 것들."""
    user_id: str
    z: np.ndarray                  # (C, S) 풀 전체 (마스크 밖도 같은 눈금)
    cand: np.ndarray               # (S,) bool — 아직 안 가본 후보
    zc: np.ndarray                 # (C, n_cand) 후보만 잘라 둔 것 (학습용)
    labels: np.ndarray             # (n_cand,) bool — 미래 방문
    tiebreak: np.ndarray           # (n_cand,) 동점 처리용 순열
    n_pos: int


@dataclass
class ComponentBank:
    """(구성요소 × 사용자) z 점수를 한 번만 계산해 들고 있는 통.

    블렌드 탐색은 가중치를 수백 번 바꿔 가며 점수를 다시 매기는 일이라, scorer 를
    매번 부르면 같은 유사도 집계를 수백 번 반복하게 된다. 여기서 한 번 계산해 두면
    이후는 (C × S) 행렬 하나에 가중치를 곱하는 일이 전부다.
    """
    names: list[str]                       # 구성요소 이름 (0번이 BASE_COMPONENT)
    n_history: int
    clip: float | None = None              # z 를 자른 폭 (None 이면 안 잘랐다)
    rows: dict[str, UserRow] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)   # splits 순서 그대로

    @property
    def n_components(self) -> int:
        return len(self.names)

    @classmethod
    def build(cls, component_models: dict[str, str], splits, ctx: ScoreContext,
              n_history: int, scorer_of=None,
              clip: float | None = None) -> "ComponentBank":
        """`{구성요소 이름: scorer 이름}` 을 받아 사용자별 z 점수를 만든다.

        `clip` 은 `zscore_masked` 로 그대로 넘어간다(기본 None = 안 자른다).
        """
        from analysis.backtest import scorers as _scorers
        get = scorer_of or _scorers.get
        names = list(component_models)
        if names[0] != BASE_COMPONENT:
            raise ValueError(f"첫 구성요소는 `{BASE_COMPONENT}` 여야 한다 "
                             f"(가중치 1 로 고정되는 자리다)")
        made = {}
        for comp, model in component_models.items():
            s = get(model)
            s.prepare(ctx)
            made[comp] = s

        pool = ctx.pool
        n_pool = len(pool)
        bank = cls(names=names, n_history=n_history, clip=clip)
        for sp in splits:
            signals = sp.signals(n_history, ctx.history_mode)
            seen = np.zeros(n_pool, dtype=bool)
            for sid in sp.seen_stores:
                seen[pool.pos[sid]] = True
            cand = ~seen
            labels = np.zeros(n_pool, dtype=bool)
            for sid, _ in sp.future:
                labels[pool.pos[sid]] = True

            z = np.zeros((len(names), n_pool), dtype=np.float64)
            for c, comp in enumerate(names):
                raw = np.asarray(made[comp].score(signals, ctx, user_id=sp.user_id),
                                 dtype=float)
                z[c] = zscore_masked(raw, cand, clip=clip)
            tb = _tiebreak(sp.user_id, n_pool, ctx.seed)[cand]
            bank.rows[sp.user_id] = UserRow(
                user_id=sp.user_id, z=z, cand=cand, zc=z[:, cand],
                labels=labels[cand], tiebreak=tb, n_pos=int(labels[cand].sum()))
            bank.order.append(sp.user_id)
        return bank

    # ── 점수
    def weight_vector(self, weights: dict[str, float]) -> np.ndarray:
        """`{구성요소: λ}` -> 길이 C 벡터. 기준 구성요소는 1 로 고정된다."""
        w = np.array([float(weights.get(n, 0.0)) for n in self.names])
        w[0] = float(weights.get(self.names[0], 1.0))
        return w

    def score(self, user_id: str, w: np.ndarray) -> np.ndarray:
        return w @ self.rows[user_id].z

    def ndcg(self, user_ids, w: np.ndarray, k: int = config.NDCG_K) -> float:
        """학습 폴드의 평균 NDCG@k — 격자 탐색과 L2 선택의 잣대.

        `evaluate` 를 부르지 않는 것은 속도 때문이다. 순서·동점 처리·정답 정의는
        `evaluate` 와 **같은 코드**(`metrics.rank_order`, `metrics.ndcg_at_k`)를 쓴다.
        """
        vals = []
        for uid in user_ids:
            r = self.rows[uid]
            if r.n_pos == 0:
                continue
            order = metrics.rank_order(w @ r.zc, r.tiebreak)
            vals.append(metrics.ndcg_at_k(order, r.labels, k))
        return float(np.mean(vals)) if vals else float("nan")


class BlendScorer(Scorer):
    """통에 든 z 점수를 가중합한다. 무거운 계산은 이미 끝나 있다."""

    def __init__(self, bank: ComponentBank, weights: dict[str, float],
                 name: str = "blend") -> None:
        self.bank, self.weights, self.name = bank, dict(weights), name
        self.w = bank.weight_vector(weights)
        self.description = "블렌드 " + " + ".join(
            f"{w:g}·z({n})" for n, w in zip(bank.names, self.w) if w)

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        return self.bank.score(user_id, self.w)


# ────────────────────────────────────────────────── (a) 격자 · 탐욕적 전진 선택
def fit_grid(bank: ComponentBank, train_ids: list[str], *, grid=GRID,
             metric_k: int = config.NDCG_K, min_gain: float = 0.0) -> dict:
    """학습 사용자에서 λ 를 고른다 — 가장 많이 올리는 modality 를 하나씩 붙인다.

    반환에는 **선택 경로**(어떤 순서로 무엇이 얼마나 올렸는지)를 남긴다. 최종 λ 만
    적으면 '왜 이 modality 가 빠졌나' 를 나중에 알 수 없다.
    """
    modalities = [n for n in bank.names[1:]]
    weights = {bank.names[0]: 1.0} | {m: 0.0 for m in modalities}
    base = bank.ndcg(train_ids, bank.weight_vector(weights), metric_k)
    trace = [{"step": 0, "added": None, "lam": None, "ndcg": base}]

    remaining = list(modalities)
    while remaining:
        best = None
        for m in remaining:
            for lam in grid:
                if lam == 0:
                    continue
                trial = dict(weights)
                trial[m] = lam
                v = bank.ndcg(train_ids, bank.weight_vector(trial), metric_k)
                if best is None or v > best[0]:
                    best = (v, m, lam)
        if best is None or best[0] <= base + min_gain:
            break
        base, m, lam = best
        weights[m] = lam
        remaining.remove(m)
        trace.append({"step": len(trace), "added": m, "lam": lam, "ndcg": base})
    return {"weights": weights, "train_ndcg": base, "trace": trace}


# ────────────────────────────────────────────────── (b) 쌍대 로지스틱 (BPR)
def pair_features(bank: ComponentBank, user_ids: list[str], *, seed: int,
                  n_neg: int = N_NEGATIVES) -> np.ndarray:
    """(양성 − 음성) 특성 차이 행렬 (P, C).

    음성은 그 사용자의 후보 중 정답이 아닌 곳에서 **씨앗 고정** 무작위로 뽑는다.
    사용자·양성마다 다른 난수를 쓰되 seed 가 같으면 항상 같은 표본이 나온다.
    """
    out = []
    for uid in user_ids:
        r = bank.rows[uid]
        if r.n_pos == 0:
            continue
        neg_pool = np.flatnonzero(~r.labels)
        if neg_pool.size == 0:
            continue
        pos_idx = np.flatnonzero(r.labels)
        rng = generator(seed, "blend-pairs", uid)
        take = min(n_neg, neg_pool.size)
        for p in pos_idx:
            neg = rng.choice(neg_pool, size=take, replace=False)
            out.append(r.zc[:, p][None, :] - r.zc[:, neg].T)
    if not out:
        return np.zeros((0, bank.n_components))
    return np.concatenate(out, axis=0)


def logistic_pairwise(X: np.ndarray, alpha: float, *, iters: int = 50,
                      tol: float = 1e-10) -> np.ndarray:
    """min_w  mean log(1 + exp(−X w)) + (α/2)‖w‖²  — 절편 없음.

    볼록 문제이고 특성이 5개뿐이라 **뉴턴법**으로 푼다(헤시안이 5×5). 경사하강과 달리
    학습률을 고를 필요가 없고 10회 안쪽에서 경사가 0 으로 떨어진다 — '수렴을 덜 해서
    가중치가 작게 나왔다' 는 의심을 없애기 위해서다. α > 0 이면 헤시안이 양정치라
    항상 풀린다.

        f(w)  = mean log(1+exp(−m)) + (α/2)‖w‖²,        m = Xw
        ∇f    = −mean(σ(−m)·x) + αw
        ∇²f   = mean(σ(m)σ(−m)·xxᵀ) + αI
    """
    n, d = X.shape
    w = np.zeros(d)
    if n == 0:
        return w
    for _ in range(iters):
        m = np.clip(X @ w, -60, 60)
        s_neg = 1.0 / (1.0 + np.exp(m))          # σ(−m)
        grad = -(X * s_neg[:, None]).mean(axis=0) + alpha * w
        if float(np.abs(grad).max()) < tol:
            break
        r = s_neg * (1.0 - s_neg)                # σ(m)·σ(−m)
        hess = (X * r[:, None]).T @ X / n + alpha * np.eye(d)
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:            # 특성이 완전히 겹치는 극단
            step = grad
        w -= step
    return w


def fit_pairwise(bank: ComponentBank, train_ids: list[str], *, seed: int,
                 l2_grid=L2_GRID, n_neg: int = N_NEGATIVES,
                 metric_k: int = config.NDCG_K) -> dict:
    """쌍대 로지스틱으로 w 를 학습한다. L2 계수는 학습 폴드의 NDCG@20 으로 고른다."""
    X = pair_features(bank, train_ids, seed=seed, n_neg=n_neg)
    best = None
    per_alpha = []
    for alpha in l2_grid:
        w = logistic_pairwise(X, alpha)
        v = bank.ndcg(train_ids, w, metric_k)
        per_alpha.append({"alpha": alpha, "w": w.tolist(), "train_ndcg": v})
        if best is None or v > best["train_ndcg"]:
            best = {"alpha": alpha, "w": w, "train_ndcg": v}

    w = best["w"]
    base = float(w[0])
    # 순위는 상수배에 불변하므로 w_pop 으로 나눠 격자의 λ 와 같은 눈금으로 읽는다.
    lam = ({n: float(v / base) for n, v in zip(bank.names, w)} if base > 0
           else {n: float(v) for n, v in zip(bank.names, w)})
    return {"weights_raw": {n: float(v) for n, v in zip(bank.names, w)},
            "weights": lam, "alpha": best["alpha"],
            "train_ndcg": best["train_ndcg"], "n_pairs": int(X.shape[0]),
            "normalised": base > 0, "per_alpha": per_alpha}


# ────────────────────────────────────────────────── 바깥 5-fold
def outer_cv_blend(bank: ComponentBank, splits, ctx: ScoreContext, *, fitter: str,
                   seed: int | None = None, k: int = 5, name: str = "blend",
                   components: list[str] | None = None) -> tuple[TaskResult, list[dict]]:
    """폴드 밖에서 가중치를 학습하고 그 폴드만 평가한다.

    `components` 를 주면 그 구성요소만 쓴다(ablation — 뺀 채로 **다시 학습한다**.
    가중치를 그대로 두고 한 항만 0 으로 만드는 것은 '없앴을 때 얼마나 나쁜가' 가
    아니라 '잘못 맞춘 모델이 얼마나 나쁜가' 를 재는 것이다).
    """
    seed = config.SEED if seed is None else seed
    folds = user_folds(splits, k=k, seed=seed)
    n = len(splits)
    use = list(components or bank.names)
    sub = subset_bank(bank, use)          # 폴드마다 다시 만들 필요가 없다
    p_arrays: dict[str, np.ndarray] = {}
    r_arr = np.full(n, np.nan)
    full = np.zeros(n, dtype=bool)
    fits: list[dict] = []

    for f in folds:
        held = {int(i) for i in f}
        train_ids = [splits[i].user_id for i in range(n) if i not in held]
        if fitter == "grid":
            fit = fit_grid(sub, train_ids)
        elif fitter == "pairwise":
            fit = fit_pairwise(sub, train_ids, seed=seed)
        else:
            raise ValueError(f"모르는 학습 방법 '{fitter}' — grid|pairwise")
        fits.append({"components": list(use)} | fit)
        scorer = BlendScorer(sub, fit["weights"], name=name)
        res = evaluate(scorer, [splits[i] for i in f], ctx, bank.n_history)
        for key, vals in res.p.items():
            if key not in p_arrays:
                p_arrays[key] = np.full(n, np.nan)
            p_arrays[key][f] = vals
        r_arr[f] = res.r_auc
        full[f] = res.full_history

    return TaskResult(user_ids=[s.user_id for s in splits], p=p_arrays,
                      r_auc=r_arr, full_history=full), fits


# ────────────────────────────────────────────────── 성분 간 상관 (Step 3a)
def spearman_matrix(x: np.ndarray) -> np.ndarray:
    """열끼리의 스피어만 상관 행렬 (C, C). 상수 열의 행·열은 NaN.

    스피어만을 쓰는 것은 성분마다 분포 모양이 다르기 때문이다 — `cf` 는 0 이 대부분인
    희소 점수이고 `popularity` 는 log 스케일이라 피어슨은 꼬리 몇 개에 끌려간다.
    순위 상관은 '같은 순서로 세우는가' 만 보므로, 우리가 실제로 쓰는 것(랭킹)과
    같은 질문이 된다. 동점은 평균 순위로 처리한다(`scipy.stats.rankdata`).
    """
    x = np.asarray(x, dtype=float)
    if x.ndim != 2:
        raise ValueError(f"2차원 (행=표본, 열=성분) 이어야 한다 — 받은 것 {x.shape}")
    r = rankdata(x, axis=0).astype(float)
    r -= r.mean(axis=0)
    sd = np.sqrt((r * r).sum(axis=0))
    ok = sd > 0
    out = np.full((x.shape[1], x.shape[1]), np.nan)
    if ok.any():
        rn = r[:, ok] / sd[ok]
        out[np.ix_(ok, ok)] = rn.T @ rn
    return np.clip(out, -1.0, 1.0)


def stack_candidate_rows(bank: ComponentBank, extra: dict | None = None
                         ) -> tuple[list[str], np.ndarray]:
    """(열 이름, 행렬) — 행은 (사용자 × 그 사용자의 후보) 전부, 열은 성분 z + extra.

    `extra` 는 `{이름: 풀 길이 벡터}`(예: `log1p(review_count)`)이고 사용자마다 그
    사용자의 후보만 잘라 붙인다. z 는 이미 사용자 안에서 정규화돼 있으므로 사용자를
    가로로 쌓아도 눈금이 맞는다 — 다만 **사용자 사이의 차이가 섞이므로** 사용자 안의
    상관은 `per_user_spearman` 로 따로 본다.
    """
    extra = dict(extra or {})
    names = list(bank.names) + list(extra)
    blocks = []
    for uid in bank.order:
        row = bank.rows[uid]
        cols = [row.zc.T]
        for v in extra.values():
            cols.append(np.asarray(v, dtype=float)[row.cand][:, None])
        blocks.append(np.concatenate(cols, axis=1))
    if not blocks:
        return names, np.zeros((0, len(names)))
    return names, np.concatenate(blocks, axis=0)


def per_user_spearman(bank: ComponentBank, extra: dict | None = None
                      ) -> tuple[list[str], np.ndarray, np.ndarray]:
    """사용자 **안에서** 잰 스피어만 상관의 (평균, 표준편차).

    풀링한 상관은 사용자 사이의 차이(이력이 긴 사람의 cf 점수가 통째로 크다든가)까지
    섞어 재는데, 우리가 쓰는 것은 한 사용자의 후보 안의 순위다. 두 숫자가 크게
    다르면 그 자체가 결과다.
    """
    extra = dict(extra or {})
    names = list(bank.names) + list(extra)
    mats = []
    for uid in bank.order:
        row = bank.rows[uid]
        cols = [row.zc.T] + [np.asarray(v, dtype=float)[row.cand][:, None]
                             for v in extra.values()]
        mats.append(spearman_matrix(np.concatenate(cols, axis=1)))
    if not mats:
        nan = np.full((len(names), len(names)), np.nan)
        return names, nan, nan
    stack = np.stack(mats)
    with np.errstate(invalid="ignore"):
        mean = np.nanmean(stack, axis=0)
        sd = np.nanstd(stack, axis=0)
    return names, mean, sd


def component_correlations(bank: ComponentBank, extra: dict | None = None) -> dict:
    """성분 상관 한 벌 — 풀링 + 사용자 내 평균. PLAN 7절 결정 5번의 산출물."""
    names, x = stack_candidate_rows(bank, extra)
    pooled = spearman_matrix(x)
    _, mean, sd = per_user_spearman(bank, extra)
    return {"names": names, "n_rows": int(x.shape[0]), "n_users": len(bank.order),
            "pooled": pooled.tolist(), "per_user_mean": mean.tolist(),
            "per_user_sd": sd.tolist()}


def subset_bank(bank: ComponentBank, names: list[str]) -> ComponentBank:
    """구성요소 일부만 남긴 통(ablation 용). z 는 다시 계산하지 않고 행만 고른다.

    남은 것 중 **첫 번째**가 가중치 1 로 고정되는 자리다. `popularity` 를 뺀 ablation
    에서는 그 자리를 다음 구성요소가 물려받는다 — 순위는 상수배에 불변이므로 '기준을
    무엇으로 두느냐' 는 눈금 문제이지 모델의 표현력 문제가 아니다.
    """
    if list(names) == list(bank.names):
        return bank
    idx = [bank.names.index(n) for n in names]
    rows = {uid: UserRow(user_id=r.user_id, z=r.z[idx], cand=r.cand, zc=r.zc[idx],
                         labels=r.labels, tiebreak=r.tiebreak, n_pos=r.n_pos)
            for uid, r in bank.rows.items()}
    return ComponentBank(names=list(names), n_history=bank.n_history, clip=bank.clip,
                         rows=rows, order=list(bank.order))
