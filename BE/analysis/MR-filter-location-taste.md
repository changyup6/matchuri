# 필터 · 사용자 위치 · 취향 전달 (`GET /users/{id}/taste`)

| | |
|---|---|
| 대상 브랜치 | `feat/be-recsys-multivector` 위에 얹는 스택 MR (상류 경로 접두어 `BE/`). !21 이 먼저 들어가야 한다 |
| 기준 커밋 | `1063555` (= !21 의 끝) |
| 변경 규모 | 서버 14개 파일 +1,904 −31 (코드 5개 신규 · 5개 수정 · 테스트 3개), 근거 문서 3개 + 캡처 2장 |
| 테스트 | **234 passed** (기존 119 + 115) |
| 배포 | **2026-09-10 EC2 반영 (7차 19:3x KST, 관측 스택 포함)** — 롤백 태그 `backmock-app:bookmarks-20260910` (2.5절) |

한 문장으로: **사용자가 필터와 위치를 설정해 두면 추천이 그것을 읽고(필터·위치), 자기 취향이
어디로 가는지 한 장으로 본다(취향 전달).** 계약은 `어휘 조회 GET /filters → 설정 PUT /users/{id}/filter ·
PUT /users/{id}/location → 추천 GET /cards?user_id=` 다. 설정은 사용자에 붙고, **GET 으로 설정하지 않는다**
(오전에 넣었던 `/cards` 의 cat/tag/within_m/lat/lon 쿼리는 같은 날 걷어냈다). 기존 응답 계약은 필드를 **더하기만** 했다.

---

## 1. 무엇을 어떻게 바꿨나

### 1.1 필터 — `PUT /users/{id}/filter` (`app/filters.py`, `app/geo.py`)

| 파라미터 | 뜻 | 결합 |
|---|---|---|
| `cat` | 업종 **9개 중** 쉼표 구분 — `한식·카페·일식·술집·양식·중식·간식·분식·기타` | **OR** ("이 중에서 골라 줘") |
| `tag` | 태그 **12개 중** 쉼표 구분 — `가성비·친절·양많은·혼밥·점심식사·야식·단체모임·분위기·데이트·술한잔·조용한·주차` | **AND** ("이 성격을 전부 가진 곳") |
| `within_m` | 저장된 위치에서 이 거리(m) 안쪽 | 위치 필요 (`PUT /users/{id}/location` 이 없으면 `/cards` 가 400) |

본문 `{"cat":[…],"tag":[…],"within_m":500}` 을 사용자에 저장하고(`DELETE` 로 해제), `GET /cards?user_id=`
가 매번 읽어 적용한다. **어휘는 서버가 소유한다.** `GET /filters` 가 위 값과 후보 풀(2,216곳) 기준 개수를
주고, 클라이언트는 그 값만 보낸다 — 밖의 값은 저장 시점에 **422 + 허용 목록**이다(오타와 '그런 가게가 없다' 를
구분해야 한다). 업종은 풀 개수 상위 8개 + `기타`(나머지 전부, cat2 없는 4곳 포함), 카드에도 같은
라벨을 `store.category_group` 으로 싣는다(`category` = cat3 는 그대로). 태그 12개는 원본 태그의
동의어 묶음이다 — `친절` = 친절+친절한, `단체모임` = 단체모임+회식장소+친목모임, `분위기` = 분위기+감성적,
`데이트` = 데이트+데이트코스+소개팅. 묶어야 하는 이유: 884곳의 단체모임 중 175곳은 회식장소·친목모임
으로만 잡힌다. 구 정책의 학습 특성 `swipe.CATS`(10+기타)는 다른 목록이고 건드리지 않았다.

**필터는 응답에서 거르지 않고 후보 선택 단계에 들어간다.** 뽑고 나서 버리면 (1) 버린 카드가
`shown` 에 남아 그 사용자에게 영영 다시 안 나오고 (2) 10장 요청이 말없이 3장이 된다. 그래서
허용 집합을 `policy.pick_many(..., allow=)` 로 두 정책에 내려보내고 `swipe._pick` ·
`taste._pick_one` 이 집합 밖 후보를 건너뛴다 — **fallback 도 집합 안에서만**. 다양성 규칙
(같은 업종 3연속 금지 등)은 양보하지만 필터는 사용자가 명시한 조건이라 양보하지 않는다.
못 채우면 있는 만큼만 준다(빈 배열도 정상). 학습(`record`)·노출 로그·재생 복원은 손대지 않았다.

