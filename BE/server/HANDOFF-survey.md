# 프론트 전달 — 길찾기·설문 API (2026-09-11, 방문 근거 2026-09-16 추가)

추천이 **실제로 맞았는지** 되묻는 기능이 백엔드에 들어갔다. 앱에서 붙여야 할 것만 모았다.
전체 API 명세는 [`API.md` 5.7 절](../../docs/API.md)에 있고, 이 문서는 **작업 지시서**다.

| | |
|---|---|
| 서버 | `http://api.example.com` — **배포 완료 (2026-09-14)**. 지금 바로 쓸 수 있다 |
| 문서 | `http://api.example.com/docs` → **`설문`** 태그 (`Try it out` 으로 직접 호출) |
| 추가된 API | 3개 (`/directions`, `/surveys/pending`, `/surveys`) |
| 앱 작업량 | 호출 3곳 + 배너 UI 1개 |
| 새 권한 | **없음** (위치 권한 추가로 필요 없다) |

---

## 왜 이걸 하나

스와이프 `like` 는 **카드를 보고 오른쪽으로 넘긴 것**일 뿐이다. 그 가게가 실제로 좋았는지는
알 수 없다. 그래서 추천 정확도를 재려면 가서 어땠는지 물어봐야 한다.

**GPS 로 방문을 판정하지 않는다.** 백그라운드 위치 추적이 필요하고, 역삼동처럼 건물이 밀집한
곳에서는 반경 100m 안에 가게가 수십 곳 들어와 어디에 갔는지 특정할 수 없다.

대신 **길찾기를 누른 순간**을 방문 의사로 본다. 사용자가 그 가게를 직접 지목한 순간이라
가게가 정확하고, 배터리도 권한도 쓰지 않는다. 방문 여부는 3시간 뒤에 직접 묻는다.

---

## 앱이 할 일 — 필수는 **1개**

2026-09-16부터 **`POST /users/{id}/surveys` 하나만 부르면 된다.** 서버가 길찾기 기록을
요구하지 않는다 — 방문 판단은 앱이 자기 위치와 `store.lat`·`store.lon` 으로 직접 하고,
결과만 설문에 실어 보낸다. 서버로 좌표가 갈 일은 없다.

```
필수    ③ POST /users/{id}/surveys          설문 (아래 ③절)
선택    ① POST /users/{id}/directions       길찾기 기록 — 퍼널 한 단계가 늘어난다
선택    ② GET  /users/{id}/surveys/pending  서버가 물어볼 목록을 만들어 준다
선택       사진 2개, 가게 상세의 user_reviews 표시
```

**①·②를 안 하면 무엇을 잃나.** 서버는 "길찾기를 눌렀는데 답하지 않은 사람" 을 볼 수
없다 — 응답률(`response_rate`)과 `directions` 단계가 비고, 정책별 비교는 `노출 → 설문`
으로 계산된다. 그 비교(legacy vs taste)는 그대로 나온다. 앱이 자기 목록을 들고 있으면
②도 필요 없다.

### ① (선택) 길찾기 누를 때 서버에 알린다

**어디에:** `FE/app/lib/widgets/directions_sheet.dart` 의 `_launch(MapApp app)` — `openDirections()` 를 부르는 그 자리 (82행 근처)

```dart
Future<void> _launch(MapApp app) async {
  setState(() => _launching = true);

  // 추가: 지도 앱으로 넘어가기 직전에 기록한다.
  // await 하지 않아도 된다 — 실패해도 사용자 흐름을 막지 않는다.
  RestaurantApi.recordDirections(cardId, app, _mode);   // fire-and-forget

  final ok = await openDirections(...);
  ...
}
```

`map_app` 과 `mode` 는 이미 앱에 있는 enum 을 문자열로 보내면 된다:

| Dart enum | 보낼 값 |
|---|---|
| `MapApp.naver` / `.kakao` / `.google` | `"naver"` / `"kakao"` / `"google"` |
| `DirectionsMode.walk` / `.transit` / `.car` | `"walk"` / `"transit"` / `"car"` |

**다른 값을 보내면 422** 다.

### ② (선택) 앱이 열릴 때 물어볼 게 있는지 확인한다

**어디에:** 앱 시작 직후 (`bootstrap_gate.dart` 나 홈 진입 시) — `user_id` 가 확보된 다음

