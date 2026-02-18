# Trading Pipeline Architecture v4.0
# Self-Evolving Agent-Based Trading System
# MDD/PF Optimized | Hyperliquid Native | OpenClaw Powered
# 2026-02-18

---

## 0. 설계 철학

```
"시장은 살아있다. 파라미터도 살아있어야 한다."
```

### 핵심 원칙

1. **MDD First**: 모든 의사결정의 1순위는 Maximum Drawdown 제한. 수익보다 생존이 먼저.
2. **PF Driven**: Profit Factor(총이익/총손실)를 전략 적합도(fitness)의 핵심 지표로 사용.
3. **Zero Hardcode**: 모든 임계값, 가중치, 파라미터는 시장 데이터에서 동적으로 도출.
4. **Hyperliquid Native**: 단일 거래소에서 데이터 수집 + 주문 실행. 복잡성 제거.
5. **Agent Autonomy**: 각 에이전트가 자기 영역의 파라미터를 스스로 진화시킴.

### 비용 목표

| 항목 | v3.0 | v4.0 | 비고 |
|---|---|---|---|
| 데이터 소스 | 3 거래소 + 뉴스 + 온체인 | **Hyperliquid만** | API 비용 $0 |
| DB | Redis + InfluxDB | **SQLite** | 서버 비용 $0 |
| 서버 | 고사양 VPS | **저사양 VPS 또는 로컬** | $5~20/월 |
| AI 토큰 | ~$50~100/월 | **~$10~20/월** | 에이전트 호출 최소화 |
| 총 운영비 | $100~200/월 | **$15~40/월** | |

---

## 1. 전체 시스템 아키텍처

```
╔══════════════════════════════════════════════════════════════════╗
║                     Hyperliquid WebSocket                        ║
║         OHLCV │ OrderBook │ Funding │ OI │ Liquidations          ║
╚═══════════════════════════╤══════════════════════════════════════╝
                            │
                    ┌───────▼────────┐
                    │  Data Collector │  Python, 상시 실행
                    │  → SQLite 저장  │  1분봉 + 메타데이터
                    └───────┬────────┘
                            │
        ┌───────────────────┼───────────────────────┐
        │                   │                         │
        ▼                   ▼                         ▼
┌───────────────┐  ┌────────────────┐  ┌──────────────────────┐
│  FAST ENGINE  │  │  AGENT LAYER   │  │  EVOLUTION ENGINE    │
│  (Python)     │  │  (OpenClaw)    │  │  (Python)            │
│               │  │                │  │                      │
│  Step 0~7     │  │  6 Agents      │  │  Self-Optimizer      │
│  ~650ms       │  │  Discord 연동  │  │  MDD/PF 기반 진화    │
│  매 캔들 실행 │  │  비동기 주기   │  │  4시간 주기           │
│               │  │                │  │                      │
│  params.json  │◄─│  파라미터 갱신 │◄─│  최적 파라미터 탐색   │
│  읽어서 실행  │  │                │  │                      │
└───────┬───────┘  └────────┬───────┘  └──────────┬───────────┘
        │                   │                      │
        └───────────────────┼──────────────────────┘
                            ▼
                ┌───────────────────────┐
                │  Discord Server       │
                │  결과 보고 + 명령 수신 │
                └───────────────────────┘
```

### 3개 레이어의 역할 분리

| 레이어 | 속도 | 역할 | AI 사용 |
|---|---|---|---|
| Fast Engine | ~650ms | 매 캔들 시그널 판단 + 주문 실행 | 없음 (순수 코드) |
| Agent Layer | 분~시간 | 파라미터 갱신 + 상황 해석 + 보고 | GPT-4o (최소 호출) |
| Evolution Engine | 4시간 | MDD/PF 기반 파라미터 자동 최적화 | 없음 (수학적 최적화) |

---

## 2. Data Layer: Hyperliquid Native

### 2.1 데이터 소스 (Hyperliquid WebSocket 단일)

```python
HYPERLIQUID_STREAMS = {
    # 가격 데이터
    "candles":       "1분봉 OHLCV (5m/15m/1h/4h는 1분봉에서 합성)",
    "trades":        "실시간 체결 (틱 데이터)",

    # 시장 구조 데이터
    "orderbook_l2":  "호가창 Top 20 레벨",
    "funding":       "펀딩비 (1시간마다)",

    # 포지션/유동성 데이터
    "open_interest":  "미결제약정 변화",
    "liquidations":   "청산 이벤트 실시간",

    # 계정 데이터
    "user_state":     "내 포지션/잔고 실시간",
    "user_fills":     "내 체결 내역",
}

# Hyperliquid이 제공하지 않는 것: 뉴스, 소셜, 온체인
# → 필요 없음. 가격에 모든 정보가 이미 반영되어 있다는 전제.
#   (뉴스가 나오면 가격/볼륨/OI/펀딩비/청산에 즉시 반영됨)
```

### 2.2 저장 구조 (SQLite, 단일 파일)

```
tradingbot/
├── data/
│   ├── market.db          ← 시장 데이터 (OHLCV, funding, OI)
│   ├── trades.db          ← 거래 이력 (내 매매 기록)
│   └── params/
│       ├── sentinel.json  ← Sentinel Agent 파라미터
│       ├── analyst.json   ← Analyst Agent 파라미터
│       ├── strategist.json← Strategist Agent 파라미터
│       ├── guardian.json  ← Guardian Agent 파라미터
│       ├── commander.json ← Commander Agent 파라미터
│       └── evolver.json   ← Evolver Agent 메타 파라미터
```

```sql
-- market.db 스키마
CREATE TABLE candles (
    symbol    TEXT,
    timestamp INTEGER,
    open      REAL, high REAL, low REAL, close REAL, volume REAL,
    funding_rate    REAL,    -- 해당 시점 펀딩비
    open_interest   REAL,    -- 해당 시점 OI
    liquidation_vol REAL,    -- 1분간 청산 물량
    bid_ask_spread  REAL,    -- 스프레드
    orderbook_imbalance REAL, -- 호가 비대칭
    PRIMARY KEY (symbol, timestamp)
);

-- trades.db 스키마
CREATE TABLE trades (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT,
    side        TEXT,     -- LONG / SHORT
    entry_time  INTEGER,
    exit_time   INTEGER,
    entry_price REAL,
    exit_price  REAL,
    leverage    REAL,
    size_usd    REAL,
    pnl_usd     REAL,
    pnl_pct     REAL,
    mdd_at_entry REAL,    -- 진입 시점 포트폴리오 MDD
    regime      TEXT,     -- 진입 시 체제
    inflection_type TEXT, -- 변곡점 타입
    inflection_score REAL,
    validation_score REAL,
    exit_reason TEXT,     -- SL/TP1/TP2/TP3/TRAILING/FORCED
    params_snapshot TEXT  -- 진입 시 사용된 파라미터 JSON (복기용)
);

CREATE TABLE equity_curve (
    timestamp   INTEGER PRIMARY KEY,
    equity      REAL,
    drawdown    REAL,     -- 현재 드로다운 (0.0~1.0)
    peak_equity REAL      -- 최고 자산
);
```

### 2.3 데이터 수집기

