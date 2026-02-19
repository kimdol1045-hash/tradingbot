# 구현 로드맵

> **원본 설계 문서**: [PIPELINE_v5.0.md](./PIPELINE_v5.0.md)
> **최종 업데이트**: 2026-02-19

---

## 프로젝트 현황 요약

```
상태: Sprint 10 코드 완료 — 분석 도구 + 통합 테스트 완성 (운영 전환만 남음)
파일: 74개+ (Python 44+, JSON 4개, 설계문서 12개, 배포 설정 등)
코드: ~37,500줄
Git: main 브랜치, 8 commits
```

---

## 환경 설정

```
Python: 3.12+ (개발 환경에서 3.14.3 확인)
가상환경: .venv (python3 -m venv)
패키지: pyproject.toml (pip install -e .)

프로젝트 구조:
  tradingbot/
  ├── src/
  │   ├── main.py                    # 메인 진입점 (asyncio 통합 실행)
  │   ├── collector/
  │   │   ├── collector.py           # WS 실시간 수집 + TF 합성
  │   │   ├── ws_client.py           # Hyperliquid WebSocket 클라이언트
  │   │   └── historical.py          # 과거 데이터 벌크 로더 (Binance/HL)
  │   ├── exchange/
  │   │   ├── hyperliquid.py         # REST API 래퍼
  │   │   ├── executor.py            # OrderExecutor (DRY_RUN + 실거래)
  │   │   └── reconciliation.py      # 시작시 포지션 대조
  │   ├── pipeline/
  │   │   ├── models.py              # 전체 dataclass 정의
  │   │   ├── phase1_safety.py       # 8 Safety 조건 + Stage 시스템
  │   │   ├── phase2_read.py         # 6-DNA + 레짐 분류 + MTF
  │   │   ├── phase3_scan.py         # 변곡점 + 패턴 오케스트레이터
  │   │   ├── phase4_gate.py         # 기술지표 스코어링 + 노출 체크
  │   │   ├── phase5_execute.py      # 레버리지/SL/TP/포지션 사이징
  │   │   ├── runner.py              # S1→S4 순차 파이프라인 실행
  │   │   ├── position_manager.py    # 포지션 라이프사이클 관리
  │   │   ├── equity_tracker.py      # 에이전트별 에쿼티/MDD/PF 추적
  │   │   └── scan/
  │   │       ├── sr_levels.py       # S/R 피봇 클러스터링
  │   │       ├── trendlines.py      # 선형 회귀 추세선 (R²≥0.80)
  │   │       ├── volume_profile.py  # VP + POC + Value Area
  │   │       ├── candlestick.py     # 22개 캔들스틱 패턴 (3 Tier)
  │   │       ├── chart_patterns.py  # 7개 차트 패턴
  │   │       └── inflection.py      # T1~T8 변곡점 감지
  │   ├── notify/
  │   │   └── telegram.py            # 슈퍼그룹 Topics 알림
  │   ├── openclaw/
  │   │   └── evolver.py             # GPT-4o 파라미터 진화 (4시간 주기)
  │   └── utils/
  │       ├── config.py              # 환경변수, AgentProfile, MDD정책
  │       ├── db.py                  # SQLite WAL + 스키마
  │       ├── health.py              # HTTP 헬스체크 (:8080)
  │       ├── indicators.py          # ATR, RSI 등 기술지표
  │       ├── market_stats.py        # 시장 통계 분포 계산
  │       └── params.py              # 에이전트 파라미터 JSON I/O
  ├── params/s1~s4/params.json       # 에이전트별 파라미터
  ├── scripts/
  │   ├── preflight.py               # 시작 전 검증 스크립트
  │   ├── analyze.py                 # 성과 분석 CLI (PF/MDD/Sharpe/상관관계)
  │   └── integration_test.py        # E2E 통합 테스트 (15개)
  ├── deploy/
  │   ├── tradingbot.service          # systemd 서비스 파일
  │   ├── setup.sh                    # 서버 설치 스크립트
  │   └── logrotate.conf              # 로그 로테이션
  ├── Dockerfile                      # Docker 이미지
  ├── docker-compose.yml              # 원커맨드 배포
  ├── pyproject.toml
  ├── .env.example
  ├── data/trades.db                  # SQLite (WAL mode)
  └── md/                             # 설계 문서

배포 옵션:
  1. Docker: docker compose up -d
  2. Systemd: sudo bash deploy/setup.sh → systemctl start tradingbot
  3. 로컬: .venv/bin/python -m src.main

환경변수:
  HYPERLIQUID_KEY, HYPERLIQUID_SECRET, HYPERLIQUID_TESTNET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  OPENAI_API_KEY
  TOTAL_CAPITAL, DRY_RUN, HEALTH_PORT, LOG_LEVEL
```

