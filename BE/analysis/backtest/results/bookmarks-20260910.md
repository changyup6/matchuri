# 찜(bookmark) — 실서버 확인 (2026-09-10)

임시 `MATCHURI_STATE` · 포트 8017 · interleave 로 띄워 담기 두 번(멱등) → 목록 → 빼기 →
목록을 부른 기록이다. 찜은 **좋아요가 아니다** — 취향 모델·`shown`·노출 로그를 건드리지
않고, 담은 카드도 추천에 다시 나올 수 있다(`tests/test_bookmarks.py` 가 호출 전후의
세션과 기록 파일을 통째로 비교해 잠근다).

#### `PUT /users/{id}/bookmarks/2018` — 담는다 (퍼그피자하우스 강남점)

```console
$ curl -s -X PUT http://127.0.0.1:8017/users/u_460df8b307d5/bookmarks/2018
{
 "card_id": 2018,
 "bookmarked": true,
 "at": "2026-09-10T16:52:07+09:00",
 "count": 1
}
```

#### 같은 카드를 한 번 더 — **멱등**

```console
$ curl -s -X PUT http://127.0.0.1:8017/users/u_460df8b307d5/bookmarks/2018
{
 "card_id": 2018,
 "bookmarked": true,
 "at": "2026-09-10T16:52:07+09:00",
 "count": 1
}
```

응답이 처음과 같다(`at` 도 그대로): True. 담은 시각이 다시 누른 시각으로 바뀌면 목록 순서가 사용자가 기억하는 순서와 달라진다.

#### 다른 카드도 담는다 (고우가 역삼역점)

```console
$ curl -s -X PUT http://127.0.0.1:8017/users/u_460df8b307d5/bookmarks/65
{
 "card_id": 65,
 "bookmarked": true,
 "at": "2026-09-10T16:52:07+09:00",
 "count": 2
}
```

#### `GET /users/{id}/bookmarks` — 최근에 담은 순

```console
$ curl -s -X GET http://127.0.0.1:8017/users/u_460df8b307d5/bookmarks
[
 {
  "card_id": 65,
  "name": "고우가 역삼역점",
  "category_group": "한식",
  "bookmarked": true,
  "bookmarked_at": "2026-09-10T16:52:07+09:00",
  "distance_m": 64
 },
 {
  "card_id": 2018,
  "name": "퍼그피자하우스 강남점",
  "category_group": "양식",
  "bookmarked": true,
  "bookmarked_at": "2026-09-10T16:52:07+09:00",
  "distance_m": 852
 }
]
```

카드 모양은 `GET /cards` 와 같고(`store`·`image`·`images` 포함, 여기서는 읽기 쉽게 몇 필드만 남겼다) `bookmarked_at`·`distance_m` 이 더 붙는다. 거리는 **저장된 위치**(역삼역)에서 잰 값이다.

#### `DELETE /users/{id}/bookmarks/{card_id}` — 뺀다

```console
$ curl -s -X DELETE http://127.0.0.1:8017/users/u_460df8b307d5/bookmarks/2018
{
 "card_id": 2018,
 "bookmarked": false,
 "at": null,
 "count": 1
}
```

#### 없는 것을 또 빼도 200

```console
$ curl -s -X DELETE http://127.0.0.1:8017/users/u_460df8b307d5/bookmarks/2018
{
 "card_id": 2018,
 "bookmarked": false,
 "at": null,
 "count": 1
}
```

이미 원하는 상태이므로 실패가 아니다(멱등).

#### 다시 목록

```console
$ curl -s -X GET http://127.0.0.1:8017/users/u_460df8b307d5/bookmarks
[
 {
  "card_id": 65,
  "name": "고우가 역삼역점",
  "category_group": "한식",
  "bookmarked": true,
  "bookmarked_at": "2026-09-10T16:52:07+09:00",
  "distance_m": 64
 }
]
```

#### `GET /users/{id}` — 찜 수가 함께 온다

