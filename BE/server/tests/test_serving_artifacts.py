# -*- coding: utf-8 -*-
"""서빙 산출물 자체의 검사 — 서버가 남의 가게 행을 읽는 사고를 막는다.

행렬의 i 번째 행이 `pool_ids[i]` 가 아니면 추천은 조용히 엉뚱해진다(에러가 아니라
'이상한 추천' 으로 나타나므로 눈으로는 못 잡는다). 그래서 순서·모양·해시를 잠근다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from recsys import content, swipe

SERVING = content.SERVING
pytestmark = pytest.mark.skipif(
    not (SERVING / "manifest.json").exists(),
    reason="data/kakao/serving 이 없다 (python -m analysis.build_serving)")

#: 이미지에 실리는 산출물의 상한. 넘으면 배포가 무거워진다.
MAX_TOTAL_MB = 80


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((SERVING / "manifest.json").read_text(encoding="utf-8"))


def test_풀_순서가_서버와_같다():
    ids = json.loads((SERVING / "pool_ids.json").read_text(encoding="utf-8"))
    assert ids == swipe.pool()["ids"]


def test_행렬_모양과_대칭성():
    p = len(json.loads((SERVING / "pool_ids.json").read_text(encoding="utf-8")))
    for name in ("sim_menu", "sim_text", "sim_review", "sim_food", "cooc"):
        m = np.load(SERVING / f"{name}.npy", mmap_mode="r")
        assert m.shape == (p, p) and m.dtype == np.float16, name
        block = np.asarray(m[:300, :300], dtype=np.float32)
        assert np.allclose(block, block.T, equal_nan=True), name


def test_NaN_은_가게_단위다():
    """modality 가 없는 가게는 **행과 열이 통째로** NaN 이어야 한다.

    서버의 집계는 이 성질에 기대고 있다 (`content._rows` 의 주석).
    """
    for name in ("sim_menu", "sim_text", "sim_review", "sim_food"):
        m = np.asarray(np.load(SERVING / f"{name}.npy"), dtype=np.float32)
        missing = ~np.isfinite(np.diagonal(m))
        assert np.isnan(m[missing]).all(), name
        assert np.isfinite(m[np.ix_(~missing, ~missing)]).all(), name


def test_음식_사진_커버리지가_Step3b_와_같다(manifest):
    """사진이 있는 1,873곳 / 없는 343곳 — 커버리지가 바뀌면 성분의 의미가 바뀐다."""
    meta = manifest["modalities"]["food"]
    m = np.asarray(np.load(SERVING / "sim_food.npy"), dtype=np.float32)
    covered = int(np.isfinite(np.diagonal(m)).sum())
    assert covered == meta["stores_with_photo"] == 1495
    assert m.shape[0] - covered == 134
    assert meta["repr"] == "single"           # MaxSim 행렬은 만들지 않는다 (Step 3b 결정 3번)
    assert meta["embed_sha256"]               # 어느 임베딩에서 나왔는지 적혀 있다


def test_공방문은_NaN_이_없다():
    m = np.asarray(np.load(SERVING / "cooc.npy"), dtype=np.float32)
    assert np.isfinite(m).all() and (m >= 0).all() and (m <= 1.0001).all()


def test_사전값이_성분을_전부_덮는다():
    prior = json.loads((SERVING / "prior.json").read_text(encoding="utf-8"))
    assert set(prior["mean"]) == set(content.COMPONENTS)
    assert set(prior["sd"]) == set(content.COMPONENTS)
    assert prior["components"] == list(content.COMPONENTS)   # 순서까지 같다
    # 원천 파일(`analysis/backtest/results/serving_prior.json`)의 가중치가 그대로 실린다. 예전에는 인기가 눈금의
    # 기준(w_pop = 1)이었는데, 실제 스와이프로 다시 배운 시작값(Step 4)에서는 인기가 0.07 이다 — 기준을 박지 않고
    # '원천과 같다' 를 본다.
    alias = {"popularity": "pop"}
    fitted = {alias.get(k, k): v for k, v in (prior["raw"].get("weights") or prior["raw"].get("mean") or {}).items()}
    assert fitted and all(prior["mean"][c] == v for c, v in fitted.items())
    assert prior["source"]
    assert all(v > 0 for v in prior["sd"].values())


def test_원천에_없는_성분은_사전_평균이_0_이고_이유가_적혀_있다():
    """원천이 값을 주지 않은 성분(`dropped`)은 자리만 남기고 사전 평균 0 으로 싣는다.

    그 사람의 쌍대 피드백이 0 에서 밀어내면 살아난다. 왜 0 인지가 파일에 적혀 있어야 다음 사람이 '실수로
    빠졌나' 를 묻지 않는다. Step 3 사전값(`step3d-clip`)에서는 review·food 가 그랬고, 실제 스와이프로 배운
    Step 4 사전값(`real-swipes-0923`)은 여덟 성분 모두에 값을 준다(`step4-real-swipes.md`).
    """
    prior = json.loads((SERVING / "prior.json").read_text(encoding="utf-8"))
    alias = {"popularity": "pop"}
    fitted = {alias.get(k, k) for k in (prior["raw"].get("weights") or prior["raw"].get("mean") or {})}
    assert set(prior["dropped"]) == set(content.COMPONENTS) - fitted - set(prior["extra"])
    for c in prior["dropped"]:
        assert prior["mean"][c] == 0.0
        assert prior["sd"][c] > 0             # 폭은 남는다 — 못 배우는 성분이 아니다
        assert prior["dropped_note"][c]
    # `extra` 는 원천에 없을 때 얹는 값이고 `dropped` 와 겹치지 않는다.
    assert set(prior["extra"]) == {"neg"} and not set(prior["extra"]) & set(prior["dropped"])
    # 사전값과 서빙 점수는 같은 눈금(z 를 [−3, 3] 으로 자른 것)에서 나와야 한다 — 둘 다 그 눈금에서 배웠다.
    assert prior["raw"].get("z_clip") == 3.0
    assert prior["source"] in ("step3d-clip", "real-swipes-0923")


def test_설명용_리뷰_인덱스가_벡터와_맞는다():
    idx = json.loads((SERVING / "review_pairs.json").read_text(encoding="utf-8"))
    vecs = np.load(SERVING / "review_vecs.npy", mmap_mode="r")
    starts, counts = idx["starts"], idx["counts"]
    assert len(starts) == len(counts) == len(idx["snippets"])
    assert starts[-1] + counts[-1] == vecs.shape[0]
    assert max(counts) <= idx["max_per_store"]
    for i in (0, len(starts) // 2, len(starts) - 1):
        assert len(idx["snippets"][i]) == counts[i] == len(idx["review_ids"][i])


def test_layout_artifact(manifest):
    """취향 지형도(`layout.json`) — 좌표·이웃이 후보 풀과 같은 순서인가.

    지도의 i 번째 점이 `pool_ids[i]` 가 아니면 관리자는 남의 가게를 보며 추천을
    해석한다. 유사도 행렬과 같은 위험이라 같은 방식으로 잠근다. 화면이 매번 받는
    파일이므로 크기도 함께 본다.
    """
    layout = json.loads((SERVING / "layout.json").read_text(encoding="utf-8"))
    ids = json.loads((SERVING / "pool_ids.json").read_text(encoding="utf-8"))
    assert layout["ids"] == ids == swipe.pool()["ids"]
    assert layout["count"] == len(ids)

    x, y = np.array(layout["x"]), np.array(layout["y"])
    assert x.shape == y.shape == (len(ids),)
    assert x.min() >= 0 and x.max() <= 1 and y.min() >= 0 and y.max() <= 1
    assert max(np.ptp(x), np.ptp(y)) > 0.99    # 두 축을 같은 배율로 줄였다

    k = layout["neighbors"]
    nbr = np.array(layout["nbr"])
    sim = np.array(layout["nbr_sim"])
    assert k == 5 and nbr.shape == sim.shape == (len(ids), k)
    assert (nbr != np.arange(len(ids))[:, None]).all()          # 자기 자신은 이웃이 아니다
    assert (np.diff(sim, axis=1) <= 1e-6).all()                 # 유사도 내림차순
    cross = np.array(layout["nbr_cross"], dtype=bool)
    cat = np.array(layout["cat"])
    assert (cross == (cat[:, None] != cat[nbr])).all()
    assert abs(float(cross.mean()) - layout["stats"]["cross_category_share"]) < 1e-3

    # 무엇으로 어떻게 그렸는지가 manifest 와 산출물 양쪽에 적혀 있다.
    meta = manifest["layout"]
    assert meta["method"] == layout["method"] in ("umap", "tsne")
    assert meta["weights"] == layout["weights"] == {"menu": 0.25, "text": 0.40,
                                                    "review": 0.20, "food": 0.20}
    for name, src in layout["sources"].items():
        assert src["sha256"] == manifest["outputs"][name]["sha256"], name

    mb = (SERVING / "layout.json").stat().st_size / 1e6
    assert mb <= 1.5, f"{mb:.2f}MB — 화면이 열 때마다 받는 파일이다"


def test_manifest_해시가_실제_파일과_같다(manifest):
    for name, meta in manifest["outputs"].items():
        path = SERVING / name
        assert path.stat().st_size == meta["bytes"], name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == meta["sha256"], name


def test_이미지에_들어갈_크기(manifest):
    total = manifest["total_bytes"] / 1e6
    assert total <= MAX_TOTAL_MB, f"{total:.1f}MB > {MAX_TOTAL_MB}MB"


def test_manifest_가_출처를_적는다(manifest):
    assert manifest["pool"]["verified_against_backtest"] is True
    assert manifest["params"]["eval_user_exclusion"] is False    # 프로덕션 빌드
    assert manifest["modalities"]["prior"]["source"]
    assert manifest["inputs"]["data/kakao/stores.jsonl"]["sha256"]


def test_컨테이너가_서버_데이터를_붙인다():
    """데이터(serving 포함)는 이미지가 아니라 compose 가 붙인 서버 폴더에서 온다.

    2026-09-14 부터 data/kakao 는 git 과 이미지에서 빠졌다. EC2 배치가 매주 갈아
    끼우므로 이미지에 구우면 배포할 때마다 옛 데이터로 돌아간다. 붙이는 줄이 사라지면
    컨테이너가 데이터 없이 뜨고, serving 이 없으면 legacy 정책으로만 조용히 돈다.
    """
    root = Path(content.ROOT)
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert ":/app/data/kakao" in compose
    assert "COPY data/kakao" not in (root / "server" / "Dockerfile").read_text(
        encoding="utf-8")