```dart
final pending = await RestaurantApi.pendingSurveys(userId);
if (pending.isNotEmpty) {
  // 배너를 띄운다
}
```

빈 배열이면 아무것도 하지 않는다. **서버에 타이머가 없다** — 부른 그 순간 계산해서 답한다.
그래서 앱이 열릴 때 한 번 부르는 것으로 충분하다.

### ③ 배너에서 답한 것을 보낸다

```dart
await RestaurantApi.submitSurvey(userId, cardId,
    visited: true, satisfaction: 'good',
    review: {'taste': '…', 'price': '…', 'mood': '…'});   // 셋 다 필수
```

### ④ (선택) 방문 근거를 앱이 계산해서 설문에 함께 보낸다 (2026-09-16 추가)

**안 해도 위 셋은 그대로 동작한다.** 이걸 하면 "길찾기만 누르고 안 간 사람" 을 가릴 수 있다.

**새 API 는 없다. 위치를 서버로 보내지 않는다.** 앱이 이미 자기 위치를 들고 있고,
가게 좌표도 카드 응답에 있으니(`store.lat`·`store.lon`) 기기에서 계산해 결과만 설문에
얹으면 된다.

```dart
// ① 길찾기를 누른 가게와 시각을 로컬에 남긴다 (pending 의 card_id·at 과 같은 값)
// ② 앱이 열려 있는 동안 위치를 모은다 — 서버로 보내지 않는다
// ③ 설문을 보낼 때 계산해서 얹는다
final d = Geolocator.distanceBetween(pos.latitude, pos.longitude,
                                     store.lat!, store.lon!);
await RestaurantApi.submitSurvey(userId, cardId,
    visited: true, satisfaction: 'good',
    review: {'taste': '…', 'price': '…', 'mood': '…'},
    arrived: closest <= 100,     // 반경 100m 권장
    closestM: closest.round(),   // **꼭 같이 보낼 것** (아래 이유)
    dwellMin: dwell);            // 그 반경 안에 머문 분. 한 번만 잡았으면 0
```

- **`store.lat`·`store.lon` 을 쓰라.** 주소를 `geocoding` 으로 변환하던 것을 대체한다 —
  `directions_launcher.dart` 주석대로 `지하 1층` 접미사에서 자주 실패한다. 서버는 정확한
  좌표를 갖고 있었고 이제 응답에 있다(목록·상세 둘 다). **지도 딥링크에도 이걸 쓰면
  길찾기 실패가 같이 줄어든다**
- **모르면 `arrived` 를 아예 빼라.** 위치를 못 본 것과 안 간 것은 다르다. `false` 로
  보내면 위치 권한을 거부한 사용자가 전부 "안 갔다" 로 기록된다
- **`closest_m` 을 꼭 같이 보내라.** `arrived` 만 오면 '100m 가 맞나' 를 나중에 데이터로
  확인할 수 없다. 거리는 스칼라라서 위치가 새지 않는다 — 궤적은 기기에 남고 서버는
  "얼마나 가까웠나" 만 안다
- 길찾기(`pending` 의 `at`)로부터 **6시간** 안의 위치만 보라. 그 이전 것도 보지 말 것
- OS 정확도가 **200m** 보다 나쁜 값은 쓰지 말 것(셀 타워만 잡힌 값이다)
- **서버는 이 값으로 설문을 막지 않는다.** 거를지는 앱이 정한다 —
  권장은 `arrived != false` 만 배너에 띄우기(모르는 건 물어보고, 멀었던 것만 빼기)

백그라운드 위치까지 켜면(`location` 패키지의 `enableBackgroundMode`) 걸어가는 구간이
다 들어와 머문 시간이 정확해진다. 다만 iOS 는 `UIBackgroundModes: location` 이 필요하고
파란 상태바가 계속 뜨며, Android 14+ 는 `FOREGROUND_SERVICE_LOCATION` 선언과 지속
알림이 붙는다. **급하지 않다 — 식당에서 앱을 한 번이라도 열면 도착 판정은 된다.**

---

## API 3개

### `POST /users/{user_id}/directions`

요청
```json
{"card_id": 2018, "map_app": "kakao", "mode": "transit"}
```

응답 `200`
```json
{"card_id": 2018,
 "at": "2026-09-11T13:55:47+09:00",
 "survey_at": "2026-09-11T16:55:47+09:00",
 "count": 1}
```

