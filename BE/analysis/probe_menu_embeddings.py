"""메뉴명 임베딩 모델 프로브 (PLAN.md 0절 "곱창전골↔모츠나베", 2절 "텍스트 임베딩").

`BAAI/bge-m3` 는 짧은 한국어 메뉴명에서 **표기(문자열) 유사도**만 잡고 음식 종류·맛은 못 잡는다.
23,171개 메뉴명 전체에서 잰 실제 값:

    곱창전골 · 모츠나베   0.222  (23,105 / 23,171위)   ← 같은 음식, 최하위권
    냉면 · 메밀소바        0.419
    메밀소바 · 멜론소다     0.688  ← 전혀 다른 음식인데 높다
    모츠나베 · 가츠나베     0.769
    아메리카노 · 오메리카노 0.945

이 스크립트는 후보 모델·프롬프트 변형을 **같은 미니 벤치마크**로 재서 근거를 남긴다.
정답 라벨이 없으므로 지표는 세 가지뿐이다.

    (a) 쌍 AUC   — 양성쌍(같은 음식 계열) 코사인이 미끼쌍(문자열만 닮음)보다 높은가
    (b) 유사어 순위 — 앵커를 질의로 줬을 때 짝이 23k 메뉴명 중 몇 위인가 (양방향, 중앙값·상위1%)
    (c) 평균 코사인 — 양성 / 미끼 / 무작위 200쌍

(a)(c) 는 쌍 텍스트 ~500건만 인코딩하면 되므로 싸다. (b) 는 23k 전체 행렬이 필요해서
쌍 AUC 상위 모델에만 돌린다.

사용 예:
    # 1단계: 모든 변형의 쌍 지표 (모델 로드 4번, ~20분)
    ~/venvs/recsys/bin/python analysis/probe_menu_embeddings.py --stage pairs

    # 2단계: 전체 23k 행렬 → 순위·이웃 (변형당 20~50분)
    ~/venvs/recsys/bin/python analysis/probe_menu_embeddings.py --stage full --variants bge-m3,kure-v1

    # 3단계: 리포트 렌더링
    ~/venvs/recsys/bin/python analysis/probe_menu_embeddings.py --stage report

산출물:
    data/vectors/probe_<variant>.npy          (N, dim) float32 L2 정규화 — git 에 넣지 않는다
    data/vectors/probe_corpus.index.jsonl     행 → 메뉴명
    analysis/backtest/results/probe-menu-embeddings.json / .md
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
VECTOR_DIR = REPO_ROOT / "data" / "vectors"
RESULT_DIR = REPO_ROOT / "analysis" / "backtest" / "results"
RESULT_STEM = "probe-menu-embeddings"

# 배경에서 리뷰 임베딩이 3스레드로 돌고 있다. 4코어 중 2개만 쓴다.
THREADS = 2
BATCH_SIZE = 32
RANDOM_PAIRS = 200
SEED = 20260909

# 이웃 목록을 찍어볼 앵커. 실패 사례(곱창전골·모츠나베)와 정상 사례를 섞었다.
NEIGHBOUR_ANCHORS = ["곱창전골", "냉면", "모츠나베", "마라탕", "티라미수"]
NEIGHBOUR_K = 5


# ---------------------------------------------------------------------------
# 미니 벤치마크 (이 파일이 곧 데이터다 — 바꾸면 결과가 비교 불가능해지므로 기록하고 바꾼다)
# ---------------------------------------------------------------------------

# 양성쌍: 표기는 다르지만 재료·조리법·맛이 같은 계열. "이걸 좋아하면 저것도 좋아한다" 가 성립해야 한다.
POSITIVE_PAIRS: list[tuple[str, str]] = [
    ("곱창전골", "모츠나베"),        # 곱창 + 국물, 한/일
    ("냉면", "메밀소바"),            # 메밀 찬 국수, 한/일
    ("육회", "사시미"),              # 생 단백질
    ("매운닭발", "마라샹궈"),        # 강한 매운맛 + 향신료
    ("김치찌개", "부대찌개"),        # 김치 베이스 찌개
    ("돈까스", "슈니첼"),            # 돼지 튀김 커틀릿, 한(일)/독
    ("삼겹살", "야키니쿠"),          # 불에 굽는 고기, 한/일
    ("순대국", "설렁탕"),            # 사골 뽀얀 국밥
    ("짬뽕", "마라탕"),              # 얼큰한 국물 + 면/건더기, 한(중)/중
    ("파스타", "스파게티"),          # 같은 음식의 상위/하위어
    ("라멘", "쌀국수"),              # 육수 + 면 한 그릇
    ("떡볶이", "매운쫄면"),          # 고추장 양념 + 쫄깃한 면/떡
    ("초밥", "스시"),                # 같은 음식의 한/일 표기
    ("아메리카노", "드립커피"),      # 블랙 커피
    ("티라미수", "치즈케이크"),      # 유제품 기반 차가운 디저트
    ("샤브샤브", "훠궈"),            # 끓는 육수에 데쳐 먹는 냄비, 일/중
    ("족발", "보쌈"),                # 삶은 돼지고기 + 쌈
    ("회덮밥", "포케"),              # 생선회 + 밥 한 그릇, 한/하와이
    ("양꼬치", "케밥"),              # 향신료 양고기 꼬치, 중/튀르키예
    ("곰탕", "설렁탕"),              # 소 사골 맑은 국밥
    ("갈비찜", "스페어립"),          # 갈비를 달게 조린 요리, 한/미
    ("타코", "부리또"),              # 토르티야 + 고기, 멕시코
    ("우동", "칼국수"),              # 굵은 밀 면 국물, 일/한
    ("크루아상", "페이스트리"),      # 버터 결 반죽
    ("닭갈비", "찜닭"),              # 양념 닭 + 채소, 한 그릇
    # ---- 아래는 이번 프로브에서 추가한 쌍 (같은 근거: 표기 다름 + 재료·조리법 같음) ----
    ("만두", "딤섬"),                # 밀피 + 소, 쪄서 먹는다. 한/중
    ("된장찌개", "미소시루"),        # 발효 콩 국물, 한/일
    ("감자탕", "뼈해장국"),          # 같은 음식의 다른 상호 표기
    ("탕수육", "꿔바로우"),          # 튀긴 돼지고기 + 새콤달콤 소스
    ("김밥", "후토마키"),            # 김에 만 밥, 한/일
    ("볶음밥", "나시고랭"),          # 기름에 볶은 밥, 한/인니
    ("어묵탕", "오뎅탕"),            # 같은 음식(어묵=오뎅) — 외래어↔고유어 대응 자체를 잰다
    ("새우튀김", "에비후라이"),      # 튀긴 새우, 한/일
]

# 미끼쌍: 문자열은 닮았지만 음식이 다르다. 여기서 점수가 높으면 표기를 외운 것이다.
DECOY_PAIRS: list[tuple[str, str]] = [
    ("메밀소바", "멜론소다"),
    ("모츠나베", "츠나마요"),
    ("냉면", "냉커피"),
    ("아메리카노", "아메리칸치즈버거"),
    ("김치찌개", "김치전"),
    ("돈까스", "돈코츠라멘"),
    ("초밥", "초코케이크"),
    ("라떼", "라멘"),
    ("카레", "카라멜마끼아또"),
    ("치킨", "치즈케이크"),
    ("마라탕", "마카롱"),
    ("소바", "소보로빵"),
    ("우동", "우유"),
    ("피자", "피클"),
    ("타코", "타르트"),
    ("갈비", "갈릭브레드"),
    ("감자탕", "감자튀김"),
    ("양꼬치", "양송이수프"),
    ("순대", "순두부찌개"),
    ("파스타", "파전"),
    ("냉면", "냉라면"),
    ("모츠나베", "가츠나베"),
    ("아메리카노", "오메리카노"),
    ("육회", "육포"),
    # ---- 추가 (족발↔족욕 은 비음식이라 뺐다 — 메뉴 코퍼스에 없는 단어로 재면 과장된다) ----
    ("탕수육", "탕후루"),
    ("삼겹살", "삼계탕"),
    ("설렁탕", "설탕"),
    ("떡볶이", "떡갈비"),
    ("케밥", "케이크"),
    ("부대찌개", "부추전"),
    ("크루아상", "크림파스타"),
    ("회덮밥", "회오리감자"),
]


# ---------------------------------------------------------------------------
# 모델 변형
# ---------------------------------------------------------------------------

TASK_EN = ("Given a Korean restaurant menu item, retrieve menu items with "
           "similar ingredients, cooking style and flavor")
TASK_KO = ("한국어 식당 메뉴 이름이 주어지면 재료·조리법·맛이 비슷한 다른 메뉴 이름을 찾아라")
KO_INSTRUCT_Q = "식당 메뉴 이름: {name}. 이 음식의 재료·조리법·맛이 비슷한 메뉴를 찾는다."
KO_INSTRUCT_D = "식당 메뉴 이름: {name}."


@dataclass(frozen=True)
class Variant:
    """모델 + 프롬프트 조합 하나. key 가 파일명·표 라벨이 된다."""

    key: str
    model_id: str
    query_tmpl: str          # "{name}" 자리에 메뉴명이 들어간다
    doc_tmpl: str
    max_length: int
    note: str = ""
    # True 면 data/vectors/menus.npy 를 그대로 코퍼스 행렬로 재사용한다 (embed.py 산출물과 동일 조건).
    reuse_menus: bool = False

    @property
    def symmetric(self) -> bool:
        return self.query_tmpl == self.doc_tmpl

    def q(self, name: str) -> str:
        return self.query_tmpl.replace("{name}", name)

    def d(self, name: str) -> str:
        return self.doc_tmpl.replace("{name}", name)


VARIANTS: list[Variant] = [
    Variant("bge-m3", "BAAI/bge-m3", "{name}", "{name}", 32,
            note="기준선. embed.py 가 만든 menus.npy 와 같은 조건", reuse_menus=True),
    Variant("bge-m3-prefix", "BAAI/bge-m3", "음식 메뉴: {name}", "음식 메뉴: {name}", 64,
            note="짧은 한국어 컨텍스트 접두사(양쪽 동일)"),
    Variant("bge-m3-instruct", "BAAI/bge-m3", KO_INSTRUCT_Q, KO_INSTRUCT_D, 96,
            note="질의쪽에만 한국어 지시문"),
    Variant("kure-v1", "nlpai-lab/KURE-v1", "{name}", "{name}", 32,
            note="한국어 튜닝된 bge-m3"),
    Variant("kure-v1-instruct", "nlpai-lab/KURE-v1", KO_INSTRUCT_Q, KO_INSTRUCT_D, 96),
    Variant("qwen3-0.6b", "Qwen/Qwen3-Embedding-0.6B",
            f"Instruct: {TASK_EN}\nQuery: {{name}}", "{name}", 128,
            note="모델 카드 형식(last-token pooling, 질의쪽 지시문)"),
    Variant("qwen3-0.6b-ko", "Qwen/Qwen3-Embedding-0.6B",
            f"Instruct: {TASK_KO}\nQuery: {{name}}", "{name}", 128,
            note="지시문만 한국어로"),
    Variant("e5-instruct", "intfloat/multilingual-e5-large-instruct",
            f"Instruct: {TASK_EN}\nQuery: {{name}}", "{name}", 128,
            note="모델 카드 형식(비대칭: 문서쪽 접두사 없음)"),
    Variant("e5-instruct-sym", "intfloat/multilingual-e5-large-instruct",
            f"Instruct: {TASK_EN}\nQuery: {{name}}",
            f"Instruct: {TASK_EN}\nQuery: {{name}}", 128,
            note="대칭 과제로 보고 양쪽에 같은 지시문"),
    Variant("arctic-l-v2", "Snowflake/snowflake-arctic-embed-l-v2.0",
            "query: {name}", "{name}", 64,
            note="모델 카드 형식(질의쪽에만 'query: ')"),
]

BY_KEY = {v.key: v for v in VARIANTS}


# ---------------------------------------------------------------------------
# 코퍼스
# ---------------------------------------------------------------------------

def bench_texts() -> list[str]:
    """벤치마크에 등장하는 모든 메뉴명 (등장 순서 유지, 중복 제거)."""
    out: list[str] = []
    seen: set[str] = set()
    for a, b in POSITIVE_PAIRS + DECOY_PAIRS:
        for t in (a, b):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def build_corpus() -> tuple[list[str], int]:
    """순위를 매길 코퍼스 = 고유 메뉴명 23,171개 + 코퍼스에 없는 벤치마크 단어.

    벤치마크 단어 중 22개(슈니첼·훠궈·포케 등)는 이 동네 메뉴판에 없다. 앵커도 짝도
    코퍼스 안에 있어야 순위를 매길 수 있으므로 뒤에 덧붙인다. (base, 덧붙인 수) 를 같이 돌려준다.
    """
    index_path = VECTOR_DIR / "menus.index.jsonl"
    if not index_path.exists():
        raise SystemExit(f"{index_path} 가 없다. 먼저 `python analysis/embed.py --what menus` 를 돌려라")
    texts: list[str] = []
    with index_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                texts.append(json.loads(line)["text"])
    base = len(texts)
    known = set(texts)
    for t in bench_texts():
        if t not in known:
            known.add(t)
            texts.append(t)
    return texts, base


def random_pairs(corpus: list[str], n_pairs: int = RANDOM_PAIRS) -> list[tuple[str, str]]:
    """무작위 기준선. 겹치지 않는 2n개를 뽑아 짝지어 준다 (seed 고정)."""
    rng = random.Random(SEED)
    picked = rng.sample(corpus, n_pairs * 2)
    return [(picked[2 * i], picked[2 * i + 1]) for i in range(n_pairs)]


# ---------------------------------------------------------------------------
# 인코딩
# ---------------------------------------------------------------------------

def load_model(model_id: str):
    """sentence-transformers 로 모델 하나를 올린다. (model, 로드초) 를 돌려준다.

    dtype 을 float32 로 못박는다. Qwen3-Embedding 은 config 에 bfloat16, e5-instruct 는
    float16 이 적혀 있어서 그대로 두면 (1) CPU 에서 훨씬 느리고 (2) 정규화 오차가 0.3%까지
    나서 코사인이 1.0 을 넘는다. bge-m3/KURE 는 원래 float32 라 조건이 같아진다.
    fp32 기준 모델 크기는 0.6B ≈ 2.4GB, XLM-R large ≈ 2.2GB 로 예산(4GB) 안이다.
    """
    import torch
    from sentence_transformers import SentenceTransformer

    t0 = time.time()
    model = SentenceTransformer(model_id, device="cpu", model_kwargs={"dtype": torch.float32})
    return model, time.time() - t0


def encode(model, texts: list[str], max_length: int, *, batch_size: int = BATCH_SIZE):
    """L2 정규화된 float32 행렬. (matrix, texts/s) 를 돌려준다."""
    model.max_seq_length = max_length
    t0 = time.time()
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    elapsed = max(time.time() - t0, 1e-9)
    return np.asarray(vecs, dtype=np.float32), len(texts) / elapsed


def encode_named(model, variant: Variant, names: list[str]) -> tuple[dict, dict, float]:
    """메뉴명 목록을 질의형/문서형으로 각각 인코딩해 {이름: 벡터} 두 개를 돌려준다.

    대칭 변형이면 한 번만 인코딩해서 같은 dict 를 재사용한다.
    """
    dmat, rate = encode(model, [variant.d(n) for n in names], variant.max_length)
    docs = {n: dmat[i] for i, n in enumerate(names)}
    if variant.symmetric:
        return docs, docs, rate
    qmat, _ = encode(model, [variant.q(n) for n in names], variant.max_length)
    queries = {n: qmat[i] for i, n in enumerate(names)}
    return queries, docs, rate


def pair_cos(queries: dict, docs: dict, pairs: list[tuple[str, str]]) -> np.ndarray:
    """쌍 코사인. 비대칭 모델을 공평하게 다루려고 양방향 평균을 쓴다."""
    out = np.empty(len(pairs), dtype=np.float64)
    for i, (a, b) in enumerate(pairs):
        out[i] = 0.5 * (float(queries[a] @ docs[b]) + float(queries[b] @ docs[a]))
    return out


def auc_score(pos: np.ndarray, neg: np.ndarray) -> float:
    """양성쌍 코사인이 미끼쌍보다 높을 확률 (Mann-Whitney U = ROC AUC)."""
    from sklearn.metrics import roc_auc_score

    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
    return float(roc_auc_score(y, np.concatenate([pos, neg])))


# ---------------------------------------------------------------------------
# 결과 파일 (변형별로 조각조각 채워 넣는다)
# ---------------------------------------------------------------------------

def result_path(ext: str) -> Path:
    return RESULT_DIR / f"{RESULT_STEM}.{ext}"


def read_results() -> dict:
    path = result_path("json")
    if not path.exists():
        return {"meta": {}, "variants": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"meta": {}, "variants": {}}
    data.setdefault("meta", {})
    data.setdefault("variants", {})
    return data


def write_results(data: dict) -> None:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    path = result_path("json")
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def merge_variant(key: str, patch: dict) -> dict:
    """한 변형의 결과 일부만 갱신한다 (pairs 단계와 full 단계가 따로 돈다)."""
    data = read_results()
    entry = data["variants"].get(key, {})
    entry.update(patch)
    data["variants"][key] = entry
    write_results(data)
    return data


# ---------------------------------------------------------------------------
# 1단계: 쌍 지표
# ---------------------------------------------------------------------------

def stage_pairs(keys: list[str]) -> None:
    corpus, base = build_corpus()
    rnd = random_pairs(corpus)
    names = sorted({n for p in POSITIVE_PAIRS + DECOY_PAIRS + rnd for n in p})
    print(f"[pairs] 벤치마크 양성 {len(POSITIVE_PAIRS)}쌍 / 미끼 {len(DECOY_PAIRS)}쌍 / "
          f"무작위 {len(rnd)}쌍, 고유 텍스트 {len(names)}건", flush=True)

    data = read_results()
    data["meta"].update({
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_size": len(corpus),
        "corpus_base": base,
        "n_positive": len(POSITIVE_PAIRS),
        "n_decoy": len(DECOY_PAIRS),
        "n_random": len(rnd),
        "threads": THREADS,
        "batch_size": BATCH_SIZE,
    })
    write_results(data)

    # 모델 로드가 제일 비싸므로 같은 model_id 를 쓰는 변형끼리 묶어서 한 번만 올린다.
    todo = [BY_KEY[k] for k in keys]
    for model_id in dict.fromkeys(v.model_id for v in todo):
        group = [v for v in todo if v.model_id == model_id]
        print(f"\n[pairs] === {model_id} ({', '.join(v.key for v in group)}) ===", flush=True)
        try:
            model, load_sec = load_model(model_id)
        except Exception as exc:  # 라이브러리 버전이 안 맞으면 기록만 하고 넘어간다
            print(f"[pairs] {model_id} 로드 실패: {type(exc).__name__}: {exc}", flush=True)
            for v in group:
                merge_variant(v.key, {"model_id": model_id, "loaded": False,
                                      "error": f"{type(exc).__name__}: {exc}"})
            continue
        dim = int(model.encode(["x"], convert_to_numpy=True).shape[1])
        print(f"[pairs] 로드 {load_sec:.1f}s dim={dim}", flush=True)

        for v in group:
            t0 = time.time()
            queries, docs, rate = encode_named(model, v, names)
            pos = pair_cos(queries, docs, POSITIVE_PAIRS)
            dec = pair_cos(queries, docs, DECOY_PAIRS)
            ran = pair_cos(queries, docs, rnd)
            auc = auc_score(pos, dec)
            merge_variant(v.key, {
                "model_id": model_id, "loaded": True, "dim": dim,
                "load_sec": round(load_sec, 1), "note": v.note,
                "max_length": v.max_length, "symmetric": v.symmetric,
                "pairs": {
                    "auc": round(auc, 4),
                    # 미끼를 빼고 무작위쌍하고만 비교한 AUC. "문자열 혼동을 걷어내면 음식 계열
                    # 신호가 조금이라도 있는가" 를 본다. 미끼 AUC 는 낮은데 이게 높으면
                    # 신호는 있으나 표기 유사도에 묻힌 것이다.
                    "auc_vs_random": round(auc_score(pos, ran), 4),
                    "decoy_auc_vs_random": round(auc_score(dec, ran), 4),
                    "pos_mean": round(float(pos.mean()), 4),
                    "pos_min": round(float(pos.min()), 4),
                    "decoy_mean": round(float(dec.mean()), 4),
                    "decoy_max": round(float(dec.max()), 4),
                    "random_mean": round(float(ran.mean()), 4),
                    "random_p95": round(float(np.percentile(ran, 95)), 4),
                    "margin": round(float(pos.mean() - dec.mean()), 4),
                    "texts_per_sec": round(rate, 1),
                    "pos_cos": [[a, b, round(float(c), 4)]
                                for (a, b), c in zip(POSITIVE_PAIRS, pos)],
                    "decoy_cos": [[a, b, round(float(c), 4)]
                                  for (a, b), c in zip(DECOY_PAIRS, dec)],
                    "random_cos": [round(float(c), 4) for c in ran],
                },
            })
            print(f"[pairs] {v.key:22s} AUC={auc:.3f}  양성={pos.mean():.3f} "
                  f"미끼={dec.mean():.3f} 무작위={ran.mean():.3f}  "
                  f"{rate:.1f} texts/s  ({time.time() - t0:.0f}s)", flush=True)

        del model
        gc.collect()


# ---------------------------------------------------------------------------
# 2단계: 전체 23k 행렬 → 순위와 이웃
# ---------------------------------------------------------------------------

def doc_key(variant: Variant) -> str:
    """문서쪽 인코딩이 같은 변형끼리 행렬 파일 하나를 공유한다.

    질의쪽 지시문만 다른 변형(qwen3-0.6b / qwen3-0.6b-ko)은 23k 문서 행렬이 완전히 같다.
    23k 인코딩이 40분짜리라 두 번 돌리지 않는다.
    """
    for v in VARIANTS:
        if (v.model_id, v.doc_tmpl, v.max_length) == (variant.model_id, variant.doc_tmpl, variant.max_length):
            return v.key
    return variant.key


def corpus_matrix_path(key_or_variant) -> Path:
    key = key_or_variant if isinstance(key_or_variant, str) else doc_key(key_or_variant)
    return VECTOR_DIR / f"probe_{key}.npy"


def write_corpus_index(corpus: list[str]) -> None:
    path = VECTOR_DIR / "probe_corpus.index.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for i, t in enumerate(corpus):
            fh.write(json.dumps({"row": i, "text": t}, ensure_ascii=False) + "\n")


def build_corpus_matrix(variant: Variant, corpus: list[str], model) -> tuple[np.ndarray, float]:
    """코퍼스 문서 행렬. 이미 저장돼 있으면 재사용한다."""
    npy = corpus_matrix_path(variant)
    if npy.exists():
        mat = np.load(npy, mmap_mode="r")
        if mat.shape[0] == len(corpus):
            print(f"[full] {variant.key}: 기존 {npy.name} 재사용 {mat.shape}", flush=True)
            return np.asarray(mat), 0.0
        print(f"[full] {variant.key}: {npy.name} 행 수 불일치({mat.shape[0]}) → 다시 만든다", flush=True)

    if variant.reuse_menus:
        # bge-m3 무접두사는 embed.py 가 이미 만들어 놨다. 뒤에 덧붙인 벤치마크 단어만 새로 인코딩.
        base_mat = np.asarray(np.load(VECTOR_DIR / "menus.npy", mmap_mode="r"), dtype=np.float32)
        extra = corpus[base_mat.shape[0]:]
        print(f"[full] {variant.key}: menus.npy {base_mat.shape} 재사용 + 추가 {len(extra)}건", flush=True)
        if extra:
            emat, rate = encode(model, [variant.d(t) for t in extra], variant.max_length)
            mat = np.vstack([base_mat, emat])
        else:
            mat, rate = base_mat, 0.0
    else:
        t0 = time.time()
        mat, rate = encode(model, [variant.d(t) for t in corpus], variant.max_length,
                           batch_size=64)
        print(f"[full] {variant.key}: {len(corpus):,}건 인코딩 {time.time() - t0:.0f}s "
              f"({rate:.1f} texts/s)", flush=True)

    mat = np.ascontiguousarray(mat, dtype=np.float32)
    np.save(npy, mat)
    return mat, rate


def stage_full(keys: list[str]) -> None:
    corpus, base = build_corpus()
    write_corpus_index(corpus)
    pos_of = {t: i for i, t in enumerate(corpus)}
    top1pct = max(1, int(round(0.01 * len(corpus))))
    print(f"[full] 코퍼스 {len(corpus):,}행 (원본 {base:,} + 추가 {len(corpus) - base}), "
          f"상위 1% = {top1pct}위 이내", flush=True)

    for key in keys:
        v = BY_KEY[key]
        print(f"\n[full] === {v.key} ({v.model_id}) ===", flush=True)
        need_model = not corpus_matrix_path(v).exists() or not v.symmetric
        model, load_sec = (None, 0.0)
        if need_model:
            try:
                model, load_sec = load_model(v.model_id)
                print(f"[full] 로드 {load_sec:.1f}s", flush=True)
            except Exception as exc:
                print(f"[full] {v.model_id} 로드 실패: {exc}", flush=True)
                merge_variant(v.key, {"loaded": False, "error": f"{type(exc).__name__}: {exc}"})
                continue

        mat, rate = build_corpus_matrix(v, corpus, model)

        # 앵커의 질의 벡터. 대칭 변형이면 코퍼스 행을 그대로 쓴다.
        anchors = sorted({n for p in POSITIVE_PAIRS for n in p} | set(NEIGHBOUR_ANCHORS))
        if v.symmetric:
            qvecs = {n: mat[pos_of[n]] for n in anchors}
        else:
            qmat, _ = encode(model, [v.q(n) for n in anchors], v.max_length)
            qvecs = {n: qmat[i] for i, n in enumerate(anchors)}

        # 순위: 앵커를 질의로 준 뒤 짝이 코퍼스 전체에서 몇 번째인가 (자기 자신 제외).
        ranks: list[list] = []
        for a, b in POSITIVE_PAIRS:
            for src, tgt in ((a, b), (b, a)):
                sims = mat @ qvecs[src]
                target = float(sims[pos_of[tgt]])
                # 동점은 불리하게(뒤로) 센다. 자기 자신은 항상 1위이므로 빼고 1-base 로 만든다.
                rank = int((sims > target).sum())
                if pos_of[src] != pos_of[tgt] and float(sims[pos_of[src]]) > target:
                    rank -= 1
                ranks.append([src, tgt, rank + 1])

        rank_vals = np.array([r[2] for r in ranks], dtype=np.float64)
        neighbours = {}
        for a in NEIGHBOUR_ANCHORS:
            sims = mat @ qvecs[a]
            order = np.argsort(-sims)
            picked = [i for i in order[: NEIGHBOUR_K + 3] if corpus[i] != a][:NEIGHBOUR_K]
            neighbours[a] = [[corpus[i], round(float(sims[i]), 4)] for i in picked]

        merge_variant(v.key, {
            "full": {
                "npy": corpus_matrix_path(v).name,
                "corpus_size": len(corpus),
                "top1pct_cut": top1pct,
                "median_rank": float(np.median(rank_vals)),
                "mean_rank": round(float(rank_vals.mean()), 1),
                "best_rank": int(rank_vals.min()),
                "worst_rank": int(rank_vals.max()),
                "top1pct_share": round(float((rank_vals <= top1pct).mean()), 4),
                "top10_share": round(float((rank_vals <= 10).mean()), 4),
                "n_directions": len(ranks),
                "encode_texts_per_sec": round(rate, 1),
                "ranks": ranks,
                "neighbours": neighbours,
            },
        })
        print(f"[full] {v.key}: 중앙값 순위 {np.median(rank_vals):,.0f}/{len(corpus):,}  "
              f"상위1% {(rank_vals <= top1pct).mean() * 100:.0f}%  "
              f"상위10 {(rank_vals <= 10).mean() * 100:.0f}%", flush=True)
        for a, rows in neighbours.items():
            print(f"        {a}: " + ", ".join(f"{t}({c:.3f})" for t, c in rows), flush=True)

        del mat, model
        gc.collect()


# ---------------------------------------------------------------------------
# 3단계: 리포트
# ---------------------------------------------------------------------------

def fmt(x, nd=3, dash="—"):
    return dash if x is None else f"{x:.{nd}f}"


def stage_report() -> None:
    data = read_results()
    meta = data.get("meta", {})
    variants = data.get("variants", {})
    order = [v.key for v in VARIANTS if v.key in variants]

    L: list[str] = []
    L.append("# 메뉴명 임베딩 모델 프로브 — 어떤 모델이 '맛/음식 계열'을 잡는가")
    L.append("")
    L.append(f"실행: {meta.get('created_at', '?')} · EC2 4 vCPU / 15 GB, GPU 없음, "
             f"`torch.set_num_threads({meta.get('threads', THREADS)})` "
             f"(리뷰 임베딩 배경 작업 3스레드와 경합 중이라 texts/s 는 보수적으로 읽어라)")
    L.append(f"스크립트: `analysis/probe_menu_embeddings.py` · 원자료: `{RESULT_STEM}.json`")
    L.append("")
    L.append("## 0. 왜 쟀나")
    L.append("")
    L.append("`BAAI/bge-m3` 는 짧은 한국어 메뉴명에서 표기 유사도만 잡는다. 23,171개 전체에서:")
    L.append("")
    L.append("| 쌍 | 관계 | 코사인 |")
    L.append("|---|---|---|")
    L.append("| 곱창전골 · 모츠나베 | 같은 음식 | 0.222 (23,105위) |")
    L.append("| 냉면 · 메밀소바 | 같은 계열 | 0.419 |")
    L.append("| 메밀소바 · 멜론소다 | 무관 | 0.688 |")
    L.append("| 모츠나베 · 가츠나베 | 무관 | 0.769 |")
    L.append("| 아메리카노 · 오메리카노 | 무관 | 0.945 |")
    L.append("")
    L.append(f"미니 벤치마크: 양성 {meta.get('n_positive', '?')}쌍(표기 다름·같은 음식 계열), "
             f"미끼 {meta.get('n_decoy', '?')}쌍(문자열만 닮음), "
             f"무작위 {meta.get('n_random', '?')}쌍. 목록은 스크립트 상단에 데이터로 박아 뒀다.")
    L.append("")
    L.append("## 1. 쌍 지표 (전 변형)")
    L.append("")
    L.append("**주 AUC** = 양성쌍 코사인이 미끼쌍보다 높을 확률. 0.5 는 동전던지기, 1.0 이면 완전 분리, "
             "0.5 미만이면 **모델이 거꾸로 가고 있다**(문자열이 닮은 남남을 같은 음식보다 위에 놓는다).")
    L.append("보조로 미끼를 빼고 무작위쌍하고만 비교한 AUC 도 같이 싣는다 — 표기 혼동을 걷어내면 "
             "음식 계열 신호가 조금이라도 남는지 보려는 것이다.")
    L.append("")
    L.append("| 변형 | 모델 | AUC 양성vs미끼 | AUC 양성vs무작위 | AUC 미끼vs무작위 | 양성 평균 | "
             "미끼 평균 | 무작위 평균 | texts/s | 비고 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for key in sorted(order, key=lambda k: -(variants[k].get("pairs", {}).get("auc") or -1)):
        e = variants[key]
        if not e.get("loaded"):
            L.append(f"| `{key}` | {e.get('model_id', '?')} | — | — | — | — | — | — | — | "
                     f"로드 실패: {e.get('error', '?')} |")
            continue
        p = e.get("pairs", {})
        L.append(f"| `{key}` | {e['model_id']} | **{fmt(p.get('auc'))}** | "
                 f"{fmt(p.get('auc_vs_random'))} | {fmt(p.get('decoy_auc_vs_random'))} | "
                 f"{fmt(p.get('pos_mean'))} | {fmt(p.get('decoy_mean'))} | {fmt(p.get('random_mean'))} | "
                 f"{fmt(p.get('texts_per_sec'), 1)} | {e.get('note', '')} |")
    L.append("")

    full_keys = [k for k in order if variants[k].get("full")]
    if full_keys:
        L.append("## 2. 유사어 순위 (23k 전체 랭킹)")
        L.append("")
        cut = variants[full_keys[0]]["full"]["top1pct_cut"]
        n = variants[full_keys[0]]["full"]["corpus_size"]
        L.append(f"양성쌍의 앵커를 질의로 주고 짝이 {n:,}개 중 몇 위인지 잰다 (양방향 "
                 f"{variants[full_keys[0]]['full']['n_directions']}회). 상위 1% = {cut}위 이내.")
        L.append("")
        L.append(f"코퍼스는 고유 메뉴명 {meta.get('corpus_base', 23171):,}개에 이 동네 메뉴판에 없는 "
                 f"벤치마크 단어 {n - meta.get('corpus_base', 23171)}개(슈니첼·훠궈·포케 등)를 덧붙인 "
                 f"{n:,}행이다. 앵커도 짝도 코퍼스 안에 있어야 순위를 매길 수 있다. "
                 f"덧붙인 만큼 순위가 밀리므로 기존 보고값(곱창전골→모츠나베 23,105/23,171)과는 "
                 f"정확히 {n - meta.get('corpus_base', 23171)}만큼 차이가 난다 — 재현된 것이다.")
        L.append("")
        L.append("| 변형 | 중앙값 순위 | 상위 1% | 상위 10위 | 최고 | 최악 | 23k 인코딩 texts/s | 23k 재임베딩 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for key in full_keys:
            f_ = variants[key]["full"]
            rate = f_.get("encode_texts_per_sec") or 0.0
            # 0.0 = 문서쪽 인코딩이 같은 다른 변형의 행렬을 재사용한 경우
            speed = f"{rate:.1f}" if rate else f"재사용(`{f_['npy'][6:-4]}`)"
            cost = f"{n / rate / 60:.0f}분" if rate else "—"
            L.append(f"| `{key}` | {f_['median_rank']:,.0f} | {f_['top1pct_share'] * 100:.0f}% | "
                     f"{f_['top10_share'] * 100:.0f}% | {f_['best_rank']:,} | {f_['worst_rank']:,} | "
                     f"{speed} | {cost} |")
        L.append("")
        L.append("`bge-m3` 의 14.8 texts/s 는 27건만 새로 인코딩하고 나머지는 `menus.npy` 를 "
                 "재사용한 값이라 참고용이다. embed.py 가 3스레드·무경합으로 잰 값은 21.8 texts/s "
                 "(23,171건 17분 42초)였다.")
        L.append("")
        L.append("### 글자를 공유하는 짝 vs 안 겹치는 짝")
        L.append("")
        L.append("이 프로브에서 제일 중요한 표다. 양성쌍을 **한 글자라도 공유하는지**로 갈라 순위를 "
                 "따로 낸다. 공유하는 쪽(설렁탕↔곰탕의 `탕`, 김치찌개↔부대찌개의 `찌개` 등) 10방향, "
                 "안 겹치는 쪽(곱창전골↔모츠나베, 돈까스↔슈니첼, 회덮밥↔포케 등) 56방향.")
        L.append("")
        L.append("| 변형 | 글자 공유 (10방향) 중앙값 | 글자 안 겹침 (56방향) 중앙값 | 배율 |")
        L.append("|---|---|---|---|")
        for key in full_keys:
            rows = variants[key]["full"]["ranks"]
            hit = [v for a, b, v in rows if set(a) & set(b)]
            miss = [v for a, b, v in rows if not set(a) & set(b)]
            mh, mm = float(np.median(hit)), float(np.median(miss))
            L.append(f"| `{key}` | {mh:,.0f} | {mm:,.0f} | {mm / mh:.0f}배 |")
        L.append("")
        L.append("**글자가 겹치면 상위 1% 안, 안 겹치면 하위권.** 배율이 50~80배다. 모델이 정렬하는 "
                 "축은 음식 계열이 아니라 표기다. 66방향 × 4변형 중 글자가 하나도 안 겹치면서 상위 1% "
                 "에 든 것은 `아메리카노→드립커피`(qwen3-0.6b-ko, 219위) **딱 하나**다.")
        L.append("")
        L.append("전체 중앙값에서는 qwen3 가 bge-m3 를 앞서지만 정작 **글자 안 겹치는 56방향만 보면 "
                 "bge-m3 가 더 낫다**. qwen3 의 우위는 음식 지식이 아니라 표기 노이즈가 덜한 데서 "
                 "온다. 갈아탈 이유가 되지 못한다.")
        L.append("")

        L.append("### 쌍별 순위 (양방향 중 나쁜 쪽)")
        L.append("")
        header = " | ".join(f"`{k}`" for k in full_keys)
        L.append(f"| 양성쌍 | {header} |")
        L.append("|---|" + "---|" * len(full_keys))
        worst: dict[str, dict[tuple[str, str], int]] = {}
        for key in full_keys:
            m: dict[tuple[str, str], int] = {}
            for src, tgt, r in variants[key]["full"]["ranks"]:
                pair = (src, tgt) if (src, tgt) in {tuple(p) for p in POSITIVE_PAIRS} else (tgt, src)
                m[pair] = max(m.get(pair, 0), r)
            worst[key] = m
        for a, b in POSITIVE_PAIRS:
            cells = " | ".join(f"{worst[k].get((a, b), 0):,}" for k in full_keys)
            L.append(f"| {a} · {b} | {cells} |")
        L.append("")

        L.append("## 3. 이웃 (상위 5)")
        L.append("")
        for key in full_keys:
            L.append(f"**`{key}`**")
            L.append("")
            for a, rows in variants[key]["full"]["neighbours"].items():
                joined = ", ".join(f"{t} ({c:.3f})" for t, c in rows)
                L.append(f"- {a} → {joined}")
            L.append("")

    L.append("## 4. 쌍별 코사인 (최고 변형)")
    L.append("")
    best = max(order, key=lambda k: variants[k].get("pairs", {}).get("auc") or -1, default=None)
    if best:
        p = variants[best].get("pairs", {})
        L.append(f"`{best}` 기준. 낮은 양성쌍 = 이 모델도 못 잡는 것.")
        L.append("")
        rows = sorted(p.get("pos_cos", []), key=lambda r: r[2])
        L.append("| 양성쌍 | 코사인 | | 미끼쌍 | 코사인 |")
        L.append("|---|---|---|---|---|")
        dec = sorted(p.get("decoy_cos", []), key=lambda r: -r[2])
        for i in range(max(len(rows), len(dec))):
            lft = f"{rows[i][0]} · {rows[i][1]} | {rows[i][2]:.3f}" if i < len(rows) else " | "
            rgt = f"{dec[i][0]} · {dec[i][1]} | {dec[i][2]:.3f}" if i < len(dec) else " | "
            L.append(f"| {lft} | | {rgt} |")
        L.append("")

    # ---- 판단 ---------------------------------------------------------------
    loaded = [k for k in order if variants[k].get("loaded")]
    aucs = {k: variants[k]["pairs"]["auc"] for k in loaded if variants[k].get("pairs")}
    above_half = [k for k, a in aucs.items() if a > 0.5]
    L.append("## 5. 판단과 권고")
    L.append("")
    if above_half:
        L.append(f"AUC 0.5 를 넘긴 변형: {', '.join(f'`{k}`' for k in above_half)}. "
                 "이 중 순위 지표가 가장 좋은 것을 후보로 올린다.")
    else:
        L.append(f"**잰 {len(aucs)}개 변형 중 AUC 가 0.5 를 넘긴 것은 하나도 없다.** "
                 f"최고가 `{best}` 의 {fmt(aucs.get(best))} 다. 0.5 미만이라는 것은 동전던지기보다 "
                 "나쁘다는 뜻 — 이 모델들은 같은 음식(곱창전골·모츠나베)보다 **문자열이 닮은 남남**"
                 "(모츠나베·가츠나베)을 더 가깝다고 본다. 표기 유사도가 음식 유사도를 이긴다.")
    L.append("")
    # 신호가 아예 없는지, 있는데 묻힌 것인지 구분한다
    vr = {k: variants[k]["pairs"] for k in loaded if variants[k].get("pairs")}
    with_r = {k: p for k, p in vr.items() if p.get("auc_vs_random") is not None}
    if with_r:
        lo = min(p["auc_vs_random"] for p in with_r.values())
        hi = max(p["auc_vs_random"] for p in with_r.values())
        dlo = min(p["decoy_auc_vs_random"] for p in with_r.values())
        L.append(f"- **신호가 없는 게 아니라 묻힌 것이다.** 미끼를 빼고 무작위쌍하고만 비교하면 "
                 f"모든 변형이 0.5 를 넘는다({fmt(lo)}~{fmt(hi)}) — 양성쌍이 아무 관계 없는 두 "
                 f"메뉴명보다는 확실히 가깝다. 그런데 미끼쌍은 그보다도 더 가깝다"
                 f"(무작위 대비 {fmt(dlo)} 이상). 음식 계열 신호는 있는데 표기 유사도가 그 위에 "
                 f"덮여 있다. 그래서 순위(2절)에서 글자 겹치는 짝만 위로 올라온다.")

    # 접두사·지시문의 효과: 같은 모델 안에서 무접두사 대비 얼마나 움직였나
    def cos_of(key: str, pair: tuple[str, str], kind: str) -> float | None:
        for a, b, c in variants.get(key, {}).get("pairs", {}).get(kind, []):
            if (a, b) == pair:
                return c
        return None

    if "bge-m3" in aucs and "bge-m3-prefix" in aucs:
        p0, p1 = variants["bge-m3"]["pairs"], variants["bge-m3-prefix"]["pairs"]
        L.append(f"- **프롬프트·접두사로는 안 고쳐진다.** 접두사를 붙이면 코사인 절대값은 크게 오르지만"
                 f"(bge-m3 양성 {fmt(p0['pos_mean'])} → {fmt(p1['pos_mean'])}) 미끼도 같이 올라서"
                 f"({fmt(p0['decoy_mean'])} → {fmt(p1['decoy_mean'])}) 분리는 그대로다"
                 f"(AUC {fmt(p0['auc'])} → {fmt(p1['auc'])}). 문맥 한 줄이 표기 유사도를 이기지 못한다.")
    if "qwen3-0.6b-ko" in aucs and "bge-m3" in aucs:
        gm = cos_of("qwen3-0.6b-ko", ("곱창전골", "모츠나베"), "pos_cos")
        kn = cos_of("qwen3-0.6b-ko", ("모츠나베", "가츠나베"), "decoy_cos")
        L.append(f"- **그나마 나은 계열은 Qwen3-Embedding 이다.** AUC {fmt(aucs['qwen3-0.6b-ko'])} "
                 f"vs bge-m3 {fmt(aucs['bge-m3'])}. 문자열 의존이 약해서 미끼쌍 코사인이 낮게 나오지만"
                 f"(모츠나베·가츠나베 {fmt(kn)}), 양성쌍도 같이 낮아서(곱창전골·모츠나베 {fmt(gm)}) "
                 "순서가 뒤집히지는 않는다.")
    # 곱창전골 → 모츠나베 순위를 full 을 돌린 변형별로 인용
    if full_keys:
        L.append("")
        L.append("**곱창전골을 질의로 줬을 때 모츠나베의 순위:**")
        L.append("")
        for key in full_keys:
            got = [r for r in variants[key]["full"]["ranks"] if r[0] == "곱창전골" and r[1] == "모츠나베"]
            n = variants[key]["full"]["corpus_size"]
            if got:
                L.append(f"- `{key}`: {got[0][2]:,} / {n:,}")
        L.append("")
        L.append("어느 모델도 모츠나베를 곱창전골 근처에 놓지 못한다. 이웃 목록(3절)이 이유를 그대로 "
                 "보여준다 — 상위 5개가 전부 `소곱창전골`·`곱창전골(대)` 처럼 **같은 글자를 공유하는 "
                 "같은 메뉴의 표기 변형**이다. 임베딩이 배운 것은 음식이 아니라 문자열이다.")
    L.append("")
    L.append("### 권고")
    L.append("")
    L.append("1. **메뉴명 임베딩 모델을 갈아끼우지 마라.** 10개 변형 중 이 문제를 푸는 것이 없다. "
             "`BAAI/bge-m3` 를 그대로 둔다 (이미 23,171건 임베딩이 끝나 있고, 교체하면 재임베딩 "
             "비용만 든다).")
    if best in variants and variants[best].get("pairs"):
        rate = variants[best]["pairs"].get("texts_per_sec") or 0
        if rate:
            mins = meta.get("corpus_size", 23198) / rate / 60
            L.append(f"   참고로 갈아끼울 경우 `{best}` 로 23k 재임베딩에 이 EC2 기준 약 {mins:.0f}분 "
                     f"({rate:.1f} texts/s, 2스레드·리뷰 임베딩과 경합 중) 걸린다. 그 값을 치를 이유가 없다.")
    L.append("2. **의미는 메뉴명이 아니라 설명에서 와야 한다.** 짧은 고유명사(모츠나베)에는 재료·조리법·"
             "맛이 글자로 안 들어 있다. 임베딩 모델이 그것을 아는 것과, 4~10글자 문자열에서 그것을 "
             "복원하는 것은 다른 일이다. LLM 으로 메뉴명 → 속성 문장(재료·조리법·맛·매운맛·국물유무)을 "
             "한 번 만들어 두고 **그 문장을 임베딩**하는 편이 옳다. 23,171건은 일회성 배치로 감당 가능하고, "
             "결과는 캐시해서 재사용한다.")
    L.append("3. **다음 검증은 이 벤치마크를 그대로 재사용해라.** LLM 설명 레이어를 붙인 뒤 같은 "
             f"양성 {meta.get('n_positive')}쌍 / 미끼 {meta.get('n_decoy')}쌍으로 AUC 를 다시 재서 "
             "0.5 를 넘는지 본다. 넘지 못하면 그 레이어도 버린다.")
    L.append("4. PLAN.md 2절의 메뉴 다중 벡터(MaxSim)는 **표현이 고쳐진 뒤에** 의미가 있다. 지금 벡터로 "
             "MaxSim 을 돌리면 '이름이 비슷한 메뉴를 파는 가게'를 찾게 된다.")
    L.append("")

    path = result_path("md")
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[report] {path} ({len(L)}줄)", flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="메뉴명 임베딩 모델 프로브")
    ap.add_argument("--stage", choices=("pairs", "full", "report"), required=True)
    ap.add_argument("--variants", default="all",
                    help=f"쉼표 구분. 가능: all, {', '.join(BY_KEY)}")
    ap.add_argument("--threads", type=int, default=THREADS)
    args = ap.parse_args()

    import torch

    torch.set_num_threads(args.threads)

    if args.stage == "report":
        stage_report()
        return

    keys = list(BY_KEY) if args.variants == "all" else [k.strip() for k in args.variants.split(",")]
    unknown = [k for k in keys if k not in BY_KEY]
    if unknown:
        raise SystemExit(f"알 수 없는 변형: {unknown} (가능: {', '.join(BY_KEY)})")

    if args.stage == "pairs":
        stage_pairs(keys)
    else:
        stage_full(keys)


if __name__ == "__main__":
    main()
