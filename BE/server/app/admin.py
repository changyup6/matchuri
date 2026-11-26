"""관리자 화면 — 사용자별로 추천이 어떻게 학습되고 있는지 본다.

추천이 잘 되는지는 응답만 봐서는 알 수 없다. 이 화면은 세 가지를 보여준다.

  ① 무엇을 배웠나   카테고리·가격대 가중치 (양수 = 선호)
  ② 어떻게 배웠나   스와이프에 따른 품질가중 감소와 선호율 변화
  ③ 그래서 뭘 줄까  다음 추천 카드와 그 점수의 품질/개인화 분해

Step 4/5 로 정책이 둘이 됐다(`recsys/policy.py`). 그래서 두 가지가 더 붙는다.

  ④ 새 정책은 뭘 배웠나   `/api/users/{id}` 의 `taste` — 성분 가중치·프로토타입·다음 카드
  ⑤ 어느 정책이 나은가     `/api/policies` — 노출 × 선호 조인, 정책별 like 율과 Wilson 구간
  ⑥ 취향이 **어디에** 있나  `/map` — 후보 풀 2,216곳의 2-D 지형도 위에 그 사람의 점수

⑥ 은 **읽기 전용**이다. 지형도 API 는 카드를 '보여줬다' 고 표시하지 않고, 노출 로그를
쓰지 않고, 어떤 모델도 갱신하지 않는다(`tests/test_map.py` 가 그것을 잰다). 지도에서
유일하게 상태를 바꾸는 것은 화면의 스와이프 버튼이고 그것은 평소와 같은
`POST /preferences` 다 — 화면에도 그렇게 적혀 있다.

세션은 프로세스 메모리에 있으므로 서버를 재시작하면 여기 목록도 비워진다(선호 기록은
`state/` 에 남아 기동할 때 다시 먹인다).
"""
from __future__ import annotations

import collections
import json
import threading
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from app import catalog_media, loader, state, user_reviews
from app import users as user_registry
from app import recommendation_sessions as rec_sessions, identities
from recsys import content, decision, policy, store as svc, swipe, taste

router = APIRouter(prefix="/admin", tags=["관리자"])
STATIC = Path(__file__).parent / "static"


def _card_brief(store_id: str) -> dict:
    """카드 한 장의 최소 정보. 목록에 썸네일로 뿌린다."""
    cid = loader.card_id_by_store.get(store_id)
    s = svc.get_store(store_id) or {}
    photos = svc.get_photos(store_id, limit=1)
    return {
        "card_id": cid,
        "store_id": store_id,
        "name": s.get("name"),
        "category": s.get("cat3") or s.get("cat2"),
        "score": s.get("review_avg"),
        "review_count": s.get("review_count") or 0,
        "image": catalog_media.public_url(photos[0]) if photos else None,
    }


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return (STATIC / "admin.html").read_text(encoding="utf-8")


@router.get("/api/stats", summary="전체 현황")
def stats():
    """서버 전체 지표. 대시보드 상단에 띄운다."""
    loader.load_all()
    ss = swipe.sessions()
    n_sw = sum(s["n"] for s in ss.values())
    n_like = sum(s["likes"] for s in ss.values())
    # 카테고리별 선호율 (모든 사용자 합산) — 어떤 카테고리가 잘 먹히는지
    agg: dict[str, list[int]] = {}
    for s in ss.values():
        for cat, (shown, liked) in (s.get("cat_stats") or {}).items():
            a = agg.setdefault(cat, [0, 0])
            a[0] += shown
            a[1] += liked
    cats = [{"name": k, "shown": v[0], "liked": v[1],
             "rate": round(v[1] / v[0], 3) if v[0] else None}
            for k, v in sorted(agg.items(), key=lambda kv: -kv[1][0])]
    return {
        "cards": len(loader.cards),
        "recommendable": len(swipe.pool()["ids"]),
        "users": len(identities._people),
        "sessions": len(ss),
        "swipes": n_sw,
        "likes": n_like,
        "like_rate": round(n_like / n_sw, 3) if n_sw else None,
        # 가게 화면에 실린 사용자 후기 수. **이 숫자가 여기 있었으면** 2026-09-21 의
        # 사고를 아무도 놓치지 않았을 것이다 — 운영 카드 2,795장을 전수 조회할 때까지
        # 후기가 1건뿐인 것을 몰랐다. 세지 않는 것은 없는 것과 같다.
        "user_reviews": user_reviews.count(),
        "categories": cats,
        "policy": policy.active(),
        "configured_policy": policy.configured(),
    }


@router.get("/api/policies", summary="정책별 성적 — 팀-드래프트 인터리빙 결과")
def policies():
    """노출 기록(`state/exposures.jsonl`)과 선호 기록을 카드 단위로 조인한 결과.

    한 화면(10장)을 두 정책이 번갈아 채우고 있으므로, 정책별 like 율의 차이가 곧
    온라인 실험 결과다. **구간이 겹치면 아직 모르는 것이다** — `verdict` 가 그걸
    한국어로 말해 준다. 노출은 서버가 카드를 실제로 내보낸 순간에만 쌓이고, 기동
    복원(재생)에서는 쌓이지 않는다.
    """
    return policy.outcome_stats()


