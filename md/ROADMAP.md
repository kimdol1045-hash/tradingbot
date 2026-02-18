# 구현 로드맵

> **원본 설계 문서**: [PIPELINE_v5.0.md](./PIPELINE_v5.0.md)

---

## 환경 설정

```
Python: 3.12+ (최신)
패키지 매니저: uv (pip 대체, 빠름)
가상환경: .venv

로컬 Mac 실행:
  tradingbot/
  ├── .venv/
  ├── pyproject.toml
  ├── src/
  │   ├── collector/       # Data Collector
  │   ├── pipeline/        # 5-Phase Pipeline
  │   ├── agents/          # OpenClaw agent configs
  │   ├── exchange/        # Hyperliquid API wrapper
  │   └── utils/           # DB, config, logging
  ├── params/              # 에이전트별 파라미터 JSON
  │   ├── s1/
  │   ├── s2/
  │   ├── s3/
  │   └── s4/
  ├── data/
  │   └── trades.db        # SQLite (WAL mode)
  ├── md/                  # 설계 문서
  └── tests/

클라우드 배포 (실전 전환 시):
  → Docker 컨테이너 1개 (Collector + Pipeline + OpenClaw)
  → VPS: Hetzner/DigitalOcean $10~20/월 (2vCPU, 4GB RAM)
  → 또는 Railway/Fly.io (컨테이너 호스팅)
  → SQLite → 볼륨 마운트로 영속성 확보
  → 환경변수: HYPERLIQUID_KEY, TELEGRAM_BOT_TOKEN, OPENAI_KEY

두 환경 모두 지원:
  → 설정 파일(.env)로 환경 분기
  → 로컬에서 개발/테스트 → Docker로 빌드 → 클라우드 배포
  → Dockerfile + docker-compose.yml 제공
```

---

## 구현 순서 (왜 이 순서인가)

```
① Data Collector (기반)
  → 없으면 아무것도 안 됨. 데이터가 있어야 파이프라인 테스트 가능.

② Pipeline Phase 1~5 (핵심 로직)
  → 매매 판단의 본체. 가장 복잡하고 가장 중요.
  → 캔들스틱 패턴만 먼저 (차트 패턴은 나중에)

③ Hyperliquid 주문 실행 (연결)
  → Pipeline → 주문까지 연결되어야 Paper Trading 가능

④ 백테스트 (검증)
  → 수집된 데이터로 과거 성능 검증
  → 여기서 파라미터 초기값 확정

⑤ OpenClaw + Telegram (AI 관리 계층)
  → Pipeline이 돌아가고 있어야 의미 있음
  → 없어도 매매는 됨. 최적화가 안 될 뿐.

⑥ Paper Trading (실전 시뮬레이션)
  → 테스트넷에서 실제 주문 실행

⑦ 실전 전환
  → 소액 → 점진 확대
```

---

## Sprint 1: 기반 + Data Collector (Week 1~2)

### Week 1 — 프로젝트 기반 + DB + 데이터 수집기 코어

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | 프로젝트 구조 생성 | `pyproject.toml`, `src/`, `tests/`, `.gitignore`, `.env.example` | `uv sync` → 의존성 설치 성공 |
| D1 | config.py: 환경변수, AgentProfile, MDD정책, 비용모델 | `src/utils/config.py` | import 에러 없음, 4개 에이전트 프로파일 로드 |
| D1 | Result dataclass 정의 | `src/pipeline/models.py` | SafetyResult, RegimeResult, ScanResult, GateResult, Signal 등 전체 정의 |
| D2 | SQLite 스키마 + WAL mode | `src/utils/db.py` | candles, trades, equity_curve, positions, pipeline_logs 테이블 생성 확인 |
| D2 | 에이전트 파라미터 JSON 초기값 | `params/s1~s4/params.json` | 4개 에이전트 JSON 로드 성공 |
| D3 | Hyperliquid REST API wrapper | `src/exchange/hyperliquid.py` | 테스트넷 연결 + candle 조회 성공 |
| D3 | WebSocket 연결 (테스트넷) | `src/collector/ws_client.py` | BTC 5m 캔들 실시간 수신 확인 |
| D4 | 5분봉 OHLCV 수신 → SQLite 저장 | `src/collector/collector.py` | DB에 candle row 적재 확인 |
| D4 | 15m/1h/4h봉 합성 로직 | collector.py 내 aggregation | 5m 3개 → 15m 1개 합성 정합성 테스트 통과 |
| D5 | 오더북/펀딩/OI/청산 데이터 수신 | collector.py 확장 | 각 필드 NULL 아닌 값 저장 확인 |
| D5 | 데이터 집계 규칙 적용 | collector.py | funding=last, OI=close, liquidation=sum, spread=mean, imbalance=mean |

