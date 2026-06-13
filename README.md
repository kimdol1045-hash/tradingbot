# Tradingbot

4개 에이전트가 멀티타임프레임 시장 구조를 읽고, 5단계 파이프라인으로 암호화폐 선물 진입/청산을 관리하는 자동매매 시스템입니다. 기본값은 `DRY_RUN=true`라서 주문을 실제로 내지 않는 모의 실행 모드입니다.

> 이 프로젝트는 연구/자동화 목적의 코드입니다. 선물 거래는 원금 손실 위험이 크며, 실거래 전에는 반드시 소액/테스트넷/드라이런으로 충분히 검증해야 합니다.

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
