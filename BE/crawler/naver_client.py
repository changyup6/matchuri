# -*- coding: utf-8 -*-
"""네이버 지도(플레이스) 비공식(웹 내부) API 클라이언트.

네이버는 봇 탐지가 강해 일반 requests 로는 429/캡차가 뜬다. 두 가지가 필요하다.
  1) curl_cffi 의 크롬 TLS 지문 임퍼소네이션 (그냥 requests 면 즉시 429)
  2) GraphQL 호출 시 WTM 헤더 2종
       X-WTM-GraphQL       = base64url(JSON({"arg":<placeId>,"type":<businessType>,"source":"place"}))
       X-Wtm-NCaptcha-Token = "NCAPTCHA_FALLBACK_NO_OBJECT"   (브라우저 밖 폴백 상수)

엔드포인트
  - 장소 검색 : GET  pcmap.place.naver.com/restaurant/list?query=...  (HTML 안 __APOLLO_STATE__)
  - 리뷰 목록 : POST pcmap-api.place.naver.com/graphql  (operation: getVisitorReviews)
"""
from __future__ import annotations

import base64
import json
import random
import time
from typing import Any

from curl_cffi import requests as cr

LIST_URL = "https://pcmap.place.naver.com/{btype}/list"
PLACE_URL = "https://pcmap.place.naver.com/{btype}/{pid}/review/visitor"
GRAPHQL_URL = "https://pcmap-api.place.naver.com/graphql"

VISITOR_REVIEWS_QUERY = """query getVisitorReviews($input: VisitorReviewsInput) {
  visitorReviews(input: $input) {
    items {
      id
      reviewId
      rating
      body
      created
      visited
      visitCount
      viewCount
      originType
      businessName
      media { type }
      author { id nickname objectId review { totalCount imageCount } }
      votedKeywords { code name }
      reactionStat { typeCount { name count } }
    }
    total
  }
}"""


def _b64url(s: str) -> str:
    return base64.b64encode(s.encode()).decode().replace("+", "-").replace("/", "_").replace("=", "")


def wtm_header(place_id: str, business_type: str = "restaurant") -> str:
    """pcmap 번들의 X-WTM-GraphQL 생성 로직을 그대로 옮긴 것."""
    return _b64url(json.dumps({"arg": place_id, "type": business_type, "source": "place"},
                              separators=(",", ":"), ensure_ascii=False))


def extract_apollo_state(html: str) -> dict | None:
    """HTML 안의 window.__APOLLO_STATE__ 를 중괄호 균형으로 잘라 파싱한다."""
    i = html.find("window.__APOLLO_STATE__")
    if i < 0:
        return None
    i = html.index("{", i)
    depth, in_str, esc = 0, False, False
    for j in range(i, len(html)):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(html[i:j + 1])
    return None


class NaverPlaceClient:
    def __init__(self, delay: float = 1.0, jitter: float = 0.6, retries: int = 3,
                 business_type: str = "restaurant"):
        self.delay = delay
        self.jitter = jitter
        self.retries = retries
        self.btype = business_type
        self.session = cr.Session(impersonate="chrome")
        self.session.headers.update({"Accept-Language": "ko-KR,ko;q=0.9"})

    def _sleep(self) -> None:
        time.sleep(self.delay + random.random() * self.jitter)

    # ---------------------------------------------------------------- search
    def search_places(self, query: str) -> list[dict]:
        """검색 결과 상위 50곳을 랭킹 순서 그대로 반환한다. 광고(adBusinesses)는 제외."""
        last = None
        for attempt in range(self.retries):
            try:
                r = self.session.get(LIST_URL.format(btype=self.btype), params={"query": query},
                                     headers={"Referer": "https://map.naver.com/"}, timeout=30)
                if r.status_code == 200:
                    st = extract_apollo_state(r.text)
                    self._sleep()
                    if not st:
                        return []
                    for k, v in (st.get("ROOT_QUERY") or {}).items():
                        if k.startswith("placeList(") and '"display":50' in k:
                            items = (v.get("businesses") or {}).get("items") or []
                            return [st[i["__ref"]] for i in items if i.get("__ref") in st]
                    return []
                last = f"HTTP {r.status_code}"
            except Exception as exc:
                last = repr(exc)
            time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"검색 실패 {query}: {last}")

    # --------------------------------------------------------------- reviews
    def visitor_reviews(self, place_id: str, size: int = 30, page: int = 1) -> dict | None:
        """방문자 리뷰. 정렬 파라미터는 서버가 무시하며 항상 최신순으로 내려온다."""
        payload = [{
            "operationName": "getVisitorReviews",
            "variables": {"input": {
                "businessId": place_id, "businessType": self.btype, "item": "0",
                "page": page, "size": size, "isPhotoUsed": False,
                "includeContent": True, "getUserStats": True, "getReactions": True,
                "includeReceiptPhotos": True, "cidList": []}},
            "query": VISITOR_REVIEWS_QUERY,
        }]
        headers = {
            "Content-Type": "application/json",
            "Referer": PLACE_URL.format(btype=self.btype, pid=place_id),
            "Origin": "https://pcmap.place.naver.com",
            "X-WTM-GraphQL": wtm_header(place_id, self.btype),
            "X-Wtm-NCaptcha-Token": "NCAPTCHA_FALLBACK_NO_OBJECT",
            "Accept": "*/*",
        }
        last = None
        for attempt in range(self.retries):
            try:
                r = self.session.post(GRAPHQL_URL, json=payload, headers=headers, timeout=30)
                if r.status_code == 200:
                    self._sleep()
                    body = r.json()
                    node = body[0] if isinstance(body, list) else body
                    if node.get("errors"):
                        return None
                    return (node.get("data") or {}).get("visitorReviews")
                last = f"HTTP {r.status_code}"
            except Exception as exc:
                last = repr(exc)
            time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"리뷰 실패 {place_id}: {last}")
