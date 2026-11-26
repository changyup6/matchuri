# 관측 스택 — Prometheus + Grafana

앱은 이미 지표를 낸다(`GET /metrics`, `server/app/observe.py`). 하지만 `curl` 로 긁어 본
숫자는 **지금 값**일 뿐이라 "어제보다 느려졌나", "인터리빙에서 어느 정책이 이기고 있나"
같은 질문에 답하지 못한다. 그 질문에 답하려면 값을 시간축에 쌓아야 하고, 그것이 여기 있는
것들의 전부다.

```
브라우저 ──80──▶ nginx ──/grafana/──▶ grafana:3000 ──▶ prometheus:9090
                                                            │ 15초마다 긁는다
                                                            ├─▶ app:8000/metrics      (job matchuri)
                                                            ├─▶ node-exporter:9100    (job node)
                                                            ├─▶ nginx-exporter:9113   (job nginx)
                                                            └─▶ 자기 자신             (job prometheus)
```

## 무엇이 도나

| 서비스 | 이미지 | 호스트 포트 | 하는 일 |
|---|---|---|---|
| `prometheus` | `prom/prometheus:v2.55.1` | **없음** | 15초마다 넷을 긁어 30일치를 볼륨(`prom-data`)에 쌓는다 |
| `node-exporter` | `prom/node-exporter:v1.8.2` | **없음** | EC2 의 CPU·메모리·디스크·load |
| `nginx-exporter` | `nginx/nginx-prometheus-exporter:1.4.0` | **없음** | 엣지의 `stub_status`(연결 수·총 요청 수) |
| `grafana` | `grafana/grafana-oss:11.3.1` | **없음** (nginx 의 `/grafana/`) | 화면. 로그인 필요 |

**넷 다 호스트 포트를 열지 않는다.** 사람이 들어오는 문은 `http://api.example.com/grafana/`
하나뿐이다. 9090 을 호스트에 열면 인증 없이 모든 지표를 보고 아무 쿼리나 돌릴 수 있는 문이
생긴다 — `/admin` 을 Basic 인증으로 막은 것과 같은 이유로 막는다.

같은 이유로 **공개 `/metrics` 는 nginx 가 404 로 닫았다**(`nginx/default.conf` 의
`location = /metrics`). Prometheus 는 도커 내부망에서 `app:8000` 을 직접 긁으므로
밖에 열려 있을 필요가 없다.

## 파일

```
monitoring/
  prometheus/prometheus.yml          수집 대상과 주기
  prometheus/rules.yml               기록 규칙 3 · 알림 규칙 5
  grafana/provisioning/datasources/  데이터소스(uid: prometheus) — 화면에서 만들지 않는다
  grafana/provisioning/dashboards/   대시보드 공급자 설정
  grafana/dashboards/*.json          대시보드 본체. **이 파일이 원본이다**
  docker-compose.test.yml            검증용 일회용 사본(운영과 무관)
```

## 대시보드

| 이름 | uid | 무엇 |
|---|---|---|
| **맛추리 운영** | `matchuri-ops` | 트래픽 · 추천(인터리빙) · 사용자 행동 · 시스템 네 줄 |
| **맛추리 지연 상세** | `matchuri-latency` | 라우트별 p95·분포 히트맵·느린 요청·예외 |

머리기사는 운영 대시보드의 **정책별 좋아요율(1시간 이동)** 이다. 두 정책이 한 화면을
나눠 쓰고 사용자는 어느 카드가 누구 것인지 모르므로, 그 두 선의 차이가 "신 정책이 구 정책보다
나은가" 에 대한 가장 정직한 답이다.

### 패널을 더하려면

화면에서 고친 것은 **저장되지 않는다**(`allowUiUpdates: false`). 일부러 그렇게 뒀다 —
파일을 고쳐야 하므로 "누가 언제 왜 이 패널을 이렇게 바꿨나" 가 git 에 남는다.

