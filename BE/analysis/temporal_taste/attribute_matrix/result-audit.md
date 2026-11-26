# 속성 실험 독립 검산

**3168개 case × 3개 정책 × 4장 = 38016장**을 원시 NPZ·동결 fixture와 대조했다. 중립 대비 효과와 선호 교체 효과를 집계 코드 import 없이 재계산했다.

- 검산 1,570,092건 통과; 고유 동결 출처 64개 일치.
- 현재-only 평균·불확실성은792개 비교 그룹의4개 arm에서 비트 단위 동일.
- 같은 과거ID·순서에서 선호 라벨만 바꾸어도 GP 불확실성은 비트 단위 동일.
- 모든4317D 계수의 X@w+b 재구성 최대오차 7.77e-16.
- 측정132셀, 미측정140셀: 미측정을0효과로 치환하지 않음.

| 정책 | 실제4장 | 허용top20 | 현재조건부top10 | heldout현재조건부top10 |
|---|---:|---:|---:|---:|
| operational | 12672 | 63360 | 31680 | 31680 |
| regularized | 12672 | 63360 | 31680 | 31680 |
| regularized_context_floor | 12672 | 60252 | 30504 | 29088 |

## 해석 한계

- Actual selections are validated as saved; this audit does not rerun the selector or independently recompute pair-diversity from every serving component.
- All score changes are model-unit directional effects under hypothetical feedback, not satisfaction or accuracy.
- Fixed heldout means, globally eligible top10 and context-conditional top10 are distinct denominators.
- A-only/B-only means competing positive evidence is absent from this audited snapshot, not confirmed absence of the attribute.
- No raw prediction or private feature arrays are exported by this audit.