@router.get("/api/surveys", summary="설문 결과 — 정책별 방문율과 만족도")
def surveys():
    """길찾기·설문을 노출 기록과 카드 단위로 조인한 결과.

    `/api/policies` 의 like 율은 **대리 지표**다(카드를 보고 넘긴 것뿐이다). 여기서
    재는 것은 실제로 가서 어땠는지다. 퍼널이 그대로 보인다:

        노출 -> 길찾기 -> 설문 -> 방문 -> 만족

    - `response_rate` : 길찾기한 가게 중 설문에 답한 비율. 이게 낮으면 아래 숫자는
      표본이 작아 믿기 어렵다(배너 문구·시점부터 손봐야 한다)
    - `satisfied_rate` : `satisfaction == "good"` 비율. `soso` 는 만족으로 세지 않는다
    - `none` 버킷 : `user_id` 없이 받은 카드(정책이 `popular`)의 길찾기다. 버리면
      퍼널 총계가 맞지 않아 응답률을 못 믿는다
    - `verdict` : 95% 구간이 겹치면 **아직 모르는 것**이라고 한국어로 말해 준다
    """
    return policy.survey_stats()


# ────────────────────────────────────────────────────── 트렌드 (읽기 전용)
@router.get("/api/trend", summary="트렌드 층 상태와 지금 뜨는 가게")
def trend_status(
    limit: int = Query(20, ge=1, le=100, description="상위 몇 곳"),
):
    """`recsys/trend.py` 가 지금 무엇을 '뜬다' 고 보는가. **읽기 전용**이다.

    `enabled` 가 `false` 면 추천에는 **아무 영향이 없다**(기본값). 그래도 점수는
    계산해서 보여준다 — 켜기 전에 무엇이 올라올지 먼저 보라는 화면이다.

    `stale` 이 `true` 면 크롤이 낡아 트렌드가 통째로 꺼진 상태다. '최근 180일' 이
    실제로는 반년 전 이야기라는 뜻이라, 0 을 조용히 주는 대신 끈다.
    """
    from recsys import trend

    st = trend.status()
    ids = list(swipe.pool()["ids"])
    z = trend.scores(ids)
    top = sorted(z.items(), key=lambda kv: -kv[1])[:limit] if z else []
    return {**st, "pool": len(ids),
            "top": [{**_card_brief(sid), "trend": round(v, 3),
                     **(trend.explain(sid) or {})} for sid, v in top]}


@router.get("/api/trend/compare", summary="트렌드 적용 전후 비교 — 카드를 소비하지 않는다")
def trend_compare(
    user_id: str = Query(..., description="비교할 사용자"),
    policy_name: str = Query("taste", alias="policy", description="legacy | taste"),
    limit: int = Query(10, ge=1, le=50, description="비교할 카드 수"),
    weight: float | None = Query(None, description="미리 볼 무게. 생략하면 지금 설정값"),
):
    """그 사용자의 다음 카드를 **트렌드 없이 / 있이** 나란히 보여준다.

    왜 이 모양인가 — 트래픽을 쪼개 A/B 를 하면 지금 돌고 있는 legacy·taste 인터리빙의
    검정력이 3분의 1로 떨어진다. 사용자 70여 명에 선호 2,500건으로는 그 비교가
    먼저 죽는다. 그래서 **같은 사용자의 같은 세션을 두 번 채점해** 차이만 본다 —
    표본을 나누지 않으므로 실험에 비용이 0 이다.

    `peek` 와 같은 규칙이다: Thompson 난수 없이 사후 평균으로 매기고, `shown` 에
    넣지 않고, 노출 로그를 쓰지 않고, 어떤 모델도 갱신하지 않는다.

    `moved_in` 은 트렌드 때문에 **새로 상위 N 에 들어온** 카드다. 이게 비어 있으면
    그 무게로는 아무것도 안 바뀐다는 뜻이다.
    """
    from recsys import trend

    if policy_name not in ("legacy", "taste"):
        raise HTTPException(400, "policy 는 legacy 또는 taste 여야 합니다")
    sess = policy.session_for(user_id)
    mod = swipe if policy_name == "legacy" else taste

    #: 상위 N 만 보면 '무엇이 밀려 올라왔나' 를 볼 수 없다 — 넉넉히 떠서 다시 정렬한다.
    wide = mod.peek(sess[policy_name], max(limit * 20, 200))
    if not wide:
        raise HTTPException(404, "그 정책의 다음 카드를 계산할 수 없습니다 "
                                 "(서빙 산출물이 없을 수 있습니다)")

    w = trend.weight() if weight is None else float(weight)
    z = trend.scores([r["store_id"] for r in wide])
    after = sorted(wide, key=lambda r: -(r["total"] + w * z.get(r["store_id"], 0.0)))

    base_rank = {r["store_id"]: i for i, r in enumerate(wide)}
    before_ids = [r["store_id"] for r in wide[:limit]]
    rows = []
    for rank, r in enumerate(after[:limit]):
        sid = r["store_id"]
        rows.append({**_card_brief(sid),
                     "rank": rank,
                     "rank_before": base_rank[sid],
                     "moved": base_rank[sid] - rank,
                     "score_before": r["total"],
                     "trend": round(z.get(sid, 0.0), 3),
                     "score_after": round(r["total"] + w * z.get(sid, 0.0), 3)})
    after_ids = [r["store_id"] for r in after[:limit]]
    return {
        "user_id": user_id, "policy": policy_name, "weight": w,
        "trend_enabled": trend.enabled(),
        "swipes": sess[policy_name].get("n", 0),
        "changed": before_ids != after_ids,
        "moved_in": [_card_brief(s) for s in after_ids if s not in before_ids],
        "moved_out": [_card_brief(s) for s in before_ids if s not in after_ids],
        "before": [_card_brief(s) for s in before_ids],
        "after": rows,
    }


