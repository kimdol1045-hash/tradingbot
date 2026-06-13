# Tradingbot

Tradingbot은 암호화폐 선물 시장을 여러 관점에서 해석하고, 진입부터 청산까지의 의사결정을 자동화하기 위한 4-Agent 트레이딩 시스템입니다. 단일 지표나 단일 전략에 의존하지 않고, 시장 레짐, 변곡점, 리스크 상태, 포지션 라이프사이클을 단계별로 분리해 검증하는 구조를 목표로 합니다.

기본값은 `DRY_RUN=true`라서 주문을 실제로 내지 않는 모의 실행 모드입니다.

> 이 프로젝트는 연구/자동화 목적의 코드입니다. 선물 거래는 원금 손실 위험이 크며, 실거래 전에는 반드시 소액/테스트넷/드라이런으로 충분히 검증해야 합니다.

## 프로젝트 개요

이 프로젝트의 핵심 질문은 “자동매매 봇이 신호를 찾는 것에서 끝나지 않고, 운영 가능한 시스템으로 행동하려면 무엇이 더 필요할까?”입니다.

일반적인 트레이딩 봇은 매수/매도 신호 생성에 집중하는 경우가 많습니다. 하지만 실제 운영에서는 신호 자체보다 더 많은 문제가 발생합니다. 시장이 갑자기 비정상적으로 변하거나, 여러 전략이 같은 코인에 동시에 진입하거나, 손절 이후 같은 실수를 반복하거나, API 장애와 주문 상태 불일치가 생길 수 있습니다.

Tradingbot은 이런 운영 문제를 전략 내부의 일부로 다룹니다. 그래서 파이프라인은 “좋아 보이는 자리인가?”뿐 아니라 “지금 거래해도 되는 환경인가?”, “이 에이전트가 감당할 수 있는 리스크인가?”, “이미 같은 자산에 노출되어 있지 않은가?”, “청산 이후 상태를 어떻게 복구할 것인가?”까지 함께 판단합니다.

## 기획 의도

- **전략과 운영을 분리하지 않기**: 신호 생성, 리스크 검증, 주문 실행, 포지션 관리, 알림, 복구를 하나의 흐름으로 연결합니다.
- **단일 전략 의존 줄이기**: `s1`~`s4` 에이전트가 서로 다른 타임프레임과 파라미터를 사용해 시장을 다르게 해석합니다.
- **리스크 우선 설계**: 수익 기회보다 생존 조건을 먼저 확인합니다. MDD, 연패, 노출 한도, 시장 급변 조건이 파이프라인 앞단과 게이트에 들어갑니다.
- **설명 가능한 자동화**: 각 단계의 판단 결과를 모델 객체와 Telegram 메시지로 남겨, 나중에 왜 진입/차단/청산했는지 추적할 수 있게 합니다.
- **운영 가능한 봇**: Docker, systemd, health check, logrotate, preflight, 재시작 복구를 포함해 장시간 실행을 전제로 설계합니다.

## 핵심 컨셉

### 4-Agent 구조

각 에이전트는 같은 시장을 보지만 서로 다른 속도로 판단합니다.

- `s1`: 5분봉 중심의 짧은 호흡
- `s2`: 5분/15분 조합의 단기 흐름
- `s3`: 5분/15분/1시간 기반의 스윙 관점
- `s4`: 4시간까지 포함하는 느린 포지션 관점

이 구조는 하나의 거대한 전략을 만드는 대신, 서로 다른 시간축의 판단을 병렬 운영하는 쪽에 가깝습니다.

### 5Phase 의사결정

Tradingbot의 매매 판단은 5단계로 나뉩니다.

1. **SAFETY**: 급변동, 스프레드, 펀딩, OI, 청산 등 거래 금지 조건 확인
2. **READ**: 멀티타임프레임 시장 레짐과 DNA 지표 계산
3. **SCAN**: 지지/저항, 추세선, 볼륨 프로파일, 다이버전스, 패턴 기반 시그널 탐색
4. **GATE**: 기술 점수, MDD, PF, 노출 한도, 에이전트 상태로 최종 통과 여부 판단
5. **EXECUTE**: 진입가, 손절, 익절, 레버리지, 주문 크기 산출

