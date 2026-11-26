# 필터 · 사용자 위치 — 실서버 확인 (2026-09-10)

`server/` 를 실데이터로 띄워(`MATCHURI_STATE` 는 임시 디렉터리, 포트 8012,
`MATCHURI_POLICY` 기본값 = interleave · 서빙 산출물 로드됨: 풀 2,216곳) `GET /filters`,
`PUT /users/{id}/location`, `GET /cards` 의 필터 3종과 `distance_m`, 에러 경우를 부른 전사다.
카드 응답은 사람이 읽을 필드만 남기고(가게 이름·`cat2`·태그·`distance_m`·`policy`) 잘랐고,
**필터 준수는 응답의 `card_id` 로 가게 레코드를 다시 찾아 확인한 문장**이 각 블록 아래에 있다.

### `GET /filters` — 필터 UI 어휘 (후보 풀 2,216곳 기준)

```console
$ curl -s http://127.0.0.1:8012/filters
{
 "cats": [
  "한식 710",
  "카페 460",
  "일식 188",
  "술집 187",
  "양식 143",
  "중식 121",
  "간식 93",
  "분식 87",
  "치킨 53",
  "샐러드 50",
  "… 총 19종"
 ],
 "tags": [
  "가성비 1778",
  "친절한 1305",
  "양많은 940",
  "맛 927",
  "친절 842",
  "점심식사 743",
  "혼밥 743",
  "단체모임 709",
  "분위기 666",
  "야식 531",
  "회식장소 453",
  "감성적 379",
  "… 상위 40개"
 ],
 "within_m": [
  300,
  500,
  1000,
  2000
 ]
}
```

업종 19종, 태그 40개(상위 40 컷), 권장 반경 4개.

### `POST /users` — 발급 응답에 `location: null`

```console
$ curl -s -X POST http://127.0.0.1:8012/users -H 'Content-Type: application/json' -d '{"name":"필터검증"}'
{
 "user_id": "u_6b30fdefbe1e",
 "name": "필터검증",
 "created_at": "2026-09-10T10:39:37+09:00",
 "location": null
}
```

### `PUT /users/{id}/location` — 역삼역을 기억시킨다

```console
$ curl -s -X PUT http://127.0.0.1:8012/users/u_6b30fdefbe1e/location -H 'Content-Type: application/json' -d '{"lat": 37.5006, "lon": 127.0366}'
{
 "user_id": "u_6b30fdefbe1e",
 "name": "필터검증",
 "created_at": "2026-09-10T10:39:37+09:00",
 "location": {
  "lat": 37.5006,
  "lon": 127.0366,
  "at": "2026-09-10T10:39:37+09:00"
 }
}
```

### `GET /cards?cat=일식&within_m=500` — 업종 ∧ 거리 (위치는 저장된 것)

```console
$ curl -s -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode 'cat=일식' --data-urlencode within_m=500 --data-urlencode limit=10
[
 {
  "card_id": 1944,
  "name": "치솟",
  "cat2": "일식",
  "distance_m": 344,
  "policy": "legacy",
  "tags": [
   "가성비",
   "건강한",
   "데이트",
   "술한잔",
   "양많은",
   "점심식사"
  ]
 },
 {
  "card_id": 1444,
  "name": "나이스샤워 역삼점",
  "cat2": "일식",
  "distance_m": 467,
  "policy": "taste",
  "tags": [
   "가성비",
   "강남역맛집",
   "강남역점심",
   "강남역혼밥",
   "단체모임",
   "심야주점"
  ]
 },
 {
  "card_id": 1030,
  "name": "카츠쇼신 강남점",
  "cat2": "일식",
  "distance_m": 381,
  "policy": "legacy",
  "tags": [
   "가성비",
   "단체모임",
   "맛",
   "분위기",
   "주차",
   "친절"
  ]
 },
 {
  "card_id": 1108,
  "name": "역삼동참치",
  "cat2": "일식",
  "distance_m": 260,
  "policy": "taste",
  "tags": [
   "가성비",
   "단체모임",
   "데이트",
   "양많은",
   "조용한",
   "참치무한리필"
  ]
 },
 {
  "card_id": 1794,
  "name": "스시쇼우키",
  "cat2": "일식",
  "distance_m": 456,
  "policy": "legacy",
  "tags": [
   "가성비",
   "양많은",
   "점심식사",
   "친절한"
  ]
 },
 {
  "card_id": 1186,
  "name": "무르",
  "cat2": "일식",
  "distance_m": 268,
  "policy": "taste",
  "tags": [
   "가성비",
   "감성적",
   "단체모임",
   "데이트",
   "디저트",
   "소개팅"
  ]
 },
 {
  "card_id": 100,
  "name": "조수사",
  "cat2": "일식",
  "distance_m": 381,
  "policy": "legacy",
  "tags": [
   "가성비",
   "가족모임",
   "단체모임",
   "점심식사",
   "조용한",
   "초복메뉴추천"
  ]
 },
 {
  "card_id": 1590,
  "name": "갓포돈",
  "cat2": "일식",
  "distance_m": 142,
  "policy": "taste",
  "tags": [
   "가성비",
   "감성적",
   "경양식요리",
   "맛",
   "분위기",
   "양많은"
  ]
 },
 {
  "card_id": 1902,
  "name": "오사무식당 역삼점",
  "cat2": "일식",
  "distance_m": 180,
  "policy": "legacy",
  "tags": [
   "가성비",
   "강남",
   "단체모임",
   "덮밥",
   "맛",
   "분위기"
  ]
 },
 {
  "card_id": 1833,
  "name": "호타루",
  "cat2": "일식",
  "distance_m": 181,
  "policy": "taste",
  "tags": [
   "가성비",
   "맛",
   "분위기",
   "점심식사",
   "친절",
   "혼밥"
  ]
 }
]
```

