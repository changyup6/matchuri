# 관측(로그·지표·헬스) — 실서버 확인 (2026-09-10)

앱 쪽 관측을 붙이고 임시 `MATCHURI_STATE` · 포트 8016 · interleave 로 띄워 평범한 흐름
한 번과 실패하는 요청 넷을 부른 기록이다. 로그는 stdout 으로 나가므로 아래는 `docker logs`
에서 보게 될 것과 같은 줄이다(값만 이 실행의 것이다).

```bash
cd server && MATCHURI_STATE=$(mktemp -d) uvicorn main:app --port 8016
curl -s -H 'X-Request-ID: demo-trace-1' 'http://127.0.0.1:8016/cards?user_id=…&limit=5'
```

## 기동 — 복원과 부팅

```json
{"ts": "2026-09-10T16:37:17+09:00", "level": "INFO", "event": "state.restore", "users": 0, "preferences": 0, "dir": "/tmp/tmp.jT9MoYtmPT", "enabled": true, "ms": 0.2, "request_id": null}
{"ts": "2026-09-10T16:37:17+09:00", "level": "INFO", "event": "boot", "version": "1.0.0", "policy": "interleave", "configured_policy": "interleave", "cards": 2795, "recommendable": 2216, "cors": [], "log_level": "INFO", "request_id": null}
```

## 요청 한 건 = 한 줄

```json
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "http", "method": "POST", "path": "/users", "route": "/users", "status": 200, "ms": 3.0, "client_ip": "127.0.0.1", "user_id": "u_10e06f0d7a79", "ua": "curl/8.5.0", "bytes_out": 121, "request_id": "0f6ea6e3eb4f49b3a1d5fca6bf42e6cb"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "http", "method": "PUT", "path": "/users/u_10e06f0d7a79/location", "route": "/users/{user_id}/location", "status": 200, "ms": 1.9, "client_ip": "127.0.0.1", "user_id": "u_10e06f0d7a79", "ua": "curl/8.5.0", "bytes_out": 180, "request_id": "756c115742884898ba847599eac975a6"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "http", "method": "PUT", "path": "/users/u_10e06f0d7a79/filter", "route": "/users/{user_id}/filter", "status": 200, "ms": 2.2, "client_ip": "127.0.0.1", "user_id": "u_10e06f0d7a79", "ua": "curl/8.5.0", "bytes_out": 259, "request_id": "562000a99df244279b8270c1c27eeeb2"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "http", "method": "GET", "path": "/cards", "route": "/cards", "status": 200, "ms": 80.7, "client_ip": "127.0.0.1", "user_id": "u_10e06f0d7a79", "ua": "curl/8.5.0", "bytes_out": 9762, "request_id": "demo-trace-1"}
```

`route` 가 경로 템플릿(`/users/{user_id}/location`)인 것이 요점이다 — 사용자 수만큼 줄
종류가 늘면 지표도 로그 집계도 못 쓴다. `X-Request-ID: demo-trace-1` 을 준 요청은 그 값을
그대로 쓰고 응답 헤더로도 돌려준다.

## 도메인 이벤트 — 무엇이 일어났나

```json
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "user.create", "user_id": "u_10e06f0d7a79", "named": true, "request_id": "0f6ea6e3eb4f49b3a1d5fca6bf42e6cb"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "user.location.set", "user_id": "u_10e06f0d7a79", "lat": 37.501, "lon": 127.037, "request_id": "756c115742884898ba847599eac975a6"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "user.filter.set", "user_id": "u_10e06f0d7a79", "cat": ["일식"], "tag": ["혼밥"], "within_m": 500, "request_id": "562000a99df244279b8270c1c27eeeb2"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "cards.serve", "user_id": "u_10e06f0d7a79", "n_requested": 5, "n_returned": 5, "policies": {"legacy": 3, "taste": 2}, "filter": {"cat": ["일식"], "tag": ["혼밥"], "within_m": 500}, "has_location": true, "ms": 78.6, "request_id": "demo-trace-1"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "pref.record", "user_id": "u_10e06f0d7a79", "card_id": 1944, "store_id": "314215514", "prefer": "like", "learned": ["legacy", "taste"], "pairs": 0, "swipes": 1, "request_id": "377fac260e8b4f44b296f61d939a286d"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "taste.view", "user_id": "u_10e06f0d7a79", "swipes": 1, "likes": 1, "region": "일식", "trail": 1, "drift": null, "request_id": "7064f59fa081461ab1f3d101ff8b3aa5"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "user.filter.clear", "user_id": "u_10e06f0d7a79", "request_id": "8aa370999c414ea6b89280c6816846a2"}
```

`cards.serve` 한 줄이면 '이 사람에게 필터가 무엇이 걸린 채로 몇 장이 나갔나' 를 알 수 있고,
`pref.record` 는 '어느 정책들이 배웠나(`learned`)' 까지 적는다. 위치는 소수 셋째 자리로
반올림해 남긴다(로그는 오래 남고 위치는 개인정보다).

## 실패한 요청도 사유가 남는다

