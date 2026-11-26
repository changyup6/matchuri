# EC2 배포 검증 · 2026-09-09 15:35 KST

대상: `api.example.com` (compose 프로젝트 `backmock`, 작업 디렉터리 `/home/ubuntu/matchuri-be`).
앱 컨테이너만 교체했다. nginx · Jenkins · Data Hub(caddy/label-studio/filebrowser) 는 손대지 않았다(`docker ps` 확인).

## 절차
```bash
docker tag backmock-app:latest backmock-app:legacy-20260909     # 롤백용 태그
docker compose -p backmock build app
docker compose -p backmock up -d app
```
롤백: `docker tag backmock-app:legacy-20260909 backmock-app:latest && docker compose -p backmock up -d app`

## 기동 로그
```
[content] 서빙 산출물 로드: 풀 2,216곳 · 사전값 step3c · /app/data/kakao/serving
[state] 복원: 사용자 40 · 선호 1465 · /app/state
```

## 전후
| | 배포 전 | 배포 후 |
|---|---|---|
| users.jsonl | 40 | 42 (검증용 사용자 2명 추가) |
| preferences.jsonl | 1,465 | 1,471 (검증 스와이프 6건) |
| exposures.jsonl | 없음 | 320 (검증 요청 32번 × 10장) |
| `/` | `{"cards":2795,"recommendable":2216,"logs":1465,"sessions":35}` | `+ "policy":"interleave"` |

기존 사용자 `u_4adaa29981bb`(스와이프 6) 의 학습 상태가 legacy · taste 양쪽에 복원됐다
(taste 가중치 pop 1.088 · quality 0.14 · cf 0.458 · menu 0.248 · text 0.432 · review 0.04 · food −0.007 · neg −0.25).

## 실서버 확인 (nginx 경유, `Host: api.example.com`)
- 새 사용자 `/cards?limit=10` 두 번: 20장 전부 유일, 정책 태그 taste 10 / legacy 10, 한 장씩 교대. 추천 이유 채워짐
  (taste: "리뷰 757개·평점 4.9 의 인기 가게예요." / legacy: "평점과 리뷰 수를 함께 고려해 골랐어요.").
- `POST /preferences` 6건 전부 200. 이후 `/admin/api/users/{id}` 의 taste 가중치가 사전값에서 이동
  (menu 0.25→0.133, text 0.40→0.498, neg −0.25→−0.495).
- `/admin/api/policies`: 노출 20 · legacy 2/1 · taste 1/2 · Wilson 구간 겹침 → "아직 모릅니다".
- `/admin` 200, 브라우저 pageerror 0건 (`live-admin-policy.png`, `live-admin-user.png`).
- 지연 `GET /cards?limit=10` 30회: **p50 10.6ms · p95 11.6ms · max 12.1ms**.

## 남은 것
- 인터리빙 판정은 노출이 쌓여야 한다. 관리자 화면 맨 위 패널이 10초마다 갱신된다.
- 검증용 사용자 `zz-verify-0909`, `zz-latency-0909` 두 명이 state 에 남아 있다(집계에는 무해).