카드 10장. cat2 = ['일식'] → 요청 ['일식'] 만족: True distance_m = [344, 467, 381, 260, 456, 268, 381, 142, 180, 181] → 최대 467m ≤ 500m: True

### `GET /cards?tag=혼밥,가성비` — 태그는 AND (둘 다 가진 곳만)

```console
$ curl -s -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode 'tag=혼밥,가성비' --data-urlencode limit=10
[
 {
  "card_id": 298,
  "name": "크라이치즈버거 강남점",
  "cat2": "양식",
  "distance_m": 581,
  "policy": "taste",
  "tags": [
   "가성비",
   "양많은",
   "친절한",
   "혼밥"
  ]
 },
 {
  "card_id": 65,
  "name": "고우가 역삼역점",
  "cat2": "한식",
  "distance_m": 64,
  "policy": "legacy",
  "tags": [
   "가성비",
   "감성적",
   "강남오마카세",
   "강남한우",
   "단체모임",
   "데이트"
  ]
 },
 {
  "card_id": 2701,
  "name": "돼봉삼겹살 신논현점",
  "cat2": "한식",
  "distance_m": 971,
  "policy": "taste",
  "tags": [
   "가성비",
   "고기맛집",
   "구워주는집",
   "넓은",
   "단체룸",
   "단체모임"
  ]
 },
 {
  "card_id": 577,
  "name": "춘만두",
  "cat2": "분식",
  "distance_m": 704,
  "policy": "legacy",
  "tags": [
   "가성비",
   "강남만두",
   "강남손만두",
   "서울만두맛집",
   "서울손만두",
   "야식"
  ]
 },
 {
  "card_id": 934,
  "name": "농민백암순대 강남직영점",
  "cat2": "한식",
  "distance_m": 776,
  "policy": "taste",
  "tags": [
   "가성비",
   "맛",
   "분위기",
   "양많은",
   "주차",
   "친절"
  ]
 },
 {
  "card_id": 2669,
  "name": "원미옥",
  "cat2": "한식",
  "distance_m": 328,
  "policy": "legacy",
  "tags": [
   "가성비",
   "넓은",
   "단체모임",
   "맛",
   "분위기",
   "주차"
  ]
 },
 {
  "card_id": 591,
  "name": "스시마이우 강남CGV점",
  "cat2": "일식",
  "distance_m": 854,
  "policy": "taste",
  "tags": [
   "가성비",
   "데이트코스",
   "야식",
   "양많은",
   "점심식사",
   "점심특선"
  ]
 },
 {
  "card_id": 2179,
  "name": "고기가득덮밥 강남역삼본점",
  "cat2": "일식",
  "distance_m": 753,
  "policy": "legacy",
  "tags": [
   "가성비",
   "맛집",
   "밥집",
   "양많은",
   "저녁",
   "점심식사"
  ]
 },
 {
  "card_id": 886,
  "name": "선덕칼국수 본점",
  "cat2": "한식",
  "distance_m": 300,
  "policy": "taste",
  "tags": [
   "가성비",
   "건강한",
   "단체모임",
   "양많은",
   "초복메뉴추천",
   "친절한"
  ]
 },
 {
  "card_id": 2492,
  "name": "류창희국수 역삼본점",
  "cat2": "한식",
  "distance_m": 600,
  "policy": "legacy",
  "tags": [
   "가성비",
   "강남맛집",
   "건강한",
   "넓은",
   "수제비",
   "양많은"
  ]
 }
]
```

카드 10장. 태그 ['혼밥', '가성비'] 를 전부 가진 곳만: True

### `GET /cards?within_m=300` — 저장된 위치(역삼역)를 그대로 쓴다

```console
$ curl -s -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode within_m=300 --data-urlencode limit=10
[
 {
  "card_id": 929,
  "name": "그린보이즈 샐러드&포케 역삼직영점",
  "cat2": "샐러드",
  "distance_m": 291,
  "policy": "legacy",
  "tags": [
   "가성비",
   "건강식",
   "건강한",
   "맛",
   "분위기",
   "양많은"
  ]
 },
 {
  "card_id": 235,
  "name": "우미돈블랙",
  "cat2": "한식",
  "distance_m": 221,
  "policy": "taste",
  "tags": [
   "가성비",
   "감성적",
   "건강한",
   "고기맛집",
   "단체모임",
   "데이트"
  ]
 },
 {
  "card_id": 2265,
  "name": "미도갈비 역삼점",
  "cat2": "한식",
  "distance_m": 245,
  "policy": "legacy",
  "tags": [
   "가성비",
   "고기맛집",
   "넓은",
   "단체모임",
   "데이트",
   "양많은"
  ]
 },
 {
  "card_id": 266,
  "name": "지아니스나폴리 역삼점",
  "cat2": "양식",
  "distance_m": 245,
  "policy": "taste",
  "tags": [
   "가성비",
   "감성적",
   "건강한",
   "소개팅",
   "양많은",
   "점심식사"
  ]
 },
 {
  "card_id": 1378,
  "name": "양국",
  "cat2": "한식",
  "distance_m": 231,
  "policy": "legacy",
  "tags": [
   "가성비",
   "건강한",
   "고기맛집",
   "맛",
   "분위기",
   "양갈비맛집"
  ]
 },
 {
  "card_id": 449,
  "name": "솔솥 GFC몰점(강남파이낸스센터)",
  "cat2": "한식",
  "distance_m": 64,
  "policy": "taste",
  "tags": [
   "가성비",
   "디저트",
   "양많은",
   "점심식사",
   "친절한"
  ]
 },
 {
  "card_id": 687,
  "name": "모던샤브하우스 GFC점",
  "cat2": "샤브샤브",
  "distance_m": 64,
  "policy": "legacy",
  "tags": [
   "shabu",
   "가족모임",
   "단체모임",
   "데이트코스",
   "샤브샤브",
   "샤브샤브맛집"
  ]
 },
 {
  "card_id": 1385,
  "name": "카페413프로젝트 역삼점",
  "cat2": "카페",
  "distance_m": 271,
  "policy": "taste",
  "tags": [
   "413프로젝트",
   "가성비",
   "감성적",
   "강남맛집",
   "강남브런치",
   "넓은"
  ]
 },
 {
  "card_id": 1482,
  "name": "근성순대",
  "cat2": "한식",
  "distance_m": 167,
  "policy": "legacy",
  "tags": [
   "단체모임",
   "수제순대",
   "순대국",
   "술한잔",
   "양많은",
   "역삼맛집"
  ]
 },
 {
  "card_id": 2429,
  "name": "취향마라마라탕 역삼점",
  "cat2": "중식",
  "distance_m": 206,
  "policy": "taste",
  "tags": [
   "가성비",
   "넓은",
   "야식",
   "양많은",
   "점심식사",
   "친절한"
  ]
 }
]
```