- `survey_at` = `at` + 3시간. **설문을 권하는 시점**이다 — 서버가 이 시각으로 막지는
  않는다(`pending` 은 기본적으로 전부 준다).
  **로컬 알림(flutter_local_notifications) 예약에 쓸 수 있다** — 서버 푸시는 아직 없다
- 없는 `card_id` → **404**
- **멱등이 아니다.** 같은 가게를 다시 누르면 시각이 갱신되고, 설문도 최신 길찾기 기준으로
  다시 묻는다 (어제 갔고 오늘 또 갔으면 오늘을 물어야 한다)

### `GET /users/{user_id}/surveys/pending?after_hours=0`

**언제 물을지는 앱이 정한다.** 서버는 시간으로 거르지 않는다 — 아직 답하지 않은 길찾기를
전부 준다. 각 항목의 `hours_ago` 를 보고 배너를 띄울지 앱이 판단하라 (권장은 **3시간**
이후이고, 그 시각이 `POST /directions` 응답의 `survey_at` 이다).

서버에서 걸러 받고 싶으면 `?after_hours=3` 을 붙인다 (기본 `0` = 전부, 범위 0~168).

응답 `200` — **최근 순, 최대 10개**
```json
[{"card_id": 2018,
  "store_id": "382622582",
  "name": "퍼그피자하우스 강남점",
  "category": "피자",
  "image": null,
  "at": "2026-09-11T09:55:47+09:00",
  "hours_ago": 4}]
```

- 없으면 404 가 아니라 **빈 배열 `[]`**
- **방금 누른 길찾기도 나온다** (`hours_ago: 0`). 개발 중 흐름 확인이 바로 된다
- **일주일** 지난 길찾기는 안 나온다 (기억이 없다)
- `hours_ago` → 배너 문구에 쓴다: "4시간 전에 퍼그피자하우스 강남점 가셨나요?"
- `image` 는 **`null` 일 수 있다** (사진 없는 가게). 플레이스홀더를 준비할 것
- `store_id` : 로컬 DB 에 오래 보관할 것이 있으면 `card_id` 가 아니라 **이걸** 키로 쓰라.
  데이터가 갱신되면 `card_id` 는 다시 매겨진다

### `POST /users/{user_id}/surveys`

방문한 경우 — **`satisfaction` 과 `review` 가 둘 다 필수다**
```json
{"card_id": 2018, "visited": true, "satisfaction": "good",
 "review": {"taste": "고등어초회가 신선했어요",
            "price": "2인 4만원, 적당했어요",
            "mood": "조용하고 좌석 간격이 넓어요"},
 "arrived": true, "closest_m": 34, "dwell_min": 41}
```
> `arrived`·`closest_m`·`dwell_min` 은 **선택**이고 앱이 계산한 방문 근거다(④ 절).

방문하지 않은 경우
```json
{"card_id": 741, "visited": false, "not_visited_reason": "changed_mind"}
```

응답 `200`
```json
{"ok": true, "card_id": 2018, "recorded": true, "remaining": 0}
```

- `recorded: false` → 같은 길찾기에 **이미 답했다**. 실패가 아니다. 조용히 배너를 닫으면 된다
- `remaining` → 아직 답하지 않은 후보 수. `0` 이면 배너를 닫는다
- **길찾기 기록은 필요 없다**(2026-09-16 변경). 없는 `card_id` 만 **404** `no such card`
- 한 번에 **한 가게**만. 여러 곳이면 각각 따로 부른다
- `satisfaction` 이나 `review` 가 없으면 **422**. 후기 한 줄이라도 비면(공백만 보내도)
  **422** 다
- **후기 세 줄은 그대로 가게 리뷰가 된다** — `GET /cards/{card_id}` 의 `user_reviews`
  로 다른 사용자에게 보인다(아래 절)

---

## 설문 문항 — 값 정의

**1단계 (필수)**

| 필드 | 값 |
|---|---|
| `visited` | `true` / `false` |

**1-A. `visited: false` 인 경우 (필수)**

| `not_visited_reason` | 화면 문구 |
|---|---|
| `far` | 멀어서 |
| `no_time` | 시간이 없어서 |
| `changed_mind` | 마음이 바뀌어서 |
| `went_elsewhere` | 다른 곳에 가서 |

