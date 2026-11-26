# -*- coding: utf-8 -*-
"""가게 요약문 표현 (PLAN 2절 '텍스트 임베딩' 의 `store_text`).

카카오가 가게마다 붙여 놓은 `ai_title + ai_summary + tags + review_tags` 를 문장 하나로
이어 붙여 임베딩한 것이다(`analysis/embed.py: compose_store_text`). 메뉴·리뷰와 달리
**가게당 벡터가 하나**라서 다중 벡터가 아니고, MaxSim 도 필요 없다 — 코사인 한 번이다.

    sim(A, B) = v_A · v_B          (벡터는 L2 정규화돼 있다)

이 modality 를 굳이 넣는 이유는 두 가지다.
  1. 메뉴명(짧은 명사)과 리뷰(사람 문장) 사이의 **중간 길이 서술**이라 실패 방식이 다를
     수 있다. Step 1 에서 bge-m3 는 짧은 메뉴명을 문자열 유사도로 다뤘다(메밀소바↔멜론소다
     0.688). 170자짜리 요약문에서도 같은 일이 벌어지는지는 별개의 질문이다.
  2. **가장 싸다.** 2,500행짜리 행렬 하나이고 가게-가게 유사도는 한 번의 행렬곱이다.

**텍스트가 없는 가게.** 2,795곳 중 요약문이 있는 곳은 2,500곳이고 후보 풀 2,216곳 중에는
2,203곳(99.4%)이다. 없는 가게의 유사도는 메뉴와 **같은 규칙으로 NaN** 이다 — 0 으로 채우면
'아무와도 안 닮은 가게' 가 되어 콘텐츠 모델이 구조적으로 밀어내는데 실제로는 '모른다' 이기
때문이다. 점수 단계에서 NaN 후보는 그 사용자의 평균 점수(중립)로 채운다
(`scorers_menu.content_score` 를 그대로 쓴다 — NaN 규칙을 두 벌 만들지 않는다).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from analysis import vectors

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "analysis" / "backtest" / "cache"


@dataclass
class StoreTextSpace:
    """후보 풀 가게 × 요약문 벡터 1개. 없는 가게는 NaN 행이다."""

    store_ids: list[str]
    vecs: np.ndarray                 # (S, D) float32, 없는 가게는 NaN
    texts: list[str]                 # (S,) 사례 설명용 원문 (없으면 "")
    manifest_sha: str = ""

    @property
    def has_text(self) -> np.ndarray:
        return np.isfinite(self.vecs).all(axis=1)

    @property
    def coverage(self) -> float:
        return float(self.has_text.mean()) if len(self.store_ids) else float("nan")

    @classmethod
    def build(cls, store_ids: list[str]) -> "StoreTextSpace":
        index, matrix = vectors.load("store_text")
        row_of = {str(r["store_id"]): int(r["row"]) for r in index}
        text_of = {str(r["store_id"]): r.get("text", "") for r in index}
        dim = matrix.shape[1]
        vecs = np.full((len(store_ids), dim), np.nan, dtype=np.float32)
        texts: list[str] = []
        for i, sid in enumerate(store_ids):
            row = row_of.get(sid)
            if row is not None:
                vecs[i] = matrix[row]
            texts.append(text_of.get(sid, ""))
        man = vectors.manifest().get("store_text", {})
        return cls(store_ids=list(store_ids), vecs=vecs, texts=texts,
                   manifest_sha=str(man.get("sha256", "")))

    def fingerprint(self) -> str:
        """캐시 키 — 임베딩 산출물(sha256) + 후보 풀 구성."""
        h = hashlib.blake2b(digest_size=12)
        h.update(self.manifest_sha.encode("utf-8"))
        h.update(f"|store_text|{len(self.store_ids)}|".encode("utf-8"))
        h.update(hashlib.blake2b("|".join(self.store_ids).encode("utf-8"),
                                 digest_size=8).digest())
        return h.hexdigest()

    def snippet(self, i: int, limit: int = 60) -> str:
        """사례 설명용 요약문 앞부분."""
        t = " ".join((self.texts[i] or "").split())
        return t[:limit] + ("…" if len(t) > limit else "")


def similarity_matrix(space: StoreTextSpace) -> np.ndarray:
    """가게-가게 코사인 (S, S) float32. 요약문이 없는 가게는 행·열 전부 NaN.

    벡터가 L2 정규화돼 있으므로 내적이 곧 코사인이다. 대칭성은 정의상 보장되지만
    행렬곱의 부동소수 오차로 마지막 자리가 어긋날 수 있어 명시적으로 대칭화한다 —
    메뉴 MaxSim 과 같은 취급을 하기 위해서다.
    """
    n = len(space.store_ids)
    idx = np.flatnonzero(space.has_text)
    out = np.full((n, n), np.nan, dtype=np.float32)
    if idx.size == 0:
        return out
    V = np.asarray(space.vecs[idx], dtype=np.float32)
    sim = V @ V.T
    sim = (sim + sim.T) * np.float32(0.5)
    out[np.ix_(idx, idx)] = sim
    return out


def load_or_build_similarity(space: StoreTextSpace, *, cache_dir: Path | None = None,
                             use_cache: bool = True) -> tuple[np.ndarray, bool]:
    """(유사도 행렬, 캐시에서 읽었는지). 키는 임베딩 sha256 + 풀 구성."""
    cache_dir = Path(cache_dir or CACHE_DIR)
    path = cache_dir / f"textsim-{space.fingerprint()}.npy"
    if use_cache and path.exists():
        try:
            return np.load(path), True
        except (ValueError, OSError):
            pass                                  # 깨진 캐시는 무시하고 다시 만든다
    mat = similarity_matrix(space)
    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(path, mat)
    return mat, False