카드 10장. distance_m = [291, 221, 245, 245, 231, 64, 64, 271, 167, 206] → 최대 291m ≤ 300m: True

### `GET /cards?lat=&lon=&within_m=200` — 쿼리 좌표가 저장된 위치를 이긴다

```console
$ curl -s -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode lat=37.5096 --data-urlencode lon=127.0366 --data-urlencode within_m=200 --data-urlencode limit=10
[
 {
  "card_id": 2358,
  "name": "나능이 능이버섯백숙 역삼직영점",
  "cat2": "한식",
  "distance_m": 197,
  "policy": "taste",
  "tags": [
   "가성비",
   "가족모임",
   "건강식",
   "맛",
   "분위기",
   "야식"
  ]
 }
]
```

카드 1장. distance_m = [197] → 최대 197m ≤ 200m: True (역삼역에서 1km 북쪽 지점 기준 — 저장 위치를 썼다면 이 카드들은 1km 넘게 떨어져 있어야 한다)

### `GET /cards?cat=중식&tag=혼밥&within_m=300` — 촘촘한 필터는 적게 온다

```console
$ curl -s -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode 'cat=중식' --data-urlencode 'tag=혼밥' --data-urlencode within_m=300 --data-urlencode limit=20
[
 {
  "card_id": 400,
  "name": "도야짬뽕 역삼점",
  "cat2": "중식",
  "distance_m": 212,
  "policy": "legacy",
  "tags": [
   "가성비",
   "넓은",
   "술한잔",
   "양많은",
   "친절한",
   "혼밥"
  ]
 },
 {
  "card_id": 2364,
  "name": "홍콩507",
  "cat2": "중식",
  "distance_m": 86,
  "policy": "taste",
  "tags": [
   "가성비",
   "강남맛집",
   "건강한",
   "단체모임",
   "딤섬",
   "술한잔"
  ]
 },
 {
  "card_id": 1054,
  "name": "안사부 역삼본점",
  "cat2": "중식",
  "distance_m": 200,
  "policy": "legacy",
  "tags": [
   "가성비",
   "단체모임",
   "맛",
   "분위기",
   "야식",
   "양많은"
  ]
 },
 {
  "card_id": 752,
  "name": "호돌이반점",
  "cat2": "중식",
  "distance_m": 185,
  "policy": "taste",
  "tags": [
   "가성비",
   "넓은",
   "맛",
   "분위기",
   "아기자기한",
   "점심식사"
  ]
 },
 {
  "card_id": 61,
  "name": "박사부's 역삼점",
  "cat2": "중식",
  "distance_m": 196,
  "policy": "legacy",
  "tags": [
   "가성비",
   "맛",
   "코스요리",
   "혼밥",
   "회식장소"
  ]
 },
 {
  "card_id": 796,
  "name": "명정루",
  "cat2": "중식",
  "distance_m": 262,
  "policy": "taste",
  "tags": [
   "가성비",
   "양많은",
   "점심식사",
   "친절한",
   "혼밥"
  ]
 },
 {
  "card_id": 2630,
  "name": "차이향",
  "cat2": "중식",
  "distance_m": 280,
  "policy": "legacy",
  "tags": [
   "가성비",
   "양많은",
   "친절한",
   "혼밥",
   "회식장소"
  ]
 },
 {
  "card_id": 1123,
  "name": "가연",
  "cat2": "중식",
  "distance_m": 144,
  "policy": "taste",
  "tags": [
   "가족모임",
   "단체모임",
   "상견례",
   "점심식사",
   "친목모임",
   "친절한"
  ]
 },
 {
  "card_id": 2023,
  "name": "성덕",
  "cat2": "중식",
  "distance_m": 201,
  "policy": "legacy",
  "tags": [
   "가성비",
   "혼밥"
  ]
 },
 {
  "card_id": 293,
  "name": "양궈푸마라탕 역삼1호점",
  "cat2": "중식",
  "distance_m": 278,
  "policy": "taste",
  "tags": [
   "가성비",
   "넓은",
   "점심식사",
   "친절한",
   "혼밥"
  ]
 }
]
```

카드 10장. cat2 = ['중식'] → 요청 ['중식'] 만족: True 태그 ['혼밥'] 를 전부 가진 곳만: True distance_m = [212, 86, 200, 185, 196, 262, 280, 144, 201, 278] → 최대 280m ≤ 300m: True limit=20 을 요청했지만 조건을 만족하는 곳만 준다.

### `GET /cards/{id}?user_id=` — 상세도 저장된 위치로 거리를 준다

```console
$ curl -s -G http://127.0.0.1:8012/cards/400 --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode reviews=0
{
 "card_id": 400,
 "distance_m": 212,
 "store": {
  "name": "도야짬뽕 역삼점",
  "category": "중국요리",
  "location": "서울 강남구 강남대로94길 87 진남빌딩 2층 (역삼동)"
 },
 "menus": "5개",
 "images": "5장"
}
```