---

## 완료된 스프린트

### ✅ Sprint 1: 기반 + Data Collector

**구현 완료 항목:**
- [x] 프로젝트 구조 생성 (`pyproject.toml`, `src/`, `tests/`, `.gitignore`, `.env.example`)
- [x] `config.py`: 환경변수, AgentProfile 4개, MDD 5단계 정책, 비용 모델, 심볼 풀
- [x] `models.py`: SafetyResult, RegimeResult, ScanResult, GateResult, Signal 전체 정의
- [x] `db.py`: SQLite WAL mode, candles/trades/positions 스키마, INSERT OR REPLACE 중복제거
- [x] `params/s1~s4/params.json`: 4개 에이전트 파라미터 초기값
- [x] `hyperliquid.py`: REST API 래퍼 (캔들, 오더북, 펀딩, OI, 청산)
- [x] `ws_client.py`: WebSocket 실시간 연결 + 재연결 로직
- [x] `collector.py`: 5분봉 수신 → 15m/1h/4h 합성 → DB 저장 → 메모리 캐시 (200개)
- [x] `market_stats.py`: TF별 lookback (5m:7d, 15m:14d, 1h:30d, 4h:90d) 분포 계산
- [x] `indicators.py`: ATR, RSI 등 기술지표
- [x] TF-specific 백필: 시작 시 과거 데이터 자동 복구

---

### ✅ Sprint 2: Phase 1 SAFETY + Phase 2 READ

**구현 완료 항목:**
- [x] `phase1_safety.py`: SafetyResult 반환, 기본 ALLOW 동작
- [x] MDD 게이트 5단계 (normal → caution → defensive → survival → emergency)
- [x] Stage 시스템 (NORMAL / STAGE_3 / STAGE_2 / STAGE_1) + severity 0~240
- [x] 8가지 Safety 조건: spread, volume, funding, OI, liquidation, volatility, 연패, 상관관계
- [x] 동적 임계값 (시장 통계 percentile 기반, 하드코딩 제거)
- [x] ATR 급변 감지 → volatility_override 플래그
- [x] `phase2_read.py`: 6-DNA 계산 (Hurst, Entropy, Liquidation, Funding, OI Momentum, Liq Density)
- [x] DNA z-score 정규화 (rolling 7일 → 0~100 매핑)
- [x] 에이전트별 Hurst window (S1:30, S2:50, S3:80, S4:100)
- [x] DNA 가중치 합=1.0 re-normalize
- [x] 체제 분류 6가지 (STRONG_UP/WEAK_UP/SIDEWAYS/WEAK_DOWN/STRONG_DOWN/VOLATILE)
- [x] 히스테리시스 전환 (blend_progress 0→1)
- [x] MTF 가중치 합산 (S1:단일TF ~ S4:4TF)

**테스트 결과:**
- 히스토리 없이: 모든 z-score → 50.0 (SIDEWAYS) — 설계대로
- 히스토리 100샘플: 상승장 49.34점, 하락장 53.87점, 고변동 60.88점 — 보수적 기본값, Evolver가 운영 중 튜닝

---

### ✅ Sprint 3: Phase 3 SCAN (변곡점 + 패턴)

