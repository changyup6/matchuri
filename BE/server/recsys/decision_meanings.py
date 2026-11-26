"""Korean meanings and evidence-bounded readings shipped with decision measurements."""


def guide():
    # path, name, meaning, unit, interpretation, limitation, missing
    rows = [
        ("feedback_count / candidate_total / candidate_offset / next_offset", "관찰 시점과 내보낸 범위", "누적 학습 피드백 횟수, 전체 자격 후보 수, 현재/다음 페이지 시작점",
         "회 / 곳 / 0부터 시작하는 위치", "feedback_count는 재평가도 포함하는 누적 횟수이며 고유 평가 수와 다르다. next_offset까지 이어받아 후보 전체 확인",
         "현재 세션에서 새로 넘긴 횟수로 자동 해석하지 않는다. 한 페이지나 쌍 범위를 전체 카탈로그로 오인하지 않는다", "next_offset=null이면 마지막 페이지"),
        ("personalization.validation_auc", "반응 구분력", "응답 전에 매긴 순위가 이후 좋아요 기록을 싫어요 기록보다 높게 두었는지",
         "0~1", "두 종류 기록의 모든 비교쌍에서 높은 순위=1, 동점=.5로 평균. .5는 구분 없는 수준",
         "시점마다 모델이 다르고 노출이 선택되어 있다. 표본 수와 함께 읽어야 하며 만족 확률로 해석할 수 없다", "긍정 또는 부정의 유효 예측이 없음"),
        ("personalization.stability", "추천 순위 유지율", "현재 미평가 풀에서 상위 후보가 최근 학습 상태와 얼마나 겹치는지",
         "0~1", "n−1/n−3 각각의 상위20 겹침 중 작은 비율. .75는 비교 중 최소15곳 유지",
         "틀린 순위도 안정될 수 있어 정확도와 별도다. 상위 개수는 config.stable_top을 확인", "체크포인트 또는 미평가 후보 부족"),
        ("personalization.*_count / *_predictions", "근거의 양", "고유 평가 수와, 실제로 답변 전 예측을 남긴 긍정/부정 기록 수",
         "건/곳", "유효 예측 수는 평가 수보다 작을 수 있다. 두 종류가 얼마나 확보됐는지 함께 읽는다",
         "같은 가게 재평가나 한 반응에서 만든 비교쌍을 독립 사용자 수로 세면 안 된다", "산출물 없으면 personalization 전체 null"),
        ("personalization.validation_window[].predicted_rank", "답변 전 예상 위치", "그 응답을 보기 직전 개인화 점수가 전체 풀에서 차지한 위치",
         "0~1", "그보다 낮은 점수의 비율에 동점 비율의 절반을 더한다. 클수록 당시 높게 평가",
         "각 기록은 다른 시점의 모델 예측이다. 현재 후보의 수락 확률을 뜻하지 않는다", "유효 예측만 window에 포함"),
        ("personalization.checkpoint_overlaps", "시점별 순위 겹침", "몇 번 전 학습 상태와 현재 상위 후보가 겹치는지",
         "feedback_lag=회, overlap=0~1", "1과3은 각각 한 번 전/세 번 전 피드백 상태",
         "짧은 구간의 안정성 관찰이다. 장기 취향 안정성을 증명하지 않는다", "비교 불가능하면 빈 배열"),
        ("model[].weight", "성분 가중치", "각 고차원 유사도 집계 등이 전체 점수에 영향을 주는 방향과 크기",
         "모델 계수", "양수면 해당 z가 클수록 점수를 올리고, 음수면 내린다. 실제 영향은 weight×z",
         "메뉴 가중치가 .4라고 취향의40%가 메뉴라는 뜻은 아니다. 성분끼리 상관될 수 있다", "비유한 값은 null"),
        ("model[].variance / sd", "가중치의 모델 불확실성", "현재 온라인 모델이 근사하는 가중치 분산과 그 제곱근",
         "계수² / 계수", "같은 모델과 조건에서 작아질수록 그 계수의 추정 폭이 좁아진 상태",
         "대각 근사와 선택한 사전값에 의존한다. 실제 오류율에 보정된 신뢰구간은 아니다", "비유한 값은 null"),
        ("candidates[].components.*.z", "정규화 성분 값", "이력과의 유사도 등을 후보 풀 안에서 표준화한 뒤 ±3으로 제한한 값",
         "표준화 점수 [-3,3]", "양수는 기준 풀 평균 위, 음수는 아래. 성분별 기여 계산의 입력",
         "결측도 정규화 뒤0이 되므로 evidence의 결측 여부를 함께 확인해야 한다", "비유한 값은 null"),
        ("candidates[].components.*.contribution", "성분의 실제 기여", "그 후보의 성분 값과 사용자 가중치를 곱해 전체 점수에 더한 값",
         "모델 점수", "weight×z. 양수는 점수를 올리고 음수는 내리며 합계가 total_score",
         "점수 분해이며 특정 속성이 선호의 원인이라는 인과 설명은 아니다", "비유한 값은 null"),
        ("candidates[].components.*.variance_contribution", "성분별 불확실성 기여", "가중치 분산과 후보 성분 값 제곱의 곱",
         "점수²", "개인화 성분만 더한 뒤 제곱근을 취하면 model_sd",
         "성분 간 공분산과 모델 구조의 오류를 포함하지 않는다", "비유한 값은 null"),
        ("candidates[].total_score / personal_score", "전체 점수 / 개인화 기여", "전체 성분의 합계 / 그중 공방문·메뉴·요약·리뷰·사진·기피 성분의 합계",
         "모델 점수", "동일 시점·기준 풀에서 클수록 해당 모델이 높게 평가. personal_score는 인기·평판을 제외",
         "상한이 정해진 만족도 척도가 아니다. 시점·기준 풀 변경 시 단순 절대 비교에 주의", "비유한 값은 null"),
        ("candidates[].model_sd", "후보 점수의 추정 폭", "후보의 개인화 성분과 가중치 분산으로 계산한 점수 표준편차 근사",
         "모델 점수", "같은 조건에서 크면 모델이 그 점수를 더 불확실하게 취급",
         "특징 결측·성분 상관·취향 변화 등 모든 불확실성을 포괄하지 않는다", "비유한 값은 null"),
        ("candidates[].conservative_score", "불확실성 차감 점수", "개인화 점수에서 모델 불확실성에 비례한 값을 차감한 현재 판정 점수",
         "모델 점수", "personal_score − config.uncertainty_weight×model_sd",
         "현재 차감 계수는 실험 가정이다. 확률 보정된 적합도 하한은 아니다", "비유한 값은 null"),
        ("fit.threshold / candidates[].fit_margin", "현재 적합도 기준 / 기준 초과분", "미평가 풀의 설정 분위수 점수 / 후보 차감 점수에서 그 기준을 뺀 값",
         "모델 점수", "margin≥0이며 personal_score>0이면 현재 적합도 규칙 통과",
         "상대 상위 후보라는 의미다. 전체 풀이 부적합해도 상대 기준은 생긴다", "미평가 기준 풀이 없으면 null"),
        ("candidates[].reference_percentile", "기준 풀 내 상대 위치", "미평가 풀에서 이 후보의 차감 점수 이하인 가게의 비율",
         "0~1", ".9면 기준 풀90%의 점수 이상. 동점을 모두 이하로 포함",
         "사용자가90% 확률로 좋아한다는 해석은 성립하지 않는다", "기준 풀이 없으면 null"),
        ("candidates[].evidence", "판단에 사용 가능한 근거", "성분 보유 여부, 좋아요/싫어요 참조 중 관측되는 수와 가장 가까운 참조",
         "보유 여부·건수·원시 유사도", "known_count/total_count로 근거량을 확인. nearest는 관측 참조 중 최대 유사도",
         "nearest 하나가 전체 집계를 대표하지는 않는다. 요약은 lse, 리뷰는 평균 등 집계가 다르다", "가까운 참조를 알 수 없으면 ID/유사도 null"),
        ("candidates[].qualified / selected / rank", "적합 규칙 통과 / 실제 제안 / 진단 순서", "후보별 규칙 통과, 실제 제안에 포함됐는지, 진단 목록에서의 위치",
         "참·거짓 / 1부터 시작", "통과 후보 우선으로 정렬하고 그 안에서 tier·rank_score·ID 순. selected는 결정 결과를 따른다",
         "qualified만으로 제안하지 않는다. 다양성과 개인화 근거도 필요하다", "해당 없음"),
        ("candidates[].tier / distance_bonus / rank_score", "태그·거리 정렬 보정", "태그 우선 등급, 거리 점수 보정, 차감 점수에 거리 보정을 더한 값",
         "등급 / 모델 점수", "먼저 tier가 큰 후보, 그다음 rank_score가 큰 후보 순",
         "거리 보정은 취향 적합성의 직접 증거가 아니다", "보정이 없으면0"),
        ("pairs[].similarities.*", "성분별 가게 유사도", "고차원 벡터에서 계산해 저장한 두 가게의 메뉴·요약문·리뷰·음식사진 유사도",
         "원시 유사도", "같은 성분·모델 안에서 클수록 표현이 가까움. 비교에는 해당 성분의 분포/결측도 필요",
         ".8은 취향80% 일치가 아니다. 메뉴 .4와 사진 .8을 직접 비교해 사진이 두 배 닮았다고 읽지 않는다", "해당 성분을 계산할 근거가 없으면 null"),
        ("pairs[].co_visit_similarity", "공방문 유사도", "두 가게의 공방문 패턴을 나타내는 저장 유사도",
         "원시 유사도", "공방문 성분을 진단하는 보조 값. 현재 다양성 평균에는 포함하지 않는다",
         "방문은 선호나 만족과 동일하지 않고 인기·희소성 영향을 받을 수 있다", "계산 불가능하면 null"),
        ("pairs[].relative_similarities / redundancy_percentile", "성분별 상대 유사도 / 조합 중복", "각 성분의 전체 관측 가게 쌍 분포에서 중간순위 백분위를 구하고 가장 높은 성분을 유지",
         "[0,1] 백분위", "클수록 같은 모델의 다른 가게 쌍보다 더 닮음. 적합 후보 안에서 가장 큰 쌍 중복부터 줄이는 조합 비교에 사용",
         "백분위는 중복 확률이나 사람의 대안 인식에 대한 보정이 아니다. 취향 학습 가중치는 적합도에만 사용", "성분 결측은 null, 관측 2성분 미만이면 중복값도 null"),
        ("pairs[].raw_mean_similarity / known_modalities / missing_modalities", "현재 평균 규칙과 근거량", "관측된 메뉴·요약문·리뷰·사진 점수의 단순 평균 및 관측/결측 성분",
         "원시 평균 / 개수 / 성분명", "현행 코드는2개 이상 관측과 평균 기준 미만을 요구. 어떤 성분이 빠졌는지도 확인",
         "성분별 눈금이 달라 평균의 의미 보정이 부족하다. 결측 조합이 다르면 같은 평균도 근거가 다르다", "모두 결측이면 평균 null"),
        ("pairs[].same_brand / same_fine_category / blocking_reasons", "중복 정책과 차단 이유", "브랜드·세부 업종 키 일치 여부와 현재 수치 규칙을 막은 조건",
         "참·거짓·알 수 없음 / 사유 목록", "passes_current_rule=true는 최소 규칙 통과이며 세부 업종 일치는 진단만 제공",
         "사용자가 서로 다른 대안으로 느낀다는 보장은 없다. 실제 조합은 상대 유사도를 추가 비교", "어느 한쪽 키가 없으면 일치 여부 null"),
        ("fit.*_count / search_truncated", "후보 수와 탐색 범위", "자격 후보, 상대 적합도 통과 후보, 실제 조합 탐색 후보 수와 범위 제한 여부",
         "곳 / 참·거짓", "qualified_count와 searched_count의 차이가 크면 탐색 바깥의 후보도 많다",
         "제한 범위에서 조합을 못 찾았다고 전체 풀에 해가 없다고 단정할 수 없다", "산출물이 없으면 fit 전체 null"),
        ("slate", "제안 조합의 진단", "조건을 만족하는 조합의 최소 적합도 여유와 가장 높은 쌍 평균 유사도",
         "모델 점수 / 원시 유사도", "약한 후보와 가장 겹치는 쌍을 확인. is_proposal=true일 때만 실제 판정상 제안",
         "개인화 근거 미충족 시 조합만 있어도 제안하지 않는다. 최고 효용 조합의 보장은 없다", "조합이 없으면 요약 수치 null"),
        ("gates / config", "현재 조건 판정과 설정값", "측정값이 설정된 비교 조건을 충족하는지 및 사용한 숫자",
         "조건별 단위", "passed는 현행 코드의 통과 여부. 값의 방향·연산·기준을 함께 읽는다",
         "장수는 config.count(기본4). 개인화/적합도/다양성 임계값은 아직 제품 실증 전의 가정", "측정 근거가 없으면 passed=null"),
        ("snapshot_id / provenance / context / pair_scope", "같은 조건에서 비교하기 위한 정보", "학습/설정/버전의 식별자, 적용 맥락, 쌍의 대상 범위",
         "식별자·설정·범위", "페이지 병합 시 snapshot_id 일치 확인. 쌍의 범위는 후보 페이지와 독립",
         "해시는 품질 점수가 아니다. 식별자가 같다는 것은 좋은 추천이라는 뜻이 아니다", "미제공 버전이나 위치는 unknown으로 남음"),
    ]
    fields = ("path", "name", "meaning", "unit", "reading", "limitation", "missing")
    return [dict(zip(fields, row)) for row in rows]