### `GET /cards/{id}` — 위치를 모르면 `distance_m: null`

```console
$ curl -s -G http://127.0.0.1:8012/cards/400 --data-urlencode reviews=0
{
 "card_id": 400,
 "distance_m": null
}
```

### `GET /cards?cat=카페&lat=&lon=&within_m=300` — user_id 없는 인기순도 같은 필터

```console
$ curl -s -G http://127.0.0.1:8012/cards --data-urlencode 'cat=카페' --data-urlencode lat=37.5006 --data-urlencode lon=127.0366 --data-urlencode within_m=300 --data-urlencode limit=5
[
 {
  "card_id": 626,
  "name": "로얄마카롱",
  "cat2": "카페",
  "distance_m": 209,
  "policy": null,
  "tags": [
   "가성비",
   "넓은",
   "단체모임",
   "디저트",
   "디저트카페",
   "수제디저트"
  ]
 },
 {
  "card_id": 1525,
  "name": "모프먼트 커피바 역삼",
  "cat2": "카페",
  "distance_m": 181,
  "policy": null,
  "tags": [
   "가성비",
   "로스팅전문",
   "맛",
   "분위기",
   "친절"
  ]
 },
 {
  "card_id": 2050,
  "name": "유월커피 역삼점",
  "cat2": "카페",
  "distance_m": 222,
  "policy": null,
  "tags": [
   "넓은",
   "조용한"
  ]
 },
 {
  "card_id": 1158,
  "name": "플레어비 역삼GFC몰점",
  "cat2": "카페",
  "distance_m": 64,
  "policy": null,
  "tags": [
   "가성비",
   "건강식",
   "건강한",
   "브런치카페",
   "소개팅",
   "아침식사"
  ]
 },
 {
  "card_id": 585,
  "name": "로우키엑스알 GS타워점",
  "cat2": "카페",
  "distance_m": 136,
  "policy": null,
  "tags": [
   "가성비",
   "감성적",
   "로스팅전문",
   "친절한",
   "혼밥"
  ]
 }
]
```

카드 5장. cat2 = ['카페'] → 요청 ['카페'] 만족: True distance_m = [209, 181, 222, 64, 136] → 최대 222m ≤ 300m: True `policy` 는 null(개인화 없음).

### 에러 경우

```console
$ curl -s -o /dev/null -w '%{http_code} ' -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_no_location_test --data-urlencode within_m=300
400
```
위치를 모르는데 within_m — 400

```console
$ curl -s -o /dev/null -w '%{http_code} ' -G http://127.0.0.1:8012/cards --data-urlencode lat=37.5
400
```
lat 만 주면 — 400

```console
$ curl -s -o /dev/null -w '%{http_code} ' -G http://127.0.0.1:8012/cards --data-urlencode lat=91 --data-urlencode lon=127
422
```
위도 범위 밖 쿼리 — 422

```console
$ curl -s -o /dev/null -w '%{http_code} ' -X PUT http://127.0.0.1:8012/users/u_6b30fdefbe1e/location -H 'Content-Type: application/json' -d '{"lat":91,"lon":127}'
422
```
PUT 위치 범위 밖 본문 — 422

```console
$ curl -s -o /dev/null -w '%{http_code} ' -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode within_m=0
422
```
within_m=0 — 422 (ge=1)

```console
$ curl -s -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode 'cat=없는업종'
[]
```
모르는 업종 — 에러가 아니라 빈 목록.

```console
$ curl -s -G http://127.0.0.1:8012/cards --data-urlencode user_id=u_6b30fdefbe1e --data-urlencode 'tag=없는태그'
[]
```
모르는 태그 — 에러가 아니라 빈 목록.

### `GET /users/{id}` — 쿼리 좌표는 저장되지 않았다(저장 위치는 역삼역 그대로)

```console
$ curl -s http://127.0.0.1:8012/users/u_6b30fdefbe1e
{
 "user_id": "u_6b30fdefbe1e",
 "name": "필터검증",
 "location": {
  "lat": 37.5006,
  "lon": 127.0366,
  "at": "2026-09-10T10:39:37+09:00"
 },
 "registered": true,
 "swipes": 0,
 "likes": 0
}
```

### 영속화 — `state/users.jsonl`

```json
{"user_id": "u_6b30fdefbe1e", "name": "필터검증", "created_at": "2026-09-10T10:39:37+09:00", "location": null}
{"user_id": "u_6b30fdefbe1e", "name": "필터검증", "created_at": "2026-09-10T10:39:37+09:00", "location": {"lat": 37.5006, "lon": 127.0366, "at": "2026-09-10T10:39:37+09:00"}}
```

u_6b30fdefbe1e 의 줄 2개 = 발급 1 + `PUT` 1. 위치를 고칠 때마다 **병합된 전체 레코드**가 한 줄 쌓이고(이름·발급시각 보존) 복원은 마지막 줄이 이긴다. `GET /cards?lat=&lon=` 은 줄을 만들지 않았다.


## 어휘 고정 — 업종 9 · 태그 12 (2026-09-10, 2차)

서버가 필터 어휘의 주인이 됐다. `GET /filters` 가 값을 내려주고 `GET /cards` 는 **그 값만**
받는다(밖이면 422). 업종은 후보 풀에서 많은 순 상위 8 + `기타`, 태그는 뜻이 같은 원본 이름을
묶은 12칸이다. 카드에는 같은 라벨이 `store.category_group` 으로 실린다.
아래는 같은 방식으로(임시 `MATCHURI_STATE`, 포트 8013, interleave) 다시 부른 전사다.

