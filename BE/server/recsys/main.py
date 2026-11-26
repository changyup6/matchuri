# -*- coding: utf-8 -*-
"""역삼동 음식점 API.

  GET /stores/{store_id}   음식점 정보 + 메뉴 + 리뷰
  GET /recommend           음식점 하나 무작위 추천 (+ 메뉴 + 리뷰)

실행
  uvicorn recsys.main:app --host 0.0.0.0 --port 8077
  문서: http://127.0.0.1:8077/docs
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import store as svc
from . import swipe

app = FastAPI(title="역삼동 음식점 API", version="0.3.0")
app.include_router(swipe.router)

# 브라우저의 교차 출처 차단 해제. 읽기 전용 개발용 API 라 전체 허용한다.
# allow_credentials 는 켜지 않는다 — 와일드카드 출처와 함께 쓰면 브라우저가 거부한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def swipe_page():
    """스와이프 데모 UI. 같은 서버에서 서빙하므로 CORS 걱정이 없다."""
    return (Path(__file__).parent / "static" / "swipe.html").read_text(encoding="utf-8")


class Review(BaseModel):
    review_id: int | None = None
    rating: int | None = Field(None, description="별점 1~5")
    content: str = ""
    date: str | None = None
    strength_tags: list[str] = Field([], description="리뷰 태그: 맛/친절/가성비/분위기/주차")
    like_count: int = 0
    author_review_count: int | None = Field(None, description="작성자 누적 리뷰 수")
    author_avg_score: float | None = None


class Menu(BaseModel):
    menu_name: str | None = None
    price: int | None = Field(None, description="원 단위. 시가·변동가는 null")
    price_text: str | None = Field(None, description="가격이 숫자가 아닐 때(시가 등)")
    is_recommend: bool = False
    description: str | None = Field(None, description="구성 설명")
    photo_url: str | None = None


class Store(BaseModel):
    store_id: str
    name: str | None = None
    cat2: str | None = Field(None, description="중분류: 한식/일식/카페 등")
    cat3: str | None = Field(None, description="소분류: 냉면/한정식 등")
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    kakao_review_count: int = 0
    kakao_avg_score: float | None = None
    review_tags: dict[str, int] = Field({}, description="리뷰 태그 집계: {맛: 32, 친절: 28, …}")
    review_blocked_by_owner: bool = Field(False, description="사장님이 리뷰 노출을 차단한 가게")
    blog_count: int = 0
    ai_title: str | None = Field(None, description="카카오 AI 한줄 요약")
    ai_keywords: list[str] = []
    ai_summary: str | None = None
    subway_station: str | None = None
    subway_exit: str | None = None
    subway_distance_m: int | None = None
    subway_minutes: int | None = None
    phones: list[str] = []
    open_headline: str | None = Field(None, description="영업중/브레이크타임 등 현재 상태")
    tags: list[str] = Field([], description="가게 성격 태그: 데이트코스/혼밥 등")
    facilities: list[str] = Field([], description="무료주차/예약가능 등")
    parking_summary: str | None = None
    delivery: list[dict] = Field([], description="배달 연동(요기요 등)")
    photo_counts: dict[str, int] = Field({}, description="분류별 사진 수: mystore/food/indoor/…")
    photo_urls: list[str] = Field(
        [], description="가게 사진. 공식(매장주·업체) → 음식 → 분위기 → 외관 순, '후기' 사진 제외")
    menus: list[Menu] = Field([], description="메뉴판. 추천 메뉴가 앞에 온다")


class StoreDetail(BaseModel):
    store: Store
    reviews: list[Review]


def _detail(s: dict, n: int, photos: int, menus: int) -> StoreDetail:
    sid = s["store_id"]
    return StoreDetail(
        store=Store(**s,
                    photo_urls=svc.get_photos(sid, limit=photos),
                    menus=[Menu(**m) for m in svc.get_menus(sid, limit=menus)]),
        reviews=[Review(**r) for r in svc.get_reviews(sid, limit=n)],
    )


@app.get("/stores/{store_id}", response_model=StoreDetail,
         summary="음식점 정보 + 메뉴 + 리뷰")
def get_store(
    store_id: str,
    reviews: int = Query(5, ge=0, le=40, description="함께 반환할 리뷰 수"),
    photos: int = Query(10, ge=0, le=100, description="함께 반환할 가게 사진 수"),
    menus: int = Query(20, ge=0, le=300, description="함께 반환할 메뉴 수"),
):
    s = svc.get_store(store_id)
    if not s:
        raise HTTPException(404, f"음식점을 찾을 수 없습니다: {store_id}")
    return _detail(s, reviews, photos, menus)


@app.get("/recommend", response_model=StoreDetail,
         summary="음식점 하나 무작위 추천")
def recommend(
    reviews: int = Query(5, ge=0, le=40, description="함께 반환할 리뷰 수"),
    photos: int = Query(10, ge=0, le=100, description="함께 반환할 가게 사진 수"),
    menus: int = Query(20, ge=0, le=300, description="함께 반환할 메뉴 수"),
    min_reviews: int = Query(1, ge=0, description="리뷰가 이 수 이상인 가게만"),
):
    s = svc.random_store(min_reviews=min_reviews)
    if not s:
        raise HTTPException(404, "조건에 맞는 음식점이 없습니다")
    return _detail(s, reviews, photos, menus)
