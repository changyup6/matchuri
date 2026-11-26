# EC2 배포 검증 — 2026-09-10 (필터·위치·취향 전달)

절차: `docker tag backmock-app:latest backmock-app:map-step3d-20260909`(롤백 태그) →
`docker compose -p backmock build app` → `docker compose -p backmock up -d app`. state 볼륨 그대로.

```
GET /               {"cards":2795,"recommendable":2216,"logs":1693,"sessions":36,"policy":"interleave"}
GET /filters        cats 19 · tags 40 · [한식 710, 카페 460, 일식 188 …]
GET /taste/map      regions 20 · {"name":"한식","x":0.838,"y":0.4531,"r":0.1161,"count":710}
GET /taste/example?demo=1               → 200
GET /cards?user_id=u_none&within_m=300  → 400 {"detail":"위치를 모릅니다 — …"}
컨테이너 로그       [state] 복원: 사용자 52 · 선호 1693 · /app/state
실행 이미지         backmock-app:latest 8aad92cfbcf1 (롤백: map-step3d-20260909 = 73cee48f9d9c)
```

실사용자(스와이프 326·좋아요 178) `GET /users/{id}/taste`:

```json
{"headline":"요즘 취향이 한식에서 양식 쪽으로 옮겨가고 있어요",
 "drift":{"from_region":"한식","to_region":"양식","distance":0.328,"window":10},
 "now":{"x":0.5667,"y":0.5969,"region":"양식",
        "near":[{"name":"카페413프로젝트 역삼점","card_id":1385},{"name":"마초쉐프 강남본점","card_id":1924},{"name":"비놀릭","card_id":131}]}}
trail 60점 · 최근 10 지역: 술집 · 양식 · 분식 · 분식 · 술집 · 술집 · 일식 · 양식 · 양식 · 양식
```

## 2차 배포 — 필터 어휘 고정 (같은 날 12:0x KST)

롤백 태그 `backmock-app:filter-v1-20260910`(= 1차 배포 이미지 8aad92cfbcf1) → `build app` → `up -d app`.
재기동 로그 `[state] 복원: 사용자 52 · 선호 1695`. 실행 이미지 `ec5149ae020f`.

```
GET /filters
{"cats":[한식 710, 카페 460, 일식 188, 술집 187, 양식 143, 중식 121, 간식 93, 분식 87, 기타 227],
 "tags":[가성비 1778, 친절 1745, 양많은 940, 혼밥 743, 점심식사 743, 야식 531, 단체모임 884, 분위기 976,
         데이트 402, 술한잔 238, 조용한 250, 주차 264], "within_m":[300,500,1000,2000]}
GET /cards?cat=기타&limit=3   → 오트보트(샐러드) · 그린보이즈 샐러드&포케(샐러드) · 모던샤브하우스(샤브샤브), category_group 전부 "기타"
GET /cards?tag=단체모임       → 고우가 · 오늘도등심 · 뼈탄집 (원본 태그 단체모임 보유)
GET /cards?cat=초밥           → 422 {"detail":"cat '초밥' 은 없는 값입니다. 허용: 한식, 카페, 일식, 술집, 양식, 중식, 간식, 분식, 기타"}
GET /cards?tag=맛             → 422 {"detail":"tag '맛' 은 없는 값입니다. 허용: 가성비, 친절, 양많은, 혼밥, 점심식사, 야식, 단체모임, 분위기, 데이트, 술한잔, 조용한, 주차"}
```

## 3차 배포 — 필터·위치를 사용자 설정으로 (14:2x KST)

롤백 태그 `backmock-app:vocab-20260910`(= 2차 이미지 ec5149ae020f) → `build app` → `up -d app`.
재기동 로그 `[state] 복원: 사용자 56 · 선호 1742`. 실행 이미지 `08829ff06541`.
OpenAPI: `GET /cards` 파라미터 = `user_id, limit, reviews` 뿐. 사용자 엔드포인트 =
`POST /users · GET /users/{id} · PUT …/location · PUT …/filter · DELETE …/filter · GET …/taste`.