### Week 2 — 안정성 + 통계 + 테스트

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | 캔들 캐시 (메모리 dict) | collector.py 내 cache | 심볼×TF별 최근 200개 유지, DB 조회 없이 접근 |
| D1 | 재연결 로직 (WS 끊김 대응) | ws_client.py | 연결 끊기 → 5초 내 재연결 + 갭 복구 |
| D2 | 시장 통계 분포 계산기 | `src/utils/market_stats.py` | TF별 lookback (5m:7d, 15m:14d, 1h:30d, 4h:90d) percentile/sigma 산출 |
| D3 | 24시간 연속 수집 테스트 | 테스트 로그 | 7개 심볼 × 4 TF, 누락 없음 확인 |
| D3 | WAL checkpoint 설정 (1시간 TRUNCATE) | db.py | checkpoint 주기 동작 확인 |
| D4 | 유닛 테스트 작성 | `tests/test_collector.py`, `tests/test_db.py` | pytest 전체 통과 |
| D5 | Dockerfile + docker-compose.yml | 프로젝트 루트 | `docker-compose up` → collector 정상 실행 |

**Sprint 1 완료 기준**: 7개 심볼 × 4 TF 실시간 수집 + DB 저장 24시간 무중단 동작.

---

## Sprint 2: Pipeline Phase 1 SAFETY + Phase 2 READ (Week 3~4)

### Week 3 — Phase 1 SAFETY 구현

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | Phase 1 SAFETY 골격 | `src/pipeline/phase1_safety.py` | SafetyResult 반환, 기본 ALLOW 동작 |
| D1 | MDD 게이트 (5단계) | phase1_safety.py | drawdown 0%→10%+ 범위별 정확한 mode 반환 |
| D2 | Stage 시스템 (NORMAL/STAGE_3/2/1) | phase1_safety.py | severity 0~240 계산, 단계별 action 매핑 |
| D2 | 8가지 Safety 조건 구현 | phase1_safety.py | 스프레드, 볼륨, 펀딩, OI, 청산, 변동성, 연패, 상관관계 |
| D3 | 동적 임계값 (분포 기반 percentile) | phase1_safety.py + market_stats | 하드코딩 제거, 시장 통계 기반 판단 |
| D3 | ATR 급변 감지 (volatility_override) | phase1_safety.py | ATR 편차 σ > 임계값 → SafetyResult.volatility_override 설정 |
| D4 | action 필드 완전 구현 | phase1_safety.py | ALLOW / BLOCK_NEW / REDUCE_LEV / CLOSE_ALL_AND_HALT 분기 |
| D4 | Phase 1 유닛 테스트 | `tests/test_phase1.py` | 정상/경고/차단 시나리오 각 2개 이상 |
| D5 | Phase 1 단위 백테스트 | `tests/backtest_phase1.py` | 수집 데이터로 Safety 판단 분포 확인 (차단률 5~15% 범위) |

### Week 4 — Phase 2 READ 구현

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | Phase 2 READ 골격 | `src/pipeline/phase2_read.py` | RegimeResult 반환 |
| D1 | 6-DNA 계산 (Hurst, Entropy, Liq, Funding, OI, LiqDensity) | phase2_read.py | 각 DNA 0~100 점수 산출 |
| D2 | DNA z-score 정규화 | phase2_read.py | 원시값 → 7일 rolling z-score → 0~100 매핑 |
| D2 | 에이전트별 Hurst window | phase2_read.py | S1:30, S2:50, S3:80, S4:100 캔들 윈도우 |
| D3 | DNA 가중치 시스템 + re-normalize | phase2_read.py | Evolver 조정 후에도 합=1.0 유지 |
| D3 | 체제 분류 (6가지) | phase2_read.py | STRONG_UP/WEAK_UP/SIDEWAYS/WEAK_DOWN/STRONG_DOWN/VOLATILE |
| D4 | 체제 전환 핸들링 (히스테리시스) | phase2_read.py | 전환 시 blend_progress 0→1 점진 이동 |
| D4 | 에이전트별 MTF 가중치 합산 | phase2_read.py | S1:단일TF, S2:2TF 가중, S3:3TF, S4:4TF |
| D5 | Phase 2 유닛 테스트 | `tests/test_phase2.py` | DNA 정규화, 체제 분류 정확성 검증 |
| D5 | Phase 1→2 통합 테스트 + 단위 백테스트 | `tests/backtest_phase2.py` | DNA 가중치 합리성 확인 (Phase 3 진입 전 검증) |

