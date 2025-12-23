# BRLN 오케스트레이터

**Python**으로 작성된 코디네이터로, 레거시 스크립트 `brln-autofee.py`, `lndg_AR_trigger.py`, `ai_param_tuner.py`를 **하나의** 프로세스로 통합하며, **SQLite**에 상태를 저장하고 외부 서비스들을 캡슐화합니다.

## 요구사항

* Python 3.11 이상
* `lncli` 설치 (PATH 또는 절대 경로)
* `bos` 설치 **또는** LND REST API 활성화 (권장)
* LND 노드 + LNDg (HTTP API 및 SQLite 데이터베이스 접근 가능)
* Amboss 계정 + GraphQL 토큰
* 선택사항: Telegram 봇 (알림용)

## 설치

### 1. uv 설치 (패키지 관리자)

이 프로젝트는 [uv](https://docs.astral.sh/uv/)를 사용하여 의존성을 관리합니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

### 2. 저장소 클론

```bash
git clone https://github.com/jvxis/brln-autofee.git
cd brln-autofee
```

### 3. 의존성 설치

```bash
uv sync
```

이는 자동으로 virtualenv (`.venv/`)를 생성하고 모든 의존성을 설치합니다.


## SQLite 초기화

```bash
python3 -m brln_orchestrator init-db
```

데이터베이스 (`brln_orchestrator.sqlite3`)가 현재 폴더에 생성됩니다.


## 보안 설정

```bash
python3 -m brln_orchestrator set-secret \
  --amboss-token "YOUR_TOKEN" \
  --telegram-token "TELEGRAM_TOKEN" \
  --telegram-chat "CHAT_ID" \
  --lndg-url "http://HOST:PORT" \
  --lndg-user "username" \
  --lndg-pass "password" \
  --lndg-db-path "/path/to/lndg/data/db.sqlite3" \
  --bos-path "/path/to/bos" \
  --lncli-path "/path/to/lncli"
```

* Telegram을 사용하지 않으면 해당 파라미터를 생략하면 됩니다.

## LND REST API (권장)

기본적으로 오케스트레이터는 채널 수수료를 업데이트하기 위해 `bos`를 사용합니다. `bos`를 호출할 때마다 새 프로세스가 시작되고 LND와의 새 연결이 생성되므로, LND가 PostgreSQL 백엔드를 사용하는 경우 데이터베이스에 과부하가 걸릴 수 있습니다.

**LND REST API**는 **지속형 HTTP 세션** (keep-alive)을 사용하여 이 문제를 해결합니다. 모든 업데이트에서 동일한 연결을 재사용합니다.

### 이점

| BOS (기본값) | REST API |
|--------------|----------|
| 채널당 1개 프로세스 | 1개 재사용 가능한 HTTP 세션 |
| 각 프로세스가 LND→PostgreSQL 연결 생성 | 지속형 연결 1개 |
| Node.js 오버헤드 | 네이티브 Python |

### 설정

```bash
python3 -m brln_orchestrator set-secret \
  --lnd-rest-host "localhost:8080" \
  --lnd-macaroon-path "/path/to/.lnd/data/chain/bitcoin/mainnet/admin.macaroon" \
  --lnd-tls-cert-path "/path/to/.lnd/tls.cert" \
  --use-lnd-rest 1
```

* `--lnd-rest-host`: LND REST API의 호스트 및 포트 (기본값: `localhost:8080`)
* `--lnd-macaroon-path`: `admin.macaroon`의 경로 (기본값: `~/.lnd/data/chain/bitcoin/mainnet/admin.macaroon`)
* `--lnd-tls-cert-path`: `tls.cert`의 경로 (기본값: `~/.lnd/tls.cert`)
* `--use-lnd-rest 1`: REST API 활성화 (BOS로 돌아가려면 `0` 사용)

### LND REST 포트 확인

```bash
grep -i "restlisten" ~/.lnd/lnd.conf
# 예: restlisten=0.0.0.0:8080
```

시작할 때 REST API가 활성화되면 오케스트레이터는 `🔌 Using LND REST API (persistent session)`을 표시합니다.

## 제외 목록

이전 목록 (pubkeys 또는 channel IDs)을 한 번만 가져옵니다:

```bash
python3 migrate-exclusion.py --db brln_orchestrator.sqlite3 \
  --autofee brln-autofee.py \
  --ar lndg_AR_trigger.py
```

동일한 유틸리티는 레거시 트리거에서 `FORCE_SOURCE_LIST`를 새 전용 테이블로 마이그레이션합니다.

수동으로 관리:

```bash
python3 -m brln_orchestrator exclusions add 0247... --note "Partner"
python3 -m brln_orchestrator exclusions rm 0247...
python3 -m brln_orchestrator exclusions list
```

항상 `pubkeys`를 사용하여 AutoFee에서 제외하고 `channel IDs`를 사용하여 Rebalance에서 제외하세요.

제외 목록은 AutoFee (pubkeys)와 AR Trigger (channel IDs) 모두에 적용됩니다.
**dry-run**에서 콘솔은 빠른 확인을 위해 무시된 항목을 나열합니다.

## 소스로 강제 설정할 채널

`FORCE_SOURCE_LIST`의 채널은 계속 유효하며, 이제 SQLite를 통해 관리됩니다:

```bash
python3 -m brln_orchestrator forced-sources add 9737... --note "peer name"
python3 -m brln_orchestrator forced-sources rm 9737...
python3 -m brln_orchestrator forced-sources list
```

**dry-run**에서 AR Trigger는 레거시를 실행하기 전에 강제된 채널을 표시합니다.

### 유용한 환경 변수

* `EXCL_DRY_VERBOSE=1` AutoFee가 콘솔/Telegram에서 "🚷excl-dry" 변경사항을 표시하도록 강제합니다 (쿼리 용도로만 dry-run 실행).

예:

```bash
EXCL_DRY_VERBOSE=1 python3 -m brln_orchestrator run --dry-run-autofee --dry-run-ar
```

## 실행

### 일반적인 Dry-run

```bash
python3 -m brln_orchestrator run \
  --mode moderado \
  --dry-run-autofee \
  --dry-run-ar \
  --dry-run-tuner \
  --loop-interval-autofee 120 \
  --loop-interval-ar 120 \
  --loop-interval-tuner 600
```

### 완전 실행 (dry-run 없음, 모든 파라미터)

```bash
python3 -m brln_orchestrator run \
  --mode moderado \
  --monthly-profit-ppm 200 \
  --monthly-profit-sat 200000 \
  --loop-interval-autofee 3600 \
  --loop-interval-ar 300 \
  --loop-interval-tuner 7200 \
  --no-dry-run-autofee \
  --no-dry-run-ar \
  --no-dry-run-tuner \
  --didactic-explain \
  --didactic-detailed
```

기본적으로 간격은 600s (AutoFee), 300s (AR), 1800s (Tuner)입니다.
원하는 빈도에 따라 조정하세요. 한 모듈만 관찰하려면 다른 모듈에서 높은 간격을 설정하세요 (예: `--loop-interval-ar 3600`).
`--no-autofee`, `--no-ar` 또는 `--no-tuner`를 사용하여 특정 루프를 비활성화합니다.
`--once`를 추가하여 단일 라운드를 실행하고 종료합니다.

이전 실행에서 `--dry-run-*`을 활성화했고 실제 모드로 돌아가고 싶다면, 저장된 상태를 지우기 위해 반대 플래그를 사용하세요: `--no-dry-run-autofee`, `--no-dry-run-ar` 및/또는 `--no-dry-run-tuner`. dry-run 플래그는 Tuner에도 존재합니다. 최종 오버라이드를 적용하려면 비활성화하는 것을 잊지 마세요.

### 작동 모드

모드는 `brln_orchestrator/presets_modes.payload_json`에 정의된 사전 설정을 적용하여 각 사이클 전에 AutoFee 및 AR Trigger의 제한을 조정합니다:

* **conservative** (기본값): 한계를 낮추고 단계를 줄이며, 쿨다운을 길게 하고 아웃레이트로 페그를 강화하여 마진 안정성을 우선시합니다.
* **moderate**: 중간 단계를 해제하고, 반복적으로 드레인된 채널에 대한 범프를 증가시키고, ROI 상한을 부드럽게 하여 수익과 활용 사이의 균형을 맞춥니다.
* **aggressive**: 단계 상한 및 서지를 확대하고, 쿨다운을 단축하고, ROI 상한을 더 관대하게 만들어 (싱크는 관찰된 가격의 90%까지), 더 빠르게 반응합니다.

`--mode`를 사용하여 모드를 선택하거나 (또는 한 번 실행하여 기본값을 설정), 해당 사전 설정이 자동으로 다음 루프에서 로드됩니다.

### 월간 수익 목표

AI Param Tuner를 지도하기 위해 `--monthly-profit-ppm` 및/또는 `--monthly-profit-sat`을 제공하세요.
목표는 7일 윈도우로 변환되며, 튜너는 `SURGE_K`, `TOP_REVENUE_SURGE_BUMP`, `REBAL_FLOOR_MARGIN`, `OUTRATE_FLOOR_FACTOR` 및 쿨다운과 같은 오버라이드를 점진적으로 조정하여 원하는 마진으로 수렴합니다.
결과 값은 `overrides` (`scope = 'autofee'`)에 유지되고 오케스트레이터의 각 사이클에 적용됩니다.

## 설정 보기

```bash
python3 -m brln_orchestrator show-config
```

## SQLite 구조

주요 테이블 포함:

* `meta`: 키/값 쌍 (버전, 설정 등)
* `secrets`: 자격 증명 및 외부 경로
* `autofee_cache`, `autofee_state`: JSON에서 마이그레이션된 레거시 상태
* `overrides`: 튜너 오버라이드 (`scope = 'autofee'`)
* `legacy_store`: 상속된 데이터용 범용 저장소 (`autofee_meta`, `assisted_ledger` 등)
* `telemetry_log`: 컴포넌트별 로그 레코드 (`autofee`, `ar`, `tuner`)
* `amboss_series`: Amboss 시리즈 캐시
* `exclusions`: 제외된 pubkeys 및 channel IDs
* `forced_sources`: AR Trigger에서 소스로 고정된 channel IDs

외부 JSON 또는 TXT 파일은 없습니다. 모든 지속형 상태는 SQLite에 있습니다.

## 로그 및 원격 분석

프로젝트는 회전 파일이 있는 중앙 집중식 로깅 시스템을 가지고 있습니다:

* `logs/brln.log` - 주 로그 (INFO+)
* `logs/brln.error.log` - 오류만 (ERROR+)

환경 변수를 통해 설정 가능:

| 변수 | 값 | 기본값 |
|----------|---------|--------|
| `BRLN_LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR | INFO |
| `BRLN_LOG_FORMAT` | text, json | text |
| `BRLN_LOG_CONSOLE` | true, false | true |
| `BRLN_LOG_FILE` | true, false | true |

디버그 예:
```bash
BRLN_LOG_LEVEL=DEBUG python3 -m brln_orchestrator run ...
```

또한 출력은 SQLite의 `telemetry_log`에 기록됩니다:

```bash
sqlite3 brln_orchestrator.sqlite3 "SELECT ts,component,level,msg FROM telemetry_log ORDER BY ts DESC LIMIT 20;"
```


## Systemd 서비스

오케스트레이터를 시스템 서비스로 실행하려면:

### 1. 서비스 파일 만들기

```bash
sudo nano /etc/systemd/system/brln-autofee.service
```

다음 내용을 붙여넣으세요 (필요에 따라 경로와 파라미터를 조정):

```ini
[Unit]
Description=BRLN AutoFee Orchestrator - Lightning Network Fee Manager
After=lnd.service
Wants=lnd.service

[Service]
Type=simple
User=lnd
Group=lnd
WorkingDirectory=/home/lnd/brln-autofee
Environment="PATH=/home/lnd/brln-autofee/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/home/lnd/brln-autofee/.venv/bin/python3 -m brln_orchestrator run \
    --mode moderado \
    --no-dry-run-autofee \
    --no-dry-run-ar \
    --no-dry-run-tuner \
    --loop-interval-autofee 3600 \
    --loop-interval-ar 300 \
    --loop-interval-tuner 7200

Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 2. 서비스 활성화 및 시작

```bash
sudo systemctl daemon-reload
sudo systemctl enable brln-autofee
sudo systemctl start brln-autofee
```

### 3. 상태 및 로그 확인

```bash
sudo systemctl status brln-autofee
sudo journalctl -fu brln-autofee
```

## 정리

로컬 데이터베이스를 제거하려면:

```bash
rm brln_orchestrator.sqlite3
```

(먼저 백업을 만드세요).
`.gitignore`는 이미 `*.sqlite3` 및 `.venv/`를 무시합니다.
