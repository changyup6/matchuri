# server — API

프론트와 합의한 계약(`/cards`, `/logs`)에 추천 엔진을 연결한 것. 배포되는 유일한 파트다.

```
../../docs/API.md  클라이언트 가이드 — 호출 순서·필드·에러·예시 (프론트가 읽는 문서)
main.py        엔드포인트. 계약의 정의이자 문서(/docs 가 여기서 나온다)
app/
  loader.py    data/kakao -> 응답 모양. card_id(int) <-> store_id(str) 매핑
  model.py     응답·요청 모델(pydantic v2) — 필드마다 설명·예시. `/openapi.json` 이 곧 프론트 타입
  filters.py   업종·태그·거리 -> 허용 store_id 집합 (후보 선택 단계에 넘긴다) + /filters 어휘
               어휘 검증(`validate`)은 필터를 **저장할 때** 돈다
  geo.py       하버사인 거리와 가게 좌표 캐시 (distance_m · within_m)
  admin.py     관리자 API + 화면 (+ 취향 지형도 `/admin/map`, 읽기 전용)
  users.py     사용자 등록부 (+ `user_id` 검증 규칙)
  bookmarks.py 찜 — 사용자별 순서 있는 목록. **추천을 건드리지 않는다**
  user_reviews.py 사용자 후기 — 설문 세 줄을 `store.reviews` 맨 앞에 섞는다(상세·리뷰 더보기만)
  ranking.py   정렬 보정 — 겹치는 태그 수(계층)와 거리 보너스. 학습에는 안 들어간다
  observe.py   관측 — 요청 JSON 로그·요청 id·도메인 이벤트·프로세스 지표·CORS
  taste_api.py 취향 전달 — 세션 `/taste?detail=1|2|3`의 궤적·cat2 → cat3 → 가게 지도와 예시 화면. 기본 1, 선택한 단계 전체를 조회. 읽기 전용
  taste_hierarchy.py 공개 지도 확대 — `/taste/map/children?parent=…`, cat2 → cat3 → 가게만 단계별 조회
  state.py     영속화 — 추가 전용 로그. 재시작 때 선호 표현을 다시 먹여 복원한다
  static/      admin.html · map.html · taste.html(취향 전달 예시, 폰 360×720)
recsys/
  store.py     데이터 로딩 (파일별 mtime 확인 후 바뀐 것만 다시 읽음)
  swipe.py     구 정책(legacy) — 카테고리·가격 로지스틱. 품질 베이스라인
  content.py   서빙 산출물 로더 + 구성요소 z 점수 8개
  taste.py     신 정책(taste) — 구성요소 위의 사용자별 가중치, 쌍대 학습
  policy.py    정책 라우터 — 팀-드래프트 인터리빙, 노출 로그, 정책별 성적
  main.py      스와이프 데모 서버 (별도 앱, 개발용)
  static/      swipe.html
tests/         pytest — 성분 수식·학습 방향·재생 복원·인터리빙·HTTP 계약
```

## 타입은 `/openapi.json` 이 준다

클라이언트 엔드포인트 전부에 `response_model=` 이 붙어 있다(`app/model.py`·`app/taste_api.py`).
프론트는 `npx openapi-typescript http://host/openapi.json -o api.d.ts` 로 타입을 만들고,
서버가 바꾸면 다시 만든다 — 손으로 베낀 타입은 계약이 바뀔 때 조용히 어긋난다.
업종·태그·`prefer`·`policy` 의 허용 값은 스키마의 `enum` 으로 실려 유니온 타입이 된다.
`/admin/api/*` 는 관리자 화면과 함께 바뀌는 내부 API 라 **의도적으로** 타입을 약속하지 않는다.
표와 확인 기록은 [`../analysis/backtest/results/openapi-20260910.md`](../analysis/backtest/results/openapi-20260910.md).

## 실행

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000          # 서비스 API + /admin
uvicorn recsys.main:app --reload --port 8077   # 스와이프 데모 화면(개발용)
pytest tests -q                                # 테스트 (MATCHURI_STATE 를 임시 경로로)

# 실서버 기록으로 폭주 회귀 검사 — state **사본**을 가리킨다(원본은 건드리지 않는다)
MATCHURI_STATE=/tmp/state-copy pytest tests/test_replay.py -q -s -k 폭주
```

## 카탈로그 사진 저장·전달

`app/catalog_media.py`가 수집 완료된 가게·메뉴·리뷰 사진을 기존 이미지 필드의 절대 URL로
바꿉니다. 원본 테이블과 추천 모델 입력은 유지합니다. 사진 바이트는 nginx의
`/media/catalog/`에서 직접 전달하며, API 요청 중 다운로드·변환을 수행하지 않습니다.
공개 주소는 `MATCHURI_MEDIA_PUBLIC_BASE_URL`로 설정합니다(compose 기본값은 운영 주소).
설정이 없으면 기존 URL을 유지합니다. 요청한 v2 파일이 없으면 저장된 v1, 마지막으로 기존 URL 순으로 대체합니다.

```bash
# server/에서 실행. report에는 원본 URL이 포함되므로 공개 media 밖에 둡니다.
python cache_catalog_images.py --data-dir ../data/kakao --media-dir ../media \
  --workers 8 --report /tmp/catalog-images.jsonl