각 단계는 다음 단계의 입력을 만들거나 거래를 차단합니다. 이 덕분에 “좋은 패턴이 보인다”는 이유만으로 바로 주문하지 않습니다.

### 운영 루프

실행 중에는 데이터 수집기, 파이프라인, 포지션 매니저, 에쿼티 트래커, AI 어드바이저, 스크리너가 같은 프로세스 안에서 비동기로 움직입니다.

```text
시장 데이터 수집
  → 5분봉 마감 감지
  → 4개 에이전트 순차 판단
  → 통과 신호만 주문 후보로 제출
  → 포지션 매니저가 SL/TP/트레일링/타임아웃 관리
  → 거래 결과를 equity/PF/MDD에 반영
  → Telegram/Health/DB로 상태 기록
```

## 주요 기능

- Hyperliquid WebSocket/REST 기반 실시간 캔들, 펀딩, OI, 호가, 청산 데이터 수집
- `s1`~`s4` 4개 에이전트의 독립 파라미터 운용
- 5Phase 파이프라인
  - Phase 1: 급변동, 스프레드, 펀딩, OI, 청산 등 안전 필터
  - Phase 2: 멀티타임프레임 시장 레짐 판독
  - Phase 3: 지지/저항, 추세선, 볼륨 프로파일, 다이버전스, 캔들/차트 패턴 스캔
  - Phase 4: 기술 점수, MDD/PF, 노출 한도 기반 게이트
  - Phase 5: 진입가, 손절, 익절, 레버리지 산출
- 포지션 매니저: SL/TP, 트레일링 스탑, TP 타임아웃, 재시작 복구
- 에이전트별 equity/PF/MDD 추적
- Telegram 알림 및 간단한 채팅 명령 핸들러
- OpenAI 또는 OpenClaw 기반 시장 어드바이저/파라미터 진화 루프
- FastAPI 기반 상태/헬스 서버
- Docker Compose, systemd 배포 파일 제공

## 현재 상태와 방향

현재 저장소는 단순한 전략 실험 코드가 아니라, 실시간 운영을 전제로 한 자동매매 애플리케이션 형태를 갖추고 있습니다. 다만 프로젝트의 성격상 “완성된 수익 제품”이 아니라 계속 검증하고 조정해야 하는 연구/운영 시스템입니다.

앞으로의 개선 방향은 다음과 같습니다.

- 백테스트/실거래 결과를 더 엄격하게 비교하는 리포트 강화
- pytest 기반 단위 테스트와 통합 테스트 확대
- 파라미터 변경 이력과 성과 분석 자동화
- 대시보드에서 에이전트별 상태와 포지션 리스크를 더 명확하게 시각화
- GitHub Actions 기반 lint/test/security scan 자동화
- Secret Scanning, Push Protection 등 퍼블릭 저장소 보안 체계 유지

## 저장소 구조

```text
src/
  main.py                    # 단일 프로세스 엔트리포인트
  collector/                 # 실시간/과거 데이터 수집
  pipeline/                  # 5Phase 파이프라인과 포지션 관리
  exchange/                  # Hyperliquid 주문/조회 어댑터
  notify/                    # Telegram 알림/채팅
  ai/                        # 시장 어드바이저
  openclaw/                  # 파라미터 진화 루프
  backtest/                  # 백테스트 엔진
  dashboard/                 # 대시보드/상태 화면
params/
  s1/ s2/ s3/ s4/            # 에이전트별 파라미터
scripts/
  preflight.py               # 실행 전 환경 점검
  monitor.py                 # 운영 모니터링
deploy/
  setup.sh                   # 서버 초기 설치
  tradingbot.service         # systemd 서비스
docs/
  ARCHITECTURE.md            # 상세 아키텍처 문서
```

## 요구 사항

- Python 3.12 이상
- Hyperliquid 계정/개인키
- Telegram Bot Token 및 Chat ID
- OpenAI API Key 또는 OpenClaw Gateway

## 빠른 시작

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env  # 파일이 없다면 아래 예시를 참고해 직접 생성
python scripts/preflight.py
DRY_RUN=true python -m src.main
```

`.env`의 기본 형태는 다음과 같습니다. 실제 키는 로컬 `.env`에만 넣고 커밋하지 않습니다.

```bash
# 기본
ENV=local
LOG_LEVEL=INFO
DRY_RUN=true
DB_PATH=data/trades.db
HYPERLIQUID_TESTNET=true