**구현 완료 항목:**
- [x] `scan/sr_levels.py`: 피봇 기반 S/R 클러스터링 + 강도 스코어링 (터치×0.45 + 볼륨×0.30 + 최신성×0.25)
- [x] `scan/trendlines.py`: 선형 회귀 추세선 (R² ≥ 0.80 필터)
- [x] `scan/volume_profile.py`: VP 히스토그램 + POC + Value Area (70%)
- [x] `scan/candlestick.py`: 22개 패턴 3 Tier (Tier1: 9개 5~9pt, Tier2: 6개 10~12pt, Tier3: 6개 13~16pt)
- [x] `scan/chart_patterns.py`: 7개 차트 패턴 (double_bottom, double_top, triple_bottom, H&S, inv_H&S, ascending/descending triangle)
- [x] `scan/inflection.py`: T1~T8 변곡점 감지 + 우선순위 정렬
- [x] `phase3_scan.py`: 오케스트레이터 (레짐×타입 가중치, 12개 시너지 콤보, MTF Grade A~F)

---

### ✅ Sprint 4: Phase 4~5 + Pipeline Runner + Position Manager

**구현 완료 항목:**
- [x] `phase4_gate.py`: 8가지 기술지표 스코어 (100점 만점), MDD/PF 동적 조정
- [x] exposure 체크: 30% hard block, 20% reduce
- [x] PF Anti-Stall: PF < 1.0 시 탐색 모드 (size_mult=0.3, threshold-10)
- [x] `phase5_execute.py`: 6단계 레버리지 계산 (레짐×신뢰도 → 변곡점 → Stage → ATR → MDD → 연패 감쇠)
- [x] ATR 기반 SL + S/R 고려 + MDD 조임 + max_loss 강제
- [x] RR 기반 TP (TP1=RR1.5/55%, TP2=RR2.5/45%) + 패턴 목표가 반영
- [x] Fixed-Loss 포지션 사이징: notional = R / sl_pct
- [x] `runner.py`: PipelineRunner — 5m 캔들 마감 → S1→S2→S3→S4 순차 실행
- [x] `position_manager.py`: submit_signal → fill → trailing stop → timeout → close

**테스트 결과:**
- LONG BTC @$51000: SL=$50670 (-0.65%), Lev=3.3x, Max Loss=$150 (1.50%)
- TP1=$51495 (RR=1.5, 55%), TP2=$51825 (RR=2.5, 45%)
- MDD Caution: Lev 3.3x→2.3x, Notional 82.4%
- 연패 감쇠: 0→3.3x, 1→2.8x, 2→2.1x, 3→1.5x, 4→1.0x

---

### ✅ Sprint 5: Telegram 알림 + Equity Tracker

**구현 완료 항목:**
- [x] `notify/telegram.py`: 슈퍼그룹 Topics (signals, fills, exits, safety, errors, daily_report, system)
- [x] Rate limiter: 20 msg/min per topic
- [x] 메시지 템플릿: notify_signal, notify_fill, notify_exit, notify_safety, notify_error, notify_daily_report, notify_system
- [x] `equity_tracker.py`: 에이전트별 에쿼티, MDD, rolling PF (최근 20거래), 연속 손실 추적
- [x] 포트폴리오 MDD 합산, 일일 리포트 생성

**테스트 결과:**
- s1: equity=$1610, mdd=0.0000, pf=3.20
- s3: equity=$2880, mdd=0.0400, streak=3 (연속 손실 3회)
- 포트폴리오 MDD: 1.14%, 일일 PnL +$430, 승률 50%, PF=2.13

---

### ✅ Sprint 6: OpenClaw Evolver + 과거 데이터

**구현 완료 항목:**
- [x] `openclaw/evolver.py`: GPT-4o 파라미터 최적화 (4시간 주기)
- [x] PARAM_BOUNDS: 14개 조정 가능 파라미터 (DNA 가중치, Gate 기준, Exit 설정)
- [x] 안전장치: ±20% max change/cycle, bounds 강제, DNA 합=1.0 re-normalize
- [x] 메트릭 수집 → 프롬프트 생성 → GPT-4o 호출 → 검증 → params 저장
- [x] `collector/historical.py`: Hyperliquid 과거 데이터 벌크 다운로드 (chunked)
- [x] Binance Futures 소스도 지원 (Hyperliquid 런칭 이전 데이터용)
- [x] CLI: `python -m src.collector.historical --years 3 --source hyperliquid`

---

### ✅ Sprint 7: 프로덕션 배포

