# -*- coding: utf-8 -*-
"""카카오맵 '사람 기준' 리뷰 API 클라이언트.

place.map.kakao.com(장소 SPA)에는 유저 프로필 기능이 없지만,
map.kakao.com 본체에는 있다.

  프로필   GET map.kakao.com/profile/info.json?mapUserId={uid}
  리뷰목록 GET map.kakao.com/review/list.json
           ?order=UPDATE&onlyRevisitY=false&mapUserId={uid}&reviewIndex={cursor}

인증/쿠키/특수헤더 불필요(Referer만). 응답은 75건 고정이고
페이지네이션은 offset이 아니라 **직전 페이지 마지막 리뷰의 reviewId**를
reviewIndex로 넘기는 커서 방식이다(map.kakao.com app.js 로직 확인).
"""
from __future__ import annotations

import random
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PROFILE_URL = "https://map.kakao.com/profile/info.json"
REVIEW_URL = "https://map.kakao.com/review/list.json"


class KakaoUserClient:
    def __init__(self, delay: float = 1.5, jitter: float = 0.7, retries: int = 3):
        self.delay = delay
        self.jitter = jitter
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Referer": "https://map.kakao.com/",
            "Accept": "application/json",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        self.calls = 0

    def _sleep(self) -> None:
        time.sleep(self.delay + random.random() * self.jitter)

    def _get(self, url: str, params: dict) -> dict | None:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params=params, timeout=25)
                self.calls += 1
                if r.status_code == 200:
                    self._sleep()
                    return r.json()
                if r.status_code in (403, 404):
                    self._sleep()
                    return None
                last = f"HTTP {r.status_code}"
            except Exception as exc:
                last = repr(exc)
            time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"요청 실패 {url} {params}: {last}")

    # ------------------------------------------------------------------
    def profile(self, uid: str) -> dict | None:
        d = self._get(PROFILE_URL, {"mapUserId": uid})
        if not d or (d.get("status") or {}).get("code") != "SUCCESS":
            return None
        return d.get("result")

    def reviews(self, uid: str, max_reviews: int = 0) -> list[dict] | None:
        """유저의 리뷰를 커서 페이지네이션으로 전량(또는 max_reviews까지) 수집.

        비공개 프로필이면 result.reviews 가 없다 -> None 반환.
        """
        out: list[dict] = []
        seen: set[str] = set()
        cursor = None
        while True:
            p = {"order": "UPDATE", "onlyRevisitY": "false", "mapUserId": uid}
            if cursor:
                p["reviewIndex"] = cursor
            d = self._get(REVIEW_URL, p)
            if not d:
                break
            res = d.get("result") or {}
            rv = res.get("reviews")
            if rv is None:                 # 비공개 계정
                return None if not out else out
            if not rv:
                break
            fresh = [x for x in rv if x.get("reviewId") not in seen]
            if not fresh:                  # 커서가 안 움직임 -> 종료
                break
            for x in fresh:
                seen.add(x["reviewId"])
            out.extend(fresh)
            total = res.get("reviewCount") or 0
            if len(out) >= total or (max_reviews and len(out) >= max_reviews):
                break
            cursor = rv[-1]["reviewId"]
        return out[:max_reviews] if max_reviews else out