카드 최상위에 `distance_m`(정수 m, 저장된 위치가 없으면 `null`) 을 더했다. `GET /cards/{id}` 도
`user_id` 를 받아 같은 값을 준다. 거리는 하버사인이다 — 역삼동 한 동네라도 평면
근사는 위도·경도 1도 길이의 차이(111km 대 88km)를 손으로 보정해야 하고, 잊으면 동서로 25%
짧게 재는 버그가 조용히 남는다.

### 1.2 사용자 위치 — `PUT /users/{id}/location`

`{"lat": 37.5006, "lon": 127.0366}` → 레지스트리에 `location: {lat, lon, at}`. `POST /users` ·
`GET /users/{id}` 응답에 `location`(없으면 `null`) 이 붙는다. 미등록 id 도 받는다(`user_id` 는
아무 문자열이나 되는 것이 이 서버의 계약이다). 영속화는 `state/users.jsonl` 에 **병합된 전체
레코드**를 append — 복원은 마지막 레코드가 이기므로(`state.restore`) 재시작 뒤에도 남는다.
위치가 바뀌면 앱이 PUT 을 다시 부른다(GET 에는 위치 쿼리가 없다).

### 1.3 취향 전달 — 지형도 위의 내 궤적 하나 (`app/taste_api.py`, `static/taste.html`)

보여 줄 것을 **하나**로 골랐다: 성분 가중치 8개는 사용자가 읽을 수 없고(관리자용), 업종 막대는
구 정책의 언어다. 취향 지형도(`/admin/map` 의 `layout.json`, 콘텐츠 유사도로 편 같은 땅)
위에 **내 취향의 중심이 어디에 있고 어디로 움직였나**를 점 하나의 이동으로 보여 준다 —
변화가 곧 그림이다.

`GET /users/{user_id}/taste` (읽기 전용):

```
map.regions   업종 20개의 원 {name, x, y, r, count} — 그리기 전용, 모든 사용자가 같은 땅
trail[]       like 마다 한 점 {n, x, y, region, store, card_id} — 위치는 like 좌표의 EMA(α=0.35), 최근 60점
now           현재 점 + near(그 점에 가장 가까운 좋아요 가게 3곳) — UMAP 이 거짓말할 때의 닻
drift         10 like 전 → 지금 {from_region, to_region, distance, window: 10}
headline      "요즘 취향이 한식에서 술집 쪽으로 옮겨가고 있어요"  (like<3 → 안내, 이동 <0.05 → 자리 잡음)
```

`GET /taste/map` 은 regions 만 따로(앱이 한 번만 받는다). `GET /taste/example?user_id=` 가
폰 크기 예시 화면이고 `?demo=1` 은 실서버 응답을 붙인 내장 샘플로 서버 없이 열린다.

**지역 이름은 이웃 15곳 다수결이다.** 처음 규칙(가장 가까운 업종 *중심*)은 가게 8곳짜리
업종이 710곳짜리와 동등한 자격으로 이름을 걸어, 곱창·갈비·삼겹살만 좋아한 궤적을
`샤브샤브` 라 부르고 헤드라인을 `퓨전요리` 에서 출발시켰다. 사용자가 보는 문장은 헤드라인
하나뿐이라 그 하나가 틀리면 나머지가 다 맞아도 소용없다. 점에서 가장 가까운 **가게** 15곳의
cat2 최다로 바꾸자(K = UMAP `n_neighbors`) 후보 2,216곳 전부에서 **20곳 미만 업종이 가져간
점 0**, 자기 업종 이름을 받는 가게 83.9% (테스트로 잠갔다).

### 1.4 API 타입 힌팅 — `/openapi.json` 이 곧 클라이언트 타입 (`analysis/backtest/results/openapi-20260910.md`)

