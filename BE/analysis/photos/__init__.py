# -*- coding: utf-8 -*-
"""사진 파이프라인 (PLAN 2절 '사진 (Step 3)').

  `download.py`  후보 풀 가게의 음식/공간 사진을 골라 내려받고 짧은 변 256px 로 줄여
                 `data/photos_cache/` 에 캐시한다.
  `embed.py`     CLIP 이미지 임베딩 → `data/vectors/photo_{food,space}*.npy`.

둘 다 git 에 들어가지 않는 산출물을 만든다(`data/photos_cache/`, `data/vectors/`).
"""