```
POST /users                              → u_7373bd0fa56e (검증용, 스와이프 0)
PUT  /users/{id}/location {37.5006,127.0366}
PUT  /users/{id}/filter {"cat":["일식"],"tag":["혼밥"],"within_m":500}
GET  /cards?user_id&limit=5              → 치솟 344m · 나이스샤워 467m · 카츠쇼신 381m · 갓포돈 142m · 오사무식당 180m
                                            category_group 전부 일식, legacy/taste 번갈아
GET  /cards?user_id&cat=카페             → ['일식','일식','일식']  (쿼리는 계약에 없어 무시된다)
PUT  /users/{id}/filter {"cat":["초밥"]}  → 422 허용 목록
DELETE /users/{id}/filter                → filter null
GET  /cards?user_id 해제 후              → ['양식','한식','양식','한식','양식']
GET  /users/{id}                         → location 유지 · filter null
```

## 4차 배포 — API 타입 힌팅 (15:0x KST)

롤백 태그 `backmock-app:user-settings-20260910`(= 3차 이미지 08829ff06541) → `build app` → `up -d app`.
재기동 로그 `[state] 복원: 사용자 62 · 선호 1771 · /app/state`. 실행 이미지 `987482e1e6d7`. `/docs` 200.

실서버 `/openapi.json` — 클라이언트 오퍼레이션 14개 전부 200 응답 스키마 보유, 컴포넌트 27개:

```
GET    /                          → StatusOut            GET    /filters                → FiltersOut
POST   /users                     → UserRecordOut        GET    /cards                  → list[CardOut]
GET    /users/{id}                → UserOut              GET    /cards/{id}             → CardDetailOut
PUT    /users/{id}/location       → UserRecordOut        GET    /cards/{id}/reviews     → list[Review]
PUT    /users/{id}/filter         → UserRecordOut        POST   /preferences            → PreferenceOut
DELETE /users/{id}/filter         → UserRecordOut        GET    /preferences/{id}       → list[CardBaseOut]
GET    /taste/map                 → TasteMapOut          GET    /users/{id}/taste       → UserTasteOut

FilterIn.cat enum   = 한식·카페·일식·술집·양식·중식·간식·분식·기타      Store.category_group enum = 같은 9개
FilterIn.tag enum   = 가성비·친절·양많은·혼밥·점심식사·야식·단체모임·분위기·데이트·술한잔·조용한·주차
Log.prefer enum     = like·dislike                                   CardOut.policy = legacy·taste | null
```

## 5차 배포 — 보안·관측 (16:4x KST)

롤백 태그 `backmock-app:apiguide-20260910`. `build app` → `up -d app`(헬스체크 healthy 20초) → `up -d nginx`.
앱 이미지 재시작 로그 `state.restore users 92 · preferences 1921 · 25.2s`. 두 컨테이너 Up, app (healthy).

```
GET /  응답 헤더        Server: nginx (버전 없음) · x-request-id · X-Content-Type-Options nosniff · X-Frame-Options DENY
                        · Referrer-Policy strict-origin-when-cross-origin · Permissions-Policy …
GET /admin/             401 (인증 없음) → 200 (Basic admin:***)
GET /docs               Content-Security-Policy 있음 (HTML)   /  GET /cards JSON 에는 CSP 없음
GET /healthz            {"ok":true}
GET /readyz             {"ok":true,"data":true,"pool":true,"state_writable":true,"artifacts":true,"policy":"interleave",…}
GET /metrics            matchuri_http_requests_total{route,method,status} · matchuri_users_total · … (Prometheus 텍스트)
GET /jenkins/login      200 (프록시 유지)

GET /filters 60회 연타   200 ×37 · 429 ×23        (zone api 10r/s burst 30)
POST /users 12회 연타    200 ×1  · 429 ×11        (zone mint 1r/s burst 5)
POST /users 20KB 본문    413
GET /cards?user_id=<script>   422 {"detail":"user_id 는 영문·숫자와 . _ : - 만 쓸 수 있고 1~64자여야 합니다"}

request_id 하나로 두 로그를 잇는다 (X-Request-ID: trace-1789026225):
nginx  {"ts":"…","request_id":"trace-1789026225","method":"GET","uri":"/cards?user_id=u_7373bd0fa56e&limit=2","status":200,
        "bytes_sent":5089,"request_time":0.099,"upstream_response_time":"0.100","limit_req_status":"PASSED",…}
app    {"event":"cards.serve","user_id":"u_7373bd0fa56e","n_requested":2,"n_returned":2,"policies":{"legacy":1,"taste":1},
        "filter":null,"has_location":true,"ms":97.5,"request_id":"trace-1789026225"}
       {"event":"http","method":"GET","path":"/cards","route":"/cards","status":200,"ms":98.9,"client_ip":"172.19.0.1",
        "user_id":"u_7373bd0fa56e","ua":"curl/8.5.0","bytes_out":4688,"request_id":"trace-1789026225"}
```

