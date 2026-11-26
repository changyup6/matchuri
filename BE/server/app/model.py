# -*- coding: utf-8 -*-
"""응답·요청 모델 — `/openapi.json` 이 곧 클라이언트의 타입 정의다.

프론트가 `npx openapi-typescript http://host/openapi.json -o api.d.ts` 한 줄로 타입을
받아 쓴다. 그래서 모델은 두 가지를 다 해야 한다: 서버가 실제로 내보내는 모양과 **정확히**
같을 것, 그리고 필드마다 뜻·null 규칙·예시를 스키마에 실을 것. 여기 적힌 설명이 `/docs`
에 그대로 뜨므로 main.py 의 docstring 과 같은 말을 한 번만 쓴다.

dataclass 였던 것을 pydantic v2 `BaseModel` 로 바꿨다 — dataclass 는 FastAPI 가 응답
스키마로 못 쓰고(`resp=NONE`), 필드 설명·예시를 실을 자리도 없었다. 생성은 그대로
키워드 인자다(`app/loader.py`).

**고정된 값 목록은 스키마의 `enum` 으로 싣지만 타입은 `str` 이다.** 업종·태그 어휘는
데이터에서 나온 목록(`app/filters.py`)이고 언제든 늘거나 줄 수 있다. `Literal` 로 박으면
① 클라이언트가 옛 값을 보낼 때 pydantic 이 먼저 422 를 내며 `filters.validate` 의
한국어 메시지("cat '초밥' 은 없는 값입니다. 허용: …")를 가로채고, ② 어휘가 바뀐 뒤
파일에 남아 있던 옛 필터를 응답에 실을 때 **500** 이 된다. 코드 생성기는 `enum` 만 보고도
유니온 타입을 만들어 주므로 프론트가 얻는 것은 같다. 서버가 값을 직접 만드는
`policy`(`recsys/policy.py` 의 상수)만 `Literal` 이다.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app import filters
from app.decision_model import DecisionMetricsOut

#: `"15:00 ~ 17:30 브레이크타임"` -> ("15:00", "17:30", "브레이크타임").
#: 뒤의 라벨은 가게마다 다른 자유 문구라서 있는 그대로 넘긴다.
_BREAK_TEXT = re.compile(r"^\s*(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})\s*(.*)$")

#: 이름의 상한. 관리자 화면의 한 줄에 들어가야 하고, 제한이 없으면 등록부가 부푼다.
#: (`user_id` 쪽 규칙은 `app/users.py` 의 `ID_RE` 다 — 그쪽은 등록부가 주인이다.)
NAME_MAX = 50

#: `/docs` 의 허용 값 목록. 어휘의 주인은 `app/filters.py` 다 — 여기서 베끼지 않고 읽는다.
CAT_VALUES: list[str] = list(filters.CAT_CHOICES)
TAG_VALUES: list[str] = list(filters.TAGS)
PREFER_VALUES = ["like", "dislike"]

#: 업종 라벨 하나 — 값은 `GET /filters` 의 `cats[].name` 과 같다.
CatName = Annotated[str, Field(json_schema_extra={"enum": CAT_VALUES},
                               examples=["일식"])]
#: 태그 하나 — 값은 `GET /filters` 의 `tags[].name` 과 같다.
TagName = Annotated[str, Field(json_schema_extra={"enum": TAG_VALUES},
                               examples=["혼밥"])]


# ────────────────────────────────────────────────────────── 데이터
class Review(BaseModel):
    """리뷰 한 건. 카드의 `store.reviews` 안에 들어간다."""

    source_platform: str | None = Field(default=None,
        description='이 레코드를 수집한 플랫폼. kakao=카카오맵, naver=네이버지도, 출처 미확인은 null. 사진 CDN/원문 작성 플랫폼과는 별개',
        examples=['kakao'])

    id: int | None = Field(description="리뷰 id. 서버는 **정수 id 가 있는 리뷰만** 싣는다(앱이 id 로 리뷰를 찾는다). "
                                       "카카오는 카카오 리뷰 id(양수), 네이버는 네이버 리뷰 id 에서 만든 **음수** — "
                                       "부호로 출처가 갈려 겹치지 않는다. 가게 안에서 유일하고 요청마다 같다. "
                                       "**`10**12`(1,000,000,000,000) 이상은 우리 사용자 후기**다"
                                       "(`source_platform: \"matchuri\"`) — 설문에 쓴 세 줄이 여기에 섞여 들어온다",
                           examples=[1152443320])
    store_id: str = Field(description="카카오 장소 id", examples=["16421356"])
    content: str = Field(
        description="리뷰 본문. **줄바꿈이 들어 있습니다** — 그대로 뿌리면 한 줄로 뭉칩니다",
        examples=["국물이 진하고 양도 많아요\n점심에 웨이팅 조금 있습니다"])
    score: int | None = Field(
        description="별점 1~5. 별점 없는 리뷰가 있어 `null` 가능", examples=[5])
    images: list[str] = Field(description="리뷰 사진 URL. 수집 완료한 사진은 서버의 축소 progressive JPEG 절대 URL. 없으면 빈 배열 `[]`",
                              examples=[[]])
    reviewer_id: str | None = Field(description="작성자 id. 없으면 `null`",
                                    examples=["a1b2c3"])
    date: str | None = Field(description='작성 시각 `"2026-08-19 09:57:16"`',
                             examples=["2026-08-19 09:57:16"])
    tags: list[str] = Field(
        description="그 리뷰가 말한 태그 — **`GET /filters` 의 `tags[].name` 과 같은 이름**(가성비·친절·혼밥·데이트 …). "
                    "리뷰어가 고른 강점·키워드와 본문 표현에서 뽑는다. '맛' 은 싣지 않는다(거의 모든 리뷰가 받아 가르지 "
                    "못한다). 없으면 `[]`. 이 값을 싣지 않은 옛 데이터에서는 수집 원본의 강점(맛·친절·가성비·분위기·주차)",
        examples=[["가성비", "혼밥"]])
    like_count: int = Field(description="그 리뷰가 받은 좋아요 수", examples=[3])


class Break(BaseModel):
    """브레이크타임 한 구간.

    원본은 `"15:00 ~ 17:30 브레이크타임"` 처럼 **한 줄짜리 문자열**이다. 그것을 앱이
    직접 쪼개면 앱 두 개가 각자 다르게 틀리므로(`app/hours.py` 의 머리말과 같은 이유)
    서버가 쪼개서 내보낸다.
    """

    start: str = Field(description="쉬기 시작하는 시각 `HH:MM`", examples=["15:00"])
    end: str = Field(description="다시 여는 시각 `HH:MM`", examples=["17:30"])
    label: str | None = Field(
        description='원본에 붙어 있던 이름("브레이크타임" 등). 없으면 `null`',
        examples=["브레이크타임"])


class OpenHours(BaseModel):
    """요일 한 줄의 영업시간. **응답에는 월~일 7개가 항상 순서대로 들어간다.**

    원본은 수집 시점(2026-09-02) 그 주의 **날짜가 박힌** 라벨(`"수(9/2)"`)이라
    요일만 남기고 날짜는 떼어 낸다 — 날짜를 그대로 두면 다음 주에 거짓말이 된다.

    원본에 빠진 요일이 있어도(2,443곳 중 86곳) 서버가 `open`·`close` 가 `null` 인 줄로
    채워 7개를 맞춘다. 배열 길이가 가게마다 다르면 "일요일에 여나" 를 묻는 쪽이
    **없는 것과 쉬는 것을 구분하려고** 매번 요일을 뒤져야 한다.
    시간표가 아예 없는 가게(2,795곳 중 352곳)만 빈 배열 `[]` 이다.
    """

    day: str = Field(description="요일 (`월`~`일`)", examples=["수"])
    open: str | None = Field(
        description="여는 시각 `HH:MM`. **`null` 이면 그날 영업하지 않습니다** "
                    "— 대부분 정기휴무이지만 '정보 없음' 과 구분되지는 않습니다",
        examples=["11:30"])
    close: str | None = Field(description="닫는 시각 `HH:MM`. `open` 과 함께 `null` 입니다",
                              examples=["21:20"])
    overnight: bool = Field(
        default=False,
        description="닫는 시각이 **다음 날**인가. 전체 15,738건 중 2,567건(16.3%)이 "
                    "`11:00 ~ 02:00` 꼴입니다 — 역삼동이라 심야 영업이 그만큼 많습니다. "
                    "`true` 면 화면에 `익일 02:00` 으로 적어야 합니다",
        examples=[False])
    breaks: list[Break] = Field(
        description="브레이크타임 등 중간 휴식. 없으면 빈 배열 `[]`")

    @field_validator("breaks", mode="before")
    @classmethod
    def _split_breaks(cls, v: Any) -> Any:
        """`"15:00 ~ 17:30 브레이크타임"` 같은 원본 문자열도 받아서 쪼갠다.

        파싱을 **여기 한 곳**에 둔다. `app/loader.py`(원본 -> 모델)와
        `app/hours.py`('지금 영업 중인가' 계산)가 각자 쪼개면 두 곳이 어긋나고,
        그러면 화면의 배지와 시간표가 서로 다른 말을 한다.
        """
        if not isinstance(v, list):
            return v
        out: list[Any] = []
        for item in v:
            if not isinstance(item, str):
                out.append(item)
                continue
            m = _BREAK_TEXT.match(item)
            if m is None:
                continue          # 못 읽는 줄은 버린다 — 거짓 시각을 만들지 않는다
            out.append({"start": m.group(1), "end": m.group(2),
                        "label": m.group(3).strip() or None})
        return out


class OpenNow(BaseModel):
    """**요청한 순간** 기준의 영업 상태. 서버가 `open_hours` 로 계산한다.

    앱이 직접 계산하지 않는 이유: 자정을 넘기는 영업이 16.3%(`11:00 ~ 02:00`)이고,
    새벽에는 전날 영업분을 봐야 하며, 브레이크타임도 걸러야 한다. 앱 두 개가 각자
    짜면 각자 다르게 틀린다.

    **시각이 `until` 하나가 아니라 넷이다.** `until` 은 배지 한 줄("22:00 영업 종료")을
    그리기 위한 값이라 상태에 따라 가리키는 대상이 바뀐다 — 그것만 주면 "오늘 몇 시까지
    하나" 와 "브레이크 언제 걸리나" 를 동시에 보여 줄 수 없다. 그래서 `opens_at`·
    `closes_at`·`break_start`·`break_end` 를 각각 따로 내보내고, **해당 없는 것은
    `null`** 로 둔다. `until` 은 그중 하나를 골라 준 것이므로 남겨 둔다.

    네 시각은 모두 **같은 영업 구간 하나**를 설명한다 — 지금 열려 있으면 그 구간,
    닫혀 있으면 다음 구간이다. 그래서 `closed` 일 때도 "다음에 열면 몇 시까지 하고
    브레이크는 언제인지" 를 미리 알 수 있다.
    """

    state: Literal["open", "break", "closed", "unknown"] = Field(
        description="`open` 영업 중 · `break` 브레이크타임 · `closed` 영업 종료 · "
                    "`unknown` 시간표 정보 없음. **`unknown` 은 배지를 감추세요** — "
                    "352곳(13%)이 여기 해당하고, 정보가 없는 것은 닫힌 것과 다릅니다",
        examples=["open"])
    until: str | None = Field(
        description="**이 상태가 끝나는 시각**(ISO 8601). 배지 한 줄만 그릴 때 쓰는 값이라 "
                    "아래 네 시각 중 지금 화면에 맞는 것이 들어옵니다 — `open` 이면 "
                    "`closes_at`(브레이크가 먼저 오면 `break_start`), `break` 면 "
                    "`break_end`, `closed` 면 `opens_at`. `unknown` 이면 `null`. "
                    "`\"22:00\"` 이 아니라 날짜까지 주는 이유는 오늘인지 내일인지 "
                    "구분해야 하기 때문입니다",
        examples=["2026-09-14T22:00:00+09:00"])
    opens_at: str | None = Field(
        description="**다음에 문 여는 시각**(ISO 8601). `closed` 면 다음 영업 시작, "
                    "`break` 면 브레이크가 끝나는 시각입니다. "
                    "지금 영업 중(`open`)이면 이미 열려 있으므로 `null`",
        examples=[None])
    closes_at: str | None = Field(
        description="**영업이 실제로 끝나는 시각**(ISO 8601). 브레이크로 끊기는 것과 "
                    "무관한 '진짜 종료' 입니다 — `until` 과 달리 브레이크 시작을 "
                    "대신 넣지 않습니다. `closed` 면 *다음* 영업의 종료 시각이고, "
                    "`unknown` 이면 `null`",
        examples=["2026-09-14T22:00:00+09:00"])
    break_start: str | None = Field(
        description="**브레이크타임 시작**(ISO 8601). `break` 면 지금 그 브레이크, "
                    "그 밖에는 이번(또는 다음) 영업에 **아직 남아 있는** 브레이크입니다. "
                    "브레이크가 없거나 이미 지났으면 `null` — 전체 3,081건에 있습니다",
        examples=[None])
    break_end: str | None = Field(
        description="그 브레이크가 끝나는 시각(ISO 8601). `break_start` 와 함께 "
                    "`null` 입니다",
        examples=[None])
    day: str | None = Field(
        description="이 답을 **어느 요일 시간표로** 냈나(`월`~`일`). 새벽에는 전날 "
                    "영업분이 아직 안 끝났을 수 있어서 **어제 요일**이 나옵니다 "
                    "(토요일 01:00 에 `금`). `store.open_hours` 에서 강조할 줄을 "
                    "고를 때 쓰세요. `unknown` 이면 `null`",
        examples=["월"])


class Store(BaseModel):
    """가게 하나. 카드 한 장이 가게 한 곳이다."""

    source_platform: str | None = Field(default=None,
        description='이 레코드를 수집한 플랫폼. kakao=카카오맵, naver=네이버지도, 출처 미확인은 null. 사진 CDN/원문 작성 플랫폼과는 별개',
        examples=['kakao'])

    id: str = Field(description="카카오 장소 id", examples=["16421356"])
    name: str = Field(description="가게 이름", examples=["역삼동참치"])
    location: str = Field(description="주소(도로명)",
                          examples=["서울 강남구 테헤란로 123 2층 (역삼동)"])
    lat: float | None = Field(
        description="위도. 좌표를 모르는 가게는 `null` 입니다(2,795곳 전부에 있습니다). "
                    "**주소를 지오코딩하지 말고 이 값을 쓰세요** — 주소에는 `지하 1층`·"
                    "`302호` 같은 접미사가 붙어 있어 지오코더가 자주 실패합니다. "
                    "지도 앱 딥링크와 '도착했나' 판정에 쓰는 값입니다",
        examples=[37.5006])
    lon: float | None = Field(
        description="경도. `lat` 과 함께 `null` 입니다", examples=[127.0366])
    category: str = Field(
        description="카카오 세부 분류(cat3, 153종). 없으면 업종(cat2)으로 대체",
        examples=["일식집"])
    category_group: CatName = Field(
        description="필터의 업종 라벨 9개 중 하나. "
                    "`PUT /users/{user_id}/filter` 의 `cat` 에 넣는 값과 같다")
    score: float | None = Field(
        description="평균 별점. 리뷰가 없으면 `null`", examples=[4.6])
    tags: list[str] = Field(
        description="리뷰 태그 상위 5개. 없으면 빈 배열 `[]`",
        examples=[["맛", "친절", "분위기"]])
    review_count: int = Field(description="**이 서비스에서 볼 수 있는** 리뷰 수 — "
                                          "`GET /cards/{card_id}/reviews` 가 돌려주는 개수와 "
                                          "같습니다(수집 리뷰 + 우리 사용자 후기). 카카오에 "
                                          "달린 전체 리뷰 수가 아닙니다. 목록과 상세가 같은 값을 "
                                          "줍니다. `score` 는 수집 원본의 평균 그대로입니다",
                              examples=[20])
    reviews: list[Review] = Field(
        description="리뷰. 목록 API 는 미리보기 1건, 상세 API 는 기본 5건. "
                    "상세·`/cards/{card_id}/reviews` 에서는 **우리 사용자 후기가 맨 앞에** "
                    "옵니다(`source_platform: \"matchuri\"`, `id >= 10**12`) — "
                    "그래서 상세 응답의 길이가 `reviews` 파라미터보다 클 수 있습니다")
    summary: str | None = Field(
        description="카카오 AI 한 줄 요약. 없으면 `null`",
        examples=["차분한 우드톤 한정식 코스"])
    subway: str | None = Field(description="가까운 역과 도보 시간. 없으면 `null`",
                               examples=["역삼역 7번 출구 6분"])
    open_hours: list[OpenHours] = Field(
        description="요일별 영업시간. 있으면 **월~일 7개가 항상 순서대로** 들어가고, "
                    "정보가 없는 가게는 빈 배열 `[]` 입니다 "
                    "(전체 2,795곳 중 2,443곳에 있습니다). "
                    "**'지금 영업 중인가' 를 이걸로 계산하지 마세요** — 응답 최상단의 "
                    "`open_now` 가 이미 계산된 답입니다",
        examples=[[{"day": "수", "open": "11:30", "close": "21:20",
                    "overnight": False,
                    "breaks": [{"start": "15:00", "end": "17:30",
                                "label": "브레이크타임"}]}]])
    parking: str | None = Field(description="주차 안내 한 줄. 없으면 `null`",
                                examples=["주차 가능"])


class Food(BaseModel):
    """메뉴 하나. 카드 상세(`GET /cards/{card_id}`)에만 들어간다."""

    source_platform: str | None = Field(default=None,
        description='이 레코드를 수집한 플랫폼. kakao=카카오맵, naver=네이버지도, 출처 미확인은 null. 사진 CDN/원문 작성 플랫폼과는 별개',
        examples=['kakao'])

    id: int = Field(description="그 가게 안에서의 순번(0부터)", examples=[0])
    store_id: str = Field(description="카카오 장소 id", examples=["16421356"])
    name: str = Field(description="메뉴 이름", examples=["참치정식"])
    price: int | None = Field(
        description="가격(원). 표기가 없으면 `null` — **`-1` 이나 `0` 은 나오지 않습니다**",
        examples=[15000])
    price_text: str | None = Field(
        description='가격이 숫자가 아닐 때의 문구("변동가격" 등). 없으면 `null`',
        examples=[None])
    image: str | None = Field(
        description="메뉴 사진 URL. 기본은 최대320px progressive JPEG. 상세의 original_images=true는 수집 원본 URL. 사진이 없으면 null", examples=[None])
    description: str | None = Field(
        description='구성 설명("돈가스+미니 소바"). 없으면 `null`', examples=[None])
    is_recommend: bool = Field(description="가게가 추천으로 표시한 메뉴", examples=[True])


class Card(BaseModel):
    """카드 한 장 — 서버 내부에서 카탈로그가 들고 다니는 모양(`app/loader.py`).

    응답으로 나가는 것은 `CardOut`·`CardDetailOut` 이다(추천 이유·거리·메뉴가 더 붙는다).
    """

    card_id: int
    store: Store
    image: str | None
    images: list[str] = Field(default_factory=list)


# ────────────────────────────────────────────────────────── 요청 본문
class UserCreate(BaseModel):
    """`POST /users` 요청 본문. 전부 선택이다."""

    name: str | None = Field(None, description="관리자 화면에서 구분할 이름(50자 이하)",
                             examples=["김철수"])

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        """제어문자를 지우고 길이를 본다.

        이름은 관리자 화면과 로그에 그대로 찍힌다 — 줄바꿈이나 ESC 가 섞이면 로그 한 줄이
        여러 줄이 되고(로그 주입) 화면도 깨진다. 지운 뒤 비면 이름 없음(`null`)이다.
        """
        if v is None:
            return None
        v = "".join(ch for ch in v if ch.isprintable()).strip()
        if len(v) > NAME_MAX:
            raise ValueError(f"이름은 {NAME_MAX}자 이하여야 합니다")
        return v or None


class LocationIn(BaseModel):
    """`PUT /users/{user_id}/location` 요청 본문.

    범위를 검증한다 — 위도 91 이나 경도 500 이 그대로 들어오면 하버사인이 조용히 엉뚱한
    거리를 내놓는다. 벗어나면 422 다.

    `at` 은 **선택**입니다. 없으면 예전처럼 동작합니다.

    **서버는 이 값으로 방문을 판정하지 않습니다.** 도착 여부는 앱이 직접 계산해서
    설문에 실어 보냅니다(`SurveyIn` 의 `arrived`·`closest_m`·`dwell_min`) — 위치가
    기기를 떠나지 않는 쪽이 맞고, 서버가 궤적을 들고 있을 이유도 없습니다.
    """

    lat: float = Field(ge=-90.0, le=90.0, description="위도 (-90 ~ 90)",
                       examples=[37.5006])
    lon: float = Field(ge=-180.0, le=180.0, description="경도 (-180 ~ 180)",
                       examples=[127.0366])
    at: str | None = Field(
        default=None,
        description="이 위치를 **잰 시각**(ISO 8601). 보낸 시각이 아닙니다 — 앱이 지하에서 "
                    "못 보낸 것을 모아 올릴 수 있으므로 잰 시각이 필요합니다. 생략하면 "
                    "받은 시각을 씁니다. **더 오래된 시각은 현재 위치를 덮지 않습니다**. "
                    "못 읽는 값이면 **422** 입니다",
        examples=["2026-09-16T12:41:03+09:00"])

    @field_validator("at")
    @classmethod
    def _iso(cls, v: str | None) -> str | None:
        """ISO 8601 이 아니면 422.

        받아 넘기면 그 문자열이 `users.jsonl` 의 `location.at` 으로 그대로 저장되고,
        '더 오래된 위치는 덮지 않는다' 판단이 그 값과 비교를 해야 한다. 한 번 들어온
        쓰레기가 그 사용자의 위치 갱신을 영구히 막을 수 있다 — 입구에서 막는다.
        """
        if v is None:
            return None
        try:
            datetime.fromisoformat(v)
        except (TypeError, ValueError):
            raise ValueError("ISO 8601 시각이어야 합니다 (예: 2026-09-16T12:41:03+09:00)")
        return v


class FilterIn(BaseModel):
    """`PUT /users/{user_id}/filter` 요청 본문 — 저장할 필터.

    `cat`·`tag` 의 어휘 검증은 `app/filters.py` 의 `validate` 가 한다(422, 허용 목록을
    메시지에 적는다). 어휘는 한 곳에만 살아야 하므로 여기서 `Literal` 로 박지 않는다.
    """

    cat: list[CatName] = Field(
        default_factory=list,
        description="업종(`GET /filters` 의 9개). 여러 개면 **OR**. 빈 목록이면 조건 없음",
        examples=[["일식"]])
    tag: list[TagName] = Field(
        default_factory=list,
        description="태그(`GET /filters` 의 12개). 여러 개면 **OR**(하나라도 가진 곳). 빈 목록이면 조건 없음",
        examples=[["혼밥"]])
    within_m: int | None = Field(
        None, ge=1, description="반경 m. `null` 이면 거리 조건 없음", examples=[500])


class Log(BaseModel):
    """`POST /preferences` 요청 본문 — 카드 한 장에 대한 선호.

    `prefer` 가 두 값 중 하나가 아니면 **400** 이다(라우터가 낸다 — 예전부터의 계약이라
    `Literal` 로 바꿔 422 로 만들지 않았다). 허용 값은 스키마의 `enum` 에 있다.
    """

    user_id: str = Field(
        description="로그인이 없으니 클라이언트가 정한다(`POST /users` 로 발급 권장)",
        examples=["u_3f2a1b4c5d6e"])
    session_id: str | None = Field(None, min_length=1, max_length=64,
        description="생략하면 현재 세션. 이전 세션 ID로 보낸 늦은 피드백은409")
    card_id: int = Field(description="`GET /cards` 에서 받은 `card_id`", examples=[12])
    prefer: Annotated[str, Field(json_schema_extra={"enum": PREFER_VALUES})] = Field(
        description='오른쪽으로 넘겼으면 `"like"`, 왼쪽이면 `"dislike"`',
        examples=["like"])


# ────────────────────────────────────────────────────────── 응답
class StatusOut(BaseModel):
    """`GET /` — 서버가 살아 있는지, 데이터가 올라갔는지."""

    cards: int = Field(description="카드(가게) 수", examples=[2795])
    recommendable: int = Field(
        description="추천 후보 수 (사진과 리뷰가 있는 가게만 카드로 돌린다)",
        examples=[2216])
    logs: int = Field(description="지금까지 쌓인 스와이프 기록 수", examples=[1476])
    sessions: int = Field(description="학습 중인 사용자 수", examples=[32])
    policy: str = Field(description="실제로 도는 추천 정책", examples=["interleave"])
    configured_policy: str = Field(
        description="환경변수가 요구한 정책(산출물이 없으면 legacy 로 후퇴한다)",
        examples=["interleave"])


class LocationOut(BaseModel):
    """저장된 위치. `PUT /users/{user_id}/location` 이 정한다."""

    lat: float = Field(description="위도", examples=[37.5006])
    lon: float = Field(description="경도", examples=[127.0366])
    at: str = Field(description="저장 시각(ISO 8601)",
                    examples=["2026-09-10T14:24:53+09:00"])


class FilterOut(BaseModel):
    """저장된 필터. `PUT /users/{user_id}/filter` 가 정하고 `GET /cards` 가 적용한다."""

    cat: list[CatName] = Field(description="업종(OR). 빈 목록이면 조건 없음",
                               examples=[["일식"]])
    tag: list[TagName] = Field(description="태그(OR). 빈 목록이면 조건 없음",
                               examples=[["혼밥"]])
    within_m: int | None = Field(description="반경 m. 없으면 `null`", examples=[500])
    at: str = Field(description="저장 시각(ISO 8601)",
                    examples=["2026-09-10T14:24:53+09:00"])


class UserRecordOut(BaseModel):
    """사용자 레코드 — 발급·설정 저장 응답이 주는 모양.

    `location`·`filter` 가 그 사용자에게 지금 걸려 있는 **설정 전부**다.
    """

    user_id: str = Field(description="이 사용자의 id", examples=["u_3f2a1b4c5d6e"])
    name: str | None = Field(description="이름. 없으면 `null`", examples=["김철수"])
    created_at: str | None = Field(description="발급 시각(ISO 8601)",
                                   examples=["2026-09-10T14:24:53+09:00"])
    location: LocationOut | None = Field(
        description="저장된 위치. 없으면 `null`")
    filter: FilterOut | None = Field(
        description="저장된 필터. 없으면 `null`")


class UserOut(UserRecordOut):
    """`GET /users/{user_id}` — 설정 + 학습 현황."""

    registered: bool = Field(
        description="`POST /users` 로 발급된 id 인가(스와이프 기록만 있어도 조회는 된다)",
        examples=[True])
    swipes: int = Field(description="그 사용자의 누적 스와이프 수", examples=[42])
    likes: int = Field(description="그중 like 수", examples=[17])
    bookmarks: int = Field(description="찜해 둔 카드 수", examples=[3])
    learned: dict[str, Any] | None = Field(
        description="지금까지 학습된 취향(디버깅용). 세션이 없으면 `null`. "
                    "**모양이 바뀔 수 있으니 클라이언트는 의존하지 마세요**. "
                    "무거운 `prototypes`·`hist` 는 이 응답에 담지 않습니다 — "
                    "관리자 화면(`/admin`)에서만 받습니다")


class CountOut(BaseModel):
    """어휘 하나와 그 개수(추천 후보 풀 기준)."""

    name: str = Field(description="업종 또는 태그 이름", examples=["일식"])
    count: int = Field(description="후보 풀에서 이 값에 걸리는 가게 수", examples=[188])


class FiltersOut(BaseModel):
    """`GET /filters` — 필터에 넣을 수 있는 값 전부. 클라이언트가 필터 UI 를 이걸로 만든다."""

    cats: list[CountOut] = Field(
        description="업종 9개(많은 순 상위 8 + `기타`). 순서 그대로 그리면 된다")
    tags: list[CountOut] = Field(description="태그 12개. 뜻이 같은 원본 이름을 묶은 것")
    within_m: list[int] = Field(description="권장 반경(m). 다른 값도 저장할 수 있다",
                                examples=[[300, 500, 1000, 2000]])


class CardBaseOut(BaseModel):
    """카드 한 장의 공통 부분(`GET /preferences/{user_id}` 가 주는 모양)."""

    card_id: int = Field(description="카드 id. `POST /preferences` 에 그대로 보낸다",
                         examples=[1944])
    store: Store = Field(description="가게 정보(리뷰 미리보기 포함)")
    open_now: OpenNow = Field(
        description="**요청한 순간** 기준의 영업 상태. 응답마다 다시 계산됩니다 — "
                    "영업 배지는 이 값으로 그리세요. `store.open_hours` 는 주간 "
                    "시간표라 '지금' 을 답하지 않습니다")
    image: str | None = Field(
        description="대표 사진 1장. 기본은 최대640px progressive JPEG. 상세의 original_images=true는 수집 원본 URL. 없으면 `null`",
        examples=["http://api.example.com/media/catalog/v2/card/ab/abcdef.jpg"])
    images: list[str] = Field(
        description="카드에서 넘겨 볼 사진(최대 5장). 기본은 최대640px JPEG, 상세의 original_images=true는 수집 원본 URL. 없으면 빈 배열 `[]`")
    bookmarked: bool = Field(
        description="그 `user_id` 가 찜한 카드인가. `user_id` 를 안 주면 늘 `false`",
        examples=[False])


class AttributionOut(BaseModel):
    """이 카드가 뽑히기까지 **무엇이 가장 크게 작용했나** — 개인화인가, 평판인가, 거리·계절인가.

    카드 점수는 여러 항의 합이고 각 항은 `가중치 × 그 가게의 z` 다. `parts` 가 그 항들을
    묶어서 주고 `dominant` 가 절댓값이 가장 큰 항 하나다. 정책마다 항이 다르다:

        taste   parts.personal {cf, menu, text, review, food, neg}  스와이프에서 배운 것
                parts.reputation {pop, quality}                      가게 자체의 평판
                parts.context {distance, trend, tag_tier}             지금 상황(학습 안 됨)
                parts.explore  true 면 점수와 무관한 탐색 카드 — dominant 는 "explore"
                parts.cold     true 면 아직 like 가 없어 콜드스타트 공식으로 뽑힘
        legacy  parts.personal {category, price} · parts.reputation {quality, reviews} · context
        trend   parts.trend {season, fad, inflow, rating} · parts.menu [근거 메뉴 키워드] · context

    `recommendation_reason` 은 이 중 가장 큰 양의 기여 하나를 문장으로 바꾼 것이다.
    콜드스타트 구간(스와이프 20~30번 전)에서는 대부분 `dominant: "pop"` 이 나온다 — 그 구간의
    추천이 사실상 인기순이라는 뜻이고, 그게 사실이다.
    """

    policy: Literal["legacy", "taste", "trend"] = Field(description="이 카드를 고른 정책")
    dominant: str = Field(
        description="가장 크게 작용한 항. `parts` 안의 키 하나, 또는 `explore`·`cold`",
        examples=["menu"])
    parts: dict[str, Any] = Field(description="항별 기여. 위 도식 참조. 값은 점수 단위(z × 가중치)")


class CardOut(CardBaseOut):
    """`GET /cards` 의 카드 한 장."""

    recommendation_reason: str = Field(
        description="이 카드를 고른 이유 한 줄",
        examples=["지금까지의 취향과 잘 맞는 일식 가게예요."])
    attribution: AttributionOut | None = Field(
        description="이 카드가 뽑힌 이유의 **숫자**. `recommendation_reason` 이 문장이라면 이건 항별 기여다. "
                    "`user_id` 없이 부르면 `null`(인기순이라 설명할 것이 없다)")
    policy: Literal["legacy", "taste", "trend"] | None = Field(
        description="이 카드를 고른 추천 정책(팀-드래프트 인터리빙 실험용). "
                    "`trend` 는 트렌드 슬롯이 넣은 카드(기본 꺼짐). "
                    "`user_id` 없이 부르면 `null` — 클라이언트가 볼 필요는 없습니다")
    distance_m: int | None = Field(
        description="저장된 위치에서 그 가게까지의 거리(m). 위치를 모르면 `null`",
        examples=[344])


class CardDetailOut(CardBaseOut):
    """`GET /cards/{card_id}` — 목록의 카드에 메뉴와 사용자 후기가 붙는다."""

    menus: list[Food] = Field(
        description="그 가게의 메뉴 전부. 메뉴가 없는 가게는 빈 배열 `[]`")
    user_reviews: list[UserReviewOut] = Field(
        default_factory=list,
        description="**설문에서 나온 사용자 후기**(표현력 순, 같으면 최근 순). "
                    "아직 없으면 빈 배열 `[]`. 세 줄이 축별로 나뉜 **원본 모양**입니다. "
                    "같은 후기가 `store.reviews` **맨 앞에도** 카카오 리뷰와 같은 모양으로 "
                    "실려 나갑니다(`source_platform: \"matchuri\"`, `id >= 10**12`) — "
                    "둘 다 그리면 같은 글이 두 번 보이니 한쪽만 쓰세요. "
                    "`store.review_count` 에는 이 후기도 세어 들어가지만 `store.score` 는 "
                    "움직이지 않습니다")
    distance_m: int | None = Field(
        description="저장된 위치에서 그 가게까지의 거리(m). `user_id` 를 안 주면 `null`",
        examples=[344])


class HealthOut(BaseModel):
    """`GET /healthz` — 프로세스가 살아 있나. 의존성을 하나도 보지 않는다."""

    ok: bool = Field(description="항상 `true`(응답이 온다는 것 자체가 답이다)",
                     examples=[True])


class RestoreOut(BaseModel):
    """기동 복원의 진행 상황(`app/boot.py`). 배포 뒤 4~5분 동안 움직인다."""

    phase: Literal["pending", "restoring", "ready", "failed"] = Field(
        description="`restoring` 이면 아직 선호 기록을 되먹이는 중. `ready` 가 돼야 카드가 나간다",
        examples=["ready"])
    preferences_done: int = Field(description="되먹인 선호 기록 수", examples=[4812])
    preferences_total: int = Field(description="되먹일 선호 기록 수(분모)", examples=[4812])
    snapshot_rows: int | None = Field(
        default=None,
        description="취향 모델 스냅샷이 대신해 준 기록 행 수(선호+세션 이벤트). 그만큼은 "
                    "재생하지 않았다. null 이면 스냅샷 없이 전체를 재생했다(첫 부팅, 또는 "
                    "학습 코드·산출물이 바뀐 뒤 첫 부팅)",
        examples=[8700])
    seconds: float = Field(description="복원에 걸린(걸리고 있는) 시간(초)", examples=[251.4])
    started_at: str | None = Field(default=None, description="복원 시작 시각")
    finished_at: str | None = Field(default=None, description="복원 끝난 시각. 진행 중이면 null")
    error: str | None = Field(default=None, description="`failed` 일 때의 이유")


class ReadyOut(BaseModel):
    """`GET /readyz` — 지금 요청을 받아도 되나. 하나라도 아니면 503 이다."""

    ok: bool = Field(description="셋(데이터·후보 풀·상태 디렉터리)이 모두 준비됐나",
                     examples=[True])
    data: bool = Field(description="가게 데이터가 올라왔나", examples=[True])
    pool: bool = Field(description="추천 후보 풀이 만들어졌나", examples=[True])
    state_writable: bool = Field(
        description="`state/` 에 쓸 수 있나(읽기 전용 볼륨이면 `false`)", examples=[True])
    media_writable: bool = Field(
        description="설문 사진을 저장할 수 있나. **없어도 `ok` 는 `true`** 다 — "
                    "사진 업로드만 503 이 되고 카드·설문은 정상이다",
        examples=[True])
    artifacts: bool = Field(
        description="서빙 산출물이 있나. 없어도 서비스는 돌고 legacy 정책만 쓴다",
        examples=[True])
    policy: str = Field(description="지금 도는 추천 정책", examples=["interleave"])
    cards: int = Field(description="카드(가게) 수", examples=[2795])
    recommendable: int = Field(description="추천 후보 수", examples=[2216])
    restore: RestoreOut = Field(
        description="기동 복원 진행 상황. `phase` 가 `ready` 전이면 `ok` 는 `false` 다")
    reasons: list[str] = Field(
        description="준비되지 않은 이유. 준비됐으면 빈 배열 `[]`", examples=[[]])


class BookmarkOut(BaseModel):
    """`PUT`·`DELETE /users/{user_id}/bookmarks/{card_id}` — 바뀐 뒤의 상태."""

    card_id: int = Field(description="그 카드", examples=[1944])
    bookmarked: bool = Field(description="지금 찜돼 있나", examples=[True])
    at: str | None = Field(
        description="찜한 시각(ISO 8601). 이미 찜한 카드에 다시 `PUT` 하면 **처음 담은 시각** "
                    "그대로이고, 빠진 상태면 `null` 입니다",
        examples=["2026-09-10T18:20:31+09:00"])
    count: int = Field(description="그 사용자의 전체 찜 수", examples=[3])


class BookmarkCardOut(CardBaseOut):
    """`GET /users/{user_id}/bookmarks` 의 한 장 — 카드에 찜한 시각과 거리가 붙는다."""

    bookmarked_at: str = Field(description="찜한 시각(ISO 8601). 목록은 이 순서의 역순이다",
                              examples=["2026-09-10T18:20:31+09:00"])
    distance_m: int | None = Field(
        description="저장된 위치에서 그 가게까지의 거리(m). 위치를 모르면 `null`",
        examples=[344])


class DecisionCardOut(CardBaseOut):
    recommendation_reason: str = Field(description="자기 참조를 제외한 관측 근거 한 줄. manual은 미검증 적합 기준 통과를 주장하지 않으며 초기에는 기본 추천임을 안내")
    distance_m: int | None = Field(description="저장 위치에서 거리(m), 위치 없으면 null")
    liked: bool = Field(description="이미 좋아요한 가게인지 여부")


class DecisionOut(BaseModel):
    ready: bool = Field(description="요청한 n장(기본4)이 준비되었나. manual은 현재 최선의 후보 제공 여부이며 개인화 검증 통과가 아님. auto는 기존 세 조건 통과 여부")
    status: Literal["ready", "learning", "insufficient_candidates", "unavailable",
                    "location_required"] = Field(description="제안 가능 여부와 보류 이유")
    proposal_id: str | None = Field(description="같은 사용자·필터·후보 조합의 안정적 ID. 결과 POST와 중복 UI 방지용")
    message: str | None = Field(description="카드가 있을 때 선택 또는 추가 탐색을 안내하는 문구")
    cards: list[DecisionCardOut] = Field(description="준비되면 정확히 n장(기본4), 그 외 빈 배열")


class DecisionFeedbackIn(BaseModel):
    proposal_id: str = Field(pattern=r'^[a-f0-9]{20}$', description='decision 응답의 proposal_id')
    action: Literal['select', 'continue'] = Field(description='한 곳 선택 또는 선택 없이 스와이프 계속')
    card_id: int | None = Field(default=None, description='select일 때 선택한 카드. continue일 때 생략/null')

    @model_validator(mode='after')
    def validate_choice(self):
        if (self.action == 'select') != (self.card_id is not None):
            raise ValueError('card_id is required only for select')
        return self


class DecisionFeedbackOut(BaseModel):
    ok: bool = Field(description='결과 저장 완료. 같은 결과 재전송도 true')


class PreferenceOut(BaseModel):
    """`POST /preferences` — 기록됐다는 응답."""

    ok: bool = Field(description="항상 `true`(실패는 4xx 로 옵니다)", examples=[True])
    count: int = Field(description="서버가 들고 있는 전체 기록 수", examples=[1477])
    learned: dict[str, Any] = Field(
        description="반영 직후의 학습 상태(디버깅용). **모양이 바뀔 수 있습니다**. "
                    "무거운 `prototypes`·`hist` 는 이 응답에 담지 않습니다 — "
                    "관리자 화면(`/admin`)에서만 받습니다")
    decision: DecisionOut | None = Field(default=None,
        description="include_decision=true로 요청하면 학습 직후 선택 제안 여부와 n장(기본4)을 반환")
    decision_metrics: DecisionMetricsOut | None = Field(default=None,
        description="include_decision_metrics=true이면 이 피드백 직후 상태의 분석 수치. 만족 확률 아님")

# ── 길찾기 · 설문 ────────────────────────────────────────────────────
# 추천의 결과를 재는 자리다. `map_app`·`mode` 는 앱의 enum 을 그대로 받으므로 고정
# 상수이고(데이터에서 나온 어휘가 아니다) `Literal` 로 박아 422 를 만든다.
# 설문 답은 `visited=false` 여도 그대로 받는다 — 엣지에서 거절하기보다 집계에서
# 무시하는 쪽이 옛 응답·부분 응답을 다루기 쉽다(`app/state.py` 의 관용적 읽기와 같은 태도).


class DirectionIn(BaseModel):
    """`POST /users/{user_id}/directions` 요청 본문 — 길찾기를 눌렀다."""

    card_id: int = Field(description="길을 찾은 카드", examples=[1944])
    map_app: Literal["naver", "kakao", "google"] = Field(
        description="넘어간 지도 앱", examples=["kakao"])
    mode: Literal["walk", "transit", "car"] = Field(
        description="이동 수단", examples=["transit"])


class DirectionOut(BaseModel):
    """`POST /users/{user_id}/directions` — 기록됐다는 응답."""

    card_id: int = Field(description="그 카드", examples=[1944])
    at: str = Field(description="기록 시각(ISO 8601)",
                    examples=["2026-09-11T13:40:12+09:00"])
    survey_at: str = Field(
        description="설문을 **권하는 시점**(길찾기 + 3시간). 앱이 알림 예약이나 배너 노출 "
                    "판단에 씁니다. **서버가 이 시각으로 막지는 않습니다** — "
                    "`pending` 은 기본적으로 전부 돌려줍니다",
        examples=["2026-09-11T16:40:12+09:00"])
    count: int = Field(description="그 사용자가 길을 찾아 본 가게 수", examples=[7])


class PendingSurveyOut(BaseModel):
    """`GET /users/{user_id}/surveys/pending` 의 한 줄 — 배너 리스트에 그대로 그립니다."""

    card_id: int = Field(description="설문 대상 카드", examples=[1944])
    store_id: str | None = Field(
        description="카카오 장소 id. 데이터가 갱신되면 `card_id` 는 바뀌지만 이건 그대로입니다",
        examples=["16421356"])
    name: str = Field(description="가게 이름", examples=["역삼동참치"])
    category: str = Field(description="음식 종류", examples=["일식 > 초밥,롤"])
    image: str | None = Field(description="대표 사진 1장. 없으면 `null`", examples=[None])
    at: str = Field(description="길찾기를 누른 시각(ISO 8601). 목록은 **최근 순**입니다",
                    examples=["2026-09-11T13:40:12+09:00"])
    hours_ago: int = Field(description="길찾기로부터 지난 시간(시). 배너 문구에 쓰세요",
                           examples=[4])
    photo_ids: list[str] = Field(
        default_factory=list,
        description="이 카드에 **이미 올려 둔** 사진 id. 배너를 다시 열었을 때 올린 것을 "
                    "보여주고 몇 장 더 올릴 수 있는지 알려주는 데 씁니다. 주소는 "
                    "`/media/{앞 2자}/{id}.jpg` 입니다",
        examples=[[]])


class ReviewIn(BaseModel):
    """설문의 후기 — **세 줄**. 이 세 줄이 그대로 가게 리뷰 본문이 됩니다.

    한 칸짜리 자유 후기가 아니라 축을 나눠 받는 이유: 빈칸을 막는 '필수' 가 실제로
    의미를 가지려면 무엇을 쓸지가 정해져 있어야 합니다. "좋았어요" 한 줄은 다음 사람에게
    아무것도 알려 주지 않지만, 맛·가격·분위기가 각각 있으면 읽을 만한 리뷰가 됩니다.

    세 축을 고른 기준은 **사용자가 답할 수 있고 추천에 쓰이는 것**입니다. 위생·대기는
    한 번 방문으로 판단하기 어려워 뺐습니다.
    """

    taste: str = Field(
        max_length=200, description="맛에 대해 한 줄", examples=["고등어초회가 신선했어요"])
    price: str = Field(
        max_length=200, description="가격에 대해 한 줄", examples=["2인 4만원, 적당했어요"])
    mood: str = Field(
        max_length=200, description="분위기에 대해 한 줄",
        examples=["조용하고 좌석 간격이 넓어요"])

    @field_validator("taste", "price", "mood")
    @classmethod
    def _filled(cls, v: str) -> str:
        """앞뒤 공백을 자르고, 그러고도 비면 **422**.

        공백만 받아 주면 '필수' 가 이름뿐이 된다. 빈 줄이 가게 리뷰에 그대로 실린다.
        """
        v = v.strip()
        if not v:
            raise ValueError("빈칸으로 둘 수 없습니다")
        return v


class SurveyIn(BaseModel):
    """`POST /users/{user_id}/surveys` 요청 본문 — 배너에서 고른 가게 하나에 답합니다.

        {"card_id": 1944, "visited": true, "satisfaction": "good",
         "review": {"taste": "…", "price": "…", "mood": "…"}}

    **`visited=true` 면 `satisfaction` 과 `review` 가 둘 다 필수입니다.** 선택으로 두면
    대부분 비워서 오고, 그러면 추천이 맞았는지도 모르고 가게 리뷰도 안 쌓인다 —
    이 설문이 존재하는 이유가 그 둘이다. 하나라도 없으면 **422** 입니다.

    `visited=false` 면 `not_visited_reason` 만 채우고 나머지는 비웁니다.
    """

    card_id: int = Field(description="답하는 카드", examples=[1944])
    visited: bool = Field(description="실제로 방문했나", examples=[True])
    not_visited_reason: Literal["far", "no_time", "changed_mind", "went_elsewhere"] | None =         Field(default=None, description="`visited=false` 일 때의 이유. 이탈 원인 분석에 씁니다",
              examples=[None])
    satisfaction: Literal["bad", "soso", "good"] | None = Field(
        default=None,
        description="**추천이 취향에 맞았나** — 이 기능의 핵심 지표입니다. `visited=true` 면 "
                    "**필수**. 정책별로 집계하고, `satisfied_rate` 는 **`good` 만** 만족으로 "
                    "셉니다 — `soso` 는 화면에 😐 로 보이지만 만족에 넣지 않습니다"
                    "(넣으면 지표가 무뎌집니다)",
        examples=["good"])
    review: ReviewIn | None = Field(
        default=None,
        description="맛·가격·분위기 세 줄. `visited=true` 면 **필수**입니다. "
                    "**그대로 가게 리뷰가 됩니다** — `GET /cards/{card_id}` 의 "
                    "`user_reviews` 로 다른 사용자에게 보입니다. 사진은 여기가 아니라 "
                    "`POST /users/{user_id}/surveys/{card_id}/photos` 로 따로 올립니다")
    arrived: bool | None = Field(
        default=None,
        description="**앱이 계산한 도착 여부.** 앱이 가진 위치 기록과 `store.lat`·"
                    "`store.lon` 을 비교해서 정합니다 — 위치는 기기에 두고 결과만 "
                    "보내세요. 모르면 생략(`null`)하고, **모른다를 `false` 로 보내지 "
                    "마세요**(위치를 못 본 것과 안 간 것은 다릅니다). 서버는 이 값으로 "
                    "설문을 막지 않습니다",
        examples=[True])
    closest_m: int | None = Field(
        default=None, ge=0,
        description="그 가게에 **가장 가까웠던 거리**(m). 좌표가 아니라 스칼라이므로 "
                    "위치가 새지 않습니다. 이 값이 쌓여야 '도착 반경 얼마가 맞나' 를 "
                    "나중에 데이터로 볼 수 있습니다 — `arrived` 만 보내면 그 판단이 "
                    "앱에 굳어 버립니다",
        examples=[34])
    dwell_min: int | None = Field(
        default=None, ge=0,
        description="그 반경 안에 **머문 시간**(분). 앞을 지나간 것과 밥을 먹은 것을 "
                    "가릅니다. 위치를 한 번만 잡았으면 `0`, 아예 못 잡았으면 `null`",
        examples=[41])

    @model_validator(mode="after")
    def _visited_needs_both(self):
        if not self.visited:
            return self
        missing = [name for name, v in (("satisfaction", self.satisfaction),
                                        ("review", self.review)) if v is None]
        if missing:
            raise ValueError(
                "visited=true 이면 " + " 와 ".join(missing) + " 가(이) 필요합니다")
        return self


class UserReviewOut(BaseModel):
    """설문에서 나온 **사용자 후기** 한 건 (`GET /cards/{card_id}` 의 `user_reviews`).

    같은 후기가 `store.reviews` **맨 앞에도** 카카오 리뷰와 같은 모양으로 실려 나갑니다
    (2026-09-21 부터 — 앱이 이 배열을 안 그려서 화면에 한 건도 안 보였습니다). 둘 다
    그리면 같은 글이 두 번 보입니다. 여기는 **원본 모양**(세 줄이 축별로 나뉜 것)이고,
    관리자·마이페이지가 이 배열을 읽습니다.

    `store.review_count` 에는 세어 들어가지만 **`store.score` 에는 들어가지 않습니다** —
    그 값은 추천 점수의 평판 성분이 읽는 값이라, 후기 한두 건이 콜드스타트에 순위를
    흔듭니다.
    """

    name: str | None = Field(
        description="작성자 이름. 이름 없이 발급받은 사용자는 `null` 입니다. "
                    "**`user_id` 는 주지 않습니다** — 그 값이 곧 그 사람의 열쇠입니다",
        examples=["민수"])
    satisfaction: Literal["bad", "soso", "good"] | None = Field(
        description="그 사람이 이 추천에 준 평가", examples=["good"])
    taste: str = Field(description="맛 한 줄", examples=["고등어초회가 신선했어요"])
    price: str = Field(description="가격 한 줄", examples=["2인 4만원, 적당했어요"])
    mood: str = Field(description="분위기 한 줄", examples=["조용하고 좌석 간격이 넓어요"])
    images: list[str] = Field(
        description="설문에 올린 사진 주소. 없으면 `[]`", examples=[[]])
    at: str = Field(description="답한 시각(ISO 8601)", examples=["2026-09-16T18:20:11+09:00"])


class MySurveyAnswerOut(BaseModel):
    """내가 그 가게에 답한 설문 한 건 (`GET /users/{id}/surveys` 의 `survey`)."""

    visited: bool = Field(description="갔다고 답했나", examples=[True])
    satisfaction: Literal["bad", "soso", "good"] | None = Field(
        description="추천에 준 평가. `visited=false` 면 `null`", examples=["good"])
    review: ReviewIn | None = Field(
        description="내가 쓴 세 줄. 안 갔거나 안 썼으면 `null`")
    not_visited_reason: Literal["far", "no_time", "changed_mind",
                                "went_elsewhere"] | None = Field(
        description="`visited=false` 일 때 고른 이유. 그 밖에는 `null`",
        examples=[None])
    images: list[str] = Field(
        description="설문에 올린 사진 주소. 없으면 `[]`. 지우면 여기서도 사라집니다",
        examples=[[]])
    at: str = Field(description="답한 시각(ISO 8601). 목록은 이 순서의 역순입니다",
                    examples=["2026-09-16T18:20:11+09:00"])


class MySurveyCardOut(CardBaseOut):
    """`GET /users/{user_id}/surveys` 의 한 건 — 가게 카드에 내 설문 답이 붙는다.

    **가게 화면의 `user_reviews` 와 다른 것입니다.** 저쪽은 남에게 보이는 글이라
    방문·세 줄·빈칸을 모두 거르지만, 이쪽은 내 기록이라 답한 대로 다 나옵니다
    (안 갔다고 답한 것도 포함).

    답을 **`survey` 안에 넣는 이유**는 이름이 겹치기 때문입니다. 카드의 `images` 는
    가게 사진이고 `survey.images` 는 내가 올린 사진입니다. 평평하게 펴면 한쪽이 다른
    쪽을 덮어써서 화면이 조용히 틀립니다.

    **방문 근거(`arrived`·`closest_m`·`dwell_min`)는 나오지 않습니다.** 앱이 위치로
    계산해 보낸 값이라 이탈 분석에만 쓰고 사용자에게 되돌려 주지 않습니다.
    """

    survey: MySurveyAnswerOut = Field(description="그 가게에 내가 답한 설문")


class SurveyPhotoOut(BaseModel):
    """설문에 붙인 사진 한 장."""

    photo_id: str = Field(description="사진 id(32자). 지울 때 씁니다",
                          examples=["9f2c1ab74e0d4c1fb3a8d5e6072c8a11"])
    url: str = Field(
        description="사진 주소. **이 값을 그대로 쓰세요** — 경로 규칙은 바뀔 수 있습니다. "
                    "서버 기준 경로라 앞에 API 호스트를 붙이면 됩니다",
        examples=["/media/9f/9f2c1ab74e0d4c1fb3a8d5e6072c8a11.jpg"])
    width: int = Field(description="저장된 가로(px)", examples=[1600])
    height: int = Field(description="저장된 세로(px)", examples=[1200])
    bytes: int = Field(description="저장된 파일 크기", examples=[284915])


class SurveyOut(BaseModel):
    """`POST /users/{user_id}/surveys` — 기록됐다는 응답."""

    ok: bool = Field(description="항상 `true`(실패는 4xx 로 옵니다)", examples=[True])
    card_id: int = Field(description="그 카드", examples=[1944])
    recorded: bool = Field(
        description="이번에 **새로 기록됐나**. 같은 길찾기에 두 번 답하면 `false` 입니다",
        examples=[True])
    remaining: int = Field(description="아직 답하지 않은 후보 수", examples=[2])
    published: bool = Field(
        description="이 후기가 가게 화면(`GET /cards/{card_id}` 의 `user_reviews`)에 "
                    "실렸나. `visited=false`, 세 줄 중 빈 줄, 또는 `recorded=false`"
                    "(중복 응답)면 `false` 입니다",
        examples=[True])
    review_levels: dict[str, int] | None = Field(
        default=None,
        description="세 줄 각각의 표현력 레벨 0~3(`taste`·`price`·`mood`). "
                    "0 언급 없음 · 1 평가만('맛있어요') · 2 속성 명시('라떼가 부드러워요') · "
                    "3 구체 묘사. **순서에만 쓰이고 실리는 자격과는 무관합니다** — "
                    "앱이 '조금 더 구체적으로 써 주세요' 안내에 쓸 수 있습니다. "
                    "`review` 가 없으면 `null`",
        examples=[{"taste": 1, "price": 2, "mood": 1}])