# 운영: 현재 배포 이미지와 영속 media 볼륨을 사용합니다. API 서버와 별도 프로세스입니다.
bash BE/ops/cache_catalog_images.sh
```

`photos.jsonl`, `menus.jsonl`, `reviews.jsonl`의 모든 사진 URL을 중복 제거해 수집합니다.
실패는 report에 기록하고 다음 실행에서 재시도하며, 완료 파일은 다시 받지 않습니다.
v2는 `media/catalog/v2/card/`(640px·quality72)와 `v2/thumb/`(320px·quality70)에
progressive JPEG를 저장합니다. 기존 v1 축소본이 있으면 이를 입력으로 재활용하며, 없는 URL만
원본을 한 번 내려받아 두 크기를 만듭니다. v1에서 만든 파일은 재압축한 썸네일이며 원본 대용이 아닙니다.
EXIF 회전·투명 배경(흰색)을 적용하고 메타데이터를 제거하며 애니메이션은 첫 프레임을 사용합니다.
v1 파일은 이전 클라이언트의 URL·캐시를 위해 그대로 둡니다. 같은 파일은 덮어쓰지 않으므로 압축 규격을 바꾸려면
캐시 경로의 버전을 올려야 합니다. URL 만료·삭제 등 수집 실패가 있으면 CLI 종료 코드는1입니다.

일반 API는 카드에 `card`, 메뉴·리뷰에 `thumb`를 사용합니다. 상세와 리뷰 조회의
`original_images=true`는 같은 응답 필드에 원본 URL을 넣습니다. 원본 옵션도 서버에서 사진을
다운로드하지 않으며, 클라이언트는 확대 요청 때만 이 옵션과 원본 이미지를 읽습니다.

운영에서는 이 스크립트를 매시간 실행해 새 데이터의 사진을 채웁니다. 작업 잠금으로 중복 실행을
막고 CPU2개·메모리2GiB를 상한으로 둡니다. 수집 결과는 `/home/ubuntu/matchuri-image-cache/`에
최근 두 실행만 보관하며, 사진 볼륨·수집 기록·원본 테이블은 Git에 넣지 않습니다.

## 필터와 위치 — 사용자 설정 (`cat` · `tag` · `within_m`)

**설정은 사용자에 붙는다. `GET /cards` 에는 필터 쿼리가 없다.** 순서는 셋이다.

```
GET  /filters                      어휘 조회 — 업종 9 · 태그 12 · 권장 반경
PUT  /users/{id}/filter            설정 {cat, tag, within_m}  (DELETE 로 해제)
PUT  /users/{id}/location          설정 {lat, lon}
GET  /cards?user_id=...            추천 — 저장된 설정을 적용한다
```

`cat`·`tag`·`within_m`·`lat`·`lon` 을 쿼리로도 받던 길을 없앴다(같은 날 2차 수정).
조회가 설정을 정하면 같은 URL 이 요청마다 다른 뜻이 되고, '지금 이 사람에게 무엇이 걸려
있나' 를 한 곳에서 답할 수 없다 — 지금은 `GET /users/{id}` 의 `filter`·`location` 이
그 답이고, 파일(`state/users.jsonl`)에 남아 재시작 뒤에도 같다.

필터는 **응답이 아니라 후보 선택에서** 건다. `app/filters.py` 가 만든 허용 집합이
`policy.pick_many(..., allow=)` 로 두 정책에 그대로 내려가고, `swipe._pick` ·
`taste._pick_one` 이 집합 밖 후보를 건너뛴다(**fallback 도 집합 안에서만**). 뽑고 나서
거르면 걸러진 카드가 `shown` 에 남아 그 사용자에게 영영 다시 안 나오기 때문이다 —
필터를 풀면 다시 나와야 맞다. 그래서 필터가 촘촘하면 `limit` 보다 적게(0장까지) 온다.

**어휘는 서버가 정한다.** `GET /filters` 가 값을 내려주고 `GET /cards` 는 **그 값만** 받는다
(어휘 밖이면 422 — 오타를 빈 목록으로 뭉개면 '그런 가게가 없다' 와 구분되지 않는다).
개수는 후보 풀 기준이다(전체 가게로 세면 카드로 안 나오는 가게가 섞인다).

- **업종 9개** — 풀에서 많은 순 상위 8(`한식` 710 · `카페` 460 · `일식` 188 · `술집` 187 ·
  `양식` 143 · `중식` 121 · `간식` 93 · `분식` 87)과 나머지 전부인 `기타` 227(cat2 가 없는
  4곳 포함). 꼬리는 치킨 53 · 샐러드 50 … 로 잘게 흩어져 필터 칸으로 쓸모가 없다.
  카드의 `store.category_group` 이 같은 라벨이다(`store.category` 는 그대로 cat3).
  legacy 정책이 학습하는 `swipe.CATS` 와는 **다른 목록**이다 — 그쪽은 모델 가중치의 배치다.
- **태그 12개** — 뜻이 같은 원본 이름을 한 칸으로 묶었다: `친절` = 친절 842 ∪ 친절한 1305
  → **1,745**, `단체모임` = 단체모임·회식장소·친목모임 → **884**, `분위기` = 분위기·감성적
  → **976**, `데이트` = 데이트·데이트코스·소개팅 → **402**. 나머지 여덟(`가성비` 1778 ·
  `양많은` 940 · `혼밥` 743 · `점심식사` 743 · `야식` 531 · `주차` 264 · `조용한` 250 ·
  `술한잔` 238)은 원본 이름 그대로다. 원본은 가게 태그(카카오)와 리뷰 태그가 섞여 있어 같은
  뜻이 두 이름으로 오고(`친절한` 대 `친절`), 사용자가 그 사정을 알 이유가 없다.

업종도 태그도 OR 다(고른 것 중 하나만 맞아도 통과) — 칩을 더 누르면 결과가 **넓어지는** 것이
필터의 상식이다. 차원 사이는 AND 다(업종 ∧ 태그 ∧ 거리): '일식이면서 혼밥 되는, 500m 안의 곳'
이 필터가 뜻하는 바이고, 차원까지 OR 로 묶으면 조건을 건 의미가 사라진다.

거리는 하버사인이다(`app/geo.py`). 위치는 `PUT /users/{id}/location` 으로 저장된 것뿐이고,
저장된 `within_m` 이 있는데 위치가 없으면 **400** 이다(그 사실을 메시지에 적는다). 카드마다
`distance_m` (m, 정수 · 위치를 모르면 `null`) 이 붙고 `user_id` 없는 호출은 늘 `null` 이다.

필터·위치를 고칠 때는 **병합한 전체 레코드**를 `state/users.jsonl` 에 다시 append 한다
(복원은 마지막 줄이 이긴다 — 부분 레코드를 쓰면 이름·발급시각·다른 설정이 사라진다).
`DELETE` 도 한 줄 쌓는다 — 지웠다는 사실이 파일에 없으면 복원이 옛 필터를 되살린다.
빈 목록·`null` 만 있는 필터는 '설정 없음' 과 같게 본다(`users.filter_of`).

## 추천 정책은 둘이고, 한 화면을 나눠 쓴다

```
MATCHURI_POLICY = interleave (기본) | legacy | taste
```

`legacy` 는 지금까지의 정책(카테고리·가격), `taste` 는 콘텐츠 벡터 기반 새 정책이다.
`interleave` 는 10장 배치를 **두 정책이 한 장씩 번갈아** 뽑아 섞고 카드마다 정책 태그를
남긴다(팀-드래프트 인터리빙). 사용자가 적어 A/B 는 검정력이 없고, shadow 는 새 정책이
**실제로 보여줬을 카드**에 대한 반응을 영영 못 보기 때문이다. 정책별 like 율 비교가 곧
온라인 검정이고 `GET /admin/api/policies` 에서 Wilson 95% 구간과 함께 볼 수 있다.

어느 쪽이 뽑았든 **두 정책이 모든 피드백을 받는다.** 그래야 나중에 정책을 바꿔도 학습이
이어지고, 성적을 '같은 이력을 본 두 모델' 로 비교할 수 있다. 먼저 뽑는 정책은 사용자
id + 배치 번호로 씨앗을 고정한 난수로 정한다(위치 편향 상쇄 + 재현).

**서빙 산출물이 없으면 경고 한 줄을 남기고 `legacy` 로만 돈다.** 죽지 않는다.

```
MATCHURI_TREND_EVERY = 0 (기본, 꺼짐) | N  — N장에 한 장을 트렌드 슬롯으로
MATCHURI_TREND_W_SEASON = 1.0 · W_FAD = 0.5 · W_INFLOW = 0.5 · W_RATING = 0.3  — 출처별 무게(첫 추정값)
```

**트렌드 슬롯**은 세 번째 정책이 아니라 자리다. N장마다 한 장을 '요즘 뜨는 가게'
(`recsys/trend.py`, 트렌드 z>0 상위 10곳·브랜드마다 한 곳 중 씨앗 고정 난수)에서 뽑아 넣고 노출 로그에
`policy="trend"` 로 남긴다. 그래서 `GET /admin/api/policies` 의 `slot` 에 트렌드 카드의
like 율이 legacy+taste 합산과 나란히 나온다 — 사용자를 나누는 A/B 없이, 같은 화면 안에서
비교된다. 트렌드를 무게(`MATCHURI_TREND_WEIGHT`)로 켜면 두 정책이 같이 밀려 이 비교가
불가능하므로, **효과를 재는 길은 슬롯이고 무게는 결론이 난 뒤에 켠다.** 슬롯 카드의
피드백도 두 정책이 똑같이 배운다. 켜기 전에 `/admin/api/trend/concentration` 의 기준선을
적어 둘 것(TREND.md 7절).

### 정렬 보정 — 겹치는 태그 수와 거리 (`app/ranking.py`)

두 정책의 점수가 나온 **뒤** 순서를 두 가지가 더 만진다. 둘 다 `policy.pick_many(tiers=, bonus=)`
를 지나 **두 정책에 같은 값으로** 내려간다 — 한쪽만 보정하면 인터리빙 비교가 무의미해진다.

```
정렬 = (겹치는 태그 수 ↓, 정책 점수 + 거리 보너스 ↓)      np.lexsort((-total, -tier))
거리 보너스 = MATCHURI_DIST_WEIGHT(기본 0.4) × clip(z(-거리), ±3)   후보 안에서의 z
```

- **계층(tier)** 은 고른 태그 중 몇 개를 가졌나다. 태그가 OR 이 된 뒤 '둘 다 맞는 곳' 과 '하나만
  맞는 곳' 이 같은 자격으로 섞이던 것을 고친 것이고, 태그를 **2개 이상** 골랐을 때만 만든다
  (하나면 후보가 전부 1 이라 정렬에 정보가 없다). 업종은 계층에 넣지 않는다 —
  `category_group` 은 가게마다 하나뿐이라 허용 집합 안에서는 전부 1 이다.
- **거리는 점수에 섞는다.** `within_m` 은 벽이고(밖은 없는 것), 이건 '더 가까우면 더 좋다' 다.
  절대 m 을 쓰면 눈금이 정책 점수와 안 맞으므로 **후보 안에서** z 로 만들고 ±3 에서 자른다
  (`content.Z_CLIP` 과 같은 이유 — 정규화는 눈금을 맞추지만 꼬리를 못 자른다). 좌표를 모르는
  가게는 0(후보 평균)이라 벌하지도 봐주지도 않는다. 위치가 없으면 보정 자체가 없다.
- **학습에는 들어가지 않는다.** 거리는 선택 시점의 보정일 뿐이고 `m`·`v` 에 한 톨도 안 들어간다 —
  위치는 요청마다 바뀌는데 학습 상태는 `preferences.jsonl` 하나로 소수점까지 복원돼야 한다
  (`tests/test_replay.py`). 같은 이유로 `peek`·`score_state`·관리자 화면·취향 지형도는 보정을
  받지 않는다(그 화면이 묻는 것은 '모델이 무엇을 믿는가' 다).
- 실측: 역삼역·`혼밥` 필터로 20장을 받을 때 무게 0 → 0.4 로 평균 거리가 **698m → 472m**
  (`analysis/backtest/results/ranking-20260911.md`). 0.4 는 실측으로 고른 값이 아니라 첫 추정이라
  환경변수로 열어 뒀다 — 온라인 like 율을 보고 고칠 값이다.

### 구 정책 (legacy) — 품질 베이스라인

```
점수 = w(n)·품질z + (1-w(n))·개인화        n = 그 사람의 누적 스와이프
w(n) = 1/(1+n/15)                          15장에서 반반, 쌓일수록 개인화로
개인화 = 로지스틱(카테고리 11 + 가격대 5 + 품질·리뷰수) + Thompson(σ×0.4)
선택   = 최고점 + 같은 카테고리 연속 3장 금지 + 이미 보여준 카드 제외
```

### 신 정책 (taste) — 구성요소 점수 위의 가중치 8개

```
z_pop     log1p(리뷰 수)                        관심 백본        Step 0
z_q       베이지안 평균 평점                     만족 가드        Step 0
z_cf      Σ_{h∈like} 공방문코사인(h, i)          공방문           Step 1.5
z_menu    max_{h∈like} 메뉴 MaxSim(h, i)        메뉴 집합        Step 1
z_text    lse(τ=0.1)_{h∈like} 요약문 코사인      가게 요약문      Step 2
z_review  mean_{h∈like} 리뷰 평균벡터 코사인      리뷰            Step 2 (사전값 0)
z_food    lse(τ=0.1)_{h∈like} 음식사진 코사인     음식 사진        Step 3b (사전값 0)
z_neg     max_{h∈dislike} 세 유사도의 평균        기피            오프라인 근거 없음

