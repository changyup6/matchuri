# -*- coding: utf-8 -*-
"""가게의 메뉴 다중 벡터 표현 (PLAN 2절 '메뉴 다중 벡터 (Step 1)').

가게를 **하나의 벡터로 평균내지 않는다.** 메뉴명+실제 설명 임베딩 K 개를 집합으로 들고,
가게-가게 유사도를 late interaction(MaxSim)으로 잰다.

    MaxSim(A→B) = Σ_{a∈A} w_a · max_{b∈B} a·b / Σ_{a∈A} w_a
    sim(A, B)   = (MaxSim(A→B) + MaxSim(B→A)) / 2

메뉴 벡터는 L2 정규화돼 있으므로 내적이 곧 코사인이다.

**범용 메뉴.** 아메리카노(364곳)·콜라·공기밥이 모든 가게를 서로 닮게 만든다. PLAN 5절
4번의 결정대로 stoplist 대신 IDF 가중을 쓴다 — `idf(m) = log(S_pool / df(m))`,
df 는 그 이름을 올린 **후보 풀 가게 수**. 임계값을 손으로 정하지 않아도 되고,
카페처럼 음료가 곧 메뉴인 업종을 특례 없이 다룰 수 있다(카페끼리는 아메리카노가
서로 상쇄되고 남는 것으로 비교된다).

**메뉴 선택.** `menu_index` 오름차순으로 앞에서 K 개(같은 이름·설명은 한 번만). 카카오의
노출 순서가 대개 대표 메뉴 먼저이기 때문이다. 화면의 `store.get_menus()` 도
원본 순서를 따른다. `analysis.backtest.data` 의 가격 특성은 서버의
`store.get_feature_menus()` 와 같이 기존 음식·인기·추천 우선순위를 유지한다.
이 모듈의 임베딩용 메뉴 선택 계산은 기존 PLAN 규칙을 유지한다.

**메뉴가 없는 가게.** 전체 2,795곳 중 메뉴가 있는 곳은 2,249곳뿐이라 후보 풀에도
메뉴 없는 가게가 남는다. 그런 가게의 유사도는 **NaN** 으로 둔다. 0 으로 채우면
'아무와도 안 닮은 가게' 가 되어 콘텐츠 모델이 구조적으로 밀어내는데, 실제로는
'모른다' 이기 때문이다. 점수를 낼 때 NaN 은 그 사용자의 평균 점수(중립)로 채운다.

**난수 벡터 대조군**(Step 3a, PLAN 7절 결정 1번). 집합형 표현의 MaxSim 점수에는
'내용' 말고 **집합 크기**의 몫이 섞여 있다. 메뉴 개수 구조는 그대로 두고 벡터만 난수
단위벡터로 바꾼 `null_packed` 가 그 몫을 그대로 남기므로, 진짜 벡터가 대조군을 넘지
못하면 그 modality 의 점수는 내용의 증거가 아니다. 리뷰(`reviews.py`)와 **같은 함수**
(`random_unit_packed`)를 쓴다.
"""
from __future__ import annotations

import collections
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from analysis import vectors
from analysis.menu_input import menu_input

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "kakao"
CACHE_DIR = ROOT / "analysis" / "backtest" / "cache"

#: 결과 .md 에 적을 '가장 범용적인 메뉴명' 개수.
GENERIC_TOP = 30


@dataclass(frozen=True)
class Variant:
    """비교할 표현 변종. K 와 IDF 가중 여부만 다르다."""
    name: str
    k: int
    idf: bool

    @property
    def description(self) -> str:
        return (f"메뉴 상위 {self.k}개(menu_index 순)"
                + (" · IDF 가중" if self.idf else " · 무가중"))


VARIANTS: dict[str, Variant] = {
    "top8": Variant("top8", 8, False),
    "top8_idf": Variant("top8_idf", 8, True),
    "top20_idf": Variant("top20_idf", 20, True),
}