#### `GET /filters` — 고정 어휘 전체 (업종 9 · 태그 12 · 반경 4)

```console
$ curl -s http://127.0.0.1:8013/filters
{
 "cats": [
  {
   "name": "한식",
   "count": 710
  },
  {
   "name": "카페",
   "count": 460
  },
  {
   "name": "일식",
   "count": 188
  },
  {
   "name": "술집",
   "count": 187
  },
  {
   "name": "양식",
   "count": 143
  },
  {
   "name": "중식",
   "count": 121
  },
  {
   "name": "간식",
   "count": 93
  },
  {
   "name": "분식",
   "count": 87
  },
  {
   "name": "기타",
   "count": 227
  }
 ],
 "tags": [
  {
   "name": "가성비",
   "count": 1778
  },
  {
   "name": "친절",
   "count": 1745
  },
  {
   "name": "양많은",
   "count": 940
  },
  {
   "name": "혼밥",
   "count": 743
  },
  {
   "name": "점심식사",
   "count": 743
  },
  {
   "name": "야식",
   "count": 531
  },
  {
   "name": "단체모임",
   "count": 884
  },
  {
   "name": "분위기",
   "count": 976
  },
  {
   "name": "데이트",
   "count": 402
  },
  {
   "name": "술한잔",
   "count": 238
  },
  {
   "name": "조용한",
   "count": 250
  },
  {
   "name": "주차",
   "count": 264
  }
 ],
 "within_m": [
  300,
  500,
  1000,
  2000
 ]
}
```

업종은 풀에서 많은 순 상위 8 + `기타` 227(맨 뒤, cat2 가 없는 4곳 포함)이고 합이 풀 크기 2,216 과 정확히 같다 — 모든 가게가 9칸 중 한 칸에 들어간다. 태그는 12칸 고정 순서이고 개수만 데이터에서 온다.

#### `GET /cards?cat=기타&limit=5` — 상위 8 밖은 전부 `기타`, 카드가 같은 라벨을 싣는다

```console
$ curl -s -G http://127.0.0.1:8013/cards --data-urlencode 'cat=기타' --data-urlencode limit=5
[
 {
  "card_id": 1414,
  "name": "오트보트",
  "cat2": "샐러드",
  "category_group": "기타",
  "category": "샐러드",
  "distance_m": null
 },
 {
  "card_id": 929,
  "name": "그린보이즈 샐러드&포케 역삼직영점",
  "cat2": "샐러드",
  "category_group": "기타",
  "category": "샐러드",
  "distance_m": null
 },
 {
  "card_id": 687,
  "name": "모던샤브하우스 GFC점",
  "cat2": "샤브샤브",
  "category_group": "기타",
  "category": "샤브샤브",
  "distance_m": null
 },
 {
  "card_id": 2131,
  "name": "라이프델리",
  "cat2": "패스트푸드",
  "category_group": "기타",
  "category": "샌드위치",
  "distance_m": null
 },
 {
  "card_id": 1685,
  "name": "라이브볼키친",
  "cat2": "샐러드",
  "category_group": "기타",
  "category": "샐러드",
  "distance_m": null
 }
]
```

카드 5장. 원본 cat2 = ['샐러드', '샤브샤브', '패스트푸드'] — 전부 상위 8 밖이다. `store.category_group` 은 모두 `기타`(필터에 쓰는 라벨), `store.category` 는 그대로 카카오 세부 분류(cat3)다.

#### `GET /cards?tag=단체모임&limit=10` — `회식장소`·`친목모임` 만 있는 가게도 걸린다

```console
$ curl -s -G http://127.0.0.1:8013/cards --data-urlencode 'tag=단체모임' --data-urlencode limit=10
[
 {
  "card_id": 65,
  "name": "고우가 역삼역점",
  "cat2": "한식",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null
 },
 {
  "card_id": 741,
  "name": "오늘도등심 강남점",
  "cat2": "한식",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null
 },
 {
  "card_id": 2340,
  "name": "뼈탄집 강남역삼점",
  "cat2": "한식",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null
 },
 {
  "card_id": 2265,
  "name": "미도갈비 역삼점",
  "cat2": "한식",
  "category_group": "한식",
  "category": "한식",
  "distance_m": null
 },
 {
  "card_id": 235,
  "name": "우미돈블랙",
  "cat2": "한식",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null
 },
 {
  "card_id": 778,
  "name": "해담아구찜&해물찜",
  "cat2": "한식",
  "category_group": "한식",
  "category": "해물,생선",
  "distance_m": null
 },
 {
  "card_id": 1893,
  "name": "동남회관 역삼본점",
  "cat2": "한식",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null
 },
 {
  "card_id": 1074,
  "name": "1977소곱창",
  "cat2": "한식",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null
 },
 {
  "card_id": 854,
  "name": "란수사",
  "cat2": "일식",
  "category_group": "일식",
  "category": "일식집",
  "distance_m": null
 },
 {
  "card_id": 131,
  "name": "비놀릭",
  "cat2": "양식",
  "category_group": "양식",
  "category": "양식",
  "distance_m": null
 }
]
```

카드 10장 모두 묶음 중 하나를 갖고 있다. 이 중 **원본에 `단체모임` 이 없고 동의어로만 걸린 곳**: 해담아구찜&해물찜(친목모임·회식장소), 1977소곱창(회식장소), 비놀릭(친목모임·회식장소). 풀 전체로는 `단체모임` 884곳 중 **175곳**이 동의어로만 걸린다 (묶지 않았다면 그만큼이 필터에서 사라진다).

#### `GET /cards?tag=혼밥,가성비&limit=5` — AND 는 그대로다

