FROM python:3.12-slim

# 빌드 컨텍스트는 BE/ 다(docker-compose.yml 참고).
# 컨테이너 안 배치를 저장소와 똑같이 맞춘다 — server/recsys/store.py 가
# parents[2]/data 를 보므로 배치가 어긋나면 데이터를 못 찾는다.
WORKDIR /app

# (1) 의존성 먼저
COPY server/requirements.txt ./server/
RUN pip install --no-cache-dir -r server/requirements.txt

# (2) 애플리케이션
COPY server ./server/

# (3) card_id 기준선. 크롤링 데이터가 아니라 **번호 계보**라서 이미지에 굽는다 —
#     이 파일이 없으면 매핑이 그때그때 가게 목록에서 새로 계산돼, 주간 갱신 때마다
#     찜·학습·집계가 조용히 어긋난다(app/card_ids.py). 54KB 다.
COPY data/card_ids.json ./data/card_ids.json

# (3b) 네이버 검색어 트렌드 스냅샷 (2026-09-18). 크롤 데이터가 아니라 **커밋되는** 산출물이다 —
#      analysis/fetch_trend_category.py 가 로컬에서 굽고 서버는 만들지 않는다. 그래서 (4) 의
#      "배포하면 옛 데이터" 문제가 없고, 이미지에 굽는 것이 곧 배포다. 7.4MB.
#      recsys/trend.py(CATEGORY_FILE) · trend_history.py(SERIES_DIR) · trend_search.py(LEVEL_FILE)
#      가 읽는다. 빠지면 서버는 죽지 않고 /admin/trends 전국 패널이 available:false 로,
#      category 항은 0 으로 조용히 빈다 — 실제로 그랬다. .dockerignore 가 로컬 캐시만 뺀다.
#      trend_menu.json(365KB, analysis/fetch_trend_menu.py)도 이 폴더에 있어 같이 실린다 —
#      trend.py 의 season·fad 항이 읽는다. 폴더째 COPY 하므로 파일마다 줄을 더하지 않는다.
COPY data/analysis ./data/analysis/

# (4) 크롤링 데이터는 이미지에 넣지 않는다 (2026-09-14).
#     data/kakao 는 EC2 배치가 매주 갈아 끼우는 서버 폴더에 있고, docker-compose.yml 이
#     그 폴더를 /app/data/kakao 에 붙인다. 이미지에 구우면 배포할 때마다 옛 데이터로
#     돌아간다. **serving/ 도 그 폴더 안에 있어야 한다** — 빠지면 서버는 죽지 않고
#     legacy 정책으로만 돈다(조용한 성능 저하라 더 위험하다).
RUN mkdir -p /app/data/kakao

# (5) 사용자·선호 기록과 card_id 증분이 쌓이는 곳. compose 가 여기에 볼륨을 붙인다.
RUN mkdir -p /app/state

# (6) 설문 사진. 여기도 compose 가 볼륨을 붙인다(state 와 별개다).
RUN mkdir -p /app/media

WORKDIR /app/server
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