클라이언트 오퍼레이션 14개 전부에 pydantic `response_model` 을 붙였다(컴포넌트 27개, 필드마다
설명·예시). `cat`·`tag`·`prefer`·`category_group`·`policy` 의 허용값이 스키마 `enum` 으로
`/docs` 에 보인다 — 프론트는 `npx openapi-typescript http://<host>/openapi.json -o api.d.ts` 로
타입을 뽑으면 된다. `Literal` 대신 `enum` 을 쓴 이유: `Literal` 은 pydantic 이 핸들러 전에 값을
거절해 `filters.validate` 의 한국어 422 와 `prefer` 의 400 을 바꿔 버리고, 어휘가 줄었을 때 옛
값을 저장한 사용자의 `GET /users/{id}` 가 500 이 된다. 코드젠 결과는 같다. 응답 모양은 한 곳만
바뀌었다 — 미등록 id 로 `PUT …/location` 하면 `filter` 키가 빠지던 구멍을 `null` 로 채웠다(더하기).

### 1.5 보안·관측 (`analysis/backtest/results/observability-20260910.md`)

**엣지(nginx).** `/admin` 접두사 전체에 Basic 인증(.htpasswd 는 커밋·이미지 제외, 평문은 EC2
`~/matchuri-secrets/` 에만). IP 당 `10r/s burst 30`(발급 `POST /users` 는 `1r/s burst 5`), 동시 연결
20, 본문 16k, `server_tokens off`, 보안 헤더 5종(nosniff·DENY·Referrer·Permissions·HTML 전용 CSP),
`X-Request-ID` 수용/생성 후 업스트림 전달, JSON 접근 로그(`request_time`·`upstream_response_time`·
`limit_req_status`). compose: `--proxy-headers --no-server-header`, `/healthz` 헬스체크, nginx 는
`service_healthy` 뒤에, 로그 로테이션 50m×5.

**앱.** 요청마다 JSON 한 줄(`event:http` — request_id·route·status·ms·client_ip·user_id·ua·bytes_out)
+ 도메인 이벤트(`user.create`·`user.location.set`·`user.filter.set/clear`·`pref.record`·`cards.serve`·
`taste.view`·`state.restore`), 500ms 초과 경고, 500 은 `{"detail":"internal error","request_id"}` 만
(traceback 은 로그에). `GET /healthz`·`/readyz`(데이터·풀·state 쓰기·산출물)·`/metrics`(Prometheus 텍스트,
의존성 없음). `user_id` 는 `[A-Za-z0-9._:-]{1,64}`, `name` ≤ 50자, CORS 는 `MATCHURI_CORS_ORIGINS` 로만.

### 1.6 카드 찜 (`analysis/backtest/results/bookmarks-20260910.md`)

`PUT/DELETE /users/{id}/bookmarks/{card_id}` · `GET /users/{id}/bookmarks`(최신 순, 카드 모양 +
`bookmarked_at` + `distance_m`). 카드 응답에 `bookmarked`, 사용자 응답에 `bookmarks` 수. 찜은 사용자
상태이고 **추천과 분리**된다 — `app/bookmarks.py` 는 `recsys` 를 import 하지 않고, 테스트가 두 정책의
세션·노출 로그가 찜 호출 전후로 같음을 잠근다. `state/bookmarks.jsonl` 추가 전용(add/remove), 재시작
복원, 사용자당 500개 상한(오래된 것부터 빠지고 WARNING 로그).

### 1.7 관측 스택 — Prometheus + Grafana (`monitoring/`, `analysis/backtest/results/grafana-20260910.md`)

지표는 1.5 절에서 이미 냈지만 **시간축이 없었다** — `/metrics` 한 장은 '지금' 만 말한다. 정책별
좋아요율이 어제보다 올랐는지, 지연이 언제부터 튀었는지는 시계열이어야 답할 수 있고 인터리빙
실험의 결론도 그 그래프에서 나온다.

- Prometheus 15초 수집(app·node·nginx·자기 자신), 30일 보관, 기록 규칙 3개(오류율·p95·
  **정책별 좋아요율**)와 알림 규칙 5개. Alertmanager 는 없다(알림 받을 곳이 아직 없다) —
  규칙은 평가되고 `ALERTS` 계열로 Grafana 에 보인다.
- Grafana 대시보드 둘(파일 프로비저닝, 화면에서 고친 것은 저장되지 않는다):
  **맛추리 운영**(트래픽 · 추천 인터리빙 · 사용자 행동 · 시스템, 26개 패널)과 **맛추리 지연 상세**.
