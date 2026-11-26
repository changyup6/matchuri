"""Client measurements; detailed model diagnostics belong to DecisionMetricsOut."""
from typing import Literal
from pydantic import BaseModel, Field


class SummaryPersonalization(BaseModel):
    ready: bool = Field(description='평가 근거·예측 구분력·순위 안정성이 완화 전 개인화 기준을 모두 통과했는가. 가게 수/다양성 및 장기 탐색의 선택 허용과 별개')
    readiness_score: int | None = Field(ge=0, le=100, description=
        '취향을 찾는 과정의 준비도. 높을수록 완화 전 개인화 기준에 가까움. '
        '필수 기준별 측정값/기준값의 최소 비율×100, 소수점 버림. 근거 미확보는0, 분석 불가는 null. '
        '순위 안정성은 학습이 진행되는 동안 내려가는 값이라 이 점수에서 제외하며 ready 조건에는 남는다. '
        '따라서100이 기준 모두 통과를 뜻하지 않고 ready가 false일 수 있음 — 통과 여부는 ready로 판단. '
        '예측 정확도·학습 완료율·남은 스와이프 수가 아님. 피드백에 따라 내려갈 수 있음')
    discrimination_score: float | None = Field(ge=0, le=100, description=
        '최근 답변 전 예측의 AUC×100. 50은 무작위 수준, 높을수록 좋아요/싫어요 구분이 좋음. '
        '긍정·부정 검증 수가 기준 미달이면 null. 만족 확률 아님')
    stability_score: float | None = Field(ge=0, le=100, description=
        '한 번 전·세 번 전과 상위 추천이 겹치는 최소 비율×100. 높을수록 안정적. 비교 근거 부족은 null')


class SummaryDiversity(BaseModel):
    passed: bool | None = Field(description=
        '지정 카드의 모든 쌍이 기존 다양성 규칙을 통과했는가. false는 유사하거나 검증 근거 부족. '
        '카드가 2장 미만이거나 분석 불가면 null. 유사도 점수 하나로 판정하지 말 것')
    similarity_score: float | None = Field(ge=0, le=100, description=
        '지정 카드 중 가장 비슷한 쌍의 성분 평균 유사도를 50×(1+평균)으로 표시. '
        '50은 평균0, 100은 평균1이며 높을수록 닮음. 만족/중복 확률 아님. '
        '한 쌍이라도 관측 성분이 2개 미만이면 null')
    card_ids: tuple[int, int] | None = Field(description='similarity_score에 해당하는 두 카드 ID. 점수 미측정이면 null')


class DecisionSummaryOut(BaseModel):
    status: Literal['ok', 'unavailable', 'location_required'] = Field(description='조회 상태. 제안 가능 여부는 decision API의 ready 사용')
    decision_readiness_score: int | None = Field(ge=0, le=100, description=
        '클라이언트 표시용 취향 파악 점수. 실제 범위는0~80이고 기존 앱은 이를0~100%로 환산해 표시. '
        '답을 보기 전 예측한 순위와 실제 응답이 같은 방향이면(적중) 오르고 반대면 내려가는 누적값이며, '
        '연속 적중이 길수록 한 장의 증가폭이 커짐. 빗나간 한 장은 누적의 절반까지만 깎으므로, 한 번이라도 순적중한 뒤에는 0으로 돌아가지 않음(0은 아직 아무것도 맞히지 못한 상태 전용). '
        '80은 personalization.ready가 true일 때만 나오고 그때만 화면100% — 화면 판정과 서버 판정이 일치. '
        'ready 전에는 최대79(화면98.8%)에서 멈추고, ready가 다시 false가 되면79 이하로 돌아감. '
        '학습된 스와이프에서만 움직이며 반복 조회·거리·필터·n·card_ids 변경으로는 변하지 않음. '
        '새 결정 세션은 항상0에서 시작하며, 이전 세션에서 물려받는 것은 점수가 아니라 적중 시 증가 속도(최대2배). '
        'personalization.readiness_score(완화 전 기준까지의 진행률)와는 다른 값이므로 서로 비교하지 말 것. '
        '예측 정확도·만족 확률·학습 완료율이 아니며 횟수만으로 상승하지 않음. '
        '분석 불가는 null. 수동 decision 요청은 점수와 관계없이 가능')
    personalization: SummaryPersonalization
    fit_count: int | None = Field(ge=0, description=
        '현재 필터·거리 안에서 취향 적합 기준을 통과한 가게 수. 좋아요 여부 무관, 다양성 검사 전. '
        '기준/위치를 계산할 수 없으면 null. 0은 계산했지만 적합 가게가 없음')
    diversity: SummaryDiversity
