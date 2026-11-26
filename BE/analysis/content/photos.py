# -*- coding: utf-8 -*-
"""가게의 사진 표현 — 음식/공간 분리 (PLAN 2절 '사진 (Step 3)').

`photos.jsonl` 의 tag 가 이미 FOOD / INDOOR / OUTDOOR 로 나뉘어 있으므로 **음식 벡터**와
**공간 벡터**를 따로 만든다. 한 가게의 '음식이 내 취향인가' 와 '공간이 내 취향인가' 는
다른 질문이고, 섞어 평균내면 둘 다 흐려진다.

    food   tag=FOOD                  가게당 ≤ 4장
    space  tag=INDOOR 또는 OUTDOOR    가게당 ≤ 3장

임베딩은 CLIP ViT-B/32 의 이미지 탑 512차원, L2 정규화 (`analysis/photos/embed.py`).

**기본 표현은 단일 벡터다.** PLAN 원칙 1번("집합으로 유지한다")은 리뷰에서 뒤집혔다
— Step 2 에서 리뷰 12개의 평균 벡터 1개가 MaxSim 을 전 지표에서 이겼고, 난수 벡터
대조군이 진짜 벡터보다 높은 AUC 를 냈다(집합 크기 ↔ 인기 교란). 사진도 같은 함정에
있으므로(사진 수는 곧 인기다) 점수용 1급 표현은 **가게당 벡터 하나**(그 가게 사진
벡터의 평균을 L2 정규화)로 두고, MaxSim 은 같은 교란을 사진에서도 다시 재는
**진단용 변종**으로만 둔다.

    sim_single(A, B) = v_A · v_B                     (코사인 한 번)
    sim_maxsim(A, B) = (MaxSim(A→B) + MaxSim(B→A))/2 (메뉴·리뷰와 같은 함수)

**난수 벡터 대조군은 의무다** (PLAN 7절 Step 2 → Step 3 결정 1번). 두 종류를 둔다.
  * 단일 벡터 대조군 — **커버리지 구조만 남기고** 벡터를 난수 단위벡터로 바꾼다.
    사진이 없는 가게는 그대로 NaN 이다. 집합 크기 교란은 없지만 '사진이 있는 가게'
    자체가 인기와 상관되므로, 커버리지만으로 얻는 점수가 여기 남는다.
  * MaxSim 대조군 — 개수 구조를 그대로 두고 벡터만 난수로(`menus.random_unit_packed`,
    메뉴·리뷰와 **같은 함수**). 집합 크기 교란의 몫이 여기 남는다.
진짜 벡터가 자기 대조군을 못 넘으면 그 표현의 점수는 내용의 증거가 아니다.

**사진이 없는 가게는 NaN 이다.** 후보 풀 2,216곳 전부에 사진이 있는 것이 아니다
(카드 자격의 '사진 1+' 은 MENU·MYSTORE 태그도 세기 때문에 FOOD 가 0인 가게가 있다).
0 으로 채우면 '아무와도 안 닮은 가게' 가 되어 콘텐츠 모델이 구조적으로 밀어내는데
실제로는 '모른다' 다 — 메뉴·리뷰·요약문과 **같은 규칙**이다. 점수 단계에서 NaN 후보는
그 사용자의 평균 점수(중립)로 채운다(`scorers_menu.content_score`).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from analysis import vectors
from analysis.content.menus import (CACHE_DIR, Packed, best_menu_pair,
                                    random_unit_packed, similarity_matrix)

ROOT = Path(__file__).resolve().parents[2]

#: kind -> 임베딩 산출물 이름. `analysis/photos/embed.py` 가 쓰는 이름과 같아야 한다.
KIND_VECTORS = {"food": "photo_food", "space": "photo_space"}

#: 사례 설명에서 사진 쌍을 뽑을 때 쓰는 이름표(사진에는 이름이 없으므로 url 이 곧 이름).
KIND_LABEL = {"food": "음식", "space": "공간"}

#: 난수 대조군의 seed 갈래. 메뉴·리뷰와 겹치지 않게 kind 이름을 섞는다.
NULL_SEED_SALT = "photo-null"

# 사진 쌍 설명은 메뉴와 같은 함수를 쓴다 — Packed 구조가 같기 때문이다.
best_photo_pair = best_menu_pair


def _paths(name: str) -> tuple[Path, Path]:
    return (vectors.VECTOR_DIR / f"{name}.npy",
            vectors.VECTOR_DIR / f"{name}.index.jsonl")


def _read_index(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@dataclass
class PhotoSpace:
    """후보 풀 가게 × 사진 벡터 (한 kind).

    `vecs` 가 점수용 1급 표현(가게당 1개, 없으면 NaN 행)이고 `packed` 는 진단용
    MaxSim 변종이 쓰는 사진 단위 집합이다. 둘은 **같은 사진**에서 나온다 —
    단일 벡터는 packed 의 평균이므로 비교가 '집합이냐 평균이냐' 하나로 좁혀진다.
    """

    kind: str
    store_ids: list[str]
    vecs: np.ndarray                       # (S, D) float32, 사진 없는 가게는 NaN
    packed: Packed                         # 사진 단위 ragged 묶음 (names = url)
    tags: list[list[str]] = field(default_factory=list)   # 가게별 사진 tag
    manifest_sha: str = ""
    seed: int = 0

    # ── 구축
    @classmethod
    def build(cls, kind: str, store_ids: list[str], *, seed: int = 0) -> "PhotoSpace":
        if kind not in KIND_VECTORS:
            raise ValueError(f"모르는 사진 kind '{kind}' (가능: {', '.join(KIND_VECTORS)})")
        name = KIND_VECTORS[kind]
        npy, idx_path = _paths(name)
        if not npy.exists() or not idx_path.exists():
            raise FileNotFoundError(
                f"{npy} 가 없다. 먼저 `python -m analysis.photos.download` 로 사진을 받고 "
                f"`python -m analysis.photos.embed` 로 임베딩해라")
        matrix = np.load(npy, mmap_mode="r")
        index = _read_index(idx_path)
        if matrix.shape[0] != len(index):
            raise ValueError(f"{name}: 행렬 {matrix.shape[0]}행 vs 인덱스 {len(index)}줄 — "
                             f"산출물이 어긋났다. 다시 임베딩해라")

        pos = {sid: i for i, sid in enumerate(store_ids)}
        rows_by_store: dict[int, list[dict]] = {}
        for r in index:
            i = pos.get(str(r["store_id"]))
            if i is not None:
                rows_by_store.setdefault(i, []).append(r)

        # ragged 묶음 — 풀 순서대로 이어 붙인다(임베딩 인덱스 순서도 풀 순서다).
        take: list[int] = []
        names: list[str] = []
        tags: list[list[str]] = []
        counts = np.zeros(len(store_ids), dtype=np.int64)
        for i in range(len(store_ids)):
            rows = rows_by_store.get(i, [])
            take.extend(int(r["row"]) for r in rows)
            names.extend(str(r.get("url", "")) for r in rows)
            tags.append([str(r.get("tag", "")) for r in rows])
            counts[i] = len(rows)
        starts = np.zeros(len(counts), dtype=np.int64)
        np.cumsum(counts[:-1], out=starts[1:])
        dim = int(matrix.shape[1])
        vecs = (np.asarray(matrix[np.asarray(take, dtype=np.int64)], dtype=np.float32)
                if take else np.zeros((0, dim), dtype=np.float32))
        packed = Packed(starts=starts, counts=counts, vecs=vecs,
                        weights=np.ones(len(take), dtype=np.float32), names=names)

        man = vectors.manifest().get(name, {})
        return cls(kind=kind, store_ids=list(store_ids),
                   vecs=cls._store_vectors(packed, dim), packed=packed, tags=tags,
                   manifest_sha=str(man.get("sha256", "")), seed=seed)

    @staticmethod
    def _store_vectors(packed: Packed, dim: int) -> np.ndarray:
        """가게당 벡터 1개 = 그 가게 사진 벡터의 평균을 L2 정규화. 없으면 NaN 행.

        `analysis/photos/embed.py` 가 저장해 두는 `*_store.npy` 와 같은 값이다.
        여기서 다시 계산하는 것은 후보 풀 구성이 바뀌어도 어긋나지 않게 하기 위해서다
        (그 파일은 만들 때의 풀 순서로 고정돼 있다).
        """
        out = np.full((len(packed.counts), dim), np.nan, dtype=np.float32)
        for i in range(len(packed.counts)):
            if packed.counts[i] == 0:
                continue
            v = packed.vecs[packed.slice(i)].astype(np.float32).mean(axis=0)
            norm = float(np.linalg.norm(v))
            out[i] = v / norm if norm > 0 else 0.0
        return out

    # ── 커버리지
    @property
    def has_photo(self) -> np.ndarray:
        return self.packed.counts > 0

    @property
    def coverage(self) -> float:
        return float(self.has_photo.mean()) if len(self.store_ids) else float("nan")

    def coverage_stats(self) -> dict:
        """결과 .md 가 읽는 커버리지 표 — 사진 없는 가게가 몇 곳인지가 NaN 규칙의 크기다."""
        n = self.packed.counts
        return {"kind": self.kind, "stores": int(n.size),
                "with_photo": int((n > 0).sum()),
                "coverage": float((n > 0).mean()) if n.size else float("nan"),
                "photos": int(n.sum()),
                "mean_per_store": float(n.mean()) if n.size else float("nan"),
                "mean_per_covered": float(n[n > 0].mean()) if (n > 0).any()
                else float("nan"),
                "max_per_store": int(n.max()) if n.size else 0,
                "hist": {str(k): int((n == k).sum()) for k in range(int(n.max()) + 1)}
                if n.size else {}}

    # ── 난수 대조군
    def null_vectors(self, *, seed: int | None = None) -> np.ndarray:
        """단일 벡터 대조군 — **커버리지 구조는 그대로**, 값만 난수 단위벡터.

        사진이 있는 가게만 난수 벡터를 받고 없는 가게는 NaN 으로 남는다. 즉 이
        대조군이 얻는 점수는 전부 '어떤 가게에 사진이 있는가' 라는 구조에서 온다.
        """
        rng = np.random.default_rng(
            _seed_int(self.seed if seed is None else seed, NULL_SEED_SALT, self.kind))
        out = np.full(self.vecs.shape, np.nan, dtype=np.float32)
        idx = np.flatnonzero(self.has_photo)
        v = rng.normal(size=(idx.size, self.vecs.shape[1])).astype(np.float32)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
        out[idx] = v
        return out

    def null_packed(self, *, seed: int | None = None) -> Packed:
        """MaxSim 대조군 — 개수 구조는 그대로, 벡터만 난수. 메뉴·리뷰와 같은 함수."""
        return random_unit_packed(
            self.packed,
            _seed_int(self.seed if seed is None else seed, NULL_SEED_SALT, self.kind,
                      "maxsim"),
            label="(난수 벡터)")

    # ── 캐시 키
    def fingerprint(self, repr_name: str) -> str:
        """임베딩 산출물(sha256) + 표현 이름 + 풀 구성 + seed."""
        h = hashlib.blake2b(digest_size=12)
        h.update(self.manifest_sha.encode("utf-8"))
        h.update(f"|photo|{self.kind}|{repr_name}|seed={self.seed}|"
                 f"{len(self.store_ids)}|".encode("utf-8"))
        h.update(hashlib.blake2b("|".join(self.store_ids).encode("utf-8"),
                                 digest_size=8).digest())
        return h.hexdigest()

    # ── 사례 설명
    def urls(self, i: int) -> list[str]:
        """가게 i 의 사진 url 목록 (임베딩에 실제로 쓰인 것)."""
        sl = self.packed.slice(i)
        return list(self.packed.names[sl.start:sl.stop])


def _seed_int(seed: int, *parts) -> int:
    h = hashlib.blake2b("|".join([str(seed), *[str(p) for p in parts]]).encode("utf-8"),
                        digest_size=8)
    return int.from_bytes(h.digest(), "big")


# ────────────────────────────────────────────────────────── 유사도
def single_similarity(vecs: np.ndarray) -> np.ndarray:
    """가게-가게 코사인 (S, S) float32. 사진 없는 가게는 행·열 전부 NaN.

    벡터가 L2 정규화돼 있으므로 내적이 곧 코사인이다. 요약문(`store_text`)과 **같은
    구현**이다 — modality 비교가 성립하려면 유사도의 정의가 같아야 한다.
    """
    n = vecs.shape[0]
    idx = np.flatnonzero(np.isfinite(vecs).all(axis=1))
    out = np.full((n, n), np.nan, dtype=np.float32)
    if idx.size == 0:
        return out
    V = np.asarray(vecs[idx], dtype=np.float32)
    sim = V @ V.T
    sim = (sim + sim.T) * np.float32(0.5)
    out[np.ix_(idx, idx)] = sim
    return out


#: 표현 이름 -> 사람이 읽을 설명. scorer 이름의 조각이기도 하다.
REPRS = {
    "single": "단일 벡터 코사인 (사진 벡터 평균 1개)",
    "single_null": "**대조군** 단일 벡터 — 커버리지 구조는 그대로, 벡터만 난수",
    "maxsim": "사진 집합 MaxSim (late interaction)",
    "maxsim_null": "**대조군** MaxSim — 개수 구조는 그대로, 벡터만 난수",
}


def build_similarity(space: PhotoSpace, repr_name: str, *, progress=None) -> np.ndarray:
    """표현 이름 -> 가게-가게 유사도 행렬."""
    if repr_name == "single":
        return single_similarity(space.vecs)
    if repr_name == "single_null":
        return single_similarity(space.null_vectors())
    if repr_name == "maxsim":
        return similarity_matrix(space.packed, progress=progress)
    if repr_name == "maxsim_null":
        return similarity_matrix(space.null_packed(), progress=progress)
    raise ValueError(f"모르는 사진 표현 '{repr_name}' (가능: {', '.join(REPRS)})")


def load_or_build_similarity(space: PhotoSpace, repr_name: str, *,
                             cache_dir: Path | None = None, use_cache: bool = True,
                             progress=None) -> tuple[np.ndarray, bool]:
    """(유사도 행렬, 캐시에서 읽었는지). 키는 임베딩 sha256 + 표현 + 풀 구성 + seed."""
    cache_dir = Path(cache_dir or CACHE_DIR)
    path = (cache_dir /
            f"photosim-{space.kind}-{repr_name}-{space.fingerprint(repr_name)}.npy")
    if use_cache and path.exists():
        try:
            return np.load(path), True
        except (ValueError, OSError):
            pass                                  # 깨진 캐시는 무시하고 다시 만든다
    mat = build_similarity(space, repr_name, progress=progress)
    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(path, mat)
    return mat, False


# ────────────────────────────────────────────────────────── scorer 등록
# 아래는 백테스트 하네스에 모델을 등록하는 부분이다. 하네스 쪽 모듈
# (`analysis/backtest/scorers*.py`)을 이 Step 에서 건드리지 않기로 했으므로
# (다른 작업자가 같은 파일을 만지고 있다) 등록을 표현 코드와 같은 파일에 둔다.
# `import analysis.content.photos` 를 한 프로세스에서만 이름이 살아난다 —
# 실행기는 `analysis/photos/step3b.py` 다.
from analysis.backtest import scorers_menu as _menu_scorers   # noqa: E402
from analysis.backtest.scorers import (History, ScoreContext,  # noqa: E402
                                       Scorer, register)

#: 사진 modality 가 쓰는 집계. 메뉴·리뷰·요약문과 같은 이름·같은 구현이다.
PHOTO_AGGS = ("max", "mean", "lse01")


def photo_space(ctx: ScoreContext, kind: str) -> PhotoSpace:
    """풀 전체의 사진 공간(kind 별). 실험 한 번에 kind 당 한 개만 만든다."""
    box = ctx.extras.setdefault("photos", {})
    spaces = box.setdefault("space", {})
    if kind not in spaces:
        spaces[kind] = PhotoSpace.build(kind, ctx.pool.ids, seed=ctx.seed)
    return spaces[kind]


def similarity(ctx: ScoreContext, kind: str, repr_name: str) -> np.ndarray:
    """(kind, 표현) 의 가게-가게 유사도 (캐시 → 없으면 계산 후 캐시)."""
    box = ctx.extras.setdefault("photos", {})
    sims = box.setdefault("sim", {})
    key = (kind, repr_name)
    if key not in sims:
        mat, _ = load_or_build_similarity(photo_space(ctx, kind), repr_name)
        sims[key] = mat
    return sims[key]


class PhotoScorer(Scorer):
    """사진 유사도를 이력 위에서 접는다 — 집계·NaN 규칙은 메뉴 쪽 코드를 그대로 쓴다."""

    def __init__(self, kind: str, repr_name: str, agg: str, name: str) -> None:
        self.kind, self.repr_name, self.agg, self.name = kind, repr_name, agg, name
        self.description = (f"{KIND_LABEL[kind]} 사진 · {REPRS[repr_name]} · 집계 "
                            f"{_menu_scorers.AGGS[agg][0]}")

    def prepare(self, ctx: ScoreContext) -> None:
        similarity(ctx, self.kind, self.repr_name)

    def score(self, history: History, ctx: ScoreContext, *, user_id: str) -> np.ndarray:
        # beta=0 — 이력 해석 `visit` 에는 dislike 가 없다(PLAN 5절 Step 1.5 결정 1번).
        return _menu_scorers.content_score(
            similarity(ctx, self.kind, self.repr_name), ctx.pool.pos, history,
            self.agg, 0.0)


def scorer_name(kind: str, repr_name: str, agg: str) -> str:
    """`photo_food_max`, `photo_space_maxsim_null_mean` 같은 이름 규칙."""
    mid = "" if repr_name == "single" else f"_{repr_name.replace('single_', '')}"
    return f"photo_{kind}{mid}_{agg}"


def register_all() -> list[str]:
    """모델 이름을 하네스 레지스트리에 등록하고 이름 목록을 돌려준다.

    두 번 불러도 안전하다(이미 있으면 건너뛴다) — 실행기가 import 순서를 신경 쓰지
    않아도 되게.
    """
    from analysis.backtest.scorers import REGISTRY

    names: list[str] = []
    for kind in KIND_VECTORS:
        for repr_name in REPRS:
            for agg in PHOTO_AGGS:
                name = scorer_name(kind, repr_name, agg)
                names.append(name)
                if name not in REGISTRY:
                    register(name)(lambda k=kind, r=repr_name, a=agg, n=name:
                                   PhotoScorer(k, r, a, n))
    return names


MODEL_NAMES = register_all()
