import logging
import time
from datetime import datetime
from dataclasses import replace
from typing import Literal

from fastapi import (FastAPI, File, HTTPException, Query, Request, Response,
                     UploadFile)
from fastapi.exception_handlers import (http_exception_handler,
                                        request_validation_exception_handler)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import (admin, admin_trends, bookmarks, boot, decision_feedback, filters, geo,
                 hours, journeys, loader, media, observe, ranking, snapshot, state,
                 user_reviews)
from app import users as user_registry
from app import taste_api, recommendation_sessions as rec_sessions, identities
from app.model import (BookmarkCardOut, BookmarkOut, CardBaseOut, CardDetailOut,
                       CardOut, DecisionOut, DecisionFeedbackIn, DecisionFeedbackOut, DirectionIn, DirectionOut, FilterIn, FiltersOut,
                       HealthOut, LocationIn, Log, MySurveyCardOut, PendingSurveyOut,
                       PreferenceOut,
                       ReadyOut, Review, StatusOut, SurveyIn, SurveyOut,
                       SurveyPhotoOut, UserCreate, UserOut, UserRecordOut)
from recsys import content, decision, decision_comments, decision_metrics, decision_summary, diversity, policy, swipe
from app.decision_model import DecisionMetricsOut
from app.decision_summary_model import DecisionSummaryOut

VERSION = "1.0.0"

# 로그부터 세운다 — 이 아래의 기동 과정(복원)도 같은 형식으로 남아야 한다.
observe.setup()

# 서버가 켜질 때 카드 카탈로그를 만든다 (데이터는 recsys.store 와 공유)
_catalog_started = time.perf_counter()
loader.load_all()
observe.log("startup.catalog", cards=len(loader.cards),
            ms=round((time.perf_counter() - _catalog_started) * 1000, 1))

# Calibrate immutable artifact similarities once before accepting requests.
# First decision/metrics requests must not pay the catalogue-wide sort cost.
_artifacts_started = time.perf_counter()
if (_decision_art := content.load()) is not None:
    diversity.matrices(_decision_art)
observe.log("startup.artifacts", available=_decision_art is not None,
            ms=round((time.perf_counter() - _artifacts_started) * 1000, 1))

# 선호 표현 기록. 메모리에 두되 state/ 에 추가 전용으로 남겨 재시작 때 되살린다.
logs: list[Log] = []


def _apply(user_id: str, card_id: int, prefer: str, *, snapshot=False):
    """선호 표현 하나를 취향 모델에 반영. 기동 복원과 실시간 요청이 같은 길을 쓴다.

    라우터를 거치므로 **두 정책이 모두** 같은 피드백을 받는다(`recsys/policy.py`).
    학습에는 난수가 쓰이지 않아 `state/preferences.jsonl` 을 다시 먹이면 두 정책의
    상태가 소수점까지 복원된다(`tests/test_replay.py` 가 그것을 잰다).
    """
    store_id = loader.to_store_id(card_id)
    if store_id is None:
        return
    result = policy.record(policy.session_for(user_id), store_id, prefer == "like", snapshot=snapshot)
    rec_sessions.remember(user_id, store_id, prefer == "like")
    return result


def _apply_restore(user_id: str, card_id: int, prefer: str):
    """복원 전용 `_apply` — 한 건마다 진행률을 올린다(`/readyz` 가 보여 준다)."""
    boot.tick()
    return _apply(user_id, card_id, prefer)


def _restore_all() -> None:
    """기동 복원 전부. **`boot.start` 가 스레드에서 부른다** — 테스트는 동기다.

    예전에는 이 블록이 모듈 최상위에 있어서 끝날 때까지 uvicorn 이 포트를 열지
    못했다(배포마다 4~5분 502, explainer 7.1). 내용은 그대로고 자리만 옮겼다 — 순서도
    그대로다: 선호 재생이 먼저, 그 뒤 찜·길찾기·설문·노출·후기.
    """
    t0 = time.perf_counter()
    boot.set_total(state.count_preferences())
    identities.restore()
    # 스냅샷이 있으면 그 절단점까지의 상태를 통째로 올리고 뒤만 재생한다(app/snapshot.py).
    # 도장이 어긋나면(학습 코드·산출물이 바뀜) None 이 와서 예전처럼 전체를 재생한다.
    snap = snapshot.load()
    skip = 0
    if snap is not None:
        n_sess = snapshot.install(snap)
        skip = int(snap["watermark"])
        boot.set_snapshot_rows(skip)
        observe.log("snapshot.installed", sessions=n_sess, watermark=skip, at=snap.get("at"))
    restored = state.restore(user_registry.all_users(), logs, _apply_restore,
                             rec_sessions.apply_boundary, skip=skip)
    boot.tick(restored["skipped"])        # 건너뛴 선호도 진행률에는 '끝난 것' 이다
    for session_id in identities._sessions:
        policy.session_for(session_id)  # include empty legacy sessions after restart
    # 찜은 취향 모델과 무관하므로 따로 복원한다(`app/bookmarks.py` 가 자기 파일을 읽는다).
    n_bookmarks = bookmarks.restore()
    # 길찾기·설문도 추천과 무관하므로 따로 복원한다. `surveys.jsonl` 까지 읽어야 재시작
    # 뒤에 이미 답한 설문을 다시 묻지 않는다(`app/journeys.py: restore`).
    n_journeys = journeys.restore()
    # 노출 지도도 되살린다 — 없으면 재시작 뒤의 선호가 전부 `policy="none"` 으로 세어져
    # 인터리빙 비교가 배포마다 끊긴다(`recsys/policy.py: restore_exposures`).
    n_exposures = policy.restore_exposures(loader.to_store_id)
    # 사용자 후기도 되살린다 — 저장소를 따로 두지 않고 `surveys.jsonl` 을 다시 읽는다
    # (`app/user_reviews.py`). 빠뜨리면 재시작 뒤에 가게 화면의 후기가 전부 사라진다.
    n_reviews = user_reviews.restore()
    decision_feedback.restore()
    restore_s = time.perf_counter() - t0
    observe.set_restore_seconds(restore_s)
    observe.log("state.restore", users=restored["users"],
                preferences=restored["preferences"], skipped=restored["skipped"],
                snapshot=skip or None, bookmarks=n_bookmarks,
                exposures=n_exposures, directions=n_journeys["directions"],
                surveys=n_journeys["surveys"], survey_photos=n_journeys["photos"],
                user_reviews=n_reviews, dir=restored["dir"],
                enabled=restored["enabled"], ms=round(restore_s * 1000, 1))


# 여기서 **즉시 돌아온다**(스레드). 그 아래 `app = FastAPI(...)` 가 곧 만들어져 uvicorn 이
# 포트를 열고, 복원이 끝날 때까지 `boot.Gate` 가 준비 전 요청을 503 으로 돌려준다.
# `MATCHURI_RESTORE_SYNC=1` 이면 예전처럼 여기서 끝까지 돈다(테스트가 그렇게 한다).
boot.start(_restore_all)
# 복원 완료 직후 첫 스냅샷을 찍고, 이후 새 선호가 쌓였을 때 주기적으로 갱신한다.
snapshot.start_periodic()

app = FastAPI(
    title="맛추리 API",
    version="1.0.0",
    openapi_tags=[
        {"name": "사용자", "description": "오래 유지되는 실제 사용자를 만들고 조회합니다."},
        {"name": "추천 세션", "description": "사용자 아래 독립적인 탐색 세션을 만들고 위치·필터를 관리합니다."},
        {"name": "카드", "description": "세션별 추천 카드와 공통 카드 상세·리뷰를 조회합니다."},
        {"name": "선호", "description": "스와이프 결과를 세션에 기록하고 해당 세션의 반응 이력을 조회합니다."},
        {"name": "선택 제안", "description": "조건을 충족했을 때 선택할 카드 묶음을 받고 선택·계속 탐색 결과를 기록합니다."},
        {"name": "판단 수치", "description": "결정 준비도와 개인화·적합 가게 수·가게 간 유사도를 조회합니다."},
        {"name": "취향", "description": "세션의 취향 위치와 시간에 따른 변화 경로를 조회합니다."},
        {"name": "찜", "description": "여러 세션에서 공유하는 사용자의 찜 목록입니다."},
        {"name": "설문", "description": "사용자의 길찾기·방문 설문·후기 사진을 기록합니다."},
        {"name": "호환 API", "description": "기존 클라이언트 전용입니다. 신규 연동에서는 위의 /user 경로를 사용합니다."},
        {"name": "운영", "description": "서버 생존·준비 상태와 운영 지표입니다."},
        {"name": "관리자", "description": "추천 상태와 취향 변화를 확인하는 관리자 전용 API입니다."},
        {"name": "기타", "description": "서버 기본 상태입니다."},
    ],
    description="""
실제 사용자와 추천 세션을 분리한 맛추리 백엔드입니다.

### 신규 클라이언트 호출 순서

1. `POST /user` — 오래 유지할 실제 `user_id`를 발급합니다.
2. `POST /user/{user_id}/session` — 한 번의 탐색을 나타내는 `session_id`를 만듭니다.
3. `/user/{user_id}/session/{session_id}` 아래의 `cards` → `preferences` → `decision`을 사용합니다.
4. 찜·길찾기·설문은 `/user/{user_id}` 아래에서 여러 세션이 공유합니다.

사용자는 여러 독립 세션을 가질 수 있습니다. 새 세션은 이전 취향을 약하게 참고하지만
스와이프 수·노출·결정 준비도는 별도로 유지합니다. 위치와 필터도 세션별 설정입니다.

`/users`, `GET /cards?user_id=...`, `POST /preferences` 등 회색 **Deprecated** 항목은
기존 클라이언트용 호환 API입니다. 그 경로의 `user_id`는 실제 사용자가 아니라 기존 탐색 세션 ID입니다.
`GET /session/{기존ID}`로 연결된 실제 사용자 ID를 확인할 수 있습니다.

없는 값은 `null`, 없는 목록은 `[]`입니다. 세부 계약은 각 기능 묶음과 저장소의
`docs/API.md`에서 확인할 수 있습니다. 관리자 화면은 [`/admin`](/admin)입니다.
""",
)

app.include_router(admin.router)
# 트렌드 추이는 **관측 전용**이라 라우터를 나눠 뒀다(`app/admin_trends.py`).
# 인증은 nginx 가 `/admin` 전체에 걸어 두므로 라우터를 나눠도 보호는 그대로다.
app.include_router(admin_trends.router)
app.include_router(taste_api.router)

# ── 관측·보안 미들웨어 ────────────────────────────────────────────────
# CORS 를 먼저 붙이고 로그를 나중에 붙인다 = 로그가 **바깥**이다. 프리플라이트(OPTIONS)도
# 한 줄로 남아야 '프론트가 왜 못 부르나' 를 로그만 보고 답할 수 있다.
_cors = observe.add_cors(app)
# 준비 게이트는 로그 **안쪽**(503 도 한 줄로 남게), CORS **바깥**(OPTIONS 는 통과시킨다).
app.add_middleware(boot.Gate)
app.add_middleware(observe.RequestLog)
observe.log("boot", version=VERSION, policy=policy.active(),
            configured_policy=policy.configured(), cards=len(loader.cards),
            recommendable=len(swipe.pool()["ids"]), cors=_cors,
            log_level=logging.getLevelName(
                logging.getLogger(observe.LOGGER_NAME).level))