```json
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "http.client_error", "status": 422, "path": "/cards", "detail": "user_id 는 영문·숫자와 . _ : - 만 쓸 수 있고 1~64자여야 합니다", "request_id": "024976c8a3184a54baadb79bf0795837"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "http.client_error", "status": 422, "path": "/users", "detail": ["body.name"], "request_id": "af6ad9606edf4641886836b32b148b42"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "http.client_error", "status": 422, "path": "/users/u_10e06f0d7a79/filter", "detail": "cat '초밥' 은 없는 값입니다. 허용: 한식, 카페, 일식, 술집, 양식, 중식, 간식, 분식, 기타", "request_id": "7219ec0cd7c64ee2a8fea1c4325f868c"}
{"ts": "2026-09-10T16:38:39+09:00", "level": "INFO", "event": "http.client_error", "status": 404, "path": "/cards/999999999", "detail": "card not found", "request_id": "8bc208b23f704a27918f94248cb5882a"}
```

| 요청 | 코드 |
|---|---|
| `GET /cards?user_id=u%20id` (공백 있는 id) | 422 |
| `POST /users` (이름 51자) | 422 |
| `PUT /users/{id}/filter` `{"cat":["초밥"]}` | 422 |
| `GET /cards/999999999` | 404 |

처리 못 한 예외는 `{"detail": "internal error", "request_id": …}` 만 나가고 traceback 은
`event: "error"` 줄로 로그에만 남는다(`tests/test_observe.py` 가 그 둘을 함께 잠근다).

## 요청 id 는 이상하면 새로 만든다

```console
$ curl -s -D - -o /dev/null http://127.0.0.1:8016/healthz -H 'X-Request-ID: 나쁜값'
HTTP/1.1 200 OK
x-request-id: e7e6b3606f66422a91d96ed46d6d4528
```

## `GET /readyz`

```json
{"ok": true, "data": true, "pool": true, "state_writable": true, "artifacts": true, "policy": "interleave", "cards": 2795, "recommendable": 2216, "reasons": []}
```

`state/` 는 탐침 파일을 실제로 한 번 열어 확인한다(권한 비트만 보면 읽기 전용 볼륨을 놓친다).
`artifacts: false` 여도 준비 상태다 — 그때는 legacy 정책으로 돈다.

## `GET /metrics` (발췌)

```
matchuri_http_requests_total{route="/cards",method="GET",status="200"} 2
matchuri_http_requests_total{route="/cards",method="GET",status="422"} 2
matchuri_http_requests_total{route="/cards/{card_id}",method="GET",status="200"} 2
matchuri_http_requests_total{route="/cards/{card_id}",method="GET",status="404"} 2
matchuri_http_requests_total{route="/healthz",method="GET",status="200"} 4
matchuri_http_requests_total{route="/metrics",method="GET",status="200"} 1
matchuri_http_request_seconds_bucket{route="/cards",le="0.005"} 2
matchuri_http_request_seconds_bucket{route="/cards",le="0.01"} 2
matchuri_http_request_seconds_bucket{route="/cards",le="0.025"} 2
matchuri_http_request_seconds_bucket{route="/cards",le="0.05"} 2
matchuri_http_request_seconds_bucket{route="/cards",le="0.1"} 3
matchuri_http_request_seconds_bucket{route="/cards",le="0.25"} 4
matchuri_http_request_seconds_bucket{route="/cards",le="0.5"} 4
matchuri_http_request_seconds_bucket{route="/cards",le="1.0"} 4
matchuri_http_request_seconds_bucket{route="/cards",le="2.5"} 4
matchuri_http_request_seconds_bucket{route="/cards",le="5.0"} 4
matchuri_http_request_seconds_bucket{route="/cards",le="+Inf"} 4
matchuri_http_request_seconds_sum{route="/cards"} 0.251019
matchuri_http_request_seconds_count{route="/cards"} 4
matchuri_preferences_total{prefer="like"} 2
matchuri_cards_served_total{policy="legacy"} 5
matchuri_cards_served_total{policy="taste"} 5
matchuri_users_total 2
matchuri_sessions 2
matchuri_state_prefs_total 2
matchuri_build_info{version="1.0.0",policy="interleave"} 1
```

의존성을 하나도 더하지 않고 프로세스 안에서 센다(재시작하면 0 — Prometheus 는 counter 의
리셋을 알아서 처리한다). 히스토그램 버킷은 5ms 부터인데, `/cards` 가 10ms 대이기 때문이다.

## 로그를 읽는 법

```bash
docker logs matchuri-be 2>&1 | jq -c 'select(.request_id=="demo-trace-1")'   # 사고 하나를 통째로
docker logs matchuri-be 2>&1 | jq -c 'select(.event=="http" and .ms>500)'    # 느린 요청
docker logs matchuri-be 2>&1 | jq -r 'select(.event=="error") | .traceback'  # 스택
docker logs matchuri-be 2>&1 | jq -c 'select(.event=="cards.serve") | {user_id, n_returned, filter}'
```

`MATCHURI_LOG_LEVEL=DEBUG` 면 `/healthz`·`/readyz`·`/metrics` 줄까지 나온다(기본은 안 나온다 —
헬스체크가 초당 한 번 오면 나머지가 그 안에 묻힌다).