## 6차 배포 — 카드 찜 (16:5x KST)

롤백 태그 `backmock-app:ops-20260910`. `build app` → `up -d app`(healthy 34초). 복원 `users 96 · preferences 1921 · bookmarks 0`.

```
PUT  /users/{id}/bookmarks/65     {"card_id":65,"bookmarked":true,"at":"2026-09-10T07:55:52+00:00","count":1}   (두 번째도 동일 — 멱등)
PUT  /users/{id}/bookmarks/1944   {"card_id":1944,"bookmarked":true,"at":"…","count":2}
GET  /users/{id}/bookmarks        [치솟(일식) 344m · 고우가 역삼역점(한식) 64m]  최신 순, bookmarked_at·distance_m
GET  /cards/65?user_id=…          bookmarked true   /  user_id 없이 → false
GET  /users/{id}                  bookmarks 2
DELETE /users/{id}/bookmarks/1944 {"card_id":1944,"bookmarked":false,"at":null,"count":1}
PUT  …/bookmarks/999999999        404 {"detail":"card not found"}
로그  bookmark.add(created false → 멱등) · bookmark.add(created true) · bookmark.remove(removed true)
지표  matchuri_bookmarks_total{action="add"} 2 · {action="remove"} 1
파일  state/bookmarks.jsonl 세 줄 (add 65 · add 1944 · remove 1944)
```

## 7차 배포 — 관측 스택 (Prometheus + Grafana, 19:3x KST)

앱 지표 확장은 7차 앞(18:1x)에 따로 반영했다 — 복원 `users 99 · preferences 1929 · bookmarks 3 ·
exposures 5602`, `matchuri_restore_seconds 24.6`.

순서: `up -d prometheus` → `node-exporter` → `grafana` → `nginx-exporter`, nginx 는 설정만 바뀌었으므로
(컨테이너 스펙은 그대로라 컴포즈가 재생성하지 않는다) `nginx -t && nginx -s reload` 로 무중단 반영했다.

```
Prometheus 수집 대상        matchuri http://app:8000/metrics            up
                          nginx    http://nginx-exporter:9113/metrics  up   ← nginx_up = 1 (stub_status 반영됨)
                          node     http://node-exporter:9100/metrics    up
                          prometheus                                    up
규칙                       기록 3 · 알림 5, 전부 health=ok · inactive
GET /metrics              404      (공개 차단)
GET /grafana/             302 → /grafana/login → 200 <title>Grafana</title>
GET /grafana/api/search   401 (익명) / 200 (admin) — 대시보드 둘: 맛추리 운영 · 맛추리 지연 상세
데이터소스 health          "Successfully queried the Prometheus API."
GET /nginx_status         Active connections: 1   (도커 대역·루프백만 허용)
GET /cards                200 (기존 API 영향 없음)
```

### 배포 중에 고친 것 — `/grafana/` 무한 리다이렉트