@app.exception_handler(StarletteHTTPException)
async def _log_http_exception(request: Request, exc: StarletteHTTPException):
    """4xx 도 남긴다 — 프론트가 '왜 400 인가' 를 물을 때 답이 로그에 있어야 한다.

    응답은 기본 처리기가 만든 것 그대로다(계약을 바꾸지 않는다).
    """
    observe.log("http.client_error", level=logging.INFO, status=exc.status_code,
                path=request.url.path, detail=str(exc.detail)[:300])
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _log_validation_error(request: Request, exc: RequestValidationError):
    """422 도 같은 이유로 남긴다. 어느 필드가 틀렸는지까지 적는다."""
    observe.log("http.client_error", level=logging.INFO, status=422,
                path=request.url.path,
                detail=[".".join(str(p) for p in e.get("loc", []))
                        for e in exc.errors()][:10])
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """예상 못 한 예외 — **traceback 은 로그에만.** 클라이언트에는 요청 id 만 준다.

    스택을 응답에 실으면 경로·패키지 버전·코드 구조가 그대로 새어 나간다. 대신
    `request_id` 를 주므로 사용자가 그 값을 알려 주면 로그에서 한 번에 찾을 수 있다
    (traceback 은 미들웨어가 `event: "error"` 로 이미 남겼다).
    """
    rid = getattr(request.state, "request_id", None) or observe.request_id()
    return JSONResponse(status_code=500,
                        content={"detail": "internal error", "request_id": rid},
                        headers={"X-Request-ID": rid} if rid else None)


# ── 운영 엔드포인트 ──────────────────────────────────────────────────
@app.get("/healthz", summary="살아 있나", tags=["운영"], response_model=HealthOut)
def healthz():
    """프로세스가 응답하는지만 봅니다. **의존성을 하나도 보지 않습니다.**

    컨테이너 재시작 판단(liveness)에 쓰세요. 데이터가 안 올라왔어도 200 입니다 —
    그건 재시작으로 고쳐지지 않는 문제이고, `GET /readyz` 가 답할 질문입니다.
    """
    return {"ok": True}


@app.get("/readyz", summary="요청을 받아도 되나", tags=["운영"], response_model=ReadyOut)
def readyz(response: Response):
    """가게 데이터·후보 풀·상태 디렉터리 셋을 확인합니다. 하나라도 아니면 **503** 입니다.

    - `data` : 카드 카탈로그가 올라왔나
    - `pool` : 추천 후보 풀이 만들어졌나
    - `state_writable` : `state/` 에 실제로 쓸 수 있나(탐침 파일을 한 번 열어 봅니다)
    - `artifacts` : 서빙 산출물이 있나. **없어도 준비 상태입니다** — legacy 정책으로 돕니다
    - `media_writable` : 설문 사진을 저장할 수 있나. **없어도 준비 상태입니다** —
      사진 업로드만 503 이 되고 카드·설문은 정상입니다
    - `restore` : 기동 복원의 진행 상황. `phase` 가 `ready` 가 되기 전에는 **503** 이고
      `preferences_done / preferences_total` 로 얼마나 남았는지 알 수 있습니다.
      배포 뒤 4~5분 동안 이 값이 움직입니다(explainer 7.1)
    - `reasons` : 준비되지 않은 이유(준비됐으면 빈 배열)

    로드밸런서·배포 스크립트가 트래픽을 붙이기 전에 부르는 자리입니다. 복원 중에는
    이 경로가 503 을 JSON 으로 주도록 nginx 가 따로 열어 둡니다(`nginx/default.conf`).
    """
    from recsys import content
    loader.load_all()
    cards = len(loader.cards)
    pool_n = len(swipe.pool()["ids"])
    writable = state.writable()
    restore = boot.progress()
    reasons = []
    if not cards:
        reasons.append("가게 데이터가 없습니다")
    if not pool_n:
        reasons.append("추천 후보 풀이 비었습니다")
    if not writable:
        reasons.append(f"state 디렉터리에 쓸 수 없습니다: {state.DIR}")
    if restore["phase"] == "failed":
        reasons.append(f"기동 복원이 실패했습니다: {restore['error']}")
    elif restore["phase"] != "ready":
        reasons.append(f"기동 복원 중입니다: 선호 {restore['preferences_done']}"
                       f"/{restore['preferences_total']}건")
    ok = not reasons
    if not ok:
        response.status_code = 503
        # 복원 중은 정상 상태다 — 배포마다 4~5분씩 WARNING 을 찍으면 진짜 경고가 묻힌다.
        level = logging.INFO if restore["phase"] in ("pending", "restoring") else logging.WARNING
        observe.log("readyz.fail", level=level, reasons=reasons)
    return {"ok": ok, "data": bool(cards), "pool": bool(pool_n),
            "state_writable": writable, "media_writable": media.writable(),
            "artifacts": content.available(),
            "policy": policy.active(), "cards": cards, "recommendable": pool_n,
            "restore": restore, "reasons": reasons}


@app.get("/metrics", summary="Prometheus 지표", tags=["운영"],
         response_class=PlainTextResponse)
def metrics():
    """Prometheus 텍스트 형식. 의존성 없이 프로세스 안에서 셉니다(재시작하면 0).

        matchuri_http_requests_total{route,method,status} · matchuri_errors_total{route}
        matchuri_http_request_seconds_bucket{route,le}   히스토그램 · matchuri_http_inflight
        matchuri_preferences_total{prefer,policy}        policy = 그 카드를 **보여준** 정책
        matchuri_cards_served_total{policy}              내보낸 카드 수
        matchuri_cards_requests_total{filtered,personalized} · matchuri_cards_short_total
        matchuri_bookmarks_total{action} · matchuri_taste_views_total · …
        matchuri_users_total · matchuri_sessions · matchuri_state_bookmarks_total
        matchuri_process_resident_bytes 등 (`/proc/self`, 리눅스에서만)
        matchuri_build_info{version,policy}

    전체 목록과 대시보드 PromQL 은 `server/README.md` 의 '운영' 절에 있습니다.

    라벨의 `route` 는 **경로 템플릿**입니다(`/cards/{card_id}`) — 카드 id 마다 시계열이
    생기면 지표 저장소가 감당하지 못합니다.
    """
    from recsys import content
    return observe.render(users=len(user_registry.all_users()),
                          sessions=len(swipe.sessions()), prefs=len(logs),
                          bookmarks=sum(len(v) for v in bookmarks.all_marks().values()),
                          artifacts=content.available(),
                          version=VERSION, policy=policy.active())


def _card_out(card, menus=None, *, bookmarked: bool = False):
    """모델 -> 응답. menus 는 상세에서만 붙이고, `bookmarked` 는 `user_id` 를 알 때만 참이다."""
    # 영업 상태는 **여기서** 계산한다. `loader.cards` 의 Card 는 기동 때 만들어 캐시되므로
    # 거기에 넣으면 서버가 켜진 시각에 굳어 버린다(`distance_m` 을 여기서 붙이는 것과 같다).
    out = {"card_id": card.card_id, "store": card.store,
           "image": card.image, "images": card.images, "bookmarked": bookmarked,
           "open_now": hours.state_at(card.store.open_hours)}
    if menus is not None:
        out["menus"] = menus
        # 사용자 후기는 **상세에만** 싣는다. 목록은 카드 수만큼 곱해지는 자리라
        # 한 장당 후기 몇 건이 붙으면 응답이 금세 커진다(사진 주소까지 들어간다).
        out["user_reviews"] = user_reviews.for_store(card.store.id)
    return out


def _position(user_id: str | None) -> tuple[float, float] | None:
    """이 사용자의 위치 — **저장된 것뿐이다**(`PUT /users/{id}/location`).

    위치와 필터는 **설정**이고 설정은 사용자에 붙는다. 좌표와 필터를 `GET /cards` 의
    쿼리로도 받던 길을 없앴다 — 조회가 상태를 정하면 같은 URL 이 요청마다 다른 뜻이 되고,
    '지금 이 사람에게 무엇이 걸려 있나' 를 사용자 레코드 하나로 답할 수 없다. 설정은 PUT
    으로 바뀌고 파일에 남고, `GET /cards` 는 그것을 읽기만 한다.

    위치가 없어서 답을 만들 수 없는 경우(저장된 `within_m`)의 400 은 호출자가 낸다.
    """
    return user_registry.location_of(user_id) if user_id else None


@app.get("/", summary="서버 상태", tags=["기타"], response_model=StatusOut)
def root():
    """서버가 살아 있는지, 데이터가 제대로 올라갔는지 확인합니다.

    - `cards` : 카드(가게) 수
    - `recommendable` : 추천 후보 수 (사진과 리뷰가 있는 가게만 카드로 돌립니다)
    - `logs` : 지금까지 쌓인 스와이프 기록 수 (재시작하면 0)
    - `sessions` : 학습 중인 사용자 수
    """
    loader.load_all()
    return {
        "cards": len(loader.cards),
        "recommendable": len(swipe.pool()["ids"]),
        "logs": len(logs),
        "sessions": len(swipe._sessions),
        "policy": policy.active(),
        "configured_policy": policy.configured(),
    }


@app.post("/users", summary="사용자 만들기", tags=["사용자"],
          response_model=UserRecordOut)
def create_user(request: Request, body: UserCreate | None = None):
    """`user_id` 를 발급합니다. 클라이언트는 이 값을 저장해 두고 계속 씁니다.

    요청 본문은 선택입니다. 이름을 주면 관리자 화면에서 구분하기 쉬워집니다.

        POST /users            {}                  -> {"user_id": "u_3f2a…"}
        POST /users            {"name": "김철수"}

    발급받지 않은 아무 문자열을 `user_id` 로 써도 동작합니다(예전 방식). 다만 id 가
    짧고 뻔하면 남이 그 사람의 추천을 넘겨볼 수 있으니 발급받는 쪽을 권합니다.
    """
    u = user_registry.create(body.name if body else None)
    identities.ensure_legacy(u['user_id'], u)
    policy.session_for(u["user_id"])     # 정책 두 벌의 세션을 미리 만들어 둔다
    request.state.user_id = u["user_id"]     # 발급한 id 가 이 요청의 http 줄에도 남게
    observe.inc_user_created()
    observe.log("user.create", user_id=u["user_id"], named=bool(u["name"]))
    return u


@app.get("/users/{user_id}", summary="사용자 정보와 학습 현황", tags=["사용자"],
         response_model=UserOut)
@rec_sessions.scoped
def get_user(user_id: str):
    """그 사용자의 스와이프 수와 지금까지 학습된 취향을 돌려줍니다.

    발급받지 않은 `user_id` 도 스와이프 기록이 있으면 조회됩니다.
    둘 다 없으면 404 입니다.
    """
    user_registry.validate_id(user_id)
    u = user_registry.get(user_id)
    ss = swipe.sessions()
    if u is None and user_id not in ss:
        raise HTTPException(status_code=404, detail="user not found")
    sess = ss.get(user_id)
    return {
        **(u or {"user_id": user_id, "name": None, "created_at": None,
                 "location": None, "filter": None}),
        "registered": u is not None,
        "swipes": sess["n"] if sess else 0,
        "likes": sess["likes"] if sess else 0,
        "bookmarks": bookmarks.count(user_id),
        # detail=False — prototypes·hist 는 관리자 화면 몫이다(recsys/taste.py: learn_state).
        "learned": (policy.learn_state(policy.session_for(user_id), detail=False)
                    if sess else None),
    }