@router.get("/api/users", summary="사용자 목록")
def users():
    """스와이프한 사용자 목록. 최근에 많이 넘긴 순."""
    out = []
    for uid, s in swipe.sessions().items():
        hist = s.get("hist") or []
        recent = hist[-10:]
        out.append({
            "user_id": uid,
            "owner_user_id": identities.owner(uid),
            "session_id": uid,
            "name": user_registry.name_of(uid),
            "swipes": s["n"],
            "likes": s["likes"],
            "like_rate": round(s["likes"] / s["n"], 3) if s["n"] else None,
            "recent_like_rate": (round(sum(1 for h in recent if h["like"]) / len(recent), 3)
                                 if recent else None),
            "quality_weight": round(1 / (1 + s["n"] / 15), 3),
            "seen": len(s["shown"]),
            "created": s["created"],
        })
    out.sort(key=lambda x: (-x["swipes"], x["user_id"]))
    return out


@router.get("/api/users/{user_id}", summary="사용자 상세 — 학습 상태와 다음 추천")
def user_detail(
    user_id: str,
    next_n: int = Query(6, ge=0, le=30, description="미리 볼 다음 추천 카드 수"),
    history_n: int = Query(40, ge=0, le=200, description="되짚을 스와이프 수"),
):
    """한 사용자의 학습 상태·스와이프 이력·다음 추천을 한 번에 준다.

    `next` 의 `quality` / `personal` 은 그 카드 점수를 만든 두 항이다.
    스와이프 초반에는 `quality` 가, 쌓일수록 `personal` 이 커진다.
    """
    ss = swipe.sessions()
    if user_id not in ss:
        raise HTTPException(404, "session not found")
    sess = ss[user_id]
    learn = swipe.learn_state(sess)

    hist = (sess.get("hist") or [])[-history_n:]
    swiped = []
    for h in reversed(hist):          # 최근 것부터
        sid = h.get("store_id")
        if not sid:
            continue
        b = _card_brief(sid)
        b["prefer"] = "like" if h["like"] else "dislike"
        b["n"] = h["n"]
        swiped.append(b)

    nxt = []
    for x in swipe.peek(sess, next_n):
        b = _card_brief(x["store_id"])
        b.update(quality=x["quality"], personal=x["personal"], total=x["total"])
        nxt.append(b)

    return {
        "user_id": user_id,
        "name": user_registry.name_of(user_id),
        "swipes": sess["n"],
        "likes": sess["likes"],
        "like_rate": round(sess["likes"] / sess["n"], 3) if sess["n"] else None,
        "seen": len(sess["shown"]),
        "quality_weight": learn["quality_weight"],
        "weights": {"base": learn["base"], "cats": learn["cats"],
                    "price": learn["price"]},
        "history": learn["hist"],
        "swiped": swiped,
        "next": nxt,
        # ── Step 4/5. 기존 필드는 그대로 두고 새 정책을 **덧붙인다.**
        "policy": policy.active(),
        "taste": _taste_block(user_id, next_n),
    }


def _taste_block(user_id: str, next_n: int) -> dict:
    """새 정책이 그 사용자에게서 배운 것 — 성분 가중치·프로토타입·다음 카드.

    성분 기여(`parts`)를 카드마다 같이 주므로 '왜 이게 위에 있나' 를 legacy 의
    품질/개인화 분해와 같은 방식으로 읽을 수 있다.
    """
    sess = taste.sessions().get(user_id)
    if sess is None:
        return {"available": False}
    learn = taste.learn_state(sess)       # 이름을 `state` 로 두면 모듈 `app.state` 를 가린다
    nxt = []
    for x in taste.peek(sess, next_n):
        b = _card_brief(x["store_id"])
        b.update(total=x["total"], parts=x["parts"])
        nxt.append(b)
    return {**learn, "next": nxt}


