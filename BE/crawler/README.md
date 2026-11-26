# crawler — 수집

카카오맵 웹이 실제로 부르는 내부 API 를 그대로 쓴다. 공개 문서가 없어 웹 번들
(`pi3P160w.js`)을 읽어 파라미터를 확인했다.

**원칙: 응답 원본을 통째로 적고, 파싱은 따로 한다.** 필드를 더 뽑고 싶어질 때
6시간짜리 재수집을 하지 않기 위해서다.

## 파이프라인

```bash
python yeoksam_stores.py        # ① 역삼동 음식점 목록      -> data/yeoksam/stores.jsonl (2,801곳, 커밋됨)
python yeoksam_full.py          # ② 응답 원본 통째로        -> data/yeoksam_full/{panel,reviews,photos,blog}.jsonl
python yeoksam_full_parse.py    # ③ 정규화 테이블           -> data/yeoksam_full/parsed/
python build_runtime_data.py --out ../data/kakao   # ④ 서버용 슬림 (538MB -> 39MB)
cd ../ && python -m analysis.build_serving         # ⑤ 유사도 행렬 -> data/kakao/serving/
```

**⑤를 빠뜨리면 조용히 망가진다.** ④가 가게 목록을 갈아 끼우면 후보 풀 순서가 바뀌는데
`serving/pool_ids.json` 은 옛 순서다. 서버는 그걸 감지하고 **죽지 않고 legacy 정책으로만
돈다** — 에러도 없고 카드도 정상으로 보이지만 개인화가 빠진다(`recsys/content.py`).

    확인:  /readyz 의 `artifacts` 가 **false** 면 이 상태다 (`ok` 는 true 라서 놓치기 쉽다)
    로그:  "[content] 후보 풀이 산출물과 다릅니다 ... legacy 정책으로만 돕니다"

②는 약 **6시간**(2,801곳 × 평균 5.9호출, 0.45초 딜레이). 이어받기가 되므로 중단해도 된다.
①의 산출물만 커밋돼 있으니 ②부터 바로 돌릴 수 있다.

갱신 주기는 **주 1회, 손으로** 돌린다. 절차는 아래 '주 1회 갱신' 을 본다.

보수용: `yeoksam_full_repair_reviews.py` — panel 의 리뷰 집계가 0 으로 오는데 실제로는
리뷰가 있는 가게(사장님이 노출을 막은 163곳)를 다시 받는다.

## 가게당 무엇을 받나

| 호출 | 얻는 것 |
|---|---|
| `panel3/{id}` | 요약·메뉴·영업시간·AI요약·방문자 시간대·지하철·편의시설·태그·리뷰통계·사진집계·배달연동 |
| `tab/reviews/kakaomap/{id}` × 2 | 리뷰 전문 + 작성자 프로필 + 리뷰 태그 + 사진 (추천순·최신순 합집합) |
| `tab/photos/{id}?tag=X` | 분류별 사진 — 매장주제공·업체제공·음식·분위기·외관·메뉴판 |
| `tab/reviews/blog/{id}` | 블로그 리뷰 10건 |

사진 탭의 `?tag=` 파라미터는 문서가 없어 웹 번들에서 찾았다. **'후기'(KAKAOMAP_REVIEW)
태그는 일부러 빼고 받는다** — 사람이 찍힌 사진이 섞이기 때문이다.

## 알아야 할 것 (전부 실측)

| 증상 | 원인 | 대응 |
|---|---|---|
| 검색 `place_totalcount` 가 8,333 인데 실제 2,450 | 신뢰할 수 없는 값 | 검색어를 쪼개 합집합, 주소·분류로 필터 |
| `panel3` 의 리뷰 집계가 0 인데 리뷰가 있음 | 사장님이 노출 차단(163곳, `PLACE_OWNER_BLOCK`) | 리뷰 탭을 직접 호출 (`*_repair_reviews.py`) |
| 메뉴 `price` 가 `-1` / `0` | 카카오의 '가격 없음' 표기 (전체의 44%) | `build_runtime_data.py` 에서 `null` 로 |
| 검색 API 의 `rating`/`rating_count` 가 전부 0 | 쓸 수 없음 | 리뷰 탭의 `score_set` 을 쓴다 |
| 사진 URL 이 대부분 네이버 CDN | 카카오가 블로그 사진을 끌어다 보여줌 | 정상. 핫링크는 6개 호스트 모두 열려 있다 |