점수 = w·z + Thompson(σ×0.4),  콜드스타트(like 0개) = z_pop + 0.5·z_q
선택 = 최고점 + 같은 브랜드 연속 금지 + 같은 cat2 3연속 금지
       + 배치 안 메뉴 MaxSim > 0.9 금지 + 10장에 1장은 불확실성 최대 카드(탐색)
```

z 는 **그 사용자의 후보 안에서** 낸다(이미 답한 가게 제외). 눈금이 modality 마다 다르고
(코사인 0.5 대 공방문 0.02) 정규화 없이 더하면 큰 눈금 하나가 전부를 결정한다. 그
modality 가 없는 가게는 유사도가 NaN 이고, **z 정규화 뒤 0**(= 후보 평균 = '모른다')이 된다.

**z 는 [−3, 3] 에서 자른다** (`content.Z_CLIP`, Step 3d). 정규화는 눈금을 맞추지만 꼬리는
못 자른다 — `cf` 는 공방문 코사인의 합이라 후보 대부분이 0 인 희소·긴꼬리 점수이고 z 가
**+41.86** 까지 간다(후보의 2.70% 가 |z| > 3). 온라인 갱신은 한 걸음이 `Δm ≈ v·Δz` 라
z 하나가 수십이면 가중치가 한 번에 수십 단위로 밀리고, 그 뒤 σ(w·Δz) 가 포화해 **어떤
피드백도 모델을 못 움직인다.** `component_scores` 한 곳에서 자르므로 서빙·peek·지형도·
재생이 전부 같은 값을 본다. 대가는 오프라인 상위 구간이 조금 눌리는 것이다
(`hit@10` −0.013~−0.040 — `analysis/backtest/results/step3d-clip-check.md`).

학습은 **쌍대(pairwise)** 다. 새 피드백이 오면 반대 라벨의 최근 5건과 짝지어
`σ(w·(z_like − z_dislike))` 위에서 한 걸음씩 간다(라플라스 근사 대각 갱신 — legacy 와
같은 골격). 반대 라벨이 아직 없으면 점별로 대신한다. 쌍이 5개라 legacy 보다 사후 분산이
빠르게 좁아진다 — 탐색은 '10장에 1장' 규칙이 따로 맡는다.

**쌍의 두 가게는 자기 근거가 되지 못한다 — leave-one-out** (Step 3d). 쌍을 채점할 때
그 쌍의 **두 가게를 like·dislike 집합에서 모두 뺀다.** 상대쪽 가게가 집합에 남아 있으면
유사도 행렬의 **대각선이 최댓값**(cooc(x,x)=1, sim(x,x)=1)이라 z(상대)가 자기 자신으로
부풀고 Δz 가 그 한 칸에 지배된다 — 실서버에서 `z_cf` 가 **+256** 이 되어 한 걸음에
`w_cf` 가 0.46 → **−63.63** 으로 밀렸다. 쌍마다 이력이 다르므로 성분 계산도 쌍마다 한 번씩
돈다(|L| = 300 에서 한 번 9ms × 최대 5쌍).

**같은 가게를 다시 평가하면 마지막 라벨만 남는다.** 새 피드백을 넣기 전에 그 가게를
**like·dislike 양쪽에서 지우고** 새 라벨의 집합에 한 번만 붙인다. 두 집합에 동시에 있는
가게는 like 이유이자 dislike 이유가 되어 위의 대각선 문제를 되살린다.

**한 걸음은 성분마다 |Δm| ≤ 0.5 다** (`taste.MAX_STEP`). 진짜 원인은 z 였고 이건 뚜껑이다 —
앞으로 어떤 성분이 또 긴 꼬리를 갖더라도 카드 한 장이 모델을 뒤집지 못하게 한다.

가중치의 사전값은 백테스트가 폴드 밖에서 학습한 λ 다(`data/kakao/serving/prior.json`).
현재 실린 것은 `step3d-clip`: `pop 1.0 · quality 0.04 · cf 0.45 · menu 0.24 · text 0.43`,
`neg −0.25`(오프라인에서 잴 수 없어 얹은 값. `prior.json` 의 `extra` 에 그렇게 적혀 있다).
**자른 z 위에서 다시 학습한 λ 다** — λ 자체는 `step3c`(자르지 않은 z) 와 소수 둘째 자리에서
거의 같지만, 사전값과 서빙 점수가 서로 다른 눈금에서 나오면 관리자 화면의 '사전값에서
얼마나 벗어났나' 가 어긋난다. 사전 표준편차는 전부 0.5.

**사전값 0 인 성분이 둘 있다 — `review` 와 `food`.** 백테스트가 점수 성분에서 뺀
것들이고(`prior.json` 의 `dropped`·`dropped_note`), 성분 자리는 남겨 두어 그 사람의
쌍대 피드백이 0 에서 밀어내면 살아난다. **오프라인이 답하지 못한 질문을 온라인에
넘기는 방식**이고, 답은 인터리빙이 준다.

| 성분 | 왜 0 인가 | 인기와의 관계 |
|---|---|---|
| `review` | Step 3a — 가장 큰 λ 를 받고도 ablation 에서 한 칸도 못 넘겼다 | `log1p(리뷰 수)` 스피어만 **+0.67** — 인기의 다른 이름 |
| `food` | Step 3c — `auc_pm` 은 세 N 에서 CI 로 올랐지만(+.0077~+.0100) `ndcg@20` N=20 이 −.0040 [−.0080, −.0003] 로 내려 '진 칸 0' 규칙에 걸렸다 | `log1p(리뷰 수)` 스피어만 **+0.14** — 인기와 겹쳐서 죽은 것이 아니다 |

`food` 를 성분으로 남긴 이유가 여기 있다. 오프라인이 말한 것은 '인기를 통제하면 이기지만
**상위 20장**을 바꿀 만큼 정확하지는 않다' 이고, 스와이프 제품이 보여 주는 것이 바로 그
상위 10~20장이라 오프라인 판정은 기각이었다. 하지만 인기와 독립적인 취향 정보를 나른다는
것도 같은 실험이 보여 준 사실이고(음식 사진의 인기 매칭 AUC `.5657` 은 이 프로젝트
콘텐츠 modality 중 **최고**다), 그 정보가 **개인의** 상위 카드를 바꾸는지는 227명 평균이
아니라 한 사람의 쌍대 피드백이 답할 질문이다. 사전 평균 0 이 그 질문의 형식이다.

집계는 `z_text` 와 같은 logsumexp τ=0.1 을 쓴다. 오프라인 블렌드에 얹은 것은
`photo_food_max` 였으므로 **이 조합을 오프라인에서 잰 적은 없다**(Step 3c 주의 ⑤).
사전 평균이 0 이라 오프라인 λ 를 물려받지 않으니 같은 τ 를 쓰는 요약문 성분과 눈금
만드는 방식을 맞췄다. 공간 사진은 성분이 아니다 — Step 3b 에서 난수 대조군을 어느
지표로도 넘지 못했다.

`POST /preferences` 한 번에 두 정책의 온라인 갱신이 각각 돈다. 학습은 **세션 안에서만**
일어나고 사용자 간에 공유되지 않는다.

기동 시 `state/preferences.jsonl` 을 순서대로 다시 먹여 같은 모델을 복원한다
(`main._apply` 가 복원과 실시간 요청 양쪽에서 쓰인다). 난수는 **선택에만** 쓰이고
학습에는 쓰이지 않으며, 성분 z 는 그 피드백을 **넣기 전** 의 like/dislike 집합에서
**쌍의 두 가게를 뺀 채**(leave-one-out) 계산하므로 복원 결과가 소수점까지 일치한다
(`tests/test_replay.py`). 실서버 기록 1,476건 재생이 20초쯤 걸린다 — 쌍마다 성분을
다시 계산하는 값이고, 그 대가로 최대 |w| 가 **63.63 → 2.01** 이 됐다.

## 서빙 산출물 (`data/kakao/serving`, 72.5MB)

서버 이미지에는 torch 도 1,024차원 임베딩도 들어가지 않는다. 들어가는 것은 후보 풀
2,216곳짜리 유사도 행렬뿐이다. `analysis/build_serving.py` 가 만든다.

```bash
~/venvs/recsys/bin/python -m analysis.build_serving      # data/kakao/serving/ 재생성
```

| 파일 | 크기 | 내용 |
|---|---|---|
| `pool_ids.json` | 29KB | 후보 풀 id — **서버 `swipe._build_pool` 과 같은 순서** |
| `sim_menu.npy` | 9.8MB | 메뉴 MaxSim top8 (float16, 없으면 NaN) |
| `sim_text.npy` | 9.8MB | 가게 요약문 코사인 |
| `sim_review.npy` | 9.8MB | 리뷰 ≤12개 평균벡터의 코사인 |
| `sim_food.npy` | 9.8MB | 음식 사진 ≤4장 평균벡터(CLIP)의 코사인 — 1,873곳, 나머지 343곳은 NaN |
| `cooc.npy` | 9.8MB | item-item 공방문 코사인 (리뷰 전부, 제외 없음) |
| `review_vecs.npy` | 21.3MB | 설명용 리뷰 벡터 (가게당 ≤ 6) |
| `review_pairs.json` | 1.6MB | 그 리뷰들의 review_id·스니펫 |
| `brand_key.json` | 70KB | 가게 → 브랜드 키 (같은 브랜드 연속 금지) |
| `layout.json` | 0.39MB | 취향 지형도 좌표·메타·5-NN (`/admin/map`) — 서버 이미지에는 UMAP 이 안 들어간다 |
| `prior.json` · `manifest.json` | 5KB | 사전값과 입력·출력 sha256 |

주의할 점:

- 행렬의 i 번째 행은 `pool_ids[i]` 다. 데이터가 바뀌어 후보 풀이 달라지면 **반드시 다시
  만들어야 한다.** 서버는 켤 때 `pool_ids.json` 과 자기 풀을 비교하고, 다르면 경고를
  남기고 legacy 로만 돈다(조용히 남의 가게 행을 읽는 것보다 낫다).
- 디스크에서는 float16(9.8MB)이지만 켤 때 float32 로 펴서 올린다(+98MB 상주). 호출마다
  변환하면 |like| = 300 에서 modality 하나당 3.5ms 가 든다 — 켤 때 한 번이면 0.6ms 다.
- **평가 사용자 제외를 하지 않는다.** 백테스트의 누수 규칙은 '정답을 미리 보지 않았다' 를
  증명하기 위한 것이고 프로덕션에는 정답이 없다. 그래서 백테스트 캐시와 값이 다르다.
- `.dockerignore` 에 `data/kakao/serving` 을 넣지 말 것. 빠지면 legacy 로만 돈다.

측정값(가상 페르소나 6종 × 20회 replay, 31~80장 구간, 품질정렬 대비, 95% CI):

| 취향이 뚜렷하면 | | 취향이 평판과 같거나 없으면 | |
|---|---|---|---|
| 카페 선호 | **+18.7%p** | 미식가(평판=취향) | −2.0%p |
| 저가 선호 | **+13.3%p** | 취향 없음 | ±0 (무해) |
| 한식 선호 | **+5.3%p** | 텍스트에만 있는 취향 | 미해결 |

기각한 방법(MF 전이·아이템 kNN·텍스트 임베딩·속성 축)과 그 이유는
[`../analysis/README.md`](../analysis/README.md) 에 있다.

## 추천 카드의 한 줄 이유

`GET /cards` 응답의 각 카드 최상위에 `recommendation_reason` 과 `policy` 가 들어간다.
`user_id` 없이 조회하면 이유만 오고 `policy` 는 `null` 이다.

```json
{"card_id": 12, "policy": "taste",
 "recommendation_reason": "좋아요한 멕시코 밥집의 리뷰와 닮았어요: '너무 맛있어서…'"}
