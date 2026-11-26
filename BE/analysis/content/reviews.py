# -*- coding: utf-8 -*-
"""가게의 리뷰 다중 벡터 표현 (PLAN 2절 '리뷰 다중 벡터 (Step 2)').

가게를 **하나의 벡터로 평균내지 않는다.** 리뷰 임베딩 K ≤ 12개를 집합으로 들고,
가게-가게 유사도를 메뉴와 **완전히 같은 late interaction(MaxSim)** 으로 잰다.

    MaxSim(A→B) = mean_{a∈A} max_{b∈B} a·b        (리뷰는 가중치가 전부 1)
    sim(A, B)   = (MaxSim(A→B) + MaxSim(B→A)) / 2

행렬을 만드는 코드는 `analysis.content.menus.similarity_matrix` 를 그대로 쓴다 —
"메뉴와 똑같이" 라는 말이 코드로도 참이어야 modality 비교가 성립한다. 리뷰 쪽에서
새로 정하는 것은 **어떤 리뷰 12개를 고르는가** 뿐이다.

**누수 규칙(PLAN 1절).** 평가 사용자의 리뷰는 **전부** 제외한다. 이력 구간이든 미래
구간이든, 후보 풀 밖 가게에 남긴 것이든 상관없다. 평가 사용자가 쓴 문장이 가게 표현에
한 줄이라도 들어가면 그 사용자의 취향으로 그 사용자의 정답을 맞히게 된다. 제외는
**여기 한 곳**(`ReviewSpace.build` 의 `excluded_authors`)에서만 일어나고,
`tests/test_review_content.py` 가 선택된 review_id 에 평가 사용자의 것이 하나도 없음을
검증한다.

**선택 변종 3종.** '표현력 있는 리뷰' 를 사람이 라벨링한 것은 아직 0건이라(PLAN 2절)
휴리스틱을 셋 두고 백테스트로 고른다.
  `longest12`  본문이 긴 순. 길이 = 구체성이라는 가정.
  `random12`   가게마다 결정적 난수로 12개. 길이 가정이 맞는지 보는 대조군.
  `recent12`   최근 순. 가게는 변한다(주방장·메뉴가 바뀐다)는 가정.

**임베딩 대상.** `analysis/embed.py: build_reviews` 는 본문 20자 이상 + 동일 본문 중복
제거를 거친 22,686건만 임베딩했다. 그래서 여기서 '쓸 수 있는 리뷰' 는 그 22,686건 중
후보 풀 가게의 것이고, 거기서 다시 평가 사용자를 뺀 것이다. 0건인 가게는 유사도가
**NaN** 이다 — 메뉴와 같은 규칙이다(0 으로 채우면 '아무와도 안 닮은 가게' 가 된다).
"""
from __future__ import annotations

import collections
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from analysis import vectors
from analysis.backtest.rng import generator
from analysis.content.menus import (CACHE_DIR, Packed, best_menu_pair, mean_vectors,
                                    random_unit_packed, similarity_matrix)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "kakao"

#: 가게당 리뷰 벡터 상한 (PLAN 2절 "표현력 있는 리뷰 ≤ 12개").
MAX_REVIEWS = 12

#: 사례 설명에 쓸 리뷰 조각의 길이.
SNIPPET = 60

# 리뷰 쌍 설명은 메뉴와 같은 함수를 쓴다 — Packed 구조가 같기 때문이다.
best_review_pair = best_menu_pair


@dataclass(frozen=True)
class Variant:
    """비교할 리뷰 선택 변종. 고르는 **순서**만 다르고 K 는 같다."""
    name: str
    k: int
    order: str                       # longest | random | recent

    @property
    def description(self) -> str:
        how = {"longest": "본문이 긴 순", "random": "결정적 난수",
               "recent": "최근 날짜 순"}[self.order]
        return f"리뷰 {self.k}개 ({how})"


VARIANTS: dict[str, Variant] = {
    "longest12": Variant("longest12", MAX_REVIEWS, "longest"),
    "random12": Variant("random12", MAX_REVIEWS, "random"),
    "recent12": Variant("recent12", MAX_REVIEWS, "recent"),
}


@dataclass(frozen=True)
class ReviewRow:
    """임베딩 행 하나의 메타. 본문은 스니펫만 들고 있는다(원문은 reviews.jsonl 에)."""
    row: int
    review_id: int
    store_id: str
    author_id: str
    rating: int
    date: str
    length: int
    snippet: str