1. Grafana 에서 마음껏 만들어 본다(패널 편집기 · Explore 는 열려 있다).
2. 패널 우상단 **⋮ → More → Panel JSON** 으로 JSON 을 복사한다.
3. `monitoring/grafana/dashboards/matchuri-ops.json` 의 `panels` 에 붙여 넣는다.
   - `id` 는 그 대시보드 안에서 겹치지 않게(행마다 10 단위로 띄워 뒀다).
   - `gridPos` 는 24칸 격자다. 아래 패널들의 `y` 를 밀어 주는 것을 잊지 말 것.
   - `datasource` 는 `{"type":"prometheus","uid":"prometheus"}` 로 고정한다.
   - `description` 에 **왜 이 패널이 필요한지**를 적는다. 숫자만 있는 패널은 아무도 안 본다.
   - `rate()` 창은 `$__rate_interval` 을 쓴다(고정 `[5m]` 은 시간 범위를 넓히면 깨진다).
4. 10초 안에 Grafana 가 파일을 다시 읽는다. 새로고침만 하면 된다.

## 알림 — Alertmanager 는 없다

`rules.yml` 의 알림 규칙 다섯(`AppDown` · `HighErrorRate` · `SlowP95` · `DiskLow` · `MemLow`)은
**평가만 되고 아무 데도 가지 않는다.** Alertmanager 를 두지 않았기 때문이다. 이유는 단순하다 —
알림을 보낼 곳(Slack 웹훅·메일)이 아직 정해지지 않았고, 받는 사람이 없는 알림 서버는
디스크만 먹으면서 "우리 알림 있어요" 라는 착각을 준다.

그래도 규칙을 써 둔 값은 있다. Prometheus 가 `ALERTS` 시계열을 만들어 주므로 지금 무엇이
울고 있는지는 Grafana 에서 볼 수 있다:

```promql
ALERTS{alertstate="firing"}
```

Slack 을 붙이는 날 `alertmanager` 서비스 하나를 더하고 `prometheus.yml` 에
`alerting:` 블록을 적으면 된다 — **규칙은 그대로 쓴다.**

## 보관 기간

30일(`--storage.tsdb.retention.time=30d`). 지금 계열 수(수백 개)로는 하루 수십 MB 라
한 달이 1~2GB 안쪽이다. 인터리빙 성적을 주 단위로 비교하려면 최소 한 달은 남아 있어야 한다.
더 늘리려면 `docker-compose.yml` 의 `command` 를 고치고 `prometheus` 만 다시 만든다.

볼륨 `prom-data` 가 지워지면 **과거 그래프가 사라진다**(앱은 멀쩡하다).
`grafana-data` 는 로그인 세션과 사용자뿐이라 지워도 화면은 파일에서 그대로 돌아온다.

## 비밀번호

| | |
|---|---|
| 비밀번호 파일 | `/home/ubuntu/matchuri-secrets/grafana-admin-password` (600, **개행 없음**) |
| 사람이 읽는 사본 | `/home/ubuntu/matchuri-secrets/grafana-admin.txt` (600) |
| 디렉토리 | `~/matchuri-secrets` (700) — `/admin` Basic 인증 평문도 여기 있다 |
| 소유자 | **`472:472`** (컨테이너 안 grafana 사용자) |

컴포즈의 `secrets:` 로 `/run/secrets/grafana_admin` 에 마운트되고 Grafana 가
`GF_SECURITY_ADMIN_PASSWORD__FILE` 로 읽는다. **환경변수에 직접 넣지 않는 이유**는
`docker inspect` 와 `docker compose config` 에 평문이 그대로 찍히기 때문이다.

파일 끝의 개행까지 비밀번호가 되므로 반드시 `printf '%s'` 로 쓴다(`echo` 는 개행을 붙인다).

**소유자가 `472:472` 여야 한다** — `nginx/.htpasswd` 가 `101:101` 이어야 하는 것과 똑같은
함정이다. 컴포즈 시크릿은 호스트 파일의 소유자·모드를 그대로 컨테이너에 넘기는데,
Grafana 는 컨테이너 안에서 **uid 472** 로 돌기 때문에 `ubuntu(1000)` 소유 + 600 이면
읽지 못하고 기동 자체가 실패한다:

```
Getting secret GF_SECURITY_ADMIN_PASSWORD from /run/secrets/grafana_admin
/run.sh: line 59: /run/secrets/grafana_admin: Permission denied
```

```bash
sudo chown 472:472 ~/matchuri-secrets/grafana-admin-password
sudo chmod 600    ~/matchuri-secrets/grafana-admin-password
```

호스트에서 값을 읽어야 할 때는 사람이 읽는 사본(`grafana-admin.txt`, `ubuntu` 소유)을 본다.

### 비밀번호 바꾸기

```bash
umask 077
PW="$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-32)"
printf '%s' "$PW" | sudo tee ~/matchuri-secrets/grafana-admin-password >/dev/null
sudo chown 472:472 ~/matchuri-secrets/grafana-admin-password   # 이걸 빼면 grafana 가 못 뜬다
sudo chmod 600     ~/matchuri-secrets/grafana-admin-password
sed -i "s|^password: .*|password: $PW|" ~/matchuri-secrets/grafana-admin.txt

cd ~/matchuri-be
docker compose -p backmock up -d --force-recreate grafana
# GF_SECURITY_ADMIN_PASSWORD 는 **admin 계정을 처음 만들 때만** 쓰인다.
# 이미 만들어진 계정은 시크릿을 바꿔도 그대로이므로 한 번 더 눌러 준다:
docker compose -p backmock exec grafana \
  grafana cli admin reset-admin-password "$(cat /run/secrets/grafana_admin)"

curl -sS -o /dev/null -w '%{http_code}\n' -u "admin:$PW" \
  http://localhost/grafana/api/org        # 200 이어야 한다
```

## TLS 가 없다는 것

`GF_SECURITY_COOKIE_SECURE=false` 다. http 로 서비스하는데 Secure 쿠키를 켜면 브라우저가
세션 쿠키를 버려 로그인이 무한 반복된다. **지금 Grafana 비밀번호는 평문으로 네트워크를
지나간다.** TLS 를 붙이는 날 이 값을 `true` 로 바꾸는 것을 잊지 말 것
(`docker-compose.yml` 의 `grafana` 블록에 같은 메모가 있다).

## 손이 가는 곳

```bash
# 수집 대상 상태 (health · lastError)
docker compose -p backmock exec prometheus \
  wget -qO- 'http://localhost:9090/api/v1/targets?state=active' \
  | jq -r '.data.activeTargets[] | "\(.labels.job)\t\(.health)\t\(.lastError)"'

# 규칙이 평가되고 있나
docker compose -p backmock exec prometheus \
  wget -qO- http://localhost:9090/api/v1/rules | jq -r '.data.groups[].rules[] | "\(.type)\t\(.name)\t\(.health)"'

# 지금 울고 있는 알림
docker compose -p backmock exec prometheus \
  wget -qO- http://localhost:9090/api/v1/alerts | jq '.data.alerts'

# 설정만 다시 읽히기 (재시작 없이)
docker compose -p backmock kill -s HUP prometheus

# 문법 검사 — 고치고 나서 반드시
docker run --rm -v ~/matchuri-be/monitoring/prometheus:/etc/prometheus:ro \
  --entrypoint promtool prom/prometheus:v2.55.1 check config /etc/prometheus/prometheus.yml
```

## 이 스택을 건드리지 않고 검증하기

`docker-compose.test.yml` 이 그 용도다. 프로젝트 이름과 볼륨이 다르고, 살아 있는
네트워크(`backmock_default`)에는 **끼어들어 읽기만** 한다.

```bash
cd ~/matchuri-be
docker compose -f monitoring/docker-compose.test.yml -p backmock-obs-test up -d
# Grafana: http://127.0.0.1:13000/grafana/  (루프백에만 열린다. SSH 터널로 본다)
docker compose -f monitoring/docker-compose.test.yml -p backmock-obs-test down -v   # 반드시 -v
```

기록은 `analysis/backtest/results/grafana-20260910.md`.