# ══════════════════════════════════════════════════════════════════════════
# 취향 지형도 (`/admin/map`) — 읽기 전용
#
# 관리자 화면이 답하지 못하는 질문이 하나 있다: **그 사람의 취향이 어디에 있는가.**
# 막대와 숫자는 '무엇을 얼마나 좋아하는가' 는 말하지만 '2,216곳 중 어디' 는 말하지
# 않는다. 지형도는 후보 풀을 결합 유사도로 한 번 펴서(`data/kakao/serving/layout.json`)
# **모든 사용자가 같은 땅**을 보게 하고, 그 위에 사람마다 다른 점수를 색으로 얹는다.
#
# 여기 있는 것은 전부 **읽기만** 한다:
#   · 카드를 `shown` 에 넣지 않는다 (`taste.pick_many` 를 부르지 않는다)
#   · 노출 로그(`state/exposures.jsonl`)를 쓰지 않는다
#   · 어떤 세션의 m·v 도 바꾸지 않는다 — `upto=` 재생도 **등록하지 않은** 새 세션이다
# 그래서 관리자가 지도를 아무리 돌려도 인터리빙 실험이 오염되지 않는다.
# ══════════════════════════════════════════════════════════════════════════

#: 지도에 고리로 표시할 상위 카드 수.
MAP_TOP = 10
#: 연결선 라벨에 쓸 리뷰 스니펫 길이.
MAP_SNIPPET = 60
#: 메뉴 쌍을 찾을 때 볼 메뉴 수(가게당). 앞쪽이 추천·대표 메뉴다.
MAP_MENUS = 40
#: `upto=` 재생 결과 캐시 크기. 스크러버를 끌면 같은 값이 계속 들어온다.
MAP_REPLAY_CACHE = 64

_map_lock = threading.Lock()
_layout_cache: dict = {"bytes": None, "key": None}
_prefs_cache: dict = {"key": None, "by_user": {}}
_replay_cache: "collections.OrderedDict[tuple, dict]" = collections.OrderedDict()

LAYOUT_PATH = content.SERVING / "layout.json"


@router.get("/map", response_class=HTMLResponse, include_in_schema=False)
@router.get("/map/", response_class=HTMLResponse, include_in_schema=False)
def map_page():
    return (STATIC / "map.html").read_text(encoding="utf-8")