- 넷 다 호스트 포트를 열지 않는다. 문은 nginx 의 `/grafana/` 하나(Grafana 자체 로그인)뿐이고
  **공개 `/metrics` 는 404 로 닫았다** — Prometheus 는 도커 네트워크에서 `app:8000` 을 직접 긁는다.
  열어 두면 사용자 수·정책별 성적·라우트 목록이 그대로 나간다.
- 앱 지표도 대시보드에 맞춰 늘렸다: `matchuri_preferences_total{prefer,policy}`(그 카드를 **노출한
  정책** — 노출 맵을 `exposures.jsonl` 로 복원하므로 재시작해도 라벨이 유지된다),
  `cards_requests_total{filtered,personalized}`·`cards_short_total`, 상태 게이지, `/proc` 기반 프로세스 지표.

### 1.8 응답 계약은 더하기만 했다

`distance_m`(카드) · `location`(사용자) 추가, 새 엔드포인트 5개
(`GET /filters`, `PUT /users/{id}/location`, `GET /users/{id}/taste`, `GET /taste/map`,
`GET /taste/example`). 없앤 필드·바뀐 이름 없음 (`tests/test_api.py` 가 잠근다).

## 2. 검증

### 2.1 테스트 — 175 passed (119 → 175)

```
cd server && MATCHURI_STATE=$(mktemp -d) python -m pytest tests -q
```

- `test_filter.py` 26개 — 허용 집합이 두 정책·인터리빙에서 지켜지는가, fallback 이 집합을
  벗어나지 않는가, 빈 집합 → 빈 목록 + 세션 불변, `record` 불변, 거리 필터와 `distance_m`,
  위치 없이 `within_m` → 400, 쿼리 > 저장 우선순위, 위치 영속·복원, `/filters` 모양.
- `test_taste_api.py` 30개 — 중앙값·EMA(3점 수치)·다수결·동점 순서를 손 계산과 비교,
  헤드라인 규칙의 모든 갈래(10→11 like 경계 포함), 404/503, **읽기 전용**(세션 m·v·`shown`·
  `hist` 와 state 파일을 호출 전후 통째 비교 — `test_map.py` 와 같은 규율), 진짜 지형에서
  작은 업종이 이름을 못 가져가는 것.

### 2.2 실데이터 스모크 (`analysis/backtest/results/filter-location-20260910.md`)

임시 state · 포트 8012 · interleave. 응답의 `card_id` 로 가게 레코드를 다시 찾아 확인했다.

| 호출 | 결과 |
|---|---|
| `cat=일식&within_m=500` (저장 위치 = 역삼역) | 10장, 전부 일식, 최대 467m |
| `tag=혼밥,가성비` | 10장, 전부 두 태그 보유 |
| `within_m=300` (저장 위치만) | 10장, 최대 291m |
| `lat=37.5096&lon=127.0366&within_m=200` | **1장 197m** — 쿼리 위치가 저장 위치를 이긴다 |
| `cat=중식&tag=혼밥&within_m=300&limit=20` | 10장 < 20 — 못 채우면 있는 만큼 |
| `user_id` 없이 `cat=카페&within_m=300` | 5장, `policy: null`, 전부 ≤300m |
| 오류 | 위치 없이 `within_m` → 400, `lat` 만 → 400, `lat=91` → 422, 모르는 업종 → `[]` |

`state/users.jsonl` 은 그 사용자에 2줄(발급 + PUT)뿐 — `GET /cards?lat&lon` 은 쓰지 않았다.

### 2.3 취향 전달 실데이터 (`analysis/backtest/results/taste-api-20260910.md`)

한식 12장 좋아요 → 일식·술집 좋아요, 58 스와이프 / 22 like. 궤적의 지역 이름을 순서대로:

```
한식 ×8 · 술집 · 한식 ×4 · 술집 · 양식 ×2 · 술집 · 일식 · 술집 · 양식 ×2 · 술집
headline  "요즘 취향이 한식에서 술집 쪽으로 옮겨가고 있어요"   drift 한식 → 술집, 0.476
```

`/taste/map` 32ms(첫 호출) → 2ms, `/users/{id}/taste` 3.6ms · 3.9KB.

### 2.4 화면 캡처 (390×780, chrome-headless-shell)