@app.get('/users/{user_id}/session', tags=['추천 세션'], response_model=rec_sessions.SessionOut,
         summary='현재 추천 세션')
@rec_sessions.scoped
def get_recommendation_session(user_id: str):
    """사용자 ID는 유지하고 현재 추천 세션 ID와 이번 세션의 스와이프 수만 조회합니다."""
    if user_registry.get(user_id) is None and user_id not in policy.sessions():
        raise HTTPException(404, 'user not found')
    return rec_sessions.info(user_id)


@app.post('/users/{user_id}/sessions', tags=['추천 세션'], response_model=rec_sessions.SessionOut,
          summary='새 추천 세션 시작')
@rec_sessions.scoped
def start_recommendation_session(user_id: str, body: rec_sessions.SessionStart | None = None):
    """이전 세션은 약한 취향 참고값으로 남고 진행도·노출은 새로 시작합니다.

    사용자 ID·찜·위치·필터는 유지합니다. previous_session_id를 보내면 같은 요청의
    재전송은 같은 세션을 돌려줍니다. 기존 클라이언트는 이 API를 쓰지 않아도 그대로 동작합니다.
    """
    if user_registry.get(user_id) is None and user_id not in policy.sessions():
        raise HTTPException(404, 'user not found')
    return rec_sessions.start(user_id, body.previous_session_id if body else None)


@app.put("/users/{user_id}/location", summary="사용자 위치 저장", tags=["추천 세션"],
         response_model=UserRecordOut)
@rec_sessions.scoped
def put_user_location(user_id: str, body: LocationIn):
    """그 사용자의 위치를 기억합니다. `GET /cards` 의 거리 필터·`distance_m` 에 쓰입니다.

        PUT /users/u_3f2a…/location    {"lat": 37.5006, "lon": 127.0366}
        PUT /users/u_3f2a…/location    {"lat": 37.5006, "lon": 127.0366,
                                        "at": "2026-09-16T12:41:03+09:00"}

    - 등록하지 않은 `user_id` 도 됩니다(이름 없는 레코드를 만듭니다).
    - 위도 -90~90, 경도 -180~180 을 벗어나면 **422** 입니다.
    - 위치가 바뀌면 이 API 를 다시 부르세요. `GET /cards` 에는 위치 쿼리가 없습니다 —
      설정은 사용자에 붙고, 조회는 저장된 값을 읽기만 합니다.
    - `at`(잰 시각)은 선택입니다. 주면 **더 오래된 위치가 현재 위치를 덮지 않습니다** —
      지하에서 못 보낸 것을 모아 올릴 때 순서가 뒤섞여도 안전합니다. ISO 8601 이
      아니면 **422** 입니다.
    - **자주 불러도 됩니다.** 파일에 줄을 더하는 것은 **마지막으로 적은 지점에서
      100m 이상 움직였을 때만**입니다 — 부를 때마다 적으면 등록부가 GPS 궤적이 됩니다
      (`app/users.py: SAVE_MOVE_M`). 메모리의 값은 매번 갱신되므로 응답과 거리 필터는
      항상 최신입니다.

    **도착 판정에는 쓰이지 않습니다.** 방문 여부는 앱이 자기 위치 기록과 가게 좌표
    (`store.lat`·`store.lon`)로 직접 정하고 결과만 설문에 실어 보냅니다 — 위치가 기기를
    떠나지 않는 쪽이 맞고, 서버가 궤적을 들고 있을 이유도 없습니다.

    응답은 갱신된 사용자 레코드(`location: {lat, lon, at}`)입니다.
    """
    user_registry.validate_id(user_id)
    rec = user_registry.set_location(user_id, body.lat, body.lon, at=body.at)
    # 좌표는 소수 셋째 자리까지만 남긴다(약 100m) — 로그는 오래 남고 위치는 개인정보다.
    observe.inc_location_set()
    observe.log("user.location.set", user_id=user_id,
                lat=round(body.lat, 3), lon=round(body.lon, 3))
    return rec


@app.put("/users/{user_id}/filter", summary="사용자 필터 저장", tags=["추천 세션"],
         response_model=UserRecordOut)
@rec_sessions.scoped
def put_user_filter(user_id: str, body: FilterIn):
    """그 사용자의 필터를 기억합니다. 이후 `GET /cards` 를 **쿼리 없이** 불러도 적용됩니다.

        PUT /users/u_3f2a…/filter    {"cat": ["일식"], "tag": ["혼밥"], "within_m": 500}
        PUT /users/u_3f2a…/filter    {"cat": [], "tag": [], "within_m": 300}

    - `cat`·`tag` 는 `GET /filters` 의 값만 받습니다. 밖의 값은 **422** 이고 허용 목록이 메시지에 옵니다
    - 빈 목록·`null` 은 '그 조건 없음' 입니다. 셋 다 비우면 저장된 필터가 없는 것과 같습니다
    - 등록하지 않은 `user_id` 도 됩니다(이름 없는 레코드를 만듭니다)
    - `GET /cards` 에는 필터 쿼리가 없습니다 — 저장된 필터가 **항상** 적용되고, 지금 무엇이
      걸려 있는지는 `GET /users/{user_id}` 의 `filter` 로 봅니다

    응답은 갱신된 사용자 레코드(`filter: {cat, tag, within_m, at}`)입니다. 파일에 남으므로
    서버를 재시작해도 유지됩니다. 지우려면 `DELETE /users/{user_id}/filter` 입니다.
    """
    user_registry.validate_id(user_id)
    filters.validate(body.cat, body.tag)      # 어휘 밖 값은 422 (쿼리 경로와 같은 함수)
    rec = user_registry.set_filter(user_id, body.cat, body.tag, body.within_m)
    observe.inc_filter_set()
    observe.log("user.filter.set", user_id=user_id, cat=body.cat, tag=body.tag,
                within_m=body.within_m)
    return rec


@app.delete("/users/{user_id}/filter", summary="사용자 필터 지우기", tags=["추천 세션"],
            response_model=UserRecordOut)
@rec_sessions.scoped
def delete_user_filter(user_id: str):
    """저장된 필터를 지웁니다. 이후 `GET /cards` 는 다시 전체 후보에서 추천합니다.

    저장된 필터가 없어도 200 이고, 응답은 `filter: null` 인 사용자 레코드입니다
    (없는 것을 지우는 것은 실패가 아니라 이미 원하는 상태입니다).
    """
    user_registry.validate_id(user_id)
    rec = user_registry.clear_filter(user_id)
    observe.log("user.filter.clear", user_id=user_id)
    return rec


# ── 찜 ──────────────────────────────────────────────────────────────
def _near_reason(card, distance_m: int | None) -> str | None:
    """가까워서 올라온 카드의 한 줄. 조건을 못 채우면 None(정책의 이유를 그대로 둔다).

    규칙은 하나다 — **거리가 `ranking.NEAR_M` 이내이고 태그 계층이 순서를 정하지 않았을 때**.
    계층이 있으면(태그를 2개 이상 고른 요청) 그 카드가 위에 있는 이유는 '고른 태그를 더 많이
    가졌다' 이고, 거리는 그 다음이다 — 사용자가 보는 문장이 하나뿐이므로 **가장 큰 이유**를
    말해야 한다. 역 이름은 가게에 이미 있을 때만 쓴다(우리가 지어내지 않는다).
    """
    if distance_m is None or distance_m > ranking.NEAR_M:
        return None
    subway = (card.store.subway or "").split(" ")[0].strip()
    walk = f"걸어서 갈 만한 {distance_m}m 거리예요."
    return f"{subway} 근처, {walk}" if subway else walk


def _bookmark_out(user_id: str, card_id: int, at: str | None, n: int) -> dict:
    return {"card_id": card_id, "bookmarked": at is not None, "at": at, "count": n}


@app.put("/users/{user_id}/bookmarks/{card_id}", summary="찜하기", tags=["찜"],
         response_model=BookmarkOut)
def put_bookmark(user_id: str, card_id: int):
    """카드를 찜 목록에 담습니다. **여러 번 눌러도 같은 결과**입니다(멱등).

        PUT /users/u_3f2a…/bookmarks/1944   ->  {"card_id":1944,"bookmarked":true,
                                                 "at":"2026-09-10T18:20:31+09:00","count":3}

    - 이미 담긴 카드면 **처음 담은 시각**이 그대로 옵니다(목록 순서가 흔들리지 않게)
    - `at` 은 담은 시각, `count` 는 그 사용자의 전체 찜 수입니다
    - 없는 `card_id` 면 404, 등록하지 않은 `user_id` 도 됩니다
    - 한 사람당 **500개**까지입니다. 넘으면 가장 오래 담아 둔 것부터 빠집니다

    **찜은 추천을 바꾸지 않습니다.** 좋아요(`POST /preferences`)와 달리 취향 모델·'이미 본
    카드' 목록·노출 로그를 건드리지 않습니다 — 찜한 가게가 추천에 다시 나올 수 있습니다.
    """
    user_registry.validate_id(user_id)
    store_id = loader.to_store_id(card_id)
    if store_id is None:
        raise HTTPException(status_code=404, detail="card not found")
    at, n, created = bookmarks.add(user_id, card_id)
    if created:
        observe.inc_bookmark("add")
    observe.log("bookmark.add", user_id=user_id, card_id=card_id, store_id=store_id,
                count=n, created=created)
    return _bookmark_out(user_id, card_id, at, n)


@app.delete("/users/{user_id}/bookmarks/{card_id}", summary="찜 빼기", tags=["찜"],
            response_model=BookmarkOut)
def delete_bookmark(user_id: str, card_id: int):
    """찜 목록에서 뺍니다. 담겨 있지 않아도 **200** 입니다(이미 원하는 상태니까).

        DELETE /users/u_3f2a…/bookmarks/1944 -> {"card_id":1944,"bookmarked":false,
                                                 "at":null,"count":2}
    """
    user_registry.validate_id(user_id)
    removed, n = bookmarks.remove(user_id, card_id)
    if removed:
        observe.inc_bookmark("remove")
    observe.log("bookmark.remove", user_id=user_id, card_id=card_id,
                store_id=loader.to_store_id(card_id), count=n, removed=removed)
    return _bookmark_out(user_id, card_id, None, n)


@app.get("/users/{user_id}/bookmarks", summary="찜한 카드 목록", tags=["찜"],
         response_model=list[BookmarkCardOut])
def get_bookmarks(
    user_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, description="건너뛸 개수(페이징)"),
    reviews: int = Query(1, ge=0, le=20, description="카드마다 담을 리뷰 수"),
):
    """찜한 카드를 **최근에 담은 순**으로 반환합니다.

    - `bookmarked_at` : 담은 시각. 목록은 이 값의 역순입니다
    - `distance_m` : 저장된 위치에서의 거리(m). 위치를 모르면 `null`
    - 카드 내용은 `GET /cards` 의 카드와 같은 모양입니다(`bookmarked` 는 당연히 `true`)
    - 하나도 없으면 404 가 아니라 **빈 배열** `[]` 입니다
    - 페이징은 `limit`(기본 50, 최대 100) · `offset` 입니다

    지웠다 다시 담은 카드는 **맨 위**로 옵니다(그때가 담은 시각입니다).
    """
    user_registry.validate_id(user_id)
    loader.load_all()
    pos = _position(user_id)
    out = []
    for card_id, at in bookmarks.page(user_id, limit, offset):
        card = loader.get_card(card_id, n_reviews=reviews)
        if card is None:                     # 데이터가 바뀌어 없어진 카드는 건너뛴다
            continue
        out.append({**_card_out(card, bookmarked=True), "bookmarked_at": at,
                    "distance_m": geo.distance_m(card.store.id,
                                                 *(pos or (None, None)))})
    return out


