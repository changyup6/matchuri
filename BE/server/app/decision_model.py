"""Stable export schema for analysis clients (not end-user confidence labels)."""
from typing import Literal
from pydantic import BaseModel, Field


class GateMetric(BaseModel):
    name: str
    value: float | bool | None
    operator: Literal[">=", "=="]
    threshold: float | bool
    passed: bool | None = Field(description="null은 측정 근거 부족. false와 구분")
    required: bool = Field(True, description="현재 제안을 막는 필수 조건인지. false여도 측정값과 통과 여부는 그대로 유지")
    baseline_threshold: float | bool | None = Field(None, description="탐색 장기화에 따른 완화 전 기준")
    explanation: str = Field(description="이 조건의 의미와 현재 통과/미통과/미측정 이유")


class ModelMetric(BaseModel):
    component: str
    weight: float | None
    variance: float | None
    sd: float | None
    used_for_personal_fit: bool


class ComponentMetric(BaseModel):
    z: float | None = Field(description="후보 풀 정규화 및 ±3 절단 후 성분 값")
    contribution: float | None = Field(description="weight × z")
    variance_contribution: float | None = Field(description="variance × z²; 개인화 성분만 model_sd에 포함")


class ReferenceMetric(BaseModel):
    known_count: int
    total_count: int
    nearest_store_id: str | None
    nearest_similarity: float | None


class EvidenceMetric(BaseModel):
    candidate_has_feature: bool
    like_references: ReferenceMetric
    dislike_references: ReferenceMetric


class CandidateMetric(BaseModel):
    store_id: str
    rank: int = Field(description="적합도 통과 후보 우선, 내부는 tier/rank_score/store_id 순서")
    qualified: bool
    selected: bool
    total_score: float | None
    personal_score: float | None
    model_sd: float | None
    conservative_score: float | None
    fit_margin: float | None
    reference_percentile: float | None = Field(description="미평가 풀에서 이 점수 이하의 비율; 만족 확률 아님")
    tier: int
    distance_bonus: float
    rank_score: float | None
    self_excluded_from_features: bool
    components: dict[str, ComponentMetric]
    evidence: dict[str, EvidenceMetric]


class PairMetric(BaseModel):
    store_ids: list[str]
    similarities: dict[str, float | None] = Field(description="menu/text/review/food 원시 벡터 유사도. 결측은 null")
    relative_similarities: dict[str, float | None] = Field(default_factory=dict, description=
        "각 성분의 전체 관측 가게 쌍 대비 유사도 백분위 [0,1]. 동점은 중간순위, 결측은 null. 중복 확률 아님")
    redundancy_percentile: float | None = Field(None, ge=0, le=1, description=
        "관측 성분별 백분위의 최댓값. 높은 한 성분을 다른 낮은 성분으로 상쇄하지 않음. 2성분 미만이면 null")
    co_visit_similarity: float | None
    known_modalities: int
    missing_modalities: list[str]
    raw_mean_similarity: float | None = Field(description="현재 휴리스틱의 관측 성분 평균. 의미 보정 안 됨")
    same_brand: bool | None
    same_fine_category: bool | None
    passes_current_rule: bool = Field(description="기존 최소 다양성 규칙 통과. 선택 조합의 최적성·만족도 보증이 아님")
    blocking_reasons: list[str]


class PredictionMetric(BaseModel):
    feedback_index: int | None
    store_id: str | None
    like: bool
    predicted_rank: float


class CheckpointMetric(BaseModel):
    feedback_lag: int
    overlap: float


class PersonalizationMetric(BaseModel):
    rated_count: int
    liked_count: int
    disliked_count: int
    positive_predictions: int
    negative_predictions: int
    validation_auc: float | None
    stability: float | None
    checkpoint_overlaps: list[CheckpointMetric]
    validation_window: list[PredictionMetric]
    probe_count: int
    checkpoint_count: int


class FitMetric(BaseModel):
    eligible_count: int
    qualified_count: int
    reference_pool: str
    reference_count: int
    threshold: float | None
    quantile: float
    score_quantiles: dict[str, float | None]
    searched_count: int
    search_truncated: bool


class SlateMetric(BaseModel):
    diagnostic_store_ids: list[str]
    is_proposal: bool = Field(description="자동 권유 조건을 통과한 조합인가. false인 진단 조합은 자동 권유에 쓰지 않음. manual 후보는 decision API에서 별도 요청")
    min_fit_margin: float | None
    max_pair_mean_similarity: float | None
    max_pair_redundancy_percentile: float | None = Field(None, ge=0, le=1, description=
        "이 조합에서 가장 큰 쌍 중복 백분위. 같은 적합 후보 범위 내 조합 비교용이며 확률 아님")
    pairs: list[PairMetric]


class MetricDecision(BaseModel):
    ready: bool = Field(description="자동 권유(mode=auto) 조건 통과 여부. 기본 manual decision의 카드 제공 여부와 별개")
    status: str
    store_ids: list[str]
    rated_count: int
    liked_count: int
    stability: float | None
    validation_auc: float | None
    qualified_count: int = Field(description="실제 판정 경로에서 도달한 수. 진단 fit.qualified_count와 다를 수 있음")


class MetricProvenance(BaseModel):
    manifest_sha256: str | None
    vectors: dict[str, str]
    built_at: str | None
    code_sha256: dict[str, str]


class MetricContext(BaseModel):
    categories: list[str] = []
    tags: list[str] = []
    within_m: int | None = None
    has_position: bool = False
    location_required: bool = False


class MetricGuide(BaseModel):
    path: str
    name: str
    meaning: str
    unit: str
    reading: str
    limitation: str
    missing: str


class DecisionMetricsOut(BaseModel):
    schema_version: Literal[1]
    snapshot_id: str = Field(description="학습 상태·필터/점수 보정·설정·버전 해시. 페이지 합칠 때 일치 확인")
    feedback_count: int
    context: MetricContext
    provenance: MetricProvenance
    config: dict[str, float | int]
    interpretation: dict[str, str]
    reading: list[str] = Field(description="현재 측정값의 의미·분모·해석 한계를 설명하는 한국어 요약")
    metric_guide: list[MetricGuide] = Field(description="지표별 의미·단위·읽는 법·한계·결측 설명")
    decision: MetricDecision
    gates: list[GateMetric]
    model: list[ModelMetric]
    personalization: PersonalizationMetric | None
    fit: FitMetric | None
    candidates: list[CandidateMetric]
    candidate_total: int
    candidate_offset: int
    candidate_limit: int
    next_offset: int | None
    pairs: list[PairMetric]
    pair_store_ids: list[str]
    pair_scope: str
    slate: SlateMetric | None