**구현 완료 항목:**
- [x] `utils/health.py`: HTTP 헬스체크 서버 (:8080/health) — uptime, WS 상태, 포지션, MDD, Evolver
- [x] `deploy/tradingbot.service`: systemd 서비스 (auto-restart, 보안 하드닝, 2GB 메모리 제한)
- [x] `Dockerfile`: Python 3.12-slim, non-root, HEALTHCHECK 내장
- [x] `docker-compose.yml`: 볼륨 마운트 (data/params), 로그 로테이션
- [x] `deploy/setup.sh`: Ubuntu/Debian 서버 원클릭 설치 스크립트
- [x] `deploy/logrotate.conf`: 14일 로그 보존 + 압축
- [x] `scripts/preflight.py`: 환경 검증 (Python, env vars, DB, API 연결, 모듈 import)
- [x] Git 초기 커밋: 65 files, 34,525 lines

**Preflight 결과:** 20/25 passed (5 failures = API 키 미설정, 정상)

---

## 남은 스프린트

### ✅ Sprint 8: 백테스트 엔진 + 버그 수정

**구현 완료 항목:**
- [x] `src/backtest/engine.py`: 캔들 리플레이 → 파이프라인 5Phase 실행
- [x] ReplayCache: TF별 캔들 누적, collector 캐시 인터페이스
- [x] SimPosition: SL/TP/trailing/timeout 분기 처리
- [x] 비용 모델: 슬리피지 + 수수료 × conservative_mult(1.5)
- [x] `src/backtest/metrics.py`: PF, MDD, Sharpe, Sortino, Calmar, 승률, 스트릭, 에쿼티 커브
- [x] `src/backtest/report.py`: 텍스트 기반 성과 리포트 + 에이전트 비교표
- [x] `src/backtest/__main__.py`: CLI (`python -m src.backtest --synthetic`)
- [x] **버그 수정**: Phase 2 READ _raw_to_score 폴백 (레짐 항상 SIDEWAYS 문제)
- [x] **버그 수정**: Phase 4 GATE Ichimoku/ADX/LiqRisk 실제 구현 (플레이스홀더 제거)
- [x] **버그 수정**: Phase 3 SCAN 적응형 min_score + MTF 페널티 완화
- [x] **버그 수정**: T1/T3 거리 임계값 확대 (1.0→1.5, 0.3→0.5 ATR)
- [x] **버그 수정**: Volume Profile 5-19 캔들 VWAP 폴백
- [x] **버그 수정**: 백테스트 SHORT TP 정렬 순서 + tolerance 수정

---

### ✅ Sprint 9: Paper Trading + 안정성

**구현 완료 항목:**
- [x] `src/exchange/executor.py`: OrderExecutor (DRY_RUN + 실거래 모드)
- [x] signal_id UNIQUE → DB 기반 중복 주문 방지 (idempotency)
- [x] 비동기 SDK 래핑 (asyncio.to_thread), 최대 3회 재시도
- [x] DB 영속화: trades + positions 테이블 동시 기록
- [x] `src/exchange/reconciliation.py`: 시작시 교환소↔DB 포지션 대조
- [x] 고아 포지션 자동 청산, 스테일 DB 레코드 정리
- [x] `runner.py`: Phase별 try/except + pipeline_logs JSON 스냅샷
- [x] `main.py`: 글로벌 예외 핸들러, DB 초기화, 시작시 reconciliation
- [x] `position_manager.py`: OrderExecutor 통합, exit 시 DB 기록

---

### ✅ Sprint 10: 분석 도구 + 통합 테스트 (코드 완료)

**구현 완료 항목:**
- [x] `scripts/analyze.py`: 성과 분석 CLI 도구
  - 에이전트별 메트릭: PF, MDD, Sharpe, Sortino, 승률, 스트릭, 청산 사유
  - 패턴별 승률 분석 (변곡점 타입별)
  - 에이전트 간 상관관계 (동시 포지션 겹침률, 일일 PnL Pearson 상관)
  - CLI: `python scripts/analyze.py [--days N] [--agent X] [--patterns] [--correlation]`
- [x] `scripts/integration_test.py`: 15개 E2E 통합 테스트
  - DB 스키마 생성 검증
  - Pipeline 5 Phase 합성 데이터 테스트
  - OrderExecutor DRY_RUN 실행 + 멱등성
  - DB trades/positions 기록 검증
  - Reconciliation (고아/스테일 처리)
  - PositionManager 라이프사이클
  - Pipeline 구조화 로깅