**2단계. `visited: true` 인 경우 (필수) — 이게 핵심 지표다**

| `satisfaction` | 화면 |
|---|---|
| `bad` | 😞 |
| `soso` | 😐 |
| `good` | 😊 |

> ⚠️ **`soso` 는 백엔드 집계에서 만족으로 세지 않는다.** `satisfied_rate` 의 분자는
> `good` 만이고 분모는 `good + soso + bad` 다. 화면상 😐 는 "중간" 으로 보이지만
> 지표에서는 만족이 아니다 — 넣으면 지표가 무뎌져 정책 비교를 못 한다.
> **프론트가 바꿀 것은 없다.** 나중에 만족도 숫자를 볼 때 정의를 알고 보라는 뜻이다.

**3단계 (필수)** — 후기 세 줄. **점수가 아니라 글이다.**

| 필드 | 화면 문구 예 | 제한 |
|---|---|---|
| `review.taste` | 맛은 어땠나요? | 1~200자 |
| `review.price` | 가격은 어땠나요? | 1~200자 |
| `review.mood` | 분위기는 어땠나요? | 1~200자 |

- **세 칸 다 채워야 보낼 수 있다.** 앞뒤 공백은 서버가 자르고, 자르고 나서 비면 **422**
  다. 공백만 보내면 통과하는 구조였으면 '필수' 가 이름뿐이 되고 빈 줄이 가게 화면에
  그대로 나간다 — **앱에서도 전송 버튼을 잠그는 게 낫다**
- 위생·대기 문항은 없어졌다. 한 번 방문으로 판단하기 어렵고, 문항이 늘면 응답률이 떨어진다
- 1~3 점수 문항(`taste: 3` 꼴)은 **더 이상 받지 않는다.** 같은 이름이 글로 바뀌었다

### `user_reviews` — 설문이 가게 리뷰가 된다

`GET /cards/{card_id}`(상세)에만 붙는다. 목록에는 없다.

```json
"user_reviews": [
  {"name": "민수", "satisfaction": "good",
   "taste": "고등어초회가 신선했어요",
   "price": "2인 4만원, 적당했어요",
   "mood": "조용하고 좌석 간격이 넓어요",
   "images": ["/media/9f/9f2c….jpg"],
   "at": "2026-09-16T18:20:11+09:00"}
]
```

- **`store.reviews`(카카오 수집분)와 다른 배열이다.** 화면에서도 나눠서 보여 줄 것 —
  "방문자 후기" 같은 섹션으로 분리하면 된다
- `store.score`·`store.review_count` 는 **움직이지 않는다**. 추천 점수가 그 둘을
  읽으므로 후기 한두 건이 순위를 흔들면 안 된다
- `name` 은 작성자 이름(이름 없이 발급받았으면 `null`). **`user_id` 는 안 준다**
- `images` 는 설문에 올린 사진. 지우면 여기서도 사라진다
- **사람당 가게당 하나**, 최근 순

> **방문했다면 2·3단계가 모두 필수다**(2026-09-16 변경). 그래서 문항을 셋으로
> 줄였다 — 위생·대기를 지웠다. 필수로 바꾼 이유는 선택으로 두면 대부분 비워서 오고,
> 그러면 추천이 맞았는지도 모르고 가게 리뷰도 안 쌓이기 때문이다.

---

## 배너 흐름 (합의된 UX)

```
"가게를 방문했나요?"     ← pending 목록을 리스트로
        │
        ▼ 사용자가 하나를 고른다
1단계  방문하셨나요?              [예]  [아니오]
        │                            │
        │                            ▼
        │                        왜?  멀어서 / 시간없음 / 마음바뀜 / 다른곳
        ▼
2단계  추천이 취향에 맞았나요?    😞  😐  😊          ← 필수
        │
        ▼
3단계  후기 세 줄                                   ← 필수
       맛은?      [                              ]
       가격은?    [                              ]
       분위기는?  [                              ]
       (+ 사진 최대 3장, 선택)
        │
        ▼
     이 세 줄이 그대로 가게 화면의 "방문자 후기" 가 된다
```

- 목록에서 **하나만** 고르게 한다. 여러 개를 한 화면에서 받으면 응답률이 떨어진다
- 답하고 나면 `remaining` 을 보고 다음 가게를 물을지 결정한다
- 사용자가 배너를 닫으면 다음에 앱을 열 때 다시 뜬다 (일주일까지)