def _layout_payload() -> bytes:
    """고정 지형에 원본 업종 두 단계만 붙여 직렬화된 바이트를 캐시한다.

    좌표·기존 분류 인덱스는 건드리지 않는다. layout 또는 stores 파일이 바뀔 때만
    다시 읽고 직렬화하므로 반복 요청은 가게별 메타데이터 조회도 하지 않는다.
    """
    if not LAYOUT_PATH.exists():
        raise HTTPException(503, "지형도 산출물이 없습니다 — "
                                 "`python -m analysis.build_serving --only layout`")
    st = LAYOUT_PATH.stat()
    try:
        stores_st = svc.STORES.stat()
        stores_key = (str(svc.STORES), stores_st.st_mtime_ns, stores_st.st_size)
    except FileNotFoundError:
        stores_key = (str(svc.STORES), None, None)
    key = (str(LAYOUT_PATH), st.st_mtime_ns, st.st_size, stores_key)
    with _map_lock:
        if _layout_cache["key"] == key and _layout_cache["bytes"] is not None:
            return _layout_cache["bytes"]
    raw = LAYOUT_PATH.read_bytes()
    layout = json.loads(raw)
    ids = swipe.pool()["ids"]
    if list(layout.get("ids") or []) != list(ids):
        # 좌표의 i 번째가 그 가게가 아니면 지도는 조용히 거짓말을 한다. 그리지 않는다.
        raise HTTPException(503, f"지형도가 후보 풀과 다릅니다 "
                                 f"(지도 {len(layout.get('ids') or [])}곳 vs 서버 {len(ids)}곳) — "
                                 f"`python -m analysis.build_serving` 를 다시 도세요")
    stores = [svc.get_store(sid) or {} for sid in ids]
    for field in ("cat2", "cat3"):
        layout[field] = [value if isinstance(value, str) and value.strip() else None
                         for row in stores for value in (row.get(field),)]
    raw = json.dumps(layout, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with _map_lock:
        _layout_cache.update(bytes=raw, key=key)
    return raw


@router.get("/api/map/layout", summary="취향 지형도 — 좌표·메타·이웃 (읽기 전용)")
def map_layout():
    """후보 풀 2,216곳의 고정 좌표와 5-최근접 이웃. 사용자와 무관하다.

    `analysis/build_serving.py` 가 결합 유사도 S(메뉴 .25 · 요약문 .40 · 리뷰 .20 ·
    음식 사진 .20)에서 거리 1−S 를 만들고 UMAP(precomputed, 씨앗 고정)으로 편 것이다.
    좌표는 [0,1]² 로 정규화돼 있고 **두 축의 배율이 같다**.

    `cat2`·`cat3` 는 `ids` 와 같은 순서의 원본 업종 문자열 배열이며 결측은 null 이다.
    기존 `cats`·`cat` 인덱스와 좌표는 보존한다. 같은 cat3 이름도 cat2 별로 구분한다.

    **주의: UMAP 은 국소 이웃만 보존한다.** 멀리 떨어진 두 점 사이의 거리는 아무 뜻이
    없다. 그래서 `nbr`(5-NN 인덱스)·`nbr_sim`·`nbr_cross` 를 함께 준다 — 화면이 진짜
    이웃을 선으로 그려 좌표가 하는 거짓말을 눈으로 잡을 수 있어야 한다.
    """
    return Response(content=_layout_payload(), media_type="application/json")


# ────────────────────────────────────────────────── 선호 기록 재생 (`upto=`)
def _prefs_by_user() -> dict:
    """`state/preferences.jsonl` 을 사용자별로 갈라 캐시한다 (mtime·크기로 무효화)."""
    path = state.PREFS
    key = None
    if path.exists():
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    with _map_lock:
        if _prefs_cache["key"] == key:
            return _prefs_cache["by_user"]
    by: dict[str, list] = {}
    for rec in state._read(state.PREFS):
        if rec.get('event') == 'session_start':
            archived = rec.get('previous_session_id') or rec_sessions.legacy_id(rec['user_id'])
            by[archived] = list(by.get(rec['user_id'], []))
            by[rec['user_id']] = []
            continue
        if rec.get('event') == 'session_create':
            by.setdefault(rec['session_id'], [])
            continue
        uid, cid, pref = state.model_id(rec), rec.get("card_id"), rec.get("prefer")
        if uid is None or cid is None or pref not in ("like", "dislike"):
            continue
        by.setdefault(uid, []).append((int(cid), pref == "like"))
    with _map_lock:
        _prefs_cache.update(key=key, by_user=by)
    return by


def _replayed(user_id: str, upto: int) -> tuple[dict, int]:
    """그 사용자의 **처음 n 장**만 먹인 임시 세션. 살아 있는 세션은 건드리지 않는다.

    캐시 열쇠에 그 사용자의 전체 기록 수를 넣는다 — 기록은 추가만 되므로 앞 n 장은
    변하지 않지만, 새 스와이프가 들어오면 열쇠가 달라져 낡은 값을 붙들지 않는다.
    """
    rows = _prefs_by_user().get(user_id, [])
    n = max(0, min(int(upto), len(rows)))
    key = (user_id, rec_sessions.current_id(user_id), n, len(rows))
    with _map_lock:
        hit = _replay_cache.get(key)
        if hit is not None:
            _replay_cache.move_to_end(key)
            return hit, len(rows)
    feedback = []
    for cid, like in rows[:n]:
        sid = loader.to_store_id(cid)
        if sid is not None:
            feedback.append((sid, like))
    sess = rec_sessions.initial_taste(user_id)
    for sid, like in feedback:
        taste.record(sess, sid, like)
    with _map_lock:
        _replay_cache[key] = sess
        while len(_replay_cache) > MAP_REPLAY_CACHE:
            _replay_cache.popitem(last=False)
    return sess, len(rows)


# ────────────────────────────────────────────────── 연결선의 근거 문장
def _bigrams(s: str) -> set:
    s = "".join((s or "").split())
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def _menu_pair(liked: str, cand: str) -> tuple[list[str], str] | None:
    """두 가게의 메뉴 이름 한 쌍과 **그 쌍을 어떻게 골랐는지**.

    점수의 메뉴 성분은 메뉴 임베딩 MaxSim 이지만 서버 이미지에는 메뉴 벡터가 실리지
    않는다(`data/kakao/serving` 에는 유사도 행렬만 있다). 그래서 '모델이 맞춘 그 쌍'
    을 여기서 복원할 수 없다 — 대신 이름이 겹치는 쌍을 보여주고 `pair_kind` 로 무엇을
    보고 고른 것인지 밝힌다. 리뷰 쌍(`content.review_pair`)만이 **벡터가 고른 쌍**이다.

      name   글자(2-gram)가 겹치는 쌍 — 모델이 본 것과 다를 수 있다
      top    겹치는 글자가 없어 그냥 각 가게의 대표 메뉴를 나란히 둔 것
    """

    a = [m.get("menu_name") or "" for m in svc.get_menus(liked, limit=MAP_MENUS)]
    b = [m.get("menu_name") or "" for m in svc.get_menus(cand, limit=MAP_MENUS)]
    a = [x for x in a if x][:MAP_MENUS]
    b = [x for x in b if x][:MAP_MENUS]
    if not a or not b:
        return None
    ga, gb = [_bigrams(x) for x in a], [_bigrams(x) for x in b]
    best, pair = 0.0, None
    for i, x in enumerate(ga):
        for j, y in enumerate(gb):
            if not x or not y:
                continue
            v = len(x & y) / max(1, min(len(x), len(y)))
            if v > best:
                best, pair = v, [a[i], b[j]]
    if pair is not None and best > 0:
        return pair, "name"
    return [a[0], b[0]], "top"


#: 연결선 라벨에 쓸 '이 쌍을 무엇을 보고 골랐나'. 화면은 이 문장을 그대로 그린다
#: (성분 이름과 마찬가지로 **화면은 근거의 종류를 모른다**).
PAIR_LABEL = {"review": "벡터가 고른 리뷰 쌍",
              "name": "메뉴 이름이 겹치는 쌍 (벡터가 아니라 글자)",
              "top": "각 가게의 대표 메뉴 (겹치는 글자가 없음)"}


def _link(art, kind: str, h: int, i: int, part: float) -> dict:
    """상위 카드 i 를 밀어올린 성분 하나 -> 그 성분에서 가장 닮은 like 가게와 근거."""
    liked, cand = art.ids[h], art.ids[i]
    out = {"key": kind, "name": content.LABELS.get(kind, kind),
           "part": round(float(part), 3), "idx": int(h), "store_id": liked,
           "store_name": (svc.get_store(liked) or {}).get("name") or liked,
           "pair": None, "pair_kind": None, "pair_label": None, "cos": None}
    if kind == "review":
        a, b, cos = art.review_pair(h, i)
        if a and b:
            out.update(pair=[a[:MAP_SNIPPET], b[:MAP_SNIPPET]], pair_kind="review",
                       cos=(round(float(cos), 3) if cos == cos else None))
    elif kind == "menu":
        got = _menu_pair(liked, cand)
        if got is not None:
            out.update(pair=got[0], pair_kind=got[1])
    out["pair_label"] = PAIR_LABEL.get(out["pair_kind"])
    return out


def _top_card(art, sess: dict, st: dict, i: int, rank: int) -> dict:
    """상위 카드 한 장 — 성분 기여와 연결선까지."""
    z, w = st["z"], st["w"]
    parts = {c: round(float(w[j] * z[j, i]), 3)
             for j, c in enumerate(content.COMPONENTS)}
    links = []
    for kind in ("cf", "menu", "text", "review", "food"):
        if parts[kind] <= 0:
            continue
        h = art.best_like(kind, i, st["like"])
        if h is None:
            continue
        links.append(_link(art, kind, h, i, parts[kind]))
    links.sort(key=lambda d: -d["part"])
    brief = _card_brief(art.ids[i])
    # `scores` 와 **같은 자리수**로 반올림한다 — 화면이 두 값을 같은 카드에 나란히
    # 쓰므로(툴팁의 점수와 카드의 총점) 어긋나면 같은 것이 둘로 보인다.
    brief.update(idx=int(i), rank=rank, total=round(float(st["total"][i]), 4),
                 parts=parts, links=links,
                 reason=taste.recommendation_reason(sess, art.ids[i], art=art,
                                                    z=z[:, i], weights=w))
    return brief


def _map_focus(st: dict, i: int) -> dict:
    """실제 채점식의 가산 성분. 콜드스타트의 고정 인기/평판 가중치도 반영한다."""
    w = st["w"].copy()
    if st["cold"]:
        w[:] = 0
        w[content.IDX["pop"]] = 1
        w[content.IDX["quality"]] = 0.5
        w[content.IDX["neg"]] = st["w"][content.IDX["neg"]]
    return {"idx": i, "parts": {c: round(float(w[j] * st["z"][j, i]), 6)
                                for j, c in enumerate(content.COMPONENTS)}}


_trace_cache = collections.OrderedDict()
TRACE_INTERVAL = 5
TRACE_POINTS = 25


def _taste_anchor(art, st, weights):
    """Observed medoid of the top personal-score set, never a 2-D centroid.

    Coordinates do not enter this calculation. Unknown similarities are omitted,
    and the line is an overview of representative stores, not the taste vector.
    """
    if st['cold']:
        return None
    scores = st['w'][decision.PERSONAL] @ st['z'][decision.PERSONAL]
    top = sorted((i for i in range(len(art.ids)) if np.isfinite(scores[i]) and scores[i] > 0),
                 key=lambda i: (-scores[i], art.ids[i]))[:decision.DEFAULT.stable_top]
    if not top:
        return None
    if len(top) == 1:
        return int(top[0])
    sums = np.zeros((len(top), len(top)))
    known = np.zeros_like(sums)
    for kind, weight in weights.items():
        values = np.asarray(getattr(art, 'sim_' + kind)[np.ix_(top, top)], float)
        valid = np.isfinite(values)
        sums += np.where(valid, values, 0) * weight
        known += valid * weight
    np.fill_diagonal(known, 0)
    pairs = np.divide(sums, known, out=np.zeros_like(sums), where=known > 0)
    counts = (known > 0).sum(axis=1)
    support = np.divide(pairs.sum(axis=1), counts, out=np.full(len(top), -np.inf), where=counts > 0)
    if not np.isfinite(support).any():
        return None
    return int(top[int(np.argmax(support))])


@router.get('/api/map/user/{user_id}/trace', summary='취향 변화 경로 — 고차원 개인화 상위 후보의 대표 가게')
@rec_sessions.scoped
def map_trace(user_id: str, upto: int | None = Query(None, ge=0),
              session_id: str | None = Query(None)):
    """All feedback is replayed; only display points are sampled (at most 25).

    Each node is an actual store selected by weighted modality similarity among
    the top 20 positive personal-score stores, excluding popularity/reputation.
    Lines indicate temporal order; their 2-D length is not a change magnitude.
    The cached model is detached and never records exposure or user feedback.
    """
    rec_sessions.require_current(user_id, session_id)
    if user_id not in taste.sessions():
        raise HTTPException(404, 'session not found')
    art = content.load()
    if art is None:
        raise HTTPException(503, '서빙 산출물이 없습니다')
    layout = json.loads(_layout_payload())
    rows = _prefs_by_user().get(user_id, [])
    at = min(upto, len(rows)) if upto is not None else len(rows)
    key = (str(state.PREFS), user_id, rec_sessions.current_id(user_id), id(art), LAYOUT_PATH.stat().st_mtime_ns)
    with _map_lock:
        cached = _trace_cache.get(key)
        if cached is not None:
            _trace_cache.move_to_end(key)
    if cached is None or cached['at'] > at:
        model = rec_sessions.initial_taste(user_id)
        cached = {'at': 0, 'model': model, 'points': [
            {'n': 0, 'idx': _taste_anchor(art, taste.score_state(model), layout['weights'])}]}
    # Work on a detached copy so an interrupted computation cannot corrupt a cache entry.
    import copy
    cached = copy.deepcopy(cached)
    for n in range(cached['at'], at):
        cid, like = rows[n]
        sid = loader.to_store_id(cid)
        if sid is not None:
            taste.record(cached['model'], sid, like)
        if (n + 1) % TRACE_INTERVAL == 0:
            cached['points'].append({'n': n + 1, 'idx': _taste_anchor(
                art, taste.score_state(cached['model']), layout['weights'])})
    cached['at'] = at
    with _map_lock:
        _trace_cache[key] = cached
        while len(_trace_cache) > 16:
            _trace_cache.popitem(last=False)
    stride = TRACE_INTERVAL * max(1, int(np.ceil(at / (TRACE_INTERVAL * (TRACE_POINTS - 1)))))
    points = [point for point in cached['points'] if point['n'] % stride == 0]
    if points[-1]['n'] != at:
        points.append({'n': at, 'idx': _taste_anchor(art, taste.score_state(cached['model']), layout['weights'])})
    return {'session_id': rec_sessions.current_id(user_id), 'upto': at,
            'stride': stride, 'top_count': decision.DEFAULT.stable_top, 'points': points}


def _parse_weights(raw: str | None) -> np.ndarray | None:
    """`weights=1.0,0.3,…` — 성분 순서(`content.COMPONENTS`)대로 8개. 없으면 None."""
    if raw is None or not raw.strip():
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip() != ""]
    if len(parts) != content.N_COMPONENTS:
        raise HTTPException(400, f"weights 는 성분 {content.N_COMPONENTS}개를 "
                                 f"쉼표로 이어 주세요 (받은 것 {len(parts)}개)")
    try:
        w = np.array([float(p) for p in parts], dtype=float)
    except ValueError:
        raise HTTPException(400, "weights 에 숫자가 아닌 값이 있습니다") from None
    if not np.isfinite(w).all() or np.abs(w).max() > 100:
        raise HTTPException(400, "weights 는 유한하고 |w| ≤ 100 이어야 합니다")
    return w