```python
class HyperliquidCollector:
    """Hyperliquid WebSocket → SQLite 실시간 수집"""

    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.db = sqlite3.connect("data/market.db")
        self.ws = HyperliquidWebSocket()

    async def run(self):
        """메인 루프: WebSocket 수신 → 1분봉 합성 → DB 저장"""
        async for msg in self.ws.subscribe(self.symbols):
            if msg.type == "candle_close":
                # 1분봉 클로즈 시: DB 저장 + Fast Engine 트리거
                self._save_candle(msg)
                await self.trigger_pipeline(msg.symbol)

            elif msg.type == "liquidation":
                # 실시간 청산 이벤트: 1분봉에 누적
                self._accumulate_liquidation(msg)

            elif msg.type == "orderbook":
                # 호가창: 최신 스냅샷만 메모리 유지
                self._update_orderbook(msg)

    async def trigger_pipeline(self, symbol: str):
        """1분봉 클로즈 → Fast Engine 실행"""
        candles = self._get_recent_candles(symbol, count=300)
        orderbook = self._get_current_orderbook(symbol)
        params = self._load_all_params()  # JSON 파일 읽기 (~0.5ms)

        result = fast_engine.run(symbol, candles, orderbook, params)

        if result.signal:
            await self.execute_on_hyperliquid(result.signal)
            self._save_trade(result.signal)
```

---

## 3. Fast Engine: Step 0~7 (동적 파라미터 버전)

### 3.0 핵심 변경: 모든 하드코딩 → params.json 참조

기존 시스템의 모든 하드코딩된 임계값을 **외부 파라미터 파일**에서 읽도록 변경합니다. 파라미터 파일은 Agent Layer와 Evolution Engine이 동적으로 갱신합니다.

```python
class FastEngine:
    """Step 0~7 실행 엔진. 매 캔들마다 ~650ms."""

    def __init__(self):
        self.params = {}       # 메모리에 캐시된 파라미터
        self.params_mtime = 0  # 파일 수정 시간 (변경 감지)

    def run(self, symbol, candles, orderbook, params) -> PipelineResult:
        self.params = params

        # Step 0: 극단 감시
        safety = self.step0_sentinel(symbol, candles[-1], orderbook)
        if safety.stage in ["ACTIVE"]:
            return PipelineResult(signal=None, blocked_by="step0")

        # Step 1: 체제 분류
        regime = self.step1_analyst(symbol, candles)

        # Step 2: 전환 핸들링
        stable_regime = self.step2_transition(symbol, regime)

        # Step 3: 차트 구조
        structure = self.step3_strategist(symbol, candles, stable_regime)

        # Step 4: 변곡점 감지
        inflection = self.step4_inflection(symbol, candles, structure, stable_regime)
        if inflection.score < self.params['guardian']['min_inflection_score']:
            return PipelineResult(signal=None, blocked_by="step4")

        # Step 5: 상태 검증
        validation = self.step5_guardian(symbol, candles, inflection, stable_regime)
        if not validation.passed:
            return PipelineResult(signal=None, blocked_by="step5")

        # Step 6: 출구 전략
        exit_plan = self.step6_exit(symbol, candles, inflection, stable_regime)

        # Step 7: 시그널 생성
        signal = self.step7_signal(symbol, inflection, validation, exit_plan,
                                    stable_regime)

        return PipelineResult(signal=signal)
```

### 3.1 Dynamic Step 0: Sentinel (동적 극단 감시)

**기존 문제**: 극단 조건 임계값이 전부 고정 (급격한 가격 변동 3%, 스프레드 3배 등)

**변경**: 모든 임계값이 **최근 N일 시장 통계에서 자동 도출**

```python
# sentinel.json — Evolver/Agent가 동적으로 갱신
{
    "version": "4.0.0",
    "updated_at": "2026-02-18T14:00:00Z",
    "updated_by": "evolver",

    // ============================================
    // 핵심 변경: 임계값이 "배수"로 정의됨
    // 절대값이 아니라 최근 시장 통계 대비 상대값
    // ============================================

    "extreme_detection": {
        // 기존: price_change_threshold = 0.03 (3% 고정)
        // 변경: 최근 7일 1분봉 가격변화 표준편차의 N배
        "price_change_sigma": 3.5,

        // 기존: spread_threshold = 3.0 (24시간 중간값의 3배 고정)
        // 변경: 최근 7일 스프레드 분포의 N-percentile
        "spread_percentile": 97,

        // 기존: volatility_ratio = 2.5 (1h ATR / 24h ATR 고정)
        // 변경: 동적 비율
        "volatility_ratio_sigma": 2.5,

        // 기존: volume_surge = 3.0 (24시간 평균의 3배 고정)
        // 변경: 볼륨 분포 기반
        "volume_surge_percentile": 95,
        "volume_drought_percentile": 5,

        // 기존: orderbook_imbalance = 0.8 고정
        // 변경: 최근 분포 기반
        "orderbook_imbalance_percentile": 95
    },

    "stage_rules": {
        // Stage 1→2 전환: 최소 유지 시간
        "stage1_min_minutes": 5,
        // Stage 2→3: 회복 조건 달성 비율
        "stage2_recovery_ratio": 0.6,
        "stage3_recovery_ratio": 0.8,
        "stage4_min_minutes": 30,

        // [NEW] MDD 기반 Stage 강화
        // 현재 MDD가 목표의 70% 이상이면 Stage 유지 시간 2배
        "mdd_caution_ratio": 0.70,
        "mdd_caution_multiplier": 2.0
    },

    // 통계 기반 자동 갱신 주기
    "recalculate_stats_every_hours": 4,

    // 최근 N일 데이터로 통계 산출
    "lookback_days": 7
}
```

**동적 임계값 계산 로직:**

```python
def calculate_dynamic_thresholds(candles_7d, params):
    """최근 7일 데이터에서 임계값을 자동 산출"""
    price_changes = [abs(c.close - c.open) / c.open for c in candles_7d]
    spreads = [c.bid_ask_spread for c in candles_7d]
    volumes = [c.volume for c in candles_7d]
    imbalances = [c.orderbook_imbalance for c in candles_7d]

    return {
        "price_change_threshold": (
            np.mean(price_changes) +
            params['price_change_sigma'] * np.std(price_changes)
        ),
        "spread_threshold": np.percentile(spreads, params['spread_percentile']),
        "volume_surge_threshold": np.percentile(volumes, params['volume_surge_percentile']),
        "volume_drought_threshold": np.percentile(volumes, params['volume_drought_percentile']),
        "imbalance_threshold": np.percentile(imbalances, params['orderbook_imbalance_percentile']),
    }
    # → 시장 변동성이 높아지면 임계값이 자동으로 넓어짐
    # → 시장이 조용해지면 임계값이 자동으로 좁아짐
    # → 어떤 시장 환경에서든 적정 수준의 극단 감지 유지
```

### 3.2 Dynamic Step 1: Analyst (동적 체제 분류)

**기존 문제**: DNA 가중치 (hurst=0.40, entropy=0.30, liquidation=0.30) 고정

**변경**: 최근 거래 성과에서 **어떤 가중치 조합이 MDD를 최소화하면서 PF를 극대화했는지** 학습