**Sprint 2 완료 기준**: Safety 8조건 동적 판단 + 6-DNA 체제 분류 정상 동작. 백테스트에서 체제 분류 분포가 합리적 (SIDEWAYS 40~60%, 트렌드 30~40%, VOLATILE 5~15%).

---

## Sprint 3: Pipeline Phase 3 — 변곡점 + 캔들스틱 패턴 (Week 5~6)

### Week 5 — 구조 분석 + 변곡점 감지

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | Phase 3 SCAN 골격 | `src/pipeline/phase3_scan.py` | ScanResult 반환 |
| D1 | ATR 계산 유틸 | `src/utils/indicators.py` | ATR(14) 검증 (수동 계산과 일치) |
| D2 | S/R 식별 (ATR 기반 클러스터링) | phase3_scan.py | 최근 200봉 기준 S/R 레벨 3~8개 추출 |
| D2 | S/R 강도 스코어링 | phase3_scan.py | 터치 횟수 + 반등 크기 기반 점수화 |
| D3 | 추세선 감지 (선형 회귀 + R² 필터) | phase3_scan.py | R² ≥ 0.7인 추세선만 유효 판정 |
| D3 | Volume Profile (POC/VAH/VAL) | phase3_scan.py | 가격 구간별 볼륨 분포 + 3개 레벨 추출 |
| D4 | 변곡점 T1~T4 (S/R 반전, 추세선 이탈, 볼프로 극단, 피보나치) | phase3_scan.py | 각 Type별 감지 + score 산출 |
| D4 | 변곡점 T5~T7 (다이버전스, DNA 극단, MTF 수렴) | phase3_scan.py | RSI/MACD 다이버전스, DNA 임계값 기반 |
| D5 | 변곡점 T8 (Funding/OI 비정상) | phase3_scan.py | 펀딩 극단 + OI 급변 감지 |
| D5 | 변곡점 유닛 테스트 | `tests/test_phase3_inflection.py` | T1~T8 각 1개 이상 감지 시나리오 |

### Week 6 — 캔들스틱 패턴 + 시너지 + 통합

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | 캔들스틱 패턴 Tier 1 (Engulfing, Hammer 등 6개) | `src/pipeline/patterns/candlestick.py` | 실제 데이터에서 패턴 감지 확인 |
| D1 | 캔들스틱 패턴 Tier 2 (Morning Star, Three Soldiers 등 8개) | candlestick.py | |
| D2 | 캔들스틱 패턴 Tier 3 (Kicker, Abandoned Baby 등 8개) | candlestick.py | |
| D2 | 에이전트별 패턴 정책 (S1/S2=캔들만, S3/S4=차트 허용) | phase3_scan.py | S1/S2에서 chart 패턴 비활성 확인 |
| D3 | 패턴-변곡점 시너지 스코어링 (cap=25) | phase3_scan.py | 시너지 보너스 계산 + 상한 적용 |
| D3 | 콤보 보너스 (50거래 후 활성, 최대 3개) | phase3_scan.py | 거래 수 부족 시 콤보 비활성 확인 |
| D4 | MTF 수렴 체크 + Grade 산출 (A~F) | phase3_scan.py | 멀티TF 일치도 → Grade 매핑 |
| D4 | confirmation_names 리스트 생성 | phase3_scan.py | PatternResult.confirmation_names: list[str] |
| D5 | Phase 1→2→3 통합 테스트 | `tests/test_pipeline_p123.py` | 전체 흐름 + ScanResult 완전 반환 |
| D5 | Phase 3 단위 백테스트 | `tests/backtest_phase3.py` | 변곡점 감지률, 패턴 정확도 통계 |

**Sprint 3 완료 기준**: S/R + 추세선 + VP + T1~T8 변곡점 + 22개 캔들스틱 패턴 동작. 백테스트에서 변곡점 감지 시 방향 정확도 ≥ 55%.

---

## Sprint 4: Pipeline Phase 4~5 + 주문 실행 + 포지션 관리 (Week 7~8)