**테스트 결과:** 15/15 passed, 0 failed

---

## 전체 진행 현황

| Sprint | 내용 | 상태 |
|--------|------|------|
| **S1** | 기반 + Data Collector (DB, WS, 캔들 합성) | ✅ 완료 |
| **S2** | Phase 1 SAFETY (8조건) + Phase 2 READ (6-DNA) | ✅ 완료 |
| **S3** | Phase 3 SCAN (S/R, 추세선, VP, T1~T8, 22패턴) | ✅ 완료 |
| **S4** | Phase 4~5 (GATE/EXECUTE) + Runner + Position Manager | ✅ 완료 |
| **S5** | Telegram 알림 + Equity Tracker | ✅ 완료 |
| **S6** | OpenClaw Evolver (GPT-4o) + 과거 데이터 로더 | ✅ 완료 |
| **S7** | 프로덕션 배포 (Docker, systemd, 헬스체크, preflight) | ✅ 완료 |
| **S8** | 백테스트 엔진 + 6개 버그 수정 | ✅ 완료 |
| **S9** | Paper Trading + 안정성 | ✅ 완료 |
| **S10** | 분석 도구 + 통합 테스트 | ✅ 완료 |

**코드 진행률: 10/10 스프린트 완료**

---

---

# 운영 전환 체크리스트

> 코드 개발은 완료됨. 아래는 실전 트레이딩까지 남은 **수동 셋업 + 운영 작업** 전체 목록.

---

## Phase A: 계정 및 API 키 준비

### A-1. Hyperliquid 거래소 계정

| 단계 | 작업 | 상세 |
|------|------|------|
| 1 | Hyperliquid 가입 | https://app.hyperliquid.xyz |
| 2 | Arbitrum 네트워크로 USDC 입금 | 브릿지 또는 CEX → Arbitrum 출금 |
| 3 | 트레이딩 지갑 프라이빗 키 확보 | MetaMask 등에서 hex 형태 export |
| 4 | 테스트넷 접속 확인 | https://app.hyperliquid-testnet.xyz (테스트넷 faucet으로 테스트 USDC 수령) |

**결과물**: `.env`에 아래 값 입력
```
HYPERLIQUID_KEY=0x프라이빗키_hex_문자열
HYPERLIQUID_SECRET=                          # 현재 사용 안함, 빈 값 OK
HYPERLIQUID_TESTNET=true                     # 처음엔 테스트넷
```

### A-2. Telegram 봇 + 슈퍼그룹

| 단계 | 작업 | 상세 |
|------|------|------|
| 1 | @BotFather에서 봇 생성 | `/newbot` → 토큰 복사 (형식: `1234567890:ABC...`) |
| 2 | 슈퍼그룹 생성 | Telegram 앱 → 그룹 만들기 → 슈퍼그룹으로 전환 |
| 3 | Topics 활성화 | 그룹 설정 → Topics 켜기 |
| 4 | 7개 토픽 생성 | signals, fills, exits, safety, errors, daily_report, system |
| 5 | 봇을 그룹에 관리자로 추가 | 메시지 보내기 + 토픽 관리 권한 필요 |
| 6 | Chat ID 확인 | 그룹에 메시지 보낸 후 `https://api.telegram.org/bot<TOKEN>/getUpdates` 에서 음수 chat_id 확인 |
| 7 | 토픽별 thread_id 확인 | 각 토픽에 메시지 보낸 후 getUpdates에서 `message_thread_id` 값 확인 |

**결과물**:
```
# .env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGhiJklMno...
TELEGRAM_CHAT_ID=-1001234567890
```

**토픽 ID는 `.env` 파일에 설정** (소스 코드 수정 불필요):
```
TELEGRAM_TOPIC_SIGNALS=12
TELEGRAM_TOPIC_FILLS=13
TELEGRAM_TOPIC_EXITS=14
TELEGRAM_TOPIC_SAFETY=15
TELEGRAM_TOPIC_ERRORS=16
TELEGRAM_TOPIC_DAILY_REPORT=17
TELEGRAM_TOPIC_SYSTEM=18
```
> 비워두면 모든 메시지가 그룹 메인 채팅으로 감 (동작은 하지만 분류 안됨)