```console
$ curl -s -G http://127.0.0.1:8013/cards --data-urlencode 'tag=혼밥,가성비' --data-urlencode limit=5
[
 {
  "card_id": 65,
  "name": "고우가 역삼역점",
  "cat2": "한식",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null
 },
 {
  "card_id": 298,
  "name": "크라이치즈버거 강남점",
  "cat2": "양식",
  "category_group": "양식",
  "category": "햄버거",
  "distance_m": null
 },
 {
  "card_id": 2669,
  "name": "원미옥",
  "cat2": "한식",
  "category_group": "한식",
  "category": "국밥",
  "distance_m": null
 },
 {
  "card_id": 577,
  "name": "춘만두",
  "cat2": "분식",
  "category_group": "분식",
  "category": "분식",
  "distance_m": null
 },
 {
  "card_id": 2179,
  "name": "고기가득덮밥 강남역삼본점",
  "cat2": "일식",
  "category_group": "일식",
  "category": "일식",
  "distance_m": null
 }
]
```

카드 5장, 두 태그를 **전부** 가진 곳만: True

#### 어휘 밖의 값은 422 — 빈 목록이 아니다

```console
$ curl -s -w '
%{http_code}
' -G http://127.0.0.1:8013/cards --data-urlencode 'cat=초밥'
{"detail":"cat '초밥' 은 없는 값입니다. 허용: 한식, 카페, 일식, 술집, 양식, 중식, 간식, 분식, 기타"}
422
```
`cat=초밥` — 없는 업종

```console
$ curl -s -w '
%{http_code}
' -G http://127.0.0.1:8013/cards --data-urlencode 'tag=맛'
{"detail":"tag '맛' 은 없는 값입니다. 허용: 가성비, 친절, 양많은, 혼밥, 점심식사, 야식, 단체모임, 분위기, 데이트, 술한잔, 조용한, 주차"}
422
```
`tag=맛` — 리뷰 태그 원본 이름(어휘가 아니다)

```console
$ curl -s -w '
%{http_code}
' -G http://127.0.0.1:8013/cards --data-urlencode 'cat=한식,없는업종'
{"detail":"cat '없는업종' 은 없는 값입니다. 허용: 한식, 카페, 일식, 술집, 양식, 중식, 간식, 분식, 기타"}
422
```
`cat=한식,없는업종` — 하나만 틀려도 거부한다

#### 어휘 안의 값인데 조건에 맞는 곳이 없으면 — 빈 목록

```console
$ curl -s -G http://127.0.0.1:8013/cards --data-urlencode 'cat=기타' --data-urlencode 'tag=술한잔,조용한,주차,야식,혼밥' --data-urlencode lat=37.5006 --data-urlencode lon=127.0366 --data-urlencode within_m=300
[]
```

카드 0장. 값이 어휘 안이면 조건이 아무리 촘촘해도 200 · 빈 배열이다 — '그런 가게가 없다' 와 '없는 값을 보냈다'(422)가 이렇게 구분된다.


## 설정은 사용자에 붙는다 — 필터 쿼리를 없앴다 (2026-09-10, 3차)

같은 날 3차 수정. `GET /cards` 의 `cat`·`tag`·`within_m`·`lat`·`lon` 쿼리와
`GET /cards/{id}` 의 `lat`·`lon` 을 **없앴다.** 조회가 설정을 정하면 같은 URL 이 요청마다
다른 뜻이 되고 '지금 이 사람에게 무엇이 걸려 있나' 를 한 곳에서 답할 수 없다. 이제 순서는
셋이다 — 어휘 조회 `GET /filters` → 설정 `PUT /users/{id}/filter` ·
`PUT /users/{id}/location` (`DELETE` 로 해제) → 추천 `GET /cards?user_id=`.
아래는 임시 `MATCHURI_STATE` · 포트 8014 · interleave 로 부른 전사다.

#### `POST /users` — 발급 응답에 `location`·`filter` 가 `null` 로 들어 있다

```console
$ curl -s -X POST http://127.0.0.1:8014/users -H 'Content-Type: application/json' -d '{"name":"설정검증"}'
{
 "user_id": "u_cef6b22b5807",
 "name": "설정검증",
 "created_at": "2026-09-10T14:24:53+09:00",
 "location": null,
 "filter": null
}
```

#### `PUT /users/{id}/location` — 위치는 설정이다(역삼역)

```console
$ curl -s -X PUT http://127.0.0.1:8014/users/u_cef6b22b5807/location -H 'Content-Type: application/json' -d '{"lat": 37.5006, "lon": 127.0366}'
{
 "user_id": "u_cef6b22b5807",
 "name": "설정검증",
 "created_at": "2026-09-10T14:24:53+09:00",
 "location": {
  "lat": 37.5006,
  "lon": 127.0366,
  "at": "2026-09-10T14:24:53+09:00"
 },
 "filter": null
}
```

#### `PUT /users/{id}/filter` — 필터도 설정이다 (일식 · 혼밥 · 500m)

```console
$ curl -s -X PUT http://127.0.0.1:8014/users/u_cef6b22b5807/filter -H 'Content-Type: application/json' -d '{"cat": ["일식"], "tag": ["혼밥"], "within_m": 500}'
{
 "user_id": "u_cef6b22b5807",
 "name": "설정검증",
 "created_at": "2026-09-10T14:24:53+09:00",
 "location": {
  "lat": 37.5006,
  "lon": 127.0366,
  "at": "2026-09-10T14:24:53+09:00"
 },
 "filter": {
  "cat": [
   "일식"
  ],
  "tag": [
   "혼밥"
  ],
  "within_m": 500,
  "at": "2026-09-10T14:24:53+09:00"
 }
}
```

#### `GET /users/{id}` — '지금 무엇이 걸려 있나' 의 답은 사용자 레코드다