### Week 7 — Phase 4 GATE + Phase 5 EXECUTE

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | Phase 4 GATE 골격 (agent_config 시그니처) | `src/pipeline/phase4_gate.py` | GateResult 반환, passed/reason 정상 |
| D1 | 8가지 검증 지표 구현 | phase4_gate.py | 체제 적합성, 변곡점 품질, MTF, 패턴 등 |
| D2 | MDD/PF 동적 기준 + base_size_mult 흐름 | phase4_gate.py | mdd_mode별 score_adj, leverage_mult, size_mult 적용 |
| D2 | exposure 체크 (open_risk 기준: notional × sl_pct) | phase4_gate.py | 포트폴리오 open_risk 30% hard_block |
| D3 | PF 거래 정지 루프 방지 (탐색 모드) | phase4_gate.py | PF < 1.0 + 거래수 < 일일최소 → 기준 완화 |
| D3 | Phase 4 유닛 테스트 | `tests/test_phase4.py` | 통과/차단/탐색 시나리오 각 2개 |
| D4 | Phase 5 EXECUTE 골격 | `src/pipeline/phase5_execute.py` | Signal 반환 |
| D4 | SL 계산 (ATR 기반 6단계 레버리지 매핑) | phase5_execute.py | sl_pct → leverage 테이블 정확 매핑 |
| D4 | TP 계산 (3단계 + 패턴 목표가 반영) | phase5_execute.py | TP 5-rule 우선순위 적용 |
| D5 | notional/margin 분리 + signal_id SHA256 생성 | phase5_execute.py | idempotency 키 중복 방지 |
| D5 | 슬리피지/수수료 모델 (Stage별 배수) | phase5_execute.py | NORMAL:1.5x, STAGE_3:2.5x 반영 |

### Week 8 — 주문 실행 + 포지션 관리 + 통합

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | Hyperliquid 주문 실행기 (시장가) | `src/exchange/executor.py` | 테스트넷 주문 실행 + 체결 확인 |
| D1 | SL/TP conditional order 등록 (reduce-only) | executor.py | 주문 후 SL/TP 오더 조회 가능 |
| D2 | 주문 idempotency (signal_id UNIQUE) | executor.py + db.py | 중복 signal_id → 주문 스킵 |
| D2 | 포지션 DB 기록 (positions 테이블) | executor.py | 주문 체결 → positions.status='OPEN' |
| D3 | 포지션 관리 루프 (5초 간격) | `src/exchange/position_manager.py` | asyncio 루프 정상 동작 |
| D3 | Trailing Stop 실행 (TP1 히트 후 활성) | position_manager.py | 가격 이동 시 SL 업데이트 확인 |
| D4 | 포지션 timeout + 긴급 SL + reduce-only | position_manager.py | 에이전트별 max_hold 초과 시 청산 |
| D4 | 재시작 Reconciliation | `src/exchange/reconciliation.py` | exchange 포지션 ↔ DB 대조 + orphan 처리 |
| D5 | 전체 파이프라인 E2E 테스트 (Phase 1→5) | `tests/test_pipeline_e2e.py` | 캔들 입력 → Signal 생성 → 주문 실행 흐름 |
| D5 | 에이전트별 파라미터 JSON 최종 정리 | `params/s1~s4/params.json` | 4개 에이전트 config 완전성 확인 |

**Sprint 4 완료 기준**: 전체 파이프라인 E2E 동작 (캔들 수신 → 5Phase → 주문 실행 → 포지션 관리). 테스트넷에서 1건 이상 주문 실행 성공.

---

## Sprint 5: 백테스트 + Paper Trading 시작 (Week 9~10)

### Week 9 — 백테스터 구현

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | 백테스터 엔진 골격 | `src/backtest/engine.py` | 캔들 리플레이 → 파이프라인 실행 구조 |
| D1 | 비용 모델 적용 (슬리피지 + 수수료 + Stage 배수) | engine.py | 백테스트 비용 = 실전 × conservative_mult(1.5) |
| D2 | 포지션 시뮬레이터 (SL/TP/Trailing 처리) | engine.py | 진입 → SL히트/TP히트/trailing/timeout 분기 |
| D2 | 성과 지표 산출 (PF, MDD, Sortino, 승률, 거래수) | `src/backtest/metrics.py` | 지표 계산 정확성 수동 검증 |
| D3 | S3(스윙)으로 먼저 백테스트 | 백테스트 결과 | S3 BTC/ETH 30일 백테스트 완료 |
| D3 | 파라미터 민감도 분석 | 분석 결과 | 핵심 파라미터 3~5개 영향도 확인 |
| D4 | S1~S4 전체 백테스트 | 백테스트 결과 | 4개 에이전트 각각 성과 지표 산출 |
| D4 | 파라미터 초기값 조정 | params/s1~s4 갱신 | 백테스트 기반 최적 초기값 설정 |
| D5 | 백테스트 리포트 생성기 | `src/backtest/report.py` | 에이전트별 성과 요약 + 거래 로그 출력 |