def read_menu_names(data_dir: Path | None = None, *,
                    described: bool = False) -> dict[str, list[str]]:
    """store_id -> 입력 키 목록 (원본 menu_index 순, 같은 키는 첫 번째만).

    파일 순서를 2차 키로 써서 menu_index 가 같아도 실행마다 같은 목록이 나온다.
    described=False는 기존 이름 전용 벡터 산출물을 읽기 위한 호환 경로다.
    """
    data_dir = Path(data_dir or DATA_DIR)
    rows: dict[str, list[tuple[int, int, str]]] = collections.defaultdict(list)
    with (data_dir / "menus.jsonl").open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid, item = m.get("store_id"), menu_input(m)
            if not sid or not item.name:
                continue
            name = item.key if described else item.name
            try:
                idx = int(m.get("menu_index") or 0)
            except (TypeError, ValueError):
                idx = 0
            rows[sid].append((idx, line_no, name))

    out: dict[str, list[str]] = {}
    for sid, items in rows.items():
        items.sort()
        seen: set[str] = set()
        names = []
        for _, _, name in items:
            if name not in seen:
                seen.add(name)
                names.append(name)
        out[sid] = names
    return out


@dataclass
class Packed:
    """가게별 메뉴 벡터를 하나로 이어 붙인 ragged 배열.

    `vecs[starts[i]:starts[i]+counts[i]]` 가 가게 i 의 메뉴 벡터다. counts[i]==0 이면
    메뉴가 없는 가게(유사도 NaN 대상).
    """
    starts: np.ndarray          # (S,) int64
    counts: np.ndarray          # (S,) int64
    vecs: np.ndarray            # (M, D) float32, L2 정규화됨
    weights: np.ndarray         # (M,) float32
    names: list[str]            # (M,) 행 -> 메뉴명 (사례 설명용)

    @property
    def has_menu(self) -> np.ndarray:
        return self.counts > 0

    def slice(self, i: int) -> slice:
        return slice(int(self.starts[i]), int(self.starts[i] + self.counts[i]))


def random_unit_packed(base: Packed, seed: int,
                       label: str = "(난수 벡터)") -> Packed:
    """개수·가중치 구조는 그대로 두고 벡터만 난수 단위벡터로 바꾼 묶음.

    집합형 modality 의 **난수 벡터 대조군**(PLAN 7절 결정 1번)이 쓰는 한 곳이다.
    메뉴와 리뷰가 같은 함수를 쓰는 것이 중요하다 — 대조군의 정의가 modality 마다
    다르면 "메뉴는 살아남고 리뷰는 죽었다" 는 비교가 성립하지 않는다.
    """
    rng = np.random.default_rng(seed)
    vecs = rng.normal(size=base.vecs.shape).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.maximum(norms, 1e-12)
    return Packed(starts=base.starts, counts=base.counts, vecs=vecs,
                  weights=base.weights, names=[label] * len(base.names))


