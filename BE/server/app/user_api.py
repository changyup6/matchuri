"""User/session HTTP boundary. Reuse the existing recommendation services and DTOs."""
import inspect
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app import identities, recommendation_sessions as sessions, users, taste_api
from app.model import Log, UserCreate, PreferenceOut, LocationOut, FilterOut, CardBaseOut
from recsys import decision

router = APIRouter()


class PersonOut(BaseModel):
    user_id: str
    name: str | None
    created_at: str | None


class StartIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str | None = Field(None, min_length=1, max_length=64,
        description='선택. 동일 사용자에서 재전송하면 같은 세션 반환')


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    card_id: int
    prefer: Literal['like', 'dislike']


class SettingsOut(BaseModel):
    user_id: str
    session_id: str
    location: LocationOut | None
    filter: FilterOut | None


class SessionTasteOut(taste_api.UserTasteOut):
    session_id: str


@router.post('/user', response_model=PersonOut, summary='실제 사용자 생성', tags=['사용자'])
def create_user(body: UserCreate | None = None):
    """사용자는 여러 독립 세션을 소유합니다. 이 ID 자체로 추천 모델을 만들지 않습니다."""
    return identities.create(body.name if body else None)


@router.get('/user/{user_id}', response_model=PersonOut, summary='실제 사용자 조회', tags=['사용자'])
def get_user(user_id: str):
    """실제 사용자 ID·이름·생성 시각만 반환합니다. 학습 진행도는 세션에서 조회합니다."""
    return identities.require(user_id)


@router.get('/session/{session_id}', response_model=sessions.SessionOut,
            summary='기존 ID의 사용자 연결 조회', tags=['추천 세션'])
def lookup_session(session_id: str):
    """기존 user_id를 session_id로 넣으면 새로 연결된 user_id와 진행도를 반환합니다."""
    uid = identities.owner(session_id)
    if uid is None:
        raise HTTPException(404, 'session not found')
    return sessions.independent_info(uid, session_id)


@router.post('/user/{user_id}/session', response_model=sessions.SessionOut,
             summary='독립 추천 세션 생성', tags=['추천 세션'])
def create_session(user_id: str, body: StartIn | None = None):
    """그 사용자의 모든 이전 반응을 약하게 상속합니다. 진행도·노출·검증은 0부터 시작합니다.

    기존 세션은 그대로 남고 계속 사용할 수 있습니다. 필터·위치는 탐색 의도에 속하므로
    세션마다 설정합니다. 같은 사람인지 이름 등으로 추정하거나 자동 병합하지 않습니다.
    """
    return sessions.create_independent(user_id, body.request_id if body else None)


@router.get('/user/{user_id}/session', response_model=list[sessions.SessionOut],
            summary='사용자의 세션 목록', tags=['추천 세션'])
def list_sessions(user_id: str):
    """새 세션을 만들어도 이전 세션이 목록에 남으며 각각 독립적으로 조회할 수 있습니다."""
    identities.require(user_id)
    return [sessions.independent_info(user_id, row['session_id'])
            for row in identities.owned(user_id)]


@router.get('/user/{user_id}/session/{session_id}', response_model=sessions.SessionOut,
            summary='특정 세션 진행도 조회', tags=['추천 세션'])
def get_session(user_id: str, session_id: str):
    """지정한 사용자 소유 세션의 ID와 현재 스와이프 수를 반환합니다. 소유가 다르면404."""
    return sessions.independent_info(user_id, session_id)


@router.get('/user/{user_id}/session/{session_id}/settings', response_model=SettingsOut,
            summary='세션 위치·필터 조회', tags=['추천 세션'])
def get_settings(user_id: str, session_id: str):
    """탐색 세션별 필터·위치를 반환합니다. 다른 세션의 설정은 바꾸지 않습니다."""
    identities.require_session(user_id, session_id)
    record = users.get(session_id) or {}
    return {'user_id': user_id, 'session_id': session_id,
            'location': record.get('location'), 'filter': record.get('filter')}


@router.get('/user/{user_id}/session/{session_id}/taste', response_model=SessionTasteOut,
            summary='세션 취향 지형 조회', tags=['취향'])
def get_taste(user_id: str, session_id: str, detail: taste_api.Detail = 1):
    """해당 세션의 취향 궤적입니다. detail=1·2·3으로 전체 cat2·cat3·가게 배경을 선택합니다."""
    with sessions.lock_for(user_id), sessions.lock_for(session_id):
        identities.require_session(user_id, session_id)
        return {**taste_api.user_taste(session_id, detail=detail),
                'user_id': user_id, 'session_id': session_id}


