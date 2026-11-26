# -*- coding: utf-8 -*-
"""네이버 데이터랩 검색어 트렌드로 **업종** 트렌드를 만들어 서빙 파일로 굽는다.

산출물은 `recsys/trend.py` 의 `category` 항이 읽는
`data/analysis/trend_category.json` — `{"scores": {"<cat>": <로그 증가율>}}`.
**커밋한다.** `data/kakao/` 는 gitignore 이고 서버에서는 배치가 갈아 끼우는 폴더라
거기 두면 배포가 나르지 않는다(`recsys/trend.py: CATEGORY_FILE` 주석).
서버는 이 API 를 런타임에 부르지 않는다(TREND.md 5절). 여기가 그 오프라인 경계다.

## 왜 요청당 키워드 그룹을 **하나만** 넣나

데이터랩의 `ratio` 는 **한 응답 안에서** 최댓값이 100 이 되도록 정규화된다.
한 요청에 여러 업종을 넣으면 '한식' 이 100 을 먹고 '퓨전요리' 는 0.3 같은 값으로
눌려 해상도를 잃는다. 그룹을 하나만 넣으면 그 키워드가 스스로 100 을 차지해
전 구간 정밀도를 다 쓴다. 대상 업종 71개 = 71콜, 한도(아래)에 견주면 무시할 양이다.

## 앵커 키워드를 쓰지 않는 이유

TREND.md 5절은 호출 간 비교를 위해 고정 앵커로 재스케일해야 한다고 적었다.
그건 **수준**(누가 더 많이 검색되나)을 비교할 때의 얘기다. 여기서 쓰는 값은
한 키워드의 **자기 대비 증가율** `log(최근/직전)` 이고, 정규화 상수는 분자·분모에
똑같이 들어가 **로그를 취하기 전에 약분된다.** 앵커가 필요 없고, 5칸 중 1칸도
안 먹는다.

## 스케일

`trend.py` 의 `_inflow` 와 같은 `log(최근/직전)` 이다. `SMOOTH` 상수는 옮기지
않았다 — 그건 리뷰 **건수**의 0 분모를 막는 값이고, 여기 `ratio` 는 0~100 연속값이라
의미가 다르다. 대신 한쪽 창이 0 이면 그 업종을 **버린다**(없으면 그 항은 0 = 모른다).
어차피 downstream 에서 후보 집합 안 z 를 내므로 절대 눈금은 씻긴다.

## 엔드포인트 — NAVER API HUB (2026-06-25 이관)

네이버가 오픈API 를 네이버 클라우드 플랫폼의 **NAVER API HUB** 로 옮겼다. 일정은

    2026-06-25  HUB 출시
    2026-07-31  개발자센터(developers.naver.com) **신규 등록 차단**
    2027-06-30  개발자센터 호스트 종료

그래서 지금 새로 받는 키는 **NCP 콘솔**에서 받아야 하고, 호스트와 헤더가 둘 다 다르다.

              | 개발자센터(레거시)          | API HUB
    호스트    | openapi.naver.com          | naverapihub.apigw.ntruss.com
    경로      | /v1/datalab/search         | /search-trend/v1/search
    ID 헤더   | X-Naver-Client-Id          | X-NCP-APIGW-API-KEY-ID
    비밀 헤더 | X-Naver-Client-Secret      | X-NCP-APIGW-API-KEY

요청 바디와 응답 형식은 **그대로**다(`keywordGroups` / `results[].data[].ratio`).
개발자센터에서 이미 데이터랩 권한을 받아둔 옛 키가 있으면 `--legacy` 로 쓸 수
있다 — 2027-06-30 까지만이다.

## 한도

HUB 는 **한시적 무료**이고(유료 전환 시 사전 공지), 검색어 트렌드는 **월 50,000건**,
API Key 당 **50 RPS** 다. 한 번 굽는 데 71콜이니 월 700회 돌려도 남는다.
한도의 50·80·100% 에서 메일이 오고 100% 에서 자동 차단된다.

사용:
    python analysis/fetch_trend_category.py            # 캐시 있으면 재사용
    python analysis/fetch_trend_category.py --refresh  # 전부 다시 받는다
    python analysis/fetch_trend_category.py --limit 3  # 키 확인용, 3개만
    python analysis/fetch_trend_category.py --legacy   # 2026-07-31 이전 발급 키
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # BE/
REPO = ROOT.parent
STORES = ROOT / "data" / "kakao" / "stores.jsonl"
CACHE = ROOT / "data" / "analysis" / "datalab"
OUT = ROOT / "data" / "analysis" / "trend_category.json"    # 커밋된다 (trend.py 참조)

#: 기본 경로. 2026-07-31 이후 발급분은 여기로만 된다.
HUB_API = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
HUB_HEADERS = ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")

#: 2026-07-31 이전에 데이터랩 권한을 받아둔 개발자센터 키용. 2027-06-30 종료.
LEGACY_API = "https://openapi.naver.com/v1/datalab/search"
LEGACY_HEADERS = ("X-Naver-Client-Id", "X-Naver-Client-Secret")

#: `trend.py` 의 WINDOW_DAYS 와 같아야 한다. 최근 W일 대 **직전 W일**.
WINDOW_DAYS = 180

#: 데이터랩은 전날까지 집계된다. 오늘을 끝으로 잡으면 마지막 칸이 비거나 반쪽이다.
LAG_DAYS = 1

#: 전년동기 비교용. 365 로 고정한다(윤년 하루 어긋나는 건 180일 평균에 묻힌다).
YEAR = 365

#: 점수에 필요한 최소 구간. 전년동기 창까지 덮어야 하므로 `YEAR + WINDOW_DAYS` 다.
#: 직전기간(seq) 창은 이 안에 이미 들어 있어 추가 호출이 없다.
SCORE_BACK_DAYS = YEAR + WINDOW_DAYS

#: 차트에 필요한 구간. 관리자 화면(`/admin/trends`)이 역삼동 추이와 **같은 월 축**에
#: 나란히 그리는데 그 축이 `recsys/trend_history.MONTHS` = 25달이다. 점수용 545일만
#: 받으면 왼쪽 7달이 빈 채로 그려진다. 한 업종당 호출 수는 그대로 1이고 응답만 길어진다.
CHART_MONTHS = 26
CHART_BACK_DAYS = CHART_MONTHS * 31

#: 실제로 받는 구간은 둘 중 긴 쪽이다.
FETCH_BACK_DAYS = max(SCORE_BACK_DAYS, CHART_BACK_DAYS)

#: 업종을 쓸 최소 가게 수. 한두 곳뿐인 업종은 트렌드를 태워도 순서에 영향이 없고
#: 콜만 먹는다. 빠진 업종은 파일에 없고, 없으면 `_category` 가 None 을 준다(= 0).
MIN_STORES = 3

#: 호출 간 간격(초). HUB 는 50 RPS 를 허용하지만 굳이 몰아칠 이유가 없다. 71콜을
#: 0.3초 간격으로 돌리면 전체 20여 초다.
SLEEP = 0.3


# ─────────────────────────────────────────────── 수준(level) — `--level` 에서만
#
# **위의 "앵커를 쓰지 않는 이유" 와 모순이 아니다.** 그 절은 *증가율*의 얘기다.
# 증가율은 `log(최근/직전)` 이라 응답별 정규화 상수가 약분돼 앵커가 필요 없다.
#
# 여기는 **수준**이다 — "어느 업종이 더 많이 검색되나". 그건 지금 캐시로 **불가능**
# 하다. 실측하면 71개 파일이 **전부 최댓값 정확히 100.0** 이다. 업종마다 따로 요청해
# 네이버가 응답 안에서 최댓값을 100 으로 맞추기 때문이고, 그래서 `국수` 의 50 과
# `국밥` 의 50 은 다른 절대량이다.
#
# 관리자 화면의 '스테디셀러'(= 변동성과 무관하게 **절대값이 월등한 것**)는 정의상
# 이 비교를 해야 한다. 그래서 요청마다 **같은 앵커 키워드를 한 칸 더** 넣어 두 그룹이
# 같은 정규화 상수를 공유하게 만들고 `업종 중앙값 / 앵커 중앙값` 으로 잰다. 상수가
# 약분되므로 이 값은 호출 간 비교가 된다.
#
# 앵커는 **중간 크기**여야 한다. `맛집` 처럼 큰 것을 넣으면 그것이 100 을 먹고 업종이
# 0 쪽으로 눌린다(위 머리말 ②). 다만 응답 `ratio` 가 소수 5자리라 100배 눌려도
# 유효숫자 3자리는 남으므로, 치명적인 건 앵커가 **너무 작아** 분모가 되는 경우다.
# `--anchor` 로 바꿔 끼울 수 있게 두고, 실제로 쓰는 값은 `--limit` 로 확인했다.
ANCHOR = "칼국수"

#: 수준 모드의 캐시. **기존 캐시를 건드리지 않는다** — 그쪽은 `trend.py` 가 먹는
#: 점수의 원자료이고, 응답 모양(그룹 1개)도 다르다.
LEVEL_CACHE = ROOT / "data" / "analysis" / "datalab_level"

#: 수준 산출물. 서빙이 아니라 **관측**이라 `data/analysis` 에 둔다(`OUT` 은 serving).
LEVEL_OUT = ROOT / "data" / "analysis" / "trend_level.json"


# ────────────────────────────────────────────────────────── 키
#: 경로마다 키가 다르다. HUB 키(NCP 발급)와 개발자센터 옛 키는 서로 안 통하므로
#: `.env` 에 둘 다 두고 쓸 수 있게 이름을 나눈다. HUB 이름이 없으면 옛 이름으로 떨어진다.
HUB_ENV = ("NAVER_CLIENT_PLATFORM_ID", "NAVER_CLIENT_PLATFORM_SECRET")
LEGACY_ENV = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")


def load_keys(legacy: bool = False) -> tuple[str, str]:
    """`.env`(저장소 루트) 또는 환경변수. 없으면 어디서 받는지 알려주고 죽는다."""
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    names = LEGACY_ENV if legacy else HUB_ENV
    cid = os.environ.get(names[0], "")
    sec = os.environ.get(names[1], "")
    if not legacy and not (cid and sec):          # HUB 이름이 없으면 옛 이름으로
        names = LEGACY_ENV
        cid = os.environ.get(names[0], "")
        sec = os.environ.get(names[1], "")
    if not cid or not sec:
        sys.exit(f"{names[0]} / {names[1]} 이 없다.\n"
                 "  NCP 콘솔 > All Services > Application Services > NAVER API HUB\n"
                 "  > Application > 인증 정보 에서 받아\n"
                 f"  {env} 에 넣는다 ({REPO / '.env.example'} 참고).")
    return cid, sec


# ────────────────────────────────────────────────────────── 업종 목록
def categories() -> list[str]:
    """`stores.jsonl` 의 cat2·cat3. `trend.py` 가 cat3 -> cat2 순으로 찾으므로 둘 다 받는다."""
    n: Counter[str] = Counter()
    with STORES.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            for key in ("cat2", "cat3"):
                v = (d.get(key) or "").strip()
                if v:
                    n[v] += 1
    return sorted(c for c, k in n.items() if k >= MIN_STORES)


#: 업종명을 그대로 검색어로 쓰면 안 되는 것들. 동음이의어가 음식 검색량을 덮는다.
#:
#: `호프` — 맥줏집이 아니라 사람·작품 이름으로 더 많이 검색된다. 갈라서 재보니
#:   `호프` 는 2025-06 평균 0.07 → 2026-07 최대 100.0 으로 1,400배 튀는데
#:   `요리주점` 은 32.45 → 22.74 로 멀쩡했다. 합쳐 놓으면 그 스파이크가 업종
#:   점수를 +4.25 로 만든다(다음으로 큰 값의 2.5배). `요리주점` 만 쓴다.
KEYWORD_OVERRIDE = {"호프,요리주점": ["요리주점"]}


def keywords_of(cat: str) -> list[str]:
    """`"돼지고기,갈비"` -> `["돼지고기", "갈비"]`.

    한 그룹 안의 키워드들은 데이터랩이 **검색량을 합쳐** 준다. 카카오 카테고리의
    쉼표는 원래 '이것 또는 저것' 이라 합치는 게 맞다. 예외는 `KEYWORD_OVERRIDE`.
    """
    if cat in KEYWORD_OVERRIDE:
        return list(KEYWORD_OVERRIDE[cat])
    return [w.strip() for w in cat.split(",") if w.strip()]


# ────────────────────────────────────────────────────────── 호출
def fetch(cat: str, cid: str, sec: str, start: str, end: str, legacy: bool,
          anchor: str = "") -> dict:
    """한 업종의 일별 시계열. `anchor` 를 주면 **그룹을 하나 더** 넣는다.

    앵커를 같은 요청에 넣는 이유는 `ANCHOR` 주석에 있다 — 두 그룹이 같은 정규화
    상수를 공유해야 호출 간 수준 비교가 되기 때문이고, 증가율에는 필요 없다.
    """
    api = LEGACY_API if legacy else HUB_API
    h_id, h_sec = LEGACY_HEADERS if legacy else HUB_HEADERS
    groups = [{"groupName": cat, "keywords": keywords_of(cat)}]
    if anchor and anchor != cat:
        groups.append({"groupName": anchor, "keywords": keywords_of(anchor)})
    body = json.dumps({
        "startDate": start, "endDate": end, "timeUnit": "date",
        "keywordGroups": groups,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(api, data=body, method="POST", headers={
        h_id: cid, h_sec: sec,
        "Content-Type": "application/json; charset=utf-8",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:200]
            if e.code in (401, 403):
                where = ("개발자센터(developers.naver.com) 앱의 '사용 API'"
                         if legacy else
                         "NCP 콘솔 NAVER API HUB > Application 의 API 선택")
                sys.exit(f"인증 거부({e.code}): {msg}\n"
                         f"  {where} 에 검색어 트렌드가 들어 있는지 확인한다.\n"
                         "  'Scopes are Empty' 면 키는 맞고 권한만 없는 것이다.\n"
                         "  개발자센터는 2026-07-31 부로 신규 등록이 막혔다 — 옛 키가\n"
                         "  아니라면 HUB(기본 경로)로 새로 발급받아야 한다.")
            if e.code == 429 and attempt < 2:
                time.sleep(2 ** attempt * 2)
                continue
            raise SystemExit(f"{cat}: HTTP {e.code} {msg}")
    raise SystemExit(f"{cat}: 429 가 계속된다 — 월 한도(50,000건)를 넘었을 수 있다")


def cached(cat: str, refresh: bool, get, where: Path = CACHE) -> dict:
    where.mkdir(parents=True, exist_ok=True)
    path = where / (re.sub(r"[^\w가-힣]+", "_", cat) + ".json")
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    raw = get()
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    time.sleep(SLEEP)
    return raw


# ────────────────────────────────────────────────────────── 점수
def _level(data: list[dict], lo: str, hi: str) -> float | None:
    """창의 대표값. **평균이 아니라 중앙값**이다.

    검색량은 하루짜리 이벤트로 수백 배 튄다. 평균이면 그 며칠이 180일 창을 끌고
    간다 — 71개 업종에서 재보니 평균은 표준편차 0.606·최대 |4.25| 인데 중앙값은
    0.494·|3.09| 였고, 둘의 상관은 +0.973 이라 **순위는 그대로고 꼬리만 눕는다.**
    """
    vals = [d["ratio"] for d in data if lo <= d["period"] <= hi]
    return statistics.median(vals) if vals else None


def growth(raw: dict, recent: tuple[str, str], prior: tuple[str, str]) -> float | None:
    """`log(최근 창 중앙값 / 비교 창 중앙값)`. 한쪽이라도 비거나 0 이면 None."""
    try:
        data = raw["results"][0]["data"]
    except (KeyError, IndexError):
        return None
    a = _level(data, *recent)
    b = _level(data, *prior)
    if not a or not b or a <= 0 or b <= 0:
        return None
    return math.log(a / b)


# ────────────────────────────────────────────────────────── 계절 선행
#: 계절 선행 창(일). 4주 — 추천이 "다음 몇 주" 를 보면 되는 시간이고, 더 길면 다음 철과 섞인다.
SEASON_DAYS = 28
#: 작년 정렬. 365 가 아니라 **364일(52주)** — 검색량은 요일 주기가 커서(주말에 뛰는 메뉴,
#: 평일 점심에 뛰는 업종) 요일을 맞춰야 28일 중앙값이 흔들리지 않는다. `yoy` 의 365 와
#: 어긋나지만 그쪽은 180일 창이라 하루가 묻히고, 여기는 28일 창이다.
YEAR_ALIGNED = 364


def seasonal(data: list[dict], end: dt.date) -> tuple[float | None, float | None]:
    """(`season`, `in_season`) — **작년 같은 시점에서 본 다음 4주.**

        season    = log(작년 다음 4주 중앙값 / 작년 지난 4주 중앙값)   이제 오르는 철인가(방향)
        in_season = log(작년 다음 4주 중앙값 / 작년 1년 중앙값)        지금이 그것의 철인가(수준)

    `yoy` 는 계절을 **지운** 값이다 — 냉면이 7월에 오르는 건 작년 7월에도 올랐으니 0 근처다.
    계절은 해마다 반복된다는 근거가 있고(`TREND-EVIDENCE.md` 2.3 — 두 해 모양 상관 0.7 이상이
    67개 중 36개, 정점 달 일치가 우연의 2.5배), 반복되면 **작년 이 시점의 다음 4주**가 올해
    다음 4주의 예측값이다. 계절 나이브(seasonal naive)다 — 시계열 수십~수천 개 규모에서는
    이게 기준선이고, 더 복잡한 모델은 이걸 홀드아웃에서 이길 때만 들인다.

    한쪽이 비거나 0 이면 None. 중앙값을 쓰는 이유는 `_level` 과 같다(하루 스파이크).
    """
    ly = end - dt.timedelta(days=YEAR_ALIGNED)
    nxt = _level(data, (ly + dt.timedelta(days=1)).isoformat(),
                 (ly + dt.timedelta(days=SEASON_DAYS)).isoformat())
    now = _level(data, (ly - dt.timedelta(days=SEASON_DAYS - 1)).isoformat(), ly.isoformat())
    year = _level(data, (ly - dt.timedelta(days=YEAR_ALIGNED - 1)).isoformat(), ly.isoformat())
    season = math.log(nxt / now) if nxt and now and nxt > 0 and now > 0 else None
    level = math.log(nxt / year) if nxt and year and nxt > 0 and year > 0 else None
    return season, level


# ────────────────────────────────────────────────────────── 수준
def _group(raw: dict, name: str) -> list[dict]:
    """응답에서 이름이 맞는 그룹의 일별 배열. 순서를 믿지 않고 `title` 로 찾는다."""
    for res in raw.get("results") or []:
        if (res.get("title") or "") == name:
            return res.get("data") or []
    return []


def level_of(raw: dict, cat: str, anchor: str) -> tuple[float | None, float, float]:
    """`업종 중앙값 / 앵커 중앙값`. 앵커 자신이면 1.0 이다.

    중앙값을 쓰는 이유는 `_level` 과 같다 — 하루짜리 스파이크가 대표값을 끌고 가면
    안 된다. 구간은 **받은 전체**다: 수준을 재는 것이므로 계절이 한 바퀴 이상 들어가
    서로 상쇄돼야 한다.
    """
    mine = [d["ratio"] for d in _group(raw, cat)]
    if not mine:
        return None, 0.0, 0.0
    me = statistics.median(mine)
    if cat == anchor:
        return 1.0, me, me
    base = [d["ratio"] for d in _group(raw, anchor)]
    if not base:
        return None, me, 0.0
    an = statistics.median(base)
    if an <= 0:
        return None, me, an
    return me / an, me, an


def level_main(args, cid: str, sec: str, start: str, end: str) -> None:
    """`--level` — 업종 사이 **비교 가능한** 검색 수준을 굽는다.

    산출물 `trend_level.json` 은 관리자 화면(`/admin/trends`)의 스테디셀러 판정에
    쓰인다. `trend.py` 가 먹는 `trend_category.json` 은 **건드리지 않는다** — 추천
    점수에 수준이 들어가면 큰 업종이 영구히 유리해진다(그건 트렌드가 아니다).
    """
    anchor = args.anchor
    cats = categories()
    if args.limit:
        cats = cats[:args.limit]
    print(f"수준 모드 — 업종 {len(cats)}개 · 앵커 `{anchor}` · {start} ~ {end}")
    print("업종마다 앵커를 한 칸 더 넣어 같은 정규화 상수를 공유시킨다.\n")

    levels: dict[str, float] = {}
    raws: dict[str, float] = {}
    squashed: list[str] = []
    for i, cat in enumerate(cats, 1):
        raw = cached(cat, args.refresh,
                     lambda c=cat: fetch(c, cid, sec, start, end, args.legacy, anchor),
                     LEVEL_CACHE)
        lv, me, an = level_of(raw, cat, anchor)
        if lv is None:
            print(f"  [{i}/{len(cats)}] {cat:20s} —  (앵커 또는 업종이 비었다)")
            continue
        levels[cat] = round(lv, 5)
        raws[cat] = round(me, 4)
        # 정밀도 경고: 한쪽이 0.01 밑으로 눌리면 유효숫자가 3자리 아래로 내려간다.
        if min(me, an) < 0.01:
            squashed.append(cat)
        print(f"  [{i}/{len(cats)}] {cat:20s} 수준 {lv:>9.4f}"
              f"   (업종 {me:>7.3f} / 앵커 {an:>7.3f})")

    LEVEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    LEVEL_OUT.write_text(json.dumps({
        "levels": levels,
        "anchor": anchor,
        "anchor_keywords": keywords_of(anchor),
        "as_of": end,
        "range": [start, end],
        "note": "업종 중앙값 / 앵커 중앙값. 같은 요청에 앵커를 넣어 응답별 정규화 "
                "상수를 약분시킨 값이므로 **업종 사이 비교가 된다**. "
                "trend_category.json 의 증가율과는 다른 축이다.",
        "source": "naver_api_hub_search_trend",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    top = sorted(levels.items(), key=lambda kv: -kv[1])
    print(f"\n{LEVEL_OUT} — {len(levels)}개")
    if top:
        print("  큰 쪽:", ", ".join(f"{k} {v:.2f}" for k, v in top[:6]))
        print("  작은 쪽:", ", ".join(f"{k} {v:.3f}" for k, v in top[-6:]))
    if squashed:
        print(f"  ⚠️ 정밀도 주의({len(squashed)}개): {', '.join(squashed[:8])}")
        print("     앵커가 너무 크다는 뜻이다 — `--anchor` 를 더 작은 말로 바꿔 본다.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받는다")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만 (키 확인용)")
    ap.add_argument("--legacy", action="store_true",
                    help="2026-07-31 이전 발급 개발자센터 키 (2027-06-30 종료)")
    ap.add_argument("--basis", choices=("yoy", "seq"), default="yoy",
                    help="비교 창: yoy=전년동기(기본), seq=직전기간")
    ap.add_argument("--level", action="store_true",
                    help="증가율이 아니라 **업종 사이 비교 가능한 검색 수준**을 굽는다 "
                         "(앵커를 같이 넣는다). 산출물은 trend_level.json")
    ap.add_argument("--anchor", default=ANCHOR,
                    help=f"수준 모드의 앵커 키워드 (기본 {ANCHOR})")
    args = ap.parse_args()

    cid, sec = load_keys(args.legacy)
    end = dt.date.today() - dt.timedelta(days=LAG_DAYS)
    start = end - dt.timedelta(days=FETCH_BACK_DAYS - 1)

    if args.level:
        level_main(args, cid, sec, start.isoformat(), end.isoformat())
        return

    def back(n: int) -> str:
        return (end - dt.timedelta(days=n)).isoformat()

    recent = (back(WINDOW_DAYS - 1), end.isoformat())
    windows = {"seq": (back(2 * WINDOW_DAYS - 1), back(WINDOW_DAYS)),
               "yoy": (back(YEAR + WINDOW_DAYS - 1), back(YEAR))}

    cats = categories()
    if args.limit:
        cats = cats[:args.limit]
    print(f"업종 {len(cats)}개 · 받는 구간 {start} ~ {end}")
    print(f"경로 {'개발자센터(레거시)' if args.legacy else 'NAVER API HUB'}")
    print(f"최근 창   {recent[0]} ~ {recent[1]}")
    for name, w in windows.items():
        mark = "←기준" if name == args.basis else "     "
        print(f"비교 창 {name}  {w[0]} ~ {w[1]} {mark}")
    print()

    scores: dict[str, float] = {}
    skipped: list[str] = []
    both: dict[str, dict[str, float | None]] = {}
    for i, cat in enumerate(cats, 1):
        raw = cached(cat, args.refresh,
                     lambda c=cat: fetch(c, cid, sec, start.isoformat(),
                                         end.isoformat(), args.legacy))
        g = {k: growth(raw, recent, w) for k, w in windows.items()}
        try:
            g["season"], g["in_season"] = seasonal(raw["results"][0]["data"], end)
        except (KeyError, IndexError, TypeError):
            g["season"] = g["in_season"] = None
        both[cat] = g
        pick = g[args.basis]
        if pick is None:
            skipped.append(cat)
        else:
            scores[cat] = round(pick, 4)
        cell = lambda v: "   —  " if v is None else f"{v:+.3f}"
        print(f"  [{i}/{len(cats)}] {cat:20s} yoy {cell(g['yoy'])}  seq {cell(g['seq'])}"
              f"  season {cell(g['season'])}  in_season {cell(g['in_season'])}")

    # `scores` 는 예전 계약 그대로(basis 하나). 넷을 **따로** 싣는 이유는 계절과 유행이 다른
    # 신호라서다 — 합칠지, 얼마로 합칠지는 `recsys/trend.py` 가 백테스트·슬롯 like 율로 정한다.
    def _col(key: str) -> dict[str, float]:
        return {c: round(g[key], 4) for c, g in both.items() if g.get(key) is not None}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "scores": scores,
        "yoy": _col("yoy"), "seq": _col("seq"),
        "season": _col("season"), "in_season": _col("in_season"),
        "as_of": end.isoformat(),
        "window_days": WINDOW_DAYS,
        "season_days": SEASON_DAYS,
        "year_aligned_days": YEAR_ALIGNED,
        "basis": args.basis,
        "source": "naver_api_hub_search_trend",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    top = sorted(scores.items(), key=lambda kv: -kv[1])
    print(f"\n{OUT} — {len(scores)}개 기록({args.basis}), {len(skipped)}개 제외")
    if top:
        print("  뜨는:", ", ".join(f"{k} {v:+.2f}" for k, v in top[:5]))
        print("  식는:", ", ".join(f"{k} {v:+.2f}" for k, v in top[-5:]))
    if skipped:
        print("  제외:", ", ".join(skipped[:10]))

    pairs = [(c, g["yoy"], g["seq"]) for c, g in both.items()
             if g["yoy"] is not None and g["seq"] is not None]
    if len(pairs) > 3:
        import statistics as st
        ys = [p[1] for p in pairs]
        ss = [p[2] for p in pairs]
        print(f"  퍼짐(표준편차)  yoy {st.pstdev(ys):.3f}  seq {st.pstdev(ss):.3f}"
              f"  · 둘의 상관 {st.correlation(ys, ss):+.3f}")


if __name__ == "__main__":
    main()