```

이유는 **그 카드를 뽑은 정책의 점수에서** 만든다. 가장 크게 양으로 기여한 성분 하나를
문장으로 옮기고, 탐색 난수만으로 양수가 된 성분은 취향이라고 말하지 않는다(사후 평균도
양수여야 한다).

| 성분 | 문장 |
|---|---|
| 리뷰 | 좋아요한 ○○의 리뷰와 닮았어요: '…' (가장 잘 맞는 리뷰 쌍을 인용) |
| 메뉴 | 메뉴가 좋아요한 ○○과 비슷해요. |
| 공방문 | ○○에 간 사람들이 자주 가는 곳이에요. |
| 요약문 | 분위기·특징이 좋아요한 ○○와 비슷해요. |
| 음식 사진 | 음식 사진이 좋아요한 ○○와 비슷해요. |
| 인기·평판 | 리뷰 n개·평점 x인 가게예요. (콜드스타트) |
| 탐색 | 새로운 취향을 찾아보려고 골랐어요. |

legacy 정책은 예전 문장을 그대로 쓴다(평점·리뷰 수, 학습된 카테고리·가격대, 다양성).

## 관리자 화면 (`/admin`)

`app/static/admin.html` 한 장(빌드 없음, 외부 자원 없음)이다. 두 가지를 본다.

**① 정책 실험 패널** (맨 위, 10초마다 갱신) — `GET /admin/api/policies`.
지금 도는 정책과 설정값·산출물 유무, 정책별 노출/좋아요/별로/무응답과 선호율,
그리고 Wilson 95% 구간을 막대로 그린다. 판정 한 줄이 붙는다 —
`아직 판정 불가 — 노출 n장, 구간이 겹침` / `taste 우세 — 구간이 겹치지 않음`.
**구간이 겹치면 아직 모르는 것이다.** 정책 행은 응답이 주는 대로 그리므로 정책이
셋이 되어도 화면은 그대로 돈다.

**② 사용자별 학습 현황** — `GET /admin/api/users`, `/admin/api/users/{id}`.
왼쪽에서 고른 사용자의 두 모델을 나란히 본다(둘은 같은 이력을 봤다).

| 취향 정책 (taste) | 구 정책 (legacy) |
|---|---|
| 성분 가중치 w ± 사후 sd (수염), 음수는 왼쪽 | 카테고리·가격대 가중치 |
| 취향 프로토타입 ≤ 5개 (대표 가게 이름·크기) | 품질/개인화 비중 게이지 |
| 이력 — 막대 높이가 쌍대 갱신에 쓴 쌍 수 | 학습 곡선 (품질 가중·선호율) |
| 다음 카드 미리보기 + 성분 기여 w·z 분해 | 다음 카드 + 품질/개인화 분해 |

**성분 이름·개수·순서는 전부 API 가 준다**(`taste.weights` 의 `key`·`name`, 카드마다의
`parts`). 성분을 하나 더해도 화면은 안 고친다 — 막대·범례·기여 분해에 저절로 나온다.
`food` 성분을 8번째로 얹을 때 `admin.html` 은 **한 줄도 고치지 않았다**(그 계약의 첫 실증).
`tests/test_admin_ui.py` 가 그 계약(두 곳의 성분 집합이 같을 것, 화면이 성분 이름을
하드코딩하지 않을 것)을 잠근다. 좋아요가 없는 사용자는 콜드스타트 점수(`인기 + 0.5·평판`)
로 뽑히므로 막대 합이 총점과 다르고, 화면이 그 이유를 적어 준다.

화면 그림은 [`../analysis/backtest/results/admin-ui/`](../analysis/backtest/results/admin-ui/)
에 있다.

## 취향 지형도 (`/admin/map`)

관리자 화면은 '무엇을 얼마나 좋아하는가' 를 말하지만 '2,216곳 중 **어디**' 는 말하지
않는다. 지형도는 후보 풀 전체를 한 장의 2-D 지도로 펴고 그 위에 사람마다 다른 점수를
색으로 얹는다. `app/static/map.html` 한 장(빌드 없음, 외부 자원 없음, Canvas 2D)이다.

**땅은 모두에게 같다.** 좌표는 `analysis/build_serving.py` 가 한 번 굳힌
`data/kakao/serving/layout.json` 이다 — 결합 유사도 `S = Σ w·sim / Σ w`
(메뉴 .25 · 요약문 .40 · 리뷰 .20 · 음식 사진 .20, NaN 은 그 행의 유한 평균)에서
거리 `1 − S` 를 만들고 UMAP(`metric="precomputed"`, `n_neighbors=15`, `min_dist=0.1`,
씨앗 20260909)으로 편 것을 [0,1]² 로 정규화했다(두 축 배율 동일). 사용자별 가중치로
그리면 사람마다 지형이 달라져 '여기가 어디' 를 배울 수 없다 — **색만 사람마다 다르다.**

| 보이는 것 | 무엇 |
|---|---|
| 점 | 가게 하나. 사용자 미선택이면 업종(cat2) 색, 선택하면 **점수 분위수 램프**(낮으면 어둡게 잠긴다) |
| 초록·빨강 핀 | 그 사람의 좋아요·별로 (좋아요에는 옅은 후광) |
| 고리와 번호 | 아직 안 본 카드 중 점수 상위 10장 |
| 노란 연결선 | 그 카드를 밀어올린 **양의 성분마다**, 그 성분에서 가장 닮은 좋아요 가게로 (`content.best_like`). 라벨이 맞은 쌍이다 |
| 이웃 선 (토글) | 결합 유사도 5-최근접. **업종을 넘는 선은 주황** — 지금 데이터에서 **19.8%** |

**UMAP 주의 — 국소 이웃만 믿을 것.** 두 점 사이 거리는 절대값으로 읽으면 안 된다.
군집의 크기와 군집 사이 간격에는 의미가 없고, 믿을 수 있는 것은 '누구 **옆에** 있나'
뿐이다. 그래서 5-NN 이웃 목록을 좌표와 함께 싣고 화면이 선으로 그린다 — 멀리 떨어져
보이는 두 점 사이에 이웃 선이 있으면 그것은 지도가 못 편 것이지 둘이 안 닮은 것이
아니다. 이 문장은 `layout.json` 의 `caveat` 에도 들어 있다.

**API 는 읽기 전용이다.**

| 경로 | 내용 |
|---|---|
| `GET /admin/api/map/layout` | 고정 좌표·기존 메타·5-NN에 원본 `cat2`·`cat3` 배열 추가, 직렬화 결과 메모리 캐시 |
| `GET /admin/api/map/user/{id}` | 그 사람의 점수 2,216개 + 상위 10장의 성분 기여·연결선 (34KB) |
| `GET /admin/api/policies` | 기존 것을 그대로 쓴다 (헤더의 정책 표시) |

확대 단계는 `cat2 → cat3 → 개별 가게`다. 분류 배열은 `ids`와 정확히 같은 순서이며
결측은 `null`이다. 같은 `cat3` 이름도 `cat2`별로 나누고, 기존 좌표·`cats`·`cat`은 유지한다.
레이아웃 또는 가게 원본 파일이 바뀌면 캐시를 갱신하고, 반복 조회는 저장된 응답 바이트를 재사용한다.

지도 API 는 **카드를 '보여줬다' 고 표시하지 않고**(`taste.pick_many` 를 부르지 않는다),
`state/exposures.jsonl` 을 쓰지 않고, 어떤 모델도 갱신하지 않는다. 그래야 관리자가 지도를
아무리 돌려도 팀-드래프트 인터리빙 결과가 오염되지 않는다 —
`tests/test_map.py: test_지도는_아무것도_바꾸지_않는다` 가 세션·`shown`·노출 로그를
호출 전후로 비교해 잠근다. 화면에서 상태를 바꾸는 곳은 **스와이프 버튼 하나**뿐이고
그것은 평소와 같은 `POST /preferences` 다(화면에도 그렇게 적혀 있다).

- **가중치 슬라이더 8개** (`?weights=` 쉼표 8개, 성분 순서 그대로). '이 성분을 더 믿었다면
  지형이 어떻게 달라지나' 를 묻는 도구다 — 서버는 같은 성분 점수를 그 가중치로 다시 더할
  뿐이고 **학습값(m·v)은 한 톨도 안 바뀐다.** 슬라이더 범위는 −1.5~2 이고, 쌍대 갱신은
  한 걸음이 클 수 있어 학습값이 그 밖으로 나갈 수 있다 — 그때는 막대가 끝에 붙고
  **숫자가 실제 값**이며 화면이 그 사실을 적는다.
- **이력 스크러버** (`?upto=n`). `state/preferences.jsonl` 을 그 사용자로 걸러 앞 n 장만
  **등록하지 않은 새 세션**에 먹여 그 시점의 지형을 다시 그린다. 학습에 난수가 없어
  `upto=전체` 는 살아 있는 세션과 소수점까지 같다. 되감은 상태에서는 스와이프를 막는다 —
  과거 모델에서 누르면 '변화량 before → after' 가 과거 대 현재가 되어 거짓말을 한다.
- **연결선의 근거**는 종류를 밝힌다. 리뷰 쌍만이 벡터가 고른 것(`content.review_pair`)이고,
  메뉴 쌍은 **글자(2-gram)가 겹치는 쌍**이다 — 서버 이미지에 메뉴 벡터가 실리지 않아
  모델이 맞춘 그 쌍을 복원할 수 없다. 응답의 `pair_label` 이 그 사실을 한국어로 적고
  화면은 그것을 그대로 그린다.

성분 이름·개수·순서는 여기서도 전부 API 가 준다(`weights` 의 `key`·`name`, 카드마다의
`parts`, 연결선의 `name`). 화면 어디에도 성분 이름이 박혀 있지 않다 —
`tests/test_map.py: test_성분_이름을_화면이_모른다` 가 admin.html 과 같은 규칙으로 잠근다.

화면 그림은 [`../analysis/backtest/results/map-ui/`](../analysis/backtest/results/map-ui/)
에 있다.

## 운영 — 로그·지표·헬스

**요청 하나가 로그 한 줄**이다. stdout 에 JSON 으로 나가므로 `docker logs | jq` 가 그대로 통한다.

```json
{"ts":"2026-09-10T16:32:53+09:00","level":"INFO","event":"http","request_id":"trace-abc_123",
 "method":"GET","path":"/cards","route":"/cards","status":200,"ms":12.4,"client_ip":"1.2.3.4",
 "user_id":"u_8b7dd6f7ac89","ua":"okhttp/4.12","bytes_out":6414}
