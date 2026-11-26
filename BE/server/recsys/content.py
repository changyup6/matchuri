# -*- coding: utf-8 -*-
"""구성요소 점수 — 서빙 산출물(`data/kakao/serving`)을 읽어 z 점수 7개를 만든다.

PLAN 6절의 표를 그대로 구현한 자리다. 후보 i 에 대해 사용자의 like 집합 L,
dislike 집합 D 로 계산한다.

    z_pop     log1p(리뷰 수)                        관심 백본       (Step 0)
    z_q       베이지안 평균 평점                     만족 가드       (Step 0)
    z_cf      Σ_{h∈L} cooc(h, i)                    공방문          (Step 1.5)
    z_menu    max_{h∈L} sim_menu(h, i)              메뉴 집합       (Step 1)
    z_text    lse_{h∈L}(τ=0.1) sim_text(h, i)       가게 요약문     (Step 2)
    z_review  mean_{h∈L} sim_review(h, i)           리뷰 평균벡터   (Step 2)
    z_food    lse_{h∈L}(τ=0.1) sim_food(h, i)       음식 사진       (Step 3b)
    z_neg     max_{h∈D} avg(sim_menu, sim_text, sim_review)         기피 — 온라인 검증

집계는 Step 2 결선에 오른 것과 **같은 것**을 쓴다(`menu_top8_max`, `text_lse01`,
`review_longest12_single`, `cf_item_sum`). 백테스트 모델과 다른 것은 둘이다:

  * **z_food 는 max 가 아니라 lse(τ=0.1) 다.** Step 3b·3c 가 블렌드에 얹은 것은
    `photo_food_max` 다. 사진 성분은 사전 평균 0 으로 실리는 **온라인 전용 성분**
    이고 오프라인 λ 를 물려받지 않으므로, 같은 τ 를 쓰는 `z_text` 와 집계를 맞춰
    한 사람 안에서 두 성분의 눈금이 같은 방식으로 만들어지게 했다. τ=0.1 은 부드러운
    max 라 순위가 크게 다르지 않지만 **오프라인에서 이 조합을 잰 적은 없다** —
    Step 3c 주의 ⑤("집계를 블렌드 위에서 다시 스윕하지 않았다")가 그대로 남아 있고,
    답은 인터리빙이 준다.
  * **z_review 는 lse 가 아니라 mean 이다.** Step 2 의 리뷰 결선 모델은
    `review_longest12_single` — 가게 벡터(리뷰 평균)와 **like 가게 평균 벡터**의
    코사인이다. 그 점수는 `v_i·(Σ_h v_h)/‖Σ_h v_h‖ = (H/‖Σ v_h‖)·mean_h cos(v_i, v_h)`
    이고 괄호 앞은 후보 i 에 무관한 상수라, **후보 안에서 z 정규화하고 나면
    mean 집계와 정확히 같은 값**이 된다. lse 를 쓰면 오프라인에서 잰 적 없는 모델이
    된다 — 그래서 mean 이다.

**사전값이 0 인 성분이 둘이다 — `review` 와 `food`.** 백테스트가 점수 성분에서 뺀
것들이고, 성분 자리는 여기 남아 있고 사전 평균만 0 이라 그 사람의 쌍대 피드백이
0 에서 밀어내면 살아난다. 뺀 이유는 서로 다르다:

  * `review` — Step 3a. `review`↔`popularity` 스피어만 +0.67 로 **인기의 다른 이름**
    이라 가장 큰 λ 를 받고도 ablation 에서 죽었다. 리뷰 MaxSim 은 설명 레이어
    (`review_pair`)에만 남는다.
  * `food` — Step 3c. 인기 매칭 AUC 는 세 N 에서 CI 로 올랐지만(+.0077~+.0100)
    `ndcg@20` N=20 이 −.0040 [−.0080, −.0003] 로 내려 '진 칸 0' 채택 규칙에 걸렸다.
    `log1p(리뷰 수)` 상관은 **+0.14** 뿐이라 인기와 겹쳐서가 아니라 **상위 20장을
    세우는 정확도가 모자라** 죽었다. 즉 인기와 독립적인 취향 정보를 나르고 있고,
    스와이프 제품이 그 정보를 쓸 수 있는지는 오프라인이 답한 적이 없다.

둘 다 온라인 전용이다 — '오프라인에서 죽은 이유가 개인 취향에서도 참인가' 는
팀-드래프트 인터리빙이 답할 수 있는 질문이고, 사전 평균 0 이 그 질문의 형식이다.

**z 정규화.** 눈금이 modality 마다 다르므로(코사인 0.5 대 공방문 0.02) 정규화 없이
더하면 큰 눈금 하나가 전부를 결정한다. 그 사용자의 **후보**(이미 답한 가게를 뺀
나머지) 안의 평균·표준편차로 z 를 내고, **NaN 은 z 정규화 뒤에 0** 으로 만든다 —
0 은 '그 사용자 후보의 평균', 즉 '모른다' 의 올바른 번역이다. 규칙과 코드가
`analysis/content/blend.py: zscore_masked` 와 같다.

**z 는 ±3 에서 자른다 (Step 3d).** z 정규화는 눈금은 맞추지만 **꼬리는 못 자른다**.
`cf` 는 공방문 코사인의 합이라 희소하다 — like 한 곳이면 후보 2,216곳 중 **중앙값
2,178곳(98.3%)이 0** 이다. 표준편차가 0 에 가까우니 몇 곳의 z 가 **+40 을 넘고**
(백테스트 후보에서 최대 +41.86, |z| > 3 이 2.70%), 같은 가게를 두 번 평가하면
cooc(x,x)=1 이 들어와 **+256** 까지 갔다(실서버 관측). 온라인 갱신은 한 걸음이
`Δm ≈ v·Δz` 라 z 하나가 수십이면 가중치가 한 번에 수십 단위로 밀리고, 그다음부터는
σ(w·Δz) 가 포화해 **어떤 피드백도 모델을 못 움직인다**. 그래서 정규화 **뒤** 에
[−3, 3] 으로 자른다 — 3σ 밖의 값은 '엄청나게 크다' 와 '아주 크다' 를 구분할 필요가
없는 자리이고, 순위는 상위 몇 곳 사이에서만 바뀐다. 서빙·peek·지형도·재생이 전부
같은 `component_scores` 를 쓰므로 규칙도 한 곳에만 있다.

**속도.** 유사도 행렬이 이미 있으므로 한 번의 호출은 (|L| × 2,216) 행 몇 장을 접는
일이다. |L| = 300 에서도 10ms 안쪽이고 난수를 쓰지 않는다 — 같은 입력이면 항상 같은
답이 나와야 `preferences.jsonl` 재생이 상태를 정확히 복원한다.

산출물이 없으면 `load()` 가 None 을 돌려주고 서버는 legacy 정책만으로 돈다
(`server/recsys/policy.py`). **없다고 죽지 않는다.**
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# BE/server/recsys/content.py -> parents[2] = BE
ROOT = Path(__file__).resolve().parents[2]
SERVING = ROOT / "data" / "kakao" / "serving"

#: 성분 순서. `analysis/build_serving.py: COMPONENTS` 와 같아야 한다.
COMPONENTS = ("pop", "quality", "cf", "menu", "text", "review", "food", "neg")

#: 성분 이름 -> 자리. 색인을 숫자로 적으면 성분을 하나 더할 때 조용히 어긋난다
#: (`z[6]` 이 어제는 기피였고 오늘은 음식 사진이다). 이름으로만 찾는다.
IDX = {c: i for i, c in enumerate(COMPONENTS)}

#: 관리자 화면·추천 이유에 쓰는 한국어 이름.
LABELS = {"pop": "인기", "quality": "평판", "cf": "공방문", "menu": "메뉴",
          "text": "분위기·특징", "review": "리뷰", "food": "음식 사진", "neg": "기피"}

#: z_text·z_food 의 logsumexp 온도 (Step 2 의 `text_lse01`).
TAU = 0.1

#: z 정규화 뒤 잘라 낼 폭 (Step 3d). 희소·긴꼬리 성분(`cf`)의 z 가 ±40 을 넘어
#: 한 번의 쌍대 갱신이 가중치를 수십 단위로 밀어 버리는 것을 막는다. 모듈 문서 참조.
Z_CLIP = 3.0

#: 산출물이 없을 때도 정책 코드가 성분 개수를 알 수 있어야 한다.
N_COMPONENTS = len(COMPONENTS)

_lock = threading.Lock()
_loaded = False
_art: "Artifacts | None" = None


# ────────────────────────────────────────────────────────── 집계 (numpy 만)
def _lse(rows: np.ndarray, tau: float = TAU) -> np.ndarray:
    """τ·log(Σ exp(s/τ)/H) — `analysis/backtest/scorers_menu.aggregate` 와 같은 식.

    H 로 나누기 때문에 이력 길이가 달라도 눈금이 같다. τ→0 이면 max, τ→∞ 면 mean.
    (H, P) 를 그대로 제자리에서 굴린다 — |L| = 300 이면 66만 칸이라 임시 배열을
    하나 더 만들 때마다 밀리초가 붙는다.
    """
    r = rows * np.float32(1.0 / tau)
    hi = r.max(axis=0)
    r -= hi
    np.exp(r, out=r)
    return tau * (hi + np.log(r.sum(axis=0) / rows.shape[0])).astype(np.float64)


def zscore_masked(v: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """마스크 안의 평균·표준편차로 z 정규화하고 NaN 을 0 으로.

    `analysis/content/blend.py: zscore_masked` 와 같은 규칙이다(값이 같아야 오프라인
    에서 고른 가중치를 온라인에서 그대로 쓸 수 있다).
    """
    v = np.asarray(v, dtype=np.float64)
    sub = v[mask] if mask is not None else v
    finite = np.isfinite(sub)
    if not finite.any():
        return np.zeros_like(v)
    mu = float(sub[finite].mean())
    sd = float(sub[finite].std())
    out = (v - mu) / (sd if sd > 0 else 1.0)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def _rows(sim: np.ndarray, idx: np.ndarray, has: np.ndarray) -> np.ndarray:
    """이력 가게들의 행. **그 modality 가 없는 이력 가게(전부 NaN)는 뺀다.**

    유사도 행렬의 NaN 은 가게 단위다 — 가게 i 에 그 modality 가 없으면 i 행과 i 열이
    통째로 NaN 이다. 그래서 (a) 뺄 이력 행은 `has` 하나로 골라낼 수 있고(행렬을
    훑지 않는다), (b) 남은 행에서 한 후보 열은 전부 NaN 이거나 전부 유한하다 —
    평범한 max/mean/lse 가 오프라인 참조 구현(`content_score`)과 같은 답을 낸다.
    """
    if idx.size == 0:
        return np.zeros((0, sim.shape[1]), dtype=np.float32)
    keep = idx[has[idx]]
    if keep.size == 0:
        return np.zeros((0, sim.shape[1]), dtype=np.float32)
    return sim[keep]


# ────────────────────────────────────────────────────────── 산출물
@dataclass
class Artifacts:
    """후보 풀 하나에 대한 서빙 행렬 묶음. 프로세스에 한 벌만 산다."""

    ids: list[str]
    pos: dict[str, int]
    pop: np.ndarray                     # (P,) log1p(리뷰 수)
    quality: np.ndarray                 # (P,) 베이지안 평균의 z (swipe.pool()["qz"])
    sim_menu: np.ndarray                # (P, P) float32(RAM), 없는 가게는 NaN
    sim_text: np.ndarray
    sim_review: np.ndarray
    sim_food: np.ndarray
    cooc: np.ndarray
    has: dict = field(default_factory=dict)     # modality -> (P,) bool
    brand: list[str] = field(default_factory=list)   # (P,) 브랜드 키
    prior: dict = field(default_factory=dict)
    review_vecs: np.ndarray | None = None       # (R, D) float16
    review_starts: np.ndarray | None = None     # (P,) int64
    review_counts: np.ndarray | None = None
    review_snippets: list[list[str]] = field(default_factory=list)
    review_ids: list[list[int]] = field(default_factory=list)
    path: str = ""
    # Only _build enables this for one immutable serving artifact instance.
    # Hand-built/research artifacts remain uncached and may be edited in place.
    _negative_average: np.ndarray | None = field(default=None, init=False, repr=False)
    _negative_known: np.ndarray | None = field(default=None, init=False, repr=False)

    # ── 성분
    def component_scores(self, like: np.ndarray, dislike: np.ndarray,
                         exclude: np.ndarray | None = None, *,
                         like_weights: np.ndarray | None = None,
                         dislike_weights: np.ndarray | None = None) -> np.ndarray:
        """(C, P) z 점수. `like`/`dislike`/`exclude` 는 풀 인덱스 배열이다.

        like 가 비면 콘텐츠 성분(cf·menu·text·review)은 전부 0 이다 — '아직 아무것도
        모른다'. 그때 점수를 만드는 것은 z_pop 과 z_q 뿐이고, 그것이 콜드스타트다.

        마지막에 **모든 성분을 [−`Z_CLIP`, `Z_CLIP`] 으로 자른다** — 이유는 모듈
        문서의 'z 는 ±3 에서 자른다' 를 보라. 여기 한 곳에서 자르므로 서빙·peek·
        지형도·재생이 전부 같은 값을 본다.
        """
        if like_weights is not None or dislike_weights is not None:
            return self._weighted_scores(like, dislike, exclude, like_weights, dislike_weights)
        n = len(self.ids)
        mask = np.ones(n, dtype=bool)
        if exclude is not None and exclude.size:
            mask[exclude] = False
        if mask.sum() < 2:                    # 후보가 거의 없으면 풀 전체로 정규화
            mask = np.ones(n, dtype=bool)

        z = np.zeros((N_COMPONENTS, n), dtype=np.float64)
        z[IDX["pop"]] = zscore_masked(self.pop, mask)
        z[IDX["quality"]] = zscore_masked(self.quality, mask)

        if like.size:
            z[IDX["cf"]] = zscore_masked(np.asarray(self.cooc[like], dtype=np.float32)
                                         .sum(axis=0), mask)
            rm = _rows(self.sim_menu, like, self.has["menu"])
            if rm.shape[0]:
                z[IDX["menu"]] = zscore_masked(rm.max(axis=0), mask)
            rt = _rows(self.sim_text, like, self.has["text"])
            if rt.shape[0]:
                z[IDX["text"]] = zscore_masked(_lse(rt), mask)
            rr = _rows(self.sim_review, like, self.has["review"])
            if rr.shape[0]:
                z[IDX["review"]] = zscore_masked(rr.mean(axis=0), mask)
            # 음식 사진 — 요약문과 **같은 NaN 규칙**이다. 음식 사진이 없는 like
            # 가게는 행에서 빠지고(`_rows`), 음식 사진이 없는 후보 343곳은 열이
            # 통째로 NaN 이라 z 정규화 뒤 0(= 후보 평균 = '모른다')이 된다.
            rf = _rows(self.sim_food, like, self.has["food"])
            if rf.shape[0]:
                z[IDX["food"]] = zscore_masked(_lse(rf), mask)

        if dislike.size:
            avg = self._avg_sim(dislike)
            if avg.shape[0]:
                z[IDX["neg"]] = zscore_masked(avg.max(axis=0), mask)
        return np.clip(z, -Z_CLIP, Z_CLIP, out=z)

    def _weighted_scores(self, like, dislike, exclude, like_weights, dislike_weights):
        """Same modality reducers with exponential evidence weights.

        Max reducers use similarity + TAU*log(weight); LSE uses weighted exp,
        mean/sum use ordinary weights. Missing modalities remain unknown (zero).
        After normalization, the newest available evidence weight bounds each
        component, so an old, unopposed like/dislike cannot regain full strength.
        """
        z = self.component_scores(np.array([], int), np.array([], int), exclude)
        mask = np.ones(len(self.ids), dtype=bool)
        if exclude is not None:
            mask[exclude] = False
        if mask.sum() < 2:
            mask[:] = True

        def aggregate(rows, weights, kind):
            if not len(rows):
                return np.zeros(len(self.ids))
            weights = np.asarray(weights, float)
            if kind in ('max', 'lse'):
                adjusted = rows + TAU * np.log(np.maximum(weights[:, None], 1e-30))
            if kind == 'max':
                values = adjusted.max(axis=0)
            elif kind == 'lse':
                values = _lse(adjusted)
            elif kind == 'sum':
                values = (rows * weights[:, None]).sum(axis=0)
            else:
                values = (rows * weights[:, None]).sum(axis=0) / max(weights.sum(), 1e-30)
            return np.clip(zscore_masked(values, mask), -Z_CLIP, Z_CLIP) * weights.max()

        lw = np.ones(len(like)) if like_weights is None else np.asarray(like_weights)
        dw = np.ones(len(dislike)) if dislike_weights is None else np.asarray(dislike_weights)
        if len(like):
            z[IDX['cf']] = aggregate(np.asarray(self.cooc[like], float), lw, 'sum')
            for kind, reducer in (('menu', 'max'), ('text', 'lse'), ('review', 'mean'), ('food', 'lse')):
                keep = self.has[kind][like]
                z[IDX[kind]] = aggregate(getattr(self, 'sim_' + kind)[like[keep]], lw[keep], reducer)
        if len(dislike):
            keep = np.logical_or.reduce([self.has[k][dislike] for k in ('menu', 'text', 'review')])
            z[IDX['neg']] = aggregate(self._avg_sim(dislike), dw[keep], 'max')
        return z

    def _avg_sim(self, idx: np.ndarray) -> np.ndarray:
        """(H, P) — 세 modality 유사도의 평균. 있는 modality 만으로 평균한다.

        기피 성분에는 오프라인 근거가 없다(방문 데이터에 dislike 가 없다). 그래서
        '어느 modality 를 쓸까' 를 고를 근거도 없어 셋을 평균한다. 셋 중 없는 것이
        있어도(메뉴 없는 가게 546곳) 남은 것으로 평균한다 — 전부 없으면 NaN 이다.

        **음식 사진은 여기 넣지 않는다.** 기피는 이미 온라인에서만 검증되는 항이라
        섞는 modality 를 늘리면 인터리빙이 무엇을 잰 것인지 갈라 읽을 수 없게 된다.
        사진은 자기 성분(`z_food`)으로 따로 묻는다.
        """
        if self._negative_average is not None:
            return self._negative_average[idx[self._negative_known[idx]]]
        total = np.zeros((idx.size, len(self.ids)), dtype=np.float32)
        cnt = np.zeros_like(total)
        for kind, sim in (("menu", self.sim_menu), ("text", self.sim_text),
                          ("review", self.sim_review)):
            has = self.has[kind]
            rows_ok = has[idx]
            if not rows_ok.any():
                continue
            block = np.where(has[None, :], sim[idx], 0.0).astype(np.float32)
            block[~rows_ok] = 0.0
            total += block
            cnt += (rows_ok[:, None] & has[None, :])
        with np.errstate(invalid="ignore", divide="ignore"):
            avg = np.where(cnt > 0, total / np.maximum(cnt, 1.0), np.nan)
        keep = np.isfinite(avg).any(axis=1)
        return avg[keep]

    # ── 설명용 조회
    def best_like(self, kind: str, cand: int, like: np.ndarray) -> int | None:
        """후보 `cand` 와 가장 닮은 like 가게의 풀 인덱스. 없으면 None."""
        if like.size == 0:
            return None
        mat = {"menu": self.sim_menu, "text": self.sim_text,
               "review": self.sim_review, "food": self.sim_food,
               "cf": self.cooc}.get(kind)
        if mat is None:
            return None
        col = np.asarray(mat[like, cand], dtype=np.float32)
        if not np.isfinite(col).any():
            return None
        col = np.where(np.isfinite(col), col, -np.inf)
        return int(like[int(np.argmax(col))])

    def review_pair(self, a: int, b: int) -> tuple[str, str, float]:
        """두 가게에서 가장 잘 맞는 리뷰 한 쌍 — (A 스니펫, B 스니펫, 코사인).

        `analysis/content/menus.py: best_menu_pair` 와 같은 계산이다. 가게당 실린
        벡터는 6개뿐이라 6×6 내적 한 번이면 끝난다.
        """
        if self.review_vecs is None or self.review_counts is None:
            return ("", "", float("nan"))
        ca, cb = int(self.review_counts[a]), int(self.review_counts[b])
        if ca == 0 or cb == 0:
            return ("", "", float("nan"))
        sa, sb = int(self.review_starts[a]), int(self.review_starts[b])
        va = np.asarray(self.review_vecs[sa:sa + ca], dtype=np.float32)
        vb = np.asarray(self.review_vecs[sb:sb + cb], dtype=np.float32)
        d = va @ vb.T
        i, j = np.unravel_index(int(np.argmax(d)), d.shape)
        return (self.review_snippets[a][int(i)], self.review_snippets[b][int(j)],
                float(d[i, j]))

    def prior_mean(self) -> np.ndarray:
        m = self.prior.get("mean", {})
        return np.array([float(m.get(c, 0.0)) for c in COMPONENTS])

    def prior_sd(self) -> np.ndarray:
        s = self.prior.get("sd", {})
        return np.array([float(s.get(c, 0.5)) for c in COMPONENTS])


# ────────────────────────────────────────────────────────── 로딩
def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


#: '정확히 1.0' 의 허용오차. 서로 다른 두 문장의 코사인이 1.0 이 되는 일은 사실상
#: 없다 — **같은 벡터일 때만** 그렇다. 산출물은 float16 으로 저장돼 있고(`npy()` 가
#: float32 로 펴서 올린다) float16 에서 1.0 바로 아래에 표현 가능한 값이 0.99951 이라,
#: 1e-6 은 '캐스팅 뒤에도 정확히 1.0' 과 같은 뜻이다. 산출물을 float32 로 굽게 되어도
#: 행렬곱·대칭화의 부동소수 오차(수 ULP)를 덮는 폭이다.
_DEGENERATE_TOL = 1e-6


def _degenerate_text_rows(sim_text: np.ndarray) -> np.ndarray:
    """소개글 벡터가 퇴화한 가게 (P,) bool — **자기 자신이 아닌** 다른 가게와
    유사도가 정확히 1.0 인 행.

    **무엇이 문제였나.** 소개글에 설명(`ai_title`·`ai_summary`)이 없는 가게들이
    태그만으로 만든 **글자까지 같은 문장**을 받아, 임베딩이 같은 점으로 보낸다.
    실제 관측(후보 풀 1,629곳): 중국집 오늘완탕 ↔ 카페 에슬로우커피, 피자집
    리츠델리피자 ↔ 닭갈비집 춘여닭숯불닭갈비 등 **9쌍**이 1.000 이었고 15곳 전부
    `ai_title`·`ai_summary` 가 None 이다. 이 한 칸이 추천 4곳의 '서로 얼마나 비슷한가'
    판정(여섯 쌍 × 네 성분의 **최댓값** — `recsys/diversity.py: _build`)을 통째로
    먹어, 중국집과 커피숍이 '가장 비슷한 조합' 으로 뽑혀 카드가 엉뚱하게 교체된다.
    원인은 `analysis/embed.py: compose_store_text` 에 주석으로 적어 두었다.

    **설계 의도는 원래 NaN 이다** — `analysis/content/store_text.py` 머리말이 '없는
    가게의 유사도는 메뉴와 같은 규칙으로 NaN' 이라고 적어 놨다. 0 으로 채우면 '아무와도
    안 닮은 가게' 가 되는데 실제로는 '모른다' 이기 때문이다.

    **왜 산출물을 다시 굽지 않나.** 원본 벡터 파일(`data/vectors/`)이 저장소에 없어
    (gitignore) `analysis/build_serving.py` 를 다시 돌릴 수 없다. 그래서 **지금 서버가
    읽는 산출물**을 불러올 때 이 자리에서 거른다. 산출물을 고쳐 다시 구우면 그 행이
    처음부터 NaN 이라 이 필터는 0곳을 잡는다(무해).

    **찾기만 하고 고치지는 않는다** — 부르는 쪽(`_build`)이 행·열을 통째로 NaN 으로
    만든다. `has["text"]` 만 내리는 것으로는 모자라기 때문이다: `has` 를 보는 것은
    이 파일의 `component_scores` 뿐이고 `recsys/diversity.py`·`recsys/decision.py` 는
    `art.sim_text` 를 **직접** 읽는다. 행렬 자체를 막아야 한 규칙이 모든 소비자에게
    같게 닿는다(대각선이 NaN 이 되므로 `has["text"]` 도 자동으로 False 가 된다).

    대각선만 보는 `has` 와 달리 **비대각 원소를 봐야** 알 수 있으므로 한 번의 벡터화
    비교가 필요하다. 소개글이 있는 행·열(1,627²)로 좁혀 bool 배열 한 장만 만든다.
    같은 행의 1.0 개수가 2 이상(자기 자신 + 다른 가게)이면 퇴화다 — 대각선을 지우려고
    행렬을 복사하지 않기 위한 셈이다.

    이미 NaN 인 행(소개글 자체가 없는 가게)은 대각선이 NaN 이라 후보에서 빠진다 —
    **건드리지 않는다.**
    """
    out = np.zeros(sim_text.shape[0], dtype=bool)
    idx = np.flatnonzero(np.isfinite(np.diagonal(sim_text)))
    if idx.size < 2:
        return out
    block = sim_text[np.ix_(idx, idx)]
    near_one = block >= np.asarray(1.0 - _DEGENERATE_TOL, dtype=block.dtype)
    out[idx] = near_one.sum(axis=1) >= 2
    return out


def _build(serving: Path) -> Artifacts | None:
    """산출물을 읽어 Artifacts 를 만든다. 하나라도 어긋나면 None(→ legacy 로 돈다)."""
    from . import swipe                        # 후보 풀의 정본. 순환 import 아님

    need = ["pool_ids.json", "sim_menu.npy", "sim_text.npy", "sim_review.npy",
            "sim_food.npy", "cooc.npy", "prior.json"]
    missing = [n for n in need if not (serving / n).exists()]
    if missing:
        print(f"[content] 서빙 산출물 없음 ({', '.join(missing)}) — "
              f"legacy 정책으로만 돕니다: {serving}", flush=True)
        return None

    ids = _read_json(serving / "pool_ids.json")
    pool = swipe.pool()
    if list(ids) != list(pool["ids"]):
        print(f"[content] 후보 풀이 산출물과 다릅니다 "
              f"(산출물 {len(ids)}곳 vs 서버 {len(pool['ids'])}곳). "
              f"`python -m analysis.build_serving` 를 다시 도세요 — "
              f"legacy 정책으로만 돕니다", flush=True)
        return None

    def npy(name: str) -> np.ndarray:
        """float16 로 저장한 행렬을 **float32 로 펴서** 메모리에 올린다.

        디스크(이미지)에서는 float16 이라 9.8MB 지만, 호출마다 float16→float32 로
        바꾸면 |L| = 300 에서 modality 하나당 3.5ms 가 든다(측정값) — 다섯 장이면
        예산을 다 쓴다. 켤 때 한 번 펴 두면 행 뽑기가 0.6ms 로 내려간다.
        대신 상주 메모리가 5장 × 19.6MB = 98MB 늘어난다.
        """
        return np.asarray(np.load(serving / name), dtype=np.float32)

    from . import store as svc
    counts = np.array([float((svc.get_store(s) or {}).get("kakao_review_count") or 0)
                       for s in ids])
    brand_map = (_read_json(serving / "brand_key.json")
                 if (serving / "brand_key.json").exists() else {})

    sim_menu, sim_text, sim_review, sim_food = (
        npy("sim_menu.npy"), npy("sim_text.npy"), npy("sim_review.npy"),
        npy("sim_food.npy"))
    # 소개글이 퇴화한 가게의 **행·열을 통째로 NaN 으로** 만든다 — `has` 를 만들기
    # 전이다. 이 modality 가 없는 가게와 똑같은 모양('행·열이 통째로 NaN')이 되므로
    # 대각선도 NaN 이 되어 `has["text"]` 가 자동으로 False 가 되고, `has` 를 보지 않고
    # `art.sim_text` 를 직접 읽는 소비자(`recsys/diversity.py`, `recsys/decision.py`)
    # 에게도 **같은 규칙**이 닿는다. `has` 만 내리면 그 둘이 여전히 1.000 을 본다.
    # 원본 벡터 파일(`data/vectors/`)이 저장소에 없어 산출물을 다시 구울 수 없으므로
    # 불러올 때 거른다. 자세한 이유는 `_degenerate_text_rows` 문서를 보라.
    degenerate = _degenerate_text_rows(sim_text)
    n_degenerate = int(degenerate.sum())
    if n_degenerate:
        sim_text[degenerate, :] = np.nan
        sim_text[:, degenerate] = np.nan
        print(f"[content] 소개글이 퇴화한 가게 {n_degenerate}곳의 sim_text 행·열을 "
              f"NaN 으로 둡니다 (다른 가게와 유사도 1.000 — 설명이 없어 태그만 남은 "
              f"문장이 글자까지 같아 같은 벡터를 받은 것)", flush=True)
    # 대각선이 곧 '그 modality 가 있는가' 다 — 없는 가게는 행·열이 통째로 NaN 이라
    # 자기 자신과의 유사도도 NaN 이다. 5백만 칸을 훑지 않고 2,216칸만 본다.
    has = {"menu": np.isfinite(np.diagonal(sim_menu)).copy(),
           "text": np.isfinite(np.diagonal(sim_text)).copy(),
           "review": np.isfinite(np.diagonal(sim_review)).copy(),
           "food": np.isfinite(np.diagonal(sim_food)).copy()}

    vecs = starts = counts_r = None
    snippets: list[list[str]] = []
    review_ids: list[list[int]] = []
    if (serving / "review_vecs.npy").exists() and (serving / "review_pairs.json").exists():
        idx = _read_json(serving / "review_pairs.json")
        vecs = npy("review_vecs.npy")
        starts = np.asarray(idx["starts"], dtype=np.int64)
        counts_r = np.asarray(idx["counts"], dtype=np.int64)
        snippets = idx["snippets"]
        review_ids = idx["review_ids"]

    art = Artifacts(
        ids=list(ids), pos={s: i for i, s in enumerate(ids)},
        pop=np.log1p(counts), quality=np.asarray(pool["qz"], dtype=np.float64),
        sim_menu=sim_menu, sim_text=sim_text, sim_review=sim_review,
        sim_food=sim_food, cooc=npy("cooc.npy"), has=has,
        brand=[brand_map.get(s, s) for s in ids],
        prior=_read_json(serving / "prior.json"),
        review_vecs=vecs, review_starts=starts, review_counts=counts_r,
        review_snippets=snippets, review_ids=review_ids, path=str(serving))
    # Negative evidence always uses the same three immutable modality matrices.
    # Compute their float32 average in the original addition order once; later
    # feedback selects rows (including repeats), rather than rebuilding it.
    known = np.logical_or.reduce([has[k] for k in ("menu", "text", "review")])
    average = np.full((len(ids), len(ids)), np.nan, dtype=np.float32)
    average[known] = art._avg_sim(np.arange(len(ids)))
    average.setflags(write=False)
    known.setflags(write=False)
    art._negative_average, art._negative_known = average, known
    print(f"[content] 서빙 산출물 로드: 풀 {len(ids):,}곳 · 사전값 "
          f"{art.prior.get('source', '?')} · {serving}", flush=True)
    return art


def load(serving: Path | None = None) -> Artifacts | None:
    """산출물을 한 번만 읽는다. 없으면 None — 부르는 쪽이 legacy 로 물러난다."""
    global _loaded, _art
    with _lock:
        if not _loaded:
            try:
                _art = _build(Path(serving or SERVING))
            except Exception as exc:              # noqa: BLE001
                print(f"[content] 산출물 로드 실패 — legacy 정책으로만 돕니다: {exc!r}",
                      flush=True)
                _art = None
            _loaded = True
        return _art


def available() -> bool:
    return load() is not None


def reset() -> None:
    """테스트용 — 다음 `load()` 가 다시 읽게 한다."""
    global _loaded, _art
    with _lock:
        _loaded = False
        _art = None