```python
# analyst.json
{
    "version": "4.0.0",
    "updated_at": "2026-02-18T14:00:00Z",
    "updated_by": "evolver",

    "dna_weights": {
        // 심볼별로 다른 가중치 (시장 특성이 다르므로)
        "BTC-USD": { "hurst": 0.42, "entropy": 0.28, "liquidation": 0.30 },
        "ETH-USD": { "hurst": 0.38, "entropy": 0.32, "liquidation": 0.30 },
        "SOL-USD": { "hurst": 0.35, "entropy": 0.35, "liquidation": 0.30 },
        "_default": { "hurst": 0.40, "entropy": 0.30, "liquidation": 0.30 }
    },

    // 체제 분류 경계값도 동적
    "regime_boundaries": {
        // 기존: Hurst > 0.6 이고 방향 양수면 STRONG_UPTREND (고정)
        // 변경: 최근 데이터 분포 기반
        "strong_trend_hurst_percentile": 80,  // 상위 20%
        "weak_trend_hurst_percentile": 60,    // 상위 40%
        "sideways_entropy_percentile": 70,    // 엔트로피 상위 30%
        "volatile_liquidation_percentile": 90  // 청산압력 상위 10%
    },

    // MTF 가중치도 동적
    "mtf_weights": {
        "5m": 0.40, "15m": 0.25, "1h": 0.20, "4h": 0.15
    },

    // 시간 가중치 반감기 (초)
    "time_decay_half_life": 300,

    // 확신도 증폭 한도
    "confidence_amplification_max": 1.20,
    "confidence_floor": 0.40
}
```

### 3.3 Dynamic Step 2: Transition (적응형 전환)

```python
# 별도 JSON 불필요 — analyst.json에 포함
{
    // ...analyst.json 내부...

    "transition": {
        // 기존: grace_period = 규칙 기반 1~7캔들
        // 변경: 최근 전환 성공률 기반 자동 조정

        // 최근 N회 전환 중 "정확했던" 전환의 평균 유예 기간을 참조
        "base_grace": 3,           // 기본값 (데이터 없을 때)
        "min_grace": 1,            // 최소
        "max_grace": 7,            // 최대

        // 확신도에 따른 유예 조정 (선형 보간)
        "high_confidence_threshold": 0.85,  // 이상이면 grace -1
        "low_confidence_threshold": 0.55,   // 이하면 grace +2

        // 블렌딩 기간도 동적
        "blend_base": 3,
        "blend_per_distance": 0.5,  // 체제 거리당 추가 캔들

        // VOLATILE 긴급 진입: 유예 최소화
        "volatile_entry_grace": 1,
        "volatile_exit_grace": 5
    }
}
```

### 3.4 Dynamic Step 3: Strategist (동적 차트 구조)

```python
# strategist.json
{
    "version": "4.0.0",
    "updated_at": "2026-02-18T14:00:00Z",
    "updated_by": "evolver",

    "sr_levels": {
        // S/R 강도 가중치: Evolver가 백테스트 기반 최적화
        "weights": {
            "touch_count": 0.45,     // 기존 0.50
            "avg_volume": 0.30,      // 기존 0.25
            "recency": 0.25          // 기존 0.25
        },

        // 클러스터링 임계값: 체제별이 아니라 ATR 기반 동적
        // 기존: RANGE_BOUND=0.3%, VOLATILE=1.0% (고정)
        // 변경: ATR의 N배로 통일
        "cluster_threshold_atr_ratio": 0.5,

        // 최대 반환 레벨 수
        "max_levels": 5,

        // [NEW] 백테스트 기반 최소 반응률
        // 이 이하의 강도를 가진 레벨은 무시
        "min_strength": 0.40
    },

    "trendline": {
        // R-squared 임계값: 동적 (변동성에 반비례)
        // 기존: 체제별 고정 (0.80~0.90)
        // 변경: base + volatility_adjustment
        "r_squared_base": 0.82,
        "r_squared_vol_adjustment": 0.05,  // ATR 높으면 기준 완화

        // 무효화 임계값: ATR 기반 동적
        "invalidation_atr_ratio": 1.5,  // ATR의 1.5배 이탈 시 무효화

        // 최소 터치 포인트
        "min_touch_points": 3
    },

    "volume_profile": {
        // 빈 수: 데이터 양에 비례
        "bins_per_100_candles": 30,  // 100캔들당 30빈
        "min_bins": 30,
        "max_bins": 150,

        // Value Area 비율: 체제에 무관하게 고정 가능
        "value_area_pct": 0.70
    },

    "context": {
        // 거리 기반 신뢰도 곡선 파라미터
        "distance_decay_rate": 2.0,    // 거리에 따른 신뢰도 감쇠 속도
        "max_relevant_distance_atr": 3.0, // ATR의 3배 넘으면 NEUTRAL

        // 허위 돌파 감지
        "false_breakout_window_candles": 3,
        "false_breakout_return_threshold_atr": 0.5,
        "false_breakout_volume_drop_pct": 0.30
    }
}
```

### 3.5 Dynamic Step 4: Inflection (동적 변곡점 스코어링)

```python
# strategist.json 내부에 포함
{
    // ...strategist.json 계속...

    "inflection": {
        // 체제별 Type 가중치 매트릭스
        // Evolver가 MDD/PF 기반으로 자동 최적화
        "regime_type_weights": {
            "STRONG_UPTREND":  { "t1": 0.80, "t2": 0.90, "t3": 0.95, "t4": 0.60, "t5": 0.50, "t6": 0.85, "t7": 0.30 },
            "WEAK_UPTREND":    { "t1": 0.85, "t2": 0.75, "t3": 0.80, "t4": 0.50, "t5": 0.55, "t6": 0.70, "t7": 0.25 },
            "SIDEWAYS":        { "t1": 0.90, "t2": 0.30, "t3": 0.40, "t4": 0.20, "t5": 0.70, "t6": 0.50, "t7": 0.15 },
            "WEAK_DOWNTREND":  { "t1": 0.80, "t2": 0.70, "t3": 0.75, "t4": 0.85, "t5": 0.50, "t6": 0.80, "t7": 0.40 },
            "STRONG_DOWNTREND":{ "t1": 0.70, "t2": 0.80, "t3": 0.90, "t4": 0.95, "t5": 0.40, "t6": 0.90, "t7": 0.50 },
            "VOLATILE":        { "t1": 0.20, "t2": 0.10, "t3": 0.15, "t4": 0.10, "t5": 0.15, "t6": 0.10, "t7": 0.05 }
        },

        // 점수 보너스 가중치 (6가지 보너스 항목)
        "score_weights": {
            "base":           0.25,  // 기본 점수 비중
            "distance":       0.15,  // 거리 보너스 비중
            "confirmation":   0.20,  // 확인 지표 비중
            "context_match":  0.20,  // 컨텍스트 일치 비중
            "secondary_type": 0.10,  // Secondary Type 비중
            "mtf_convergence": 0.10  // MTF 수렴 비중
        },

        // 합격 기준: 동적 (PF에 따라 조정)
        // PF > 2.0이면 기준 낮춰서 더 많은 시그널 허용
        // PF < 1.5이면 기준 높여서 엄격하게
        "pass_score_base": 70,
        "pass_score_pf_adjustment": true,
        "pass_score_range": [60, 85]
    }
}
```

### 3.6 Dynamic Step 5: Guardian (MDD 중심 검증)

**v4.0의 핵심 변경: MDD를 Step 5 검증의 1순위로 격상**

