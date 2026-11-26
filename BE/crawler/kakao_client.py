# -*- coding: utf-8 -*-
"""카카오맵 비공식(웹 내부) API 클라이언트.

카카오맵 웹이 실제로 호출하는 엔드포인트를 그대로 사용한다.
  - 장소 검색 : https://search.map.kakao.com/mapsearch/map.daum
  - 장소 상세 : https://place-api.map.kakao.com/places/panel3/{confirm_id}
  - 리뷰 목록 : https://place-api.map.kakao.com/places/tab/reviews/kakaomap/{confirm_id}
"""
from __future__ import annotations

import random
import time
from typing import Any

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SEARCH_URL = "https://search.map.kakao.com/mapsearch/map.daum"
PANEL_URL = "https://place-api.map.kakao.com/places/panel3/{pid}"
REVIEW_URL = "https://place-api.map.kakao.com/places/tab/reviews/kakaomap/{pid}"
PHOTOS_URL = "https://place-api.map.kakao.com/places/tab/photos/{pid}"
BLOG_URL = "https://place-api.map.kakao.com/places/tab/reviews/blog/{pid}"

# 사진 탭 분류 태그 (웹 번들 pi3P160w.js 에서 확인). ?tag=INDOOR&page=1 형식.
#   MYSTORE=매장주제공  VENDOR=업체제공  VOD=영상  KAKAOMAP_REVIEW=후기
#   FOOD=음식  INDOOR=분위기  OUTDOOR=외관  MENU=메뉴판
PHOTO_TAGS = ["MYSTORE", "VENDOR", "FOOD", "INDOOR", "OUTDOOR", "MENU"]


class KakaoMapClient:
    """예의 있는(rate-limited) 카카오맵 스크레이퍼."""

    def __init__(self, delay: float = 0.8, jitter: float = 0.4, retries: int = 3):
        self.delay = delay
        self.jitter = jitter
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})

    # ------------------------------------------------------------------ util
    def _sleep(self) -> None:
        time.sleep(self.delay + random.random() * self.jitter)

    def _get(self, url: str, *, params=None, headers=None) -> Any:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=25)
                if r.status_code == 200:
                    self._sleep()
                    return r.json()
                if r.status_code in (404, 403):
                    self._sleep()
                    return None
                last = f"HTTP {r.status_code}"
            except Exception as exc:  # 네트워크 오류
                last = repr(exc)
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET 실패 {url} params={params}: {last}")

    # ---------------------------------------------------------------- public
    def search_places(self, query: str, page: int = 1) -> list[dict]:
        """키워드로 장소 검색. 카카오맵 기본 정렬(정확도/인기) 순서를 그대로 유지한다."""
        data = self._get(
            SEARCH_URL,
            params={"q": query, "msFlag": "A", "sort": 0, "page": page},
            headers={"Referer": "https://map.kakao.com/"},
        )
        return (data or {}).get("place") or []

    def place_detail(self, pid: str) -> dict | None:
        return self._get(
            PANEL_URL.format(pid=pid),
            headers={
                "Referer": f"https://place.map.kakao.com/{pid}",
                "Origin": "https://place.map.kakao.com",
                "pf": "web",
                "Accept": "application/json",
            },
        )

    def _place_headers(self, pid: str) -> dict:
        return {
            "Referer": f"https://place.map.kakao.com/{pid}",
            "Origin": "https://place.map.kakao.com",
            "pf": "web",
            "Accept": "application/json",
        }

    def reviews(self, pid: str, order: str = "RECOMMENDED") -> dict | None:
        """카카오맵 자체 후기(별점+텍스트). order: RECOMMENDED | LATEST"""
        return self._get(
            REVIEW_URL.format(pid=pid),
            params={"order": order, "only_photo_review": "false"},
            headers=self._place_headers(pid),
        )

    def photos(self, pid: str, tag: str | None = None, page: int = 1) -> dict | None:
        """사진 탭. tag 없으면 전체(최신순), 있으면 분류별. 페이지당 30장."""
        params = {"page": page}
        if tag:
            params["tag"] = tag.upper()
        return self._get(PHOTOS_URL.format(pid=pid), params=params,
                         headers=self._place_headers(pid))

    def blog_reviews(self, pid: str, page: int = 1) -> dict | None:
        """블로그 리뷰 탭. 페이지당 10건(제목·본문 일부·작성자·사진)."""
        return self._get(BLOG_URL.format(pid=pid), params={"page": page},
                         headers=self._place_headers(pid))