# ── 길찾기 · 설문 ────────────────────────────────────────────────────
# 추천의 **결과**를 재는 자리다. 세 API 모두 `recsys` 를 건드리지 않는다 — 취향 모델·
# `shown`·노출 로그가 여기 때문에 움직이면 인터리빙의 like 율이 오염된다.


def _pending_out(user_id: str, after_hours: float = 0.0) -> list[PendingSurveyOut]:
    """`journeys.pending` 의 `(card_id, at)` 에 가게 정보를 붙인다.

    **방문 근거는 여기서 붙이지 않는다.** 도착 판정은 앱이 자기 위치 기록과
    `store.lat`·`store.lon` 으로 직접 하고, 결과만 설문에 실어 보낸다 — 위치가 기기를
    떠나지 않는 쪽이 맞고, 서버가 궤적을 들고 있을 이유도 없다.
    """
    now = datetime.now().astimezone()
    out: list[PendingSurveyOut] = []
    for card_id, at in journeys.pending(user_id, now, after_hours):
        card = loader.cards.get(card_id)
        if card is None:
            continue                    # 데이터가 갱신돼 사라진 카드는 묻지 않는다
        dt = datetime.fromisoformat(at)
        out.append(PendingSurveyOut(
            card_id=card_id, store_id=card.store.id, name=card.store.name,
            category=card.store.category, image=card.image, at=at,
            hours_ago=int((now - dt).total_seconds() // 3600),
            photo_ids=journeys.photos_of(user_id, card_id)))
    return out


@app.post("/users/{user_id}/directions", summary="길찾기 기록", tags=["설문"],
          response_model=DirectionOut)
def post_direction(user_id: str, body: DirectionIn):
    """지도 앱으로 넘어가기 **직전에** 부르세요. 방문 의사로 기록됩니다.

        POST /users/u_3f2a.../directions  {"card_id":1944,"map_app":"kakao","mode":"transit"}

    - `survey_at` 이후에 `GET /users/{user_id}/surveys/pending` 에 이 가게가 뜹니다
      (길찾기 + **3시간**). 앱이 알림 예약에 쓸 수 있습니다
    - 멱등이 **아닙니다** — 같은 카드를 다시 누르면 시각이 갱신되고, 설문도 최신
      길찾기를 기준으로 다시 묻습니다(어제 갔고 오늘 또 갔으면 오늘을 물어야 합니다)
    - 없는 `card_id` 면 404 입니다

    **추천을 바꾸지 않습니다.** 좋아요(`POST /preferences`)와 달리 취향 모델·'이미 본
    카드' 목록·노출 로그를 건드리지 않습니다.
    """
    user_registry.validate_id(user_id)
    store_id = loader.to_store_id(body.card_id)
    if store_id is None:
        raise HTTPException(status_code=404, detail="card not found")
    at = journeys.record(user_id, store_id, body.card_id, body.map_app, body.mode)
    survey_at = (datetime.fromisoformat(at) + journeys.SURVEY_AFTER
                 ).isoformat(timespec="seconds")
    n = journeys.direction_count(user_id)
    observe.inc_direction(body.map_app, body.mode)
    observe.log("direction.record", user_id=user_id, card_id=body.card_id,
                store_id=store_id, map_app=body.map_app, mode=body.mode, count=n)
    return DirectionOut(card_id=body.card_id, at=at, survey_at=survey_at, count=n)


@app.get("/users/{user_id}/surveys/pending", summary="설문할 가게 목록", tags=["설문"],
         response_model=list[PendingSurveyOut])
def get_pending_surveys(
    user_id: str,
    after_hours: float = Query(
        0, ge=0, le=168,
        description="길찾기 후 이 시간이 지난 것만 받습니다. 기본 **0 = 전부**"),
):
    """아직 답하지 않은 길찾기를 **최근 순**으로 줍니다.

        GET /users/u_3f2a.../surveys/pending
        -> [{"card_id":1944,"name":"역삼동참치","hours_ago":4,...}]

    앱이 열릴 때 한 번 부르고, 결과가 있으면 배너를 띄우세요 —
    "가게를 방문했나요?" 하고 이 목록을 보여 주면 사용자가 하나를 고릅니다.

    **언제 물을지는 앱이 정합니다.** 서버는 시간으로 거르지 않습니다(기본 `after_hours=0`).
    각 항목의 `hours_ago` 를 보고 배너를 띄울 시점을 앱이 판단하세요 — 권장은 **3시간**
    이후이고, 그 시각은 `POST /directions` 응답의 `survey_at` 으로도 받을 수 있습니다.
    서버에서 걸러 받고 싶으면 `?after_hours=3` 을 주세요.

    - 하나도 없으면 404 가 아니라 **빈 배열** `[]` 입니다
    - **일주일**이 지난 길찾기는 나오지 않습니다(기억이 남아 있지 않습니다)
    - 최대 **10개**입니다. 더 있으면 최근 것부터 줍니다
    - 서버에 타이머가 없습니다 — 이 API 를 부른 **그 순간** 계산합니다

    **도착 여부는 서버가 판정하지 않습니다.** 앱이 자기 위치 기록과 각 가게의
    `store.lat`·`store.lon`(`GET /cards/{card_id}`)을 비교해 직접 정하고, 그 결과를
    설문에 실어 보내세요(`arrived`·`closest_m`·`dwell_min`). 이 목록은 **답하지 않은
    길찾기 전부**이므로, 도착한 것만 배너에 띄울지는 앱이 고릅니다.
    """
    user_registry.validate_id(user_id)
    return _pending_out(user_id, after_hours)


@app.get("/users/{user_id}/surveys", summary="내가 답한 설문 목록", tags=["설문"],
         response_model=list[MySurveyCardOut])
def get_my_surveys(
    user_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, description="건너뛸 개수(페이징)"),
    visited: bool | None = Query(None, description="갔다/안 갔다로 거르기. 생략하면 전부"),
    reviews: int = Query(0, ge=0, le=20,
                         description="카드마다 담을 **카카오** 리뷰 수. 기본 0"),
):
    """내가 답한 설문을 **최근에 답한 순**으로, 그 가게 카드와 함께 반환합니다.

    마이페이지의 '내 기록' 입니다. `GET /users/{id}/surveys/pending`(아직 답할 것)의
    반대쪽이에요.

    - `survey` : 내가 답한 것 — `visited`·`satisfaction`·`review` 세 줄·사진·시각
    - 카드 내용은 `GET /cards` 의 카드와 같은 모양입니다
    - 하나도 없으면 404 가 아니라 **빈 배열** `[]` 입니다
    - 페이징은 `limit`(기본 50, 최대 100) · `offset`

    **안 갔다고 답한 것도 나옵니다.** '내가 답한 설문' 의 목록이지 '내가 쓴 후기' 의
    목록이 아닙니다. 후기만 보고 싶으면 `visited=true` 로 부르세요.

    ⚠️ **가게 화면의 `user_reviews` 와 다른 것입니다.** 저쪽은 남에게 보이는 글이라
    방문·세 줄·빈칸을 모두 거르지만, 여기는 내 기록이라 답한 대로 다 나옵니다.

    **방문 근거(`arrived`·`closest_m`·`dwell_min`)는 나오지 않습니다** — 앱이 위치로
    계산해 보낸 값이라 이탈 분석에만 쓰고 사용자에게 되돌려 주지 않습니다.

    카드당 한 건이고 **마지막에 답한 것**이 남습니다(`POST` 의 중복 규칙과 같습니다).
    """
    user_registry.validate_id(user_id)
    loader.load_all()
    out = []
    for r in journeys.history(user_id):
        if visited is not None and r["visited"] != visited:
            continue
        # `card_id` 는 답하던 때의 값이다. 카드 번호 계보가 갈아 끼워지면 그 번호가
        # 다른 가게를 가리킬 수 있어서, 가게가 맞는지 보고 아니면 `store_id` 로 다시
        # 찾는다 — 기록의 주인은 가게지 번호가 아니다(`app/card_ids.py`).
        card_id = r["card_id"]
        if r["store_id"] and loader.to_store_id(card_id) != r["store_id"]:
            card_id = loader.card_id_by_store.get(r["store_id"])
        card = loader.get_card(card_id, n_reviews=reviews) if card_id else None
        if card is None:                     # 데이터가 바뀌어 없어진 가게는 건너뛴다
            continue
        out.append({**_card_out(card, bookmarked=bookmarks.has(user_id, card_id)),
                    "survey": {k: r[k] for k in ("visited", "satisfaction", "review",
                                                 "not_visited_reason", "images", "at")}})
    # 거르기가 끝난 뒤에 자른다 — 필터를 먼저 적용하지 않으면 `limit` 이 거른 것까지
    # 세어서 페이지가 비어 보인다.
    return out[offset:offset + limit]


@app.post("/users/{user_id}/surveys", summary="설문 답하기", tags=["설문"],
          response_model=SurveyOut)