### A-3. OpenAI API (선택사항 — Evolver용)

| 단계 | 작업 | 상세 |
|------|------|------|
| 1 | OpenAI 계정 + API 키 발급 | https://platform.openai.com/api-keys |
| 2 | GPT-4o 접근 확인 | `gpt-4o` 모델 사용 가능한 플랜 필요 |
| 3 | 사용량 제한 설정 권장 | 4시간마다 × 4에이전트 호출 → 하루 ~24회. 월 $5~15 예상 |

**결과물**: `.env`에 `OPENAI_API_KEY=sk-...`

> **없으면?** 봇은 정상 트레이딩하지만 파라미터 자동 진화 비활성 (수동 튜닝 필요)

---

## Phase B: 로컬 환경 검증

### B-1. 의존성 설치 + Preflight

```bash
# 가상환경 생성 (최초 1회)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# .env 파일 작성
cp .env.example .env
# 위 Phase A 결과물로 실제 값 입력

# 사전 점검 실행
python scripts/preflight.py
```

**기대 결과**: 모든 항목 ✓ (API 연결 포함)

### B-2. 통합 테스트 실행

```bash
python scripts/integration_test.py
```

**기대 결과**: 15/15 passed

---

## Phase C: 과거 데이터 + 백테스트

### C-1. 과거 캔들 데이터 다운로드

```bash
# Hyperliquid 3년치 데이터 (BTC, ETH, SOL, XRP, DOGE, AVAX, LINK)
python -m src.collector.historical --years 3 --source hyperliquid
```

> 소요 시간: 심볼당 수 분 ~ 수십 분 (API rate limit 따라 다름)

### C-2. 실 데이터 백테스트

```bash
# 전체 에이전트 백테스트
python -m src.backtest

# 에이전트별 개별 실행도 가능
python -m src.backtest --agent s1
python -m src.backtest --agent s3
```

**확인 포인트**:
- [ ] 에이전트별 PF ≥ 1.2 이상
- [ ] 최대 MDD ≤ 10%
- [ ] 거래 수가 합리적 (일 0~5건 수준)
- [ ] SHORT TP가 정상 동작하는지

### C-3. 백테스트 결과 분석

```bash
python scripts/analyze.py --patterns --correlation
```

**확인 포인트**:
- [ ] 패턴별 승률 확인 → 승률 30% 미만 패턴이 있으면 가중치 하향 고려
- [ ] 에이전트 간 상관관계 ≤ 0.5 (너무 높으면 자본 배분 조정)

---

## Phase D: 테스트넷 Paper Trading

### D-1. 테스트넷 DRY_RUN 실행

```bash
# .env 확인: HYPERLIQUID_TESTNET=true, DRY_RUN=true
DRY_RUN=true python -m src.main
```

**모니터링 (별도 터미널)**:
```bash
# 헬스체크
curl http://localhost:8080/health | python -m json.tool

# 로그 실시간 확인
tail -f data/tradingbot.log    # 또는 콘솔 출력 확인
```

### D-2. 24시간+ 무중단 테스트 (핵심)

| 확인 항목 | 기준 | 비고 |
|-----------|------|------|
| WS 연결 유지 | 24시간 중 재연결 ≤ 3회 | 로그에서 `reconnect` 검색 |
| 파이프라인 실행 | 5분마다 S1→S4 로그 출력 | `pipeline_logs` 테이블 확인 |
| 메모리 누수 | RSS ≤ 500MB 유지 | `ps aux \| grep main` |
| 헬스체크 응답 | 200 OK 지속 | `curl` 주기적 호출 |
| Telegram 알림 | system 토픽에 시작 메시지 수신 | 봇이 그룹에 메시지 보내는지 |
| DB 기록 | candles, pipeline_logs 증가 | `sqlite3 data/trades.db "SELECT COUNT(*) FROM pipeline_logs"` |
| 에러 없음 | ERROR 레벨 로그 0건 | `grep ERROR` 로 확인 |

### D-3. 테스트넷 실제 주문 테스트 (선택)

