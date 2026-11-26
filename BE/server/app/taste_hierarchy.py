"""Read-only category drill-down on the existing, fixed taste-map coordinates."""
from __future__ import annotations

import base64
import json
import math
import statistics
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import loader
from recsys import content, store

router = APIRouter(tags=["취향"])
LAYOUT_PATH = content.SERVING / "layout.json"
_lock = threading.Lock()
_cache: dict = {"key": None, "payloads": {}}


class TasteMapNodeOut(BaseModel):
    key: str = Field(description="안정적인 항목 키. 같은 세부 분류명도 상위 분류별로 구분")
    name: str = Field(description="분류 이름 또는 가게 이름")
    x: float = Field(description="기존 지형 좌표. 분류는 구성원 x의 중앙값")
    y: float = Field(description="기존 지형 좌표. 분류는 구성원 y의 중앙값")
    r: float = Field(description="중심까지 거리의 중앙값. 가게는 0; 분류의 경계가 아님")
    count: int = Field(description="포함된 가게 수. 개별 가게는 1")
    card_id: int | None = Field(description="가게 상세 GET /cards/{card_id} 연결. 분류는 null")


class TasteMapChildrenOut(BaseModel):
    level: Literal["cat2", "cat3", "store"] = Field(description="반환하는 항목의 단계")
    nodes: list[TasteMapNodeOut] = Field(description="선택한 분류의 바로 아래 항목. parent 생략 시 cat2")


def _key(level: str, value) -> str:
    # JSON null stays distinct from a category literally named '분류 없음'.
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return level + "." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _category(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
        return str(path), st.st_mtime_ns, st.st_size
    except FileNotFoundError:
        return str(path), None, None


def _group(key: str, name: str, members: list[dict]) -> dict:
    x = statistics.median(row["x"] for row in members)
    y = statistics.median(row["y"] for row in members)
    radius = statistics.median(math.dist((row["x"], row["y"]), (x, y)) for row in members)
    return {"key": key, "name": name, "x": round(x, 4), "y": round(y, 4),
            "r": round(radius, 4), "count": len(members), "card_id": None}


def _build(layout: dict) -> dict:
    metadata = {row["store_id"]: row for row in store.all_stores()}
    groups: dict = {}
    all_stores = []
    names = layout.get("name") or []
    for i, sid in enumerate(layout["ids"]):
        cid = loader.card_id_by_store.get(sid)
        if not isinstance(cid, int) or isinstance(cid, bool):
            # Startup loads the catalogue. Never assign/persist IDs from this read API,
            # or silently drop a member whose detail link is not ready.
            raise HTTPException(503, "취향 지도의 카드 카탈로그가 준비되지 않았습니다")
        row = metadata.get(sid) or {}
        cat2, cat3 = _category(row.get("cat2")), _category(row.get("cat3"))
        node = {"key": _key("store", sid),
                "name": row.get("name") or (names[i] if i < len(names) else sid),
                "x": layout["x"][i], "y": layout["y"][i],
                "r": 0, "count": 1, "card_id": cid}
        all_stores.append(node)
        groups.setdefault(cat2, {}).setdefault(cat3, []).append(node)

    result = {None: {"level": "cat2", "nodes": []}}
    all_cat3 = []
    for cat2, subgroups in groups.items():
        parent = _key("cat2", cat2)
        children = []
        members = []
        for cat3, nodes in subgroups.items():
            key = _key("cat3", [cat2, cat3])
            children.append(_group(key, cat3 if cat3 is not None else "세부분류 없음", nodes))
            members.extend(nodes)
            result[key] = {"level": "store", "nodes": nodes}
        result[parent] = {"level": "cat3", "nodes": children}
        all_cat3.extend(children)
        result[None]["nodes"].append(
            _group(parent, cat2 if cat2 is not None else "분류 없음", members))
    # Integer keys are global detail levels; existing string parent keys stay
    # unchanged. Build and serialize each flat view once with the shared cache.
    result[1] = {"level": 1, "nodes": result[None]["nodes"]}
    result[2] = {"level": 2, "nodes": all_cat3}
    result[3] = {"level": 3, "nodes": all_stores}
    return {parent: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            for parent, value in result.items()}


def _payload(parent: str | int | None) -> bytes:
    if not LAYOUT_PATH.exists():
        raise HTTPException(503, "취향 지형도 산출물이 없습니다")
    # IDs are already loaded by startup. Include the in-memory mapping so a
    # catalogue refresh cannot leave cached links pointing at different cards.
    key = (_stamp(LAYOUT_PATH), _stamp(store.STORES), tuple(loader.card_id_by_store.items()))
    with _lock:
        if _cache["key"] != key:
            payloads = _build(json.loads(LAYOUT_PATH.read_bytes()))
            _cache.update(key=key, payloads=payloads)
        payload = _cache["payloads"].get(parent)
    if payload is None:
        raise HTTPException(404, "취향 지도 분류를 찾을 수 없습니다")
    return payload


def read_children(parent: str | None = None) -> dict:
    """Reuse the cached background in a user response without sharing mutable dicts."""
    return json.loads(_payload(parent))


def read_level(detail: int = 1) -> dict:
    """All nodes at one global detail level, reusing the serialized hierarchy."""
    if type(detail) is not int or detail not in (1, 2, 3):
        raise HTTPException(422, "detail은 1, 2, 3 중 하나여야 합니다")
    return json.loads(_payload(detail))


@router.get("/taste/map/children", response_model=TasteMapChildrenOut,
            summary="취향 지도 확대 — cat2 → cat3 → 가게")
def children(parent: str | None = None):
    """parent 생략 시 cat2, 반환된 분류 key를 전달하면 바로 아래 항목만 반환합니다.

    분류가 없어도 별도 항목으로 남습니다. 개별 가게 key나 없는 key는 404입니다.
    고정 배경의 조회이며 사용자 취향·추천·노출 이력을 변경하지 않습니다.
    """
    return Response(_payload(parent), media_type="application/json",
                    headers={"Cache-Control": "no-cache"})