```python
# guardian.json
{
    "version": "4.0.0",
    "updated_at": "2026-02-18T14:00:00Z",
    "updated_by": "evolver",

    // ============================================
    // MDD 기반 동적 리스크 제어 (v4.0 핵심)
    // ============================================
    "mdd_control": {
        "target_max_mdd": 0.10,       // 목표 최대 MDD: 10%
        "warning_mdd": 0.05,          // 경고 MDD: 5%
        "critical_mdd": 0.08,         // 위험 MDD: 8%
        "emergency_mdd": 0.10,        // 긴급 MDD: 10% (전 포지션 청산)

        // MDD 구간별 행동 정책
        "mdd_policy": {
            // MDD 0~3%: 정상 운영
            "normal": {
                "mdd_range": [0.0, 0.03],
                "leverage_multiplier": 1.0,
                "position_size_multiplier": 1.0,
                "min_validation_score": "base"  // 체제별 기본값
            },
            // MDD 3~5%: 주의 모드
            "caution": {
                "mdd_range": [0.03, 0.05],
                "leverage_multiplier": 0.7,      // 레버리지 30% 감소
                "position_size_multiplier": 0.7,  // 포지션 30% 축소
                "min_validation_score": "+5"      // 기준 +5점 상향
            },
            // MDD 5~8%: 방어 모드
            "defensive": {
                "mdd_range": [0.05, 0.08],
                "leverage_multiplier": 0.4,      // 레버리지 60% 감소
                "position_size_multiplier": 0.5,  // 포지션 50% 축소
                "min_validation_score": "+15",    // 기준 +15점 상향
                "allowed_regimes": ["STRONG_UPTREND", "STRONG_DOWNTREND"]
                // 강한 추세에서만 진입 허용
            },
            // MDD 8~10%: 생존 모드
            "survival": {
                "mdd_range": [0.08, 0.10],
                "leverage_multiplier": 0.2,      // 레버리지 80% 감소
                "position_size_multiplier": 0.3,  // 포지션 70% 축소
                "min_validation_score": "+25",    // 기준 +25점 상향
                "max_concurrent_positions": 1     // 동시 1포지션만
            },
            // MDD ≥ 10%: 긴급 모드
            "emergency": {
                "mdd_range": [0.10, 1.0],
                "action": "CLOSE_ALL_AND_HALT",   // 전 포지션 청산 + 거래 중단
                "halt_hours": 24,                  // 24시간 중단
                "notify": true                     // Discord 긴급 알림
            }
        }
    },

    // PF 기반 동적 검증 기준
    "pf_control": {
        "target_pf": 2.0,              // 목표 Profit Factor
        "min_pf": 1.3,                 // 최소 PF (이하면 기준 강화)

        // 최근 20거래 기준 롤링 PF
        "rolling_window": 20,

        // PF에 따른 합격 기준 조정
        "pf_score_adjustment": {
            "pf_above_2.5": -5,     // PF 좋으면 기준 살짝 완화 (더 많은 기회)
            "pf_2.0_to_2.5": 0,     // 목표 달성 시 유지
            "pf_1.5_to_2.0": +5,    // PF 부족하면 기준 강화
            "pf_1.0_to_1.5": +10,   // PF 나쁘면 많이 강화
            "pf_below_1.0": +20     // PF 1 미만이면 대폭 강화 (손실 중)
        }
    },

    // 기존 10가지 검증 지표 유지 (가중치는 동적)
    "validation_weights": {
        "rsi": 0.15,
        "ichimoku": 0.15,
        "entropy": 0.10,
        "adx": 0.10,
        "volume_cvd": 0.10,
        "htf_consensus": 0.15,
        "liquidation_pressure": 0.10,
        "mfi": 0.05,
        "atr": 0.05,
        "divergence": 0.05
    },

    // 체제별 합격 기준 (동적 조정의 base)
    "base_pass_scores": {
        "STRONG_UPTREND": 60,
        "WEAK_UPTREND": 70,
        "SIDEWAYS": 75,
        "WEAK_DOWNTREND": 70,
        "STRONG_DOWNTREND": 65,
        "VOLATILE": 999     // 사실상 차단
    },

    // 포트폴리오 리스크 한도
    "portfolio_limits": {
        "max_concurrent_positions": 5,
        "max_single_symbol_exposure": 0.30,    // 자본의 30%
        "max_total_exposure": 0.80,            // 자본의 80%
        "max_correlated_exposure": 0.40,       // 상관 그룹 40%
        "daily_loss_limit": 0.03               // 일일 손실 3%
    },

    // 연속 손실 제어
    "consecutive_loss": {
        "max_streak": 4,
        "leverage_decay": [1.0, 0.80, 0.60, 0.40, 0.20],  // 연속 손실별 레버리지 배수
        "halt_after": 5,           // 5연패 후 중단
        "halt_hours": 8            // 8시간 중단
    }
}
```

**Step 5 실행 로직 (MDD 우선):**

```python
def step5_guardian(self, symbol, candles, inflection, regime):
    params = self.params['guardian']
    current_mdd = self._get_current_mdd()
    rolling_pf = self._get_rolling_pf(window=params['pf_control']['rolling_window'])

    # ========== Phase 0: MDD 체크 (최우선) ==========
    mdd_policy = self._get_mdd_policy(current_mdd, params['mdd_control'])

    if mdd_policy['action'] == 'CLOSE_ALL_AND_HALT':
        self._close_all_positions()
        self._halt_trading(mdd_policy['halt_hours'])
        return ValidationResult(passed=False, reason="MDD_EMERGENCY")

    # ========== Phase 1: 합격 기준 동적 산출 ==========
    base_score = params['base_pass_scores'][regime.name]

    # MDD 구간 조정
    mdd_adjustment = self._get_mdd_score_adjustment(mdd_policy)

    # PF 조정
    pf_adjustment = self._get_pf_score_adjustment(rolling_pf, params['pf_control'])

    # 최종 합격 기준
    pass_score = base_score + mdd_adjustment + pf_adjustment
    pass_score = max(50, min(95, pass_score))  # 50~95 범위 제한

    # ========== Phase 2: 기존 10가지 지표 검증 ==========
    score = self._evaluate_indicators(candles, inflection, regime, params)

    # ========== Phase 3: 레버리지/포지션 MDD 기반 조정 ==========
    leverage_mult = mdd_policy['leverage_multiplier']
    size_mult = mdd_policy['position_size_multiplier']

    return ValidationResult(
        passed=(score >= pass_score),
        score=score,
        pass_threshold=pass_score,
        leverage_multiplier=leverage_mult,
        position_size_multiplier=size_mult,
        mdd_mode=mdd_policy['name'],
        rolling_pf=rolling_pf
    )
```

### 3.7 Dynamic Step 6: Commander (동적 출구)