def post_survey(user_id: str, body: SurveyIn):
    """배너에서 고른 가게 하나에 답합니다.

        POST /users/u_3f2a.../surveys
        {"card_id": 1944, "visited": true, "satisfaction": "good",
         "review": {"taste": "고등어초회가 신선했어요",
                    "price": "2인 4만원, 적당했어요",
                    "mood": "조용하고 좌석 간격이 넓어요"}}

    - **`visited=true` 면 `satisfaction` 과 `review` 가 둘 다 필수**입니다(없으면 422).
      선택으로 두면 대부분 비워서 오고, 그러면 추천이 맞았는지도 모르고 가게 리뷰도
      안 쌓입니다 — 이 설문이 있는 이유가 그 둘입니다
    - `review` 세 줄은 **가게 리뷰가 됩니다.** 세 줄이 다 차 있으면 `GET /cards/{card_id}` 의
      `user_reviews` 로 다른 사용자에게 보입니다 — "맛있어요" 도 실립니다. 다만 **구체적일수록
      앞에 섭니다**(표현력 순, 같으면 최근 순). 실렸는지는 응답의 `published` 로, 세 줄이
      각각 얼마나 구체적인지는 `review_levels`(축별 0~3) 로 옵니다. 레벨이 낮아도 거절되지
      않으니 앱은 이 값을 "조금 더 구체적으로 써 주세요" 안내에만 쓰면 됩니다
    - `visited=false` 면 `not_visited_reason` 만 채우세요(이탈 원인 분석에 씁니다).
      이때 후기를 보내도 **가게에 실리지 않습니다** — 안 가 본 가게의 후기이기 때문입니다
    - `arrived`·`closest_m`·`dwell_min` 은 **앱이 계산한 방문 근거**입니다(선택).
      앱이 가진 위치 기록과 `store.lat`·`store.lon` 을 비교해 정하세요 — 위치는 기기에
      두고 결과만 보내면 됩니다. 서버는 이 값으로 설문을 막지 않습니다
    - **길찾기 기록은 필요 없습니다.** 앱이 방문 판단을 스스로 하고 설문만 보내는 것이
      기본 경로입니다. 없는 `card_id` 만 **404** 입니다
    - 같은 카드에 두 번 답하면 `recorded=false` 이고 **덮어쓰지 않습니다**(배너를 두 번
      눌러도 표본이 부풀지 않아야 집계를 믿을 수 있습니다). `POST /directions` 를 부른
      경우에는 **새 길찾기가 문을 다시 엽니다** — 다시 가 봤으면 다시 물어야 합니다
    """
    user_registry.validate_id(user_id)
    if loader.to_store_id(body.card_id) is None:
        raise HTTPException(status_code=404, detail="no such card")
    store_id = loader.to_store_id(body.card_id)
    # 후기는 **방문했을 때만** 담는다. 안 가 본 가게의 글을 가게 화면에 실을 수 없다.
    review = body.review.model_dump() if (body.visited and body.review) else None
    answers = {k: v for k, v in (
        ("not_visited_reason", body.not_visited_reason),
        ("satisfaction", body.satisfaction),
        # `review` 는 dict 다. 집계는 `SURVEY_DETAILS` 화이트리스트의 **수치만** 평균
        # 내므로(`recsys/policy.py`) 여기 무엇이 들어와도 지표가 오염되지 않는다.
        ("review", review),
        # 앱이 계산한 방문 근거. 좌표가 아니라 스칼라라서 위치가 새지 않는다.
        # `closest_m` 을 쌓아 두는 이유: 이게 없으면 '도착 반경 얼마가 맞나' 가
        # 앱에 굳어 버리고 나중에 다시 볼 수 없다.
        ("arrived", body.arrived), ("closest_m", body.closest_m),
        ("dwell_min", body.dwell_min)) if v is not None}
    recorded = journeys.answer(user_id, store_id, body.card_id, body.visited, answers)
    # 실렸는지를 **돌려준다.** 예전에는 조용히 떨어졌고, 그래서 운영 카드 2,795장 중
    # 후기가 실린 카드가 1장인 것을 아무도 몰랐다. 앱도 로그도 알 수 있어야 한다.
    published = False
    if recorded:                       # 두 번 답한 것은 세지 않는다(표본이 부푼다)
        observe.inc_survey(body.visited, body.satisfaction)
        # 가게 화면에 바로 뜨게 한다. 복원과 같은 함수를 쓰므로 재시작해도 같은 결과다.
        published = user_reviews.apply({"user_id": user_id, "store_id": store_id,
                                        "card_id": body.card_id, "visited": body.visited,
                                        "answers": answers, "at": state.now()})
    # 세 줄의 표현력 레벨 — 실리는 자격이 아니라 순서와 앱 안내용이다. 각 줄이 None 일
    # 수 있으므로(`review` 가 dict 여도 값은 비어 올 수 있다) 빈 문자열로 받는다.
    levels = user_reviews.levels_of(*(str(review.get(k) or "")
                                      for k in ("taste", "price", "mood"))) if review else None
    observe.log("survey.answer", user_id=user_id, card_id=body.card_id,
                store_id=store_id, visited=body.visited,
                satisfaction=body.satisfaction, recorded=recorded,
                # 방문 근거는 숫자라서 그대로 남긴다(좌표가 아니다).
                arrived=body.arrived, closest_m=body.closest_m,
                dwell_min=body.dwell_min,
                # 본문은 남기지 않는다 — 사용자가 쓴 글이 로그로 새어 나가면 안 된다.
                # 길이만 있어도 '얼마나 쓰는가' 는 알 수 있다.
                review_len=sum(len(v) for v in (review or {}).values()),
                # 실렸나·얼마나 구체적인가. 본문은 여전히 남기지 않는다 — 숫자뿐이다.
                published=published, expr=levels,
                photos=len(journeys.photos_of(user_id, body.card_id)))
    return SurveyOut(ok=True, card_id=body.card_id, recorded=recorded,
                     remaining=len(_pending_out(user_id)),
                     published=published, review_levels=levels)


@app.post("/users/{user_id}/surveys/{card_id}/photos",
          summary="설문에 사진 붙이기", tags=["설문"],
          response_model=SurveyPhotoOut, status_code=201)
async def post_survey_photo(user_id: str, card_id: int, file: UploadFile = File(...)):
    """방문한 가게의 사진을 한 장 올립니다. `multipart/form-data`, 필드 이름은 `file`.

        POST /users/u_3f2a.../surveys/1944/photos      (multipart, file=<사진>)

    설문(`POST .../surveys`)과 **순서는 상관없습니다.** 사진은 `(사람, 카드)` 에 붙으므로
    설문 전에 올려도 되고 답한 뒤에 더해도 됩니다.

    - 길찾기한 적 없는 카드에는 올릴 수 없습니다 — **404**
      (아무 카드에나 올릴 수 있으면 파일 저장소가 됩니다)
    - 한 카드에 **최대 3장** — 넘으면 **409** 입니다. 먼저 지우세요
    - `image/jpeg` · `image/png` · `image/webp`, **최대 10MB** — 그 밖은 **415**
      (아이폰 HEIC 는 받지 않습니다. 앱에서 JPEG 로 변환해 주세요)
    - 응답의 `url` 을 **그대로 쓰세요.** 경로 규칙은 바뀔 수 있습니다

    **사진은 다시 구워서 저장합니다.** 긴 변 1,600px JPEG 로 줄이고 **EXIF 를 없앱니다** —
    휴대폰 사진에는 촬영 좌표가 들어 있어서 그대로 내보내면 위치가 새어 나갑니다.
    회전 정보는 없애기 전에 적용하므로 세로 사진이 눕지 않습니다. 그래서 올린 파일과
    받는 파일의 크기·해상도가 다릅니다.
    """
    user_registry.validate_id(user_id)
    if loader.to_store_id(card_id) is None:
        raise HTTPException(status_code=404, detail="no such card")
    # 길찾기를 요구하지 않으므로 '올릴 수 있는 카드' 에 울타리가 없다. 사람당 총량으로
    # 막는다 — 없으면 카드 2,795장 × 3장까지 한 사람이 밀어 넣을 수 있다.
    if journeys.photo_count(user_id) >= media.MAX_PER_USER:
        raise HTTPException(
            status_code=409,
            detail=f"사진은 한 사람당 최대 {media.MAX_PER_USER}장입니다")
    have = journeys.photos_of(user_id, card_id)
    if len(have) >= media.MAX_PER_CARD:
        raise HTTPException(
            status_code=409,
            detail=f"이 카드에는 최대 {media.MAX_PER_CARD}장까지입니다 "
                   f"(지금 {len(have)}장). 먼저 지우세요")
    # 상한보다 한 바이트 더 읽어 본다 — 정확히 상한인 파일을 거절하지 않으면서
    # 10MB 를 넘는 것을 더 읽지 않는다.
    raw = await file.read(media.MAX_BYTES + 1)
    try:
        saved = media.save(raw, file.content_type)
    except media.Rejected as exc:
        # 415: 형식·크기 문제다. 400 이 아니라 415 인 이유는 '요청이 잘못됐다' 가 아니라
        # '이 형식은 안 받는다' 여서다. 앱이 재시도할지 변환할지 구분할 수 있다.
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except OSError as exc:
        # 볼륨이 안 붙었거나 디스크가 찼다. 기록과 달리 조용히 넘기지 않는다 —
        # 사용자는 올렸다고 생각한다.
        observe.log("media.save_fail", level=logging.ERROR,
                    error=type(exc).__name__, detail=str(exc)[:200])
        raise HTTPException(status_code=503, detail="사진을 저장할 수 없습니다") from exc

    store_id = loader.to_store_id(card_id)
    journeys.add_photo(user_id, store_id, card_id, saved["photo_id"],
                       {k: saved[k] for k in ("bytes", "width", "height")})
    observe.log("survey.photo", user_id=user_id, card_id=card_id,
                store_id=store_id, photo_id=saved["photo_id"],
                bytes=saved["bytes"], total=len(have) + 1)
    return SurveyPhotoOut(**saved)


@app.delete("/users/{user_id}/surveys/{card_id}/photos/{photo_id}",
            summary="붙인 사진 지우기", tags=["설문"], status_code=204)
def delete_survey_photo(user_id: str, card_id: int, photo_id: str):
    """올린 사진을 지웁니다. 본인이 그 카드에 올린 사진만 지울 수 있습니다.

    - 그 사람 사진이 아니거나 이미 지웠으면 **404**
    - 성공하면 본문 없이 **204** 입니다
    """
    user_registry.validate_id(user_id)
    store_id = loader.to_store_id(card_id)
    if not journeys.remove_photo(user_id, store_id, card_id, photo_id):
        raise HTTPException(status_code=404, detail="photo not found")
    media.delete(photo_id)              # 색인에서 빠진 뒤에 지운다 — 순서가 바뀌면
    observe.log("survey.photo_delete", user_id=user_id, card_id=card_id,
                photo_id=photo_id)      # 파일은 없는데 색인에 남는 창이 생긴다
    return Response(status_code=204)


@app.get("/filters", summary="필터에 쓸 수 있는 값", tags=["카드"],
         response_model=FiltersOut)
def get_filters():
    """`GET /cards` 의 필터에 넣을 수 있는 **어휘**입니다. 클라이언트가 필터 UI 를 이 응답으로 만듭니다.

    **어휘는 서버가 정합니다.** 여기 있는 값만 `PUT /users/{user_id}/filter` 가 받고, 다른 값은 **422** 입니다
    (오타를 빈 목록으로 뭉개면 '그런 가게가 없다' 와 구분되지 않습니다). 목록과 순서는
    데이터가 아니라 서버가 고정한 것이라 개수가 0 이 되어도 칸은 그대로 옵니다.

    개수는 **추천 후보 풀 기준**입니다(사진과 리뷰가 있는 가게 2,216곳). 전체 가게로
    세면 카드로 나오지 않는 가게가 섞여 '10곳' 이라 적힌 필터가 카드 0장을 줍니다.

    - `cats` : 업종 **9개** — 후보가 많은 상위 8개(`한식`·`카페`·`일식`·`술집`·`양식`·
      `중식`·`간식`·`분식`)와 나머지 전부를 담는 `기타`(맨 뒤). 카드의
      `store.category_group` 이 같은 라벨입니다. `{"cat": ["일식","한식"]}` (**OR**)
    - `tags` : 태그 **12개**. `{"tag": ["혼밥","가성비"]}` (**OR** — 하나라도 가진 곳)
      뜻이 같은 이름은 한 칸으로 묶여 있습니다(`친절` = 친절·친절한, `단체모임` =
      단체모임·회식장소·친목모임, `분위기` = 분위기·감성적, `데이트` = 데이트·데이트코스·소개팅).
    - `within_m` : 거리 필터로 권하는 반경(m). 다른 값도 그대로 저장됩니다(`within_m` 만은
      어휘가 아니라 권장값입니다). 저장된 위치(`PUT /users/{user_id}/location`)가 있어야 합니다.
    """
    loader.load_all()
    return filters.vocabulary()