---

## 타입 생성

`/openapi.json` 에 5개 스키마가 추가됐다 — `DirectionIn`, `DirectionOut`,
`PendingSurveyOut`, `SurveyIn`, `SurveyOut`.

`map_app`·`mode`·`satisfaction`·`not_visited_reason` 은 **`enum` 으로 나오므로**
문자열을 손으로 적지 말고 생성된 타입을 쓰라.

```bash
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o api.d.ts
```

Flutter 는 코드 생성을 쓰지 않으니 위 표의 값을 그대로 상수로 두면 된다.

---

## 지금 없는 것 — 푸시 알림

앱에 푸시 패키지가 없다 (`firebase_messaging` 미설치). 그래서 **서버가 "3시간 됐다"고
먼저 알려줄 수 없다.**

**대안 둘.**

1. **인앱 배너** (권장, 지금 가능) — 앱이 열릴 때 `pending` 을 부르고 있으면 띄운다.
   추가 작업이 없다
2. **로컬 알림** — `POST /directions` 응답의 `survey_at` 으로 `flutter_local_notifications`
   예약. 앱을 안 열어도 알림이 간다. 푸시 인프라(FCM/APNs) 없이 된다

FCM 을 붙여도 **서버 API 는 그대로 쓴다** — `pending` 함수를 재사용한다.

---

## 테스트 방법

**실서버**로 바로 하면 된다 — http://api.example.com/docs → **`설문`** 태그.
`Try it out` → `Execute` 로 브라우저에서 호출된다 (**Postman 불필요**).

로컬로 띄우고 싶으면:

```bash
cd BE/server
pip install -r requirements.txt
uvicorn main:app --port 8000
```

→ `http://127.0.0.1:8000/docs`

**기다릴 필요가 없다.** `POST /directions` 직후에 `GET /surveys/pending` 을 부르면
방금 누른 가게가 `hours_ago: 0` 으로 바로 나온다. 서버가 시간으로 막지 않는다.

3시간 필터를 시험하고 싶으면 `?after_hours=3` 을 붙여 보면 된다(빈 배열이 나온다).

---

## 배포 상태 — 끝났다

`master` 에 머지됐고 **실서버에 반영됐다**. 아래 4개가 지금 살아 있다.

```
POST /users/{user_id}/directions
GET  /users/{user_id}/surveys/pending
POST /users/{user_id}/surveys
GET  /admin/api/surveys            (관리자, Basic 인증)
```

확인:

```bash
curl http://api.example.com/openapi.json | grep directions
```

`master` 에 머지하면 **2분 내 자동 배포**된다(Jenkins SCM 폴링). 이후 API 가 바뀌면
이 문서와 `API.md` 를 함께 갱신한다.

---

## 주의사항 정리

1. **`image` 가 `null` 일 수 있다** — 플레이스홀더 필요
2. **`recorded: false` 는 실패가 아니다** — 이미 답한 것이다. 에러 토스트를 띄우지 말 것
3. **`hours_ago` 를 그대로 쓰라** — `at` 으로 직접 계산하면 기기 시계 오차와
   **타임존 오프셋**이 섞인다. 파싱해야 한다면 문자열 끝의 `+09:00` 을 반드시 반영할 것
4. **`store_id` 를 저장하라** — `card_id` 는 데이터 갱신 시 바뀐다
5. **`POST /directions` 는 await 하지 않아도 된다** — 지도 앱 전환을 늦추지 말 것
6. **길찾기·설문은 추천을 바꾸지 않는다** — 길찾기한 가게가 카드에 다시 나올 수 있다.
   버그가 아니다 (좋아요와 달리 '이미 본 카드' 로 처리하지 않는다)
7. **`arrived` 를 모를 때는 필드를 빼라** — `false` 로 보내면 위치 권한을 거부한
   사용자가 전부 "안 갔다" 로 기록된다. 서버는 값 있음 / `false` / 없음 셋을 구분한다
8. **`store.lat`·`store.lon` 을 쓰라** — 주소 지오코딩은 `지하 1층` 접미사에서 자주
   실패한다. 지도 딥링크와 도착 판정 둘 다 이 값으로 하면 된다
9. **위치를 서버로 보내지 않는다** — 도착 판정은 앱에서 하고 결과(숫자)만 설문에 얹는다