def attach(out):
    """Attach observed-value readings; never invent latent preference labels."""
    out["metric_guide"] = guide()
    p, fit = out["personalization"], out["fit"]
    summary = ["현재 수치는 실험용 판정의 작동 근거입니다. 실제 사용자 만족도에 보정된 척도는 아닙니다."]
    if p is None:
        summary.append("취향 산출물을 사용할 수 없어 개인화·적합도·유사도를 측정하지 못했습니다.")
    else:
        if p["validation_auc"] is None:
            summary.append(f"응답 전 유효 예측이 긍정 {p['positive_predictions']}건, 부정 {p['negative_predictions']}건으로 두 반응의 구분력을 계산할 근거가 부족합니다.")
        else:
            pairs = p["positive_predictions"] * p["negative_predictions"]
            summary.append(f"구분력 {p['validation_auc']:.3f}: 응답 전 순위를 남긴 긍정 {p['positive_predictions']}건과 부정 {p['negative_predictions']}건의 {pairs}개 비교쌍에서 긍정 순위가 더 높은 정도입니다(동점은 절반). 이 비교쌍들이 각각 독립 표본은 아닙니다.")
        if p["stability"] is not None:
            size = out["config"]["stable_top"]
            summary.append(f"순위 유지율 {p['stability']:.0%}: 현재 상위 {size}곳이 한 번 전·세 번 전 목록과 최소 {round(p['stability']*size)}곳 겹칩니다. 최근 변화가 작다는 근거이며 예측의 정확성은 별도로 확인해야 합니다.")
        else:
            summary.append("최근 순위 유지율은 비교할 학습 상태 또는 미평가 후보가 부족해 아직 계산할 수 없습니다.")
        if fit["threshold"] is not None:
            scope = '미평가' if fit['reference_pool'] == 'currently_unrated_catalog' else '미평가 후보가 없어 전체'
            summary.append(f"적합도 기준 {fit['threshold']:.3f}: {scope} {fit['reference_count']}곳의 차감 점수 {fit['quantile']:.0%} 분위수입니다. 자격 후보 {fit['eligible_count']}곳 중 {fit['qualified_count']}곳이 이 기준과 양수 개인화 조건을 통과했습니다. 상대적인 선별이며 절대 만족을 보장하지 않습니다.")
        summary.append(f"조합 탐색은 적합 후보 중 {fit['searched_count']}곳까지 수행했습니다. 탐색 범위 제한 여부는 {str(fit['search_truncated']).lower()}입니다.")
    blockers = [g["name"] for g in out["gates"] if g["passed"] is False and g.get("required", True)]
    unknown = [g["name"] for g in out["gates"] if g["passed"] is None and g.get("required", True)]
    if out['config'].get('relaxation_progress', 0):
        summary.append("고유 평가가 누적되어 예측 근거·구분력·안정성 조건을 완화합니다. 60곳부터 이 값들은 진단으로만 남습니다. 가게 적합성·다양성·사용자 필터는 유지하며 개인화가 충분히 검증됐다는 의미는 아닙니다.")
    if out["decision"]["ready"]:
        summary.append(f"자동 권유 규칙을 만족하는 {out['config']['count']}곳을 찾았습니다. 이것만으로 취향의 충분한 이해나 사람이 느끼는 다양성이 검증된 것은 아닙니다.")
    else:
        summary.append(f"현재는 자동 권유를 보류합니다. 측정된 미통과 조건 {len(blockers)}개, 근거 부족으로 판정할 수 없는 조건 {len(unknown)}개입니다. 사용자가 직접 요청하는 manual decision은 이 검증과 별개입니다.")
    names = {"artifacts_available": "취향 산출물 사용 가능", "location_available_if_required": "반경 필터에 필요한 위치",
             "rated_count": "고유 평가 수", "liked_count": "긍정 근거 수", "positive_predictions": "긍정 유효 예측 수",
             "negative_predictions": "부정 유효 예측 수", "validation_auc": "반응 구분력", "checkpoint_count": "비교 학습 상태 수",
             "eligible_count": "현재 자격 후보 수", "probe_count": "순위 비교용 미평가 후보 수",
             "stability": "최근 순위 유지율", "diverse_fit_count": "적합성과 다양성 규칙을 통과한 제안 장수 (계속 둘러본 동일 조합 제외)"}
    for g in out["gates"]:
        state = "근거 부족으로 미측정" if g["passed"] is None else "현행 조건 통과" if g["passed"] else "현행 조건 미통과"
        g["explanation"] = f"{names[g['name']]}: {state}. 측정값 {g['value']}, 비교 조건 {g['operator']} {g['threshold']}."
        if not g.get('required', True):
            g['explanation'] += ' 오래 탐색한 세션에서는 이 진단이 선택 제안을 막지 않습니다.'
    out["reading"] = summary
    return out