#: `attribution.parts.personal` 에 들어가는 taste 성분 — 그 사람의 스와이프에서 배운 것.
_PERSONAL = ("cf", "menu", "text", "review", "food", "neg")
#: `attribution.parts.reputation` — 사람과 무관한 가게 자체의 평판.
_REPUTATION = ("pop", "quality")


def _attribution(policy_name: str | None, ex: dict | None,
                 tier: int | None, ctx: dict | None) -> dict | None:
    """정책이 남긴 설명 재료를 응답의 `attribution` 으로 합친다(`app/model.py: AttributionOut`).

    정책마다 재료의 모양이 다르다 — taste 는 성분 8개의 `가중치 × z`, legacy 는 평점·업종·가격
    항, 트렌드 슬롯은 출처별 `무게 × z` 와 근거 메뉴. 여기서 셋을 같은 틀(personal · reputation ·
    context)에 넣고 절댓값이 가장 큰 항을 `dominant` 로 고른다. 0 인 항은 싣지 않는다 —
    "영향이 없었다" 는 키가 없는 것으로 읽힌다.

    `context` 는 학습되지 않는 것들이다(거리·트렌드 보너스·태그 계층). 트렌드 보너스는 무게
    (`MATCHURI_TREND_WEIGHT`)가 0 인 지금 슬롯 카드가 아니면 늘 0 이다 — 그게 사실이다.
    """
    if policy_name is None or ex is None:
        return None
    ctx = ctx or {}
    context = {"distance": round(float(ctx.get("distance", 0.0)), 4),
               "trend": round(float(ctx.get("trend", 0.0)), 4),
               "tag_tier": int(tier or 0)}
    if policy_name == policy.SLOT:
        sources = ex.get("sources") or {}
        dominant = max(sources, key=lambda k: abs(sources[k])) if sources else "season"
        return {"policy": policy_name, "dominant": dominant,
                "parts": {"trend": sources, "menu": ex.get("menu") or [], "context": context}}
    if policy_name == "taste":
        comps = ex.get("components") or {}
        personal = {k: comps[k] for k in _PERSONAL if comps.get(k)}
        reputation = {k: comps[k] for k in _REPUTATION if comps.get(k)}
        flags = {"explore": bool(ex.get("explore")), "cold": bool(ex.get("cold"))}
    else:                                            # legacy
        personal = {k: ex[k] for k in ("category", "price") if ex.get(k)}
        reputation = {k: ex[k] for k in ("quality", "reviews") if ex.get(k)}
        flags = {"explore": False, "cold": (ex.get("cold_weight") or 0.0) > 0.5,
                 "diverse": bool(ex.get("diverse"))}
    pool = {**personal, **reputation,
            "distance": context["distance"], "trend": context["trend"]}
    if flags["explore"]:
        dominant = "explore"
    elif any(pool.values()):
        dominant = max(pool, key=lambda k: abs(pool[k]))
    else:
        dominant = "cold"
    return {"policy": policy_name, "dominant": dominant,
            "parts": {"personal": personal, "reputation": reputation,
                      "context": context, **flags}}


@app.get("/cards", summary="추천 카드 목록", tags=["카드"],
         response_model=list[CardOut])
@rec_sessions.scoped
def get_cards(
    user_id: str | None = Query(None,
                                description="주면 그 사람 취향과 저장된 필터로 추천합니다"),
    limit: int = Query(10, ge=1, le=50),
    reviews: int = Query(1, ge=0, le=20, description="카드마다 담을 리뷰 수"),
    session_id: str | None = Query(None, min_length=1, max_length=64, description="생략하면 현재 세션; 이전 세션이면409"),
):
    """스와이프로 넘길 **가게 카드**를 반환합니다.

    `user_id` 를 주면 **그 사람이 지금까지 넘긴 결과로 학습한 추천**입니다.
    처음에는 평점이 좋은 가게 위주로 주다가, 스와이프가 쌓일수록 취향
    (카테고리·가격대)에 맞춰 옮겨 갑니다. 같은 카테고리가 3장 연속 나오지 않습니다.

    한 번 내보낸 카드는 그 사용자에게 다시 나오지 않습니다.
    `user_id` 를 안 주면 예전처럼 인기순으로 주고, 학습도 하지 않습니다.

    - `store.reviews` : 리뷰 미리보기 (`reviews` 개수만큼, 기본 1건)
    - `store.tags` : 리뷰 태그 상위 5개 (예: `["맛","친절","분위기"]`)
    - `store.category_group` : 필터의 업종 라벨 9개 중 하나(저장된 필터의 `cat` 값).
      `store.category` 는 그대로 카카오의 세부 분류입니다(`한정식`·`국수` 등)
    - `image` : 대표 사진 1장. `images` 는 카드에서 넘겨 볼 사진(최대 5장)
    - `store.score` : 평균 별점. 리뷰가 없으면 `null`
    - `policy` : 이 카드를 고른 추천 정책 (`legacy` · `taste` · `trend`). `user_id` 없이
      부르면 `null` 입니다. 두 정책이 한 화면을 번갈아 채우며(팀-드래프트 인터리빙)
      어느 쪽이 더 잘 맞히는지를 재는 중입니다 — 클라이언트가 볼 필요는 없습니다.
      `trend` 는 트렌드 슬롯(`MATCHURI_TREND_EVERY=N`, 기본 꺼짐)이 N장에 한 장 넣는
      '요즘 뜨는 가게' 카드입니다.
    - `recommendation_reason` : 카드 최상위의 한 줄 추천 이유. 예: "지금까지의 취향과 잘 맞는 한식 가게예요."
      평점·리뷰 수, 학습된 카테고리·가격대 취향, 다양성을 위한 선택을 구분합니다.
    - `distance_m` : 위치에서 그 가게까지의 거리(m, 정수). **위치를 모르면 `null`** 입니다.
    - `attribution` : 이 카드가 뽑힌 이유의 **숫자**. `dominant` 가 가장 크게 작용한 항 하나이고
      (`menu`·`cf` 같은 개인화, `pop`·`quality` 같은 평판, `distance`·`trend` 같은 상황,
      또는 `explore`), `parts` 에 항별 기여가 정책별 틀로 들어 있습니다. `recommendation_reason`
      은 이 중 가장 큰 양의 기여를 문장으로 바꾼 것입니다. `user_id` 없이 부르면 `null` 입니다.

    **필터는 쿼리가 아니라 사용자 설정입니다.** 순서는 셋입니다 —
    어휘 조회 `GET /filters` → 설정 `PUT /users/{user_id}/filter` ·
    `PUT /users/{user_id}/location` → 추천 `GET /cards?user_id=...`.

    - 저장된 필터(`cat` 업종 **OR** · `tag` 태그 **OR** · 셋 사이는 **AND**)를 이 응답이
      **항상** 적용합니다. 이 엔드포인트에는 필터 쿼리가 없습니다 — 조회가 설정을 정하면
      같은 URL 이 요청마다 다른 뜻이 되고, '지금 무엇이 걸려 있나' 를 사용자 레코드
      하나로 답할 수 없습니다. 무엇이 걸려 있는지는 `GET /users/{user_id}` 의 `filter` 입니다
    - 필터를 바꾸려면 `PUT /users/{user_id}/filter`, 풀려면
      `DELETE /users/{user_id}/filter` 입니다. 어휘 밖의 값은 그 PUT 이 **422** 로 막습니다
    - 저장된 `within_m` 이 있는데 저장된 위치가 없으면 **400** 입니다
      (`PUT /users/{user_id}/location` 으로 위치를 먼저 저장하세요)
    - `user_id` 를 안 주면 필터도 위치도 없습니다 — 인기순이고 `distance_m` 은 `null` 입니다
    - 필터가 촘촘하면 `limit` 보다 적게(0장까지) 올 수 있습니다. 필터를 무시하고 채우지는
      않습니다. **필터로 걸러진 카드는 '보여준 것' 으로 세지 않으므로** 필터를 풀면 다시 나옵니다
    """
    started = time.perf_counter()
    if user_id:
        user_registry.validate_id(user_id)
    loader.load_all()
    pos = _position(user_id)
    saved = user_registry.filter_of(user_id) if user_id else None
    cats: list[str] = []
    want_tags: list[str] = []
    within_m: int | None = None
    if saved:
        # 넣을 때 검증했지만 그 뒤 어휘가 바뀔 수 있다. 매 요청을 422 로 죽이는 것보다
        # 이제 없는 값만 버리고 남은 조건으로 좁히는 쪽이 낫다(422 는 PUT 의 몫이다).
        cats = [c for c in saved["cat"] if c in filters.CAT_CHOICES]
        want_tags = [t for t in saved["tag"] if t in filters.TAGS]
        within_m = saved["within_m"]
        if within_m is not None and pos is None:
            raise HTTPException(400, "저장된 필터의 within_m 을 쓸 위치가 없습니다 — "
                                     "PUT /users/{user_id}/location 으로 위치를 저장하거나 "
                                     "PUT /users/{user_id}/filter 로 반경을 지우세요")
    allow = filters.allow_ids(cats=cats, tags=want_tags,
                              within_m=within_m,
                              lat=pos[0] if pos else None,
                              lon=pos[1] if pos else None)
    # 필터가 '누가 후보인가' 를 정했으면, 정렬 보정이 '누가 먼저인가' 를 정한다
    # (겹치는 태그 수 -> 1순위 키, 거리 -> 점수에 섞임. `app/ranking.py`).
    tier_by, bonus_by, bonus_parts = ranking.build_detailed(saved, pos, allow)
    reasons: dict[str, str] = {}
    tags: dict[str, str] = {}
    explain: dict[str, dict] = {}
    if user_id:
        sess = policy.session_for(user_id)
        store_ids = policy.pick_many(sess, limit, reasons=reasons, tags=tags,
                                     allow=allow, tiers=tier_by, bonus=bonus_by,
                                     explain=explain)
        card_ids = loader.to_card_ids(store_ids)
    else:
        # user_id 가 없으면 개인화 없이 평판(베이지안 평균)순 — 필터는 똑같이 걸린다
        p = swipe.pool()
        cand = [i for i in range(len(p["ids"]))
                if allow is None or p["ids"][i] in allow]
        order = sorted(cand, key=lambda i: -p["qz"][i])[:limit]
        card_ids = loader.to_card_ids([p["ids"][i] for i in order])

    out = []
    for cid in card_ids:
        card = loader.get_card(cid, n_reviews=reviews)
        if card is None:
            continue
        distance_m = geo.distance_m(card.store.id, *(pos or (None, None)))
        reason = (reasons.get(card.store.id) if user_id else
                  swipe.recommendation_reason(None, card.store.id))
        if not tier_by:            # 계층이 순서를 정한 요청에서는 태그가 더 큰 이유다
            reason = _near_reason(card, distance_m) or reason
        sid = card.store.id
        out.append({**_card_out(card, bookmarked=bookmarks.has(identities.bookmark_key(user_id), cid)),
                    "recommendation_reason": reason,
                    "policy": tags.get(sid),
                    "attribution": _attribution(tags.get(sid), explain.get(sid),
                                                tier_by.get(sid), bonus_parts.get(sid)),
                    "distance_m": distance_m})

    by_policy: dict[str, int] = {}
    for card in out:
        by_policy[card["policy"] or "popular"] = \
            by_policy.get(card["policy"] or "popular", 0) + 1
    for name, n in by_policy.items():
        observe.inc_cards(name, n)
    observe.inc_cards_request(filtered=saved is not None,
                              personalized=bool(user_id),
                              short=len(out) < limit)
    observe.inc_cards_ranked("tier" if tier_by else
                             "distance" if bonus_by else "none")
    observe.log("cards.serve", user_id=user_id, n_requested=limit,
                n_returned=len(out), policies=by_policy,
                filter={"cat": cats, "tag": want_tags, "within_m": within_m}
                if saved else None,
                has_location=pos is not None, tiered=bool(tier_by),
                dist_weight=ranking.weight() if bonus_by else None,
                ms=round((time.perf_counter() - started) * 1000, 1))
    return out