```python
# commander.json
{
    "version": "4.0.0",
    "updated_at": "2026-02-18T14:00:00Z",
    "updated_by": "evolver",

    "stop_loss": {
        // 손절 방식 우선순위 유지 (추세선 → S/R → ATR → 고정%)
        // 파라미터만 동적

        // ATR 배수: 체제 무관, 변동성에 자동 적응
        "atr_multiplier_base": 1.5,

        // [NEW] MDD 기반 손절 타이트닝
        // 현재 MDD가 높을수록 손절을 가깝게
        "mdd_tighten_factor": {
            "normal": 1.0,       // MDD 0~3%: 기본
            "caution": 0.85,     // MDD 3~5%: 15% 타이트
            "defensive": 0.70,   // MDD 5~8%: 30% 타이트
            "survival": 0.50     // MDD 8~10%: 50% 타이트
        },

        // 최대 손실 (절대 상한)
        "max_loss_pct": 0.03    // 단일 거래 최대 3% 손실
    },

    "take_profit": {
        // TP 레벨 수: 동적 (체제에 따라)
        // 강한 추세 → 3단계, 횡보 → 2단계
        "levels_by_regime": {
            "STRONG_UPTREND": 3,
            "WEAK_UPTREND": 3,
            "SIDEWAYS": 2,
            "WEAK_DOWNTREND": 3,
            "STRONG_DOWNTREND": 3
        },

        // RR 비율: ATR 기반 동적
        "tp1_rr_range": [1.2, 2.0],   // 최소~최대
        "tp2_rr_range": [2.0, 3.5],
        "tp3_rr_range": [3.0, 5.0],

        // 분할 비율: Evolver가 MDD/PF 기반 최적화
        "split_ratios": {
            "STRONG_UPTREND":  [35, 35, 30],
            "WEAK_UPTREND":    [45, 35, 20],
            "SIDEWAYS":        [55, 45, 0],
            "WEAK_DOWNTREND":  [45, 35, 20],
            "STRONG_DOWNTREND":[40, 35, 25]
        }
    },

    "trailing_stop": {
        // TP1 도달 후 활성화
        "activation": "after_tp1",

        // 트레일링 간격: ATR 기반
        "trail_atr_multiplier": 1.2,

        // [NEW] MDD 기반 트레일링 조정
        // MDD 높을 때 → 트레일링 타이트 → 수익 빠르게 확보
        "mdd_trail_factor": {
            "normal": 1.0,
            "caution": 0.90,
            "defensive": 0.75,
            "survival": 0.60
        }
    },

    "forced_exit": {
        "max_hold_hours": 48,     // 최대 보유 시간
        "step0_active_action": "immediate_close",
        "regime_reversal_action": "close_at_next_candle"
    },

    "leverage": {
        // 레버리지 테이블: 체제 × 확신도
        "table": {
            "STRONG_UPTREND":  { "high": 8, "medium": 5, "low": 3 },
            "WEAK_UPTREND":    { "high": 5, "medium": 3, "low": 2 },
            "SIDEWAYS":        { "high": 3, "medium": 2, "low": 1 },
            "WEAK_DOWNTREND":  { "high": 5, "medium": 3, "low": 2 },
            "STRONG_DOWNTREND":{ "high": 7, "medium": 4, "low": 2 },
            "VOLATILE":        { "high": 1, "medium": 1, "low": 1 }
        },

        // 확신도 구간 정의
        "confidence_thresholds": {
            "high": 0.80,
            "medium": 0.60
        },

        // 절대 상한
        "absolute_max": 10,

        // [NEW] 자본 기반 상한
        "capital_based_max": {
            "below_1k": 7,
            "1k_to_10k": 10,
            "10k_to_50k": 7,
            "above_50k": 5
        }
    }
}
```

---

## 4. Agent Layer: OpenClaw 에이전트 구성

### 4.0 에이전트 설계 원칙

```
에이전트는 실시간 판단을 하지 않는다.
에이전트는 "메타 판단"을 한다.
 - 파라미터가 적절한가?
 - 최근 성과가 나빠진 원인은?
 - 어떤 방향으로 조정하면 MDD가 줄고 PF가 올라가는가?
에이전트의 출력은 JSON 파라미터 파일 업데이트다.
```

### 4.1 6개 에이전트 구조

```
┌──────────────────────────────────────────────────────────┐
│                    OpenClaw Agent Layer                    │
│                    Model: GPT-4o (OAuth)                   │
│                    Channel: Discord                        │
│                                                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ Sentinel │ │ Analyst  │ │Strategist│ │ Guardian │    │
│  │ Agent    │ │ Agent    │ │ Agent    │ │ Agent    │    │
│  │          │ │          │ │          │ │          │    │
│  │ Step 0   │ │ Step 1+2 │ │ Step 3+4 │ │ Step 5   │    │
│  │ 극단감시 │ │ 체제분류 │ │ 차트분석 │ │ 리스크   │    │
│  │ 파라미터 │ │ 파라미터 │ │ 파라미터 │ │ 파라미터 │    │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘ └─────┬────┘    │
│        │            │            │            │          │
│  ┌─────▼────────────▼────────────▼────────────▼─────┐   │
│  │              Evolver Agent (총괄 최적화)           │   │
│  │                                                    │   │
│  │  - MDD/PF 기반 전체 파라미터 진화                   │   │
│  │  - 에이전트 간 파라미터 일관성 검증                  │   │
│  │  - 백테스트 기반 검증 후 적용                       │   │
│  └───────────────────┬──────────────────────────────┘   │
│                      │                                    │
│  ┌───────────────────▼──────────────────────────────┐   │
│  │              Commander Agent (실행/보고)           │   │
│  │                                                    │   │
│  │  - Step 6+7 출구/시그널 파라미터                    │   │
│  │  - Discord 리포트 작성                              │   │
│  │  - 유저 명령 처리                                   │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

### 4.2 각 에이전트 실행 주기 및 GPT 호출 비용

| 에이전트 | 실행 주기 | 1회 토큰 (예상) | 일일 호출 | 일일 비용 |
|---|---|---|---|---|
| Sentinel | 4시간 | ~2,000 tokens | 6회 | ~$0.03 |
| Analyst | 4시간 | ~3,000 tokens | 6회 | ~$0.05 |
| Strategist | 8시간 | ~4,000 tokens | 3회 | ~$0.04 |
| Guardian | 4시간 | ~2,500 tokens | 6회 | ~$0.04 |
| Commander | 이벤트 + 1일 1회 | ~3,000 tokens | ~5회 | ~$0.04 |
| Evolver | 4시간 | ~5,000 tokens | 6회 | ~$0.08 |
| **합계** | | | **~32회/일** | **~$0.28/일 ≈ $8.5/월** |

### 4.3 Evolver Agent — 핵심 에이전트 (MDD/PF 최적화)

```
SOUL.md (Evolver Agent)
========================

# Evolver — 트레이딩 시스템 진화자

## 정체성
너는 트레이딩 시스템의 파라미터 최적화를 담당하는 에이전트다.
너의 유일한 목표는 MDD를 최소화하면서 PF를 극대화하는 것이다.

## 핵심 지표 (우선순위 순)
1. MDD (Maximum Drawdown): 절대 10%를 초과해서는 안 된다
2. PF (Profit Factor): 최소 1.5 이상, 목표 2.0 이상
3. 승률: 참고만. MDD와 PF가 좋으면 승률은 중요하지 않다
4. 시그널 빈도: 너무 적으면(일 3개 미만) 기준이 과하게 높은 것

## 실행 주기
4시간마다 실행. 매 실행 시:

### Step 1: 성과 수집
- trades.db에서 최근 7일 거래 이력 로드
- equity_curve에서 현재 MDD 확인
- 롤링 PF (20거래) 계산

### Step 2: 진단
각 에이전트의 파라미터와 거래 결과를 교차 분석:
- 손실 거래의 공통 패턴은? (특정 체제, 특정 시간대, 특정 Type)
- MDD가 발생한 구간의 시장 특성은?
- PF가 높았던 구간의 파라미터 설정은?

### Step 3: 파라미터 조정 제안
- 한 번에 최대 3개 파라미터만 변경 (너무 많이 바꾸면 원인 추적 불가)
- 각 변경의 예상 효과를 MDD/PF 관점에서 설명
- 변경 폭은 현재 값의 ±20% 이내 (급격한 변경 금지)