```

`route` 는 **경로 템플릿**(`/cards/{card_id}`)이라 카드 id 마다 줄 종류가 늘지 않는다.
`client_ip` 는 `X-Forwarded-For` 의 첫 홉(nginx 뒤에 있으므로), 없으면 소켓 주소다 — 위조할 수 있는
값이라 로그에만 쓴다. `user_id` 는 경로·쿼리·(`POST /preferences` 는) 본문에서 온다.

| event | 언제 | 주요 필드 |
|---|---|---|
| `http` | 요청 한 건 | 위 예시. `/healthz`·`/readyz`·`/metrics` 는 **DEBUG** 라 기본 설정에서는 안 남는다 |
| `http.slow` | 500ms 초과 (WARNING) | `route`·`ms`·`limit_ms` |
| `http.client_error` | 4xx·422 | `status`·`path`·`detail`(어느 필드가 왜 틀렸나) |
| `error` | 처리 못 한 예외 (ERROR) | `error`·`detail`·`traceback` — **traceback 은 로그에만** 남는다 |
| `boot` · `state.restore` | 기동 | 버전·정책·CORS·복원한 사용자/선호 수·ms |
| `user.create` · `user.location.set` · `user.filter.set` · `user.filter.clear` | 설정 변경 | `user_id` 와 바뀐 값(좌표는 소수 셋째 자리로 반올림) |
| `pref.record` | 스와이프 | `card_id`·`store_id`·`prefer`·`learned`(배운 정책)·`pairs` |
| `cards.serve` | 추천 응답 | `n_requested`·`n_returned`·`policies`·`filter`·`has_location`·`ms` |
| `taste.view` | 취향 화면 | `likes`·`region`·`drift` |
| `state.write_fail` | 파일에 못 씀 (ERROR) | `path`·`error` — 이 뒤로 영속화가 꺼진다 |
| `policy.fallback` | 산출물이 없어 legacy 로 후퇴 (WARNING) | `wanted`·`using` |

**한 사고를 id 하나로 따라간다.** 모든 응답에 `X-Request-ID` 가 실리고(클라이언트가 보낸 값을
쓰거나 서버가 만든다), 그 요청 중에 남은 모든 줄에 같은 `request_id` 가 붙는다.

```bash
docker logs matchuri-be 2>&1 | jq -c 'select(.request_id=="trace-abc_123")'
docker logs matchuri-be 2>&1 | jq -c 'select(.event=="http" and .ms>500)'      # 느린 요청
docker logs matchuri-be 2>&1 | jq -c 'select(.event=="pref.record")' | tail -20
docker logs matchuri-be 2>&1 | jq -r 'select(.event=="error") | .traceback'
```

| 경로 | 무엇 |
|---|---|
| `GET /healthz` | 프로세스가 살아 있나(의존성을 보지 않는다). liveness |
| `GET /readyz` | 데이터·후보 풀·`state/` 쓰기 확인. 하나라도 아니면 **503** + `reasons`. readiness |
| `GET /metrics` | Prometheus 텍스트. 의존성 없이 프로세스 안에서 센다(재시작하면 0) |

### 지표 목록

의존성 없이 손으로 쓴 Prometheus 텍스트다(형식은 `tests/test_observe.py` 의 엄격 파서가
잠근다 — 한 줄만 어긋나도 스크레이프가 통째로 버려진다). 카운터는 재시작하면 0 이고,
Prometheus 는 그 리셋을 알아서 처리한다.

| 지표 | 종류 | 뜻 |
|---|---|---|
| `matchuri_http_requests_total{route,method,status}` | counter | 처리한 요청 수. `route` 는 **경로 템플릿** |
| `matchuri_http_request_seconds{route}` | histogram | 요청 시간. 버킷 5ms~5s, 라벨은 `route` 하나뿐(카디널리티) |
| `matchuri_http_slow_total{route}` | counter | 500ms 를 넘은 요청 수 |
| `matchuri_errors_total{route}` | counter | 5xx 로 끝난 요청 수 |
| `matchuri_http_inflight` | gauge | 지금 처리 중인 요청 수(**스크레이프 자신을 포함해 기준선이 1**) |
| `matchuri_preferences_total{prefer,policy}` | counter | 선호 표현. `policy` = 그 카드를 **보여준** 정책(`legacy`·`taste`·`none`) |
| `matchuri_cards_served_total{policy}` | counter | 내보낸 **카드 수**(뽑은 정책별) |
| `matchuri_cards_requests_total{filtered,personalized}` | counter | `GET /cards` **요청 수** |
| `matchuri_cards_short_total` | counter | `limit` 을 못 채운 요청 수 — '필터가 좁다' 의 신호 |
| `matchuri_cards_ranked_total{by}` | counter | 그 요청의 순서를 무엇이 정했나 — `tier`(겹치는 태그 수)·`distance`·`none` |
| `matchuri_bookmarks_total{action}` | counter | 찜이 바뀐 횟수(멱등 호출은 안 센다) |
| `matchuri_taste_views_total` | counter | 취향 화면 조회 수 |
| `matchuri_filter_sets_total` · `matchuri_location_sets_total` | counter | 설정을 저장한 횟수 |
| `matchuri_users_created_total` | counter | 발급한 `user_id` 수 |
| `matchuri_users_total` (= `matchuri_state_users_total`) | gauge | 등록부 사용자 수. 두 이름은 같은 값이다(대시보드 호환) |
| `matchuri_sessions` | gauge | 학습 중인 세션 수 |
| `matchuri_state_prefs_total` · `matchuri_state_bookmarks_total` | gauge | 메모리에 올라온 선호 기록 수 · 담겨 있는 찜 수 |
| `matchuri_restore_seconds` | gauge | 마지막 기동의 복원 시간(배포 뒤 '왜 늦게 떴나') |
| `matchuri_artifacts_available` | gauge | 서빙 산출물 유무(0 이면 legacy 로만 돈다) |
| `matchuri_process_resident_bytes` · `_cpu_seconds_total` · `_open_fds` · `_start_time_seconds` · `_threads` | gauge/counter | `/proc/self` 에서 직접 읽는다(psutil 없이). 리눅스가 아니면 이 계열만 빠진다 |
| `matchuri_build_info{version,policy}` | gauge | 항상 1. 버전·정책을 라벨로 나른다 |

**`policy` 라벨이 곧 온라인 실험이다.** 선호가 들어온 순간 '그 카드를 누가 보여줬나' 를
메모리 지도(`recsys/policy.py` 의 `_exposed`, 20만 쌍 상한, 첫 노출이 이긴다)에서 읽어 붙인다.
기동 때 `state/exposures.jsonl` 로 그 지도를 다시 만들기 때문에 재배포가 비교를 끊지 않는다.
노출 기록이 없는 카드(찜 목록에서 바로 평가 등)는 `none` 이고, 그것도 알아야 하는 값이다.

### 대시보드 PromQL (여섯 패널)

```promql
# ① 초당 요청 (경로별)
sum by (route) (rate(matchuri_http_requests_total[5m]))