# Hyperliquid
# 단일 지갑 모드
HYPERLIQUID_KEY=

# 멀티 지갑 모드. 설정 시 에이전트별 키가 우선됩니다.
HYPERLIQUID_KEY_S1=
HYPERLIQUID_KEY_S2=
HYPERLIQUID_KEY_S3=
HYPERLIQUID_KEY_S4=
WALLET_ADDRESS_S1=
WALLET_ADDRESS_S2=
WALLET_ADDRESS_S3=
WALLET_ADDRESS_S4=

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# LLM
OPENAI_API_KEY=
ADVISOR_ENABLED=true
ADVISOR_INTERVAL_HOURS=1
EVOLVER_INTERVAL_HOURS=4

# OpenClaw 사용 시
USE_OPENCLAW=false
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
OPENCLAW_GATEWAY_TOKEN=

# 서버/스케줄
HEALTH_PORT=8080
DASHBOARD_PORT=8501
BALANCE_SYNC_INTERVAL=60
DAILY_REPORT_HOUR_KST=9
SCREENER_INTERVAL_HOURS=72
SCREENER_START_HOUR_UTC=3
SCREENER_MIN_VOLUME_M=1.0
```

## 실행 모드

### 드라이런

```bash
DRY_RUN=true python -m src.main
```

주문을 실제로 제출하지 않고 로그/DB/알림 흐름을 확인합니다. 처음 실행할 때는 이 모드가 기본입니다.

### 실거래

```bash
DRY_RUN=false HYPERLIQUID_TESTNET=false python -m src.main
```

실거래 전 확인할 것:

- `scripts/preflight.py` 통과
- `.env` 개인키와 Telegram 값 확인
- `params/s*/params.json` 리스크 값 확인
- 소액으로 주문/청산/재시작 복구 테스트
- 서버 시간 동기화와 로그 로테이션 확인

## Docker 실행

```bash
docker compose up -d --build
docker compose logs -f tradingbot
curl http://localhost:8080/health
```

`docker-compose.yml`은 `.env`를 읽고, `data/`와 `params/`를 컨테이너에 마운트합니다.

## 서버 배포

Ubuntu/Debian 계열 서버에서는 다음 스크립트를 기준으로 배포할 수 있습니다.

```bash
sudo bash deploy/setup.sh
sudo systemctl start tradingbot
journalctl -u tradingbot -f
curl http://localhost:8080/health
```

서비스 파일은 `/opt/tradingbot/.env`를 환경 파일로 사용합니다.

## 백테스트와 점검

```bash
python scripts/preflight.py
python scripts/integration_test.py
python -m src.backtest --help
python scripts/monitor.py
```

현재 테스트 디렉터리는 준비되어 있지만, pytest가 수집할 단위 테스트는 아직 없습니다.

## 운영 데이터와 Git 관리

커밋하지 않는 항목:

- `.env`
- `data/*.db`, WAL/SHM 파일
- `logs/`
- `reports/*.log`, `reports/*.txt`
- 로컬 assistant/workspace 상태 파일

운영 중 생성되는 DB와 로그는 로컬/서버에만 보관합니다. 장기 보관이 필요한 리포트는 민감 정보가 없는지 확인한 뒤 별도 커밋합니다.

## 퍼블릭 저장소 보안 체크리스트

이 저장소는 공개 저장소로 운영할 수 있게 구성합니다.

- 실제 `.env`는 절대 커밋하지 않습니다.
- 개인키, Telegram 토큰, OpenAI/OpenClaw 토큰은 `.env.example`에 넣지 않습니다.
- 커밋 전 `git status`로 새로 추가되는 파일을 확인합니다.
- 로그와 DB에는 거래 내역, 지갑 주소, 실행 상태가 남을 수 있으므로 공개하지 않습니다.
- 실거래용 키는 출금 권한과 별도로 관리하고, 가능하면 에이전트별/서브계정별로 분리합니다.
- 키가 한 번이라도 노출됐다고 의심되면 즉시 폐기하고 새 키로 교체합니다.

## 참고 문서

- [시스템 아키텍처](docs/ARCHITECTURE.md)
- [SNS 콘텐츠 초안](docs/sns_content.md)