### Step 4: 적용
- params/ 디렉토리의 해당 JSON 파일 업데이트
- 변경 이력을 params/history.jsonl에 기록
- Discord #evolution 채널에 변경 사항 보고

## 판단 규칙

### MDD가 5% 이상일 때 (방어 최우선)
- 모든 레버리지 관련 파라미터를 10~30% 하향
- 합격 기준(pass_score)을 5~10점 상향
- 손절 ATR 배수를 10~20% 타이트하게
- "수익보다 생존"을 최우선

### PF가 1.5 미만일 때 (효율 개선)
- 합격 기준 상향 (약한 시그널 필터링)
- 익절 비율 조정 (TP1 비중 증가 → 확실한 수익 우선)
- 손절 거래의 공통점 분석 → 해당 패턴 가중치 하향

### MDD < 3% 이고 PF > 2.0일 때 (성장 모드)
- 합격 기준 5점 하향 (더 많은 기회)
- 레버리지 10% 상향 가능
- TP3 비중 증가 (큰 수익 기회 확대)

### 데이터 부족 시 (거래 20개 미만)
- 파라미터 변경하지 않음
- "관찰 모드" 유지
- 기본값(default) 사용

## 금지 사항
- 레버리지를 절대 상한(absolute_max) 이상으로 올리지 마
- MDD target(10%)을 변경하지 마 (이것은 유저가 정하는 값)
- 한 번에 3개 초과 파라미터를 변경하지 마
- 이전 변경의 효과가 확인되기 전에 같은 파라미터를 또 변경하지 마
  (최소 20거래 또는 48시간 경과 후에만 동일 파라미터 재조정)
```

### 4.4 Commander Agent — 실행 + 보고

```
SOUL.md (Commander Agent)
==========================

# Commander — 실행 및 보고 담당

## 정체성
너는 트레이딩 시스템의 실행과 보고를 담당하는 에이전트다.
Step 6+7 파라미터를 관리하고, 유저에게 결과를 보고한다.

## 실행 역할

### 매일 00:00 UTC: 일일 리포트
trades.db에서 오늘 거래를 분석하여 Discord #daily-report에 게시:
- 총 PnL (금액 + %)
- 승률, 평균 RR, 거래 수
- 현재 MDD, 롤링 PF
- 최고/최악 거래 분석
- 내일 주의사항 (특이 펀딩비, OI 변화 등)

### 매 4시간: 포지션 요약
Discord #dashboard에 현재 상태 업데이트:
- 활성 포지션 목록
- 포트폴리오 가치
- MDD 상태 (normal/caution/defensive/survival)

### 유저 명령 처리 (#commands)
- "상태" → 전체 시스템 상태 요약
- "포지션" → 현재 포지션 상세
- "수익" → PnL 요약
- "중지" → 전체 거래 중단
- "{심볼} 차단" → 특정 심볼 진입 차단
- "레버리지 {N}배" → 글로벌 레버리지 상한 변경
- "MDD {N}%" → MDD 목표 변경

## 리포트 형식

간결하게. 핵심 숫자만.

### 일일 리포트 예시:
---
2026-02-18 Daily Report

PnL: +$342.50 (+3.4%)
Trades: 8 (Win 5 / Loss 3)
Win Rate: 62.5% | Avg RR: 2.1
MDD: 2.8% (Normal) | PF(20): 2.34

Best: SOL LONG +$156 (BREAKOUT)
Worst: ETH SHORT -$89 (SR_BOUNCE)

Evolver Changes Today:
- guardian.pass_score SIDEWAYS: 75 → 78
- commander.trailing_atr: 1.2 → 1.15

Note: BTC funding -0.012% (숏 과다, 롱 유리)
---
```

### 4.5 나머지 에이전트 SOUL.md 핵심

**Sentinel Agent (Step 0 파라미터 관리):**
```
- 4시간마다 실행
- 최근 7일 시장 통계를 분석하여 극단 감지 임계값 갱신
- sigma, percentile 값 조정 (절대값이 아니라 분포 기반)
- 최근 false positive/negative 비율 분석
  → false positive 많으면: 임계값 완화
  → false negative 많으면: 임계값 강화
- sentinel.json 업데이트 후 Discord #system-logs에 기록
```

**Analyst Agent (Step 1+2 파라미터 관리):**
```
- 4시간마다 실행
- 최근 거래에서 체제 분류가 정확했는지 후향 분석
  (분류 시점 체제 vs 실제 이후 가격 움직임)
- 정확했던 분류의 DNA 가중치 패턴 학습
- MTF 가중치도 최근 정확도 기반 조정
- analyst.json 업데이트
```

**Strategist Agent (Step 3+4 파라미터 관리):**
```
- 8시간마다 실행 (변곡점 데이터 축적에 시간 필요)
- S/R 레벨의 실제 반응률을 역추적
  → 반응률 높았던 레벨의 특성 분석 → 가중치 조정
- 변곡점 Type별 성공률 분석
  → 최근 수익낸 Type의 가중치 상향
  → 최근 손실낸 Type의 가중치 하향
