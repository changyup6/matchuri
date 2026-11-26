"""임베딩 산출물 로더 (PLAN.md 2절 "로더: analysis/vectors.py").

`analysis/embed.py` 가 만든 data/vectors/*.npy 와 *.index.jsonl 을 읽는다.
행렬은 mmap 으로 열기 때문에 실제로 건드린 행만 메모리에 올라온다
(리뷰 22,686 x 1,024 float32 = 93MB — 다 올려도 되지만 여러 kind 를 동시에 쓰면 아깝다).

사용 예:
    from analysis.vectors import load, manifest

    index, mat = load("menus")          # index[i] 가 mat[i] 행의 출처
    mat[index[0]["row"]]                # row 는 항상 i 와 같다 (자기기술용)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
VECTOR_DIR = REPO_ROOT / "data" / "vectors"

KINDS = ("menus", "reviews", "store_text")

# kind → (index_rows, matrix) 캐시. 같은 프로세스에서 여러 번 부르면 재사용한다.
_CACHE: dict[str, tuple[list[dict], np.ndarray]] = {}


def _paths(kind: str) -> tuple[Path, Path]:
    return VECTOR_DIR / f"{kind}.npy", VECTOR_DIR / f"{kind}.index.jsonl"


def manifest() -> dict:
    """data/vectors/manifest.json 을 읽는다. 없으면 빈 dict."""
    path = VECTOR_DIR / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def available() -> list[str]:
    """지금 로드 가능한 kind 목록 (.npy 와 .index.jsonl 이 둘 다 있는 것)."""
    out = []
    for kind in KINDS:
        npy, idx = _paths(kind)
        if npy.exists() and idx.exists():
            out.append(kind)
    return out


def load_index(kind: str) -> list[dict]:
    """<kind>.index.jsonl 만 읽는다. 행렬이 필요 없을 때 쓴다."""
    _, index_path = _paths(kind)
    if not index_path.exists():
        raise FileNotFoundError(
            f"{index_path} 가 없다. 먼저 `python analysis/embed.py --what {kind}` 를 돌려라"
        )
    with index_path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load(kind: str, *, mmap: bool = True, use_cache: bool = True) -> tuple[list[dict], np.ndarray]:
    """(index_rows, matrix) 를 돌려준다.

    index_rows[i] 는 matrix[i] 행의 출처를 적은 dict 다.
      menus      -> {"row", "text"}
      reviews    -> {"row", "review_id", "store_id", "author_id", "rating"}  (본문은 reviews.jsonl 에)
      store_text -> {"row", "store_id", "text"}

    mmap=True 면 행렬을 읽기전용 mmap 으로 연다. 쓰기가 필요하면 mmap=False 로 부르거나
    돌려받은 배열을 np.array(...) 로 복사해서 쓴다.
    """
    if kind not in KINDS:
        raise ValueError(f"알 수 없는 kind: {kind!r} (가능: {', '.join(KINDS)})")
    if use_cache and kind in _CACHE:
        return _CACHE[kind]

    npy_path, _ = _paths(kind)
    if not npy_path.exists():
        raise FileNotFoundError(
            f"{npy_path} 가 없다. 먼저 `python analysis/embed.py --what {kind}` 를 돌려라"
        )

    index = load_index(kind)
    matrix = np.load(npy_path, mmap_mode="r" if mmap else None)

    if matrix.shape[0] != len(index):
        raise ValueError(
            f"{kind}: 행렬 {matrix.shape[0]}행 vs 인덱스 {len(index)}줄 — 산출물이 어긋났다. "
            f"해당 kind 를 다시 임베딩해라"
        )

    result = (index, matrix)
    if use_cache:
        _CACHE[kind] = result
    return result


def clear_cache() -> None:
    """캐시를 비운다 (임베딩을 다시 돌린 뒤 같은 프로세스에서 재로드할 때)."""
    _CACHE.clear()


if __name__ == "__main__":  # 간단 점검용
    man = manifest()
    print(json.dumps(man, ensure_ascii=False, indent=2) if man else "manifest.json 없음")
    for kind in available():
        index, mat = load(kind)
        norms = np.linalg.norm(np.asarray(mat[: min(1000, len(mat))]), axis=1)
        print(f"{kind}: {mat.shape} dtype={mat.dtype} "
              f"norm[min={norms.min():.4f} max={norms.max():.4f}] index={len(index)}줄")
