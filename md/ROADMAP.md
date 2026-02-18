# 구현 로드맵

> **원본 설계 문서**: [PIPELINE_v5.0.md](./PIPELINE_v5.0.md)
> **최종 업데이트**: 2026-02-18

---

## 프로젝트 현황 요약

```
상태: Sprint 8 완료 — 백테스트 엔진 + 6개 버그 수정 완료
파일: 70개+ (Python 40+, JSON 4개, 설계문서 12개, 배포 설정 등)
코드: ~35,300줄
Git: main 브랜치, 4 commits
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
  │   │   └── hyperliquid.py         # REST API 래퍼
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
  │   └── preflight.py               # 시작 전 검증 스크립트
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

### 🔲 Sprint 9: Paper Trading + 안정성

| 작업 | 산출물 | 설명 |
|------|--------|------|
| Hyperliquid 테스트넷 주문 실행 | `src/exchange/executor.py` | 시장가 주문 + SL/TP conditional order |
| 주문 idempotency | executor.py + db.py | signal_id UNIQUE → 중복 주문 방지 |
| 재시작 Reconciliation | `src/exchange/reconciliation.py` | exchange 포지션 ↔ DB 대조 + orphan 처리 |
| 테스트넷 Paper Trading 시작 | 실행 로그 | DRY_RUN=false + TESTNET=true |
| 24시간 무중단 테스트 | 모니터링 | WS 재연결, DB, 주문 실행 안정성 |
| 구조화 로깅 | pipeline_logs | Phase별 스냅샷 JSON 저장 |
| 에러 핸들링 강화 | 전체 코드 | 미처리 예외 → 로그 + 안전모드 전환 |

**완료 기준**: 테스트넷 Paper Trading 24시간 무중단 + 최소 5거래 실행.

---

### 🔲 Sprint 10: 검증 + 실전 전환

| 작업 | 산출물 | 설명 |
|------|--------|------|
| Paper Trading 분석 | 분석 리포트 | 에이전트별 PF, MDD, Sortino, 거래수 |
| 에이전트 간 상관관계 분석 | 분석 결과 | 동시 포지션 충돌률, 드로다운 상관계수 |
| 패턴별 승률 통계 | Evolver 반영 | 저성과 패턴 가중치 하향 조정 |
| 최종 파라미터 확정 | params/s1~s4 최종 | 백테스트 + Paper Trading 종합 |
| 클라우드 배포 | VPS (Docker) | Hetzner/DO $10~20/월, 볼륨 마운트 |
| 메인넷 전환 | .env | HYPERLIQUID_TESTNET=false |
| 소액 실전 ($100~300) | 실거래 | DRY_RUN=false, 실 자금 |
| 점진적 자본 확대 | 운영 판단 | PF ≥ 1.5 유지 확인 후 단계적 증액 |

**완료 기준**: 실전 $100~300으로 1주 운영. PF ≥ 1.5, MDD ≤ 5%.

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
| **S9** | Paper Trading + 안정성 | 🔲 다음 |
| **S10** | 검증 + 실전 전환 | 🔲 |

**진행률: 8/10 스프린트 완료 (코어 시스템 + 백테스트 100%, Paper Trading/실전 전환 남음)**

---

## 실행 방법 (Quick Start)

```bash
# 1. 환경 설정
cp .env.example .env
# .env 파일에 실제 API 키 입력

# 2. 의존성 설치
python3 -m venv .venv
.venv/bin/pip install -e .

# 3. 사전 점검
.venv/bin/python scripts/preflight.py

# 4. 과거 데이터 다운로드 (최초 1회)
.venv/bin/python -m src.collector.historical --years 3

# 5. 실행 (DRY_RUN 모드)
DRY_RUN=true .venv/bin/python -m src.main

# 6. 헬스체크
curl http://localhost:8080/health
```