### Week 10 — Paper Trading 개시 + 안정성

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | 메인 엔트리포인트 (단일 프로세스) | `src/main.py` | Collector + Pipeline + PositionManager 통합 실행 |
| D1 | 에이전트 순차 실행 루프 (S1→S2→S3→S4) | main.py | 5분봉 마감 → 4개 에이전트 순차 파이프라인 실행 |
| D2 | 구조화 로깅 활성화 | pipeline_logs 테이블 | Phase별 스냅샷 JSON 저장 확인 |
| D2 | 테스트넷 Paper Trading 시작 | 실행 로그 | 4개 에이전트 동시 운영 개시 |
| D3 | 24시간 무중단 테스트 | 모니터링 | WS 재연결, DB 쓰기, 주문 실행 전체 안정성 |
| D3 | Reconciliation 자동화 (시작 + 1시간 주기) | reconciliation.py | 재시작 시 포지션 정합성 확인 |
| D4 | 에러 핸들링 강화 | 전체 코드 | 미처리 예외 → 로그 + 안전 모드 전환 |
| D5 | Paper Trading 모니터링 (수동, 1주간 관찰) | — | 최소 5거래 이상 실행 확인 |

**Sprint 5 완료 기준**: 백테스터로 S1~S4 성과 검증 완료. 테스트넷 Paper Trading 24시간 무중단 운영 성공.

---

## Sprint 6: OpenClaw + Telegram + 차트 패턴 (Week 11~12)

### Week 11 — AI Layer 2 + Telegram 알림

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | Telegram 봇 생성 + 슈퍼그룹 + Topics 설정 | Telegram 그룹 | 10개 Topic 생성 + thread_id 확인 |
| D1 | Telegram 알림 모듈 | `src/utils/telegram.py` | send_telegram() → Topic별 메시지 전송 성공 |
| D2 | 거래 알림 연동 (진입/청산/MDD 변경) | telegram.py + executor.py | 주문 실행 시 Live Trades Topic에 자동 메시지 |
| D2 | OpenClaw 설치 + GPT-4o OAuth 연결 | `src/agents/openclaw_client.py` | API 호출 → 응답 수신 확인 |
| D3 | 7개 에이전트 SOUL.md 작성 | `src/agents/souls/` | S1~S4, Guardian, Evolver, Reporter 프롬프트 |
| D3 | S1~S4 통합 AI 리뷰 (1콜/4시간) | `src/agents/trading_reviewer.py` | 4시간 주기로 에이전트별 리뷰 → Telegram 전송 |
| D4 | Guardian 구현 (이벤트 트리거 + 디바운싱) | `src/agents/guardian.py` | MDD 변경, 변동성 급등 → 알림 (트리거별 쿨다운) |
| D4 | Reporter 구현 (대시보드 + 일일 리포트) | `src/agents/reporter.py` | 4시간 Dashboard + 일일 Report → Telegram |
| D5 | Evolver 구현 (Sortino 적합도 + 파라미터 진화) | `src/agents/evolver.py` | 적합도 계산 → 파라미터 변경 제안 → atomic write |

### Week 12 — 차트 패턴 + AI 사이클 검증

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | 차트 패턴 Stage 2 구현 (S3/S4 전용) | `src/pipeline/patterns/chart.py` | Double Bottom, Triangle, Wedge 감지 |
| D1 | T9 (Chart Pattern) 변곡점 연동 | phase3_scan.py | 차트 패턴 → T9 변곡점 생성 |
| D2 | ZigZag 체제 감응 (min_depth_atr 동적) | chart.py | 트렌드:0.8×ATR, SIDEWAYS:1.5×ATR, VOLATILE:2.0×ATR |
| D2 | 차트 패턴 체제 필터 | chart.py | Head&Shoulders → UPTREND에서만 유효 등 |
| D3 | agentToAgent 통신 테스트 | OpenClaw 로그 | Guardian → Evolver 경고 전달 확인 |
| D3 | AI 리뷰 → Evolver → params 갱신 사이클 | 전체 흐름 | 리뷰 → 제안 → A/B shadow → 적용 사이클 1회 완주 |
| D4 | Evolver 안전장치 (동일 카테고리 동시변경 금지, min trades) | evolver.py | S3/S4: trades 조건만, 동시변경 차단 확인 |
| D5 | Paper Trading 2주차 모니터링 시작 | — | AI Layer 포함 전체 시스템 운영 |