네이버 지도 수집기(`naver_client.py` 등)도 있다. TLS 지문 때문에 `curl_cffi` 로
크롬을 흉내내고, GraphQL 은 `X-WTM-GraphQL` 헤더가 필요하다. 지금 서비스는 카카오만 쓴다.

## 주 1회 갱신

데이터는 주마다 통째로 다시 받는다. **자동화하지 않는다** — 아래를 손으로 돌린다.

### 왜 서버가 아니라 내 PC 에서 돌리나

이건 공개 API 가 아니라 카카오맵 내부 엔드포인트를 `curl_cffi` 로 브라우저인 척 부르는
것이다. 제한이 걸리면 **요청을 보낸 IP** 가 대상이 되는데, 서버 IP 는 서비스·Jenkins·
Grafana 를 같이 돌린다. 로컬에서 돌리면 최악의 경우에도 서비스는 멀쩡하다. AWS 대역은
안티봇이 기본적으로 더 의심하기도 한다.

속도로 얻는 것도 없다. 6시간은 CPU 가 아니라 `--delay` 때문이라 서버에서도 똑같다.

### 회차(run)

`yeoksam_full.py` 가 모든 줄에 `run` 을 적는다. 기본값은 **오늘 날짜**다.

    건너뛰기 대상 = 이번 회차에 **성공한** 가게

그래서 날짜가 바뀌면 저절로 전수 재수집이 되고, 같은 날 다시 돌리면 중단 지점부터
이어받는다. 실패한 가게는 건너뛰지 않으니 이어받기가 다시 시도한다.

자정을 넘겨 이어받을 때만 회차를 직접 준다: `--run 2026-09-14`

### 절차

```bash
cd BE/crawler

# 1. 전수 재수집 (약 6시간. 중단해도 같은 명령으로 이어받는다)
python yeoksam_full.py

# 2. 정규화 — 이번 회차에 응답한 가게만 나온다(= 폐업 제외)
python yeoksam_full_parse.py

# 3. 서버용 슬림 (538MB -> 39MB)
python build_runtime_data.py --out ../data/kakao

# 4. 유사도 행렬. **빼면 안 된다** — 개인화가 조용히 꺼진다(위 파이프라인 설명 참고)
cd .. && python -m analysis.build_serving && cd crawler

# 5. 눈으로 확인
wc -l ../data/kakao/stores.jsonl        # 2,700~2,800 이면 정상
ls -la ../data/kakao/serving/           # *.npy 6개의 시각이 방금이어야 한다
```

### 어떤 파일이 바뀌나

되돌릴 수 있는지 판단하려면 단계마다 다르다는 것을 알아야 한다.

| 단계 | 대상 | 방식 |
|---|---|---|
| ① 수집 | `data/yeoksam_full/*.jsonl` | **추가 전용.** 지우지 않는다. 매주 커진다 |
| ② 정규화 | `data/yeoksam_full/parsed/*` | **통째로 덮어쓴다** |
| ③ 슬림 | `data/kakao/*.jsonl` | **통째로 덮어쓴다** |
| ④ 행렬 | `data/kakao/serving/*` | **통째로 덮어쓴다** |

**원본은 안전하고 가공물은 멱등이다.** ①이 추가 전용이라 과거 회차가 그대로 남고,
②③④는 몇 번 돌려도 같은 결과다. 잘못 만들었으면 원본에서 다시 만들면 된다
(재수집 없이 몇 분이면 된다).

로컬에서 돌리는 것은 **서버를 건드리지 않는다.** 아래 '서버에 반영' 을 해야 반영된다.

### 폐업 처리와 안전장치

`panel.jsonl` 은 추가 전용이라 한 번 수집된 가게는 영원히 남는다. 그대로 내보내면 **문
닫은 가게가 매주 되살아난다.** 그래서 ②가 이번 회차에 응답한 가게만 내보낸다.

문제는 '수집이 끊긴 것' 과 '폐업' 이 똑같이 보인다는 점이다. 차단당하거나 노트북이 꺼져
1,000곳만 받은 상태로 내보내면 멀쩡한 가게가 조용히 사라진다. 그래서 **10% 넘게 줄면
멈춘다.**

```
회차 2026-09-14: 아는 가게 2,795곳 중 2,401곳이 응답 (빠짐 394곳)
멈춘다: 394곳(14.1%)이 빠졌다. 기준은 10% 다.
  수집이 끊겼거나 차단당했을 가능성이 크다.
  먼저 `yeoksam_full.py --run 2026-09-14` 로 이어받아라.
  정말 폐업이면 --allow-shrink 를 준다.
```