`analysis/backtest/results/taste-api-20260910-light.png` · `-dark.png`. 큰 업종만 옅은 면으로,
궤적은 오래된 쪽이 가늘고 옅은 곡선, 점은 시작·10번 전·지금 셋뿐. 360×640 에서 스크롤 없이
한 화면.

### 2.5 EC2 배포 검증 (`analysis/backtest/results/deploy-20260910/verification.md`)

**7차(관측 스택)**: 수집 대상 4잡 UP(`nginx_up=1`), 규칙 8개 health=ok, `/metrics` 404,
`/grafana/` → 로그인(익명 401), 데이터소스 health OK, 대시보드 둘 프로비저닝. 배포 중
`/grafana/` 무한 리다이렉트를 잡았다(`proxy_pass` 끝 슬래시 ↔ `serve_from_sub_path`).
캡처: `deploy-20260910/grafana-live-ops.png` · `-latency.png`.


**6차(찜)**: PUT 멱등, 목록 최신 순 + `distance_m`, 카드 `bookmarked`, DELETE, 404, 로그·지표·파일 확인.


**5차(보안·관측, nginx 포함)**: `/admin` 401→200(Basic), 보안 헤더·`Server: nginx`, `/docs` 에만 CSP,
`/filters` 60연타 → 429 ×23, `POST /users` 12연타 → 429 ×11, 20KB → 413, `user_id=<script>` → 422,
`/readyz` ok, `/metrics`, 같은 `request_id` 로 nginx·앱 로그 조인 확인, `/jenkins` 유지.


**4차(타입 힌팅)**: 실서버 `/openapi.json` 에서 클라이언트 오퍼레이션 14개 전부 200 응답 스키마,
`FilterIn.cat/tag`·`Log.prefer`·`Store.category_group`·`CardOut.policy` enum 확인, `/docs` 200.


**3차(사용자 설정)**: OpenAPI 의 `GET /cards` 파라미터가 `user_id, limit, reviews` 뿐임을 확인.
PUT 위치·필터(일식·혼밥·500m) → `/cards` 5장 전부 일식·≤467m, `?cat=카페` 를 실어도 무시,
`cat=초밥` 저장 422, DELETE 뒤 `/cards` 가 양식·한식으로 넓어짐. 복원 `사용자 56 · 선호 1742`.


**2차(어휘 고정)**: `/filters` = 업종 9 · 태그 12 · 개수 합 2,216, `cat=기타` 카드의
`category_group: "기타"`, `cat=초밥` · `tag=맛` → 422, 복원 `사용자 52 · 선호 1695`.

1차:

`docker compose -p backmock build app` → `up -d app`, state 볼륨 그대로. 재기동 로그
`[state] 복원: 사용자 52 · 선호 1693`. 실서버에서 `/filters`(19 업종·40 태그) ·
`/taste/map`(20 지역) · `/taste/example?demo=1` 200 · 위치 없는 `within_m` 400 확인.
실사용자(스와이프 326) `/users/{id}/taste` → "요즘 취향이 한식에서 양식 쪽으로 옮겨가고 있어요",
drift 0.328, `near` 3곳.

## 3. 남은 제약

- **EMA 중심은 두 갈래 취향의 가운데(아무 데도 아닌 곳)에 앉을 수 있다.** UMAP 은 먼 점 사이
  거리를 보존하지 않는다. 그래서 `now.near`(현재 점에 가장 가까운 좋아요 가게 3곳)를 같이
  준다 — 화면은 항상 실제 가게 이름을 닻으로 보여 줘야 한다.
- 헤드라인의 창은 최근 10 like 다. 25 like 시나리오에서는 12장째 한식이 창 밖으로 밀려
  `양식 → 술집` 이 된다 — '요즘' 의 정의이고 규칙대로다.
- `regions.r` 은 구성원 2곳짜리 업종에서 0.30 까지 커진다(중앙값 정의). API 값은 두고
  화면에서 40곳 미만은 칠하지 않고 반지름을 0.14 로 자른다.
- 태그 AND 는 촘촘하면 금방 빈다. 어휘·개수를 `GET /filters` 가 주므로 UI 가 개수 0 인
  조합을 미리 막을 수 있다.
- 위치는 메모리 + append 로그다(DB 없음, 기존과 같다).