@router.get("/api/map/user/{user_id}", summary="취향 지형도 — 한 사용자의 점수 지형 (읽기 전용)")
@rec_sessions.scoped
def map_user(
    user_id: str,
    weights: str | None = Query(None, description="성분 순서대로 쉼표 구분 8개. "
                                                  "주면 그 가중치로 다시 채점만 한다"),
    upto: int | None = Query(None, ge=0, description="처음 n 장만 먹인 시점의 모델"),
    focus: int | None = Query(None, ge=0, description="선택 가게의 풀 인덱스. 생략하면 아직 안 본 1위, 없으면 풀 첫 가게"),
    session_id: str | None = Query(None, description="비교 중 세션 전환 시 409로 거절"),
):
    """그 사용자의 취향이 지형도 **어디에** 있는지.

    - `scores` — 전체 후보의 점수(풀 순서). Thompson 난수가 없는 순수한 사후 평균
      점수라 같은 입력이면 항상 같다. `weights` 를 주면 **그 가중치로 다시 채점만**
      한다(모델은 한 톨도 안 바뀐다 — 슬라이더는 '만약' 을 묻는 도구다).
    - `top` — 아직 안 본 카드 중 점수 상위 10장. 카드마다 성분 기여 `parts` 와,
      그 성분에서 가장 닮은 **좋아요한 가게**로 가는 연결선 `links` 가 붙는다.
      리뷰 성분의 연결선만이 벡터가 고른 쌍이고(`pair_kind="review"`), 메뉴 쌍은
      글자로 고른 근사다(`"name"`/`"top"` — `_menu_pair` 참고).
    - `upto=n` — 그 사용자의 선호 기록 앞 n 장만 먹인 **임시** 모델로 채점한다.
      살아 있는 세션은 건드리지 않는다. 학습에 난수가 없어 `upto=전체` 는 살아 있는
      세션과 소수점까지 같다.
    - `feedback` — 조회 시점까지 최근 30회 반응. `n`은 현재 세션 내 기록 순서,
      `weight`는 현재 유효한 반응의 시간 감쇠 계수이며 점수 기여율이 아니다.
      재평가로 대체됐거나 학습 풀 밖이면 0이다. `idx`가 null이면 지도에 없는 가게다.
    - `focus` — 선택한 한 가게의 실제 채점 성분. 두 시점의 차이 합은 원점수 변화와
      같다(반올림 오차 제외). 각 반응의 인과 기여도를 의미하지 않는다.

    좋아요가 없으면 콜드스타트 점수(`인기 + 0.5·평판`)다 — `cold: true` 로 알린다.
    """
    art = content.load()
    if art is None:
        raise HTTPException(503, "서빙 산출물이 없어 취향 점수를 낼 수 없습니다")
    rec_sessions.require_current(user_id, session_id)
    if focus is not None and focus >= len(art.ids):
        raise HTTPException(400, "focus가 후보 풀 범위를 벗어났습니다")
    w = _parse_weights(weights)

    all_prefs = len(_prefs_by_user().get(user_id, []))
    if upto is None:
        sess = taste.sessions().get(user_id)
        if sess is None:
            raise HTTPException(404, "session not found")
    else:
        sess, all_prefs = _replayed(user_id, upto)

    st = taste.score_state(sess, weights=w)
    if st is None:
        raise HTTPException(503, "서빙 산출물이 없어 취향 점수를 낼 수 없습니다")
    total = st["total"]

    shown = sess["shown"]
    top = []
    for i in np.argsort(-total):
        i = int(i)
        if art.ids[i] in shown:
            continue
        top.append(_top_card(art, sess, st, i, len(top) + 1))
        if len(top) >= MAP_TOP:
            break

    m, sd = sess["m"], np.sqrt(sess["v"])
    rows = _prefs_by_user().get(user_id, [])
    at = min(upto, all_prefs) if upto is not None else all_prefs
    # 로그 순서와 모델 n은 풀 밖 피드백이 있을 때 다를 수 있다.
    last = {cid: n for n, (cid, _) in enumerate(rows[:at], 1)}
    feedback = []
    for n in range(max(0, at - 30), at):
        cid, like = rows[n]
        sid = loader.to_store_id(cid)
        active = last[cid] == n + 1 and sid in sess.get("evidence_at", {})
        weight = taste.RECENCY_DECAY ** max(0, sess["n"] - sess["evidence_at"][sid]) if active else 0
        feedback.append({"n": n + 1, "idx": art.pos.get(sid), "like": like,
                         "weight": float(weight)})
    return {
        "user_id": user_id,
        "name": user_registry.name_of(user_id),
        "session_id": rec_sessions.current_id(user_id),
        "history_effect": {"half_life_swipes": taste.RECENCY_HALF_LIFE,
                           "previous_share": taste.MEMORY_SHARE * taste.RECENCY_DECAY ** sess["n"] if sess.get("memory") else 0},
        "upto": min(upto, all_prefs) if upto is not None else None,
        "prefs": all_prefs,
        "swipes": sess["n"],
        "cold": bool(st["cold"]),
        "override": w is not None,
        "weights": [{"key": c, "name": content.LABELS[c],
                     "w": round(float(m[j]), 3), "sd": round(float(sd[j]), 3),
                     "used": round(float(st["w"][j]), 3)}
                    for j, c in enumerate(content.COMPONENTS)],
        "like_ids": [art.pos[s] for s in sess["like_ids"] if s in art.pos],
        "dislike_ids": [art.pos[s] for s in sess["dislike_ids"] if s in art.pos],
        "shown": len(shown),
        "scores": [round(float(v), 4) for v in total],
        "top": top,
        "feedback": feedback,
        "focus": _map_focus(st, focus if focus is not None else (top[0]["idx"] if top else 0)),
        "anchor": _taste_anchor(art, st, json.loads(_layout_payload())['weights']),
    }