이 메시지가 나오면 **먼저 이어받아라.** `--allow-shrink` 는 이어받아도 계속 빠질 때만
쓴다. 리뷰·사진·블로그는 회차로 거르지 않는다 — 가게는 응답했는데 리뷰 호출만 실패한
경우 직전 회차의 리뷰를 쓰는 편이 빈 값보다 낫다.

### 서버에 반영

> **먼저 확인할 것.** 서버에 팀원이 만든 주간 배치가 이미 있다
> (`~/matchuri-batch/code/ops/weekly.sh`). 수집부터 데이터 교체·앱 재시작·되돌리기까지
> 한다. 그게 도는 중이면 아래를 손으로 하지 마라 — 둘이 같은 폴더를 건드린다.
> `cat ~/matchuri-batch/logs/last_status` 로 마지막 결과를 본다.

데이터는 이미지에 들어 있지 않다. EC2 의 `/home/ubuntu/matchuri-data/kakao` 를 컨테이너가
읽기 전용으로 붙여 읽는다(`docker-compose.yml`). 그래서 **파일만 갈아 끼우고 앱을
재시작**하면 된다. 배포는 필요 없다.

```bash
# 1. 올린다 (39MB, 몇 분). 홈으로 받는다 — matchuri-data 는 root 소유라 못 쓴다
scp -r BE/data/kakao ubuntu@YOUR_SERVER_HOST:/home/ubuntu/kakao-new

# 2. 서버에서 바꿔 끼운다. 폴더 주인이 root 라 sudo 가 필요하다
ssh my-server
  sudo mv /home/ubuntu/matchuri-data/kakao /home/ubuntu/matchuri-data/kakao.bak
  sudo mv /home/ubuntu/kakao-new           /home/ubuntu/matchuri-data/kakao
  sudo chown -R ubuntu:ubuntu /home/ubuntu/matchuri-data/kakao
  docker restart backmock-app-1

# 3. 확인
curl -s localhost/readyz          # ok:true, cards 수가 비슷하거나 늘어야 한다
curl -s "localhost/cards?limit=1" # 이름이 제대로 나오는지
```

**`docker compose` 를 쓰지 않는다.** 도는 스택은 Jenkins 작업공간
(`/var/jenkins_home/workspace/matchuri-be-deploy/BE`)의 compose 파일에 릴리스 override 를
겹쳐 띄운 것이라, 다른 폴더에서 `docker compose restart` 를 부르면 **다른 스택을 만든다**.
컨테이너 이름을 직접 재시작하는 것이 안전하다.

**재시작이 꼭 필요하다.** `serving/*.npy`(유사도 행렬)와 추천 풀은 **기동 때 한 번만**
읽는다. 파일만 바꾸고 재시작하지 않으면 겉보기엔 성공인데 옛 데이터로 계속 돈다.

확인이 끝나면 `sudo rm -rf /home/ubuntu/matchuri-data/kakao.bak` 로 정리한다. 이상하면
반대로 되돌리고 다시 재시작한다.

`card_id` 는 갱신해도 그대로다 — `data/card_ids.json` 이 `store_id -> card_id` 를 박아
두고, 새 가게만 뒤 번호를 받는다(`server/app/card_ids.py`). 찜·학습 기록이 어긋나지
않는다.

## 예의

기본 딜레이 0.45~1.0초 + 지터. 병렬 호출하지 않는다. 이어받기가 되니 급하게 몰아치지 말 것.


## 출처 보존

수집 레코드의 `source_platform`은 해당 데이터를 가져온 플랫폼입니다. 카카오맵 수집기는
`kakao`, 네이버지도 수집기는 `naver`를 기록합니다. 원본 응답 내용은 그대로 두고 수집 봉투에
메타데이터를 추가하며, 정규화·런타임 변환에서도 가게·리뷰·메뉴·사진 출처를 유지합니다.
외부 블로그·CDN 주소가 있더라도 수집 플랫폼과 원문 작성/이미지 호스팅 플랫폼을 혼동하지 않습니다.

과거 런타임 파일은 출처를 확인한 데이터셋에만 다음 명령으로 보완합니다. 기존 출처 값과
명시적인 null은 덮어쓰지 않습니다. 원본 수집 파일·벡터·행렬·사용자 기록은 바꾸지 않습니다.
운영에서는 데이터 생산 작업과 같은 잠금을 잡고 백업한 사본으로 검증한 뒤 실행합니다.

```bash
python backfill_source_platform.py --dir /verified/runtime/path --platform kakao
```