@app.get("/cards/{card_id}", summary="카드 상세 정보", tags=["카드"],
         response_model=CardDetailOut)
def get_card(
    card_id: int,
    reviews: int = Query(loader.PREVIEW_REVIEW, ge=0, le=50),
    user_id: str | None = Query(None, description="저장된 위치로 거리를 계산합니다"),
    original_images: bool = Query(False, description=
        "사진 확대 시에만 true. 응답 구조와 사진 순서를 유지하며 수집 원본 URL을 반환합니다"),
):
    """카드 한 장의 상세 정보를 반환합니다.

    목록에서 주던 내용에 **메뉴가 추가**되고, 리뷰가 더 많이 들어옵니다.

    - `menus` : 그 가게의 메뉴 전부. 메뉴가 없는 가게는 **빈 배열** `[]`
      - `menus[].price` 는 **가격이 표기된 메뉴에만** 있습니다(전체의 56%). 카카오가 가격을
        안 주는 메뉴는 `null` 입니다. **`-1` 이나 `0` 은 나오지 않습니다.**
        `price_text` 가 있으면(`"변동가격"` 등) 그걸 대신 보여주세요.
      - `menus[].image` 는 가게가 사진을 등록한 메뉴에만 있습니다(12%).
      - `menus[].description` 은 구성 설명("돈가스+미니 소바"), 없으면 `null`
    - `store.reviews` : 수집 리뷰 `reviews` 건 (기본 5). 없으면 빈 배열 `[]`.
      **표현력이 높은 리뷰가 앞에 옵니다** — 무엇이 어떤지 구체적으로 쓴 것부터
      - ⚠️ 그 **앞에 우리 앱 사용자의 방문 후기**가 붙습니다
        (`source_platform: "matchuri"`, `id >= 10**12`). 그래서 배열 길이가 `reviews`
        보다 클 수 있고, `store.review_count` 에도 이 후기가 세어 들어갑니다
        (`store.score` 는 그대로).
        같은 후기가 `user_reviews` 에도 있으니 **두 자리를 다 그리면 두 번 보입니다**
      - `store.reviews[].score` 는 1~5 정수이고 `null` 일 수 있습니다.
      - `store.reviews[].images` 는 사진이 없으면 빈 배열 `[]`
      - `store.reviews[].tags` 는 그 리뷰가 받은 태그(맛/친절/…)
    - `store.review_count` : **여기서 볼 수 있는** 리뷰 수 = `GET /cards/{card_id}/reviews`
      가 돌려주는 개수(수집 리뷰 + 우리 사용자 후기). **카카오에 달린 전체 리뷰 수가
      아닙니다** — 수집은 가게당 상한이 있어서, 카카오 화면에 928개라고 적힌 가게도
      여기서 볼 수 있는 것은 37건일 수 있습니다. 목록과 상세가 같은 값을 줍니다

    - `distance_m` : 위치에서 이 가게까지의 거리(m, 정수). `user_id` 의 **저장된 위치**가
      있을 때만 값이고, 모르면 `null` 입니다(목록과 같은 규칙).
      상세는 필터와 무관합니다 — 필터에 걸리지 않는 가게도 id 로는 다 조회됩니다.

    전부 보려면 `GET /cards/{card_id}/reviews` 를 부르세요. 없는 `card_id` 면 404 입니다.
    """
    if user_id:
        user_registry.validate_id(user_id)
    # 사용자 후기를 `store.reviews` 앞에 섞는 것은 **상세에서만**이다. 목록은 카드 수만큼
    # 곱해지는 자리다. 세션 경로(`/user/{uid}/session/{sid}/cards/{card_id}`)는
    # `app/user_api.py` 가 이 함수를 그대로 옮겨 다는 것이라 저절로 따라온다.
    card = loader.get_card(card_id, n_reviews=reviews, original_images=original_images,
                           include_user_reviews=True)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    pos = _position(user_id)
    return {**_card_out(card, menus=loader.get_menus(card_id, original_images=original_images),
                        bookmarked=bookmarks.has(identities.bookmark_key(user_id), card_id)),
            "distance_m": geo.distance_m(card.store.id, *(pos or (None, None)))}


@app.get("/cards/{card_id}/reviews", summary="카드 리뷰 전체", tags=["카드"],
         response_model=list[Review])
def get_card_reviews(card_id: int, limit: int = Query(0, ge=0, le=1000),
                     original_images: bool = Query(False, description=
                         "사진 확대 시에만 true. 기존 images 필드로 수집 원본 URL을 반환합니다")):
    """그 가게의 리뷰를 반환합니다. "리뷰 더보기" 용입니다.

    - 상세와 마찬가지로 **우리 앱 사용자의 방문 후기가 맨 앞**에 옵니다
      (`source_platform: "matchuri"`, `id >= 10**12`). 상세에서 보이던 글이 더보기에서
      사라지지 않게 하기 위해서입니다.
    - `limit` 을 주면 그만큼만, 0(기본)이면 전부입니다. 페이징은 없습니다.
    - 리뷰가 없는 가게는 **빈 배열** `[]` 입니다.
    - `content` 에 **줄바꿈이 들어 있습니다.** 화면에 그대로 뿌리면 한 줄로 뭉칩니다.

    없는 `card_id` 면 404 입니다.
    """
    if loader.to_store_id(card_id) is None:
        raise HTTPException(status_code=404, detail="card not found")
    return loader.get_reviews(card_id, limit=limit or None, original_images=original_images)


def _decision_scope(user_id: str):
    pos = _position(user_id)
    saved = user_registry.filter_of(user_id)
    context = {"categories": (saved or {}).get("cat", []), "tags": (saved or {}).get("tag", []),
               "within_m": (saved or {}).get("within_m"), "has_position": pos is not None,
               "location_required": bool(saved and saved["within_m"] is not None and pos is None)}
    allow = set() if context["location_required"] else filters.allow_ids(
        cats=[c for c in context["categories"] if c in filters.CAT_CHOICES],
        tags=[t for t in context["tags"] if t in filters.TAGS],
        within_m=context["within_m"], lat=pos[0] if pos else None, lon=pos[1] if pos else None)
    tiers, bonus = ranking.build(saved, pos, allow)
    return pos, context, allow, tiers, bonus


def _with_decision_feedback(user_id, snapshot, pos, context):
    # A combined preference/metrics/decision response shares the same outcome snapshot.
    if 'declined_slates' not in snapshot:
        snapshot['declined_slates'] = decision_feedback.declined(
            user_id, decision_feedback.scope_key(context, pos, session_id=rec_sessions.explicit_id(user_id)))
    return snapshot


def _decision_out(user_id: str, *, snapshot=None, scope=None, evaluation=None, n=4, manual=False):
    sess = snapshot if snapshot is not None else policy.taste_snapshot(policy.session_for(user_id))
    pos, context, allow, tiers, bonus = scope if scope is not None else _decision_scope(user_id)
    sess = _with_decision_feedback(user_id, sess, pos, context)
    if context["location_required"]:
        result = {"ready": False, "status": "location_required", "store_ids": [],
                  "rated_count": len(set(sess["like_ids"]) | set(sess["dislike_ids"])),
                  "liked_count": len(sess["like_ids"]), "stability": None,
                  "validation_auc": None, "qualified_count": 0}
    else:
        evaluator = decision.recommend if manual else decision.evaluate
        result = dict(evaluation) if evaluation is not None else evaluator(
            sess, allow=allow, tiers=tiers, bonus=bonus, config=replace(decision.DEFAULT, count=n))
    ids = result.pop("store_ids")
    reasons = decision_comments.reasons(sess, ids, manual=manual)
    cards = []
    for cid in loader.to_card_ids(ids):
        card = loader.get_card(cid, n_reviews=1)
        if card is not None:
            cards.append({**_card_out(card, bookmarked=bookmarks.has(identities.bookmark_key(user_id), cid)),
                          "distance_m": geo.distance_m(card.store.id, *(pos or (None, None))),
                          "liked": card.store.id in sess["like_ids"],
                          "recommendation_reason": reasons[card.store.id]})
    if result["ready"] and len(cards) != n:
        result.update(ready=False, status="insufficient_candidates")
        cards = []
    try:
        proposal_id = decision_feedback.offer(user_id, decision_feedback.scope_key(context, pos, session_id=rec_sessions.explicit_id(user_id)),
            {c['card_id']: c['store'].id for c in cards}) if cards else None
    except OSError as exc:
        raise HTTPException(503, 'could not save decision proposal; retry') from exc
    result.update(cards=cards,
                  proposal_id=proposal_id,
                  message=((f"현재 정보로 {'네' if n == 4 else n} 곳을 골랐어요. 마음에 드는 곳을 선택하거나 더 둘러보세요."
                            if manual else f"취향에 맞는 {'네' if n == 4 else n} 곳을 골랐어요. 이제 어디로 갈지 골라볼까요?")
                           if cards else None))
    observe.log("decision.evaluate", user_id=user_id, mode="manual" if manual else "auto", status=result["status"],
                rated_count=result["rated_count"], stability=result["stability"],
                validation_auc=result["validation_auc"],
                qualified_count=result["qualified_count"], proposal_id=result["proposal_id"])
    return {key: result[key] for key in ("ready", "status", "proposal_id", "message", "cards")}


@app.get("/users/{user_id}/decision", summary="선택할 가게 제안", tags=["선택 제안"],
         response_model=DecisionOut)
@rec_sessions.scoped
def get_decision(user_id: str,
                 n: int = Query(4, ge=1, le=decision.MAX_COUNT, description="제안 장수. 기본4, 최대20"),
                 mode: Literal["manual", "auto"] = Query("manual", description="manual: 점수와 관계없이 현재 최선의 n곳. auto: 기존 검증 조건 충족 시에만 권유")):
    """기본 manual은 스와이프 전에도 현재 정보로 n곳을 반환한다. 점수로 차단하지 않는다.

    mode=auto는 기존 취향 검증·적합도·다양성을 모두 통과해야 제안한다.
    두 모드 모두 필터·거리를 지키며, 후보 부족/필수 위치 누락/분석 불가 시 빈 목록이다.

    조회는 학습·스와이프 노출을 변경하지 않는다. 새 제안은 결과 접수를 위해 기록한다.
    같은 proposal_id는 중복 표시하지 않는다. 선택 또는 계속 둘러보기는
    POST /users/{user_id}/decision/feedback으로 전달한다.
    """
    user_registry.validate_id(user_id)
    if user_registry.get(user_id) is None and user_id not in policy.sessions():
        raise HTTPException(404, "user not found")
    return _decision_out(user_id, n=n, manual=mode == "manual")