def _adapt(endpoint, *, session_scope, settings=False):
    """Keep original query validation and response DTOs at the new explicit scope."""
    sig = inspect.signature(endpoint)
    def arguments(kwargs):
        uid = kwargs['user_id']
        if session_scope:
            sid = kwargs.pop('session_id')
            identities.require_session(uid, sid)
            kwargs['user_id'] = sid
            if 'session_id' in sig.parameters:
                kwargs['session_id'] = sessions.current_id(sid)
        else:
            identities.require(uid)
            kwargs['user_id'] = identities.profile_key(uid)
        return uid

    if inspect.iscoroutinefunction(endpoint):
        async def adapted(**kwargs):
            arguments(kwargs)
            return await endpoint(**kwargs)
    else:
        def adapted(**kwargs):
            uid, sid = kwargs['user_id'], kwargs.get('session_id')
            with sessions.lock_for(uid):
                arguments(kwargs)
                result = endpoint(**kwargs)
                if settings:
                    return {'user_id': uid, 'session_id': sid,
                            'location': result.get('location'), 'filter': result.get('filter')}
                return result
    params = [inspect.Parameter('user_id', inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)]
    if session_scope:
        params.append(inspect.Parameter('session_id', inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str))
    params += [p for k, p in sig.parameters.items() if k not in ('user_id', 'session_id')]
    # Endpoint modules use postponed annotations; resolve them before transplanting.
    annotations = inspect.get_annotations(endpoint, eval_str=True)
    params = [p.replace(annotation=annotations.get(p.name, p.annotation))
              if p.name not in ('user_id', 'session_id') else p for p in params]
    adapted.__signature__ = sig.replace(parameters=params)
    adapted.__name__ = ('session_' if session_scope else 'person_') + endpoint.__name__
    return adapted


def install(app, post_preference, card_out):
    @router.get('/user/{user_id}/session/{session_id}/preferences',
                response_model=list[CardBaseOut], summary='해당 세션의 스와이프 카드 이력',
                tags=['선호'])
    def preferences(user_id: str, session_id: str, prefer: Literal['like', 'dislike', 'all'] = 'like'):
        """세션 경계를 분리한 반응 이력입니다. prefer=all이면 like/dislike 모두 시간순으로 반환합니다."""
        from app import admin, loader, bookmarks
        identities.require_session(user_id, session_id)
        result = []
        with sessions.lock_for(user_id), sessions.lock_for(session_id):
            for cid, like in admin._prefs_by_user().get(session_id, []):
                if prefer != 'all' and like != (prefer == 'like'):
                    continue
                card = loader.get_card(cid, n_reviews=0)
                if card:
                    result.append(card_out(card,
                        bookmarked=bookmarks.has(identities.profile_key(user_id), cid)))
        return result

    @router.post('/user/{user_id}/session/{session_id}/preferences',
                 response_model=PreferenceOut, summary='해당 세션에 스와이프 기록', tags=['선호'])
    def preference(user_id: str, session_id: str, body: PreferenceIn, request: Request,
                   include_decision: bool = False, include_decision_metrics: bool = False,
                   n: int = Query(4, ge=1, le=decision.MAX_COUNT)):
        """본문은 card_id·prefer만 받습니다. count는 해당 세션의 누적 스와이프 수입니다.

        옵션으로 같은 학습 시점의 decision/상세 판단 수치를 받을 수 있습니다. 기본 응답은 기존 DTO를 유지합니다.
        """
        with sessions.lock_for(user_id):
            identities.require_session(user_id, session_id)
            result = post_preference(Log(user_id=session_id, session_id=sessions.current_id(session_id),
                                         card_id=body.card_id, prefer=body.prefer), request,
                                     include_decision, include_decision_metrics, n)
            result['count'] = sessions.independent_info(user_id, session_id)['swipes']
            return result

    session_paths = {'/cards': '/cards', '/cards/{card_id}': '/cards/{card_id}',
                     '/users/{user_id}/taste': '/taste'}
    for suffix in ('decision', 'decision/feedback', 'decision/metrics', 'decision/metrics/summary',
                   'location', 'filter'):
        session_paths['/users/{user_id}/' + suffix] = '/' + suffix
    for route in list(app.routes):
        path = getattr(route, 'path', '')
        copied_person_route = path.startswith('/users/{user_id}/') and any(
            path.startswith('/users/{user_id}/' + part)
            for part in ('bookmarks', 'directions', 'surveys'))
        if path in session_paths:
            settings = path.endswith(('/location', '/filter'))
            if settings:
                tags = ['추천 세션']
            elif '/decision/metrics' in path:
                tags = ['판단 수치']
            elif '/decision' in path:
                tags = ['선택 제안']
            else:
                tags = list(route.tags or ['카드'])
            router.add_api_route('/user/{user_id}/session/{session_id}' + session_paths[path],
                _adapt(route.endpoint, session_scope=True, settings=settings), methods=list(route.methods),
                response_model=SettingsOut if settings else route.response_model,
                tags=tags, summary=route.summary or route.name,
                description='경로의 사용자 소유 세션만 처리합니다. 다른 사용자의 세션은404. ' 
                            '기존 API와 같은 분석·카드·판단 수치 schema를 사용합니다.\n\n' + (route.description or ''))
        elif copied_person_route:
            router.add_api_route(path.replace('/users/', '/user/', 1),
                _adapt(route.endpoint, session_scope=False), methods=list(route.methods),
                response_model=route.response_model, status_code=route.status_code,
                tags=list(route.tags or ['사용자']), summary=route.summary or route.name,
                description='실제 사용자에 보관하여 여러 추천 세션에서 함께 사용합니다.')
        if path in session_paths or copied_person_route or path in (
                '/users', '/users/{user_id}', '/users/{user_id}/session',
                '/users/{user_id}/sessions', '/preferences', '/preferences/{user_id}'):
            route.deprecated = True
            route.tags = ['호환 API']
            route.description = '호환용: 이 경로의 user_id는 예전 추천 세션 ID입니다. 신규 연동은 /user 아래의 명시적 사용자/세션 경로를 사용하세요.\n\n' + (route.description or '')
    app.include_router(router)
