# 맛추리 BE

세 파트로 나뉩니다. **서로 독립해서 돌아가고, 데이터로만 이어집니다.**

```
crawler/   ①  카카오맵에서 긁는다        →  data/
server/    ②  API 로 서빙한다 (배포됨)   ←  data/kakao
analysis/  ③  실험하고 검증한다          ←  data/ + server/recsys
```

| | 무엇을 | 언제 돌리나 | 의존성 |
|---|---|---|---|
| [`crawler/`](crawler/) | 수집 → 정규화 → 런타임 데이터 | 데이터를 새로 받을 때 (약 6시간) | `requests`, `curl_cffi` |
| [`server/`](server/) | 카드·추천·관리자 API | 항상 (배포 중) | `fastapi`, `uvicorn`, `numpy` |
| [`analysis/`](analysis/) | 추천 방법 비교, 리뷰 표현력 분석 | 필요할 때 | `numpy`, `matplotlib` |
| [`data/`](data/) | 세 파트가 공유하는 데이터 | — | — |

배포되는 것은 **`server/` 뿐**입니다. `crawler/` 와 `analysis/` 는 도커 이미지에 들어가지
않습니다(`.dockerignore`).

## 빨리 돌려보기

```bash
# 서버
cd server && pip install -r requirements.txt
uvicorn main:app --reload --port 8000     # http://127.0.0.1:8000/docs
                                          # http://127.0.0.1:8000/admin  관리자 화면
```