```console
$ curl -s http://127.0.0.1:8014/users/u_cef6b22b5807
{
 "user_id": "u_cef6b22b5807",
 "name": "설정검증",
 "location": {
  "lat": 37.5006,
  "lon": 127.0366,
  "at": "2026-09-10T14:24:53+09:00"
 },
 "filter": {
  "cat": [
   "일식"
  ],
  "tag": [
   "혼밥"
  ],
  "within_m": 500,
  "at": "2026-09-10T14:24:53+09:00"
 },
 "registered": true,
 "swipes": 0
}
```

#### `GET /cards?user_id=…&limit=10` — 쿼리 없이도 저장된 설정이 걸린다

```console
$ curl -s -G http://127.0.0.1:8014/cards --data-urlencode user_id=u_cef6b22b5807 --data-urlencode limit=10
[
 {
  "card_id": 1944,
  "name": "치솟",
  "category_group": "일식",
  "category": "일식집",
  "distance_m": 344,
  "policy": "legacy"
 },
 {
  "card_id": 1444,
  "name": "나이스샤워 역삼점",
  "category_group": "일식",
  "category": "일식",
  "distance_m": 467,
  "policy": "taste"
 },
 {
  "card_id": 1030,
  "name": "카츠쇼신 강남점",
  "category_group": "일식",
  "category": "돈까스,우동",
  "distance_m": 381,
  "policy": "legacy"
 },
 {
  "card_id": 1590,
  "name": "갓포돈",
  "category_group": "일식",
  "category": "돈까스,우동",
  "distance_m": 142,
  "policy": "taste"
 },
 {
  "card_id": 1902,
  "name": "오사무식당 역삼점",
  "category_group": "일식",
  "category": "일식",
  "distance_m": 180,
  "policy": "legacy"
 },
 {
  "card_id": 1907,
  "name": "돈카츠멘",
  "category_group": "일식",
  "category": "돈까스,우동",
  "distance_m": 464,
  "policy": "taste"
 },
 {
  "card_id": 208,
  "name": "동경",
  "category_group": "일식",
  "category": "초밥,롤",
  "distance_m": 464,
  "policy": "legacy"
 },
 {
  "card_id": 1736,
  "name": "진가와 한국본점",
  "category_group": "일식",
  "category": "일식",
  "distance_m": 298,
  "policy": "taste"
 },
 {
  "card_id": 557,
  "name": "마구로젠",
  "category_group": "일식",
  "category": "참치회",
  "distance_m": 306,
  "policy": "legacy"
 },
 {
  "card_id": 1833,
  "name": "호타루",
  "category_group": "일식",
  "category": "일식",
  "distance_m": 181,
  "policy": "taste"
 }
]
```

카드 10장 — `category_group` 전부 일식: True · 태그 `혼밥` 보유: True · `distance_m` [344, 467, 381, 142, 180, 464, 464, 298, 306, 181] (최대 467m ≤ 500m: True). 두 정책이 같은 집합만 봤다: ['legacy', 'taste']

#### `GET /cards?...&cat=카페&within_m=1` — 필터 쿼리는 없앴다(읽지도 않는다)

```console
$ curl -s -G http://127.0.0.1:8014/cards --data-urlencode user_id=u_cef6b22b5807 --data-urlencode 'cat=카페' --data-urlencode 'tag=맛' --data-urlencode within_m=1 --data-urlencode limit=5
[
 {
  "card_id": 2413,
  "name": "후라토식당 GFC역삼직영점",
  "category_group": "일식",
  "category": "후라토식당",
  "distance_m": 64,
  "policy": "taste"
 },
 {
  "card_id": 793,
  "name": "탄",
  "category_group": "일식",
  "category": "일식",
  "distance_m": 259,
  "policy": "legacy"
 },
 {
  "card_id": 2655,
  "name": "긴자료코 역삼점",
  "category_group": "일식",
  "category": "돈까스,우동",
  "distance_m": 276,
  "policy": "taste"
 },
 {
  "card_id": 1431,
  "name": "카즈야",
  "category_group": "일식",
  "category": "돈까스,우동",
  "distance_m": 224,
  "policy": "legacy"
 },
 {
  "card_id": 2047,
  "name": "백초밥",
  "category_group": "일식",
  "category": "초밥,롤",
  "distance_m": 246,
  "policy": "taste"
 }
]
```

카드 5장 — 여전히 ['일식'] · 최대 276m 다. 어휘 밖 값(`tag=맛`)을 실어도 422 가 아니다 — 그 쿼리는 계약에 없으므로 FastAPI 가 버린다. 설정을 바꾸는 길은 PUT 하나뿐이다.

#### 어휘 검증은 **설정을 저장할 때** 한다

```console
$ curl -s -w '
%{http_code}
' -X PUT http://127.0.0.1:8014/users/u_cef6b22b5807/filter -H 'Content-Type: application/json' -d '{"cat":["초밥"]}'
{"detail":"cat '초밥' 은 없는 값입니다. 허용: 한식, 카페, 일식, 술집, 양식, 중식, 간식, 분식, 기타"}
422
```
`cat: ["초밥"]` — 없는 업종

```console
$ curl -s -w '
%{http_code}
' -X PUT http://127.0.0.1:8014/users/u_cef6b22b5807/filter -H 'Content-Type: application/json' -d '{"tag":["맛"]}'
{"detail":"tag '맛' 은 없는 값입니다. 허용: 가성비, 친절, 양많은, 혼밥, 점심식사, 야식, 단체모임, 분위기, 데이트, 술한잔, 조용한, 주차"}
422
```
`tag: ["맛"]` — 리뷰 태그 원본 이름(어휘가 아니다)

```console
$ curl -s -w '
%{http_code}
' -X PUT http://127.0.0.1:8014/users/u_cef6b22b5807/filter -H 'Content-Type: application/json' -d '{"within_m":0}'
{"detail":[{"type":"greater_than_equal","loc":["body","within_m"],"msg":"Input should be greater than or equal to 1","input":0,"ctx":{"ge":1}}]}
422
```
`within_m: 0` — 반경은 1 이상(pydantic)