- strategist.json 업데이트
```

**Guardian Agent (Step 5 파라미터 관리):**
```
- 4시간마다 실행
- MDD 상태에 따른 정책 적절성 검증
- 합격했지만 손실로 끝난 시그널 분석 → 어떤 검증이 부족했는지
- 차단했지만 수익이었을 시그널 분석 → 어떤 검증이 과했는지
- guardian.json 업데이트 (특히 validation_weights)
```

### 4.6 OpenClaw 설정

```json5
// ~/.openclaw/openclaw.json
{
  auth: {
    openai: { type: "oauth" }
  },

  agents: {
    list: [
      {
        id: "sentinel",
        workspace: "~/.openclaw/workspace-trading/sentinel",
        agentDir: "~/.openclaw/agents/sentinel/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec"] }
      },
      {
        id: "analyst",
        workspace: "~/.openclaw/workspace-trading/analyst",
        agentDir: "~/.openclaw/agents/analyst/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec"] }
      },
      {
        id: "strategist",
        workspace: "~/.openclaw/workspace-trading/strategist",
        agentDir: "~/.openclaw/agents/strategist/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec"] }
      },
      {
        id: "guardian",
        workspace: "~/.openclaw/workspace-trading/guardian",
        agentDir: "~/.openclaw/agents/guardian/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec"] }
      },
      {
        id: "commander",
        workspace: "~/.openclaw/workspace-trading/commander",
        agentDir: "~/.openclaw/agents/commander/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec", "write"] }
      },
      {
        id: "evolver",
        workspace: "~/.openclaw/workspace-trading/evolver",
        agentDir: "~/.openclaw/agents/evolver/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec", "write"] }
      }
    ]
  },

  tools: {
    agentToAgent: {
      enabled: true,
      allow: ["sentinel", "analyst", "strategist", "guardian", "commander", "evolver"]
    }
  },

  channels: {
    discord: {
      token: "BOT_TOKEN",
      guilds: {
        "GUILD_ID": {
          requireMention: false,
          channels: {
            "dashboard":      { allow: true },
            "live-trades":    { allow: true },
            "daily-report":   { allow: true },
            "evolution":      { allow: true },
            "alerts":         { allow: true },
            "commands":       { allow: true },
            "system-logs":    { allow: true }
          }
        }
      }
    }
  },

  bindings: [
    { agentId: "commander",  match: { channel: "discord", peer: { kind: "channel", id: "DASHBOARD_CH" } } },
    { agentId: "commander",  match: { channel: "discord", peer: { kind: "channel", id: "COMMANDS_CH" } } },
    { agentId: "commander",  match: { channel: "discord", peer: { kind: "channel", id: "DAILY_CH" } } },
    { agentId: "evolver",    match: { channel: "discord", peer: { kind: "channel", id: "EVOLUTION_CH" } } },
    { agentId: "sentinel",   match: { channel: "discord", peer: { kind: "channel", id: "ALERTS_CH" } } }
  ]
}
```

### 4.7 Discord 서버 구조

```
Trading Bot Server
│
├── LIVE
│   ├── #dashboard        ← 포트폴리오 상태 (4시간마다)
│   ├── #live-trades      ← 실시간 체결 알림
│   └── #alerts           ← 긴급 알림 (MDD 경고, 연속 손실, Stage 변경)
│
├── ANALYSIS
│   ├── #daily-report     ← 일일 리포트 (매일 00:00)
│   └── #evolution        ← Evolver 파라미터 변경 기록
│
├── CONTROL
│   ├── #commands         ← 유저 명령 입력
│   └── #system-logs      ← 시스템 로그 (에러, 에이전트 실행 기록)
```

---

## 5. Evolution Engine: 자동 파라미터 진화

### 5.1 개요

Evolution Engine은 GPT를 사용하지 않는 순수 수학적 최적화 엔진입니다. Evolver Agent가 "방향"을 판단하면, Evolution Engine이 "최적값"을 계산합니다.

```
Evolver Agent (GPT):  "S/R 가중치에서 touch_count를 높이고 recency를 낮춰야 할 것 같다"
Evolution Engine:      "touch_count: 0.45→0.52, recency: 0.25→0.18이 최적이다" (수학적 탐색)
```

### 5.2 MDD/PF 적합도 함수

```python
def fitness(params: dict, trade_history: list[Trade]) -> float:
    """파라미터 세트의 적합도를 MDD/PF로 평가"""

    # 거래 이력으로 equity curve 재구성
    equity_curve = simulate_equity(trade_history)

    # MDD 계산
    mdd = calculate_max_drawdown(equity_curve)

    # PF 계산
    gross_profit = sum(t.pnl for t in trade_history if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trade_history if t.pnl < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else 999

    # 거래 횟수 패널티 (너무 적으면 통계적 의미 없음)
    trade_count = len(trade_history)
    frequency_penalty = max(0, 1.0 - (trade_count / 20))  # 20거래 미만이면 패널티

    # ============================================
    # 적합도 = PF × (1 - MDD 패널티) × (1 - 빈도 패널티)
    # ============================================
    # MDD 패널티: MDD가 목표(10%) 초과 시 급격히 감점
    if mdd > 0.10:
        mdd_penalty = 1.0  # 불합격 (fitness = 0)
    elif mdd > 0.05:
        mdd_penalty = (mdd - 0.05) / 0.05 * 0.5  # 5~10%: 선형 감점
    else:
        mdd_penalty = 0.0  # 5% 이하: 페널티 없음

    fitness_score = pf * (1.0 - mdd_penalty) * (1.0 - frequency_penalty)

    return fitness_score
```

### 5.3 파라미터 진화 프로세스

```python
class EvolutionEngine:
    """MDD/PF 기반 파라미터 자동 진화"""

    def evolve(self, current_params: dict, trade_history: list[Trade]):
        """
        4시간마다 Evolver Agent에 의해 호출.
        현재 파라미터 주변을 탐색하여 더 나은 조합을 찾음.
        """

        current_fitness = self.fitness(current_params, trade_history)

        # 현재 파라미터 주변에서 변이(mutation) 생성
        candidates = []
        for _ in range(20):  # 20개 후보
            mutated = self._mutate(current_params, max_change_pct=0.15)
            # 후보의 적합도를 과거 거래 데이터로 시뮬레이션
            f = self.fitness(mutated, trade_history)
            candidates.append((mutated, f))

        # 최고 적합도 후보 선택
        best_candidate, best_fitness = max(candidates, key=lambda x: x[1])

        # 현재보다 5% 이상 개선된 경우에만 적용
        if best_fitness > current_fitness * 1.05:
            return {
                "apply": True,
                "new_params": best_candidate,
                "improvement": (best_fitness - current_fitness) / current_fitness,
                "new_fitness": best_fitness,
                "old_fitness": current_fitness
            }
        else:
            return {"apply": False, "reason": "no_significant_improvement"}

    def _mutate(self, params: dict, max_change_pct: float) -> dict:
        """파라미터를 ±max_change_pct 범위 내에서 무작위 변이"""
        mutated = copy.deepcopy(params)

        # Evolver Agent가 지정한 파라미터만 변이 (최대 3개)
        for key in self.target_params:
            current_val = get_nested(mutated, key)
            change = current_val * random.uniform(-max_change_pct, max_change_pct)
            new_val = current_val + change

            # 범위 제한 적용
            new_val = max(self.bounds[key][0], min(self.bounds[key][1], new_val))
            set_nested(mutated, key, new_val)

        return mutated
```

### 5.4 진화 안전장치

```python
EVOLUTION_SAFETY = {
    # 한 번에 변경 가능한 파라미터 수
    "max_params_per_cycle": 3,

    # 파라미터 변경 최대 폭 (현재 값 대비)
    "max_change_pct": 0.20,  # ±20%

    # 변경 후 최소 관찰 기간
    "min_observation_trades": 20,
    "min_observation_hours": 48,

    # 연속 악화 시 롤백
    "rollback_after_consecutive_degradation": 3,

    # 변경 이력 보관 (롤백용)
    "history_retention_days": 30,

    # 절대 변경 불가 파라미터
    "immutable": [
        "guardian.mdd_control.target_max_mdd",     # MDD 목표 (유저 결정)
        "guardian.mdd_control.emergency_mdd",       # 긴급 MDD (유저 결정)
        "commander.leverage.absolute_max",           # 레버리지 절대 상한
        "commander.stop_loss.max_loss_pct"           # 단일 거래 최대 손실
    ]
}
```

---

## 6. 전체 데이터 흐름 (1 시그널 생명주기)

```
[Hyperliquid WS] 1분봉 클로즈 수신
    │
    ▼  (~1ms)
[Data Collector] SQLite 저장 + Fast Engine 트리거
    │
    ▼  (~0.5ms)
[Params Load] params/*.json 읽기 (메모리 캐시, 변경 시에만 리로드)
    │
    ▼  (120ms)
[Step 0: Sentinel] 동적 임계값으로 극단 체크
    │ 통계 기반 sigma/percentile → 시장에 자동 적응
    │ MDD caution 상태면 Stage 유지 시간 2배
    │
    ▼  (200ms)
[Step 1: Analyst] 동적 DNA 가중치로 체제 분류
    │ 심볼별 최적 가중치, 분포 기반 경계값
    │
    ▼  (16ms)
[Step 2: Transition] 적응형 히스테리시스/블렌딩
    │
    ▼  (55ms)
[Step 3: Strategist] 동적 S/R/추세선/VP
    │ ATR 기반 모든 거리/임계값 자동 스케일
    │
    ▼  (85ms)
[Step 4: Inflection] 최적화된 Type 가중치로 스코어링
    │ PF 기반 합격 기준 동적 조정
    │
    ▼  (40ms)
[Step 5: Guardian] MDD 정책 최우선 적용
    │ MDD 구간별: normal → caution → defensive → survival → emergency
    │ PF 기반 합격 기준 추가 조정
    │ 포트폴리오 리스크 한도 체크
    │
    ▼  (38ms)
[Step 6: Exit] MDD 기반 SL 타이트닝, 동적 TP 분할
    │
    ▼  (45ms)
[Step 7: Signal] MDD 기반 레버리지 조정, 포지션 사이징
    │
    ▼  (~500ms)
[Hyperliquid API] 주문 실행
    │
    ▼  (~1ms)
[SQLite] 거래 기록 저장
    │
    ▼
[Discord #live-trades] 체결 알림
```

**총 소요: ~650ms (기존과 동일) + 주문 실행 ~500ms**

---

## 7. MDD/PF 모니터링 체계

### 7.1 실시간 MDD 추적

```python
class MddTracker:
    """실시간 MDD 추적 및 정책 적용"""

    def __init__(self, target_mdd: float = 0.10):
        self.target_mdd = target_mdd
        self.peak_equity = 0
        self.current_equity = 0

    def update(self, equity: float):
        self.current_equity = equity
        self.peak_equity = max(self.peak_equity, equity)

        # MDD 계산
        self.current_mdd = (self.peak_equity - equity) / self.peak_equity

        # equity_curve 테이블에 기록
        self._save_to_db(equity, self.current_mdd, self.peak_equity)

        # MDD 정책 결정
        return self.get_policy()

    def get_policy(self) -> str:
        if self.current_mdd >= 0.10: return "emergency"
        if self.current_mdd >= 0.08: return "survival"
        if self.current_mdd >= 0.05: return "defensive"
        if self.current_mdd >= 0.03: return "caution"
        return "normal"
```

### 7.2 롤링 PF 추적

```python
class PfTracker:
    """롤링 Profit Factor 추적"""

    def __init__(self, window: int = 20):
        self.window = window

    def get_rolling_pf(self) -> float:
        """최근 N거래의 PF 계산"""
        recent = self._get_recent_trades(self.window)
        if not recent:
            return 0.0

        gross_profit = sum(t.pnl for t in recent if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in recent if t.pnl < 0))

        if gross_loss == 0:
            return 999.0 if gross_profit > 0 else 0.0

        return gross_profit / gross_loss
```

### 7.3 Discord 알림 조건

```python
ALERT_CONDITIONS = {
    # MDD 관련
    "mdd_caution":    {"mdd": 0.03, "message": "MDD 3% 도달 — Caution 모드 진입"},
    "mdd_defensive":  {"mdd": 0.05, "message": "MDD 5% 도달 — Defensive 모드 (레버리지 60% 감소)"},
    "mdd_survival":   {"mdd": 0.08, "message": "MDD 8% 도달 — Survival 모드 (최대 1포지션)"},
    "mdd_emergency":  {"mdd": 0.10, "message": "MDD 10% 도달 — 전 포지션 청산 + 24시간 중단"},

    # PF 관련
    "pf_warning":     {"pf_below": 1.3, "message": "PF 1.3 미만 — 검증 기준 강화됨"},
    "pf_critical":    {"pf_below": 1.0, "message": "PF 1.0 미만 (손실 구간) — 레버리지 대폭 감소"},

    # 연속 손실
    "loss_streak_3":  {"streak": 3, "message": "3연패 — 레버리지 40% 감소"},
    "loss_streak_5":  {"streak": 5, "message": "5연패 — 8시간 거래 중단"},

    # 시스템
    "step0_active":   {"message": "극단 시장 감지 — 거래 차단 중"},
    "param_changed":  {"message": "Evolver가 파라미터 변경함 — #evolution 확인"},
}
```

---

## 8. v2.x → v4.0 비교 요약

| 영역 | 기존 v2.x (하드코딩) | v4.0 (동적) |
|---|---|---|
| **데이터** | Binance + OKX + Bybit | **Hyperliquid만** |
| **인프라** | Redis + InfluxDB | **SQLite** (파일 1개) |
| **월 비용** | $100~200 | **$15~40** |
| | | |
| **극단 임계값** | 3%, 3배, 2.5배... 고정 | **sigma/percentile 기반, 시장 통계에서 자동 도출** |
| **DNA 가중치** | 0.40/0.30/0.30 고정 | **심볼별, 성과 기반 동적 조정** |
| **체제 경계** | Hurst > 0.6 고정 | **분포 percentile 기반 동적** |
| **히스테리시스** | 규칙 기반 1~7캔들 | **최근 전환 성공률 기반 적응** |
| **S/R 가중치** | 0.50/0.25/0.25 고정 | **Evolver가 MDD/PF 기반 최적화** |
| **변곡점 가중치** | 42개 수동 설정 | **Evolver가 자동 진화** |
| **합격 기준** | 체제별 고정 점수 | **MDD 구간 + PF에 따라 실시간 조정** |
| **레버리지** | 체제×확신도 테이블 | **+ MDD 정책 기반 동적 감축** |
| **손절** | 체제별 ATR 배수 고정 | **+ MDD 기반 타이트닝** |
| **익절 분할** | 40/35/25 고정 | **Evolver가 PF 기반 최적화** |
| | | |
| **MDD 관리** | 최대 손실 5% 고정 | **5단계 정책 (normal→emergency), 전 Step 연동** |
| **PF 관리** | 없음 | **롤링 PF 기반 동적 기준 조정** |
| **자기 학습** | 없음 | **Evolver + Evolution Engine 4시간 주기** |
| **에이전트** | 없음 | **6개 OpenClaw 에이전트** |
| **보고** | 로그 파일 | **Discord 실시간** |

---

## 9. 구현 로드맵

### Phase 0: 기반 (1~2주)
```
├ Hyperliquid 테스트넷 설정
├ Data Collector 구현 (WebSocket → SQLite)
├ 기존 Step 0~7을 FastEngine 클래스로 래핑
├ params/*.json 구조 설계 + 기본값 생성
└ 기존 하드코딩 → params.json 참조로 전환
```

### Phase 1: 동적 파라미터 (2주)
```
├ 통계 기반 동적 임계값 (Step 0, 1, 3)
├ MDD 5단계 정책 구현 (Step 5, 6, 7)
├ PF 기반 합격 기준 동적 조정
├ MddTracker + PfTracker 구현
└ 백테스트로 기존 대비 MDD/PF 비교
```

### Phase 2: OpenClaw 에이전트 (1~2주)
```
├ OpenClaw 설치 + GPT OAuth 연결
├ Discord 서버 + 봇 설정
├ 6개 에이전트 SOUL.md 작성
├ Commander: 리포트 + 명령 처리
├ Evolver: 파라미터 진화 로직
└ Paper Trading 시작 (Hyperliquid 테스트넷)
```

### Phase 3: Evolution Engine (1~2주)
```
├ fitness 함수 구현 (MDD/PF 기반)
├ 파라미터 변이 + 시뮬레이션 로직
├ 안전장치 (변경 폭 제한, 롤백, 이력)
├ Evolver ↔ Evolution Engine 연동
└ Paper Trading 2주 + 성과 분석
```

### Phase 4: 실전 (2주+)
```
├ 소액 실전 ($100~500)
├ MDD/PF 실측 + 파라미터 안정화
├ 에이전트 판단 정확도 추적
└ 점진적 자본 확대
```

**총 예상: 6~8주 (백테스트 포함)**
