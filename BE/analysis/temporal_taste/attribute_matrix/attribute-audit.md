# 메뉴 속성 감사 — 점수 열람 전 동결

운영 카탈로그1498곳의 수집 메뉴23532건을 전수 규칙 검색했다. 속성은 **해당 근거가 있는 메뉴를 제공함**을 뜻하며, 가게 전체의 강도·주력·독점 특성을 뜻하지 않는다. 추출되지 않은 속성은 미확정이다.

직접 메뉴 표현과 선언한 음식명 사전 추론을 분리했다. 메뉴 설명의 다른 음식과 비교하는 문구는 음식사전 추론에 쓰지 않는다. 리뷰는 사용하지 않았다.

## 속성별 근거 수

| 속성 | 가게 | 브랜드 | 직접 표현 | 음식명 사전 | 출처 추천표시 메뉴 |
|---|---:|---:|---:|---:|---:|
| spicy | 361 | 346 | 361 | 0 | 20 |
| explicit_mild | 7 | 7 | 7 | 0 | 1 |
| sweet | 128 | 104 | 128 | 0 | 15 |
| sour | 83 | 63 | 83 | 0 | 13 |
| savory_umami | 43 | 43 | 43 | 0 | 2 |
| crispy | 113 | 102 | 113 | 0 | 12 |
| chewy | 92 | 88 | 92 | 0 | 15 |
| tender_soft | 150 | 130 | 150 | 0 | 27 |
| nutty_rich | 100 | 97 | 100 | 0 | 19 |
| fried | 377 | 353 | 253 | 220 | 20 |
| grilled | 405 | 386 | 405 | 0 | 39 |
| steamed | 10 | 7 | 10 | 0 | 0 |
| braised_or_steamed | 211 | 204 | 211 | 0 | 9 |
| stir_fried | 369 | 355 | 369 | 0 | 10 |
| raw | 241 | 232 | 231 | 13 | 18 |
| hot_temperature | 474 | 456 | 30 | 453 | 21 |
| cold_temperature | 209 | 205 | 174 | 180 | 4 |
| brothy | 788 | 753 | 560 | 544 | 44 |
| brothless_explicit | 587 | 565 | 0 | 587 | 37 |
| meat | 1106 | 1045 | 948 | 803 | 143 |
| seafood | 817 | 779 | 794 | 225 | 72 |
| vegetable | 428 | 408 | 428 | 0 | 47 |
| plant_protein | 237 | 226 | 237 | 0 | 4 |
| set_meal | 337 | 324 | 337 | 0 | 41 |
| single_serving_explicit | 156 | 154 | 156 | 0 | 17 |
| sharing_explicit | 149 | 146 | 149 | 0 | 8 |

## 대조 풀

A-only/B-only는 경쟁 태그의 **긍정 근거가 발견되지 않음**을 뜻한다. 실제 그 속성이 없는 것으로 확인한 것은 아니다. 두 근거가 모두 있는 가게는 대조에서 제외한다.

| A | B | A-only 가게/브랜드 | B-only 가게/브랜드 | 양쪽 모두 |
|---|---|---:|---:|---:|
| spicy | explicit_mild | 358/343 | 4/4 | 3 |
| sweet | savory_umami | 103/79 | 18/18 | 25 |
| sour | savory_umami | 64/44 | 24/24 | 19 |
| fried | grilled | 239/232 | 267/261 | 138 |
| fried | steamed | 376/352 | 9/6 | 1 |
| fried | braised_or_steamed | 333/312 | 167/162 | 44 |
| raw | grilled | 132/130 | 296/280 | 109 |
| hot_temperature | cold_temperature | 339/328 | 74/72 | 135 |
| brothy | brothless_explicit | 369/359 | 168/167 | 419 |
| meat | seafood | 426/412 | 137/135 | 680 |
| meat | vegetable | 724/694 | 46/46 | 382 |
| single_serving_explicit | sharing_explicit | 112/112 | 105/104 | 44 |
| crispy | tender_soft | 53/43 | 90/71 | 60 |
| chewy | tender_soft | 38/35 | 96/77 | 54 |

## 검토와 한계

- 속성별 초기 양성 사례와 부정·선택·비교 표현을 포함한147개 메뉴명/근거 구절을 직접 검토했다. 전수 의미 판독이나 추출 정확도 추정은 아니다.
- 실제 발견한 비교대상 음식, 맵기선택, 하와이안/서해안의 안, 냉이와 냉요리, 커피의 에스프레소, 함께 나오는 국물의 충돌을 수정했다. 실제 원문 및 합성 문법 반례26개 검증을 통과했다.
- is_recommend=True는503메뉴/279가게에만 있다. 이를 대표메뉴 전체나 인기 확률로 해석하지 않는다.
- 원문·플랫폼·파일행·메뉴index는 private evidence 파일에만 두었다. 공개용 labels에는 원문과 리뷰를 넣지 않았다.

- Attribute positive means a collected offered menu/ingredient has supporting evidence, not store-wide intensity, dominance, exclusivity or user preference.
- Local regular expressions do not solve general Korean negation, sarcasm, omitted ingredients, course alternatives or recipe substitutions.
- Optional and recommended-menu metadata is source-supplied, incomplete and not a calibrated representation of main dishes.
- Direct and lexical evidence are separated; lexical soup/temperature assumptions are conventional dish defaults, not measured food temperature or complete recipes.
- The reviewer inspected 147 targeted records; no population precision/recall estimate is claimed.
- A-only and B-only mean missing competing positive evidence, not verified exclusion of the competing attribute.