class MenuSpace:
    """후보 풀 가게 × 메뉴 입력 벡터. 변종별 packing 과 IDF 를 들고 있다."""

    def __init__(self, store_ids: list[str], names: dict[str, list[str]],
                 index: list[dict], matrix: np.ndarray,
                 manifest_sha: str = "", seed: int = 0) -> None:
        self.store_ids = list(store_ids)
        self.names = names
        self.matrix = matrix
        self.manifest_sha = manifest_sha
        self.seed = seed
        self.row_of: dict[str, int] = {r.get("key", r["text"]): int(r["row"]) for r in index}
        self._row_name: list[str] = [r.get("menu_name", r["text"]) for r in index]

        # df = 그 이름을 올린 **후보 풀 가게 수**. 풀 밖 가게는 세지 않는다 —
        # 가중치가 후보 풀 안에서의 변별력을 뜻해야 하기 때문이다.
        df: collections.Counter = collections.Counter()
        for sid in self.store_ids:
            for name in set(self.names.get(sid, ())):
                df[name] += 1
        self.df = df
        s_pool = float(len(self.store_ids))
        self.idf = {name: float(np.log(s_pool / d)) for name, d in df.items()}
        self._packed: dict[str, Packed] = {}

    # ── 구축
    @classmethod
    def build(cls, store_ids: list[str], *, data_dir: Path | None = None,
              seed: int = 0) -> "MenuSpace":
        index, matrix = vectors.load("menus")
        man = vectors.manifest().get("menus", {})
        described = any("key" in row for row in index)
        names = read_menu_names(data_dir, described=described)
        if described:
            if any("key" not in row or row.get("row") != i
                   for i, row in enumerate(index)):
                raise ValueError("menu input index format/row order mismatch")
            available = {row["key"] for row in index}
            if len(available) != len(index):
                raise ValueError("duplicate menu input keys in vector index")
            required = {name for sid in store_ids for name in names.get(sid, ())}
            missing = required - available
            if missing:
                raise ValueError(f"menu vector index misses {len(missing)} source input keys; "
                                 "rebuild vectors for this menu source")
        return cls(store_ids, names, index, matrix,
                   manifest_sha=str(man.get("sha256", "")), seed=seed)

    # ── 조회
    def matrix_name(self, row: int) -> str:
        """행 번호 -> 메뉴명 (임베딩 공간을 직접 찔러볼 때 쓴다)."""
        return self._row_name[int(row)]

    def selection(self, variant: Variant, store_id: str) -> list[str]:
        """그 가게가 쓰는 입력 키 — 벡터가 있는 것만 원본 순서로 앞에서 K 개."""
        out = []
        for name in self.names.get(store_id, ()):
            if name in self.row_of:
                out.append(name)
                if len(out) >= variant.k:
                    break
        return out

    def generic_names(self, n: int = GENERIC_TOP) -> list[tuple[str, int, float]]:
        """가장 범용적인(df 가 큰) 메뉴명 n 개 — (이름, df, idf)."""
        return [(self._row_name[self.row_of[name]] if name in self.row_of else name,
                 d, self.idf[name]) for name, d in self.df.most_common(n)]

    def packed(self, variant: Variant) -> Packed:
        """변종의 ragged 벡터 묶음. 같은 변종은 한 번만 만든다."""
        key = variant.name
        if key in self._packed:
            return self._packed[key]

        rows: list[int] = []
        weights: list[float] = []
        names: list[str] = []
        counts = np.zeros(len(self.store_ids), dtype=np.int64)
        for i, sid in enumerate(self.store_ids):
            picked = self.selection(variant, sid)
            w = [self.idf[n] for n in picked] if variant.idf else [1.0] * len(picked)
            # IDF 가 전부 0 인 가게(모든 메뉴가 풀 전체에 있는 극단)는 무가중으로 되돌린다.
            if picked and sum(w) <= 0:
                w = [1.0] * len(picked)
            rows.extend(self.row_of[n] for n in picked)
            weights.extend(w)
            names.extend(self._row_name[self.row_of[name]] for name in picked)
            counts[i] = len(picked)

        starts = np.zeros(len(counts), dtype=np.int64)
        np.cumsum(counts[:-1], out=starts[1:])
        vecs = (np.asarray(self.matrix[np.asarray(rows, dtype=np.int64)],
                           dtype=np.float32)
                if rows else np.zeros((0, self.matrix.shape[1]), dtype=np.float32))
        packed = Packed(starts=starts, counts=counts, vecs=vecs,
                        weights=np.asarray(weights, dtype=np.float32), names=names)
        self._packed[key] = packed
        return packed

    def null_packed(self, variant: Variant, *, seed: int | None = None) -> Packed:
        """**대조군** — 같은 개수·가중치 구조에 난수 단위벡터를 채운 묶음.

        Step 2 가 리뷰에서 배운 것(PLAN 7절 결정 1번: "집합형 modality 에는 난수 벡터
        대조군을 의무로 붙인다")을 메뉴에도 적용한다. MaxSim 의 `max_{b∈B}` 는 B 가
        클수록 커지므로, 메뉴가 8개인 가게는 2개인 가게보다 **내용과 무관하게** 점수가
        높다. 벡터만 난수로 바꾸고 개수 구조를 그대로 두면 그 몫이 남으므로, 이
        대조군의 성능이 곧 '내용을 하나도 안 보고 개수만으로 얻는 점수' 다.
        """
        key = f"__null__{variant.name}"
        if key in self._packed:
            return self._packed[key]
        packed = random_unit_packed(self.packed(variant),
                                    self.seed if seed is None else seed)
        self._packed[key] = packed
        return packed

    def fingerprint(self, variant: Variant, *, null: bool = False) -> str:
        """캐시 키 — 임베딩 sha256 + 변종 + 후보 풀 + 선택 메뉴 입력/순서.

        난수 대조군일 때만 seed 를 추가한다.
        """
        h = hashlib.blake2b(digest_size=12)
        h.update(self.manifest_sha.encode("utf-8"))
        h.update(f"|{variant.name}|k={variant.k}|idf={variant.idf}|".encode("utf-8"))
        if null:
            h.update(f"null|seed={self.seed}|".encode("utf-8"))
        h.update(str(len(self.store_ids)).encode("utf-8"))
        h.update(hashlib.blake2b("|".join(self.store_ids).encode("utf-8"),
                                 digest_size=8).digest())
        # Identical vector files can be assigned to different stores or appear
        # in a different source menu order. Those are different MaxSim inputs.
        h.update(json.dumps([self.selection(variant, sid) for sid in self.store_ids],
                            ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        return h.hexdigest()


# ────────────────────────────────────────────────────────── MaxSim
def maxsim_directed(packed: Packed, a: int, b: int) -> float:
    """MaxSim(A→B) 한 쌍. 테스트와 사례 설명이 쓰는 참조 구현(느리다)."""
    va, vb = packed.vecs[packed.slice(a)], packed.vecs[packed.slice(b)]
    if va.shape[0] == 0 or vb.shape[0] == 0:
        return float("nan")
    w = packed.weights[packed.slice(a)]
    best = (va @ vb.T).max(axis=1)
    return float((w * best).sum() / w.sum())


def similarity_matrix(packed: Packed, *, chunk_rows: int = 4096,
                      progress=None) -> np.ndarray:
    """대칭화한 가게-가게 유사도 (S, S) float32. 메뉴 없는 가게는 행·열 전부 NaN.

    A 쪽 메뉴를 `chunk_rows` 행씩 잘라 전체 메뉴와 한 번에 내적하고, 열 방향으로
    가게 경계마다 max 를 접는다(`np.maximum.reduceat`). 청크 하나가 쓰는 임시 메모리는
    `chunk_rows × 전체메뉴수 × 4B` — 4,096행 × 4만 메뉴면 650MB 정도다.
    """
    n = len(packed.counts)
    has = packed.has_menu
    idx = np.flatnonzero(has)                     # 메뉴가 있는 가게만 계산한다
    if idx.size == 0:
        return np.full((n, n), np.nan, dtype=np.float32)

    starts = packed.starts[idx]
    counts = packed.counts[idx]
    # 계산용으로 다시 촘촘하게 이어 붙인다 (reduceat 은 빈 구간을 다룰 수 없다).
    take = np.concatenate([np.arange(s, s + c) for s, c in zip(starts, counts)])
    V = packed.vecs[take]
    W = packed.weights[take]
    dense_counts = counts
    dense_starts = np.zeros(idx.size, dtype=np.int64)
    np.cumsum(dense_counts[:-1], out=dense_starts[1:])
    w_sum = np.maximum(np.add.reduceat(W, dense_starts), 1e-12)

    directed = np.zeros((idx.size, idx.size), dtype=np.float32)
    m_total = V.shape[0]
    row = 0
    while row < m_total:
        stop = min(row + chunk_rows, m_total)
        # 이 청크가 걸친 가게 범위
        lo = int(np.searchsorted(dense_starts, row, side="right") - 1)
        hi = int(np.searchsorted(dense_starts, stop - 1, side="right"))
        # 가게 경계에서 자른다 — 한 가게의 메뉴가 두 청크로 쪼개지지 않게.
        stop = int(dense_starts[hi]) if hi < idx.size else m_total
        block = V[row:stop] @ V.T                                    # (r, M)
        best = np.maximum.reduceat(block, dense_starts, axis=1)      # (r, S')
        del block
        for j in range(lo, hi):
            s = int(dense_starts[j]) - row
            e = s + int(dense_counts[j])
            directed[j] = (W[dense_starts[j]:dense_starts[j] + dense_counts[j]]
                           @ best[s:e]) / w_sum[j]
        del best
        if progress is not None:
            progress(stop, m_total)
        row = stop

    sym = (directed + directed.T) * np.float32(0.5)
    out = np.full((n, n), np.nan, dtype=np.float32)
    out[np.ix_(idx, idx)] = sym
    return out


def mean_vectors(packed: Packed) -> np.ndarray:
    """대조군용 단일 벡터 — 가게 메뉴 벡터의 가중 평균을 L2 정규화. 없으면 NaN 행."""
    n = len(packed.counts)
    dim = packed.vecs.shape[1] if packed.vecs.size else 1
    out = np.full((n, dim), np.nan, dtype=np.float32)
    for i in range(n):
        if packed.counts[i] == 0:
            continue
        sl = packed.slice(i)
        w = packed.weights[sl].astype(np.float32)
        v = (w[:, None] * packed.vecs[sl]).sum(axis=0)
        norm = float(np.linalg.norm(v))
        out[i] = v / norm if norm > 0 else 0.0
    return out


def best_menu_pair(packed: Packed, a: int, b: int) -> tuple[str, str, float]:
    """두 가게에서 가장 잘 맞는 메뉴 한 쌍 — (A 메뉴, B 메뉴, 코사인). 사례 설명용."""
    sa, sb = packed.slice(a), packed.slice(b)
    if packed.counts[a] == 0 or packed.counts[b] == 0:
        return ("", "", float("nan"))
    d = packed.vecs[sa] @ packed.vecs[sb].T
    i, j = np.unravel_index(int(np.argmax(d)), d.shape)
    return (packed.names[sa.start + int(i)], packed.names[sb.start + int(j)],
            float(d[i, j]))


# ────────────────────────────────────────────────────────── 캐시
def load_or_build_similarity(space: MenuSpace, variant: Variant, *,
                             cache_dir: Path | None = None, use_cache: bool = True,
                             null: bool = False,
                             progress=None) -> tuple[np.ndarray, bool]:
    """(유사도 행렬, 캐시에서 읽었는지). 키는 임베딩 sha256 + 변종 + 풀 구성.

    `null=True` 면 벡터를 난수로 바꾼 대조군 행렬이다(`MenuSpace.null_packed`).
    """
    cache_dir = Path(cache_dir or CACHE_DIR)
    tag = f"null_{variant.name}" if null else variant.name
    path = cache_dir / f"menusim-{tag}-{space.fingerprint(variant, null=null)}.npy"
    if use_cache and path.exists():
        try:
            return np.load(path), True
        except (ValueError, OSError):
            pass                                  # 깨진 캐시는 무시하고 다시 만든다
    packed = space.null_packed(variant) if null else space.packed(variant)
    mat = similarity_matrix(packed, progress=progress)
    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(path, mat)
    return mat, False
