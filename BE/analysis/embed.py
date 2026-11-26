"""텍스트 임베딩 파이프라인 (PLAN.md 2절 "텍스트 임베딩", Step 1 준비).

메뉴명과 실제 설명 / 리뷰 본문 / 가게 요약문을 BAAI/bge-m3 dense 벡터
(1,024차원, L2 정규화)로 만든다.
CPU 전용(4코어, GPU 없음)이라 몇 시간짜리 작업이므로 ~50배치마다 체크포인트를 남기고
중단되면 이어서 돌린다.

사용 예:
    # 스모크 테스트 (kind 별 256건, 결과는 *.smoke.* 로 따로 저장)
    ~/venvs/recsys/bin/python analysis/embed.py --what all --limit 256

    # 전체 실행
    ~/venvs/recsys/bin/python analysis/embed.py --what all

산출물 (data/vectors/, git 에는 넣지 않는다):
    <kind>.npy          float32 (N, 1024), 행마다 L2 정규화
    <kind>.index.jsonl  행 → 원본 식별자. 한 줄이 .npy 한 행에 1:1 대응
    manifest.json       kind 별 {model, count, dim, elapsed_sec, sha256, created_at}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.menu_input import menu_input

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "kakao"
VECTOR_DIR = REPO_ROOT / "data" / "vectors"

MODEL_NAME = "BAAI/bge-m3"
KINDS = ("menus", "reviews", "store_text")

# Descriptions need more than the old 32-token name budget. Menu inputs are
# checked before encoding: an explicit shorter limit must not silently cut them.
DEFAULTS: dict[str, dict[str, int]] = {
    "menus": {"max_length": 256, "batch_size": 16},
    "reviews": {"max_length": 256, "batch_size": 16},
    "store_text": {"max_length": 256, "batch_size": 16},
}

# 리뷰 최소 길이. 이보다 짧으면 ("맛있어요" 류) 임베딩해도 변별력이 없다.
MIN_REVIEW_LEN = 20
# 가게 텍스트에 넣을 태그 개수 상한.
MAX_TAGS = 8
MAX_REVIEW_TAGS = 8


# ---------------------------------------------------------------------------
# 입력 읽기
# ---------------------------------------------------------------------------

def iter_jsonl(path: Path) -> Iterator[dict]:
    """jsonl 을 한 줄씩 dict 로 읽는다. 빈 줄은 건너뛴다."""
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _as_list(value: Any) -> list[str]:
    """리스트 필드를 문자열 리스트로 정규화한다.

    stores.jsonl 을 실제로 확인해 보면 tags 는 항상 list, review_tags 는 항상 dict 지만
    수집 스크립트가 파이썬 repr 문자열("['a', 'b']")을 흘릴 때가 있어 방어적으로 파싱한다.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith(("[", "{")):
            try:
                import ast

                return _as_list(ast.literal_eval(text))
            except (ValueError, SyntaxError):
                return [text]
        return [text]
    if isinstance(value, dict):
        return [str(k).strip() for k in value if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _as_counter(value: Any) -> dict[str, int]:
    """review_tags 를 {태그: 건수} 로 정규화한다. 건수가 없으면 0 으로 채운다."""
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            import ast

            return _as_counter(ast.literal_eval(text))
        except (ValueError, SyntaxError):
            return {}
    if isinstance(value, dict):
        out: dict[str, int] = {}
        for k, v in value.items():
            key = str(k).strip()
            if not key:
                continue
            try:
                out[key] = int(v)
            except (TypeError, ValueError):
                out[key] = 0
        return out
    return {str(k).strip(): 0 for k in _as_list(value)}


def _clean(value: Any) -> str:
    """문자열 필드를 공백 정리해서 돌려준다. None 이면 빈 문자열."""
    if not value:
        return ""
    return " ".join(str(value).split())


# ---------------------------------------------------------------------------
# kind 별 텍스트 + 인덱스 구성
# ---------------------------------------------------------------------------

def build_menus() -> tuple[list[str], list[dict]]:
    """고유 (메뉴명, 실제 설명). 설명이 다르면 같은 이름도 별도 벡터다.

    등장 순서(menus.jsonl 파일 순서)를 유지해 실행마다 같은 행 번호가 나오게 한다.
    """
    seen: dict[str, tuple[str, str]] = {}
    texts: list[str] = []
    index: list[dict] = []
    for rec in iter_jsonl(DATA_DIR / "menus.jsonl"):
        item = menu_input(rec)
        if not item.name:
            continue
        identity = (item.name, item.description)
        if item.key in seen:
            if seen[item.key] != identity:
                raise ValueError("menu input key collision; source names and described keys overlap")
            continue
        seen[item.key] = identity
        index.append({"row": len(texts), "key": item.key, "text": item.text,
                      "menu_name": item.name, "description": item.description})
        texts.append(item.text)
    return texts, index


def build_reviews() -> tuple[list[str], list[dict]]:
    """리뷰 본문. 20자 미만은 버리고, 완전히 같은 본문은 첫 건만 남긴다.

    본문 자체는 reviews.jsonl 에 이미 있으므로 인덱스에는 식별자만 적는다.
    중복 본문(복붙 리뷰)은 대표 1건의 메타데이터만 남는다.
    """
    seen: set[str] = set()
    texts: list[str] = []
    index: list[dict] = []
    for rec in iter_jsonl(DATA_DIR / "reviews.jsonl"):
        content = rec.get("content") or ""
        if len(content) < MIN_REVIEW_LEN:
            continue
        if content in seen:
            continue
        seen.add(content)
        index.append(
            {
                "row": len(texts),
                "review_id": rec.get("review_id"),
                "store_id": rec.get("store_id"),
                "author_id": rec.get("author_id"),
                "rating": rec.get("rating"),
            }
        )
        texts.append(content)
    return texts, index


def compose_store_text(rec: dict) -> str:
    """가게 하나를 문장 하나로 합친다: ai_title + ai_summary + tags + review_tags.

    review_tags 는 {태그: 건수} 라서 건수 내림차순(동점이면 태그 이름순)으로 상위 8개만 쓴다.
    네 필드가 전부 비어 있는 가게는 빈 문자열이 되고, 호출부에서 제외한다.

    ⚠ **설명(`ai_title`·`ai_summary`)이 둘 다 없는 가게는 태그만으로 문장을 만들면
    다른 가게와 글자까지 같아진다.** 태그 어휘가 작고(`특징:` 은 몇 개짜리 목록,
    `리뷰 키워드:` 는 맛·가성비·친절·분위기 정도) 정렬까지 고정이라, 업종이 전혀
    달라도 완성된 문장이 **한 글자도 다르지 않다**. 같은 문장은 같은 벡터가 되고,
    그 둘의 코사인은 **정확히 1.000** 이다. 실측(서빙 산출물 · 후보 풀 1,629곳):
    그런 쌍이 **9개** 였고 낀 가게 15곳 전부 `ai_title`·`ai_summary` 가 None 이다.

        오늘완탕(중국집)  ↔ 에슬로우커피 선릉4호점(카페)
            둘 다 tags=[] · review_tags 를 정렬하면 똑같이
            "리뷰 키워드: 가성비, 맛, 분위기, 친절"
        샬롬김밥 ↔ 명동만두
            tags 까지 같다 — ['친절한','혼밥','가성비','점심식사']
        리츠델리피자 ↔ 춘여닭숯불닭갈비 · 고메스테이크 ↔ 네네치킨 역삼1점
            둘 다 tags=['야식'] · review_tags 정렬 결과가 같다

    유사도 1.000 은 추천 4곳의 '서로 얼마나 비슷한가' 판정(`server/recsys/diversity.py`
    가 네 modality 백분위의 **최댓값**을 쓴다)을 통째로 먹어, 중국집과 커피숍이 '가장
    비슷한 조합' 으로 뽑히고 카드가 엉뚱하게 교체된다.

    **산출물을 다시 구울 때 여기를 고쳐야 한다** — 설명이 둘 다 없는 가게는 빈
    문자열을 돌려 행을 만들지 않거나(`build_store_text` 가 이미 빈 문자열을 거른다),
    태그만으로 만든 문장을 별도 취급해야 한다. 어느 쪽이 맞는지는 재 봐야 안다:
    태그 문장에도 약한 신호는 있고(야식 ↔ 야식), 그것을 버리는 비용을 잰 적이 없다.
    **여기서는 고치지 않는다** — `data/vectors/store_text.npy` 가 저장소에 없어
    (gitignore) 이 경로를 로컬에서 돌려 검증할 수 없기 때문이다. 검증할 수 없는
    변경은 남기지 않는다.

    지금 서버가 읽는 산출물은 다시 구울 수 없으므로 **불러올 때** 막는다 —
    `server/recsys/content.py: _degenerate_text_rows` 가 다른 가게와 유사도가 정확히
    1.0 인 행을 찾아 `sim_text` 의 행·열을 NaN 으로 만든다. 여기를 고쳐 다시 구우면
    그 필터는 0곳을 잡는다(무해).
    """
    parts: list[str] = []
    title = _clean(rec.get("ai_title"))
    summary = _clean(rec.get("ai_summary"))
    if title:
        parts.append(title)
    if summary:
        parts.append(summary)

    tags = _as_list(rec.get("tags"))[:MAX_TAGS]
    if tags:
        parts.append("특징: " + ", ".join(tags))

    review_tags = _as_counter(rec.get("review_tags"))
    if review_tags:
        top = sorted(review_tags.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_REVIEW_TAGS]
        parts.append("리뷰 키워드: " + ", ".join(k for k, _ in top))

    return " ".join(parts)


def build_store_text() -> tuple[list[str], list[dict]]:
    """가게당 문장 1개. 텍스트가 하나도 없는 가게는 행을 만들지 않는다."""
    texts: list[str] = []
    index: list[dict] = []
    for rec in iter_jsonl(DATA_DIR / "stores.jsonl"):
        text = compose_store_text(rec)
        if not text:
            continue
        index.append({"row": len(texts), "store_id": rec.get("store_id"), "text": text})
        texts.append(text)
    return texts, index


BUILDERS = {
    "menus": build_menus,
    "reviews": build_reviews,
    "store_text": build_store_text,
}


# ---------------------------------------------------------------------------
# 체크포인트 / 산출물
# ---------------------------------------------------------------------------

def fingerprint(kind: str, texts: list[str], model: str, max_length: int) -> str:
    """입력이 바뀌었는데 옛 부분결과를 이어붙이는 사고를 막는 지문.

    텍스트 전체 해시까지 포함하므로 데이터가 한 줄이라도 달라지면 resume 을 포기하고
    처음부터 다시 계산한다.
    """
    h = hashlib.sha256()
    h.update(f"{kind}|{model}|{max_length}|{len(texts)}\n".encode("utf-8"))
    for t in texts:
        h.update(t.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    """같은 디렉터리에 임시 파일로 쓰고 rename. 중간에 죽어도 반쪽 파일이 남지 않는다."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_manifest() -> dict:
    """manifest.json 을 dict 로 읽는다. 없거나 깨졌으면 빈 dict."""
    path = VECTOR_DIR / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def manifest_entry(key: str) -> dict | None:
    entry = read_manifest().get(key)
    return entry if isinstance(entry, dict) else None


def update_manifest(key: str, entry: dict) -> None:
    """manifest.json 의 해당 kind 항목만 갈아끼운다 (다른 kind 결과는 보존)."""
    # 깨진 manifest 때문에 몇 시간짜리 계산을 버리지 않는다 (read_manifest 가 빈 dict 로 삼킨다).
    data = read_manifest()
    data[key] = entry
    write_json_atomic(VECTOR_DIR / "manifest.json", data)


def fmt_eta(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# 임베딩 본체
# ---------------------------------------------------------------------------

def model_dim(model) -> int:
    """임베딩 차원. sentence-transformers 6.x 에서 이름이 바뀌어 둘 다 지원한다."""
    getter = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    return int(getter())


def load_model(model_name: str, threads: int, *, local_only: bool = False,
               model_path: Path | None = None):
    """sentence-transformers 모델 로드. import 가 무거워서 필요할 때만 부른다."""
    import torch

    # 4코어 중 3개만 쓴다. 1개는 OS·체크포인트 I/O 용으로 남긴다 (PLAN.md).
    torch.set_num_threads(threads)
    from sentence_transformers import SentenceTransformer

    t0 = time.time()
    model = SentenceTransformer(str(model_path or model_name), device="cpu",
                                local_files_only=local_only)
    print(f"[model] {model_name} 로드 {time.time() - t0:.1f}s "
          f"dim={model_dim(model)} threads={threads}", flush=True)
    return model


def menu_token_lengths(model, texts: list[str], max_length: int) -> list[int]:
    """Reject truncation instead of silently dropping course contents."""
    ceiling = int(model.tokenizer.model_max_length)
    if max_length > ceiling:
        raise ValueError(f"menu max_length={max_length} exceeds model limit {ceiling}")
    lengths = [len(ids) for ids in model.tokenizer(
        texts, truncation=False, padding=False)["input_ids"]]
    longest = max(lengths, default=0)
    if longest > max_length:
        raise ValueError(f"menu input needs {longest} tokens; max_length={max_length} "
                         "would truncate source descriptions; increase --max-length")
    return lengths


def menu_reuse(cache_dir: Path, texts: list[str], lengths: list[int],
               model_name: str, dim: int):
    """Reuse exact text only when the earlier token budget did not truncate it."""
    cache_dir = Path(cache_dir)
    meta = json.loads((cache_dir / "manifest.json").read_text())["menus"]
    if meta.get("model") != model_name or meta.get("dim") != dim:
        raise ValueError("menu cache model/dimension differs from the requested model")
    source = cache_dir / "menus.npy"
    if not meta.get("sha256") or sha256_file(source) != meta["sha256"]:
        raise ValueError("menu cache checksum mismatch")
    index = list(iter_jsonl(cache_dir / "menus.index.jsonl"))
    matrix = np.load(source, mmap_mode="r")
    if matrix.shape != (len(index), dim) or matrix.dtype != np.float32:
        raise ValueError("menu cache matrix/index mismatch")
    if any(row.get("row") != i for i, row in enumerate(index)):
        raise ValueError("menu cache row order mismatch")
    old = {row["text"]: i for i, row in enumerate(index)}
    token_limit = int(meta.get("max_length", 0))
    rows = {i: old[text] for i, text in enumerate(texts)
            if text in old and lengths[i] <= token_limit}
    return matrix, rows, meta["sha256"]


def embed_kind(
    kind: str,
    *,
    model,
    model_name: str,
    batch_size: int,
    max_length: int,
    limit: int | None,
    checkpoint_every: int,
    force: bool = False,
    reuse_dir: Path | None = None,
) -> dict | None:
    """kind 하나를 임베딩해 .npy / .index.jsonl / manifest 항목까지 완성한다.

    이미 같은 조건으로 끝나 있으면 건너뛰고 None 을 돌려준다 (--force 로 무시).
    """
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)

    t_build = time.time()
    texts, index = BUILDERS[kind]()
    total_all = len(texts)
    if limit is not None:
        texts = texts[:limit]
        index = index[:limit]
    n = len(texts)
    print(f"[{kind}] 텍스트 {n:,}건 (전체 {total_all:,}건) 준비 {time.time() - t_build:.1f}s", flush=True)
    if n == 0:
        raise SystemExit(f"[{kind}] 임베딩할 텍스트가 없다")
    fp = fingerprint(kind, texts, model_name, max_length)
    # The matrix may be identical while source keys change. Include the index
    # in skip/resume identity so it can never be paired with stale lookup keys.
    fp = hashlib.sha256((fp + json.dumps(index, ensure_ascii=False,
                                        sort_keys=True)).encode()).hexdigest()

    # --limit 은 스모크 테스트용이므로 본 산출물을 덮어쓰지 않도록 이름을 분리한다.
    stem = f"{kind}.smoke" if limit is not None else kind
    final_npy = VECTOR_DIR / f"{stem}.npy"
    index_path = VECTOR_DIR / f"{stem}.index.jsonl"
    partial_npy = VECTOR_DIR / f"{stem}.partial.npy"
    state_path = VECTOR_DIR / f"{stem}.partial.json"

    # ---- 이미 끝난 kind 는 건너뛴다 -----------------------------------------
    # 몇 시간짜리 작업이라 중간에 죽은 뒤 --what all 을 다시 돌려도 끝난 것을 다시 하지 않는다.
    if not force and final_npy.exists() and index_path.exists():
        prev = manifest_entry(stem)
        if (
            prev
            and prev.get("count") == n
            and prev.get("model") == model_name
            and prev.get("max_length") == max_length
            and prev.get("fingerprint") == fp
        ):
            print(f"[{kind}] 이미 완료됨 ({n:,}행) → 건너뜀 (--force 로 재실행)", flush=True)
            return None

    dim = model_dim(model)
    lengths = menu_token_lengths(model, texts, max_length) if kind == "menus" else []
    reuse_matrix, reuse_rows, reuse_sha = None, {}, None
    if reuse_dir is not None and kind == "menus":
        if Path(reuse_dir).resolve() == VECTOR_DIR.resolve():
            raise ValueError("reuse cache and output directory must differ")
        reuse_matrix, reuse_rows, reuse_sha = menu_reuse(
            reuse_dir, texts, lengths, model_name, dim)
        print(f"[menus] exact cache reuse {len(reuse_rows):,}; "
              f"new encoding {n - len(reuse_rows):,}", flush=True)

    # ---- resume 판단 -------------------------------------------------------
    done = 0
    if partial_npy.exists() and state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        if state.get("fingerprint") == fp and state.get("dim") == dim:
            done = int(state.get("done", 0))
            done = max(0, min(done, n))
            print(f"[{kind}] 이어서 실행: {done:,}/{n:,} 행 완료됨", flush=True)
        else:
            print(f"[{kind}] 지문 불일치 → 부분결과 폐기하고 처음부터", flush=True)
            partial_npy.unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)

    mm = None
    if done > 0 and partial_npy.exists():
        mm = np.lib.format.open_memmap(partial_npy, mode="r+")
        if mm.shape != (n, dim) or mm.dtype != np.float32:
            # 방어: 모양·타입이 다르면 재사용할 수 없다
            mm = None
            print(f"[{kind}] 부분결과 모양 불일치 → 처음부터", flush=True)
    if mm is None:
        # done 이 0 이거나 부분결과를 못 쓰는 경우: 새로 만든다
        done = 0
        partial_npy.unlink(missing_ok=True)
        mm = np.lib.format.open_memmap(
            partial_npy, mode="w+", dtype=np.float32, shape=(n, dim)
        )

    model.max_seq_length = max_length

    # ---- 인코딩 ------------------------------------------------------------
    # block = batch_size * checkpoint_every. 한 block 을 encode 에 통째로 넘기면
    # sentence-transformers 가 block 안에서 길이순 정렬을 해줘서 패딩 낭비가 줄고,
    # block 이 곧 체크포인트 단위가 된다.
    block = max(1, batch_size * checkpoint_every)
    t0 = time.time()
    start_done = done
    while done < n:
        stop = min(done + block, n)
        fresh = [i for i in range(done, stop) if i not in reuse_rows]
        cached = [i for i in range(done, stop) if i in reuse_rows]
        if cached:
            mm[cached] = reuse_matrix[[reuse_rows[i] for i in cached]]
        if fresh:
            vecs = model.encode(
                [texts[i] for i in fresh], batch_size=batch_size,
                normalize_embeddings=True, convert_to_numpy=True,
                show_progress_bar=False,
            )
            mm[fresh] = np.asarray(vecs, dtype=np.float32)
        done = stop
        mm.flush()
        # 상태 파일을 마지막에 써야 "기록된 done 까지는 확실히 디스크에 있다" 가 성립한다.
        write_json_atomic(
            state_path,
            {"kind": kind, "fingerprint": fp, "done": done, "total": n, "dim": dim,
             "model": model_name, "max_length": max_length},
        )
        elapsed = time.time() - t0
        rate = (done - start_done) / elapsed if elapsed > 0 else 0.0
        eta = (n - done) / rate if rate > 0 else 0.0
        print(f"[{kind}] {done:,}/{n:,} ({done / n * 100:5.1f}%) "
              f"{rate:6.1f} texts/s  경과 {fmt_eta(elapsed)}  ETA {fmt_eta(eta)}", flush=True)

    elapsed = time.time() - t0
    del mm  # memmap 을 닫아야 rename 이 안전하다

    # ---- 마무리 ------------------------------------------------------------
    os.replace(partial_npy, final_npy)
    state_path.unlink(missing_ok=True)

    with index_path.open("w", encoding="utf-8") as fh:
        for row in index:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    digest = sha256_file(final_npy)
    entry = {
        "model": model_name,
        "count": n,
        "dim": dim,
        "elapsed_sec": round(elapsed, 1),
        "sha256": digest,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "max_length": max_length,
        "batch_size": batch_size,
        "source_total": total_all,
        "npy": final_npy.name,
        "index": index_path.name,
        "fingerprint": fp,
    }
    if kind == "menus":
        entry.update(input_format="menu-name-description-v2",
                     longest_input_tokens=max(lengths), reused_rows=len(reuse_rows),
                     encoded_rows=n - len(reuse_rows), reuse_sha256=reuse_sha)
    if limit is not None:
        entry["limit"] = limit
    update_manifest(stem, entry)

    rate = n / elapsed if elapsed > 0 else 0.0
    print(f"[{kind}] 완료 {n:,}행 x {dim} — {fmt_eta(elapsed)} ({rate:.1f} texts/s) "
          f"→ {final_npy.name}", flush=True)
    return entry


def main(argv: list[str] | None = None) -> int:
    global DATA_DIR, VECTOR_DIR
    parser = argparse.ArgumentParser(
        description="메뉴/리뷰/가게텍스트를 bge-m3 dense 벡터로 임베딩한다",
    )
    parser.add_argument("--what", default="all", choices=[*KINDS, "all"],
                        help="임베딩 대상 (기본 all)")
    parser.add_argument("--limit", type=int, default=None,
                        help="스모크 테스트용 상한. 지정하면 <kind>.smoke.* 로 저장한다")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="인코딩 배치 크기 (기본: 16)")
    parser.add_argument("--max-length", type=int, default=None,
                        help="토큰 상한 (기본: 256; 메뉴 원문이 넘으면 중단)")
    parser.add_argument("--checkpoint-every", type=int, default=50,
                        help="몇 배치마다 체크포인트를 남길지 (기본 50)")
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--model-path", type=Path, help="검증된 로컬 모델 경로")
    parser.add_argument("--local-only", action="store_true", help="모델 다운로드 금지")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=VECTOR_DIR)
    parser.add_argument("--reuse-dir", type=Path, help="읽기 전용 기존 메뉴 벡터 캐시")
    parser.add_argument("--threads", type=int, default=3,
                        help="torch 스레드 수 (기본 3, 4코어 중 1개는 남긴다)")
    parser.add_argument("--force", action="store_true",
                        help="이미 완료된 kind 도 다시 임베딩한다")
    args = parser.parse_args(argv)
    DATA_DIR, VECTOR_DIR = args.data_dir, args.output_dir

    # 실행 순서는 PLAN.md 대로 menus → store_text → reviews (싼 것부터, 리뷰가 제일 오래 걸린다)
    kinds = ["menus", "store_text", "reviews"] if args.what == "all" else [args.what]

    print(f"[start] {datetime.now().isoformat(timespec='seconds')} kinds={kinds} "
          f"limit={args.limit}", flush=True)
    model = load_model(args.model, args.threads, local_only=args.local_only,
                       model_path=args.model_path)

    t_all = time.time()
    for kind in kinds:
        defaults = DEFAULTS[kind]
        embed_kind(
            kind,
            model=model,
            model_name=args.model,
            batch_size=args.batch_size or defaults["batch_size"],
            max_length=args.max_length or defaults["max_length"],
            limit=args.limit,
            checkpoint_every=args.checkpoint_every,
            force=args.force,
            reuse_dir=args.reuse_dir,
        )
    print(f"[done] 전체 {fmt_eta(time.time() - t_all)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
