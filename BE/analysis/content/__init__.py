# -*- coding: utf-8 -*-
"""가게의 콘텐츠 표현 — 메뉴/리뷰/사진 벡터를 '집합' 으로 다루는 코드.

백테스트(`analysis.backtest`)와 나중의 서버(`server/recsys`)가 **같은 함수**를 쓰도록
하네스 바깥에 둔다. 여기 있는 것은 데이터 → 표현 → 유사도까지이고, 사용자 점수를
어떻게 접을지(mean/max/logsumexp)는 scorer 쪽 관심사다.
"""