def read_review_meta(data_dir: Path | None = None) -> dict[int, tuple[str, int, str]]:
    """review_id -> (date, 본문 길이, 스니펫). 임베딩 인덱스에 없는 것도 함께 읽는다.

    인덱스에는 식별자만 있어(본문은 원본에 있다) 길이·날짜를 여기서 가져온다.
    """
    data_dir = Path(data_dir or DATA_DIR)
    out: dict[int, tuple[str, int, str]] = {}
    with (data_dir / "reviews.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = r.get("review_id")
            if rid is None:
                continue
            content = r.get("content") or ""
            flat = " ".join(content.split())
            out[int(rid)] = (str(r.get("date") or ""), len(content),
                             flat[:SNIPPET] + ("…" if len(flat) > SNIPPET else ""))
    return out


def collect_rows(index: list[dict], store_ids: list[str],
                 excluded_authors: set[str],
                 meta: dict[int, tuple[str, int, str]]) -> dict[str, list[ReviewRow]]:
    """임베딩 인덱스 -> 가게별 리뷰 행 목록. **누수 규칙이 적용되는 유일한 곳이다.**

    후보 풀 밖 가게와 평가 사용자(`excluded_authors`)의 행은 여기서 사라지고, 이후
    어떤 코드도 그것들을 다시 볼 수 없다. 클래스 밖 함수로 둔 것은 임베딩 파일 없이도
    테스트가 이 규칙을 직접 찌를 수 있게 하기 위해서다.
    """
    pool = set(store_ids)
    excluded = set(excluded_authors)
    out: dict[str, list[ReviewRow]] = collections.defaultdict(list)
    for r in index:
        sid = str(r.get("store_id"))
        author = str(r.get("author_id"))
        if sid not in pool or author in excluded:
            continue
        rid = r.get("review_id")
        if rid is None:
            continue
        date, length, snippet = meta.get(int(rid), ("", 0, ""))
        out[sid].append(ReviewRow(
            row=int(r["row"]), review_id=int(rid), store_id=sid, author_id=author,
            rating=int(r.get("rating") or 0), date=date, length=length,
            snippet=snippet))
    # 기준 정렬을 고정한다 — 변종의 정렬 키가 동점일 때 실행마다 같은 답이 나오게.
    for rows in out.values():
        rows.sort(key=lambda x: x.row)
    return dict(out)


class ReviewSpace:
    """후보 풀 가게 × 리뷰 벡터. 평가 사용자의 리뷰는 **들어오지 않는다.**"""

    def __init__(self, store_ids: list[str], rows_by_store: dict[str, list[ReviewRow]],
                 matrix: np.ndarray, *, excluded_authors: set[str] | None = None,
                 manifest_sha: str = "", seed: int = 0) -> None:
        self.store_ids = list(store_ids)
        self.rows_by_store = rows_by_store
        self.matrix = matrix
        self.excluded_authors = set(excluded_authors or ())
        self.manifest_sha = manifest_sha
        self.seed = seed
        self._packed: dict[str, Packed] = {}

    # ── 구축
    @classmethod
    def build(cls, store_ids: list[str], excluded_authors: set[str], *,
              data_dir: Path | None = None, seed: int = 0) -> "ReviewSpace":
        """임베딩 산출물 + 원본 메타를 합쳐 가게별 리뷰 후보 목록을 만든다.

        `excluded_authors` 는 평가 사용자 전원이다. 이 함수 밖에서는 어떤 필터도
        하지 않는다 — 제외 집합을 두 군데서 만들면 언젠가 어긋난다.
        """
        index, matrix = vectors.load("reviews")
        rows_by_store = collect_rows(index, store_ids, excluded_authors,
                                     read_review_meta(data_dir))
        man = vectors.manifest().get("reviews", {})
        return cls(store_ids, rows_by_store, matrix,
                   excluded_authors=set(excluded_authors),
                   manifest_sha=str(man.get("sha256", "")), seed=seed)

    # ── 선택
    def candidates(self, store_id: str) -> list[ReviewRow]:
        """그 가게에서 쓸 수 있는 리뷰 전부(제외 규칙 적용 뒤). 정렬은 행 번호 순."""
        return self.rows_by_store.get(store_id, [])

    def selection(self, variant: Variant, store_id: str) -> list[ReviewRow]:
        """변종이 고른 리뷰 ≤ K 개.

        `random` 은 **가게마다 결정적인** 난수다(seed + 가게 id). 실행마다 다른 12개를
        고르면 실험을 재현할 수 없다.
        """
        rows = self.candidates(store_id)
        if not rows:
            return []
        if variant.order == "longest":
            picked = sorted(rows, key=lambda x: (-x.length, x.review_id))
        elif variant.order == "recent":
            picked = sorted(rows, key=lambda x: (x.date, x.review_id), reverse=True)
        elif variant.order == "random":
            order = generator(self.seed, "reviews", variant.name, store_id).permutation(
                len(rows))
            picked = [rows[int(i)] for i in order]
        else:
            raise ValueError(f"모르는 선택 순서 '{variant.order}'")
        return picked[:variant.k]

    def used_review_ids(self, variant: Variant) -> set[int]:
        """실제로 벡터로 쓰인 review_id 전부 — 누수 테스트가 이걸 본다."""
        return {r.review_id for sid in self.store_ids
                for r in self.selection(variant, sid)}

    def counts(self, variant: Variant) -> np.ndarray:
        return np.array([len(self.selection(variant, sid)) for sid in self.store_ids],
                        dtype=np.int64)

    def usable_stats(self) -> dict:
        """후보 풀 가게의 '쓸 수 있는 리뷰' 분포 — 0 / <12 / ≥12 를 결과에 적는다."""
        n = np.array([len(self.candidates(sid)) for sid in self.store_ids])
        return {"stores": int(n.size),
                "zero": int((n == 0).sum()),
                "under_k": int(((n > 0) & (n < MAX_REVIEWS)).sum()),
                "at_least_k": int((n >= MAX_REVIEWS).sum()),
                "mean": float(n.mean()) if n.size else float("nan"),
                "median": float(np.median(n)) if n.size else float("nan"),
                "max": int(n.max()) if n.size else 0,
                "total_used": int(np.minimum(n, MAX_REVIEWS).sum())}

    # ── 벡터 묶음
    def packed(self, variant: Variant) -> Packed:
        """변종의 ragged 벡터 묶음. 메뉴와 같은 자료구조라 MaxSim 코드를 공유한다.

        `names` 자리에는 리뷰 스니펫을 넣는다 — `best_review_pair` 가 사례 설명에서
        '어떤 문장끼리 맞았나' 를 그대로 뽑아 쓸 수 있게.
        """
        if variant.name in self._packed:
            return self._packed[variant.name]

        rows: list[int] = []
        names: list[str] = []
        counts = np.zeros(len(self.store_ids), dtype=np.int64)
        for i, sid in enumerate(self.store_ids):
            picked = self.selection(variant, sid)
            rows.extend(r.row for r in picked)
            names.extend(r.snippet for r in picked)
            counts[i] = len(picked)

        starts = np.zeros(len(counts), dtype=np.int64)
        np.cumsum(counts[:-1], out=starts[1:])
        vecs = (np.asarray(self.matrix[np.asarray(rows, dtype=np.int64)],
                           dtype=np.float32)
                if rows else np.zeros((0, self.matrix.shape[1]), dtype=np.float32))
        packed = Packed(starts=starts, counts=counts, vecs=vecs,
                        weights=np.ones(len(rows), dtype=np.float32), names=names)
        self._packed[variant.name] = packed
        return packed

    def null_packed(self, variant: Variant, *, seed: int | None = None) -> Packed:
        """**대조군** — 같은 개수 구조에 난수 단위벡터를 채운 묶음.

        MaxSim 의 `max_{b∈B}` 는 B 가 클수록 커진다. 리뷰가 12개인 가게는 1개인
        가게보다 (내용과 무관하게) 최댓값이 높게 나오고, 리뷰 수는 곧 `popularity`
        다 — 즉 **리뷰 MaxSim 의 점수 일부는 인기의 다른 이름**일 수 있다. 벡터만
        난수로 바꾸고 개수 구조를 그대로 두면 그 몫이 그대로 남으므로, 이 대조군의
        성능이 '내용을 하나도 안 보고 개수만으로 얻는 점수' 다.
        """
        key = f"__null__{variant.name}"
        if key in self._packed:
            return self._packed[key]
        packed = random_unit_packed(self.packed(variant),
                                    self.seed if seed is None else seed)
        self._packed[key] = packed
        return packed

    def fingerprint(self, variant: Variant, *, null: bool = False) -> str:
        """캐시 키 — 리뷰 임베딩 sha256 + 변종 + 풀 구성 + **제외 저자 집합**.

        제외 집합이 빠지면 누수 규칙을 바꿔 놓고 옛 캐시를 쓰는 사고가 난다.
        """
        h = hashlib.blake2b(digest_size=12)
        h.update(self.manifest_sha.encode("utf-8"))
        h.update(f"|{variant.name}|k={variant.k}|order={variant.order}|"
                 f"seed={self.seed}|null={null}|".encode("utf-8"))
        h.update(str(len(self.store_ids)).encode("utf-8"))
        h.update(hashlib.blake2b("|".join(self.store_ids).encode("utf-8"),
                                 digest_size=8).digest())
        h.update(hashlib.blake2b("|".join(sorted(self.excluded_authors)).encode("utf-8"),
                                 digest_size=8).digest())
        return h.hexdigest()


def load_or_build_similarity(space: ReviewSpace, variant: Variant, *,
                             cache_dir: Path | None = None, use_cache: bool = True,
                             null: bool = False,
                             progress=None) -> tuple[np.ndarray, bool]:
    """(유사도 행렬, 캐시에서 읽었는지). 계산은 메뉴와 같은 함수가 한다.

    `null=True` 면 벡터를 난수로 바꾼 대조군 행렬이다(`ReviewSpace.null_packed`).
    """
    cache_dir = Path(cache_dir or CACHE_DIR)
    tag = "null" if null else variant.name
    path = cache_dir / f"revsim-{tag}-{space.fingerprint(variant, null=null)}.npy"
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


def store_mean_vectors(space: ReviewSpace, variant: Variant) -> np.ndarray:
    """단일 벡터 대조군 — 그 가게 리뷰 벡터의 평균을 L2 정규화. 없으면 NaN 행."""
    return mean_vectors(space.packed(variant))