```bash
# DRY_RUN=false로 테스트넷에서 실제 주문 테스트
# .env: HYPERLIQUID_TESTNET=true, DRY_RUN=false, TOTAL_CAPITAL=1000
DRY_RUN=false python -m src.main
```

**확인 포인트**:
- [ ] 테스트넷 Hyperliquid UI에서 주문 확인
- [ ] 포지션 오픈 → SL/TP 트리거 → 클로즈 사이클 확인
- [ ] Reconciliation이 정상 동작하는지 (재시작 후)

---

## Phase E: 파라미터 최종 확정

### E-1. 검토할 파라미터 목록

| 파일 | 주요 파라미터 | 조정 기준 |
|------|-------------|----------|
| `params/s{1-4}/params.json` | DNA 가중치, Gate 임계값, Exit 설정 | 백테스트 + Paper Trading 종합 |
| `src/utils/config.py` | `SYMBOL_POOL` (거래 심볼) | 유동성 충분한 심볼만 |
| `src/utils/config.py` | `capital_pct` (자본 배분) | s1:15%, s2:25%, s3:30%, s4:30% |
| `src/utils/config.py` | 수수료 모델 (taker 3.5bps) | 실제 Hyperliquid 수수료 등급과 일치시키기 |
| `.env` → `EVOLVER_INTERVAL_HOURS` | 기본값 4시간 | 처음엔 더 길게 (12) 설정 고려 |

### E-2. 하드코딩 설정 확인

```
MDD 10% → CLOSE_ALL_AND_HALT (24시간 정지)    # config.py
에이전트 4개: s1(5m), s2(5m+15m), s3(5m+15m+1h), s4(5m+15m+1h+4h)
7개 심볼: BTC, ETH, SOL, XRP, DOGE, AVAX, LINK
OpenClaw 모델: gpt-4o (EVOLVER_MODEL env로 변경 가능)
```

---

## Phase F: 서버 배포

### F-1. 서버 선택

| 옵션 | 사양 | 비용 | 비고 |
|------|------|------|------|
| Hetzner CX22 | 2vCPU, 4GB RAM | ~$5/월 | 유럽 DC, 가성비 최고 |
| DigitalOcean Basic | 2vCPU, 2GB RAM | ~$12/월 | 글로벌 DC |
| AWS Lightsail | 2vCPU, 2GB RAM | ~$10/월 | 미국/아시아 DC |

> 최소 요구: 2GB RAM, 10GB 디스크, 안정적 네트워크

### F-2. Docker 배포 (권장)

```bash
# 서버에서
git clone <repo> /opt/tradingbot
cd /opt/tradingbot

# .env 파일 작성 (로컬에서 복사 또는 직접 편집)
vi .env

# 과거 데이터 다운로드 (서버에서)
docker compose run --rm tradingbot python -m src.collector.historical --years 3

# 실행
docker compose up -d

# 확인
docker compose logs -f
curl http://localhost:8080/health
```

### F-3. Systemd 배포 (대안)

```bash
# Ubuntu/Debian 서버에서 root로 실행
sudo bash deploy/setup.sh

# .env 편집
sudo vi /opt/tradingbot/.env

# 과거 데이터
sudo -u tradingbot /opt/tradingbot/.venv/bin/python -m src.collector.historical --years 3

# 시작
sudo systemctl start tradingbot
sudo systemctl status tradingbot
journalctl -u tradingbot -f
```

### F-4. 서버 모니터링 설정

- [ ] 헬스체크 외부 모니터링 (UptimeRobot 등 — 무료): `http://서버IP:8080/health`
- [ ] Telegram errors 토픽으로 에러 알림 자동 수신
- [ ] 디스크 사용량 확인 (SQLite DB 증가): `du -sh /opt/tradingbot/data/`
- [ ] 자동 재시작 확인: `systemctl is-enabled tradingbot` 또는 Docker `restart: unless-stopped`

---

## Phase G: 메인넷 전환 + 소액 실전

### G-1. 메인넷 전환 체크리스트

```bash
# .env 변경 (⚠️ 실제 자금 사용 시작)
HYPERLIQUID_TESTNET=false
DRY_RUN=false
TOTAL_CAPITAL=200          # 소액으로 시작 ($100~300)
```