배포는 아래 [배포](#배포) 절을 보세요 (`docker compose -p backmock up -d --build`, Jenkins 파이프라인이 수행).

## 데이터가 흐르는 길

```
crawler/yeoksam_stores.py        역삼동 음식점 목록      →  data/yeoksam/stores.jsonl (공개본에서 제외)
crawler/yeoksam_full.py          카카오맵 응답 원본      →  data/yeoksam_full/*.jsonl  (538MB, 커밋 안 함)
crawler/yeoksam_full_parse.py    정규화 테이블          →  data/yeoksam_full/parsed/
crawler/build_runtime_data.py    서버용 슬림 데이터      →  data/kakao/               (39MB, 공개본에서 제외)
                                                            ↑ server 가 읽는 것
```

원본을 커밋하지 않는 이유와 다시 만드는 방법은 [`crawler/README.md`](crawler/README.md) 에
있습니다. 데이터가 무엇을 담고 있는지는 [`data/README.md`](data/README.md) 를 보세요.

## API 요약

| | |
|---|---|
| `POST /users` | `user_id` 발급 |
| `GET /filters` | **서버가 정한 어휘** — 업종 9개(상위 8 + 기타)·태그 12개·반경. 필터에 넣을 수 있는 값 |
| `PUT /users/{user_id}/filter` | 필터 저장(`{cat, tag, within_m}`) — 어휘 밖이면 422 |
| `DELETE /users/{user_id}/filter` | 필터 지우기 |
| `PUT /users/{user_id}/location` | 위치 저장(`{lat, lon}`) — 거리 필터·`distance_m` 에 쓰인다 |
| `PUT`·`DELETE /users/{user_id}/bookmarks/{card_id}` | **찜** 담기·빼기(멱등). 추천을 건드리지 않는다 |
| `GET /users/{user_id}/bookmarks` | 찜한 카드 목록(최신 순, 페이징) |
| `GET /cards?user_id=&limit=10` | **추천.** 저장된 필터·위치를 적용한다(쿼리로는 못 건다). 한 번 준 카드는 다시 안 준다 |
| `GET /cards/{card_id}` | 상세 — `store.reviews` + `menus`(이미지 포함) |
| `GET /cards/{card_id}/reviews` | 리뷰 전체 |
| `POST /preferences` | 선호 표현(like/dislike) + **취향 모델 즉시 갱신** |
| `GET /preferences/{user_id}` | 선호한 카드 (`?prefer=like·dislike·all`) |
| `GET /users/{user_id}/taste` | **취향 전달** — 취향 지형도 위의 내 궤적(headline·trail·now·drift). 읽기 전용 |
| `GET /taste/map` | 취향 지형(업종 20개의 원). 사용자와 무관하므로 앱이 한 번만 받는다 |
| `GET /admin` | 관리자 화면 — 사용자별 학습 현황 |

자세한 계약은 [`docs/API.md`](../docs/API.md)(클라이언트 가이드 — 호출 순서·모든 필드·에러·예시)와 `/docs`, 구현은 [`server/README.md`](server/README.md).
**`/openapi.json` 에 모든 응답·요청 타입이 들어 있습니다** — 프론트는 타입을 직접 쓰지 말고
생성하세요: `npx openapi-typescript http://<host>/openapi.json -o api.d.ts`
(업종·태그·`prefer` 의 허용 값도 `enum` 으로 들어 있어 유니온 타입이 됩니다).

## 배포

```bash
docker compose -p backmock up -d --build      # BE/ 에서. Jenkins 파이프라인이 수행
```

`app`(uvicorn) 과 `nginx`(80) 둘이다. 인터넷과 맞닿는 것은 nginx 뿐이고 **앱의 8000 은
`expose` 로만 열려 있다** — 호스트에 열면 아래의 인증과 레이트 리밋을 우회하는 문이 생긴다
(`/admin` 이 그대로 열린다). 엣지 설정은 `nginx/default.conf` 한 장이고 호스트에서
마운트하므로 이미지를 다시 굽지 않고 고칠 수 있다:

```bash
docker compose -p backmock exec nginx nginx -t          # 문법 검사
docker compose -p backmock exec nginx nginx -s reload   # 무중단 반영
```

### 엣지가 막는 것 (`nginx/default.conf`)

| | |
|---|---|
| `/admin` **접두사 전체** | HTTP Basic 인증 — 화면(`/admin`, `/admin/map`)과 `/admin/api/*` 가 같은 문을 쓴다 |
| 공개 | 서비스 API 전부 + `/docs` · `/openapi.json`(프론트가 타입을 생성한다) · `/metrics`(집계 수치뿐) |
| 레이트 리밋 | IP 당 **10r/s · burst 30 nodelay**, 초과는 **429** |
| `POST /users` | IP 당 **1r/s · burst 5** (전체 상한과 겹쳐 적용) |
| 동시 연결 | IP 당 **20** |
| 본문 크기 | **16k** (초과 413) · 본문·헤더 타임아웃 10s |
| 응답 헤더 | `nosniff` · `X-Frame-Options DENY` · `Referrer-Policy` · `Permissions-Policy` · **HTML 응답에만** CSP |
| 업스트림 헤더 | `Host` · `X-Real-IP` · `X-Forwarded-For` · `X-Forwarded-Proto` · `X-Request-ID` |

숫자의 근거는 앱의 사용 모양이다. 스와이프는 카드 한 장에 요청 한 번꼴이라 사람 손으로는
초당 1~2건이 한계다. 10r/s 는 그 10배 여유이고, `burst 30 nodelay` 가 화면 진입 때 몰리는
수십 건(`/filters`·`/users/{id}`·`/cards`·상세)을 지연 없이 통과시킨다. **정상 사용자는 429 를
볼 일이 없다** — 보인다면 클라이언트가 루프를 돌고 있는 것이다. `POST /users` 만 훨씬 좁은
이유는 id 발급이 `state/users.jsonl` 을 영구히 키우고 그만큼 재시작 복원이 느려지기 때문이다.

CSP 는 **HTML 응답에만** 붙는다(`map $sent_http_content_type`). 화면 셋은 빌드 없는 단일
HTML 이라 인라인 스크립트·스타일을 쓰고(`'unsafe-inline'`), `/docs` 는 Swagger UI 를
`cdn.jsdelivr.net` 에서 받고, 카드 사진은 `*.kakaocdn.net`·`*.daumcdn.net` 에서 **http 로**
온다 — 정책이 그 셋만 허용한다. 화면에 iframe 이 없어 `X-Frame-Options` 는 `DENY` 다.

### 관리자 비밀번호

- 해시: `nginx/.htpasswd` (apr1, 사용자 `admin`). **커밋되지 않는다** — `.gitignore`·`.dockerignore`
  둘 다에 있고, 견본만 `nginx/.htpasswd.example` 로 커밋된다.
- 평문: **`/home/ubuntu/matchuri-secrets/admin-basic-auth.txt`** (디렉토리 700 · 파일 600).
  여기 말고는 어디에도 없다.
- 파일 소유자가 `101:101` · 모드 600 인 이유: 컨테이너 안 nginx **워커가 uid 101** 로 요청마다
  이 파일을 읽는다. 호스트 사용자(uid 1000) 소유 + 600 이면 워커가 못 읽어 `/admin` 이 **500**
  이 되고 에러 로그에 `.htpasswd … Permission denied` 가 남는다.

바꾸기 — 재배포 없이 reload 로 끝난다.

```bash
cd ~/matchuri-be
PW="$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | cut -c1-28)"
printf 'admin:%s\n' "$(openssl passwd -apr1 "$PW")" > nginx/.htpasswd
sudo chown 101:101 nginx/.htpasswd && sudo chmod 600 nginx/.htpasswd
printf 'user: admin\npassword: %s\n' "$PW" > ~/matchuri-secrets/admin-basic-auth.txt
chmod 600 ~/matchuri-secrets/admin-basic-auth.txt
docker compose -p backmock exec nginx nginx -t && docker compose -p backmock exec nginx nginx -s reload
curl -sS -o /dev/null -w '%{http_code}\n' -u "admin:$PW" http://localhost/admin    # 200 이어야 한다
```

### 지표와 대시보드 — Grafana

```
http://api.example.com/grafana/     대시보드 (자체 로그인, 비밀번호는 ~/matchuri-secrets/grafana-admin.txt)
```

Prometheus 가 15초마다 앱·호스트·nginx 를 모으고(30일 보관) Grafana 가 두 대시보드로 보여
줍니다 — `맛추리 운영`(트래픽·추천 인터리빙·사용자 행동·시스템)과 `맛추리 지연 상세`.
설정·대시보드·규칙은 전부 [`monitoring/`](monitoring/) 에 파일로 있고 화면에서 고친 것은
저장되지 않습니다. **공개 `/metrics` 는 404 입니다** — Prometheus 만 도커 네트워크 안에서
직접 긁습니다. 자세한 것은 [`monitoring/README.md`](monitoring/README.md).

### 로그 보기

엣지와 앱이 **둘 다 한 줄 JSON** 으로 찍고 `request_id` 로 이어진다(엣지가 만들거나 클라이언트
값을 이어받아 `X-Request-ID` 로 넘긴다). 컨테이너 로그로만 나가고 파일로 쌓지 않는다 —
컨테이너당 50MB × 5개가 상한이다(`logging.options`).

```bash
# 접두사(`nginx-1  | `)를 빼야 jq 로 넘길 수 있다. error_log 는 JSON 이 아니라 그대로 흘린다.
docker compose -p backmock logs nginx --since 10m --no-log-prefix | jq -R 'fromjson? // .'

# 실패한 요청만
docker compose -p backmock logs nginx --since 1h --no-log-prefix \
  | jq -Rc 'fromjson? // empty | select(.status>=400) | {ts,status,uri,remote_addr,limit_req_status}'

# 레이트 리밋에 걸린 IP 순위 (REJECTED = 429 를 받은 것, DELAYED = 문턱에 닿은 것)
docker compose -p backmock logs nginx --no-log-prefix \
  | jq -Rr 'fromjson? // empty | select(.limit_req_status=="REJECTED") | .remote_addr' | sort | uniq -c | sort -rn

# 한 요청을 엣지와 앱 양쪽에서 본다
docker compose -p backmock logs --no-log-prefix | grep <request_id>
```

### 배포할 때 걸릴 수 있는 것

- `nginx` 는 `app` 이 **healthy** 가 된 뒤에 뜬다(`depends_on: condition: service_healthy`).
  healthcheck 는 `GET /healthz` 이므로 그 경로가 없는 옛 이미지로는 영원히 healthy 가 되지
  않는다 — **반드시 `--build` 로 새 이미지를 올릴 것.**
- 이 nginx 가 `/jenkins` 도 프록시한다. 앱이 healthy 가 못 되면 Jenkins 화면도 같이 안 열린다.
  그때는 `docker compose -p backmock logs app` 을 먼저 본다.
- 배포 대기는 이미지 빌드 외에 카탈로그 준비와 학습 상태 복원을 포함한다. 앱 로그의
  `startup.catalog`·`startup.artifacts`·`state.restore`의 `ms`로 구간을 나눠 확인한다.
  `/healthz`는 프로세스 생존, `/readyz`는 복원 완료 여부다. Jenkins는 `/readyz`를 확인한다.
- 학습 스냅샷이 유효하면 저장 시점 이후 기록만 재생한다. 학습 동작이나 데이터가 바뀌면
  전체 재생이 필요하다. 복원 직후 스냅샷을 저장하고 이후 기본 300초마다 갱신한다
  (`MATCHURI_SNAPSHOT_EVERY=0`이면 자동 저장 중지). 캐시 검증을 끄거나 준비 판정을
  앞당겨 배포 시간을 줄이지 않는다.
- 리뷰 표현력 점수는 `state/review-ranks.cache`에 저장한다. 두 리뷰 원본의 실제 바이트와
  채점 코드의 SHA256이 같을 때만 재사용하고, 동점 정렬은 매번 기존 규칙대로 수행한다.
  캐시가 없거나 무효이면 첫 기동에서 전수 채점한다. 파일 손상·쓰기 실패 시에도 원래
  계산으로 돌아가며, 사용자 기록과 별개여서 캐시 파일만 지워도 다시 만들 수 있다.

## 알려진 한계

- **DB 는 아직 없습니다.** 대신 `state/` 에 추가 전용 로그로 남깁니다
  (`users.jsonl`, `preferences.jsonl`). 취향 모델은 선호 표현을 순서대로 접은 결과라
  재시작 때 검증된 모델 스냅샷과 이후 로그를 함께 읽고, 스냅샷이 없거나 호환되지 않으면
  전체 로그를 재생해 복원합니다.
  컨테이너에서는 `state` 볼륨이라 재배포해도 살아남습니다.
- **메뉴 가격은 56%만 있습니다.** 카카오가 나머지를 `-1`(변동/미표기)·`0` 으로 주는데,
  값이 아니라 결측이라 `null` 로 바꿔 내보냅니다.
- **메뉴 이미지 12%.** 가게가 등록한 것만 옵니다. 없으면 카드의 `images`(가게 사진 5장)를
  쓰세요.
- **사진이 원본 크기(170KB~1.5MB)입니다.** CDN 썸네일 파라미터가 먹지 않아
  (`?width=`, `?type=w400` 전부 무시) 줄이려면 리사이즈 프록시가 필요합니다.
- **추천 후보는 2,216곳**입니다(전체 2,795곳). 사진이나 리뷰가 없는 가게는 카드로 돌리지
  않습니다. `GET /cards/{id}` 로는 전부 조회됩니다.