# ② 에러율 — 5xx 비율
sum(rate(matchuri_errors_total[5m])) / sum(rate(matchuri_http_requests_total[5m]))

# ③ p95 지연 (경로별)
histogram_quantile(0.95,
  sum by (route, le) (rate(matchuri_http_request_seconds_bucket[5m])))

# ④ 정책별 like 율 — 팀-드래프트 인터리빙의 온라인 결과
sum by (policy) (rate(matchuri_preferences_total{prefer="like"}[1h]))
  / sum by (policy) (rate(matchuri_preferences_total[1h]))

# ⑤ 초당 내보낸 카드 (정책별)
sum by (policy) (rate(matchuri_cards_served_total[5m]))

# ⑥ 필터 사용 — 필터를 건 요청 비율과 '못 채운' 비율
sum(rate(matchuri_cards_requests_total{filtered="true"}[30m]))
  / sum(rate(matchuri_cards_requests_total[30m]))
sum(rate(matchuri_cards_short_total[30m])) / sum(rate(matchuri_cards_requests_total[30m]))
```

환경변수 셋을 더 읽는다.

| 변수 | 기본 | 뜻 |
|---|---|---|
| `MATCHURI_LOG_LEVEL` | `INFO` | `DEBUG` 면 헬스체크 줄까지 나온다 |
| `MATCHURI_CORS_ORIGINS` | (없음) | 쉼표 목록일 때만 CORS 를 켠다. `*` 면 자격증명은 허용하지 않는다 |
| `MATCHURI_DIST_WEIGHT` | `0.4` | 거리 보너스의 무게(z 한 칸당 점수). `0` 이면 거리를 정렬에서 뺀다 |
| `MATCHURI_STATE` | `BE/state` | 상태 파일 위치(예전부터) |

**입력 검증.** `user_id` 는 어디서 오든 영문·숫자와 `. _ : -` 1~64자만 받는다(아니면 422, 한국어 메시지).
이 값은 URL·상태 파일·로그 줄에 그대로 들어가므로 제어문자가 섞이면 로그 한 줄이 여러 줄이 된다.
`POST /users` 의 `name` 도 제어문자를 지우고 50자로 자른다. 처리 못 한 예외는 `{"detail": "internal error",
"request_id": …}` 만 돌려준다 — 스택은 로그에만 남긴다.

## 배포

`../Dockerfile` 이 아니라 `server/Dockerfile` 이지만 **빌드 컨텍스트는 BE/** 다
(`data/kakao` 가 이미지에 필요하기 때문). 컨테이너 안 배치는 저장소와 똑같다 —
`recsys/store.py` 가 `parents[2]/data` 를 보므로 배치가 어긋나면 데이터를 못 찾는다.

```
/app/server/{main.py,app,recsys}
/app/data/kakao
```

## 손댈 때 주의

- `card_id` 는 `store_id` 를 정렬해 매긴 번호다. **데이터가 바뀌면 번호도 바뀐다.**
  DB 를 붙이면 그때 고정 키로 옮겨야 한다.
- `app/loader.py` 는 데이터를 직접 읽지 않고 `recsys.store` 를 쓴다. 같은 메모리를
  공유하므로 40MB 를 두 번 올리지 않는다.
- 세션은 프로세스 메모리지만 **선호 표현은 `state/` 에 남는다.** 재시작하면 로그를
  되먹여 복원한다. 로그를 지우면 학습도 같이 사라진다.
- `POST /logs`, `GET /logs/{user_id}` 는 예전 경로다. 계속 동작하지만 문서에는 숨겼다.
- **`store.reviews` 에는 우리 사용자 후기가 섞여 있다**(2026-09-21, `app/user_reviews.py`).
  상세와 `/cards/{id}/reviews` 에만 붙고 **목록에는 안 붙는다**(카드 수만큼 곱해지는 자리다).
  맨 앞 고정이라 상세의 `store.reviews` 길이가 `reviews` 파라미터보다 클 수 있고
  `store.review_count` 도 그만큼 크다. 우리 글을 골라내는 조건은
  `source_platform == "matchuri"` 또는 `id >= 10**12`. **`store.score` 와 추천 점수는
  건드리지 않는다** — 표시와 점수는 다른 문제다.
- 응답 계약은 **더하기만** 했다. `/cards` 에 `policy`·`distance_m`, `learned` 에
  `policies`, `/admin/api/users/{id}` 에 `taste`, 사용자 레코드에 `location` 이 붙었고
  없앤 필드는 없다.
- 노출은 `state/exposures.jsonl` 에 쌓인다(`{user_id, card_id, policy, batch, rank, at}`).
  취향 모델의 입력이 아니라 **인터리빙 실험의 자료**다. 지워도 추천은 그대로 돌고
  정책별 성적만 사라진다.
- **성분 z 의 눈금을 바꾸면 사전값도 같이 바꿔야 한다.** `content.Z_CLIP` 을 손대면
  `data/kakao/serving/prior.json` 의 λ 가 다른 눈금에서 학습된 값이 된다 —
  `analysis/backtest/step3d.py` 를 다시 돌려 확인하고, 규칙에 걸리면
  `python -m analysis.build_serving --only prior,manifest` 로 다시 만든다.
- **취향 정책의 학습 규칙 넷은 서로 붙어 있다** — leave-one-out · 마지막 라벨만 남기기 ·
  `Z_CLIP` · `MAX_STEP`. 하나만 되돌리면 실서버에서 봤던 폭주가 그대로 돌아온다.
  `tests/test_taste.py` 의 `test_되평가_순서가_가중치를_폭주시키지_않는다` 와
  `test_가드를_끄면_같은_입력이_실제로_폭주한다` 가 그 짝을 잠근다.

## 사용자와 추천 세션 (2026-09-16)

`user_id`는 계속 보관하는 사용자 키이고, 추천 세션은 한 번의 음식점 탐색 단위다.
한 사용자 아래 여러 세션을 독립적으로 보관한다. 기존 클라이언트는 호환 경로를 계속 사용할 수 있다.

- 실제 사용자: `POST /user`로 발급한 `usr_...` ID. 추천 모델을 직접 갖지 않는다.
- 독립 세션: `POST /user/{user_id}/session`으로 `s_...` ID 발급. `GET` 같은 경로는 목록,
  `GET /user/{user_id}/session/{session_id}`는 해당 세션의 `{user_id, session_id, swipes}`다.
- cards/preferences/decision/metrics는 위 명시적 세션 경로 아래에서 기존 서비스를 사용한다.
  preferences 본문에는 `{card_id, prefer}`만 넣는다. 사용자/세션 소유 불일치는404다.
- 새 세션을 만들어도 기존 세션의 모델·스와이프·노출·decision을 지우지 않는다.
  같은 사용자의 여러 세션을 다시 열어 계속 사용할 수 있다. 생성 재시도는 선택적 request_id로 멱등 처리한다.
- 사용자 참고 취향은 그 사람의 모든 세션 반응을 도착 순서대로 학습한다. 새 세션 생성 시에만
  약한 사전값으로 복사하고, 이미 열린 다른 세션의 진행도·검증에는 섞지 않는다.
- 찜·방문은 사용자 소유, 위치·필터는 각 탐색 세션 소유다.
- 이전 user_id는 기존 세션 ID로 유지한다. `GET /session/{기존ID}`로 새 소유 user_id를 조회한다.
  각 기존 ID에 별도 사용자 하나를 연결한다. 이름/취향으로 사람을 임의 병합하지 않는다.
- 기존 /users 경로와 user_id를 받는 cards/preferences는 호환용이다. 옛 세션 초기화 경계도
  각각 보관하여 새 경로로 이전 구간을 조회할 수 있다. 상세 연동은 API.md 첫 절 참조.

이전 세션은 전역 사전값에서 벗어난 가중치의20%와 모든 고유 가게의 벡터 참조 근거로
약하게 이어받는다. 과거의 작은 분산이나 검증 성공 횟수는 상속하지 않는다.
현재 반응으로 같은 가게를 다시 평가하면 이전 라벨을 덮어쓴다.

Legacy의 초기 품질 혼합이 상속한 취향을0배로 만들지 않도록, 상속 사전값은 첫 카드부터
한 번 반영한다. `품질계수 × 품질 + 상속값 + (1-품질계수) × (현재가중치-상속값)`이며
상속이 없는 사용자의 기존 점수는 그대로다. 품질계수는 품질 항의 계수이지 모든 기여의 백분율이 아니다.
스와이프 서빙에서는 이전에 싫어요한 정확한 가게를 양쪽 정책 공통으로 후순위에 둔다.
먼저 다른 허용 후보를 제공하고, 대안이 소진되면 원래 필터 안에서 다시 제공한다.
새 세션의 shown을 과거 노출로 채우거나 같은 업종 전체를 막지 않으며, 최신 재평가가 우선한다.
최종 decision 후보는 이 스와이프 재노출 순서와 별개로 기존 적합도·다양성을 사용한다.

최근 반응은 스와이프 순서에 따른 지수 감쇠를 사용한다.10회가 지나면 근거의
가중치는1/2,20회면1/4이다. 성분 집계와 쌍대 학습 모두 감쇠를 적용하고,
누적된 사후 정밀도도 세션 사전값 쪽으로 완화해 오래 사용한 모델이 굳지 않게 한다.
이전 세션의 특징 혼합 비중은 처음 최대20%에서 같은 속도로 감소한다.
이는 엔트로피나 선호 확률이 아니며,10회/20%는 운영 정책값이다.
20~30회 내 모든 사용자의 결정 성공을 보장하는 수치로 해석하면 안 된다.

사용자는 `people.jsonl`, 독립 세션은 `preferences.jsonl`의 `event=session_create`에 기록한다.
새 반응/노출 행에는 실제 user_id와 session_id를 분리해 기록한다. 기존 `event=session_start`도
호환 복원하여 이전 구간을 보존한다. 옛 행은 최초 세션으로 읽고 원본은 수정하지 않는다. 재시작 시 경계와
선호를 순서대로 재생한다. 현재 구성은 단일 프로세스이고 다중 worker 간 락은 제공하지 않는다.

`/admin/map`은 최근10회, 세션 시작, 직전1회 또는 지정한 두 시점을 비교한다. 같은 후보 전체의
순위 이동을 고정 색 범위(±25%p)로 보여준다. 평균 이동량은 확신도가 아니며,
상위10곳 신규 진입도 노출 제외와 무관한 전체 후보 순위 기준이다. 지도 위치와
고차원 벡터는 그대로 두고, 동점은 평균 순위로 처리한다. 가중치 조작은 실험으로 표시된다.

취향 경로는 각 시점의 개인화 점수(인기·평판 제외) 상위 최대20곳에서 고차원 성분
유사도의 평균이 가장 높은 실제 가게를 골라 시간순으로 연결한다. 2차원 중심점이 아니며,
선의 길이·방향은 취향 변화의 크기나 의미가 아니다. 모든 반응을 재생하고 표시점만
최대25개로 줄인다. `GET /admin/api/map/user/{user_id}/trace`는 세션 ID와 조회 시점,
표시 간격, 대표 가게의 인덱스·스와이프 순서를 반환하는 읽기 전용 관리자 API다.

직접 요청한 `GET …/decision`은 기본 manual이며 점수와 무관하게 현재 정보로 n곳을 제공한다.
사용자 필터·거리는 지키고, 적합하고 다양한 조합을 우선하되 엄격한 조합이 없으면 현재
후보 안에서 차선의 선택지를 제공한다. 취향 반응이 없는 초기에는 기존 기본 점수를 쓴다.
`mode=auto`와 스와이프의 `include_decision=true`는 기존 자동 권유 조건을 유지한다.
auto는 고유 평가30~60곳에서 예측 검증을 완화하지만 적합성·다양성·필터·최소 긍정 근거는 유지한다.
상세 metrics의 decision/gates/slate는 자동 권유 기준을 설명한다.

summary의 `decision_readiness_score`는 클라이언트 표시용 점수이고 **적중 누적**이다
(`decision_summary.advance_display`). 답을 보기 전 매긴 개인화 순위(`personal_rank`)와 실제
응답이 같은 방향이면 오르고 반대면 내려가며, 연속으로 맞히면 한 장의 증가폭이 커진다
(`BASE_GAIN`·`STREAK_STEP`·`STREAK_CAP`). 빗나간 장에서는 내려가되 **한 번에 이제까지 쌓은
값의 절반까지만** 깎는다(`MAX_MISS_SHARE`) — 예전의 고정폭 `MAX_SCORE_DROP`이 아니라 누적에
비례하는 상한이다. 누적이 양수이면 화면은 최소 1%이므로(`MIN_SHOWN_ONCE_POSITIVE`), 0%는
"이 세션에서 아직 아무것도 맞히지 못했다" 전용이다. 기준이 얼마나 채워졌나(`personalization.readiness_score`)와는 **다른 값**이다.

기존 앱은 API0~80을 화면0~100%로 환산하고 `>= 80`을 준비됨으로 쓰므로, **80은
`personalization.ready`가 참일 때만** 나온다(그 전 천장은79 = 화면98.8%). 화면 100%와
서버 `ready`가 정확히 일치한다 — FE는 한 줄도 고치지 않았다.
학습으로 받아들인 스와이프에서만 한 번 접히고 조회·필터 변경으로는 움직이지 않으며,
preferences 재생·스냅샷 복원으로 같은 값이 나온다(난수 없음).

**새 결정 세션이 물려받는 것은 누적이 아니라 '증가 속도' 다.** 새 세션의 누적·화면은
**정확히 0** 에서 시작한다 — 이 세션이 아직 한 장도 맞히지 않았는데 화면에 30%가
적혀 있으면 그 숫자가 거짓이기 때문이다. 대신 이전 세션에서 쌓은 누적으로 배속
(`taste.HIT_BOOST_KEY` = `hit_carry_boost`)을 정해, **적중한 장의 증가폭에만** 곱한다
(`decision_summary.carry_boost` · `CARRY_BOOST=1.0` -> 1.0~2.0배). 빗나간 장에는 곱하지
않는다 — 양쪽에 곱하면 오르내림이 같이 커져 진동만 하고 빨리 가지 않는다. 그래서 같은
답을 한 두 사람 중 이전 세션이 있는 쪽이 **같은 장수에서 더 높은 점수**에 닿는다
(실측: 같은 답 순서에서 18장째 72.9 대 44.5, 천장 도달 20장 대 26장).

승계 경로는 둘이다: 세션 회전(`POST /users/{id}/sessions`)은 직전 정책 세션에서,
새 세션 생성(`POST /user/{id}/session` — 앱이 쓰는 길)은 계정 기억
(`recommendation_sessions._user_memory`)에서 배속을 계산한다. 그래서 계정 기억도
`remember()` 에서 같은 규칙으로 적중을 접는다 — 접지 않으면 앱 경로의 배속이 항상
1.0 이다. 한 장도 답하지 않은 세션을 다시 리셋할 때는 배속을 **재계산하지 않고 그대로
복사한다**(`taste.start_from` 의 빈 세션 분기). 재계산하면 그 세션의 누적이 0 이라
배속이 1.0 으로 리셋되어, 스와이프 없이 두 번 리셋한 사람이 속도를 잃는다.
연속 적중수는 어느 경로로도 물려주지 않는다.
정확도·학습 완료율이 아니며 스와이프 횟수만으로는 오르지 않는다.
점수가 낮아도 manual은 호출 가능하다. 클라이언트는 점수로 선택 버튼을 막지 않는다.

**진행 점수에는 순위 안정성이 들어가지 않는다**(`decision_summary.PROGRESS_GATES`).
안정성은 미평가 상위 목록을 한 번 전·세 번 전 학습 상태와 비교해 겹침이 작은 쪽을 쓰므로
학습이 진행되는 동안 내려간다 — 남은 일이 아니라 방금 배운 양을 재는 값이다. 운영 산출물
재생에서 번갈아 답한30장은 이 게이트에 막혀 진행 점수가 한 번도80(앱 화면100%)에 닿지
못했고, 최약 게이트가 안정성인 장수가15/30이었다. 진행 척도로 남길 명분인 "선호 추출 정도를
반영한다"도 확인되지 않았다 — 잠재 취향이 있는 가상 사용자로 잰 화면 퍼센트 대 미래 AUC
순위상관이+0.08이었다(동전 라벨 사용자는-0.16). `ready`(`PERSONALIZATION_GATES`)는 안정성을
그대로 요구하고 `stability_score`·상세 gates도 그대로이므로, **자동 권유 기준은 바뀌지 않았고
진행 점수가100이어도 `ready`는 false일 수 있다.**
