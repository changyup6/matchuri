# 운영 경로 재생 범위

정확한 표현은 **“동결한 운영 코드의 무필터·위치/트렌드 보정 없는 수동 결정 추천을, 가정한 스와이프 이력으로 오프라인 재생했다”**이다. 실제 이용자 로그, HTTP/API 실사용 검사, 온라인 A/B 시험을 뜻하지 않는다. 이 소스 검토만으로 현재 배포 컨테이너와의 버전 일치까지 새로 입증하지는 않는다.

- `matrix_bench.py`는 `taste.replay(past)`로 누적 이력을 만든 뒤 `taste.start_from(...)`으로 현재 세션을 시작하고, `taste.record`에 현재 20개 반응을 순서대로 넣는다. 실제 독립 세션 생성도 `recommendation_sessions.apply_created`에서 누적 사용자 `taste` 상태에 같은 `start_from`을 적용한다. 과거 20% 전달·최근 반응 감쇠·학습 수식은 운영 구현 그대로다. 이번 입력은 고정된 가정 반응이고 실제 사용자·세션 DB는 읽지 않는다.
- 최종 4장은 수정하지 않은 `decision.recommend(state)`와 `decision.DEFAULT(count=4)`로 고른다. API의 기본 `mode=manual`도 `_decision_out`에서 이 함수를 호출한다. 개인화 성분 평균, 불확실성 차감, 평가 가게의 자기 응답을 특징 집계에서 제외하는 처리, 적합 후보 우선 탐색·다양성 검사·부족할 때의 후퇴가 같다. 운영 mean/std는 이 개인화 성분 점수이며, 전체 점수나 만족 확률이 아니다. 자기 제외는 특징 집계 수준이고 학습 계수 전체를 다시 적합하는 LOO는 아니다.
- 벤치는 `allow/tiers/bonus`를 전달하지 않고 거절한 슬레이트도 없다. API의 필터·위치·거리/트렌드 보정, 이전 조합 거절, `mode=auto` 준비도 조건, 카드 ID/상세/이미지/북마크/설명 직렬화, 제안 ID 저장 및 결과 기록은 검사하지 않는다. 따라서 일반적인 모든 API 요청 결과와 같다는 주장은 부적절하다.
- 스와이프 카드 공급의 `legacy`/`taste` 인터리빙은 이번 경로에 없다. 이는 결정 추천 API 자체가 `taste` 스냅샷을 사용하기 때문이며, 벤치가 운영 결정 경로의 인터리빙을 누락한 것은 아니다. 다만 인터리빙이 실제 사용자가 보게 될 학습 카드와 그 반응 분포를 바꾸는 효과는 고정 입력 실험에서 평가하지 않는다. 실제 `policy.record`의 legacy 동시 학습·표시 진행도 갱신도 생략하지만 이 함수의 결정 점수 계산 입력은 같은 `taste.record`가 만든다.

검토한 위치: `BE/analysis/temporal_taste/matrix_bench.py`의 `run`, `bench.py`의 `offline_runtime`; `BE/server/recsys/taste.py`의 `replay/start_from/record`; `recsys/decision.py`의 `_components/_scores/recommend`; `recsys/policy.py`의 `record/taste_snapshot`; `app/recommendation_sessions.py`의 `remember/apply_created`; `server/main.py`의 `_decision_scope/_decision_out/get_decision`; `app/ranking.py`의 `build_detailed`. 코드와 입력은 변경하지 않았다.