거부된 PUT 은 저장하지 않는다 — 위의 세 번 뒤에도 필터는 {"cat": ["일식"], "tag": ["혼밥"], "within_m": 500, "at": "2026-09-10T14:24:53+09:00"} 그대로다.

#### 저장된 반경인데 저장된 위치가 없으면 400

```console
$ curl -s -w '
%{http_code}
' -G http://127.0.0.1:8014/cards --data-urlencode user_id=u_45e2026eb011
{"detail":"저장된 필터의 within_m 을 쓸 위치가 없습니다 — PUT /users/{user_id}/location 으로 위치를 저장하거나 PUT /users/{user_id}/filter 로 반경을 지우세요"}
400
```

메시지가 다음에 할 일을 적는다(위치를 저장하거나 반경을 지우기). 쿼리로 좌표를 받는 길이 없어졌으므로 이 400 을 넘길 방법은 설정 하나뿐이다.

#### `DELETE /users/{id}/filter` — 해제도 설정 변경이다

```console
$ curl -s -X DELETE http://127.0.0.1:8014/users/u_cef6b22b5807/filter
{
 "user_id": "u_cef6b22b5807",
 "name": "설정검증",
 "location": {
  "lat": 37.5006,
  "lon": 127.0366,
  "at": "2026-09-10T14:24:53+09:00"
 },
 "filter": null
}
```

#### `GET /cards?user_id=…&limit=10` — 필터를 지운 뒤(위치는 남아 있다)

```console
$ curl -s -G http://127.0.0.1:8014/cards --data-urlencode user_id=u_cef6b22b5807 --data-urlencode limit=10
[
 {
  "card_id": 2018,
  "name": "퍼그피자하우스 강남점",
  "category_group": "양식",
  "category": "피자",
  "distance_m": 852,
  "policy": "taste"
 },
 {
  "card_id": 65,
  "name": "고우가 역삼역점",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": 64,
  "policy": "legacy"
 },
 {
  "card_id": 131,
  "name": "비놀릭",
  "category_group": "양식",
  "category": "양식",
  "distance_m": 1051,
  "policy": "taste"
 },
 {
  "card_id": 741,
  "name": "오늘도등심 강남점",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": 496,
  "policy": "legacy"
 },
 {
  "card_id": 1003,
  "name": "60년전통신촌황소곱창 강남직영점",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": 842,
  "policy": "taste"
 },
 {
  "card_id": 854,
  "name": "란수사",
  "category_group": "일식",
  "category": "일식집",
  "distance_m": 511,
  "policy": "legacy"
 },
 {
  "card_id": 298,
  "name": "크라이치즈버거 강남점",
  "category_group": "양식",
  "category": "햄버거",
  "distance_m": 581,
  "policy": "taste"
 },
 {
  "card_id": 2340,
  "name": "뼈탄집 강남역삼점",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": 548,
  "policy": "legacy"
 },
 {
  "card_id": 2701,
  "name": "돼봉삼겹살 신논현점",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": 971,
  "policy": "taste"
 },
 {
  "card_id": 1270,
  "name": "바코드플러스",
  "category_group": "술집",
  "category": "와인바",
  "distance_m": 351,
  "policy": "legacy"
 }
]
```

카드 10장 — `category_group` ['술집', '양식', '일식', '한식'] 로 다시 넓어졌고, 거리 조건이 없어 최대 1051m 까지 온다. `distance_m` 은 위치 설정이 남아 있으니 계속 값이 있다.

#### `GET /cards?limit=3` — `user_id` 가 없으면 걸릴 설정이 없다

```console
$ curl -s -G http://127.0.0.1:8014/cards --data-urlencode limit=3
[
 {
  "card_id": 65,
  "name": "고우가 역삼역점",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null,
  "policy": null
 },
 {
  "card_id": 741,
  "name": "오늘도등심 강남점",
  "category_group": "한식",
  "category": "육류,고기",
  "distance_m": null,
  "policy": null
 },
 {
  "card_id": 2018,
  "name": "퍼그피자하우스 강남점",
  "category_group": "양식",
  "category": "피자",
  "distance_m": null,
  "policy": null
 }
]
```

인기순이고 `policy`·`distance_m` 은 `null` 이다(예전 동작 그대로).

#### 영속화 — `state/users.jsonl` (병합된 전체 레코드, 마지막 줄이 이긴다)

```json
{"user_id": "u_cef6b22b5807", "name": "설정검증", "created_at": "2026-09-10T14:24:53+09:00", "location": null, "filter": null}
{"user_id": "u_cef6b22b5807", "name": "설정검증", "created_at": "2026-09-10T14:24:53+09:00", "location": {"lat": 37.5006, "lon": 127.0366, "at": "2026-09-10T14:24:53+09:00"}, "filter": null}
{"user_id": "u_cef6b22b5807", "name": "설정검증", "created_at": "2026-09-10T14:24:53+09:00", "location": {"lat": 37.5006, "lon": 127.0366, "at": "2026-09-10T14:24:53+09:00"}, "filter": {"cat": ["일식"], "tag": ["혼밥"], "within_m": 500, "at": "2026-09-10T14:24:53+09:00"}}
{"user_id": "u_cef6b22b5807", "name": "설정검증", "created_at": "2026-09-10T14:24:53+09:00", "location": {"lat": 37.5006, "lon": 127.0366, "at": "2026-09-10T14:24:53+09:00"}, "filter": null}
```

u_cef6b22b5807 의 줄 4개 = 발급 · 위치 · 필터 · 해제. 거부된 PUT 은 줄을 만들지 않았고, `GET /cards` 는 어떤 줄도 만들지 않는다. 해제도 한 줄 남기므로 재시작 복원이 옛 필터를 되살리지 않는다.