**Sprint 6 완료 기준**: Telegram 10개 Topic 알림 정상. OpenClaw 7개 에이전트 동작. 차트 패턴 S3/S4에서 감지. AI 리뷰→Evolver 사이클 1회 이상 완주.

---

## Sprint 7: 검증 + 실전 전환 (Week 13~14+)

### Week 13 — Paper Trading 분석 + 최종 조정

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | Paper Trading 2주 결과 수집 + 분석 | 분석 리포트 | 에이전트별 PF, MDD, Sortino, 거래수 산출 |
| D1 | S1 검증: PF ≥ 1.5, MDD ≤ 3% | 판정 결과 | 미달 시 → S1 자본 비율 재배분 or 비활성화 |
| D2 | 에이전트 간 상관관계 분석 | 분석 결과 | 동시 포지션 충돌률, 드로다운 상관계수 |
| D2 | 패턴별 승률 통계 → Evolver 반영 | params 갱신 | 저성과 패턴 가중치 하향 조정 |
| D3 | 차트 패턴 Stage 3 (Triple Bottom, H&S, Cup&Handle) | chart.py 확장 | S4 전용 고급 패턴 추가 |
| D3 | 오탐률 측정 + 필터 조정 | chart.py | Paper Trading 데이터 기반 오탐률 < 30% |
| D4 | 전체 시스템 스트레스 테스트 | 테스트 로그 | 고변동성 시뮬레이션 (과거 급등락 구간) |
| D5 | 최종 파라미터 확정 | params/s1~s4 최종 | 백테스트 + Paper Trading 종합 기반 |

### Week 14+ — 클라우드 배포 + 실전 전환

| Day | 작업 | 산출물 | 완료 기준 |
|-----|------|--------|-----------|
| D1 | 클라우드 배포 (Docker → VPS) | 배포 환경 | Hetzner/DO VPS에서 전체 시스템 가동 |
| D1 | 볼륨 마운트 + 환경변수 설정 | docker-compose.yml | SQLite 영속성 + .env 분리 |
| D2 | 메인넷 전환 (HYPERLIQUID_TESTNET=false) | .env | 메인넷 API 연결 확인 |
| D2 | 소액 실전 시작 ($100~300) | 실제 거래 | 첫 실전 거래 체결 확인 |
| D3~D5 | 실전 모니터링 (1주) | Telegram 로그 | 모든 알림 정상, 주문 정합성, MDD 범위 내 |
| D5+ | 점진적 자본 확대 | 운영 결정 | PF ≥ 1.5 유지 확인 후 단계적 증액 |

**Sprint 7 완료 기준**: 실전 $100~300으로 1주 운영. PF ≥ 1.5, MDD ≤ 5%. 전체 시스템 무중단 동작.

---

## 전체 일정 요약

| Sprint | 기간 | 핵심 내용 | 주요 산출물 |
|--------|------|-----------|-------------|
| **S1** | Week 1~2 | 기반 + Data Collector | DB, WS 수집기, 24시간 수집 |
| **S2** | Week 3~4 | Phase 1 SAFETY + Phase 2 READ | Safety 8조건, 6-DNA 체제 분류 |
| **S3** | Week 5~6 | Phase 3 SCAN (변곡점+패턴) | T1~T8, 22개 캔들스틱, MTF Grade |
| **S4** | Week 7~8 | Phase 4~5 + 주문 + 포지션 관리 | GATE, EXECUTE, Trailing, Reconciliation |
| **S5** | Week 9~10 | 백테스트 + Paper Trading | 백테스터, 테스트넷 운영 개시 |
| **S6** | Week 11~12 | OpenClaw + Telegram + 차트패턴 | AI 7에이전트, 10 Topic 알림, Stage 2 패턴 |
| **S7** | Week 13~14+ | 검증 + 실전 전환 | Paper분석, VPS 배포, 소액 실전 |

**총 예상: 14주 (Sprint 1~7)**

> **버퍼 포함 현실적 예상: 16~18주**
> Sprint 3(패턴), Sprint 4(주문실행)에서 디버깅/조정이 예상보다 길어질 수 있음.
