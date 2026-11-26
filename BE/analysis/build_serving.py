# -*- coding: utf-8 -*-
"""서빙 산출물 빌드 (PLAN 6절 '서빙 산출물') — 벡터를 서버가 읽을 행렬로 굳힌다.

서버 이미지에는 torch 도, 1,024차원 임베딩도 들어가지 않는다. 들어가는 것은 **후보 풀
크기의 유사도 행렬 몇 개**뿐이고, 온라인에서 배우는 것은 그 위의 가중치 7개다
(PLAN 3절 원칙 3번). 이 스크립트가 그 행렬을 만든다.

    data/kakao/serving/
      pool_ids.json      후보 풀 가게 id — **서버 `swipe._build_pool` 과 같은 순서**
      sim_menu.npy       (P, P) float16  메뉴 MaxSim top8 (대칭화)      Step 1
      sim_text.npy       (P, P) float16  가게 요약문 코사인              Step 2
      sim_review.npy     (P, P) float16  리뷰 평균벡터 코사인            Step 2
      sim_food.npy       (P, P) float16  음식 사진 평균벡터 코사인        Step 3b
      cooc.npy           (P, P) float16  item-item 공방문 코사인         Step 1.5
      review_vecs.npy    (R, D) float16  설명용 리뷰 벡터 (가게당 ≤ 6)
      review_pairs.json  가게 -> 그 리뷰들의 (review_id, 스니펫)과 행 위치
      brand_key.json     가게 -> 브랜드 키 (같은 브랜드 연속 노출 금지에 쓴다)
      layout.json        취향 지형도 — 2-D 좌표·메타·5-NN (`/admin/map`)
      prior.json         가중치 사전값 (아래 '사전값' 참고)
      manifest.json      입력·출력 sha256, 개수, 파라미터

**백테스트와 다른 점은 하나뿐이다: 평가 사용자 제외를 하지 않는다.** 백테스트의 누수
규칙(PLAN 1절)은 '정답을 미리 보지 않았다' 를 증명하기 위한 것이고, 프로덕션에는
정답이 없다. 리뷰 표현도 공방문 통계도 **가진 리뷰 전부**로 만든다. 그래서 이 산출물은
백테스트 캐시와 값이 다르며, 섞이지 않도록 파일 이름도 경로도 따로 둔다.

**float16 과 NaN.** 코사인은 [-1, 1] 이라 float16(유효숫자 3자리)이면 순위를 바꾸지
않는다. 대신 행렬 하나가 2,216² × 2B = 9.8MB 로 내려간다. 어떤 modality 가 없는 가게
(메뉴 없음·요약문 없음·리뷰 없음)는 **NaN** 으로 둔다 — 0 으로 채우면 '아무와도 안
닮은 가게' 가 되어 구조적으로 밀려나는데, 실제로는 '모른다' 이기 때문이다
(`analysis/content/menus.py` 의 NaN 규칙 그대로). 서버는 z 정규화 뒤에 NaN 을 0(=그
사용자 후보의 평균)으로 바꾼다.

**사전값(prior).** 사용자별 가중치의 출발점은 Step 2 의 쌍대 로지스틱이 폴드 밖에서
학습한 λ 다(PLAN 7절 결정 3번). `analysis/backtest/results/serving_prior.json` 이 있으면
그걸 그대로 복사하고, 아직 없으면 PLAN 7절에 적힌 값(`cf .49 / menu .27 / text .43 /
review .58`, w_pop = 1)에 온라인에서만 검증할 두 성분(품질 0.3 · 기피 −0.25)을 더해
`"source": "step2-fallback"` 으로 적는다. **서버는 이 파일만 읽는다.**

**온라인 전용 성분(`dropped`).** 백테스트가 점수 성분에서 뺀 것도 성분 자리는 남기고
사전 평균만 **0** 으로 싣는다 — 처음에는 점수에 아무것도 더하지 않지만 그 사람의 쌍대
피드백이 0 에서 밀어내면 살아난다. 지금 둘이다: `review`(Step 3a — 인기와 스피어만
+0.67, 인기의 다른 이름), `food`(Step 3c — 인기 매칭 AUC 는 올랐지만 상위 20장에서
NDCG 가 내려 채택 규칙에 걸렸다. `log1p(리뷰 수)` 와의 상관은 +0.14 뿐이라 인기와
겹쳐서 죽은 것이 아니다). 둘 다 **오프라인이 못 답한 질문이 온라인에는 남아 있다** —
개인 취향에서도 같은 결론인지는 팀-드래프트 인터리빙이 답한다.

**취향 지형도(`layout.json`).** 관리자 화면 `/admin/map` 이 읽는 고정 좌표다. 결합
유사도에서 만든 거리를 UMAP 으로 2-D 로 펴고 [0,1]² 로 정규화한 것이며, 가게마다 5-최근접
이웃과 그 이웃이 업종(cat2)을 넘는지를 같이 싣는다. **umap-learn 이 없으면 sklearn TSNE
로 물러나고** 어느 쪽을 썼는지 산출물과 manifest 에 적는다. 서버 이미지에는 umap 도
sklearn 도 들어가지 않는다 — 좌표는 여기서 굳고 서버는 JSON 한 장을 읽는다.

실행:
    ~/venvs/recsys/bin/python -m analysis.build_serving            # 전체
    ~/venvs/recsys/bin/python -m analysis.build_serving --only prior,manifest
    ~/venvs/recsys/bin/python -m analysis.build_serving --only layout   # 지형도만 (~30초)
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "kakao"
OUT_DIR = DATA_DIR / "serving"
SERVER_DIR = ROOT / "server"
PRIOR_SRC = ROOT / "analysis" / "backtest" / "results" / "serving_prior.json"

#: 점수 성분의 순서. 서버(`server/recsys/content.py`)의 COMPONENTS 와 같아야 한다.
COMPONENTS = ["pop", "quality", "cf", "menu", "text", "review", "food", "neg"]

#: 백테스트 사전값 파일의 성분 이름 -> 서빙 성분 이름. `popularity` 만 다르다.
PRIOR_ALIAS = {"popularity": "pop"}

#: 오프라인에서 **잴 수 없는** 성분의 사전값. 방문 데이터에는 dislike 가 없어
#: (PLAN 5절 Step 1.5 결정 1번) 기피 성분은 백테스트가 값을 낼 수 없다. 온라인
#: 인터리빙으로만 검증되므로 사전값을 여기서 주고 `prior.json` 에 `extra` 로 남긴다.
EXTRA_PRIOR = {"neg": -0.25}

#: 백테스트가 점수 성분에서 **뺀** 성분과 그 이유. 사전 평균 0 으로 실리고
#: (`build_prior` 의 `dropped`), 온라인 쌍대 피드백만이 0 에서 밀어낼 수 있다.
#: `prior.json` 에 그대로 적어 둔다 — 왜 0 인지를 파일 하나만 보고 알 수 있게.
DROPPED_NOTE = {
    "review": ("Step 3a — `review`↔`popularity` 스피어만 +0.67 로 인기의 다른 이름이라 "
               "가장 큰 λ 를 받고도 ablation 에서 죽었다. MaxSim 은 설명 레이어에만 남는다."),
    "food": ("Step 3c — 인기 매칭 AUC 는 세 N 에서 CI 로 올랐지만(+.0077~+.0100) "
             "`ndcg@20` N=20 이 −.0040 [−.0080, −.0003] 로 내려 '진 칸 0' 채택 규칙에 "
             "걸렸다. `log1p(리뷰 수)` 상관은 +0.14 뿐이라 인기와 겹쳐서가 아니라 "
             "상위를 세우는 정확도가 모자라 죽었다 — 개인 취향에서도 그런지는 온라인이 답한다."),
}

#: PLAN 7절 결정 3번의 λ (N 별 폴드 평균, w_pop = 1) + 오프라인 근거가 없는 두 성분.
#: `serving_prior.json` 이 아직 없을 때만 쓰는 되돌림 값이다.
FALLBACK_PRIOR = {
    "source": "step2-fallback",
    "components": COMPONENTS,
    "mean": {"pop": 1.0, "quality": 0.3, "cf": 0.49, "menu": 0.27,
             "text": 0.43, "review": 0.58, "food": 0.0, "neg": -0.25},
    "sd": {"pop": 0.3, "quality": 0.4, "cf": 0.35, "menu": 0.35,
           "text": 0.35, "review": 0.5, "food": 0.5, "neg": 0.5},
    "note": ("Step 2(`step2-text-reviews.md`)의 blend_pairwise 가 바깥 5-fold 로 학습한 "
             "λ 의 N 평균. quality·neg 는 오프라인 근거가 없어 사전 폭(sd)을 넓게 뒀고 "
             "food 는 Step 2 에 없던 성분이라 0 이다."),
}

#: 메뉴 modality 변종 — Step 2 결선에 오른 `menu_top8_max` 와 같은 집합이다.
MENU_VARIANT = "top8"
#: 리뷰 선택 변종 — Step 2 의 `review_longest12_single` 과 같은 12개.
REVIEW_VARIANT = "longest12"
#: 사진 kind — Step 3b 에서 신호가 확인된 것은 음식뿐이다(공간은 난수 대조군을 못 넘었다).
PHOTO_KIND = "food"

#: 설명(리뷰 쌍)용으로 벡터를 이미지에 넣을 리뷰 수. 12개를 다 넣으면 35MB 라
#: 산출물 예산(80MB)을 위협한다. 6개면 21MB 이고 쌍 후보는 36개다 — 인용문 한 줄에
#: 충분하다. 점수용 평균 벡터는 **12개 전부**로 만든다(선택의 앞 6개만 싣는 것이다).
EXPLAIN_REVIEWS = 6

#: 취향 지형도(`/admin/map`)의 결합 유사도 가중치. **사용자 가중치가 아니다** —
#: 지도는 한 사람이 아니라 후보 풀 전체의 구조를 한 장으로 굳힌 것이라 사용자별 w 를
#: 쓸 수 없다(사람마다 지도가 움직이면 '어디가 어디' 를 배울 수 없다). 값은 Step 2·3c
#: 가 학습한 λ 의 비율(`text` 가 가장 크고 `menu` 가 그다음)을 따르되, 사전 평균이 0 인
#: 두 성분(`review`·`food`)도 **구조를 그리는 데는** 넣는다 — 지도는 점수가 아니다.
LAYOUT_WEIGHTS = {"menu": 0.25, "text": 0.40, "review": 0.20, "food": 0.20}

#: 가게마다 그릴 최근접 이웃 수(이웃 선). 5개면 2,216 × 5 = 11,080 선이라 캔버스가 견딘다.
LAYOUT_NEIGHBORS = 5

#: UMAP 파라미터. `random_state` 를 고정하면 UMAP 이 스레드를 1개로 낮추는 대신
#: **같은 입력이면 같은 지도**가 나온다 — 지도가 매번 흔들리면 아무도 못 읽는다.
LAYOUT_UMAP = {"n_neighbors": 15, "min_dist": 0.1, "random_state": 20260909}

#: 좌표 정밀도. [0,1] 을 5자리로 적으면 2,216점이 1e-5 격자 위에 놓인다(화면 화소보다 촘촘).
LAYOUT_ROUND = 5


# ────────────────────────────────────────────────────────── 유틸
def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def log(msg: str) -> None:
    print(msg, flush=True)


def save_npy(path: Path, arr: np.ndarray) -> None:
    np.save(path, arr)
    log(f"  → {path.name}  {arr.shape} {arr.dtype}  {path.stat().st_size / 1e6:.1f}MB")


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    log(f"  → {path.name}  {path.stat().st_size / 1e6:.2f}MB")


def cosine_matrix(V: np.ndarray) -> np.ndarray:
    """L2 정규화된 (S, D) 에서 (S, S) 코사인. 벡터가 없는(NaN) 가게는 행·열 전부 NaN.

    `analysis/content/store_text.py: similarity_matrix` 와 같은 규칙이다. 그 함수를
    그대로 부르지 않는 것은 리뷰 평균벡터가 `StoreTextSpace` 가 아니기 때문이고,
    같은 규칙을 여섯 줄 다시 쓰는 편이 자료구조를 억지로 맞추는 것보다 낫다.
    """
    n = V.shape[0]
    ok = np.isfinite(V).all(axis=1)
    idx = np.flatnonzero(ok)
    out = np.full((n, n), np.nan, dtype=np.float32)
    if idx.size:
        sub = np.asarray(V[idx], dtype=np.float32)
        sim = sub @ sub.T
        out[np.ix_(idx, idx)] = (sim + sim.T) * np.float32(0.5)
    return out


# ────────────────────────────────────────────────────────── 후보 풀
def server_pool_ids() -> list[str] | None:
    """서버 `swipe._build_pool` 이 만드는 그 목록. 이것이 **정본**이다.

    카드 자격(리뷰 1+·사진 1+)과 순서(stores.jsonl 등장 순)를 여기서 다시 구현하지
    않는다 — 규칙을 두 벌 만들면 언젠가 어긋나고, 어긋나면 서버가 남의 가게 행을
    읽는다. 서버를 import 할 수 없으면(fastapi 없음 등) None 을 돌려주고 백테스트
    쪽 풀로 대신한다.
    """
    try:
        sys.path.insert(0, str(SERVER_DIR))
        from recsys import swipe                     # noqa: PLC0415
        return list(swipe.pool()["ids"])
    except Exception as exc:                          # noqa: BLE001
        log(f"  ! 서버 swipe 를 import 하지 못했다 ({exc!r}) — 백테스트 풀을 쓴다")
        return None


def build_pool(ds) -> tuple[list[str], dict]:
    """(후보 풀 id, 검증 메모). 서버와 백테스트 두 경로가 같은 답을 내는지 확인한다."""
    from analysis.backtest.pool import build_pool as backtest_pool

    bt = backtest_pool(ds)
    srv = server_pool_ids()
    same = srv is not None and srv == bt.ids
    if srv is not None and not same:
        raise SystemExit(
            f"후보 풀이 어긋났다: 서버 {len(srv)}곳 vs 백테스트 {len(bt.ids)}곳. "
            f"카드 자격 규칙이 갈라졌으니 먼저 그것부터 맞춰라")
    ids = srv if srv is not None else list(bt.ids)
    return ids, {"source": "server.recsys.swipe" if srv is not None else "analysis.backtest.pool",
                 "verified_against_backtest": bool(same), "count": len(ids)}


# ────────────────────────────────────────────────────────── modality 별 행렬
def build_sim_menu(ids: list[str]) -> tuple[np.ndarray, dict]:
    """메뉴 MaxSim (top8, 무가중, 대칭화). `analysis/content/menus.py` 가 계산한다."""
    from analysis.content import menus as menu_content

    space = menu_content.MenuSpace.build(ids)
    variant = menu_content.VARIANTS[MENU_VARIANT]
    packed = space.packed(variant)
    # 백테스트가 이미 만들어 둔 것과 **같은 행렬**이다(같은 임베딩·같은 풀·같은 변종).
    # 캐시 키가 그 셋으로 만들어지므로 값이 다를 수 없고, 4코어를 다른 작업과 나눠
    # 쓰는 중이라 다시 계산할 이유가 없다. 캐시가 없으면 그 자리에서 계산한다.
    mat, cached = menu_content.load_or_build_similarity(space, variant)
    meta = {"variant": MENU_VARIANT, "k": variant.k, "idf": variant.idf,
            "stores_with_menu": int((packed.counts > 0).sum()),
            "vectors": int(packed.counts.sum()),
            "from_backtest_cache": bool(cached),
            "embed_sha256": space.manifest_sha}
    return mat, meta


def build_sim_text(ids: list[str]) -> tuple[np.ndarray, dict]:
    """가게 요약문 코사인. `analysis/content/store_text.py` 가 계산한다."""
    from analysis.content import store_text as text_content

    space = text_content.StoreTextSpace.build(ids)
    mat = text_content.similarity_matrix(space)
    return mat, {"stores_with_text": int(space.has_text.sum()),
                 "coverage": round(float(space.coverage), 4),
                 "embed_sha256": space.manifest_sha}


def build_sim_food(ids: list[str]) -> tuple[np.ndarray, dict]:
    """음식 사진 **단일 벡터** 코사인. `analysis/content/photos.py` 가 계산한다.

    가게 벡터는 그 가게 음식 사진(≤ 4장) CLIP 벡터의 평균을 L2 정규화한 것이고
    유사도는 그 코사인이다 — Step 3b 의 `photo_food_max` 와 **같은 표현**이다.
    MaxSim 행렬은 만들지 않는다(Step 3b 결정 3번): 사진 MaxSim 은 단일 벡터에
    AUC·auc_pm 둘 다 졌고, 사진 쌍 설명은 `PhotoSpace.packed` 에서 그때그때 뽑으면
    되므로 2.2k² 행렬이 필요 없다.

    `PhotoSpace` 가 가게 벡터를 `data/vectors/photo_food_store.npy` 에서 읽지 않고
    사진 벡터에서 **다시 평균내는** 것은 그 파일이 만들 때의 풀 순서로 굳어 있기
    때문이다. 여기서는 풀 순서가 정본이라 그 편이 안전하다(값은 같다).

    음식 사진이 없는 343곳(15.5%)은 행·열이 통째로 NaN 이다 — 메뉴·요약문·리뷰와
    같은 규칙이고, 서버가 z 정규화 뒤 0(= 후보 평균 = '모른다')으로 바꾼다.
    """
    from analysis.content import photos as photo_content

    space = photo_content.PhotoSpace.build(PHOTO_KIND, ids)
    mat = photo_content.single_similarity(space.vecs)
    man = _vectors_manifest().get(photo_content.KIND_VECTORS[PHOTO_KIND], {})
    return mat, {"kind": PHOTO_KIND, "repr": "single",
                 "stores_with_photo": int(space.has_photo.sum()),
                 "coverage": round(float(space.coverage), 4),
                 "photos": int(space.packed.counts.sum()),
                 "max_per_store": int(space.packed.counts.max()),
                 "model": str(man.get("model", "")),
                 "embed_sha256": space.manifest_sha,
                 "store_mean_sha256": str(man.get("store_mean_sha256", ""))}


def build_review(ids: list[str]) -> tuple[np.ndarray, np.ndarray, dict, dict]:
    """리뷰 (유사도 행렬, 설명용 벡터, 설명용 인덱스, 메타).

    **점수는 단일 벡터로 낸다** (PLAN 7절 결정 2번). Step 2 에서 리뷰 MaxSim 은 단일
    벡터 대조군에 전 지표로 졌고, 난수 벡터 대조군이 진짜 벡터보다 AUC 가 높았다 —
    집합형 표현의 점수는 리뷰 **개수**(=인기)를 다시 세고 있었다. 그래서 가게 벡터는
    '고른 리뷰 ≤12개의 평균을 L2 정규화한 것' 이고 유사도는 그 코사인이다.

    MaxSim 은 **설명 전용**으로 남는다. 그래서 고른 리뷰의 앞 6개는 벡터째 이미지에
    싣는다 — 서버가 "좋아요한 ○○의 리뷰와 닮았어요: '…'" 의 인용문을 고를 때 쓴다.

    프로덕션 빌드라 `excluded_authors` 는 **빈 집합**이다(백테스트와 다른 유일한 점).
    """
    from analysis.content import menus as menu_content
    from analysis.content import reviews as review_content

    space = review_content.ReviewSpace.build(ids, set())
    variant = review_content.VARIANTS[REVIEW_VARIANT]
    packed = space.packed(variant)
    V = menu_content.mean_vectors(packed)          # 없으면 NaN 행
    sim = cosine_matrix(V)

    rows, starts, counts, review_ids, snippets = [], [], [], [], []
    for sid in ids:
        picked = space.selection(variant, sid)[:EXPLAIN_REVIEWS]
        starts.append(len(rows))
        counts.append(len(picked))
        rows.extend(r.row for r in picked)
        review_ids.append([int(r.review_id) for r in picked])
        snippets.append([r.snippet for r in picked])
    vecs = (np.asarray(space.matrix[np.asarray(rows, dtype=np.int64)], dtype=np.float16)
            if rows else np.zeros((0, space.matrix.shape[1]), dtype=np.float16))
    index = {"max_per_store": EXPLAIN_REVIEWS, "starts": starts, "counts": counts,
             "review_ids": review_ids, "snippets": snippets}
    meta = {"variant": REVIEW_VARIANT, "k": variant.k,
            "stores_with_review": int((packed.counts > 0).sum()),
            "vectors_for_mean": int(packed.counts.sum()),
            "vectors_shipped": int(vecs.shape[0]),
            "excluded_authors": 0,
            "embed_sha256": space.manifest_sha}
    return sim, vecs, index, meta


def build_cooc(ds, ids: list[str]) -> tuple[np.ndarray, dict]:
    """item-item 공방문 코사인 — `analysis/backtest/cf.py` 와 같은 식, **제외 없이**.

        sim(h, i) = |U_h ∩ U_i| / sqrt(|U_h| · |U_i|)

    `cf.build` 를 그대로 부르지 않는 것은 그 함수가 백테스트의 `Pool` 객체를 받고
    누수 규칙(평가 사용자 제외)을 전제로 쓰이기 때문이다. 여기서는 제외 집합이 비어
    있다는 사실 자체가 프로덕션 빌드의 정의라, 열 줄을 다시 쓰고 그 사실을 이름과
    주석으로 못박는 편이 안전하다. 값이 같음은 `test_build_serving_cooc` 가 잰다.
    """
    pos = {sid: i for i, sid in enumerate(ids)}
    n = len(ids)
    by_author: dict[str, set[int]] = collections.defaultdict(set)
    for r in ds.reviews:
        i = pos.get(r.store_id)
        if i is not None:
            by_author[r.author_id].add(i)

    item_users = np.zeros(n, dtype=np.float64)
    co = np.zeros((n, n), dtype=np.float32)
    n_multi = 0
    for stores in by_author.values():
        idx = np.fromiter(sorted(stores), dtype=np.int64, count=len(stores))
        item_users[idx] += 1.0
        if idx.size >= 2:
            n_multi += 1
            co[np.ix_(idx, idx)] += 1.0

    denom = np.sqrt(np.outer(item_users, item_users))
    with np.errstate(invalid="ignore", divide="ignore"):
        sim = np.where(denom > 0, co / np.maximum(denom, 1e-12), 0.0).astype(np.float32)
    np.fill_diagonal(sim, np.where(item_users > 0, 1.0, 0.0).astype(np.float32))
    off = np.triu(sim, k=1)
    meta = {"authors": len(by_author), "authors_multi": n_multi,
            "pairs_nonzero": int((off > 0).sum()),
            "pairs_total": n * (n - 1) // 2,
            "items_empty": int((item_users == 0).sum()),
            "excluded_authors": 0}
    return sim, meta


def build_brand_key(ds, ids: list[str]) -> tuple[dict, dict]:
    """가게 -> 브랜드 키. 같은 브랜드 연속 노출 금지(PLAN 6절)가 이걸 읽는다.

    Step 1.5 는 브랜드 억제가 **지표를 바꾸지 않는다**는 것을 확인했다. 그래서 점수가
    아니라 노출 규칙으로만 쓴다.
    """
    from analysis.content import brands

    keys = {sid: brands.brand_key(ds.stores.get(sid, {}).get("name") or sid)
            for sid in ids}
    n_brands = len(set(keys.values()))
    multi = sum(1 for _, c in collections.Counter(keys.values()).items() if c >= 2)
    return keys, {"brands": n_brands, "brands_with_multi_stores": multi}


# ────────────────────────────────────────────────────────── 취향 지형도
def _fill_row_mean(mat: np.ndarray) -> np.ndarray:
    """NaN 을 **그 행의 유한 평균**으로 채운다(행이 통째로 NaN 이면 행렬 전체 평균).

    점수 계산의 NaN 규칙(z 정규화 뒤 0 = '그 사용자 후보의 평균')과 **같은 뜻**을 지도
    쪽 눈금으로 옮긴 것이다: 모르는 칸은 '평균만큼 닮았다' 로 둔다. 0 으로 채우면
    메뉴 없는 124곳·사진 없는 343곳이 '아무와도 안 닮은 가게' 가 되어 지도 바깥으로
    튕겨 나가는데, 그건 데이터가 아니라 결측이 만든 모양이다.
    """
    m = np.asarray(mat, dtype=np.float32).copy()
    bad = ~np.isfinite(m)
    if not bad.any():
        return m
    finite = ~bad
    cnt = finite.sum(axis=1)
    tot = np.where(finite, m, np.float32(0.0)).sum(axis=1, dtype=np.float64)
    g = float(tot.sum() / max(int(cnt.sum()), 1))
    row = np.where(cnt > 0, tot / np.maximum(cnt, 1), g).astype(np.float32)
    m[bad] = np.broadcast_to(row[:, None], m.shape)[bad]
    return m


def _combined_similarity(out: Path) -> tuple[np.ndarray, dict]:
    """S = Σ_m w_m·sim_m / Σ w_m. 방금 저장한 행렬을 디스크에서 다시 읽는다.

    메모리에 들고 있지 않고 다시 읽는 것은 `--only layout` 하나만으로도 지도를 다시
    만들 수 있어야 하기 때문이다(UMAP 만 30초, 나머지 전부는 6초다).
    """
    mats, sources = [], {}
    for kind, w in LAYOUT_WEIGHTS.items():
        path = out / f"sim_{kind}.npy"
        if not path.exists():
            raise SystemExit(f"지형도를 만들려면 {path.name} 가 필요하다 — "
                             f"`--only {kind}` 를 먼저 돌려라")
        sources[path.name] = {"sha256": sha256_file(path), "weight": w}
        mats.append(w * _fill_row_mean(np.load(path)))
    S = np.sum(mats, axis=0, dtype=np.float32) / np.float32(sum(LAYOUT_WEIGHTS.values()))
    # 행 평균 채우기는 대칭을 깬다(메뉴 없는 가게의 행 평균 ≠ 그 열의 값). 거리는
    # 대칭이어야 하므로 다시 접는다 — 두 방향의 평균이다.
    S = ((S + S.T) * np.float32(0.5)).astype(np.float32)
    np.fill_diagonal(S, np.float32(1.0))
    return S, sources


def _embed_2d(D: np.ndarray) -> tuple[np.ndarray, dict]:
    """거리 행렬 -> 2-D 좌표. UMAP 이 있으면 UMAP, 없으면 t-SNE 로 물러난다.

    어느 쪽을 썼는지는 산출물과 manifest 에 적는다 — 지도의 모양이 방법마다 다르므로
    '어떤 방법으로 그린 그림인가' 를 보는 사람이 알아야 한다.
    """
    t0 = time.time()
    try:
        import umap                                    # noqa: PLC0415
        red = umap.UMAP(metric="precomputed", **LAYOUT_UMAP)
        xy = np.asarray(red.fit_transform(D), dtype=np.float64)
        meta = {"method": "umap", "umap_version": umap.__version__, **LAYOUT_UMAP}
    except Exception as exc:                            # noqa: BLE001
        log(f"  ! UMAP 을 쓸 수 없다 ({exc!r}) — sklearn TSNE 로 그린다")
        from sklearn.manifold import TSNE               # noqa: PLC0415
        red = TSNE(n_components=2, metric="precomputed", init="random",
                   perplexity=30.0, random_state=LAYOUT_UMAP["random_state"])
        xy = np.asarray(red.fit_transform(D), dtype=np.float64)
        meta = {"method": "tsne", "perplexity": 30.0, "init": "random",
                "random_state": LAYOUT_UMAP["random_state"],
                "fallback_reason": repr(exc)[:200]}
    meta["elapsed_sec"] = round(time.time() - t0, 1)
    return xy, meta


def _scale_unit(xy: np.ndarray) -> np.ndarray:
    """[0,1]² 안으로 min-max. **두 축을 같은 배율로** 줄이고 짧은 축을 가운데 둔다.

    축마다 따로 늘리면 UMAP 이 그나마 지키는 것(국소 이웃의 모양)까지 찌그러진다.
    같은 배율이면 긴 축이 정확히 [0,1] 을 채우고 짧은 축은 가운데 놓인다.
    """
    lo = xy.min(axis=0)
    span = float((xy.max(axis=0) - lo).max()) or 1.0
    z = (xy - lo) / span
    return z + (1.0 - z.max(axis=0)) / 2.0


def _neighbors(S: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """자기 자신을 뺀 상위 k 이웃 (인덱스, 유사도). 유사도 내림차순."""
    n = S.shape[0]
    work = S.copy()
    np.fill_diagonal(work, -np.inf)
    part = np.argpartition(-work, k, axis=1)[:, :k]
    sims = np.take_along_axis(work, part, axis=1)
    order = np.argsort(-sims, axis=1)
    idx = np.take_along_axis(part, order, axis=1)
    return idx.astype(np.int32), np.take_along_axis(work, idx, axis=1).astype(np.float32)


def build_layout(ds, ids: list[str], out: Path) -> tuple[dict, dict]:
    """취향 지형도 — 후보 풀 전체를 한 장의 2-D 지도로 굳힌다 (`/admin/map`).

    지도는 **점수가 아니다.** 사용자별 가중치로 그리면 사람마다 지형이 달라져 '여기가
    어디' 를 배울 수 없으므로, 결합 유사도(`LAYOUT_WEIGHTS`) 하나로 한 번만 그리고
    사용자별 점수는 그 위에 **색**으로 얹는다. 색은 사람마다 다르고 땅은 같다.

    UMAP 의 caveat 를 산출물에도 적어 둔다: **국소 이웃만 믿을 수 있고 두 점 사이
    거리는 절대값으로 읽으면 안 된다.** 그래서 5-NN 이웃 목록을 좌표와 함께 싣는다 —
    화면이 '진짜 이웃' 을 선으로 그릴 수 있어야 좌표의 거짓말을 눈으로 잡는다.
    """
    from analysis.content import brands                 # noqa: PLC0415

    t0 = time.time()
    S, sources = _combined_similarity(out)
    log(f"  결합 유사도 {S.shape} — " +
        " · ".join(f"{k} {w}" for k, w in LAYOUT_WEIGHTS.items()))

    D = np.clip(1.0 - S, 0.0, None).astype(np.float32)
    np.fill_diagonal(D, 0.0)
    xy, emb = _embed_2d(D)
    xy = _scale_unit(xy)
    log(f"  {emb['method']} {emb['elapsed_sec']}초 → 좌표 "
        f"x[{xy[:, 0].min():.2f}, {xy[:, 0].max():.2f}] "
        f"y[{xy[:, 1].min():.2f}, {xy[:, 1].max():.2f}]")

    nbr, nbr_sim = _neighbors(S, LAYOUT_NEIGHBORS)

    cat_of = [(ds.stores.get(sid, {}).get("cat2") or "미분류") for sid in ids]
    cats = [c for c, _ in collections.Counter(cat_of).most_common()]
    cat_idx = {c: i for i, c in enumerate(cats)}
    cross = np.array([[cat_of[i] != cat_of[int(j)] for j in row]
                      for i, row in enumerate(nbr)], dtype=bool)

    layout = {
        "version": 1,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(ids),
        "method": emb["method"],
        "embedding": emb,
        "weights": dict(LAYOUT_WEIGHTS),
        "neighbors": LAYOUT_NEIGHBORS,
        "sources": sources,
        "caveat": ("UMAP 은 국소 이웃만 보존한다. 두 점 사이 거리를 절대값으로 읽지 말 것 — "
                   "군집의 크기·간격에는 의미가 없고, 믿을 수 있는 것은 '누구 옆에 있나' 다."),
        "cats": cats,
        "ids": list(ids),
        "name": [(ds.stores.get(sid, {}).get("name") or sid) for sid in ids],
        "cat": [cat_idx[c] for c in cat_of],
        "brand": [brands.brand_key(ds.stores.get(sid, {}).get("name") or sid)
                  for sid in ids],
        "reviews": [int(ds.stores.get(sid, {}).get("review_count") or 0) for sid in ids],
        "rating": [(round(float(r), 2) if (r := ds.stores.get(sid, {}).get("review_avg"))
                    else None) for sid in ids],
        "x": [round(float(v), LAYOUT_ROUND) for v in xy[:, 0]],
        "y": [round(float(v), LAYOUT_ROUND) for v in xy[:, 1]],
        "nbr": nbr.tolist(),
        "nbr_sim": [[round(float(v), 4) for v in row] for row in nbr_sim],
        "nbr_cross": cross.astype(np.uint8).tolist(),
    }
    share = float(cross.mean())
    stats = {
        "cross_category_share": round(share, 4),
        "sim_mean": round(float(nbr_sim.mean()), 4),
        "cats": len(cats),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    layout["stats"] = stats
    log(f"  이웃 {LAYOUT_NEIGHBORS}개 · 업종을 넘는 선 {share * 100:.1f}% · "
        f"cat2 {len(cats)}종")
    return layout, {**emb, "weights": dict(LAYOUT_WEIGHTS),
                    "neighbors": LAYOUT_NEIGHBORS, "sources": sources, **stats}


# ────────────────────────────────────────────────────────── 사전값
def build_prior() -> tuple[dict, dict]:
    """`analysis/backtest/results/serving_prior.json` 이 있으면 그것, 없으면 Step 2 값.

    서버는 `data/kakao/serving/prior.json` **만** 읽는다. 백테스트 결과 디렉터리를
    런타임에 읽게 하면 이미지에 analysis/ 를 넣어야 하고, 그러면 '서버는 numpy 만
    쓴다' 는 약속이 깨진다.
    """
    if not PRIOR_SRC.exists():
        return dict(FALLBACK_PRIOR), {"source": FALLBACK_PRIOR["source"],
                                      "sha256": None}

    raw = json.loads(PRIOR_SRC.read_text(encoding="utf-8"))
    fitted = {PRIOR_ALIAS.get(k, k): float(v)
              for k, v in (raw.get("weights") or raw.get("mean") or {}).items()}
    sd_flat = raw.get("prior_sd")
    sd_map = {PRIOR_ALIAS.get(k, k): float(v) for k, v in (raw.get("sd") or {}).items()}

    mean, sd, dropped = {}, {}, []
    for c in COMPONENTS:
        if c in fitted:
            mean[c] = fitted[c]
        elif c in EXTRA_PRIOR:
            mean[c] = float(EXTRA_PRIOR[c])
        else:
            # 백테스트가 성분으로 두지 **않기로** 한 것(Step 3a 의 review, Step 3c 의
            # food)은 0 으로 둔다. 성분 자체는 남는다 — 사전 평균이 0 이라 처음에는
            # 점수에 아무것도 더하지 않지만, 그 사람의 쌍대 피드백이 0 에서 밀어내면
            # 살아날 수 있다. 오프라인에서 죽은 이유가 개인 취향에서도 참인지는
            # 온라인(팀-드래프트 인터리빙)이 답한다. 이유는 `DROPPED_NOTE` 에 적는다.
            mean[c] = 0.0
            dropped.append(c)
        sd[c] = float(sd_map.get(c, sd_flat if sd_flat is not None
                                 else FALLBACK_PRIOR["sd"][c]))

    prior = {
        "source": str(raw.get("source") or f"backtest/results/{PRIOR_SRC.name}"),
        "file": f"analysis/backtest/results/{PRIOR_SRC.name}",
        "components": list(COMPONENTS),
        "mean": mean,
        "sd": sd,
        "extra": dict(EXTRA_PRIOR),
        "dropped": dropped,
        "dropped_note": {c: DROPPED_NOTE[c] for c in dropped if c in DROPPED_NOTE},
        "note": ("가중치는 백테스트가 학습한 것이고, `extra` 는 오프라인에서 잴 수 없어 "
                 "여기서 얹은 성분이다. `dropped` 는 백테스트가 성분에서 뺀 것으로 "
                 "사전 평균 0 에서 출발한다(온라인에서만 살아날 수 있다)."),
        "raw": raw,
    }
    return prior, {"source": prior["source"], "file": prior["file"],
                   "dropped": dropped, "extra": dict(EXTRA_PRIOR),
                   "sha256": sha256_file(PRIOR_SRC)}


# ────────────────────────────────────────────────────────── 실행
STEPS = ("pool", "menu", "text", "review", "food", "cooc", "brand", "prior",
         "layout", "manifest")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="서빙 산출물 빌드")
    ap.add_argument("--out", default=str(OUT_DIR), help="산출물 디렉터리")
    ap.add_argument("--only", default="", help=f"쉼표 구분 단계 ({'|'.join(STEPS)})")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = {s.strip() for s in args.only.split(",") if s.strip()} or set(STEPS)
    unknown = only - set(STEPS)
    if unknown:
        raise SystemExit(f"모르는 단계: {', '.join(sorted(unknown))}")

    t0 = time.time()
    from analysis.backtest.data import load_dataset

    log("데이터 로드…")
    ds = load_dataset(DATA_DIR)

    log("후보 풀…")
    ids, pool_meta = build_pool(ds)
    log(f"  풀 {len(ids):,}곳 ({pool_meta['source']}, "
        f"백테스트와 일치={pool_meta['verified_against_backtest']})")
    if "pool" in only:
        save_json(out / "pool_ids.json", ids)

    meta: dict = {}
    if "menu" in only:
        log("메뉴 MaxSim…")
        mat, meta["menu"] = build_sim_menu(ids)
        save_npy(out / "sim_menu.npy", mat.astype(np.float16))
        del mat
    if "text" in only:
        log("요약문 코사인…")
        mat, meta["text"] = build_sim_text(ids)
        save_npy(out / "sim_text.npy", mat.astype(np.float16))
        del mat
    if "review" in only:
        log("리뷰 평균벡터 코사인…")
        mat, vecs, index, meta["review"] = build_review(ids)
        save_npy(out / "sim_review.npy", mat.astype(np.float16))
        save_npy(out / "review_vecs.npy", vecs)
        save_json(out / "review_pairs.json", index)
        del mat, vecs
    if "food" in only:
        log("음식 사진 코사인…")
        mat, meta["food"] = build_sim_food(ids)
        save_npy(out / "sim_food.npy", mat.astype(np.float16))
        del mat
    if "cooc" in only:
        log("공방문 코사인…")
        mat, meta["cooc"] = build_cooc(ds, ids)
        save_npy(out / "cooc.npy", mat.astype(np.float16))
        del mat
    if "brand" in only:
        log("브랜드 키…")
        keys, meta["brand"] = build_brand_key(ds, ids)
        save_json(out / "brand_key.json", keys)
    if "prior" in only:
        log("사전값…")
        prior, meta["prior"] = build_prior()
        save_json(out / "prior.json", prior)
        log(f"  source={meta['prior']['source']}")

    layout_meta: dict | None = None
    if "layout" in only:
        log("취향 지형도…")
        layout, layout_meta = build_layout(ds, ids, out)
        save_json(out / "layout.json", layout)

    if "manifest" in only:
        log("manifest…")
        # `--only` 로 일부만 돌렸으면 **안 돈 단계의 기록은 이전 manifest 에서 물려
        # 받는다.** 그러지 않으면 `--only prior,manifest`(문서가 권하는 사전값 갱신
        # 경로) 한 번에 modality 기록과 지형도 기록이 통째로 사라진다 — 파일은 그대로
        # 있는데 manifest 만 '없다' 고 말하게 된다. `outputs` 는 언제나 디렉터리를
        # 다시 읽어 만드므로 sha 검사는 그대로 유효하다.
        prev = _previous_manifest(out)
        inputs = {}
        for name in ("stores.jsonl", "reviews.jsonl", "menus.jsonl", "photos.jsonl"):
            p = DATA_DIR / name
            if p.exists():
                inputs[f"data/kakao/{name}"] = {"sha256": sha256_file(p),
                                                "bytes": p.stat().st_size}
        outputs = {}
        for p in sorted(out.iterdir()):
            if p.name == "manifest.json" or not p.is_file():
                continue
            outputs[p.name] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}
        man = {
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "elapsed_sec": round(time.time() - t0, 1),
            "pool": pool_meta,
            "params": {"components": COMPONENTS, "menu_variant": MENU_VARIANT,
                       "review_variant": REVIEW_VARIANT,
                       "explain_reviews": EXPLAIN_REVIEWS,
                       "dtype": "float16", "nan_rule": "modality 없는 가게는 NaN",
                       "eval_user_exclusion": False},
            "modalities": (prev.get("modalities") or {}) | meta,
            "layout": layout_meta if "layout" in only else prev.get("layout"),
            "vectors_manifest": {k: v.get("sha256") for k, v in
                                 _vectors_manifest().items()},
            "inputs": inputs,
            "outputs": outputs,
            "total_bytes": sum(o["bytes"] for o in outputs.values()),
        }
        save_json(out / "manifest.json", man)
        log(f"완료 — 산출물 합계 {man['total_bytes'] / 1e6:.1f}MB, "
            f"{man['elapsed_sec']:.0f}초")
    return 0


def _previous_manifest(out: Path) -> dict:
    """이전 manifest. 없거나 깨졌으면 빈 dict — 빌드는 계속돼야 한다."""
    path = out / "manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _vectors_manifest() -> dict:
    try:
        from analysis import vectors
        return {k: v for k, v in vectors.manifest().items()
                if isinstance(v, dict) and not k.endswith(".smoke")}
    except Exception:                                 # noqa: BLE001
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
