# -*- coding: utf-8 -*-
"""사진 CLIP 임베딩 (Step 3b).

`data/photos_cache/` 에 받아 둔 이미지를 `openai/clip-vit-base-patch32` 의 **이미지
탑**으로 임베딩한다(512차원, L2 정규화). 텍스트 쪽(bge-m3, 1,024차원)과는 공간이
다르므로 사진 벡터끼리만 비교한다 — 사진과 메뉴명을 한 공간에서 재는 것은 이 Step 의
질문이 아니다.

**왜 CLIP 인가.** PLAN 2절이 지정했다. CPU 4코어에 GPU 가 없으므로 ViT-B/32 (151M,
224px, 패치 32)가 현실적인 상한이다. `torch.set_num_threads(2)` 로 묶는 것은 다른
실험(백테스트)과 같이 돌기 위해서다.

**산출물** (`data/vectors/`, git 제외):
  `photo_food.npy` / `photo_space.npy`              사진 한 장 = 한 행
  `photo_food.index.jsonl` / `photo_space...`       행 -> store_id·tag·type·url
  `photo_food_store.npy` / `photo_space_store.npy`  **가게당 벡터 1개** (그 가게 사진
                                                    벡터의 평균을 L2 정규화)
  `photo_food_store.index.jsonl` / ...              행 -> store_id·n_photos
  `manifest.json` 의 `photo_food` / `photo_space` 항목 (읽고-고쳐-쓰기; 다른 kind 의
  항목은 건드리지 않는다 — 같은 파일을 텍스트 임베딩이 이미 쓰고 있다)

가게당 평균 벡터를 같이 내는 이유는 Step 2 의 결론이다: 집합형 표현의 MaxSim 은 집합
크기(=인기)와 얽혀 있어 **단일 평균 벡터가 리뷰에서 MaxSim 을 이겼다**(PLAN 7절).
사진도 같은 함정에 있으므로 단일 벡터를 1급 산출물로 둔다.

**이어하기.** 배치마다 memmap 에 바로 쓰고 `.progress.json` 에 진행 행 수를 남긴다.
중간에 죽어도 다시 돌리면 남은 행부터 한다.

실행:
    nice -n 10 ~/venvs/recsys/bin/python -m analysis.photos.embed
    nice -n 10 ~/venvs/recsys/bin/python -m analysis.photos.embed --limit 64  # 연기 시험
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from analysis.photos import download as dl

ROOT = Path(__file__).resolve().parents[2]
VECTOR_DIR = ROOT / "data" / "vectors"
MANIFEST = VECTOR_DIR / "manifest.json"

MODEL_NAME = "openai/clip-vit-base-patch32"
BATCH = 32
THREADS = 2
DIM = 512

#: kind -> 그 kind 에 들어가는 tag 들.
KINDS: dict[str, tuple[str, ...]] = {
    "photo_food": dl.FOOD_TAGS,
    "photo_space": dl.SPACE_TAGS,
}


# ────────────────────────────────────────────────── 대상 고르기
def rows_for_kind(kind: str, store_ids: list[str], index: dict[str, dict],
                  *, photo_rows: list[dl.PhotoRow] | None = None,
                  max_food: int = dl.MAX_FOOD,
                  max_space: int = dl.MAX_SPACE) -> list[dict]:
    """임베딩할 사진 목록. **순서는 `download.select_photos` 와 같다**(풀 순서·선택 순서).

    내려받기에 실패했거나 파일이 사라진 사진은 조용히 빠진다 — 그래서 임베딩 인덱스는
    '실제로 벡터가 된 사진' 의 목록이고, 커버리지는 여기서 센다.
    """
    tags = KINDS[kind]
    rows = photo_rows if photo_rows is not None else dl.select_photos(
        dl.read_photo_rows(), store_ids, max_food=max_food, max_space=max_space)
    out: list[dict] = []
    for r in rows:
        if r.tag not in tags:
            continue
        rec = index.get(r.url)
        if not rec or not rec.get("ok"):
            continue
        path = Path(rec["path"])
        if not path.exists():
            continue
        out.append({"row": len(out), "store_id": r.store_id, "tag": r.tag,
                    "type": r.type, "url": r.url, "path": str(path)})
    return out


# ────────────────────────────────────────────────── 임베딩
def _load_model():
    import torch
    from transformers import CLIPImageProcessor, CLIPModel

    torch.set_num_threads(THREADS)
    # transformers 5 에서 `torch_dtype` 은 `dtype` 으로 이름이 바뀌었다. 값은 같은 뜻
    # (float32 고정) — 프로브에서 bf16/fp16 config 가 CPU 에서 3~7배 느렸던 함정(PLAN 6절).
    model = CLIPModel.from_pretrained(MODEL_NAME, dtype=torch.float32).eval()
    proc = CLIPImageProcessor.from_pretrained(MODEL_NAME)
    return torch, model, proc


def _progress_path(kind: str) -> Path:
    return VECTOR_DIR / f".{kind}.progress.json"


def embed_rows(rows: list[dict], kind: str, *, batch: int = BATCH,
               log_every: int = 20, resume: bool = True) -> np.ndarray:
    """(N, 512) float32, L2 정규화. 배치마다 memmap 에 쓰고 진행을 기록한다."""
    from PIL import Image

    torch, model, proc = _load_model()
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    npy_path = VECTOR_DIR / f"{kind}.npy"
    prog_path = _progress_path(kind)

    done = 0
    if resume and npy_path.exists() and prog_path.exists():
        try:
            prev = json.loads(prog_path.read_text(encoding="utf-8"))
            if int(prev.get("total", -1)) == len(rows):
                done = int(prev.get("done", 0))
        except (json.JSONDecodeError, OSError):
            done = 0
    mode = "r+" if done and npy_path.exists() else "w+"
    if mode == "r+":
        mat = np.lib.format.open_memmap(npy_path, mode="r+")
        if mat.shape != (len(rows), DIM):
            mat = np.lib.format.open_memmap(npy_path, mode="w+", dtype=np.float32,
                                            shape=(len(rows), DIM))
            done = 0
    else:
        mat = np.lib.format.open_memmap(npy_path, mode="w+", dtype=np.float32,
                                        shape=(len(rows), DIM))
    print(f"[{kind}] {len(rows)}장 (이미 한 것 {done}장)", flush=True)

    start = time.time()
    n_bad = 0
    for b0 in range(done, len(rows), batch):
        chunk = rows[b0:b0 + batch]
        images, keep = [], []
        for j, r in enumerate(chunk):
            try:
                with Image.open(r["path"]) as im:
                    images.append(im.convert("RGB").copy())
                keep.append(j)
            except Exception:                        # noqa: BLE001 — 깨진 파일은 0 벡터
                n_bad += 1
        if images:
            inputs = proc(images=images, return_tensors="pt")
            with torch.no_grad():
                feats = model.get_image_features(**inputs)
            # transformers 5 의 `get_image_features` 는 텐서가 아니라 출력 객체를
            # 돌려주고, 투영된 512차원 임베딩은 `pooler_output` 자리에 있다
            # (`last_hidden_state` 는 768차원 패치 토큰 50개다 — 그걸 쓰면 안 된다).
            if hasattr(feats, "pooler_output"):
                feats = feats.pooler_output
            v = feats.detach().numpy().astype(np.float32)
            v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
        else:
            v = np.zeros((0, DIM), dtype=np.float32)
        block = np.zeros((len(chunk), DIM), dtype=np.float32)
        for k, j in enumerate(keep):
            block[j] = v[k]
        mat[b0:b0 + len(chunk)] = block
        n_done = b0 + len(chunk)
        if (b0 // batch) % log_every == 0 or n_done == len(rows):
            mat.flush()
            prog_path.write_text(json.dumps({"done": n_done, "total": len(rows)}),
                                 encoding="utf-8")
            el = time.time() - start
            rate = (n_done - done) / max(el, 1e-9)
            eta = (len(rows) - n_done) / max(rate, 1e-9) / 60
            print(f"  [{n_done}/{len(rows)}] {rate:.1f}장/초 경과 {el / 60:.1f}분 "
                  f"남은 {eta:.1f}분", flush=True)
    mat.flush()
    prog_path.write_text(json.dumps({"done": len(rows), "total": len(rows)}),
                         encoding="utf-8")
    el = time.time() - start
    print(f"[{kind}] 끝 {len(rows)}장 {el / 60:.1f}분 "
          f"({(len(rows) - done) / max(el, 1e-9):.1f}장/초) 실패 {n_bad}장", flush=True)
    return np.asarray(mat)


def store_mean_vectors(rows: list[dict], mat: np.ndarray,
                       store_ids: list[str]) -> tuple[np.ndarray, list[dict]]:
    """가게당 벡터 1개 — 그 가게 사진 벡터의 평균을 L2 정규화. 사진이 없으면 **NaN 행**.

    0 으로 채우지 않는다. 0 벡터는 '아무와도 안 닮은 가게' 이고 NaN 은 '모른다' 인데,
    사진이 없는 가게는 후자다(PLAN 2절 NaN 규칙, 메뉴·리뷰와 같다).
    """
    by_store: dict[str, list[int]] = {}
    for r in rows:
        by_store.setdefault(r["store_id"], []).append(int(r["row"]))
    out = np.full((len(store_ids), mat.shape[1]), np.nan, dtype=np.float32)
    index: list[dict] = []
    for i, sid in enumerate(store_ids):
        idx = by_store.get(sid, [])
        if idx:
            v = mat[np.asarray(idx, dtype=np.int64)].astype(np.float32).mean(axis=0)
            norm = float(np.linalg.norm(v))
            out[i] = v / norm if norm > 0 else 0.0
        index.append({"row": i, "store_id": sid, "n_photos": len(idx)})
    return out, index


# ────────────────────────────────────────────────── 저장
def write_index(path: Path, rows: list[dict], keys: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({k: r[k] for k in keys if k in r},
                                ensure_ascii=False) + "\n")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def update_manifest(entries: dict[str, dict]) -> None:
    """`manifest.json` 을 **읽고-고쳐-쓴다.** 다른 kind 의 항목을 지우면 안 된다.

    같은 파일을 텍스트 임베딩(`analysis/embed.py`)이 이미 쓰고 있어서, 통째로 덮으면
    메뉴·리뷰·요약문의 sha256 이 사라지고 백테스트 캐시 키가 무너진다.
    """
    import fcntl

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    # 잠금 파일을 따로 둔다 — manifest.json 자체를 열어 잠그면 '읽기 전에 비운다' 는
    # 경합이 남는다. 음식/공간을 프로세스 둘로 나눠 동시에 돌리므로 필요하다.
    lock_path = MANIFEST.with_suffix(".json.lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        man: dict = {}
        if MANIFEST.exists():
            try:
                man = json.loads(MANIFEST.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                man = {}
        man.update(entries)
        MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="사진 CLIP 임베딩 (Step 3b)")
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N 장만 (연기 시험)")
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args(argv)

    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass

    from analysis.backtest.data import load_dataset
    from analysis.backtest.pool import build_pool

    pool = build_pool(load_dataset())
    index = dl.read_index()
    photo_rows = dl.select_photos(dl.read_photo_rows(), pool.ids)
    print(f"[대장] {len(index)}줄 · 후보 사진 {len(photo_rows)}장", flush=True)

    entries: dict[str, dict] = {}
    for kind in [k.strip() for k in args.kinds.split(",") if k.strip()]:
        rows = rows_for_kind(kind, pool.ids, index, photo_rows=photo_rows)
        if args.limit:
            rows = rows[:args.limit]
            for i, r in enumerate(rows):
                r["row"] = i
        t0 = time.time()
        mat = embed_rows(rows, kind, batch=args.batch, resume=not args.no_resume)
        elapsed = time.time() - t0

        npy = VECTOR_DIR / f"{kind}.npy"
        write_index(VECTOR_DIR / f"{kind}.index.jsonl", rows,
                    ("row", "store_id", "tag", "type", "url"))

        smat, sindex = store_mean_vectors(rows, mat, pool.ids)
        np.save(VECTOR_DIR / f"{kind}_store.npy", smat)
        write_index(VECTOR_DIR / f"{kind}_store.index.jsonl", sindex,
                    ("row", "store_id", "n_photos"))
        covered = int(sum(1 for r in sindex if r["n_photos"] > 0))

        entries[kind] = {
            "model": MODEL_NAME, "dim": int(mat.shape[1]), "count": int(mat.shape[0]),
            "stores": len(pool.ids), "stores_with_photo": covered,
            "coverage": round(covered / max(len(pool.ids), 1), 4),
            "batch": args.batch, "threads": THREADS, "dtype": "float32",
            "seconds": round(elapsed, 1),
            "images_per_sec": round(mat.shape[0] / max(elapsed, 1e-9), 2),
            "sha256": sha256_of(npy),
            "store_mean_sha256": sha256_of(VECTOR_DIR / f"{kind}_store.npy"),
            "generated_at": datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"),
            "max_per_store": dl.MAX_FOOD if kind == "photo_food" else dl.MAX_SPACE,
        }
        print(f"[{kind}] 가게 {covered}/{len(pool.ids)}곳 "
              f"({covered / max(len(pool.ids), 1):.1%}) 커버", flush=True)

    update_manifest(entries)
    print(json.dumps(entries, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