`proxy_pass http://grafana:3000/;` 처럼 **끝에 슬래시**를 두면 nginx 가 `/grafana/` 접두사를 잘라
업스트림에 `/` 를 넘긴다. Grafana 는 `GF_SERVER_SERVE_FROM_SUB_PATH=true` 라 접두사가 붙은 경로를
기대하므로 잘린 `/` 를 받으면 root_url(`/grafana/`)로 301 을 돌려주고, 그 요청이 다시 들어와
**같은 자리를 맴돌았다**(`/grafana/ → 301 → /grafana/`). 슬래시를 빼서 접두사를 그대로 넘기고,
잘라 내는 일은 Grafana 에 맡겼다. API(`/grafana/api/*`)는 접두사가 살아 있어 처음부터 정상이었기
때문에 **API 검증만으로는 안 잡혔다** — 브라우저 진입 경로를 따로 확인해야 한다.

### 캡처 (실서버)

| 파일 | 무엇 |
|---|---|
| `grafana-live-ops.png` | 맛추리 운영 — 트래픽·추천·사용자 행동·시스템 |
| `grafana-live-latency.png` | 맛추리 지연 상세 |

'수집 대상 상태(up)' 패널에 nginx 잡이 **빨강 → 초록**으로 바뀐 구간이 남아 있다 — stub_status 가
배포된 순간이다. 추천 행(정책별 좋아요율·prefer 별 선호)은 앱 재시작 뒤 아직 스와이프가 없어 비어
있고, 사용자가 카드를 넘기기 시작하면 채워진다(지표 자체는 임시 서버에서 값까지 확인했다).

## 8차 배포 — 태그 필터 AND → OR (2026-09-11 10:2x KST)

롤백 태그 `backmock-app:metrics-20260910`. `build app` → `up -d app`(healthy 34초).

```
PUT /users/{id}/filter {"tag":["단체모임","분위기"]}
GET /cards?user_id=…&limit=20
  → 20장 전부 두 태그 중 **하나 이상** 보유 · 한 개만 가진 카드 13장 · 둘 다 7장
     (AND 였다면 그 13장이 나오지 않는다)
후보 풀 기준  AND 398곳 → OR 1,552곳
```

카드 응답의 `store.tags` 는 **리뷰 태그 상위 5개**라 매칭 어휘 전체가 아니다 — 검증은 서버
안에서 `filters.client_tags(가게 레코드)` 로 다시 계산했다(처음에 `store.tags` 로 재다가
'선덕칼국수 본점' 이 안 맞는 것처럼 보였는데, 그 가게의 원본 태그에는 `단체모임`·`회식장소` 가
있었다).

## 9차 배포 — 정렬 보정(겹치는 태그 · 거리) (2026-09-11 13:0x KST)

롤백 태그 `backmock-app:tag-or-20260911`. `build app` → `up -d app`(healthy 34초).
복원 `users 141 · preferences 2060 · bookmarks 14 · exposures 8207`.

```
① 위치(역삼역) + tag=["단체모임","분위기"]  → 10장 **전부 겹침 2** (한 개만 맞는 후보는 아래로 밀렸다)
② 위치 + tag=["혼밥"] (태그 하나 = tier 없음) → 거리만 반영
   고우가 64m · 원미옥 328m · 크라이치즈버거 581m · 선덕칼국수 300m · 농민백암순대 776m
   · 질할브로스 66m · 돼봉삼겹살 971m · 라이브볼키친 86m      평균 396m
   (≤500m 카드는 "역삼역 근처, 걸어서 갈 만한 328m 거리예요.", 그 밖은 정책의 원래 이유 유지)
③ 위치 없음 → distance_m null, 거리 보정 없음

로그  cards.serve  {tiered: true,  dist_weight: 0.4, has_location: true}
                   {tiered: false, dist_weight: 0.4, has_location: true}
                   {tiered: false, dist_weight: null, has_location: false}
지표  matchuri_cards_ranked_total{by="tier"} 2 · {by="distance"} 1 · {by="none"} 1
```

거리는 **벽이 아니라 점수**다 — ②에서 971m 가게가 여전히 나온다(인기·취향 점수가 이겼다).
가중치는 `MATCHURI_DIST_WEIGHT`(기본 0.4)로 조절하고, 0 으로 두면 예전 순서가 된다.