| 확인 | 내용 |
|------|------|
| ✅ | 테스트넷 24시간+ 무중단 통과 |
| ✅ | 백테스트 PF ≥ 1.2 |
| ✅ | 모든 API 키 정상 (preflight 통과) |
| ✅ | Telegram 알림 정상 수신 |
| ✅ | 서버 자동 재시작 설정됨 |
| ✅ | Hyperliquid 지갑에 USDC 입금 완료 |

### G-2. 소액 실전 운영 ($100~300)

```bash
# 서비스 재시작
docker compose down && docker compose up -d
# 또는
sudo systemctl restart tradingbot
```

**1주차 모니터링 일일 루틴**:
```bash
# 매일 아침
curl http://서버:8080/health | python -m json.tool
python scripts/analyze.py --days 1

# 매주
python scripts/analyze.py --days 7 --patterns --correlation
```

| 지표 | 합격 기준 | 위험 신호 |
|------|----------|----------|
| PF (Profit Factor) | ≥ 1.5 | < 1.0 (손실 구간) |
| MDD | ≤ 5% | > 8% (Stage 3 진입) |
| 일 거래 수 | 0~10건 | > 30건 (과매매) |
| 승률 | ≥ 40% | < 30% |
| 연속 손실 | ≤ 5회 | > 7회 (자동 size 감쇠 확인) |

### G-3. 자본 확대 계획

| 단계 | 조건 | 자본 | 비고 |
|------|------|------|------|
| **1단계** | 시작 | $100~300 | 1~2주 운영 |
| **2단계** | PF ≥ 1.5, MDD ≤ 5%, 2주 경과 | $500~1,000 | |
| **3단계** | PF ≥ 1.3, MDD ≤ 7%, 4주 경과 | $2,000~5,000 | |
| **4단계** | PF ≥ 1.3, MDD ≤ 7%, 8주 경과 | $5,000~10,000 | 목표 자본 |

> 기준 미달 시: 즉시 `DRY_RUN=true` 전환 → 파라미터 재조정 → 백테스트 → 재시도

---

## Phase H: 장기 운영 + 유지보수

### H-1. 정기 점검 항목

| 주기 | 작업 |
|------|------|
| 매일 | Telegram daily_report 확인, 헬스체크 응답 확인 |
| 매주 | `scripts/analyze.py --patterns --correlation` 실행, DB 백업 |
| 매월 | 서버 업데이트, Python/의존성 업그레이드, Hyperliquid 수수료 변경 확인 |
| 분기 | Evolver 파라미터 이력 리뷰, 심볼 풀 재평가 |

### H-2. 장애 대응

| 상황 | 대응 |
|------|------|
| WS 끊김 (자동 재연결 실패) | 서비스 재시작: `systemctl restart tradingbot` |
| MDD 10% → CLOSE_ALL_AND_HALT | 24시간 자동 정지. 원인 분석 후 파라미터 조정 |
| 서버 다운 | 재시작 시 Reconciliation이 자동 포지션 정리 |
| Hyperliquid API 장애 | 자동 재시도 3회. 실패 시 Telegram errors 알림 |
| OpenAI API 장애 | Evolver만 정지, 트레이딩 정상 계속 |

### H-3. DB 백업

```bash
# SQLite 백업 (서비스 중에도 안전 — WAL mode)
cp data/trades.db data/trades_backup_$(date +%Y%m%d).db

# 또는 원격 백업
scp user@서버:/opt/tradingbot/data/trades.db ./backups/
```

---

## 실행 방법 (Quick Start)

```bash
# 1. 환경 설정
cp .env.example .env
# .env 파일에 실제 API 키 입력 (Phase A 참조)

# 2. 의존성 설치
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 3. 사전 점검
python scripts/preflight.py

# 4. 통합 테스트
python scripts/integration_test.py

# 5. 과거 데이터 다운로드 (최초 1회)
python -m src.collector.historical --years 3

# 6. 백테스트
python -m src.backtest
python scripts/analyze.py --patterns --correlation

# 7. 실행 (DRY_RUN 모드)
DRY_RUN=true python -m src.main

# 8. 헬스체크
curl http://localhost:8080/health
```