```console
$ curl -s -X GET http://127.0.0.1:8017/users/u_460df8b307d5
{
 "user_id": "u_460df8b307d5",
 "name": "찜검증",
 "location": {
  "lat": 37.5006,
  "lon": 127.0366,
  "at": "2026-09-10T16:52:07+09:00"
 },
 "filter": null,
 "registered": true,
 "swipes": 0,
 "likes": 0,
 "bookmarks": 1
}
```

#### `GET /cards/{card_id}?user_id=…` — 카드에도 `bookmarked` 가 붙는다

```console
$ curl -s -X GET 'http://127.0.0.1:8017/cards/65?user_id=u_460df8b307d5'
{
 "card_id": 65,
 "bookmarked": true,
 "distance_m": 64,
 "store": {
  "name": "고우가 역삼역점",
  "category_group": "한식"
 },
 "menus": "4개"
}
```

#### `user_id` 없이 부르면 `bookmarked: false`

```console
$ curl -s -X GET http://127.0.0.1:8017/cards/65
{
 "card_id": 65,
 "bookmarked": false
}
```


## 에러·경계

| 요청 | 코드 |
|---|---|
| `PUT /users/{id}/bookmarks/999999999` (없는 카드) | 404 |
| `PUT /users/u%20id/bookmarks/65` (잘못된 `user_id`) | 422 |
| `GET /users/anon_none/bookmarks` (찜한 적 없는 사용자) | 200 — 빈 배열 `[]` |

## 로그와 지표

```json
{"ts": "2026-09-10T16:52:07+09:00", "level": "INFO", "event": "bookmark.add", "user_id": "u_460df8b307d5", "card_id": 2018, "store_id": "382622582", "count": 1, "created": true, "request_id": "30b48d1346b748c2a89f28fb6cbfcd80"}
{"ts": "2026-09-10T16:52:07+09:00", "level": "INFO", "event": "bookmark.add", "user_id": "u_460df8b307d5", "card_id": 2018, "store_id": "382622582", "count": 1, "created": false, "request_id": "c93f2184d11e4b3f932b14a8389160c5"}
{"ts": "2026-09-10T16:52:07+09:00", "level": "INFO", "event": "bookmark.add", "user_id": "u_460df8b307d5", "card_id": 65, "store_id": "1050527540", "count": 2, "created": true, "request_id": "0215d78ae48f49d39957b3d5ef7ce81b"}
{"ts": "2026-09-10T16:52:07+09:00", "level": "INFO", "event": "bookmark.remove", "user_id": "u_460df8b307d5", "card_id": 2018, "store_id": "382622582", "count": 1, "removed": true, "request_id": "cc15e1386ac04a5daefdceec0b5631ed"}
{"ts": "2026-09-10T16:52:07+09:00", "level": "INFO", "event": "bookmark.remove", "user_id": "u_460df8b307d5", "card_id": 2018, "store_id": "382622582", "count": 1, "removed": false, "request_id": "84fe793f232347099b345b15c4109790"}
```

```
matchuri_bookmarks_total{action="add"} 2
matchuri_bookmarks_total{action="remove"} 1
```

멱등 호출(`created: false`)은 지표를 올리지 않는다 — '찜이 **바뀐** 횟수' 를 세는 값이다.

## 영속화 — `state/bookmarks.jsonl`

```json
{"user_id": "u_460df8b307d5", "card_id": 2018, "action": "add", "at": "2026-09-10T16:52:07+09:00"}
{"user_id": "u_460df8b307d5", "card_id": 65, "action": "add", "at": "2026-09-10T16:52:07+09:00"}
{"user_id": "u_460df8b307d5", "card_id": 2018, "action": "remove", "at": "2026-09-10T16:52:07+09:00"}
```

담은 줄과 뺀 줄을 순서대로 쌓고, 기동 때 그대로 다시 먹인다(`bookmarks.restore()`).
**뺀 것도 남기는 이유**는 그 줄이 없으면 재시작이 지운 찜을 되살리기 때문이다.
멱등 호출은 줄을 만들지 않는다. 한 사람당 500개를 넘으면 가장 오래된 것부터 빠지고
그 사실도 `remove` 한 줄로 남는다(`event: "bookmark.evict"` 경고와 함께).