@app.post('/users/{user_id}/decision/feedback', summary='선택 제안 결과 기록',
          tags=['선택 제안'], response_model=DecisionFeedbackOut)
@rec_sessions.scoped
def post_decision_feedback(user_id: str, body: DecisionFeedbackIn):
    """select는 선택 결과를 보관하고, continue는 같은 범위의 동일 조합 재제안을 막는다.

    계속 둘러본 뒤 기존 /cards와 /preferences로 스와이프를 이어간다. 선택/미선택은
    like/dislike로 변환하지 않는다. 같은 결과 재전송은 멱등, 다른 결과는409다.
    서버가 발급한 최근100개 제안만 접수하며 타 사용자/만료된 제안은404다.
    """
    user_registry.validate_id(user_id)
    try:
        decision_feedback.record(user_id, body.proposal_id, body.action, body.card_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, 'could not save decision feedback; retry') from exc
    return {'ok': True}


def _decision_metrics_out(user_id: str, *, snapshot=None, scope=None, **kwargs):
    snapshot = snapshot if snapshot is not None else policy.taste_snapshot(policy.session_for(user_id))
    pos, context, allow, tiers, bonus = scope if scope is not None else _decision_scope(user_id)
    snapshot = _with_decision_feedback(user_id, snapshot, pos, context)
    try:
        return decision_metrics.export(snapshot, allow=allow, tiers=tiers, bonus=bonus,
                                       context=context, **kwargs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/users/{user_id}/decision/metrics", summary="선택 제안 판단 수치 내보내기",
         tags=["판단 수치"], response_model=DecisionMetricsOut)
@rec_sessions.scoped
def get_decision_metrics(user_id: str, response: Response,
                         candidate_offset: int = Query(0, ge=0),
                         candidate_limit: int = Query(60, ge=1, le=200),
                         pair_store_ids: list[str] | None = Query(None, max_length=20),
                         snapshot_id: str | None = Query(None),
                         download: bool = Query(False),
                         n: int = Query(4, ge=1, le=decision.MAX_COUNT, description="제안 평가 장수. decision 조회와 같은 값 사용")):
    """준비 전에도 개인화·후보별 적합도·쌍별 원시 유사도와 근거량을 반환한다.

    후보는 페이지로 전체 자격 풀을 조회한다. 다음 페이지에 snapshot_id를 보내면
    학습/필터/설정 변경 시 409. 쌍은 기본 제안4곳 또는 진단 상위8곳이다.
    최대20개 가게를 명시하면 그 사이 모든 쌍(최대190개)을 반환한다.
    """
    user_registry.validate_id(user_id)
    if user_registry.get(user_id) is None and user_id not in policy.sessions():
        raise HTTPException(404, "user not found")
    result = _decision_metrics_out(user_id, candidate_offset=candidate_offset,
                                   candidate_limit=candidate_limit, pair_store_ids=pair_store_ids,
                                   config=replace(decision.DEFAULT, count=n))
    if snapshot_id is not None and snapshot_id != result["snapshot_id"]:
        raise HTTPException(409, "decision snapshot changed; restart export")
    if download:
        response.headers["Content-Disposition"] = f'attachment; filename="decision-metrics-{result["feedback_count"]}.json"'
    response.headers["Cache-Control"] = "no-store"
    return result


@app.get("/users/{user_id}/decision/metrics/summary", summary="클라이언트용 가벼운 판단 수치",
         tags=["판단 수치"], response_model=DecisionSummaryOut)
@rec_sessions.scoped
def get_decision_summary(user_id: str, response: Response,
                         card_ids: list[int] | None = Query(None, max_length=decision.MAX_COUNT,
                             description="화면 카드의 card_id를 반복 지정. 최대20, 생략 시 지정 카드의 다양성만 미측정"),
                         n: int = Query(4, ge=1, le=decision.MAX_COUNT,
                             description="자동 제안의 후보 분석 장수. 개인화 점수는 n과 무관. card_ids 개수와 별개")):
    """전체 서버 분석 결과에서 개인화 점수·적합 가게 수·가장 비슷한 카드 쌍만 반환한다.

    개인화·전체 후보 채점·자기 참조 제외·조합 탐색은 상세 API와 동일하게 수행한다.
    응답만 작게 유지한다. 카드 목록은 제안이 아닌 측정 대상이다.
    다른 스와이프 요청과 같은 시점이라고 가정하지 않는다. 학습/노출을 변경하지 않는다.
    """
    user_registry.validate_id(user_id)
    if user_registry.get(user_id) is None and user_id not in policy.sessions():
        raise HTTPException(404, "user not found")
    cards = [(cid, loader.to_store_id(cid)) for cid in dict.fromkeys(card_ids or [])]
    if any(sid is None for _, sid in cards):
        raise HTTPException(422, "unknown card_ids")
    snapshot = policy.taste_snapshot(policy.session_for(user_id))
    pos, context, allow, tiers, bonus = _decision_scope(user_id)
    snapshot = _with_decision_feedback(user_id, snapshot, pos, context)
    try:
        result = decision_summary.summarize(snapshot, cards, allow=allow, context=context,
                                            tiers=tiers, bonus=bonus,
                                            config=replace(decision.DEFAULT, count=n))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return result


@app.post("/preferences", summary="선호 표현하기", tags=["선호"],
          response_model=PreferenceOut)
@rec_sessions.scoped
def post_preference(log: Log, request: Request,
                    include_decision: bool = Query(False, description="학습 직후 선택 제안도 반환"),
                    include_decision_metrics: bool = Query(False, description="이 피드백 직후 판단 수치 내보내기"),
                    n: int = Query(4, ge=1, le=decision.MAX_COUNT, description="선택 제안 옵션의 장수. 기본4")):
    """카드 하나에 대한 선호를 기록하고 **추천 모델에 바로 반영**합니다.

    요청 본문 예시:

        {"user_id": "u_3f2a…", "card_id": 12, "prefer": "like"}

    - `user_id` : `POST /users` 로 받은 값. 이 값으로 취향을 학습합니다
    - `card_id` : `GET /cards` 에서 받은 `card_id`
    - `prefer` : 오른쪽으로 넘겼으면 `"like"`, 왼쪽이면 `"dislike"`

    응답의 `learned` 로 지금까지 학습된 취향을 확인할 수 있습니다(디버깅용).
    기록은 파일에 남으므로 서버를 재시작해도 유지됩니다.

    없는 `card_id` 는 404, `prefer` 가 둘 중 하나가 아니면 400 입니다.
    """
    user_registry.validate_id(log.user_id)
    # 본문에서 확인한 뒤 요청에 심어 둔다 — 미들웨어의 http 줄에 `user_id` 가 붙는다.
    request.state.user_id = log.user_id
    if log.prefer not in ("like", "dislike"):
        raise HTTPException(status_code=400, detail="prefer must be 'like' or 'dislike'")
    store_id = loader.to_store_id(log.card_id)
    if store_id is None:
        raise HTTPException(status_code=404, detail="card not found")

    if identities.owner(log.user_id) is None:
        identities.ensure_legacy(log.user_id, user_registry.get(log.user_id))
    logs.append(log)
    sess = policy.session_for(log.user_id)
    before = (sess["legacy"]["n"], sess["taste"]["n"])
    # 메모리 적용과 파일 기록을 한 절단점 안에 — 그 사이에 스냅샷이 찍히면 재시작 뒤 이
    # 한 건이 두 번 학습된다(app/snapshot.py 머리말).
    with state.PREF_LOCK:
        metric_snapshot = _apply(log.user_id, log.card_id, log.prefer,
                                 snapshot=include_decision_metrics is True or include_decision is True)
        state.record_preference(log.user_id, log.card_id, log.prefer, rec_sessions.current_id(log.user_id))
    learned = [name for i, name in enumerate(("legacy", "taste"))
               if (sess["legacy"]["n"], sess["taste"]["n"])[i] > before[i]]
    hist = sess["taste"].get("hist") or []
    # 그 카드를 **보여준** 정책이 이 선호의 라벨이다(노출 기록이 없으면 `none`).
    exposed = policy.exposed_by(log.user_id, store_id) or "none"
    observe.inc_pref(log.prefer, exposed)
    observe.log("pref.record", user_id=log.user_id, card_id=log.card_id,
                store_id=store_id, prefer=log.prefer, exposed_by=exposed,
                learned=learned,
                pairs=hist[-1].get("pairs") if hist and "taste" in learned else None,
                swipes=sess["legacy"]["n"])
    decision_scope = _decision_scope(log.user_id) if metric_snapshot is not None else None
    metrics_out = _decision_metrics_out(log.user_id, snapshot=metric_snapshot, scope=decision_scope,
                                       config=replace(decision.DEFAULT, count=n)) \
        if include_decision_metrics is True else None
    decision_out = None
    if include_decision is True:
        try:
            decision_out = _decision_out(log.user_id, snapshot=metric_snapshot, scope=decision_scope,
                evaluation=metrics_out["decision"] if metrics_out is not None else None, n=n)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            # The preference has already succeeded. Do not invite a duplicate
            # swipe retry just because optional proposal persistence failed.
            observe.log('decision.offer_unavailable', level=logging.WARNING, user_id=log.user_id)
    return {
        "ok": True,
        "count": len(logs),
        # detail=False — 이 응답이 이 엔드포인트 비용의 76~88% 였다(recsys/taste.py: learn_state).
        "learned": policy.learn_state(policy.session_for(log.user_id), detail=False),
        "decision": decision_out,
        "decision_metrics": metrics_out,
    }


@app.get("/preferences/{user_id}", summary="선호한 카드 목록", tags=["선호"],
         response_model=list[CardBaseOut])
def get_preferences(
    user_id: str,
    prefer: str = Query("like", description="like · dislike · all",
                        json_schema_extra={"enum": ["like", "dislike", "all"]}),
):
    """그 사용자가 선호를 표현한 카드를 **표현한 순서대로** 반환합니다.

    - 기본은 `like` 한 카드만 줍니다. `prefer=dislike` 나 `prefer=all` 도 됩니다.
    - 같은 카드를 두 번 표현하면 두 번 들어갑니다. (거르지 않습니다)
    - 기록이 없는 `user_id` 면 404 가 아니라 **빈 배열** `[]` 입니다.
    """
    user_registry.validate_id(user_id)
    if prefer not in ("like", "dislike", "all"):
        raise HTTPException(status_code=400,
                            detail="prefer must be 'like', 'dislike' or 'all'")
    out = []
    for log in logs:
        if log.user_id != user_id:
            continue
        if prefer != "all" and log.prefer != prefer:
            continue
        card = loader.get_card(log.card_id, n_reviews=0)
        if card:
            out.append(_card_out(card,
                                 bookmarked=bookmarks.has(identities.bookmark_key(user_id), log.card_id)))
    return out


# ── 예전 경로. 프론트가 옮겨 갈 때까지 남겨 둔다(문서에는 숨김) ─────────
@app.post("/logs", include_in_schema=False)
def post_log_legacy(log: Log, request: Request):
    return post_preference(log, request)


@app.get("/logs/{user_id}", include_in_schema=False)
def get_logs_legacy(user_id: str):
    return get_preferences(user_id, prefer="like")


# Explicit person → session API; old endpoints remain session-ID compatibility adapters.
from app import user_api
user_api.install(app, post_preference, _card_out)
