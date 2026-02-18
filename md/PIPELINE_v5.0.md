# Trading Pipeline v5.0
# 4-Agent Autonomous Trading System
# Hyperliquid Native | OpenClaw Powered | MDD/PF Optimized
# 2026-02-18

---

## 0. 설계 철학

기존 Step 0~7의 8단계 파이프라인을 **5-Phase 파이프라인**으로 재설계한다.
4개의 독립 에이전트가 각각 이 파이프라인을 자기 타임프레임 세트로 실행하며,
각자 독립적으로 Hyperliquid에서 롱/숏 자동매매를 수행한다.

### 핵심 원칙

1. **4 Agents, 4 Timeframe Sets, 4 Independent Traders**
2. **MDD First**: 모든 판단의 1순위는 드로다운 제한
3. **Zero Hardcode**: 모든 임계값은 시장 통계에서 동적 도출
4. **5 Phases**: Safety → Read → Scan → Gate → Execute
5. **Hyperliquid Native**: 단일 거래소, 거래소 수수료 극저 (실전 비용은 슬리피지/펀딩이 지배)

### 기존 Step 0~7 → v5.0 매핑

```
기존 8 Steps                   v5.0 5 Phases
─────────────────              ─────────────────
Step 0 (극단 감시)         →   Phase 1: SAFETY
Step 1 (체제 분류)         ┐
Step 2 (전환 핸들링)       ┘→  Phase 2: READ
Step 3 (차트 구조)         ┐
Step 4 (변곡점 감지)       ┘→  Phase 3: SCAN
Step 5 (상태 검증)         →   Phase 4: GATE
Step 6 (출구 전략)         ┐
Step 7 (시그널/레버리지)   ┘→  Phase 5: EXECUTE
```

---

## 1. 전체 시스템 아키텍처

```
                      Hyperliquid WebSocket
                     (OHLCV, OrderBook, Funding, OI, Liquidations)
                              │
                     ┌────────▼────────┐
                     │  Data Collector  │
                     │  → SQLite 저장   │
                     │  → 캔들 합성     │
                     │  (5m,15m,1h,4h) │
                     └───────┬─────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                    │
          ▼                  ▼                    ▼
  ┌──────────────────────────────────────┐  ┌──────────┐
  │         4 Trading Agents              │  │ Meta     │
  │                                       │  │ Agents   │
  │  ┌────────┐ ┌────────┐ ┌────────┐   │  │          │
  │  │  S1    │ │  S2    │ │  S3    │   │  │ Guardian │
  │  │  5m    │ │ 5m+15m │ │5+15+1h│   │  │ (리스크) │
  │  │        │ │        │ │       │   │  │          │
  │  │ 5-Phase│ │ 5-Phase│ │5-Phase│   │  │ Evolver  │
  │  │Pipeline│ │Pipeline│ │Pipeline│   │  │ (최적화) │
  │  └───┬────┘ └───┬────┘ └───┬────┘   │  │          │
  │      │          │          │         │  │ Reporter │
  │  ┌───┴────┐     │          │         │  │ (보고)   │
  │  │  S4    │     │          │         │  │          │
  │  │5+15+   │     │          │         │  └─────┬────┘
  │  │1h+4h   │     │          │         │        │
  │  │5-Phase │     │          │         │        │
  │  └───┬────┘     │          │         │        │
  └──────┼──────────┼──────────┼─────────┘        │
         │          │          │                   │
         ▼          ▼          ▼                   ▼
  ┌─────────────────────────────────────────────────────┐
  │              Hyperliquid API (주문 실행)              │
  │              Telegram (알림/명령)                      │
  └─────────────────────────────────────────────────────┘
```

### 4개 트레이딩 에이전트

| Agent | 타임프레임 | 성격 | 자본 배분 | 시그널 빈도 |
|-------|-----------|------|----------|-----------|
| **S1** | 5m | 단타 스캘퍼 | 15% | 10~25/일 |
| **S2** | 5m + 15m | 단기 트레이더 | 25% | 5~15/일 |
| **S3** | 5m + 15m + 1h | 스윙 트레이더 | 30% | 2~8/일 |
| **S4** | 5m + 15m + 1h + 4h | 포지션 트레이더 | 30% | 0~3/일 |

### S1 조건부 운용 정책 (피드백 #2)

S1(단일 5m)은 MTF 정렬이 없고, confidence에 15% 할인이 적용되며,
단일 타임프레임 스캘핑은 크립토 노이즈에 취약하다.

```
정책: Paper Trading에서 S1을 가장 먼저, 가장 엄격하게 검증한다.

검증 기준 (Paper Trading 2주, 최소 50거래):
  PF ≥ 1.5  AND  MDD ≤ 3%  AND  승률 ≥ 45%
  → 통과 시: S1 실전 투입 (자본 15%)
  → 미달 시: S1 자본을 S2(+7.5%)/S3(+7.5%)에 재배분
             S1은 Paper 모드로 계속 데이터 수집

실전 투입 후에도:
  매주 PF < 1.0이면 → 자본 절반으로 축소 (15% → 7.5%)
  2주 연속 PF < 1.0 → S1 중단, 자본 S2/S3에 재배분
  Guardian이 S1의 PF를 특별 모니터링
```

### 1.5 심볼 선택 모듈 (피드백 #3)

에이전트가 어떤 심볼을 거래할지 정의한다.
Phase 1 SAFETY 이전에 실행되는 **Phase 0: SYMBOL SELECT**에 해당.

```python
# ═══ 심볼 풀: 전 에이전트 공유 ═══
SYMBOL_POOL = {
    "tier_1": ["BTC", "ETH"],          # 항상 거래. 유동성 최고.
    "tier_2": ["SOL", "XRP"],          # 기본 거래. 유동성 양호.
    "tier_3": ["DOGE", "AVAX", "LINK"],# 선택적. Evolver가 활성/비활성 결정.
}

# ═══ 에이전트별 심볼 정책 ═══
AGENT_SYMBOL_POLICY = {
    "s1": {
        # 스캘퍼: 유동성 최고인 심볼만 (슬리피지 최소화)
        "allowed_tiers": ["tier_1"],
        "max_symbols": 2,
        "reason": "5m 스캘핑은 슬리피지에 민감. BTC/ETH만.",
    },
    "s2": {
        "allowed_tiers": ["tier_1", "tier_2"],
        "max_symbols": 4,
        "reason": "단기 트레이딩. 주요 4개 심볼.",
    },
    "s3": {
        "allowed_tiers": ["tier_1", "tier_2", "tier_3"],
        "max_symbols": 6,
        "reason": "스윙. 보유 시간이 길어서 슬리피지 영향 작음.",
    },
    "s4": {
        "allowed_tiers": ["tier_1", "tier_2"],
        "max_symbols": 4,
        "reason": "포지션. 유동성 있는 것만. 보유 기간 길어서 tier_3 위험.",
    },
}

# ═══ 동적 심볼 필터 (매 4시간, Evolver가 실행) ═══
def filter_active_symbols(symbol_pool, agent_policy, market_data):
    """
    거래할 심볼을 동적으로 선택.
    유동성, 변동성, 최근 성과를 기반으로.
    """
    candidates = []
    for tier in agent_policy['allowed_tiers']:
        candidates.extend(symbol_pool[tier])

    scored = []
    for sym in candidates:
        score = 0
        data = market_data[sym]

        # 유동성 점수: 24h 볼륨 기반
        score += min(data.volume_24h / 1_000_000, 30)   # 최대 30점

        # 스프레드 점수: 낮을수록 좋음
        score += max(0, 20 - data.avg_spread * 10000)    # 최대 20점

        # ATR/가격 비율: 적당한 변동성
        atr_pct = data.atr / data.price
        if 0.005 < atr_pct < 0.03:   # 0.5~3% 변동성
            score += 20
        elif atr_pct <= 0.005:        # 변동성 부족
            score += 5
        else:                          # 변동성 과다
            score += 10

        # [피드백] ATR/가격 비율도 분포 기반으로 (Zero Hardcode 일관성)
        # → 7일 분포에서 "적당한 변동성" 범위를 자동 산출
        # atr_pct_range = [stats.atr_pct_p20, stats.atr_pct_p80]
        # if atr_pct_range[0] < atr_pct < atr_pct_range[1]: score += 20

        # 최근 PF (해당 에이전트의 해당 심볼 성과)
        # [피드백] PF 가중치를 30% 이하로 제한 (자기강화 루프 방지)
        # 시장 품질(volume/spread/atr) = 70%, 최근 PF = 30% 이하
        recent_pf = get_symbol_pf(agent_id, sym, days=7)
        if recent_pf and recent_pf > 1.5:
            score += 10     # 최대 10점 (전체 100점 중 ~10%)
        elif recent_pf and recent_pf < 0.8:
            score -= 5      # 감점도 약하게

        scored.append((sym, score))

    # 상위 N개 선택
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = [s[0] for s in scored[:agent_policy['max_symbols']]]

    # [피드백] 쿨다운/재평가: PF<0.8로 제외된 심볼도 24시간마다 1번 소량 탐색
    # → "다시 회복될 기회"를 보장
    excluded = [s[0] for s in scored if s[0] not in selected and s[1] > 0]
    if excluded and should_explore(agent_id):  # 24시간마다 1회
        selected[-1] = excluded[0]  # 마지막 슬롯을 탐색용으로 교체

    return selected
```

```
5분봉 클로즈 시 각 에이전트의 파이프라인 실행 흐름:

Phase 0: SYMBOL SELECT (매 4시간 갱신, 캐시 사용)
  ↓ 활성 심볼 목록
Phase 1~5: 각 심볼에 대해 파이프라인 실행
  ↓
결과: 에이전트당 여러 심볼에서 시그널 가능

예시:
  S3 활성 심볼: [BTC, ETH, SOL, XRP]
  5분봉 클로즈 → BTC 파이프라인 → 시그널 없음
                 ETH 파이프라인 → LONG 시그널 ✅
                 SOL 파이프라인 → SCAN 탈락
                 XRP 파이프라인 → GATE 탈락
```

---

## 2. Data Layer

### 2.1 Hyperliquid WebSocket 스트림

```python
STREAMS = {
    "candle_5m":     "5분봉 OHLCV",
    "candle_15m":    "15분봉 OHLCV (5분봉에서도 합성 가능)",
    "candle_1h":     "1시간봉 OHLCV",
    "candle_4h":     "4시간봉 OHLCV",
    "orderbook_l2":  "호가창 Top 20",
    "trades":        "체결 내역 (틱)",
    "funding":       "펀딩비 (1시간)",
    "liquidations":  "청산 이벤트 실시간",
    "user_state":    "내 포지션/잔고",
}
```

### 2.1.1 데이터 정합성 정의 (피드백 반영)

```
5m candle row에 넣는 보조 데이터의 집계 방식:

  필드                  원본 주기       5m row에 넣는 값            비고
  ──────────────       ──────────     ──────────────────        ──────────
  funding_rate          1시간          해당 5분 구간의 last      5분간 변화 없으면 동일값
  open_interest         실시간          5분 구간 close 시점 값    close snapshot
  liquidation_vol       tick성          5분 구간 합산 (sum)       구간 내 총 청산량
  bid_ask_spread        실시간          5분 구간 close 시점 값    close snapshot
  orderbook_imbalance   실시간          5분 구간 close 시점 값    close snapshot

향후 정규화 옵션 (초기엔 위 방식으로 시작):
  → 별도 테이블(funding, orderbook_snapshots 등)로 분리 가능
  → 파이프라인에서 "close 시점 기준 join"으로 전환
  → 백테스트 정밀도가 부족하면 그때 분리
```

### 2.2 SQLite 스키마

```sql
CREATE TABLE candles (
    symbol          TEXT,
    timeframe       TEXT,       -- '5m','15m','1h','4h'
    timestamp       INTEGER,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    funding_rate    REAL,
    open_interest   REAL,
    liquidation_vol REAL,
    bid_ask_spread  REAL,
    orderbook_imbalance REAL,
    PRIMARY KEY (symbol, timeframe, timestamp)
);

CREATE TABLE trades (
    id              INTEGER PRIMARY KEY,
    signal_id       TEXT UNIQUE,  -- 중복 실행 방지 (피드백 #7-2)
    agent_id        TEXT,         -- 's1','s2','s3','s4'
    symbol          TEXT,
    side            TEXT,
    entry_time      INTEGER,
    exit_time       INTEGER,
    entry_price     REAL,
    exit_price      REAL,
    leverage        REAL,
    notional_usd    REAL,         -- 포지션 노셔널 (피드백 #3-1)
    margin_usd      REAL,         -- 실제 증거금 (피드백 #3-1)
    sl_pct          REAL,         -- SL 거리 (open_risk 계산용)
    pnl_usd         REAL,
    pnl_pct         REAL,
    regime          TEXT,
    inflection_type TEXT,
    inflection_score REAL,
    validation_score REAL,
    pattern_confirmations TEXT,   -- JSON array
    exit_reason     TEXT,
    params_version  TEXT,         -- 사용된 파라미터 버전 태그
    phase_snapshot  TEXT          -- JSON: Phase별 디버깅 스냅샷
);

CREATE TABLE equity_curve (
    timestamp       INTEGER,
    agent_id        TEXT,
    equity          REAL,
    drawdown        REAL,
    peak_equity     REAL,
    PRIMARY KEY (timestamp, agent_id)
);

-- [피드백 반영] 포지션 테이블 (reconciliation + open_risk 추적)
CREATE TABLE positions (
    id              INTEGER PRIMARY KEY,
    agent_id        TEXT,
    symbol          TEXT,
    direction       TEXT,         -- 'LONG' or 'SHORT'
    entry_price     REAL,
    size_usd        REAL,
    sl_pct          REAL,
    entry_time      INTEGER,
    signal_id       TEXT UNIQUE,
    exchange_order_id TEXT,       -- Hyperliquid order ID (reconciliation용)
    status          TEXT DEFAULT 'OPEN'  -- 'OPEN','CLOSED'
);

-- [피드백 반영] 파이프라인 실행 로그 (Phase별 디버깅)
CREATE TABLE pipeline_logs (
    id              INTEGER PRIMARY KEY,
    timestamp       INTEGER,
    agent_id        TEXT,
    symbol          TEXT,
    phase_snapshot  TEXT,         -- JSON: Phase별 중간 결과
    signal_generated BOOLEAN DEFAULT 0
);
```

### 2.3 캔들 트리거 규칙

```
5분봉 클로즈  →  S1 파이프라인 실행
              →  S2 파이프라인 실행 (15분봉이 3개마다 갱신)
              →  S3 파이프라인 실행 (1시간봉이 12개마다 갱신)
              →  S4 파이프라인 실행 (4시간봉이 48개마다 갱신)

모든 에이전트는 5분봉 클로즈를 기본 트리거로 사용.
상위 타임프레임은 그 시점까지 축적된 데이터를 사용.
```

---

## 3. 5-Phase Pipeline (고도화된 통합 파이프라인)

각 에이전트가 동일한 파이프라인 로직을 실행하되,
타임프레임 세트와 파라미터만 다르다.

```
┌─────────────────────────────────────────────────┐
│            5-Phase Pipeline (per Agent)           │
│                                                   │
│  Phase 1    Phase 2    Phase 3    Phase 4   Phase 5│
│  SAFETY  →  READ   →  SCAN   →  GATE  →  EXECUTE│
│  ~50ms      ~100ms     ~120ms    ~40ms    ~40ms  │
│                                                   │
│  극단감지    체제분류    구조+변곡   MDD/PF    SL/TP    │
│  +MDD체크   +전환      점+패턴     리스크검증 +주문    │
│                        통합탐지                      │
│  총 ~370ms (기존 650ms에서 단축)                     │
│  + 비동기 AI 리뷰 (4시간 주기, 파이프라인에 영향 없음)│
└─────────────────────────────────────────────────┘
```

---

### Phase 간 데이터 계약 (Result Dataclasses)

각 Phase의 입·출력 필드를 명확히 고정한다.
**모든 Phase 결과는 "완전 반환"을 강제한다** — 조기 반환(early return) 시에도 모든 필드를 채운다.
Optional 필드는 None 허용을 명시하며, 그 외 필드는 반드시 유효한 값이 들어가야 한다.

```python
@dataclass
class SafetyResult:
    blocked: bool
    stage: str           # 'NORMAL', 'STAGE_3', 'STAGE_2', 'STAGE_1'
    severity: float      # 0~240
    mdd_mode: str        # 'normal','caution','defensive','survival','emergency'
    action: str          # 'ALLOW', 'BLOCK_NEW', 'REDUCE_LEV', 'CLOSE_ALL_AND_HALT'
    reason: Optional[str] = None   # 차단 사유 (예: 'MDD_EMERGENCY', 'STAGE_1')
    conditions: dict = field(default_factory=dict)
    volatility_override: Optional[float] = None  # ATR 급변 시 대체 변동성 값
    volatility_alert: bool = False

    # 검증: blocked=True이면 reason과 action이 반드시 존재해야 함
    def __post_init__(self):
        if self.blocked:
            assert self.reason is not None, "blocked=True인데 reason이 None"
            assert self.action in ('BLOCK_NEW', 'CLOSE_ALL_AND_HALT')

@dataclass
class RegimeResult:
    regime: str          # 'STRONG_UPTREND', 'WEAK_UPTREND', 'SIDEWAYS', ...
    confidence: float    # 0.0~1.0
    alignment: float = 1.0            # MTF 정렬도 0.0~1.0 (단일 TF면 1.0)
    tf_results: dict = field(default_factory=dict)
    in_transition: bool = False
    blend_progress: float = 0.0

    # 조기 반환 시에도 모든 필드를 채운다.
    # 예: STAGE_1 → RegimeResult(regime='WAIT', confidence=0.0, alignment=0.0,
    #                             tf_results={}, in_transition=False, blend_progress=0.0)

@dataclass
class PatternResult:
    candlestick: list = field(default_factory=list)
    chart: list = field(default_factory=list)
    synergy_bonus: float = 0.0       # cap 적용 후 값
    confirmation_names: list[str] = field(default_factory=list)
    # confirmation_names: 최종 정리된 문자열 리스트 (list[str]로 고정)
    # Phase 5에서 pattern_confirmations로 사용

@dataclass
class ScanResult:
    found: bool
    primary_type: str = ''             # 'T1_SR_REACTION', 'T2_...', ...
    secondary_type: Optional[str] = None
    score: float = 0.0
    mtf_grade: str = 'NONE'            # 'A','B','C','D','F','NONE'
    implied_direction: str = ''        # 'LONG' or 'SHORT'
    entry_price: float = 0.0
    context: dict = field(default_factory=dict)
    sr_levels: list = field(default_factory=list)
    trendlines: list = field(default_factory=list)
    vol_profile: dict = field(default_factory=dict)
    patterns: PatternResult = field(default_factory=PatternResult)
    atr: float = 0.0
    pattern_target_atr: Optional[float] = None

    # found=False 반환 시 예시:
    # ScanResult(found=False, atr=current_atr)
    # → 최소한 atr은 채워서 디버깅 가능하게

@dataclass
class GateResult:
    passed: bool
    reason: Optional[str] = None       # 탈락 사유
    score: float = 0.0                 # 0~100
    pass_threshold: float = 0.0
    mdd_mode: str = 'normal'
    leverage_mult: float = 1.0         # MDD_POLICIES['leverage_mult']
    size_mult: float = 1.0             # MDD_POLICIES['size_mult'] × exposure 축소
    rolling_pf: float = 0.0

@dataclass
class Signal:
    signal_id: str
    agent_id: str
    symbol: str
    direction: str             # 'LONG' or 'SHORT'
    entry_price: float
    stop_loss: float
    take_profits: list         # [{price, ratio, rr}, ...]
    leverage: float
    notional_usd: float        # 포지션 노셔널 (USD)
    margin_usd: float          # 실제 증거금 (USD)
    regime: str
    confidence: float
    inflection_type: str
    inflection_score: float
    validation_score: float
    mdd_mode: str
    pattern_confirmations: list[str]   # 항상 list[str] (PatternResult.confirmation_names 사용)
    timestamp: int
    # Phase별 디버깅 스냅샷 (각 거래의 판단 근거 추적용)
    phase_snapshot: dict = field(default_factory=dict)
    # 예: {"safety_severity": 12, "regime": "STRONG_UPTREND", "scan_score": 78,
    #       "gate_score": 72, "gate_threshold": 65, "stage": "NORMAL", ...}
```

---

### Phase 1: SAFETY (극단 감시 + MDD 게이트)

**기존 Step 0에서 가져온 것**: 5가지 극단 조건, Stage 시스템, 원인 추론
**고도화**: 동적 임계값 + MDD 선행 게이트 + Hyperliquid 네이티브 데이터 활용

```python
def phase1_safety(candle, orderbook, agent_state, params):
    """
    가장 먼저 실행. 시장이 비정상이거나 MDD가 위험하면 즉시 차단.
    ~50ms
    """

    # ━━━━ 1A: MDD 선행 게이트 (Step 0에 없던 것, v5.0 신규) ━━━━
    current_mdd = agent_state.current_mdd
    mdd_mode = get_mdd_mode(current_mdd, params['mdd_control'])

    if mdd_mode == 'emergency':     # MDD ≥ 10%
        return SafetyResult(
            blocked=True, stage='NORMAL', severity=0, mdd_mode='emergency',
            action='CLOSE_ALL_AND_HALT', reason='MDD_EMERGENCY')
    if mdd_mode == 'survival':      # MDD 8~10%
        # 기존 포지션은 유지하되 신규 진입 극도로 제한
        pass  # Phase 4에서 추가 필터링

    # ━━━━ 1A-2: ATR 급변 감지 (피드백 #7) ━━━━
    # ATR(14) = 70분 후행. 급격한 변동성 변화를 놓칠 수 있다.
    # 보조 지표: 최근 3봉 range vs ATR(14) 괴리 체크
    recent_range = max(c.high - c.low for c in candles[-3:])
    atr_14 = agent_state.atr_14
    volatility_divergence = recent_range / atr_14 if atr_14 > 0 else 1.0

    # [피드백] volatility_divergence 임계값도 동적화 (심볼/Stage별)
    vol_div_threshold = stats.volatility_divergence_p95  # 7일 분포 95th percentile
    if volatility_divergence >= vol_div_threshold:
        # 최근 3봉의 range가 ATR을 크게 초과
        # → ATR이 아직 반영 못한 급변 상황
        # → 보수적 처리: SL을 recent_range 기반으로 확대
        volatility_override = recent_range
        volatility_alert = True
    else:
        volatility_override = None
        volatility_alert = False
    # volatility_override는 SafetyResult에 저장 (agent_state가 아닌 결과 객체에)

    # ━━━━ 1B: 극단 시장 감지 (Step 0 고도화) ━━━━
    # 모든 임계값: 최근 7일 통계에서 자동 산출
    stats = agent_state.market_stats_7d

    conditions = {
        'price_spike': detect_price_spike(candle, stats, params),
        'spread_blow': detect_spread_blowout(candle, stats, params),
        'vol_surge':   detect_volatility_surge(candle, stats, params),
        'book_stress': detect_orderbook_stress(orderbook, stats, params),
        'volume_anomaly': detect_volume_anomaly(candle, stats, params),
        # [NEW] Hyperliquid 네이티브 조건
        'funding_extreme': detect_funding_extreme(candle, params),
        'oi_shock':        detect_oi_shock(candle, stats, params),
        'liquidation_cascade': detect_liquidation_cascade(candle, stats, params),
    }

    # ━━━━ 1C: 심각도 계산 (Step 0 비선형 스케일 유지) ━━━━
    severity = calculate_severity(conditions)
    # 공식: score = 30 × (1 - e^(-0.8 × (ratio-1))) per condition
    # 조건당 최대 30점 × 8개 조건 = 총점 0~240

    # ━━━━ 1D: Stage 판정 ━━━━
    if severity >= 80:  # 기존 60에서 상향 (조건 8개로 늘었으므로)
        stage = determine_stage(severity, agent_state.current_stage)
    else:
        stage = try_recover_stage(agent_state, conditions, params)

    blocked = stage in ['STAGE_1', 'STAGE_2']
    if blocked:
        action = 'BLOCK_NEW'
        reason = f'STAGE_{stage}'
    elif stage == 'STAGE_3':
        action = 'REDUCE_LEV'
        reason = None
    else:
        action = 'ALLOW'
        reason = None

    return SafetyResult(
        blocked=blocked,
        stage=stage,
        severity=severity,
        mdd_mode=mdd_mode,
        action=action,
        reason=reason,
        conditions=conditions,
        volatility_override=volatility_override,
        volatility_alert=volatility_alert,
    )
```

#### 1B 상세: 동적 임계값 (8가지 조건)

기존 Step 0의 5가지 + Hyperliquid 네이티브 3가지 = **8가지 조건**

```python
# 모든 임계값은 최근 7일 분포에서 자동 산출
SAFETY_PARAMS = {
    # ── 기존 5가지 (Step 0에서 계승, 절대값→상대값 변환) ──

    # 기존: price_change > 3% (고정)
    # v5.0: 최근 7일 가격변화 분포의 N-sigma
    "price_spike_sigma": 3.5,

    # 기존: spread > median_24h × 3.0 (고정)
    # v5.0: 분포의 percentile
    "spread_percentile": 97,

    # 기존: ATR_1h > ATR_24h × 2.5 (고정)
    # v5.0: ATR 비율 분포의 percentile
    "volatility_percentile": 95,

    # 기존: imbalance > 0.8 (고정)
    # v5.0: 분포 기반
    "orderbook_stress_percentile": 95,

    # 기존: volume > avg × 3.0 또는 < avg × 0.3 (고정)
    # v5.0: 분포 기반
    "volume_surge_percentile": 95,
    "volume_drought_percentile": 5,

    # ── Hyperliquid 네이티브 3가지 (v5.0 신규) ──

    # 펀딩비 극단: 분포 기반
    "funding_extreme_percentile": 97,

    # OI 급변: 5분간 OI 변화율 분포 기반
    "oi_shock_percentile": 97,

    # 청산 연쇄: 5분간 청산량 분포 기반
    "liquidation_cascade_percentile": 95,

    # 통계 갱신 주기
    "stats_recalc_hours": 4,

    # [피드백] TF별 lookback 분리 (4h는 7일이면 42개뿐 → 통계 흔들림)
    "stats_lookback": {
        "5m":  7,      # 7일 = 2016개 (충분)
        "15m": 14,     # 14일 = 1344개
        "1h":  30,     # 30일 = 720개
        "4h":  90,     # 90일 = 540개 (최소 500 확보)
    },
    # 또는 캔들 수 기준: 각 TF에서 최소 500샘플 확보
    "min_samples_per_tf": 500,
}
```

#### 1D 상세: Stage 시스템 (Step 0 계승 + MDD 연동)

```
Stage enum (4단계, 심각도 순):

  STAGE_1  (완전 차단)   severity ≥ 160   → action=BLOCK_NEW, 신규 진입 불가
                                          → 기존 포지션: SL/TP 유지, 추가 조정 없음
                                          → MDD emergency와 겹치면 emergency 우선
  STAGE_2  (차단)        severity 120~159  → action=BLOCK_NEW, 신규 진입 불가
                                          → 기존 포지션: SL/TP 유지
  STAGE_3  (조건부 허용) severity 80~119   → action=REDUCE_LEV, 레버리지 max 3x
                                          → 기존 포지션: SL 타이트닝 (×0.8)
  NORMAL   (정상)        severity < 80     → action=ALLOW, 제한 없음

Stage 회복 전환 (순차적으로만 전환 가능):

  STAGE_1 → STAGE_2 → STAGE_3 → NORMAL
  최소 5분   최소 15분   최소 30분

전환 조건: 기존 Step 0의 회복 비율 체크를 유지하되,
임계값을 모두 percentile 기반으로 변환.

[NEW] MDD 연동:
  MDD caution(3%+) 상태에서 Stage 발생 시
  → 최소 유지 시간 2배 (Stage 1: 10분, Stage 2: 30분, ...)
  → 회복 조건 10% 강화

[NEW] Stage 3 조건부 진입:
  severity ≤ 119 AND validation_score ≥ 85 AND inflection_score ≥ 80
  → 레버리지 max 3x로 제한하여 진입 가능

[NOTE] 레버리지 계산(Phase 5)에서의 Stage 제한:
  STAGE_3 → max leverage 3x
  STAGE_2 → 진입 차단 (여기까지 도달 안 함)
  STAGE_1 → 진입 차단 (여기까지 도달 안 함)
```

---

### Phase 2: READ (시장 체제 분류 + 전환 핸들링)

**기존 Step 1 + Step 2를 통합**
**고도화**: DNA 가중치 동적화 + 타임프레임 세트별 적응 + 히스테리시스 단순화

```python
def phase2_read(candles_by_tf, safety_result, agent_config, agent_state, params):
    """
    시장이 지금 어떤 상태인지 판단.
    에이전트의 타임프레임 세트에 따라 다르게 동작.
    agent_state: 현재 체제, 전환 이력 등 상태 정보
    ~100ms
    """

    # ━━━━ 2A: 극단 상황 시 조기 반환 (Step 1에서 계승) ━━━━
    if safety_result.stage == 'STAGE_1':
        return RegimeResult(regime='WAIT', confidence=0.0, alignment=0.0,
                            tf_results={}, in_transition=False, blend_progress=0.0)

    # ━━━━ 2B: 타임프레임별 DNA 계산 ━━━━
    tf_results = {}
    for tf in agent_config.timeframes:
        candles = candles_by_tf[tf]
        dna = calculate_dna(candles, params['dna_weights'])
        regime = classify_regime(dna, params['regime_boundaries'])
        tf_results[tf] = (regime, dna.confidence)

    # ━━━━ 2C: MTF 통합 (에이전트별 다르게 동작) ━━━━
    if len(agent_config.timeframes) == 1:
        # S1: 단일 타임프레임 → MTF 정렬 불가, 확신도 구조적으로 낮음
        final_regime = tf_results[agent_config.timeframes[0]]
        alignment = 1.0  # 자기 자신과는 항상 정렬
        final_confidence = final_regime[1] * 0.85  # 15% 할인

    else:
        # S2~S4: MTF 정렬 분석 (Step 1 로직 계승)
        alignment = calculate_alignment(tf_results)
        final_regime = apply_mtf_priority(tf_results, alignment,
                                          agent_config.mtf_weights, params)
        final_confidence = apply_confidence_amplification(
            final_regime[1], alignment, params)

    # ━━━━ 2D: 전환 핸들링 (Step 2 간소화) ━━━━
    transition = handle_transition(
        current=agent_state.current_regime,
        proposed=final_regime,
        confidence=final_confidence,
        alignment=alignment,
        params=params['transition']
    )

    # 극단 상황 DNA 수정자 (Step 1에서 계승)
    if safety_result.stage == 'STAGE_3':
        final_confidence *= 0.85  # RECOVERY: -15%

    return RegimeResult(
        regime=transition.stable_regime,
        confidence=final_confidence,
        alignment=alignment,
        tf_results=tf_results,
        in_transition=transition.is_blending,
        blend_progress=transition.blend_progress
    )
```

#### 2B 상세: DNA 계산 (Step 1 계승 + 고도화)

```python
def calculate_dna(candles, weights):
    """
    3+3 DNA 체계 (기존 3개 + Hyperliquid 네이티브 3개)
    """
    # ── 기존 3개 DNA (Step 1 계승) ──

    # Hurst Exponent: R/S 분석, 범위 -1.0~+1.0
    # [피드백] window를 에이전트별로 분리 (S1: 30, S2: 50, S3: 80, S4: 100)
    # → S1이 8시간(100봉) 짜리 Hurst를 보는 건 스캘핑에 부적합
    hurst = calculate_hurst_rs(candles, window=weights.get('hurst_window', 100))
    directional_hurst = hurst * price_direction(candles)

    # Entropy: Shannon Entropy, 범위 0.0~1.0
    # bins=10, window=20
    entropy = calculate_shannon_entropy(candles, window=20, bins=10)

    # Liquidation Pressure: 범위 0.0~1.0
    # norm_vol×0.5 + sharp_moves×0.3 + volume_signal×0.2
    liquidation = calculate_liquidation_pressure(candles, window=20)

    # ── Hyperliquid 네이티브 3개 (v5.0 신규) ──
    # [피드백] Phase별 신호 역할 분리 (중복 반영 방지):
    #   Phase 1 SAFETY: funding/liq → "비정상/사고 방지" (극단값 감지)
    #   Phase 2 READ:   funding/liq → "체제 서술/방향성" (추세 해석)
    #   Phase 3 SCAN:   funding/liq → "엔트리 타이밍/변곡점" (T8)
    #   → 각 Phase에서 같은 데이터를 쓰되 목적이 다름. 의도적 설계.
    #   → 가중치는 Phase별로 "약하게" 조절 (Phase2는 방향만, Phase1은 극단만)

    # Funding Pressure: 펀딩비 누적 방향성, 범위 -1.0~+1.0
    # 양수(롱 과다) = 숏 유리, 음수(숏 과다) = 롱 유리
    funding = calculate_funding_pressure(candles)

    # OI Momentum: OI 변화 방향, 범위 -1.0~+1.0
    # OI 증가+가격상승 = 강세, OI 증가+가격하락 = 약세
    oi_momentum = calculate_oi_momentum(candles)

    # Liquidation Density: 최근 청산 집중도, 범위 0.0~1.0
    # 높을수록 추가 연쇄 청산 위험
    liq_density = calculate_liquidation_density(candles)

    # ── 정규화 후 가중 합산 ──
    # [피드백] 각 DNA 요소를 rolling z-score로 정규화
    # → 스케일이 곧 가중치가 되는 문제 방지
    components_raw = {
        'hurst': directional_hurst, 'entropy': entropy,
        'liquidation': liquidation, 'funding': funding,
        'oi_momentum': oi_momentum, 'liq_density': liq_density,
    }
    components_norm = {k: rolling_zscore(v, stats) for k, v in components_raw.items()}

    # 가중치는 Evolver가 동적으로 조정
    # [피드백] 조정 후 합이 1.0이 되도록 re-normalize
    raw_weights = {k: weights[k] for k in components_norm}
    w_sum = sum(raw_weights.values())
    norm_weights = {k: v / w_sum for k, v in raw_weights.items()}

    score = sum(components_norm[k] * norm_weights[k] for k in components_norm)

    return DNAResult(score=score, components=components_norm, weights_used=norm_weights)
```

**기본 DNA 가중치 (Evolver가 동적 조정):**

```python
DEFAULT_DNA_WEIGHTS = {
    # 기존 3개 (Step 1의 0.40/0.30/0.30에서 재배분)
    "hurst":        0.25,   # 추세 지속성
    "entropy":      0.15,   # 불확실성
    "liquidation":  0.15,   # 청산 압력

    # Hyperliquid 네이티브 3개 (v5.0 신규)
    "funding":      0.15,   # 펀딩비 압력
    "oi_momentum":  0.15,   # OI 방향성
    "liq_density":  0.15,   # 청산 집중도
}
# 합계 = 1.0
# [피드백] Evolver가 가중치 조정 시 re-normalize하여 합 1.0 유지

# [피드백] 에이전트별 DNA 파라미터 (Hurst window 등)
AGENT_DNA_PARAMS = {
    "s1": {"hurst_window": 30},   # 5m × 30 = 2.5시간 (스캘퍼)
    "s2": {"hurst_window": 50},   # 5m × 50 = 4.2시간
    "s3": {"hurst_window": 80},   # 5m × 80 = 6.7시간
    "s4": {"hurst_window": 100},  # 5m × 100 = 8.3시간 (포지션)
}
```

#### 2C 상세: 에이전트별 MTF 가중치

```python
AGENT_MTF_WEIGHTS = {
    "s1": {
        "5m": 1.0
        # 단일 TF → 정렬 분석 없음
    },
    "s2": {
        "5m":  0.45,   # 진입 타이밍
        "15m": 0.55    # 방향 확인
    },
    "s3": {
        "5m":  0.25,   # 진입 타이밍
        "15m": 0.30,   # 단기 방향
        "1h":  0.45    # 중기 방향 (주도)
    },
    "s4": {
        "5m":  0.15,   # 진입 타이밍
        "15m": 0.20,   # 단기 확인
        "1h":  0.30,   # 중기 방향
        "4h":  0.35    # 장기 방향 (주도)
    }
}
```

#### 2D 상세: 전환 핸들링 (Step 2 간소화)

```python
TRANSITION_PARAMS = {
    # 히스테리시스 유예: 동적 (기존 규칙 기반 유지하되 범위 단순화)
    "base_grace": 3,       # 기본 유예 캔들
    "min_grace": 1,
    "max_grace": 6,        # 기존 7에서 축소

    # 확신도에 따른 조정
    "high_conf_threshold": 0.85,  # 이상이면 grace -1
    "low_conf_threshold":  0.55,  # 이하면 grace +2

    # MTF 정렬에 따른 조정
    "strong_alignment_threshold": 0.70,  # 이상이면 grace -1
    "conflict_alignment_threshold": 0.30, # 이하면 grace +2

    # VOLATILE 특별 처리
    "volatile_entry_grace": 1,   # VOLATILE 진입은 빠르게
    "volatile_exit_grace": 4,    # VOLATILE 탈출은 신중하게

    # 블렌딩 (Step 2에서 계승, 단순화)
    "blend_candles_per_distance": 1,  # 체제 거리 1당 1캔들 블렌딩
    "min_blend": 2,
    "max_blend": 5,
}
```

**체제 분류 (Step 1의 6가지 체제 유지):**

```python
REGIMES = {
    'STRONG_UPTREND':   5,  # 공격적 롱
    'WEAK_UPTREND':     4,  # 보수적 롱
    'SIDEWAYS':         3,  # 레인지
    'WEAK_DOWNTREND':   2,  # 보수적 숏
    'STRONG_DOWNTREND': 1,  # 공격적 숏
    'VOLATILE':         0,  # 거래 중단
}

# 경계값: percentile 기반 (기존 고정값 대체)
REGIME_BOUNDARIES = {
    "strong_trend_hurst_pct": 80,    # Hurst 상위 20%
    "weak_trend_hurst_pct":  60,     # Hurst 상위 40%
    "volatile_entropy_pct":  90,     # Entropy 상위 10%
    "volatile_liq_pct":      90,     # Liquidation 상위 10%
    "sideways_entropy_pct":  70,     # Entropy 상위 30%
}
```

---

### Phase 3: SCAN (차트 구조 + 변곡점 + 패턴 분석 통합 탐지)

**기존 Step 3 + Step 4를 통합 + 패턴 분석 신규 추가**
**고도화**: S/R+추세선+VP+변곡점+캔들스틱패턴+차트패턴을 한 패스에서 수행, ATR 기반 모든 거리 정규화

```python
def phase3_scan(candles_by_tf, regime_result, safety_result, params):
    """
    차트 구조 분석 + 변곡점 탐지 + 패턴 분석을 동시에 수행.
    기존에는 Step 3 → Step 4 순차였지만, 정보가 겹치므로 통합.
    ~150ms (패턴 분석 추가로 120→150ms)
    """
    primary_tf = agent_config.primary_timeframe  # S1: 5m, S2: 5m, S3: 5m, S4: 5m
    primary_candles = candles_by_tf[primary_tf]
    atr = calculate_atr(primary_candles, period=14)

    # ━━━━ 3A: 구조 분석 (병렬, Step 3 계승) ━━━━
    # ThreadPoolExecutor 4개로 병렬 실행 (패턴 분석 추가)
    sr_levels    = identify_sr_levels(primary_candles, atr, params['sr'])
    trendlines   = calculate_trendlines(primary_candles, atr, params['trendline'])
    vol_profile  = calculate_volume_profile(primary_candles, params['vp'])
    patterns     = detect_patterns(primary_candles, candles_by_tf, atr, params['pattern'])  # [NEW]

    # ━━━━ 3B: 컨텍스트 판단 (Step 3 계승 + 패턴 반영) ━━━━
    context = resolve_context(
        price=primary_candles[-1].close,
        sr_levels=sr_levels,
        trendlines=trendlines,
        vol_profile=vol_profile,
        patterns=patterns,  # [NEW] 패턴이 컨텍스트에 영향
        atr=atr
    )

    # ━━━━ 3C: 변곡점 탐지 (Step 4 계승, 통합) ━━━━
    inflections = detect_inflections(
        candles=primary_candles,
        sr_levels=sr_levels,
        trendlines=trendlines,
        vol_profile=vol_profile,
        patterns=patterns,  # [NEW] 패턴이 변곡점 보강
        context=context,
        regime=regime_result,
        atr=atr,
        params=params['inflection']
    )

    # ━━━━ 3D: MTF 수렴 체크 (Step 4 MTF 로직) ━━━━
    if len(agent_config.timeframes) > 1:
        mtf_grade = check_mtf_convergence(
            inflections, candles_by_tf, agent_config.timeframes)
    else:
        mtf_grade = 'NONE'  # S1은 MTF 없음

    # ━━━━ 3E: 패턴-변곡점 시너지 보너스 ━━━━
    apply_pattern_synergy(inflections, patterns, params['pattern_synergy'])

    # ━━━━ 3F: 최종 점수 산출 ━━━━
    best = select_best_inflection(inflections, mtf_grade, params)

    if best is None or best.score < params['inflection']['min_score']:
        return ScanResult(found=False)

    return ScanResult(
        found=True,
        primary_type=best.primary_type,
        secondary_type=best.secondary_type,
        score=best.score,
        mtf_grade=mtf_grade,
        implied_direction=best.implied_direction,
        entry_price=primary_candles[-1].close,    # 현재봉 종가
        context=context,
        sr_levels=sr_levels,
        trendlines=trendlines,
        vol_profile=vol_profile,
        patterns=patterns,
        atr=atr,
        pattern_target_atr=best.pattern_target_atr,
    )
```

#### 3A-4 상세: 패턴 분석 (v5.0 신규)

```python
def detect_patterns(primary_candles, candles_by_tf, atr, params):
    """
    캔들스틱 패턴 + 차트 패턴을 동시에 탐지.
    ~30ms (병렬 처리)
    """
    result = PatternResult()

    # ━━━━ (1) 캔들스틱 패턴 (최근 1~3봉 분석) ━━━━
    result.candlestick = detect_candlestick_patterns(
        primary_candles, atr, params['candlestick'])

    # ━━━━ (2) 차트 패턴 (최근 N봉 구조 분석) ━━━━
    result.chart = detect_chart_patterns(
        primary_candles, candles_by_tf, atr, params['chart'])

    return result
```

##### (1) 캔들스틱 패턴 인식

3-Tier 체계: 단일봉(Tier 1) → 2봉 조합(Tier 2) → 3봉 조합(Tier 3)

```python
CANDLESTICK_PATTERNS = {
    # ═══ Tier 1: 단일봉 패턴 (가장 빠른 감지, +5~10점) ═══
    "HAMMER": {
        "direction": "LONG",
        "score": 8,
        "condition": "lower_shadow >= body * 2 AND upper_shadow <= body * 0.3",
        "description": "긴 아래꼬리. 매수세 유입",
    },
    "INVERTED_HAMMER": {
        "direction": "LONG",
        "score": 6,
        "condition": "upper_shadow >= body * 2 AND lower_shadow <= body * 0.3",
        "description": "매도 실패 → 반등 가능",
    },
    "SHOOTING_STAR": {
        "direction": "SHORT",
        "score": 8,
        "condition": "upper_shadow >= body * 2 AND lower_shadow <= body * 0.3 AND uptrend",
        "description": "상승 중 매도 압력. 천장 시그널",
    },
    "HANGING_MAN": {
        "direction": "SHORT",
        "score": 7,
        "condition": "lower_shadow >= body * 2 AND upper_shadow <= body * 0.3 AND uptrend",
        "description": "상승 중 매도세 경고",
    },
    "DOJI": {
        "direction": "NEUTRAL",
        "score": 5,
        "condition": "abs(open - close) <= atr * 0.05",
        "description": "매수/매도 균형 → 추세 전환 가능",
    },
    "DRAGONFLY_DOJI": {
        "direction": "LONG",
        "score": 7,
        "condition": "doji AND lower_shadow >= atr * 0.5 AND upper_shadow <= atr * 0.05",
        "description": "바닥 반전 도지",
    },
    "GRAVESTONE_DOJI": {
        "direction": "SHORT",
        "score": 7,
        "condition": "doji AND upper_shadow >= atr * 0.5 AND lower_shadow <= atr * 0.05",
        "description": "천장 반전 도지",
    },
    "MARUBOZU_BULL": {
        "direction": "LONG",
        "score": 9,
        "condition": "close > open AND upper_shadow <= body * 0.05 AND lower_shadow <= body * 0.05",
        "description": "강한 매수. 꼬리 없는 양봉",
    },
    "MARUBOZU_BEAR": {
        "direction": "SHORT",
        "score": 9,
        "condition": "close < open AND upper_shadow <= body * 0.05 AND lower_shadow <= body * 0.05",
        "description": "강한 매도. 꼬리 없는 음봉",
    },

    # ═══ Tier 2: 2봉 조합 (확인 패턴, +8~15점) ═══
    "BULLISH_ENGULFING": {
        "direction": "LONG",
        "score": 12,
        "condition": "prev:bearish AND curr:bullish AND curr.body > prev.body AND downtrend",
        "description": "음봉을 완전히 감싸는 양봉. 강한 반전",
    },
    "BEARISH_ENGULFING": {
        "direction": "SHORT",
        "score": 12,
        "condition": "prev:bullish AND curr:bearish AND curr.body > prev.body AND uptrend",
        "description": "양봉을 완전히 감싸는 음봉. 강한 반전",
    },
    "TWEEZER_BOTTOM": {
        "direction": "LONG",
        "score": 10,
        "condition": "abs(prev.low - curr.low) <= atr * 0.05 AND downtrend",
        "description": "같은 저점 2번 터치. 지지 확인",
    },
    "TWEEZER_TOP": {
        "direction": "SHORT",
        "score": 10,
        "condition": "abs(prev.high - curr.high) <= atr * 0.05 AND uptrend",
        "description": "같은 고점 2번 터치. 저항 확인",
    },
    "PIERCING_LINE": {
        "direction": "LONG",
        "score": 10,
        "condition": "prev:bearish AND curr:bullish AND curr.close > prev.midpoint AND curr.open < prev.low",
        "description": "하락 후 반등, 전봉 중간 이상 회복",
    },
    "DARK_CLOUD_COVER": {
        "direction": "SHORT",
        "score": 10,
        "condition": "prev:bullish AND curr:bearish AND curr.close < prev.midpoint AND curr.open > prev.high",
        "description": "상승 후 하락, 전봉 중간 이하 하락",
    },

    # ═══ Tier 3: 3봉 조합 (고확률, +12~18점) ═══
    "MORNING_STAR": {
        "direction": "LONG",
        "score": 15,
        "condition": "c1:bearish(big) AND c2:small_body AND c3:bullish(big) AND c3.close > c1.midpoint",
        "description": "바닥 반전 3봉. 높은 신뢰도",
    },
    "EVENING_STAR": {
        "direction": "SHORT",
        "score": 15,
        "condition": "c1:bullish(big) AND c2:small_body AND c3:bearish(big) AND c3.close < c1.midpoint",
        "description": "천장 반전 3봉. 높은 신뢰도",
    },
    "THREE_WHITE_SOLDIERS": {
        "direction": "LONG",
        "score": 16,
        "condition": "3연속 양봉 AND 각 봉 body >= atr*0.3 AND 각 종가 > 전봉 종가",
        "description": "강력한 상승 지속. 추세 확인",
    },
    "THREE_BLACK_CROWS": {
        "direction": "SHORT",
        "score": 16,
        "condition": "3연속 음봉 AND 각 봉 body >= atr*0.3 AND 각 종가 < 전봉 종가",
        "description": "강력한 하락 지속. 추세 확인",
    },
    "THREE_INSIDE_UP": {
        "direction": "LONG",
        "score": 13,
        "condition": "c1:bearish(big) AND c2:bullish(inside c1) AND c3:bullish(close > c1.high)",
        "description": "하락세 내부 반전 확인",
    },
    "THREE_INSIDE_DOWN": {
        "direction": "SHORT",
        "score": 13,
        "condition": "c1:bullish(big) AND c2:bearish(inside c1) AND c3:bearish(close < c1.low)",
        "description": "상승세 내부 반전 확인",
    },
}
```

```python
def detect_candlestick_patterns(candles, atr, params):
    """
    최근 1~3봉에서 캔들스틱 패턴 탐지.
    ATR 기반으로 body/shadow 크기를 정규화하여 판단.
    """
    detected = []
    c = candles[-1]          # 현재봉
    p = candles[-2]          # 전봉
    pp = candles[-3]         # 전전봉

    body = abs(c.close - c.open)
    upper_shadow = c.high - max(c.open, c.close)
    lower_shadow = min(c.open, c.close) - c.low

    # 추세 판단 (최근 5봉 방향)
    trend = detect_micro_trend(candles[-6:-1], atr)  # 'UP', 'DOWN', 'FLAT'

    # Tier 1: 단일봉 스캔
    for name, pattern in TIER_1_PATTERNS.items():
        if evaluate_single_candle(c, body, upper_shadow, lower_shadow, atr, trend, pattern):
            detected.append(CandlestickMatch(
                name=name, tier=1, direction=pattern['direction'],
                score=pattern['score'], candles_used=1))

    # Tier 2: 2봉 조합 스캔
    for name, pattern in TIER_2_PATTERNS.items():
        if evaluate_two_candles(p, c, atr, trend, pattern):
            detected.append(CandlestickMatch(
                name=name, tier=2, direction=pattern['direction'],
                score=pattern['score'], candles_used=2))

    # Tier 3: 3봉 조합 스캔
    for name, pattern in TIER_3_PATTERNS.items():
        if evaluate_three_candles(pp, p, c, atr, trend, pattern):
            detected.append(CandlestickMatch(
                name=name, tier=3, direction=pattern['direction'],
                score=pattern['score'], candles_used=3))

    # 중복 방향 필터: 같은 방향 패턴 중 최고 점수만 유지
    return filter_best_per_direction(detected)
```

##### (2) 차트 패턴 인식

```python
CHART_PATTERNS = {
    # ═══════════════════════════════════════════
    # 8개 핵심 차트 패턴 (유저 선정, 우선순위 순)
    # ═══════════════════════════════════════════

    # ── Rank 1: TRIPLE_BOTTOM (최강 패턴) ──
    "TRIPLE_BOTTOM": {
        "type": "reversal",
        "direction": "LONG",
        "score": 20,              # 최고 점수 (최강 패턴)
        "min_candles": 25,
        "preferred_symbols": "ALL",
        "description": "같은 지지대 3번 터치 후 상승. 강력한 바닥 확인",
        "detect": {
            "method": "peak_valley_sequence",
            "tolerance_atr": 0.3,      # 3개 저점 차이 < ATR * 0.3
            "min_peak_height_atr": 0.8, # 중간 고점 깊이 > ATR * 0.8
            "confirmation": "close above highest intermediate peak",
            "volume": "3rd touch volume > 2nd touch volume (이상적)",
        },
        "target": "pattern_height × 1.0 from breakout",
    },

    # ── Rank 2: DOUBLE_BOTTOM (BTC 전용 최적화) ──
    "DOUBLE_BOTTOM": {
        "type": "reversal",
        "direction": "LONG",
        "score": 17,
        "min_candles": 15,
        "preferred_symbols": ["BTC"],  # BTC에서 특히 효과적
        "symbol_score_boost": {"BTC": 3},  # BTC일 때 +3점
        "description": "같은 지지대 2번 터치 후 상승. W자 형태. BTC에서 높은 승률",
        "detect": {
            "tolerance_atr": 0.3,          # 두 저점 차이 < ATR * 0.3
            "min_peak_height_atr": 1.0,    # 중간 고점 > ATR * 1.0
            "confirmation": "close above intermediate peak (넥라인)",
            "volume": "2nd bottom volume < 1st (매도세 소진 확인)",
        },
        "target": "pattern_height × 1.0 from neckline",
    },

    # ── Rank 3: ASCENDING_TRIANGLE (SOL/XRP용) ──
    "ASCENDING_TRIANGLE": {
        "type": "continuation",
        "direction": "LONG",
        "score": 15,
        "min_candles": 15,
        "preferred_symbols": ["SOL", "XRP"],  # 알트코인에서 효과적
        "symbol_score_boost": {"SOL": 3, "XRP": 3},
        "description": "수평 저항 + 상승 지지. 매수 압력 증가. 알트코인 강세장에서 자주 출현",
        "detect": {
            "resistance": "horizontal (tolerance < atr * 0.2)",
            "support": "ascending trendline (R² > 0.80, 최소 3 touch)",
            "confirmation": "close above resistance with volume > avg * 1.3",
            "min_touches": 3,  # 최소 3번 저항선 터치
        },
        "target": "triangle_height × 1.0 from breakout",
    },

    # ── Rank 4: INVERSE_HEAD_AND_SHOULDERS (역 헤드앤숄더) ──
    "INVERSE_HEAD_AND_SHOULDERS": {
        "type": "reversal",
        "direction": "LONG",
        "score": 18,
        "min_candles": 20,
        "preferred_symbols": "ALL",
        "description": "역 머리어깨. 강한 바닥 반전 시그널. 넥라인 돌파 시 확인",
        "detect": {
            "method": "peak_valley_sequence",
            "left_shoulder": "valley",
            "head": "lower valley (deeper than shoulders)",
            "right_shoulder": "valley (similar depth to left, tolerance < atr * 0.4)",
            "neckline": "connect peaks between shoulders",
            "confirmation": "close above neckline with volume surge",
            "symmetry": "right_shoulder depth within 70~130% of left",
        },
        "target": "head_to_neckline distance × 1.0 from neckline",
    },

    # ── Rank 5: CUP_AND_HANDLE (컵앤핸들) ──
    "CUP_AND_HANDLE": {
        "type": "continuation",
        "direction": "LONG",
        "score": 16,
        "min_candles": 30,          # 형성에 시간이 걸림
        "preferred_symbols": "ALL",
        "description": "U자형 컵 + 작은 하락 조정(핸들). 상승 지속 패턴의 왕",
        "detect": {
            "cup": {
                "shape": "rounded bottom (U자, V자 아님)",
                "depth_atr": [2.0, 8.0],    # 컵 깊이: ATR의 2~8배
                "width_candles": [15, 50],   # 컵 너비: 15~50봉
                "rim_tolerance_atr": 0.3,    # 양쪽 림 높이 차이 < ATR * 0.3
            },
            "handle": {
                "retrace_pct": [0.10, 0.50],  # 컵 깊이의 10~50% 조정
                "width_candles": [3, 15],      # 핸들 너비: 3~15봉
                "direction": "slight_down or sideways",
            },
            "confirmation": "close above cup rim (breakout) with volume",
        },
        "target": "cup_depth × 1.0 from rim breakout",
    },

    # ── Rank 6: HEAD_AND_SHOULDERS (헤드앤숄더) ──
    "HEAD_AND_SHOULDERS": {
        "type": "reversal",
        "direction": "SHORT",
        "score": 18,
        "min_candles": 20,
        "preferred_symbols": "ALL",
        "description": "왼쪽 어깨-머리-오른쪽 어깨. 강한 천장 반전 시그널",
        "detect": {
            "method": "peak_valley_sequence",
            "left_shoulder": "peak",
            "head": "higher peak (taller than shoulders)",
            "right_shoulder": "peak (similar height to left, tolerance < atr * 0.4)",
            "neckline": "connect valleys between shoulders",
            "confirmation": "close below neckline with volume surge",
            "symmetry": "right_shoulder height within 70~130% of left",
        },
        "target": "head_to_neckline distance × 1.0 below neckline",
    },

    # ── Rank 7: DESCENDING_TRIANGLE (하락 삼각형) ──
    "DESCENDING_TRIANGLE": {
        "type": "continuation",
        "direction": "SHORT",
        "score": 15,
        "min_candles": 15,
        "preferred_symbols": "ALL",
        "description": "수평 지지 + 하락 저항. 매도 압력 증가",
        "detect": {
            "support": "horizontal (tolerance < atr * 0.2)",
            "resistance": "descending trendline (R² > 0.80, 최소 3 touch)",
            "confirmation": "close below support with volume > avg * 1.3",
            "min_touches": 3,
        },
        "target": "triangle_height × 1.0 below breakdown",
    },

    # ── Rank 8: WEDGE (쐐기형) ──
    "RISING_WEDGE": {
        "type": "reversal",
        "direction": "SHORT",
        "score": 14,
        "min_candles": 15,
        "preferred_symbols": "ALL",
        "description": "상승 쐐기. 기울기 줄어드는 상승 → 하방 이탈. 상승세 약화 신호",
        "detect": {
            "upper_line": "ascending trendline (R² > 0.75)",
            "lower_line": "ascending trendline (R² > 0.75, steeper slope)",
            "convergence": "두 선이 수렴 (기울기 차이 감소)",
            "confirmation": "close below lower trendline",
        },
        "target": "wedge_height × 0.6 below breakdown",
    },
    "FALLING_WEDGE": {
        "type": "reversal",
        "direction": "LONG",
        "score": 14,
        "min_candles": 15,
        "preferred_symbols": "ALL",
        "description": "하락 쐐기. 기울기 줄어드는 하락 → 상방 돌파. 하락세 약화 신호",
        "detect": {
            "upper_line": "descending trendline (R² > 0.75)",
            "lower_line": "descending trendline (R² > 0.75, steeper slope)",
            "convergence": "두 선이 수렴 (기울기 차이 감소)",
            "confirmation": "close above upper trendline",
        },
        "target": "wedge_height × 0.6 above breakout",
    },
}

# ═══ 심볼별 패턴 점수 보정 ═══
# 특정 심볼에서 특정 패턴이 더 효과적일 때 적용
SYMBOL_PATTERN_BOOST = {
    "BTC":  {"DOUBLE_BOTTOM": +3, "HEAD_AND_SHOULDERS": +2},
    "SOL":  {"ASCENDING_TRIANGLE": +3, "CUP_AND_HANDLE": +2},
    "XRP":  {"ASCENDING_TRIANGLE": +3, "FALLING_WEDGE": +2},
    "ETH":  {"INVERSE_HEAD_AND_SHOULDERS": +2, "TRIPLE_BOTTOM": +2},
    # Evolver가 거래 결과 기반으로 이 테이블을 동적 업데이트
}
```

```python
def detect_chart_patterns(primary_candles, candles_by_tf, atr, params):
    """
    최근 N봉에서 차트 패턴(형태 패턴) 탐지.
    Peak/Valley 추출 → 패턴 매칭 → 확인(confirmation) 체크.
    """
    detected = []

    # Step 1: Peak/Valley 추출 (ZigZag 기반)
    # atr_threshold: peak/valley 최소 깊이를 ATR 기반으로 설정
    peaks, valleys = extract_peaks_valleys(
        primary_candles, min_depth_atr=0.5, lookback=params['max_lookback'])

    # Step 2: 각 패턴 매칭
    for name, pattern in CHART_PATTERNS.items():
        if len(primary_candles) < pattern['min_candles']:
            continue

        match = match_chart_pattern(
            name, pattern, primary_candles, peaks, valleys, atr)

        if match is not None:
            # Step 3: 확인(confirmation) 체크
            # 패턴이 '완성'되었는지 (넥라인 돌파, 채널 이탈 등)
            confirmed = check_pattern_confirmation(
                match, primary_candles[-1], atr)

            detected.append(ChartPatternMatch(
                name=name,
                type=pattern['type'],
                direction=pattern['direction'],
                score=pattern['score'],
                confirmed=confirmed,
                key_level=match.key_level,     # 넥라인/돌파레벨
                target_atr=match.target_atr,   # 패턴 목표가 (ATR 단위)
                confidence=match.confidence,
            ))

    # MTF 보강: 상위 타임프레임에서도 같은 패턴 확인 시 점수 ×1.3
    if len(candles_by_tf) > 1:
        detected = apply_mtf_pattern_boost(detected, candles_by_tf, atr)

    return detected
```

##### 차트 패턴 파라미터

```python
CHART_PATTERN_PARAMS = {
    # Peak/Valley 추출
    "min_depth_atr": 0.5,       # 최소 peak-valley 깊이 (ATR 배수)
    "max_lookback": 60,         # 최대 탐색 캔들 수 (5m × 60 = 5시간)

    # 패턴별 크기 기준 (모두 ATR 배수)
    "double_top_tolerance": 0.3,         # 두 고점 허용 오차
    "double_bottom_tolerance": 0.3,
    "h_and_s_shoulder_tolerance": 0.4,   # 어깨 높이 허용 오차
    "flag_pole_min": 3.0,                # 깃대 최소 길이
    "flag_channel_max": 0.5,             # 깃발 채널 폭 최대
    "triangle_convergence_min": 0.3,     # 삼각형 수렴 최소 비율

    # 확인(confirmation) 기준 (피드백 #4: 엄격화)
    # close 1회 돌파만으로는 confirmed 아님
    "confirmation_mode": "strict",
    "confirmation_rules": {
        "min_closes_beyond": 2,          # 최소 2캔들 연속 돌파 레벨 너머 종가
        "breakout_close_beyond": 0.1,    # 돌파 레벨 넘어선 종가 (ATR 배수)
        "volume_confirmation": 1.3,      # 돌파 시 볼륨 > 평균의 1.3배
        "retest_bonus": True,            # 돌파 후 리테스트 발생 시 추가 확인
    },

    # 미확인 패턴 감점
    "unconfirmed_score_mult": 0.5,       # 미확인 시 점수 × 0.5 (기존 0.6에서 강화)

    # MTF 보강 배수
    "mtf_boost": 1.3,                    # 상위 TF에서도 같은 패턴 → × 1.3
}
```

##### 차트 패턴 에이전트별 활성화 정책 (피드백 반영)

```python
# [피드백] S1/S2는 5분봉에서 Cup&Handle(2.5시간), Triple Bottom(2시간) 등
# 차트 패턴 구분이 극히 어려움. 캔들스틱 패턴만 사용.
# S3/S4는 상위 TF(1h, 4h)에서 차트 패턴을 돌린다.

AGENT_PATTERN_POLICY = {
    "s1": {
        "candlestick_enabled": True,   # Tier 1~3 전부
        "chart_pattern_enabled": False, # 차트 패턴 비활성화
        "reason": "5m 단일 TF에서 차트 패턴 = 노이즈",
    },
    "s2": {
        "candlestick_enabled": True,
        "chart_pattern_enabled": False, # 15m에서도 아직 부족
        "reason": "5m+15m은 차트 패턴 최소 봉 수 미달 가능",
    },
    "s3": {
        "candlestick_enabled": True,
        "chart_pattern_enabled": True,  # 1h에서 차트 패턴 활성
        "chart_pattern_tf": "1h",       # 차트 패턴은 1h 캔들에서 탐지
        "reason": "1h 봉에서 Double Bottom(15봉=15시간) 등 유효",
    },
    "s4": {
        "candlestick_enabled": True,
        "chart_pattern_enabled": True,  # 4h에서 차트 패턴 활성
        "chart_pattern_tf": "4h",       # 차트 패턴은 4h 캔들에서 탐지
        "reason": "4h 봉에서 H&S(20봉=80시간) 등 가장 신뢰도 높음",
    },
}
```

##### ZigZag 체제별 min_depth_atr (피드백 반영)

```python
# [피드백] SIDEWAYS에서 0.5 ATR이면 거의 모든 움직임이 peak/valley가 됨
# 체제별로 min_depth_atr을 다르게 설정
ZIGZAG_REGIME_PARAMS = {
    "STRONG_UPTREND":   {"min_depth_atr": 0.8},  # 되돌림만 잡기
    "WEAK_UPTREND":     {"min_depth_atr": 0.6},
    "SIDEWAYS":         {"min_depth_atr": 1.0},  # 엄격하게 (노이즈 방지)
    "WEAK_DOWNTREND":   {"min_depth_atr": 0.6},
    "STRONG_DOWNTREND": {"min_depth_atr": 0.8},
    "VOLATILE":         {"min_depth_atr": 1.2},  # 가장 엄격
}
```

##### Confirmation 에이전트별 엄격도 분리 (피드백 반영)

```python
# [피드백] min_closes_beyond=2는 5m에서 10분 지연. S1에겐 치명적.
AGENT_CONFIRMATION_STRICTNESS = {
    "s1": {"min_closes_beyond": 1, "breakout_close_beyond_atr": 0.15},
    "s2": {"min_closes_beyond": 1, "breakout_close_beyond_atr": 0.12},
    "s3": {"min_closes_beyond": 2, "breakout_close_beyond_atr": 0.10},
    "s4": {"min_closes_beyond": 2, "breakout_close_beyond_atr": 0.10},
}
```

##### 차트 패턴 레짐 제한 (피드백 반영)

```python
# [피드백] Cup&Handle은 SIDEWAYS에서만, H&S는 업트렌드에서만 등
# 패턴 "출현 조건"을 체제로 강제
CHART_PATTERN_REGIME_FILTER = {
    "CUP_AND_HANDLE":            ["WEAK_UPTREND", "SIDEWAYS"],
    "HEAD_AND_SHOULDERS":        ["STRONG_UPTREND", "WEAK_UPTREND"],
    "INVERSE_HEAD_AND_SHOULDERS":["STRONG_DOWNTREND", "WEAK_DOWNTREND"],
    "ASCENDING_TRIANGLE":        ["WEAK_UPTREND", "SIDEWAYS"],
    "DESCENDING_TRIANGLE":       ["WEAK_DOWNTREND", "SIDEWAYS"],
    "TRIPLE_BOTTOM":             ["WEAK_DOWNTREND", "SIDEWAYS"],
    "DOUBLE_BOTTOM":             ["WEAK_DOWNTREND", "SIDEWAYS"],
    "RISING_WEDGE":              ["STRONG_UPTREND", "WEAK_UPTREND"],
    "FALLING_WEDGE":             ["STRONG_DOWNTREND", "WEAK_DOWNTREND"],
}
# 해당 체제가 아닌데 패턴이 감지되면 → 무시 (오탐 방지)
```

##### peak/valley 디버깅 저장 (피드백 반영)

```
[피드백] ZigZag 기반 peak/valley 추출 결과를 DB에 저장.
나중에 "왜 패턴이 오탐이었는지" 역추적 가능하게.

테이블: pattern_debug (Phase별 스냅샷 로그에 포함)
  - timestamp, symbol, agent_id
  - peaks: JSON array of {price, index, candle_ts}
  - valleys: JSON array
  - detected_patterns: JSON array
  - outcome: 'TP'/'SL'/'TIMEOUT'/NULL (사후 기록)
```

##### 차트 패턴 단계적 구현 순서 (피드백 #4)

```
처음부터 8개 차트 패턴을 다 넣으면 디버깅이 지옥이다.
5분봉에서 Cup&Handle(30봉=2.5시간), Triple Bottom(25봉=2시간)은
ZigZag 파라미터에 따라 오탐(false positive)이 극단적으로 달라진다.

═══ 구현 로드맵 ═══

Stage 1 (Day 1~): 캔들스틱 패턴만
  → Tier 1~3 캔들스틱 22개 구현
  → 이건 최근 1~3봉만 보므로 오탐 위험 낮음
  → Paper trading 시작

Stage 2 (Week 2~, Paper 데이터 축적 후): 단순 차트 패턴
  → Double Bottom (가장 단순, BTC 전용)
  → Ascending/Descending Triangle (추세선+수평선 조합)
  → Wedge (Rising/Falling)
  → 이 4개는 Peak/Valley + 직선 피팅으로 비교적 안정적

Stage 3 (Week 4~, Stage 2 검증 후): 복잡 차트 패턴
  → Triple Bottom (Double Bottom 로직 확장)
  → Head & Shoulders / Inverse H&S (3-peak/valley 매칭)
  → Cup & Handle (곡선 피팅 필요, 가장 어려움)

각 Stage 추가 시:
  1. 해당 패턴만 별도 오탐률 측정 (최소 1주)
  2. 오탐률 > 60% → 파라미터 재조정 또는 비활성화
  3. Evolver가 패턴별 승률 통계를 자동 수집
```

##### 패턴-변곡점 시너지 (v5.0 핵심 통합)

패턴 분석은 독립적으로 동작하지 않고, 기존 변곡점 시스템과 **시너지 보너스**를 생성한다.

```python
PATTERN_SYNERGY = {
    # 캔들스틱 패턴이 변곡점과 같은 방향일 때 보너스
    "candlestick_same_direction": {
        "tier_1": +5,    # 단일봉 확인
        "tier_2": +8,    # 2봉 조합 확인
        "tier_3": +12,   # 3봉 조합 확인 (높은 신뢰)
    },

    # 차트 패턴이 변곡점과 같은 방향일 때 보너스
    "chart_pattern_same_direction": {
        "confirmed":    +15,   # 확인된 차트 패턴 = 강력 보강
        "unconfirmed":  +5,    # 미확인 = 약한 보강
    },

    # 특별 시너지 조합 (높은 점수)
    "special_combos": {
        # S/R 반응(T1) + 반전 캔들스틱 = 강한 반전 시그널
        ("T1_SR_REACTION", "BULLISH_ENGULFING"):    +12,
        ("T1_SR_REACTION", "BEARISH_ENGULFING"):    +12,
        ("T1_SR_REACTION", "MORNING_STAR"):         +15,
        ("T1_SR_REACTION", "EVENING_STAR"):         +15,
        ("T1_SR_REACTION", "HAMMER"):               +8,
        ("T1_SR_REACTION", "SHOOTING_STAR"):        +8,

        # 돌파 리테스트(T3) + 지속 차트 패턴 = 강한 지속 시그널
        ("T3_BREAKOUT_RETEST", "ASCENDING_TRIANGLE"):  +14,
        ("T3_BREAKOUT_RETEST", "DESCENDING_TRIANGLE"): +14,
        ("T3_BREAKOUT_RETEST", "CUP_AND_HANDLE"):     +15,

        # 추세선 돌파(T4) + 반전 차트 패턴 = 추세 전환 확인
        ("T4_TRENDLINE_BREAK", "HEAD_AND_SHOULDERS"):      +16,
        ("T4_TRENDLINE_BREAK", "INVERSE_HEAD_AND_SHOULDERS"):+16,
        ("T4_TRENDLINE_BREAK", "DOUBLE_BOTTOM"):            +14,
        ("T4_TRENDLINE_BREAK", "TRIPLE_BOTTOM"):            +18,  # 최강 조합
        ("T4_TRENDLINE_BREAK", "RISING_WEDGE"):             +12,
        ("T4_TRENDLINE_BREAK", "FALLING_WEDGE"):            +12,

        # 볼륨 폭발(T7) + 마루보즈 = 극단적 모멘텀
        ("T7_VOLUME_EXPLOSION", "MARUBOZU_BULL"):    +10,
        ("T7_VOLUME_EXPLOSION", "MARUBOZU_BEAR"):    +10,
        ("T7_VOLUME_EXPLOSION", "THREE_WHITE_SOLDIERS"): +12,
        ("T7_VOLUME_EXPLOSION", "THREE_BLACK_CROWS"):    +12,

        # 다이버전스(T6) + 반전 캔들스틱/차트 = 반전 확정
        ("T6_DIVERGENCE", "MORNING_STAR"):           +14,
        ("T6_DIVERGENCE", "EVENING_STAR"):           +14,
        ("T6_DIVERGENCE", "BULLISH_ENGULFING"):      +10,
        ("T6_DIVERGENCE", "BEARISH_ENGULFING"):      +10,
        ("T6_DIVERGENCE", "DOUBLE_BOTTOM"):          +14,
        ("T6_DIVERGENCE", "TRIPLE_BOTTOM"):          +16,
        ("T6_DIVERGENCE", "INVERSE_HEAD_AND_SHOULDERS"): +16,

        # S/R 반응(T1) + 바닥 차트 패턴 = 지지 확인 최강
        ("T1_SR_REACTION", "TRIPLE_BOTTOM"):         +18,
        ("T1_SR_REACTION", "DOUBLE_BOTTOM"):         +14,
        ("T1_SR_REACTION", "FALLING_WEDGE"):         +10,

        # 펀딩/OI(T8) + 차트 패턴 = 펀더멘털+기술적 합치
        ("T8_FUNDING_OI_SIGNAL", "TRIPLE_BOTTOM"):   +14,
        ("T8_FUNDING_OI_SIGNAL", "HEAD_AND_SHOULDERS"): +12,
        ("T8_FUNDING_OI_SIGNAL", "ASCENDING_TRIANGLE"): +10,
    },

    # ═══ 패턴 보너스 상한 (피드백 #5) ═══
    # 패턴은 보강 근거지, 단독 진입 근거가 되면 안 된다.
    # 이론상 +45점까지 가능했던 것을 +25점으로 제한.
    "pattern_bonus_cap": 25,

    # 패턴 방향 불일치 시 감점
    "direction_conflict_penalty": -10,  # 변곡점과 패턴이 반대 방향

    # 차트 패턴 목표가를 TP 계산에 반영
    "use_pattern_target_for_tp": True,

    # [피드백] 콤보 보너스 활성화 조건:
    # 최소 거래 수 충족 전에는 special_combos 보너스 비활성 (과최적화 방지)
    "combo_min_trades": 50,            # 에이전트가 50거래 이상일 때만 콤보 보너스 활성
    # 활성화 이후에도 상위 3개 콤보만 적용 (나머지는 0)
    "combo_max_active": 3,
}
```

```python
def apply_pattern_synergy(inflections, patterns, params):
    """
    패턴 분석 결과를 변곡점 점수에 반영.
    방향 일치 → 보너스, 방향 충돌 → 감점.

    [피드백 #5] 패턴 보너스 총합에 상한(cap) 적용.
    패턴은 보강 근거지, 단독 진입 근거가 되면 안 된다.
    """
    cap = params['pattern_bonus_cap']  # 기본 25점

    for inf in inflections:
        inf_direction = inf.implied_direction  # 'LONG' or 'SHORT'
        pattern_bonus_total = 0  # 보너스 누적 추적

        # 캔들스틱 시너지
        for cs in patterns.candlestick:
            if cs.direction == inf_direction:
                bonus = params['candlestick_same_direction'][f'tier_{cs.tier}']
                pattern_bonus_total += bonus
                inf.pattern_confirmations.append(cs.name)
            elif cs.direction != 'NEUTRAL' and cs.direction != inf_direction:
                pattern_bonus_total += params['direction_conflict_penalty']

        # 차트 패턴 시너지
        for cp in patterns.chart:
            cp_dir = cp.direction
            if cp_dir == 'FOLLOW_TREND':
                cp_dir = inf_direction

            if cp_dir == inf_direction:
                key = 'confirmed' if cp.confirmed else 'unconfirmed'
                pattern_bonus_total += params['chart_pattern_same_direction'][key]
                inf.pattern_confirmations.append(cp.name)

                if cp.confirmed and cp.target_atr:
                    inf.pattern_target_atr = cp.target_atr

        # 특별 시너지 조합 체크
        for cs in patterns.candlestick:
            combo_key = (inf.primary_type, cs.name)
            if combo_key in params['special_combos']:
                pattern_bonus_total += params['special_combos'][combo_key]
        for cp in patterns.chart:
            combo_key = (inf.primary_type, cp.name)
            if combo_key in params['special_combos']:
                pattern_bonus_total += params['special_combos'][combo_key]

        # ═══ 상한 적용 (피드백 #5) ═══
        # 보너스는 cap까지만, 감점(음수)은 상한 없이 그대로 적용
        if pattern_bonus_total > 0:
            pattern_bonus_total = min(pattern_bonus_total, cap)
        inf.score += pattern_bonus_total
        inf.pattern_bonus = pattern_bonus_total
```

##### 패턴 분석 결과가 영향을 미치는 곳

```
1. Phase 3 SCAN → 변곡점 점수 보강/감점
2. Phase 3 SCAN → 컨텍스트 판단에 패턴 정보 추가
3. Phase 5 EXECUTE → 차트 패턴 목표가를 TP 계산에 반영
4. Evolver → 패턴별 승률 통계 수집 → 점수 가중치 최적화
```

---

#### 3A 상세: S/R 식별 (Step 3 계승, ATR 정규화)

```python
SR_PARAMS = {
    # 강도 계산 가중치 (Evolver가 동적 조정)
    "strength_weights": {
        "touch_count": 0.45,   # Step 3: 0.50
        "avg_volume":  0.30,   # Step 3: 0.25
        "recency":     0.25    # Step 3: 0.25
    },

    # 클러스터링: 체제별 고정값 → ATR 기반 통일
    # 기존: RANGE_BOUND=0.3%, VOLATILE=1.0%
    # v5.0: ATR의 N배로 통일 (시장에 자동 적응)
    "cluster_atr_ratio": 0.5,

    # 최대 레벨 수
    "max_levels": 5,

    # 최소 강도 (이하 무시)
    "min_strength": 0.35,

    # 백테스트 기반 반응률 최소 기준
    "min_reaction_rate": 0.50,  # 50% 이상 반응한 레벨만

    # 허위 돌파 감지 (Step 3 계승)
    "false_breakout_window": 3,          # 캔들
    "false_breakout_return_atr": 0.5,    # ATR의 0.5배 이내 복귀
    "false_breakout_vol_drop": 0.30,     # 볼륨 30% 감소
    "false_breakout_confidence_threshold": 0.70,
}
```

#### 3C 상세: 변곡점 7가지 Type (Step 4 계승 + 고도화)

```python
INFLECTION_TYPES = {
    # Type 1~7 유지 (Step 4에서 검증된 체계)
    "T1_SR_REACTION":        {"priority": 3, "max_base": 30},
    "T2_TRENDLINE_REACTION": {"priority": 4, "max_base": 25},
    "T3_BREAKOUT_RETEST":    {"priority": 2, "max_base": 30},
    "T4_TRENDLINE_BREAK":    {"priority": 1, "max_base": 30},
    "T5_POC_MAGNET":         {"priority": 5, "max_base": 20},
    "T6_DIVERGENCE":         {"priority": 6, "max_base": 20},
    "T7_VOLUME_EXPLOSION":   {"priority": 7, "max_base": 15},

    # [NEW] T8: 펀딩/OI 기반 (Hyperliquid 네이티브)
    "T8_FUNDING_OI_SIGNAL":  {"priority": 5, "max_base": 20},

    # [NEW] T9: 차트 패턴 기반 (v5.0 패턴 분석)
    "T9_CHART_PATTERN":      {"priority": 2, "max_base": 25},
    # 확인된 차트 패턴(H&S, Double Top/Bottom, Flag 등)이
    # 독립적으로 변곡점을 생성. T1~T8과 Secondary로 조합 가능.
}
```

**[NEW] T8: 펀딩/OI 시그널**
```python
def detect_t8_funding_oi(candle, stats, atr):
    """
    Hyperliquid 고유 데이터를 활용한 변곡점 감지.
    기존 Step 0~7에는 없던 로직.
    """
    score = 0

    # 펀딩비 극단 역방향 (군중 반대 베팅)
    if candle.funding_rate > stats.funding_p95:   # 극단 양수(롱 과다)
        score += 15  # SHORT 시그널
        direction = 'SHORT'
    elif candle.funding_rate < stats.funding_p5:  # 극단 음수(숏 과다)
        score += 15  # LONG 시그널
        direction = 'LONG'

    # OI 급증 + 가격 정체 = 스퀴즈 임박
    if candle.oi_change > stats.oi_change_p90 and abs(candle.price_change) < atr * 0.3:
        score += 10

    # 청산 집중 가격대 접근 (청산 자석 효과)
    if distance_to_liquidation_cluster(candle.close) < atr * 1.5:
        score += 8

    return score, direction
```

#### 변곡점 스코어링 (Step 4 고도화)

```python
INFLECTION_PARAMS = {
    # 합격 기준: 동적 (PF에 따라 조정)
    "min_score": 65,  # 기존 60에서 상향 (기본)
    "min_score_range": [55, 85],  # Evolver 조정 범위

    # 체제별 Type 가중치 (Step 4 매트릭스 계승, Evolver가 최적화)
    "regime_type_weights": {
        "STRONG_UPTREND":   {"t1":0.80,"t2":0.90,"t3":0.95,"t4":0.60,"t5":0.50,"t6":0.85,"t7":0.30,"t8":0.70,"t9":0.85},
        "WEAK_UPTREND":     {"t1":0.85,"t2":0.75,"t3":0.80,"t4":0.50,"t5":0.55,"t6":0.70,"t7":0.25,"t8":0.65,"t9":0.75},
        "SIDEWAYS":         {"t1":0.90,"t2":0.30,"t3":0.40,"t4":0.20,"t5":0.70,"t6":0.50,"t7":0.15,"t8":0.80,"t9":0.70},
        "WEAK_DOWNTREND":   {"t1":0.80,"t2":0.70,"t3":0.75,"t4":0.85,"t5":0.50,"t6":0.80,"t7":0.40,"t8":0.65,"t9":0.75},
        "STRONG_DOWNTREND": {"t1":0.70,"t2":0.80,"t3":0.90,"t4":0.95,"t5":0.40,"t6":0.90,"t7":0.50,"t8":0.70,"t9":0.85},
        "VOLATILE":         {"t1":0.20,"t2":0.10,"t3":0.15,"t4":0.10,"t5":0.15,"t6":0.10,"t7":0.05,"t8":0.40,"t9":0.30},
    },

    # 점수 구성 가중치 (Evolver가 조정)
    "score_composition": {
        "base":           0.22,
        "distance":       0.13,
        "confirmation":   0.18,
        "context":        0.12,
        "secondary":      0.08,
        "mtf":            0.08,
        "funding_oi":     0.05,  # T8
        "pattern":        0.14,  # [NEW] 캔들스틱+차트패턴 시너지
    },

    # MTF 수렴 보너스 (Step 4 계승)
    "mtf_bonus": {
        "A": +15, "B": +10, "C": +5, "D": -5, "F": -15, "NONE": 0
    },

    # T7 단독 제한 유지 (Step 4 계승)
    "t7_solo_cap": 50,

    # Secondary Type 조합 보너스 (Step 4 상위 12개 계승)
    "combo_bonus": {
        ("T4","T7"): 15, ("T3","T7"): 15, ("T1","T7"): 12,
        ("T2","T7"): 10, ("T4","T6"): 12, ("T3","T2"): 12,
        ("T1","T6"): 10, ("T1","T2"):  8, ("T3","T6"):  8,
        ("T1","T8"): 10, ("T8","T3"): 10, ("T8","T6"): 12,  # [NEW]
    },
}
```

---

### Phase 4: GATE (MDD/PF 중심 리스크 검증)

**기존 Step 5에서 가져온 것**: 10가지 검증 지표, 체제별 합격 기준
**고도화**: MDD 5단계 정책 + PF 동적 기준 + 검증 지표 8개로 정비

```python
def phase4_gate(candles, scan_result, regime_result, safety_result,
                agent_config, agent_state, params):
    """
    시그널이 정말 안전한지 최종 검증. MDD/PF가 1순위.
    agent_config: 에이전트 프로파일 (id, symbol, timeframes 등)
    ~40ms
    """

    # ━━━━ 4A: Safety action 강제 반영 ━━━━
    if safety_result.action == 'CLOSE_ALL_AND_HALT':
        return GateResult(passed=False, reason='MDD_EMERGENCY')
    if safety_result.action == 'BLOCK_NEW':
        return GateResult(passed=False, reason=safety_result.reason)

    mdd_mode = safety_result.mdd_mode
    mdd_policy = params['mdd_control']['policies'][mdd_mode]

    # ━━━━ 4B: 합격 기준 동적 산출 ━━━━
    base = params['base_pass_scores'][regime_result.regime]

    # MDD 조정 (키 이름 = MDD_POLICIES와 동일: score_adj)
    mdd_adj = mdd_policy.get('score_adj', 0)

    # PF 조정 (롤링 PF 기반)
    rolling_pf = agent_state.rolling_pf
    pf_adj = get_pf_adjustment(rolling_pf, params['pf_control'])

    pass_score = clamp(base + mdd_adj + pf_adj, 45, 95)

    # ━━━━ 4B-1: base_size_mult 산출 (MDD 정책 기반) ━━━━
    base_leverage_mult = mdd_policy.get('leverage_mult', 1.0)
    base_size_mult = mdd_policy.get('size_mult', 1.0)

    # ━━━━ 4B-2: 포트폴리오 노출 체크 (피드백 #1 반영) ━━━━
    # Guardian의 1시간 주기 체크만으로는 부족.
    # 진입 시점에 다른 에이전트의 포지션을 실시간으로 확인한다.
    # [피드백] notional이 아닌 open_risk(notional × sl_pct) 기준으로 평가
    planned_notional = estimate_position_size(agent_state, base_size_mult, scan_result)
    exposure_result = evaluate_portfolio_exposure(
        agent_id=agent_config.id,
        symbol=scan_result.symbol if hasattr(scan_result, 'symbol') else agent_config.symbol,
        direction=scan_result.implied_direction,
        size_usd=planned_notional,
        params=params['exposure']
    )

    if exposure_result.blocked:
        return GateResult(passed=False, reason=exposure_result.reason)

    # 노출 초과 시 사이즈 축소 (차단 아닌 경우)
    final_size_mult = base_size_mult * exposure_result.size_multiplier

    # ━━━━ 4C: 기술 지표 검증 (8가지, Step 5의 10가지에서 정비) ━━━━
    score = 0
    score += evaluate_rsi(candles, regime_result, params)           # 0~20
    score += evaluate_ichimoku(candles, regime_result, params)      # 0~15
    score += evaluate_adx_di(candles, regime_result, params)        # 0~10
    score += evaluate_volume_cvd(candles, regime_result, params)    # 0~10
    score += evaluate_htf_consensus(regime_result, params)          # 0~15
    score += evaluate_liquidation_risk(candles, params)             # 0~10
    score += evaluate_funding_alignment(candles, scan_result, params) # 0~10 [NEW]
    score += evaluate_atr_regime(candles, params)                   # 0~10
    # 총 100점 만점

    # ━━━━ 4D: 변곡점/MTF 보정 (Step 5 계승) ━━━━
    if scan_result.score >= 90:  score += 5
    elif scan_result.score >= 80: score += 3
    elif scan_result.score < 70:  score -= 5

    mtf_mod = {"A":+3, "B":+2, "C":+1, "D":-1, "F":-3, "NONE":0}
    score += mtf_mod.get(scan_result.mtf_grade, 0)

    # 페널티 (Step 5 계승, 상한 -30)
    # [피드백 #2] 고정 임계값 → 최근 7일 분포 percentile 기반으로 변환
    # [피드백 #5] 페널티 = "실행 리스크"로만 제한
    #   펀딩 극단은 Phase 2(DNA), Phase 3(T8)에서 이미 기회/위험으로 반영.
    #   Gate에서는 "체결이 불리한 상황"만 페널티 = 스프레드, 유동성, 전환 불안정
    #   펀딩 방향성 자체는 Gate 페널티에서 제외 (중복 반영 방지)
    stats = agent_state.market_stats_7d
    penalty = 0
    # 스프레드 폭발 = 체결 슬리피지 위험 (실행 리스크)
    if candles[-1].bid_ask_spread > stats.spread_p90: penalty -= 8
    # 호가 유동성 부족 = 체결 불리 (실행 리스크)
    if candles[-1].orderbook_imbalance > stats.imbalance_p90: penalty -= 5
    # 체제 전환 중 불안정 (판단 리스크)
    if regime_result.in_transition and regime_result.blend_progress < 0.5: penalty -= 10
    penalty = max(penalty, -30)
    score += penalty

    final_score = clamp(score, 0, 100)

    # ━━━━ 4E: 최종 판정 ━━━━
    passed = final_score >= pass_score

    return GateResult(
        passed=passed,
        reason=None if passed else f'SCORE_{final_score}<{pass_score}',
        score=final_score,
        pass_threshold=pass_score,
        mdd_mode=mdd_mode,
        leverage_mult=base_leverage_mult,
        size_mult=final_size_mult,       # MDD + exposure 축소 반영
        rolling_pf=rolling_pf
    )
```

#### MDD 5단계 정책 (v4.0에서 계승)

```python
MDD_POLICIES = {
    "normal":    {"range":[0.00,0.03], "leverage_mult":1.0, "size_mult":1.0, "score_adj":0},
    "caution":   {"range":[0.03,0.05], "leverage_mult":0.7, "size_mult":0.7, "score_adj":+5},
    "defensive": {"range":[0.05,0.08], "leverage_mult":0.4, "size_mult":0.5, "score_adj":+15,
                  "allowed_regimes":["STRONG_UPTREND","STRONG_DOWNTREND"]},
    "survival":  {"range":[0.08,0.10], "leverage_mult":0.2, "size_mult":0.3, "score_adj":+25,
                  "max_positions":1},
    "emergency": {"range":[0.10,1.00], "action":"CLOSE_ALL_AND_HALT", "halt_hours":24},
}
```

#### PF 기반 합격 기준 조정

```python
PF_ADJUSTMENTS = {
    "above_2.5": -5,    # PF 좋으면 기준 완화
    "2.0_to_2.5": 0,    # 목표 구간
    "1.5_to_2.0": +5,   # 부족
    "1.0_to_1.5": +10,  # 나쁨
    "below_1.0":  +15,  # 손실 중 (기존 +20 → +15로 완화)
}

# [피드백] PF 기반 거래 정지 루프 방지
# PF가 낮아져서 기준이 올라감 → 거래 감소 → 표본 부족 → PF 회복 안 됨
PF_ANTI_STALL = {
    # 최소 거래 빈도: PF<1이어도 하루 최소 N건은 "소액 탐색" 허용
    "min_daily_trades": 2,             # PF<1이어도 최소 2건/일
    "exploration_size_mult": 0.3,      # 탐색 모드 시 사이즈 ×0.3
    # PF 조정은 threshold보다 size_mult/leverage_mult로 밀기
    # → 진입은 되지만 작게. 경험 축적 + 리스크 제한 양립
    "prefer_size_over_threshold": True,
}
```

#### 포트폴리오 노출 체크 (피드백 #1: 에이전트 간 충돌 사전 방지)

```python
def evaluate_portfolio_exposure(agent_id, symbol, direction, size_usd, params):
    """
    다른 에이전트의 활성 포지션을 실시간으로 읽어서
    같은 심볼/같은 방향 노출이 과도한지 사전 체크.

    Guardian의 1시간 주기 사후 체크와 별개로,
    진입 시점에 즉시 확인 (~5ms, SQLite 쿼리).

    [피드백] notional이 아닌 open_risk(notional × sl_pct) 기준으로 평가.
    $50,000 notional + SL 0.5% = 리스크 $250
    $10,000 notional + SL 5% = 리스크 $500
    → 후자가 실질적으로 더 위험하다.
    """
    all_positions = get_all_active_positions()  # SQLite에서 조회

    # open_risk 기준으로 합산 (notional × sl_pct)
    same_risk = sum(
        p.size_usd * p.sl_pct for p in all_positions
        if p.symbol == symbol and p.direction == direction and p.agent_id != agent_id
    )

    hedge_risk = sum(
        p.size_usd * p.sl_pct for p in all_positions
        if p.symbol == symbol and p.direction != direction and p.agent_id != agent_id
    )

    # 신규 주문의 예상 리스크
    planned_sl_pct = params.get('estimated_sl_pct', 0.02)  # Phase 5 전이므로 추정값
    new_risk = size_usd * planned_sl_pct

    net_risk = same_risk - hedge_risk + new_risk
    total_capital = get_total_capital()
    risk_pct = net_risk / total_capital

    if risk_pct > params['hard_block_pct']:
        return ExposureResult(blocked=True,
            reason=f'EXPOSURE_RISK_{risk_pct:.1%}>{params["hard_block_pct"]:.0%}')

    if risk_pct > params['reduce_pct']:
        reduction = 1.0 - (risk_pct - params['reduce_pct']) / (params['hard_block_pct'] - params['reduce_pct'])
        return ExposureResult(blocked=False, size_multiplier=max(reduction, 0.3))

    return ExposureResult(blocked=False, size_multiplier=1.0)


EXPOSURE_PARAMS = {
    # [피드백] hard_block을 50%→30%로 강화 (MDD 10% 목표와 일치시키기 위해)
    # BTC가 2% 역행 시 자본의 30% notional이면 MDD ~0.6%. 30%가 안전한 상한.
    "hard_block_pct": 0.30,   # 한 심볼 한 방향 open_risk 30% 이상 → 차단
    "reduce_pct":     0.20,   # 한 심볼 한 방향 open_risk 20% 이상 → 사이즈 축소
    "min_size_mult":  0.30,   # 축소 하한 (30%까지만 줄임)
    "estimated_sl_pct": 0.02, # Phase 4에서의 SL 추정값 (Phase 5 전이므로)
}
```

#### 검증 지표 8가지 (Step 5의 10가지에서 정비)

```
Step 5 → v5.0 매핑:
  RSI           → 유지 (0~20점)
  Ichimoku      → 유지 (0~15점), 배점 20→15
  Entropy       → 제거 (Phase 2 DNA에서 이미 반영)
  ADX           → 유지 (0~10점)
  볼륨/CVD      → 유지 (0~10점)
  HTF 합의      → 유지 (0~15점)
  청산 압력     → 유지 (0~10점)
  MFI           → 제거 (RSI와 중복성 높음)
  ATR           → 유지 (0~10점)
  다이버전스    → Phase 3 T6에서 이미 반영, 제거

  [NEW] 펀딩 정합성 → 추가 (0~10점)

  총 8가지, 100점 만점
```

---

### Phase 5: EXECUTE (출구 전략 + 레버리지 + 주문 실행)

**기존 Step 6 + Step 7을 통합**
**고도화**: MDD 기반 SL 타이트닝, 에이전트별 차별화된 출구 전략

```python
def phase5_execute(scan_result, gate_result, regime_result, safety_result,
                    agent_config, agent_state, params):
    """
    합격한 시그널의 출구 전략 설계 + 레버리지 계산 + Hyperliquid 주문.
    safety_result: Stage 정보 + volatility_override
    ~40ms (주문 실행 제외)
    """
    atr = scan_result.atr
    mdd_mode = gate_result.mdd_mode

    # ━━━━ 5A: 방향 결정 ━━━━
    direction = determine_direction(regime_result, scan_result)

    # ━━━━ 5B: 손절가 계산 (Step 6 계승, MDD 타이트닝) ━━━━
    # [피드백] ATR 급변 시 보조 변동성 지표 사용
    # volatility_override는 SafetyResult에서 가져온다 (agent_state가 아닌)
    effective_atr = atr
    if safety_result.volatility_override:
        effective_atr = max(atr, safety_result.volatility_override * 0.7)

    raw_sl = calculate_stop_loss(
        direction=direction,
        price=scan_result.entry_price,
        sr_levels=scan_result.sr_levels,
        trendlines=scan_result.trendlines,
        atr=effective_atr,
        regime=regime_result.regime,
        params=params['stop_loss']
    )

    # MDD 기반 타이트닝 (v5.0 핵심)
    sl_tighten = params['stop_loss']['mdd_tighten'][mdd_mode]
    sl_distance = abs(scan_result.entry_price - raw_sl) * sl_tighten
    stop_loss = (scan_result.entry_price - sl_distance if direction == 'LONG'
                 else scan_result.entry_price + sl_distance)

    # 최대 손실 제한 (에이전트별)
    max_loss_pct = params['stop_loss']['max_loss_pct']
    stop_loss = enforce_max_loss(stop_loss, scan_result.entry_price, direction, max_loss_pct)

    # ━━━━ 5C: 익절가 계산 (Step 6 계승, 에이전트별 차별화) ━━━━
    take_profits = calculate_take_profits(
        direction=direction,
        price=scan_result.entry_price,
        sr_levels=scan_result.sr_levels,
        trendlines=scan_result.trendlines,
        vol_profile=scan_result.vol_profile,
        atr=atr,
        regime=regime_result.regime,
        sl_distance=sl_distance,
        pattern_target_atr=scan_result.pattern_target_atr,  # [피드백] 패턴 목표가 반영
        params=params['take_profit']
    )
    # [피드백] TP 패턴 목표가 반영 규칙:
    # 1. pattern_target_atr가 존재하면 → TP 후보에 추가
    # 2. RR 기반 TP와 패턴 목표가 중 더 보수적인(가까운) 것이 TP1
    # 3. 더 공격적인(먼) 것이 TP2 또는 TP3
    # 4. 패턴 목표가가 RR 기반 TP1보다 가까우면 → RR TP를 우선 (패턴은 참고만)
    # 5. 패턴 목표가가 RR 기반 TP3보다 멀면 → RR 기준 유지 (과도한 TP 방지)

    # ━━━━ 5D: 레버리지 계산 (Step 7 7단계 → 6단계 정비) ━━━━
    leverage = calculate_leverage(
        regime=regime_result,
        gate=gate_result,
        safety=safety_result,
        scan=scan_result,
        agent_state=agent_state,
        params=params['leverage']
    )

    # ━━━━ 5E: 포지션 사이징 (피드백 #3-1: notional vs margin 명확화) ━━━━
    #
    # 용어 정의:
    #   R = 이 거래에서 허용하는 최대 손실 (USD)
    #   sl_pct = 진입가 대비 SL 거리 (%)
    #   notional = 포지션 노셔널 (USD, 레버리지 적용 전 기준)
    #   margin = 실제 증거금 (USD) = notional / leverage
    #
    # Hyperliquid API: 주문 size는 "asset 수량"이므로
    #   order_qty = notional / entry_price
    #
    R = agent_state.capital * params['risk_per_trade']  # 자본의 1~2%
    R *= gate_result.size_mult  # MDD + 노출 축소 배수 적용
    sl_pct = abs(scan_result.entry_price - stop_loss) / scan_result.entry_price
    notional_usd = R / sl_pct                # 레버리지 무관. "SL 찍히면 R만큼 잃는" 크기.
    margin_usd = notional_usd / leverage     # 실제 증거금
    order_qty = notional_usd / scan_result.entry_price

    # ━━━━ 5E-2: Open Risk Budget 체크 (피드백 #3-2) ━━━━
    # 현재 열려있는 포지션들의 "SL까지 잠재 손실" 합산
    open_risk = calculate_open_risk(agent_config.id)  # Σ(notional_i × sl_pct_i)
    new_risk = notional_usd * sl_pct
    max_open_risk = agent_state.capital * params['max_open_risk_pct']  # 자본의 3~5%

    if open_risk + new_risk > max_open_risk:
        # 예산 초과 → 사이즈 축소 (차단이 아닌 축소)
        available = max(max_open_risk - open_risk, 0)
        if available < new_risk * 0.3:  # 가용 예산이 30% 미만이면 차단
            return None  # 시그널 포기
        reduction = available / new_risk
        notional_usd *= reduction
        margin_usd = notional_usd / leverage
        order_qty = notional_usd / scan_result.entry_price

    # ━━━━ 5F: 시그널 생성 ━━━━
    signal = Signal(
        signal_id=generate_signal_id(              # 피드백 #7-2: 중복 방지
            agent_config.symbol, agent_config.timeframes,
            candle_timestamp, direction, scan_result.primary_type),
        agent_id=agent_config.id,
        symbol=agent_config.symbol,
        direction=direction,
        entry_price=scan_result.entry_price,
        stop_loss=stop_loss,
        take_profits=take_profits,
        leverage=leverage,
        notional_usd=notional_usd,                # 노셔널 (포지션 크기)
        margin_usd=margin_usd,                     # 증거금
        regime=regime_result.regime,
        confidence=regime_result.confidence,
        inflection_type=scan_result.primary_type,
        inflection_score=scan_result.score,
        validation_score=gate_result.score,
        mdd_mode=mdd_mode,
        pattern_confirmations=scan_result.patterns.confirmation_names,  # list[str]로 고정
        timestamp=candle_timestamp,
    )

    # ━━━━ 5G: 주문 실행 ━━━━
    order_result = execute_on_hyperliquid(signal)

    # ━━━━ 5H: 기록 ━━━━
    save_trade(signal, order_result)
    notify_telegram(signal, order_result)

    return signal
```

#### 에이전트별 출구 전략 차별화

```python
AGENT_EXIT_PROFILES = {
    "s1": {  # 5m 스캘퍼: 빠른 익절, 타이트한 손절
        "tp_levels": 2,                     # TP 2단계만
        "tp1_rr": [1.0, 1.5],              # RR 1.0~1.5 (빠르게)
        "tp2_rr": [1.5, 2.5],
        "split_ratios": [60, 40],           # 60/40 (빠른 확보 우선)
        "trailing_atr_mult": 0.8,           # 타이트한 트레일링
        "max_hold_candles": 36,             # 5m × 36 = 3시간 최대
        "max_loss_pct": 0.02,               # 단일 거래 최대 2%
        "risk_per_trade": 0.01,             # 자본의 1% 리스크
    },
    "s2": {  # 5m+15m 단기: 중간
        "tp_levels": 2,
        "tp1_rr": [1.2, 1.8],
        "tp2_rr": [2.0, 3.0],
        "split_ratios": [55, 45],
        "trailing_atr_mult": 1.0,
        "max_hold_candles": 96,             # 5m × 96 = 8시간
        "max_loss_pct": 0.025,
        "risk_per_trade": 0.015,
    },
    "s3": {  # 5m+15m+1h 스윙: 넓은 손절, 높은 RR
        "tp_levels": 3,
        "tp1_rr": [1.2, 2.0],
        "tp2_rr": [2.0, 3.5],
        "tp3_rr": [3.5, 5.0],
        "split_ratios": [35, 35, 30],
        "trailing_atr_mult": 1.3,
        "max_hold_candles": 288,            # 5m × 288 = 24시간
        "max_loss_pct": 0.03,
        "risk_per_trade": 0.02,
    },
    "s4": {  # Full MTF 포지션: 가장 넓은 손절, 가장 높은 RR
        "tp_levels": 3,
        "tp1_rr": [1.5, 2.5],
        "tp2_rr": [2.5, 4.0],
        "tp3_rr": [4.0, 6.0],
        "split_ratios": [30, 35, 35],
        "trailing_atr_mult": 1.5,
        "max_hold_candles": 1008,           # 5m × 1008 = 3.5일
        "max_loss_pct": 0.035,
        "risk_per_trade": 0.02,
    },
}
```

#### 레버리지 6단계 (Step 7의 7단계에서 정비)

```python
def calculate_leverage(regime, gate, safety, scan, agent_state, params):
    """Step 7의 7단계를 6단계로 정비"""

    # Step 1: 체제 × 확신도 기본값
    lev = params['table'][regime.regime][confidence_tier(regime.confidence)]

    # Step 2: 변곡점 점수 조정
    if scan.score >= 90:   lev *= 1.25
    elif scan.score >= 80: lev *= 1.10
    elif scan.score < 70:  lev *= 0.85

    # Step 3: Stage 강제 제한
    # STAGE_1/2는 Phase 1에서 이미 차단되므로 여기까지 도달 안 함
    if safety.stage == 'STAGE_3': lev = min(lev, 3)

    # Step 4: 변동성(ATR) 조정 (Step 7 계승)
    atr_ratio = scan.atr / agent_state.avg_atr_7d
    if atr_ratio >= 2.0:   lev *= 0.5
    elif atr_ratio >= 1.5: lev *= 0.7

    # Step 5: MDD 배수 적용 (v5.0 핵심)
    lev *= gate.leverage_mult

    # Step 6: 연속 손실 감쇠 (Step 7 계승)
    streak = agent_state.consecutive_losses
    decay = [1.0, 0.85, 0.65, 0.45, 0.25]
    lev *= decay[min(streak, 4)]

    # 범위 제한
    lev = max(1, min(lev, params['max_leverage']))

    return round(lev, 1)
```

**에이전트별 레버리지 프로파일:**

```python
AGENT_LEVERAGE = {
    "s1": {
        "table": {
            "STRONG_UPTREND":   {"high": 5, "medium": 3, "low": 2},
            "WEAK_UPTREND":     {"high": 3, "medium": 2, "low": 1},
            "SIDEWAYS":         {"high": 2, "medium": 1, "low": 1},
            "WEAK_DOWNTREND":   {"high": 3, "medium": 2, "low": 1},
            "STRONG_DOWNTREND": {"high": 5, "medium": 3, "low": 2},
            "VOLATILE":         {"high": 1, "medium": 1, "low": 1},
        },
        "max_leverage": 5,
        "confidence_thresholds": {"high": 0.80, "medium": 0.60},
    },
    "s2": {
        "table": {
            "STRONG_UPTREND":   {"high": 7, "medium": 4, "low": 2},
            "WEAK_UPTREND":     {"high": 4, "medium": 3, "low": 2},
            "SIDEWAYS":         {"high": 3, "medium": 2, "low": 1},
            "WEAK_DOWNTREND":   {"high": 4, "medium": 3, "low": 2},
            "STRONG_DOWNTREND": {"high": 7, "medium": 4, "low": 2},
            "VOLATILE":         {"high": 1, "medium": 1, "low": 1},
        },
        "max_leverage": 7,
        "confidence_thresholds": {"high": 0.80, "medium": 0.60},
    },
    "s3": {
        "table": {
            "STRONG_UPTREND":   {"high": 8, "medium": 5, "low": 3},
            "WEAK_UPTREND":     {"high": 5, "medium": 3, "low": 2},
            "SIDEWAYS":         {"high": 3, "medium": 2, "low": 1},
            "WEAK_DOWNTREND":   {"high": 5, "medium": 3, "low": 2},
            "STRONG_DOWNTREND": {"high": 8, "medium": 5, "low": 3},
            "VOLATILE":         {"high": 1, "medium": 1, "low": 1},
        },
        "max_leverage": 8,
        "confidence_thresholds": {"high": 0.85, "medium": 0.65},
    },
    "s4": {
        "table": {
            "STRONG_UPTREND":   {"high": 6, "medium": 4, "low": 2},
            "WEAK_UPTREND":     {"high": 4, "medium": 3, "low": 2},
            "SIDEWAYS":         {"high": 2, "medium": 1, "low": 1},
            "WEAK_DOWNTREND":   {"high": 4, "medium": 3, "low": 2},
            "STRONG_DOWNTREND": {"high": 6, "medium": 4, "low": 2},
            "VOLATILE":         {"high": 1, "medium": 1, "low": 1},
        },
        "max_leverage": 6,   # Full MTF는 보유 기간이 길어서 상한 낮음
        "confidence_thresholds": {"high": 0.85, "medium": 0.65},
    },
}
```

#### Trailing Stop 로직 (피드백 반영)

```python
# [피드백] trailing_atr_mult가 정의되어있지만 실행 로직이 없었음
# Trailing Stop은 수익에 직접 영향을 주는 핵심 로직

TRAILING_STOP_RULES = {
    # Trailing 시작 조건
    "activation": {
        "method": "tp1_hit",   # TP1 히트 후 trailing 시작
        # 대안: "profit_pct": 진입가 대비 N% 수익 도달 시
    },

    # Trailing 이동 규칙
    "movement": {
        "step": "close",       # 매 캔들 close 시 업데이트
        "distance": "trailing_atr_mult × ATR",  # 에이전트별 다름
        "direction": "only_favorable",  # SL은 유리한 방향으로만 이동
    },

    # 에이전트별 trailing 프로파일
    "profiles": {
        "s1": {
            "activation": "tp1_hit",  # TP1 히트 후
            "trailing_atr_mult": 0.8, # 타이트 (빠른 확보)
            "min_profit_lock": 0.3,   # 최소 RR 0.3 확보 후 trailing 시작
        },
        "s2": {
            "activation": "tp1_hit",
            "trailing_atr_mult": 1.0,
            "min_profit_lock": 0.5,
        },
        "s3": {
            "activation": "tp1_hit",
            "trailing_atr_mult": 1.3, # 넓게 (스윙 유지)
            "min_profit_lock": 0.5,
        },
        "s4": {
            "activation": "tp1_hit",
            "trailing_atr_mult": 1.5, # 가장 넓게 (포지션 유지)
            "min_profit_lock": 0.8,
        },
    },
}

# Trailing Stop 실행 흐름:
# 1. 진입 → SL/TP 설정 (Hyperliquid conditional order)
# 2. TP1 히트 → 잔여 포지션에 trailing 시작
# 3. 매 캔들 close 시 → trailing SL을 current_close - trailing_distance로 갱신
# 4. trailing SL은 유리한 방향으로만 이동 (불리하게 이동 안 함)
# 5. trailing SL 히트 → 잔여 포지션 전량 청산
```

#### 포지션 관리 루프 (피드백 반영: 빠진 항목)

```python
# [피드백] Pipeline이 5분마다 돌지만, 진입 후 SL/TP 도달 전에
# 시장이 급변하면? Trailing stop 업데이트, 부분 청산 등의
# "인터벌 관리 루프"가 별도로 필요하다.

POSITION_MANAGER = {
    # 실행 주기: 5초마다 (Pipeline과 독립적)
    "interval_sec": 5,

    # 수행 작업:
    "tasks": [
        # 1. Trailing Stop 업데이트 (최신 캔들 close 기준)
        "update_trailing_stops",

        # 2. SL/TP conditional order 상태 확인
        #    → Hyperliquid에서 실제 체결되었는지 확인
        #    → 체결 시 DB 업데이트 + Telegram 알림
        "check_order_fills",

        # 3. 포지션 timeout 체크
        #    → max_hold_candles 초과 시 시장가 청산
        "check_position_timeout",

        # 4. 긴급 SL 축소 (Stage 변경 시)
        #    → Safety Stage 악화 시 기존 포지션 SL 타이트닝
        "emergency_sl_tighten",

        # 5. reduce-only 확인
        #    → TP 분할 청산 시 실수로 반대 포지션 열리는 사고 방지
        #    → 모든 TP/SL 주문은 reduce-only 플래그 필수
        "verify_reduce_only",
    ],
}

# SL/TP 주문 정책:
# - 모든 SL/TP는 reduce-only (반대 포지션 열림 방지)
# - Hyperliquid SL/TP가 서버사이드인지 확인 필수
#   → 서버사이드: 봇이 죽어도 SL 유지 ✅
#   → 클라이언트사이드: heartbeat + 재설정 필요 ⚠️
# - 구현 시 Hyperliquid API 문서에서 확인 후 결정
```

#### 재시작/장애 복구 (피드백 반영: 빠진 항목)

```python
# [피드백] 봇이 죽었다가 살아날 때 열린 포지션 상태를 어떻게 동기화하는가?

RECONCILIATION = {
    # 봇 시작 시 (startup sequence):
    "on_startup": [
        # 1. Hyperliquid user_state 조회 → 실제 열린 포지션 목록
        "fetch_exchange_positions",

        # 2. DB의 positions 테이블과 비교
        "compare_db_vs_exchange",

        # 3. 불일치 처리:
        #    a. Exchange에 있고 DB에 없음 → "고아 포지션"
        #       → DB에 추가 + Telegram 경고 + SL 즉시 설정
        #    b. DB에 있고 Exchange에 없음 → "이미 청산됨"
        #       → DB status='CLOSED' 업데이트
        #    c. 사이즈/방향 불일치 → Telegram 긴급 알림
        "resolve_discrepancies",

        # 4. SL/TP conditional order 재확인/재설정
        "verify_stop_orders",
    ],

    # 운영 중 주기적 reconcile (1시간마다):
    "periodic_interval_hours": 1,
    "periodic_tasks": [
        "fetch_exchange_positions",
        "compare_db_vs_exchange",
        "alert_if_mismatch",
    ],

    # 주문 성공/DB 기록 실패 대비:
    # → signal_id를 Hyperliquid clientOrderId에 설정 (가능한 범위에서)
    # → 주문 직후 exchange order_id를 즉시 저장 (트랜잭션)
    "order_safety": {
        "use_client_order_id": True,
        "save_exchange_order_id_immediately": True,
    },
}
```

#### 구조화 로깅 (피드백 반영: 빠진 항목)

```python
# [피드백] Phase별 결과를 어디에 어떤 형식으로 남기는지 정의가 없다.

LOGGING_CONFIG = {
    # 모든 파이프라인 실행의 Phase별 중간 결과를 JSON으로 저장
    "phase_snapshot": {
        "enabled": True,
        "storage": "SQLite (pipeline_logs 테이블)",
        "fields_per_phase": {
            "phase1": ["severity", "stage", "mdd_mode", "action", "conditions_triggered"],
            "phase2": ["regime", "confidence", "alignment", "dna_components"],
            "phase3": ["found", "primary_type", "score", "mtf_grade", "patterns_detected"],
            "phase4": ["passed", "score", "threshold", "penalties", "exposure_result"],
            "phase5": ["direction", "sl_pct", "leverage", "notional_usd", "signal_id"],
        },
        "retention_days": 30,  # 30일 보관 후 자동 삭제
    },

    # Signal에 phase_snapshot 딕셔너리 포함 (각 거래의 판단 근거 추적)
    "attach_to_signal": True,
}
```

---

## 3.5 Phase별 AI 활용 (GPT-4o Integration)

### 설계 원칙

```
핵심: AI는 실시간 파이프라인(~370ms)에 동기적으로 개입하지 않는다.

이유:
  1. GPT-4o API 호출 = 1~3초 (파이프라인의 3~8배)
  2. 토큰 비용 발생 (매 5분마다 호출하면 $100+/월)
  3. API 장애 시 전체 파이프라인 중단 위험

해결:
  → AI는 "비동기 어드바이저"로 동작
  → 실시간 파이프라인은 Python 규칙 엔진이 100% 실행
  → AI는 주기적으로(1~4시간) 분석/검증/제안
  → AI 결과는 "파라미터 조정"으로 간접 반영
  → AI가 죽어도 파이프라인은 계속 동작

비용 제어:
  → AI 호출 = 4시간 주기 × 7개 에이전트
  → 1회 호출당 ~1000 토큰 (입력 500 + 출력 500)
  → 일 42회 × $0.01 = ~$0.42/일 = ~$13/월
```

### Phase 1: SAFETY — AI 역할: 극단 이벤트 사후 분석

```python
# ═══ 실시간 (Python, ~50ms) ═══
# AI 개입 없음. 규칙 기반이 가장 적합.
# 이유: 극단 상황에서는 속도가 생명. 1초 지연 = 큰 손실.

# ═══ 비동기 AI (4시간 주기) ═══
def ai_safety_review(agent_id):
    """
    Stage 발생 이력을 AI가 사후 분석.
    "이 Stage가 왜 발생했는가? 임계값이 적절한가?"
    """
    prompt = f"""
    최근 24시간 Stage 이벤트:
    {get_stage_events(agent_id, hours=24)}

    현재 SAFETY 파라미터:
    {get_safety_params(agent_id)}

    질문:
    1. Stage 발동이 너무 잦은가? (과민) 또는 너무 드문가? (둔감)
    2. 어떤 조건이 주로 트리거되는가?
    3. 임계값 조정 제안 (percentile 기준으로)

    JSON으로 답변:
    {{"assessment": "적절/과민/둔감",
      "dominant_trigger": "조건명",
      "param_suggestions": {{"param_name": new_value, ...}},
      "reasoning": "근거"}}
    """
    return call_gpt(prompt)

# AI 출력 → Evolver가 수신 → 파라미터 조정에 반영
```

**AI 가치**: Stage 임계값의 과민/둔감 판별. 사람이 로그를 보지 않아도 AI가 자동으로 감시 품질을 평가.

### Phase 2: READ — AI 역할: 체제 전환 컨텍스트 해석

```python
# ═══ 실시간 (Python, ~100ms) ═══
# AI 개입 없음. DNA 계산 + 체제 분류는 수학적 로직.

# ═══ 비동기 AI (4시간 주기) ═══
def ai_regime_analysis(agent_id):
    """
    AI가 현재 체제 분류의 적절성을 평가.
    "지금 WEAK_UPTREND로 분류되는데, 맞는 판단인가?"

    AI가 잘하는 것: 여러 지표를 종합한 정성적 판단
    코드가 잘하는 것: 빠른 수치 계산
    """
    prompt = f"""
    현재 체제 분류 결과:
    {get_regime_snapshot(agent_id)}

    DNA 값:
    - Hurst: {dna.hurst} (추세 지속성)
    - Entropy: {dna.entropy} (불확실성)
    - Funding: {dna.funding} (펀딩비 방향)
    - OI: {dna.oi_momentum} (OI 방향)

    최근 4시간 체제 이력:
    {get_regime_history(agent_id, hours=4)}

    최근 전환 이벤트:
    {get_transition_events(agent_id, hours=8)}

    질문:
    1. 현재 체제 분류가 적절한가?
    2. 전환이 너무 잦은가? (히스테리시스 부족)
    3. DNA 가중치 조정 제안
    4. 현재 시장의 "서사(narrative)"는 무엇인가?
       (예: "4h 상승 추세에서 1h 조정 중, 5m에서 반등 시도")

    JSON으로 답변:
    {{"regime_assessment": "적절/의심",
      "narrative": "시장 서사 1줄",
      "dna_weight_suggestions": {{"hurst": 0.25, ...}},
      "transition_assessment": "적절/과민/둔감",
      "reasoning": "근거"}}
    """
    return call_gpt(prompt)

# AI의 "서사(narrative)"는 Telegram에 게시 → 유저가 시장 상황 파악
# DNA 가중치 제안 → Evolver가 검증 후 반영
```

**AI 가치**: 숫자만으로는 파악하기 어려운 "시장 서사" 해석. 체제 전환의 적절성 평가. DNA 가중치 미세 조정 제안.

### Phase 3: SCAN — AI 역할: 복잡한 패턴 검증 + 변곡점 품질 평가

```python
# ═══ 실시간 (Python, ~150ms) ═══
# AI 개입 없음. S/R, 추세선, 패턴 감지는 수학적 로직.

# ═══ 비동기 AI (4시간 주기) ═══
def ai_scan_review(agent_id):
    """
    AI가 최근 변곡점 감지의 품질을 사후 평가.

    AI가 진짜 잘하는 영역:
    1. "이 S/R 레벨은 진짜 중요한가?"
    2. "이 차트 패턴이 진짜 패턴인가, 노이즈인가?"
    3. "변곡점 점수 70인데 실패했다 → 왜?"
    """
    prompt = f"""
    최근 4시간 변곡점 감지 기록:
    {get_inflection_history(agent_id, hours=4)}

    각 변곡점의 결과 (진입 후 성공/실패):
    {get_inflection_outcomes(agent_id, hours=24)}

    현재 S/R 레벨과 반응률:
    {get_sr_performance(agent_id)}

    최근 패턴 감지 기록:
    {get_pattern_history(agent_id, hours=8)}

    질문:
    1. 어떤 변곡점 Type이 가장 정확한가?
    2. 어떤 Type이 가장 부정확한가? (과다 감지)
    3. S/R 레벨의 품질은? (중요한 레벨을 놓치고 있는가?)
    4. 패턴 감지가 정확한가? (오탐이 많은가?)
    5. 패턴-변곡점 시너지 보너스 조정 제안

    JSON으로 답변:
    {{"best_types": ["T1", "T3"],
      "worst_types": ["T7"],
      "sr_quality": "양호/개선필요",
      "pattern_accuracy": "양호/과다감지/미감지",
      "score_adjustments": {{"T7_solo_cap": 45, ...}},
      "pattern_synergy_adjustments": {{"tier_2_bonus": 10, ...}},
      "reasoning": "근거"}}
    """
    return call_gpt(prompt)

# 핵심: AI가 "어떤 패턴이 이 시장에서 잘 작동하는가"를 판단
# → Evolver가 해당 패턴의 점수 가중치를 조정
```

**AI 가치**: 패턴/변곡점의 사후 품질 평가. "이 시장에서는 T1이 잘 먹히고 T7은 노이즈가 많다" 같은 메타 분석. 규칙 기반 코드로는 하기 어려운 "왜 실패했는가?" 분석.

### Phase 4: GATE — AI 역할: 정성적 리스크 평가

```python
# ═══ 실시간 (Python, ~40ms) ═══
# AI 개입 없음. 지표 검증은 수학적 점수 계산.

# ═══ 비동기 AI (4시간 주기) ═══
def ai_gate_review(agent_id):
    """
    AI가 GATE 합격/불합격 패턴을 분석.
    "합격했는데 손실 난 거래" vs "불합격됐는데 수익 났을 거래"

    이것이 AI 활용의 진정한 가치:
    규칙 기반 시스템은 합격 기준을 수치로만 판단.
    AI는 "합격했어야 하는데 탈락한" 기회를 발견할 수 있음.
    """
    prompt = f"""
    최근 24시간 GATE 결과:
    - 합격 후 수익:   {stats.pass_and_profit} 건
    - 합격 후 손실:   {stats.pass_and_loss} 건
    - 불합격 (진입 안 함): {stats.rejected} 건

    합격 후 손실 거래 상세:
    {get_failed_trades_detail(agent_id, hours=24)}

    불합격 중 사후 수익 가능했던 것:
    {get_missed_opportunities(agent_id, hours=24)}

    현재 합격 기준:
    base_score={params.base}, mdd_adj={params.mdd_adj}, pf_adj={params.pf_adj}

    질문:
    1. 합격 기준이 너무 높은가(기회 놓침)? 너무 낮은가(손실 많음)?
    2. 어떤 지표가 판별력이 높은가?
    3. 어떤 지표가 노이즈인가? (있으나 마나)
    4. MDD/PF 조정 계수의 적절성

    JSON으로 답변:
    {{"threshold_assessment": "적절/높음/낮음",
      "useful_indicators": ["RSI", "HTF_consensus"],
      "weak_indicators": ["ATR_regime"],
      "base_score_suggestion": 70,
      "reasoning": "근거"}}
    """
    return call_gpt(prompt)
```

**AI 가치**: "놓친 기회" 발견. 합격 기준의 적정성을 거래 결과로부터 역추론. 어떤 지표가 실제로 판별력이 있는지 통계적 사후 분석.

### Phase 5: EXECUTE — AI 역할: 출구 전략 리뷰 + 교훈 추출

```python
# ═══ 실시간 (Python, ~40ms) ═══
# AI 개입 없음. SL/TP/레버리지는 수학적 계산.

# ═══ 비동기 AI (4시간 주기) ═══
def ai_execute_review(agent_id):
    """
    AI가 완료된 거래의 출구 전략을 사후 평가.

    핵심 질문: "더 벌 수 있었는데 일찍 나왔는가?"
                "더 빨리 나왔어야 했는가?"
    """
    prompt = f"""
    최근 24시간 완료 거래:
    {get_completed_trades(agent_id, hours=24)}

    각 거래:
    - 진입가, SL, TP1/TP2/TP3
    - 실제 청산가, 청산 사유 (SL/TP1/TP2/TP3/trailing/timeout)
    - 청산 후 가격 움직임 (30분간)

    현재 출구 파라미터:
    {get_exit_params(agent_id)}

    질문:
    1. SL이 너무 타이트한가(자주 찍힘)? 너무 넓은가(손실 큼)?
    2. TP 도달률은? (TP1 100%, TP2 60%, TP3 30% 등)
    3. 청산 후 추가 움직임이 있었다면 → trailing이 너무 타이트?
    4. timeout 청산이 많다면 → 보유 시간이 너무 짧은가?
    5. RR 비율 실측값 vs 설계값

    JSON으로 답변:
    {{"sl_assessment": "적절/타이트/넓음",
      "tp_reach_rates": {{"TP1": 0.85, "TP2": 0.55, "TP3": 0.25}},
      "trailing_assessment": "적절/타이트/넓음",
      "actual_rr": 2.1,
      "param_suggestions": {{"trailing_atr_mult": 1.2, ...}},
      "key_lesson": "요약 1줄",
      "reasoning": "근거"}}
    """
    return call_gpt(prompt)

# key_lesson은 Telegram #s{n}-status에 게시
# param_suggestions는 Evolver에게 전달
```

**AI 가치**: "더 벌 수 있었는데" 분석. 출구 전략의 실측 성능 평가. 각 거래에서 교훈 추출 → 파라미터 개선에 반영.

### AI 활용 종합 플로우

```
┌──────────────────────────────────────────────────────────────────┐
│                   실시간 파이프라인 (~370ms)                      │
│                                                                  │
│  SAFETY → READ → SCAN → GATE → EXECUTE                         │
│  (Python 100%, AI 0%, API비용 $0)                                 │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ 매 5분봉 클로즈마다 자동 실행                              │    │
│  │ 파라미터 JSON 파일 읽기 → 실행 → 주문                     │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                              │
                     결과 (거래 이력)
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                   비동기 AI 리뷰 (4시간 주기)                     │
│                                                                  │
│  Phase 1 AI: Stage 이벤트 사후 분석                               │
│  Phase 2 AI: 체제 분류 적절성 + 시장 서사                         │
│  Phase 3 AI: 변곡점/패턴 품질 평가                                │
│  Phase 4 AI: 합격 기준 적정성 + 놓친 기회                         │
│  Phase 5 AI: 출구 전략 사후 평가 + 교훈                           │
│                                                                  │
│  (GPT-4o, 1회 호출 ~$0.01, 총 ~$13/월)                          │
└──────────────────────────────────┬───────────────────────────────┘
                                   │
                          AI 제안 (JSON)
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                   Evolver Agent                                   │
│                                                                  │
│  AI 제안 수신 → 적합도 함수로 검증 → 파라미터 조정               │
│  → params/{agent_id}/*.json 업데이트                             │
│  → 다음 파이프라인 실행부터 새 파라미터 적용                      │
│                                                                  │
│  안전장치:                                                        │
│  - AI 제안이라도 적합도 함수 통과 필수                             │
│  - 변경 폭 ±20% 제한 유지                                        │
│  - 3회 연속 악화 시 자동 롤백                                     │
│  - MDD/레버리지 상한은 절대 불변                                   │
└──────────────────────────────────────────────────────────────────┘
```

### AI 호출 구조 (피드백 #6: 에이전트당 1콜로 통합)

```
기존 문제: S1~S4가 Phase별로 5번 호출 → 일 120콜, 복잡도 높음
개선: 에이전트당 1콜로 5 Phase를 한 번에 리뷰

에이전트    호출 주기    콜 수/회    일 호출 수    토큰/회
─────────  ─────────  ─────────  ──────────  ────────
S1          4시간       1 통합     6           ~1500
S2          4시간       1 통합     6           ~1500
S3          4시간       1 통합     6           ~2000
S4          4시간       1 통합     6           ~2000
Guardian    1시간       1 통합     24          ~600
Evolver     4시간       1 통합     6           ~1200
Reporter    4시간       1 리포트   6           ~500

일 총: 60콜, ~65,000 토큰
GPT-4o 비용: ~$0.25/일 = ~$8/월

총 AI 비용: ~$8/월 (기존 대비 절반)
```

**Trading Agent의 통합 1콜 구조:**

```python
def ai_unified_review(agent_id):
    """에이전트당 4시간마다 1번. 5 Phase를 한 프롬프트로 리뷰."""
    prompt = f"""
    [에이전트 {agent_id} 4시간 리뷰]

    거래 이력: {get_trades(agent_id, hours=4)}
    현재 상태: {get_agent_snapshot(agent_id)}

    Phase별 진단 요청:
    1. SAFETY: Stage 발동 이력 → 과민/둔감?
    2. READ: 체제 분류 적절성 + 시장 서사
    3. SCAN: 변곡점/패턴 품질 → 어떤 Type이 잘 되고 안 되는가?
    4. GATE: 합격 기준 적정성 → 놓친 기회 vs 잘못된 통과
    5. EXECUTE: SL/TP 사후 평가 → 더 벌 수 있었나?

    JSON으로 답변: (schema 고정)
    """
    result = call_gpt(prompt)

    # JSON schema validation (필수)
    validated = validate_ai_output(result, AI_REVIEW_SCHEMA)

    # 변경폭 제한 (기존 ±20% 유지)
    clamped = clamp_suggestions(validated, max_change_pct=0.20)

    # 파라미터 적용: atomic write + 버전 태그
    apply_params_atomic(agent_id, clamped, version=timestamp)

    return validated
```

**AI 출력 안전장치:**

```python
AI_SAFETY = {
    # 1. JSON schema validation: 필수 필드 누락 → 무시
    "schema_validation": True,

    # 2. 변경폭 제한: 현재 값의 ±20% 이내
    "max_change_pct": 0.20,

    # 3. 적합도 함수 통과 후 반영 (Evolver 검증)
    "fitness_gate": True,

    # 4. 파라미터 파일 atomic write + 버전 태그
    # params/s3/v20260218_140000.json
    "atomic_write": True,
    "version_format": "v{YYYYMMDD}_{HHMMSS}",

    # 5. 3회 연속 악화 → 자동 롤백 (기존 규칙 유지)
    "max_consecutive_degradation": 3,

    # 6. AI 응답 타임아웃: 10초 이내 미응답 → 스킵
    "timeout_sec": 10,

    # 7. AI 장애 시: 마지막 성공 파라미터로 계속 운영
    "fallback": "last_successful_params",
}
```

### AI가 절대 하지 않는 것

```
1. 실시간 의사결정 ❌ (파이프라인 속도 보장)
2. 주문 직접 실행 ❌ (Python Fast Engine이 실행)
3. MDD/레버리지 상한 변경 ❌ (Evolver도 불가)
4. 파라미터 직접 수정 ❌ (제안만, Evolver가 검증 후 적용)
5. 다른 에이전트 중단/시작 ❌ (Guardian만 가능)
```

---

## 4. OpenClaw Agent 구성

### 4.0 "OpenClaw 트레이딩팀"의 정체

이 시스템은 **2-Layer 구조**다.

```
┌──────────────────────────────────────────────────────────────┐
│  Layer 1: Python Fast Engine (트레이딩 실행)                   │
│                                                                │
│  "선수" — 실제 매매를 하는 엔진                                 │
│                                                                │
│  ┌──────────────────────────────────────────┐                 │
│  │ Data Collector (WebSocket → SQLite)       │                 │
│  │        ↓                                  │                 │
│  │ ┌────┐ ┌────┐ ┌────┐ ┌────┐            │                 │
│  │ │ S1 │ │ S2 │ │ S3 │ │ S4 │  Pipeline   │                 │
│  │ │엔진│ │엔진│ │엔진│ │엔진│  instances  │                 │
│  │ └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘            │                 │
│  │    └──────┴──────┴──────┘               │                 │
│  │           ↓                              │                 │
│  │    Hyperliquid API (주문)                 │                 │
│  └──────────────────────────────────────────┘                 │
│                                                                │
│  특징: Python 100%, AI 0%, ~370ms, API비용 $0                  │
│  실행: 5분봉 클로즈마다 자동                                    │
│  파라미터: params/{agent_id}/*.json에서 읽기                    │
└──────────────────────────────────────────────────────────────┘
                           ↕
                   params/*.json (읽기/쓰기)
                   trades.db (읽기/쓰기)
                           ↕
┌──────────────────────────────────────────────────────────────┐
│  Layer 2: OpenClaw Agent Team (AI 관리 계층)                   │
│                                                                │
│  "코칭스태프" — 분석/최적화/보고/리스크 관리                     │
│                                                                │
│  ┌────────────────────────────────────────────────────┐      │
│  │  OpenClaw (GPT-4o via OAuth + Telegram)              │      │
│  │                                                      │      │
│  │  Trading Agents:                                     │      │
│  │  ┌────┐ ┌────┐ ┌────┐ ┌────┐                      │      │
│  │  │ S1 │ │ S2 │ │ S3 │ │ S4 │  4시간마다 복기       │      │
│  │  │ AI │ │ AI │ │ AI │ │ AI │  "왜 졌지?" 분석      │      │
│  │  └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘  파라미터 제안        │      │
│  │     │      │      │      │                          │      │
│  │     └──────┴──────┴──┬───┘                          │      │
│  │                      ↓                               │      │
│  │  Meta Agents:                                        │      │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐          │      │
│  │  │ Guardian │  │ Evolver  │  │ Reporter │          │      │
│  │  │ 리스크   │  │ 최적화   │  │ 보고     │          │      │
│  │  │ 1h+즉시  │  │ 4시간    │  │ 4시간    │          │      │
│  │  └──────────┘  └──────────┘  └──────────┘          │      │
│  └────────────────────────────────────────────────────┘      │
│                                                                │
│  특징: GPT-4o, 비동기, ~$8/월                                  │
│  실행: 1~4시간 주기 + 이벤트 트리거                             │
│  역할: 분석 → 제안 → Evolver 검증 → 파라미터 갱신              │
└──────────────────────────────────────────────────────────────┘
                           ↕
                      Telegram (보고/명령)
                           ↕
                        유저 (나)
```

**왜 이 구조가 "OpenClaw 트레이딩팀"인가:**

```
1. 에이전트가 "팀"으로 동작한다
   → S1~S4: 각자 담당 타임프레임에서 독립적으로 트레이딩
   → Guardian: 전체 팀의 리스크 감독
   → Evolver: 팀 전체의 파라미터 최적화
   → Reporter: 팀 성과를 유저에게 보고

2. agentToAgent 커뮤니케이션
   → S3가 "최근 T1(S/R반응)에서 손실이 많다" → Evolver에게 전달
   → Evolver가 T1 가중치를 조정 → S1~S4 모두에 반영
   → Guardian이 "S2 MDD 초과" → S2에게 거래 중단 명령

3. Telegram = 팀 회의실
   → 각 에이전트가 자기 채널에 상태 보고
   → 유저가 #commands에서 팀에 지시
   → 긴급 상황 시 #alerts로 전원 통지

4. "선수(Python)"와 "코칭스태프(OpenClaw)"의 분리
   → 선수는 빠르게 플레이 (5분마다, 370ms)
   → 코치는 느리게 분석하고 전략 수정 (4시간마다)
   → 코치가 아파도 선수는 계속 뛸 수 있음
```

### 4.1 에이전트-파이프라인 상세 플로우

```
═══════════════════════════════════════════════════════
  실시간 루프 (매 5분봉 클로즈)
═══════════════════════════════════════════════════════

  [Data Collector]
       │ WebSocket → SQLite (WAL mode)
       │ candle close 이벤트 발생
       ▼
  ┌─────────────────────────────────────────────────┐
  │ for agent in [S1_engine, S2_engine, S3_engine, S4_engine]:  │
  │   for symbol in agent.active_symbols:            │
  │                                                   │
  │     params = load_params(agent.id)  ← JSON 파일  │
  │     candles = load_candles(symbol, agent.tfs)     │
  │                                                   │
  │     Phase 1: SAFETY(candles, agent_state, params) │
  │       → blocked? → skip                          │
  │                                                   │
  │     Phase 2: READ(candles, safety, agent_state)   │
  │       → regime = STRONG_UPTREND                  │
  │                                                   │
  │     Phase 3: SCAN(candles, regime, params)         │
  │       → found? → no → skip                       │
  │       → T1_SR_REACTION, score=82, LONG           │
  │                                                   │
  │     Phase 4: GATE(candles, scan, regime, state)    │
  │       → exposure check → ok                      │
  │       → open_risk check → ok                     │
  │       → score=78 ≥ pass=70 → PASS               │
  │                                                   │
  │     Phase 5: EXECUTE(scan, gate, regime, safety)   │
  │       → SL/TP 계산, leverage 계산                 │
  │       → signal_id 생성 (중복 체크)                │
  │       → Hyperliquid에 주문 ← 여기서 실제 매매!   │
  │       → trades.db에 기록                          │
  │       → Telegram #live-trades에 알림              │
  └─────────────────────────────────────────────────┘
                     │
                     │ 370ms 완료, 다음 5분봉까지 대기
                     ▼

═══════════════════════════════════════════════════════
  OpenClaw 에이전트 루프 (비동기, 4시간 주기)
═══════════════════════════════════════════════════════

  ┌─ S1 AI Agent (OpenClaw) ────────────────────────┐
  │  1. trades.db에서 최근 거래 읽기                  │
  │  2. GPT-4o에게 통합 리뷰 요청 (1콜)              │
  │     "SAFETY 과민한가? 체제 적절한가?              │
  │      어떤 Type이 잘 먹히나? 합격기준 적절한가?    │
  │      SL 타이트한가?"                             │
  │  3. AI 응답 (JSON) → Evolver에게 전달            │
  │  4. Telegram #s1-status에 상태 보고               │
  └──────────────────────────────────────────────────┘
       │
       │ agentToAgent (OpenClaw 내장 기능)
       ▼
  ┌─ Evolver Agent (OpenClaw) ──────────────────────┐
  │  1. S1~S4의 제안을 모두 수신                      │
  │  2. 적합도 함수(Sortino × √trades × (1-MDD))로   │
  │     제안의 유효성 검증                            │
  │  3. 검증 통과 → params/*.json 갱신               │
  │     (atomic write + version tag)                 │
  │  4. Telegram #evolution에 변경 기록               │
  │                                                   │
  │  ※ 다음 5분봉 클로즈 시 Pipeline이               │
  │    새 파라미터를 자동으로 읽어서 적용             │
  └──────────────────────────────────────────────────┘

  ┌─ Guardian Agent (OpenClaw) ─────────────────────┐
  │  정기: 1시간마다                                  │
  │  즉시: MDD 0.5% 증가, Stage 발생, open_risk 초과 │
  │                                                   │
  │  [피드백] 디바운싱: 같은 트리거 5분 이내 재발     │
  │  → 무시 (변동성 큰 날 분단위 트리거 방지)        │
  │                                                   │
  │  1. 전체 포트폴리오 MDD 추적                      │
  │  2. 에이전트 간 상관관계/충돌 체크                 │
  │  3. MDD ≥ 10% → 전원 즉시 중단                   │
  │  4. 자본 재배분 (PF 기반)                         │
  │  5. Telegram #alerts에 긴급 알림                   │
  │                                                   │
  │  ※ 중단 명령 = agent_state.halted = True 설정    │
  │    → Pipeline이 Phase 1에서 읽고 즉시 차단       │
  │                                                   │
  │  디바운싱 규칙:                                    │
  │    guardian_debounce = {                           │
  │      "mdd_increase": 300,    # 5분                │
  │      "stage_event": 600,     # 10분               │
  │      "open_risk": 300,       # 5분                │
  │      "daily_loss": 3600,     # 1시간              │
  │    }                                               │
  └──────────────────────────────────────────────────┘

  ┌─ Reporter Agent (OpenClaw) ─────────────────────┐
  │  1. 매 4시간: #dashboard에 포트폴리오 요약        │
  │  2. 매일 00:00: #daily-report에 일일 리포트       │
  │  3. #commands에서 유저 명령 수신                   │
  │     "포지션" → 전체 포지션 조회                   │
  │     "S2 중지" → Guardian에게 전달                 │
  │     "BTC 차단" → 심볼 블랙리스트에 추가           │
  └──────────────────────────────────────────────────┘
```

**핵심 연결고리: params/*.json**

```
이것이 Layer 1(Python)과 Layer 2(OpenClaw)를 연결하는 인터페이스.

Layer 2 (OpenClaw/AI)              Layer 1 (Python Engine)
  Evolver가 params 갱신  ─────→   Pipeline이 params 읽기
  Guardian이 halt flag 설정 ────→  Phase 1이 halt 체크
  Reporter가 trades.db 읽기 ←───  Pipeline이 trades.db 기록

파일 시스템이 두 Layer의 통신 채널.
Redis나 message queue 같은 복잡한 인프라 불필요.

[피드백] params/*.json race condition 방지:
  Evolver가 write 도중 Pipeline이 읽으면 깨진 JSON을 읽을 수 있음.
  → atomic write 구현: write to temp file → os.rename() (POSIX atomic)
  → 패턴: params/s3/params.json.tmp → params/s3/params.json
  → Pipeline은 읽기 실패 시 마지막 성공 params를 사용 (fallback)
  → 또는 params를 SQLite의 별도 테이블로 이관 (더 안전)
```

### 4.2 Trading Agent의 역할 (정리)

각 Trading Agent(S1~S4)는 **2개의 몸체**를 가진다:

```
몸체 1: Python Engine (Fast, 5분마다)
  → 5-Phase Pipeline 자동 실행
  → params/*.json 읽기 → 실행 → 주문
  → OpenClaw/GPT 개입 없음, API비용 $0

몸체 2: OpenClaw Agent (Smart, 4시간마다)
  → GPT-4o가 거래 이력 분석
  → "왜 졌지?", "어떤 패턴이 잘 먹히나?"
  → 파라미터 조정 제안 → Evolver에게 전달
  → Telegram에 상태 보고

비유: 몸체1 = 레이싱카 (빠르게 달림)
      몸체2 = 엔지니어 (피트스톱에서 세팅 조정)
```

### 4.3 Trading Agent SOUL.md (S3 예시)

```markdown
# S3 — 스윙 트레이더 (5m + 15m + 1h)

## 정체성
나는 5분봉, 15분봉, 1시간봉을 조합하여 스윙 트레이딩을 수행하는 에이전트다.
1시간봉이 방향을 잡고, 15분봉이 확인하고, 5분봉이 진입 타이밍을 잡는다.
보유 기간은 수 시간에서 24시간까지.

## 자동 실행 (GPT 개입 없음)
- 5분봉 클로즈마다 5-Phase Pipeline이 자동 실행된다
- 파이프라인은 내 파라미터 파일(params/s3/*.json)을 읽어서 동작한다
- 시그널 발생 시 Hyperliquid에 자동으로 주문이 나간다
- 이 과정에 나(GPT)는 개입하지 않는다

## 내가 하는 일 (4시간마다)
1. trades.db에서 내 최근 거래 이력을 가져온다
2. 손실 거래의 패턴을 분석한다
   - 어떤 체제에서 손실이 많았나?
   - 어떤 변곡점 Type이 실패했나?
   - 손절이 너무 타이트/넉넓했나?
3. 개선 제안을 #s3-analysis 채널에 게시하고, Evolver에게 전달한다

## 자본 배분
전체 자본의 30%를 사용한다.
이 비율은 Guardian이 조정할 수 있다.

## MDD 정책
내 개별 MDD 한도: 5% (전체의 절반)
전체 포트폴리오 MDD 한도: 10%
둘 중 하나라도 위반 시 Guardian의 지시에 따른다.

## 리포트 (Telegram #s3-status)
매 4시간:
  S3 Status | 14:00~18:00 UTC
  Trades: 3 (W2/L1) | PnL: +$89
  Active: SOL LONG 5x +1.2%
  Regime: STRONG_UPTREND (0.85)
  MDD: 1.8% (Normal)
```

### 4.4 Guardian Agent

```markdown
# Guardian — 포트폴리오 리스크 관리자

## 역할
4개 Trading Agent의 전체 리스크를 통합 관리한다.
개별 에이전트는 자기 자본만 보지만, Guardian은 전체를 본다.

## 실행 주기: 1시간 정기 + 즉시 이벤트 트리거

정기: 1시간마다 전체 포트폴리오 스냅샷 평가

즉시 이벤트 트리거 (피드백 #3-3):
  → 포트폴리오 MDD가 0.5% 단위로 증가할 때마다
  → 에이전트의 open_risk가 예산의 80% 초과 시
  → 동일 심볼/동일 방향 동시 노출 발생 시 (Phase 4 exposure 체크와 별개)
  → Safety Stage 1 또는 Stage 2 발생 즉시
  → 일일 손실이 2% 초과 시

## 관리 항목
1. 전체 포트폴리오 MDD 추적
2. 에이전트별 MDD 추적
3. 에이전트 간 상관관계 체크
4. 자본 재배분 제안
5. 긴급 차단 발동
6. [NEW] open_risk_usd 통합 모니터링

## 규칙

### 전체 MDD ≥ 10%
→ 모든 에이전트 즉시 중단
→ 전 포지션 청산
→ 24시간 거래 중단
→ Telegram #alerts 긴급 알림

### 개별 에이전트 MDD ≥ 자기 한도
→ 해당 에이전트만 중단
→ 해당 에이전트 포지션 청산
→ 다른 에이전트는 계속

### 상관관계 경고
→ S2와 S3가 같은 심볼/같은 방향 동시 보유
→ 실질 노출도 = 합산
→ 합산 노출 > 자본의 40% → 후순위 에이전트(S2) 포지션 축소

### 자본 재배분
→ 특정 에이전트의 PF가 1.0 미만 (손실 중) → 자본 10% 회수
→ 특정 에이전트의 PF가 2.5 이상 (고성과) → 자본 5% 추가 배분
→ 변경 시 Telegram에 공지 후 즉시 적용 (유저 승인 불필요)

### 일일 손실 한도
→ 전체 일일 손실 > 3% → 남은 시간 레버리지 50% 감소
→ 전체 일일 손실 > 5% → 당일 거래 중단
```

### 4.5 Evolver Agent

```markdown
# Evolver — 파라미터 진화자

## 역할
4개 Trading Agent의 파라미터를 MDD/PF 기반으로 최적화한다.
직접 거래하지 않는다. 파라미터만 조정한다.

## 실행 주기: 4시간마다

## 프로세스
1. 전체 거래 이력 수집 (4개 에이전트 합산)
2. 에이전트별 MDD/PF/승률 계산
3. 각 에이전트의 자기 복기 결과 수신
4. 파라미터 조정 방향 결정
5. Evolution Engine 호출 (수학적 최적화)
6. 개선된 파라미터 적용
7. Telegram #evolution에 변경 기록

## 적합도 함수 (피드백 #6: 통계적 신뢰도 반영)

기존: fitness = PF × (1 - MDD_penalty) × (1 - frequency_penalty)
문제: PF 3.0(2건 승리) vs PF 1.8(50건 안정) 구분 불가

개선:
  fitness = Sortino × sqrt(trades) × (1 - MDD_penalty) × (1 - FP_penalty)

  Sortino = (평균수익 - 0) / 하방표준편차
  → Sharpe보다 우수: 상방 변동성(큰 수익)을 페널티로 안 봄
  → 하방 리스크만 측정 → MDD 중시 철학에 부합

  sqrt(trades): 거래 수의 통계적 신뢰도
  → 2건: sqrt(2) = 1.4
  → 50건: sqrt(50) = 7.1
  → 자연스럽게 거래 수가 많을수록 신뢰

MDD_penalty:
  MDD > 10% → 1.0 (불합격)
  MDD 5~10% → (MDD-5)/5 × 0.5
  MDD < 5%  → 0.0

FP_penalty (피드백 #4: 패턴 오탐 페널티):
  confirmed 패턴으로 진입했는데 실패한 비율
  FP_rate = confirmed_pattern_losses / confirmed_pattern_trades
  FP_rate > 0.6 → penalty = (FP_rate - 0.6) × 2.0  (최대 0.8)
  FP_rate ≤ 0.6 → penalty = 0
  → 패턴 오탐이 많으면 자동으로 해당 패턴 가중치 하향

최소 거래 수 가드:
  trades < 10 → fitness = 0 (평가 불가, 파라미터 변경 금지)
  trades 10~20 → fitness × 0.5 (반신반의)
  trades > 20 → fitness × 1.0 (정상 평가)

## 파라미터 변경 규칙
- 한 번에 에이전트당 최대 3개 파라미터
- [피드백] 같은 카테고리 동시 변경 금지 (예: Gate 임계값 + 레버리지 동시 변경 ✗)
- 변경 폭: 현재 값의 ±20% 이내
- [피드백] 변경 적용 전 A/B shadow 테스트:
    신규 파라미터를 1시간 동안 shadow로만 점수 계산하여 기존과 비교 로그를 남김.
    shadow에서 악화가 명확하면 적용 취소.
- 최소 관찰 (에이전트별 분리):
    S1(스캘퍼): min(20거래, 48시간) — 거래 빈도 높으므로 시간 기준도 가능
    S2(단기):   min(20거래, 72시간)
    S3(스윙):   20거래 기준만 (시간 무시) — 72시간에 ~16~56거래
    S4(포지션): 20거래 기준만 (시간 무시) — 7~10일 걸릴 수 있음
    [피드백] S4는 48시간이면 2~6건뿐이라 통계적으로 무의미
    → S3/S4는 최소 거래 수 기준만 적용
- 연속 3회 악화 시 자동 롤백
- 절대 불변: MDD 목표(10%), 레버리지 절대 상한, 최대 손실 %

## 에이전트별 MDD 목표
  S1: 개별 3%
  S2: 개별 4%
  S3: 개별 5%
  S4: 개별 5%
  전체: 10%
```

### 4.6 Reporter Agent

```markdown
# Reporter — 보고 및 명령 처리

## 역할
1. Telegram에 정기 리포트 게시
2. 유저 명령 수신 및 처리
3. 긴급 알림 발송

## 리포트 스케줄
- 매 4시간: 포지션 요약 (#dashboard)
- 매일 00:00: 일일 리포트 (#daily-report)

## 유저 명령 (#commands)
- "상태"     → 전체 시스템 상태
- "포지션"   → 활성 포지션 목록
- "수익"     → PnL 요약 (일/주/월)
- "중지"     → 전체 거래 중단
- "S2 중지"  → 특정 에이전트만 중단
- "{심볼} 차단" → 특정 심볼 진입 차단
- "레버리지 {N}" → 글로벌 레버리지 상한
```

### 4.7 OpenClaw 설정

```json5
{
  auth: { openai: { type: "oauth" } },

  agents: {
    list: [
      { id: "s1", workspace: "~/.openclaw/ws-trading/s1", agentDir: "~/.openclaw/agents/s1/agent", model: "openai/gpt-4o", tools: { allow: ["read","exec"] } },
      { id: "s2", workspace: "~/.openclaw/ws-trading/s2", agentDir: "~/.openclaw/agents/s2/agent", model: "openai/gpt-4o", tools: { allow: ["read","exec"] } },
      { id: "s3", workspace: "~/.openclaw/ws-trading/s3", agentDir: "~/.openclaw/agents/s3/agent", model: "openai/gpt-4o", tools: { allow: ["read","exec"] } },
      { id: "s4", workspace: "~/.openclaw/ws-trading/s4", agentDir: "~/.openclaw/agents/s4/agent", model: "openai/gpt-4o", tools: { allow: ["read","exec"] } },
      { id: "guardian", workspace: "~/.openclaw/ws-trading/guardian", agentDir: "~/.openclaw/agents/guardian/agent", model: "openai/gpt-4o", tools: { allow: ["read","exec","write"] } },
      { id: "evolver", workspace: "~/.openclaw/ws-trading/evolver", agentDir: "~/.openclaw/agents/evolver/agent", model: "openai/gpt-4o", tools: { allow: ["read","exec","write"] } },
      { id: "reporter", workspace: "~/.openclaw/ws-trading/reporter", agentDir: "~/.openclaw/agents/reporter/agent", model: "openai/gpt-4o", tools: { allow: ["read","exec"] } },
    ]
  },

  tools: { agentToAgent: { enabled: true, allow: ["s1","s2","s3","s4","guardian","evolver","reporter"] } },

  channels: {
    telegram: {
      token: "BOT_TOKEN",
      guilds: {
        "GUILD_ID": {
          requireMention: false,
          channels: {
            "dashboard": { allow: true },
            "live-trades": { allow: true },
            "s1-status": { allow: true },
            "s2-status": { allow: true },
            "s3-status": { allow: true },
            "s4-status": { allow: true },
            "daily-report": { allow: true },
            "evolution": { allow: true },
            "alerts": { allow: true },
            "commands": { allow: true },
          }
        }
      }
    }
  },

  bindings: [
    { agentId: "s1", match: { channel: "telegram", peer: { kind: "channel", id: "S1_CH" } } },
    { agentId: "s2", match: { channel: "telegram", peer: { kind: "channel", id: "S2_CH" } } },
    { agentId: "s3", match: { channel: "telegram", peer: { kind: "channel", id: "S3_CH" } } },
    { agentId: "s4", match: { channel: "telegram", peer: { kind: "channel", id: "S4_CH" } } },
    { agentId: "reporter",  match: { channel: "telegram", peer: { kind: "channel", id: "DASHBOARD_CH" } } },
    { agentId: "reporter",  match: { channel: "telegram", peer: { kind: "channel", id: "COMMANDS_CH" } } },
    { agentId: "evolver",   match: { channel: "telegram", peer: { kind: "channel", id: "EVOLUTION_CH" } } },
    { agentId: "guardian",  match: { channel: "telegram", peer: { kind: "channel", id: "ALERTS_CH" } } },
  ]
}
```

---

## 5. Telegram 알림 시스템

### 5.1 구현 방식

- **라이브러리**: `httpx` (async HTTP client) — 별도 Telegram SDK 불필요
- **API**: `https://api.telegram.org/bot{TOKEN}/sendMessage` (HTTPS POST)
- **인증**: `.env`의 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- **포맷**: `parse_mode: "HTML"` (볼드, 코드블록 등)

#### Bot 설정 절차
1. Telegram에서 **@BotFather**에게 `/newbot` → 봇 이름 입력 → `BOT_TOKEN` 발급
2. **슈퍼그룹** 생성 → 그룹 설정에서 **Topics 활성화**
3. 봇을 그룹에 **관리자로 초대** (메시지 전송 권한)
4. `https://api.telegram.org/bot<TOKEN>/getUpdates` 호출 → `chat.id` 확인
5. `.env`에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 기입

#### 메시지 전송 코드 패턴
```python
import httpx
from src.utils.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

async def send_telegram(
    message: str,
    thread_id: int | None = None,
    parse_mode: str = "HTML",
) -> None:
    """Telegram 메시지 전송. thread_id로 Topic 지정."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10)
        resp.raise_for_status()
```

### 5.2 슈퍼그룹 Topics 구조

Telegram 슈퍼그룹의 **Topics** 기능으로 알림을 분리한다.
각 Topic은 독립 스레드처럼 동작하며, `message_thread_id`로 지정한다.

```
📱 TradingBot (슈퍼그룹, Topics 활성화)
│
├── 📊 Dashboard          ← thread_id: TOPIC_DASHBOARD
│   └── 전체 포트폴리오 (Reporter, 4시간)
│
├── 💰 Live Trades        ← thread_id: TOPIC_TRADES
│   └── 실시간 체결 (자동, 모든 에이전트)
│
├── 🚨 Alerts             ← thread_id: TOPIC_ALERTS
│   └── 긴급 알림 (Guardian, MDD 변경 등)
│
├── 🤖 S1 Scalper         ← thread_id: TOPIC_S1
├── 🤖 S2 Short-term      ← thread_id: TOPIC_S2
├── 🤖 S3 Swing           ← thread_id: TOPIC_S3
├── 🤖 S4 Position        ← thread_id: TOPIC_S4
│   └── 각 에이전트 상태/복기
│
├── 📈 Daily Report       ← thread_id: TOPIC_REPORT
│   └── 일일 통합 리포트 (Reporter)
│
├── 🧬 Evolution          ← thread_id: TOPIC_EVOLUTION
│   └── 파라미터 변경 기록 (Evolver)
│
└── ⌨️ Commands           ← thread_id: TOPIC_COMMANDS
    └── 유저 명령
```

> **Topic thread_id 확인법**: Topic을 만든 뒤 그 Topic에 메시지를 보내고
> `getUpdates` API를 호출하면 `message.message_thread_id` 값을 확인할 수 있다.

#### config.py Topic ID 관리
```python
# .env 또는 config.py에서 관리
TELEGRAM_TOPICS = {
    "dashboard":  int(os.getenv("TG_TOPIC_DASHBOARD", "0")),
    "trades":     int(os.getenv("TG_TOPIC_TRADES", "0")),
    "alerts":     int(os.getenv("TG_TOPIC_ALERTS", "0")),
    "s1":         int(os.getenv("TG_TOPIC_S1", "0")),
    "s2":         int(os.getenv("TG_TOPIC_S2", "0")),
    "s3":         int(os.getenv("TG_TOPIC_S3", "0")),
    "s4":         int(os.getenv("TG_TOPIC_S4", "0")),
    "report":     int(os.getenv("TG_TOPIC_REPORT", "0")),
    "evolution":  int(os.getenv("TG_TOPIC_EVOLUTION", "0")),
    "commands":   int(os.getenv("TG_TOPIC_COMMANDS", "0")),
}
```

### 5.3 알림 유형별 메시지 포맷

#### Dashboard (4시간 주기)
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PORTFOLIO | 2026-02-18 14:00 UTC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Capital: $10,450 (+4.5% MTD)
 MDD: 2.1% (Normal) | PF(20): 2.34
 Active Positions: 7
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 S1 [5m] Scalper         $1,567 (+3.2%)
 ├ PF: 1.85 | MDD: 0.8% | Trades: 18
 └ Active: BTC SHORT 3x +0.4%

 S2 [5m+15m] Short-term  $2,612 (+4.8%)
 ├ PF: 2.41 | MDD: 1.2% | Trades: 9
 └ Active: ETH LONG 5x +1.1%, SOL LONG 4x -0.3%

 S3 [5+15+1h] Swing      $3,145 (+5.1%)
 ├ PF: 2.65 | MDD: 1.5% | Trades: 5
 └ Active: BTC LONG 7x +2.8% (TP1 hit, trailing)

 S4 [5+15+1h+4h] Position $3,126 (+4.2%)
 ├ PF: 2.10 | MDD: 2.1% | Trades: 2
 └ Active: BTC LONG 5x +3.5%, ETH LONG 4x +1.2%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

#### Live Trades (실시간, 진입/청산)
```
📈 S3 LONG BTC 7x @ $97,432
 SL $96,580 (-0.87%) | TP1 $98,800 | TP2 $100,200 | TP3 $102,500
 Score: 85/90 | Regime: STRONG_UP (0.88)
 MTF: Grade A | MDD: 2.1% Normal
```
```
✅ S3 BTC LONG 청산 @ $98,800 (TP1)
 PnL: +$245.60 (+1.42%) | 보유: 2h 15m
 Trailing 활성화 → 잔여 포지션 50%
```

#### Alerts (긴급)
```
🚨 MDD Alert: caution → defensive
 Drawdown: 5.2% | Action: REDUCE_LEV
 S3 leverage 0.4x 제한 적용
```
```
⚠️ Guardian: BTC 변동성 급등 감지
 ATR 편차 +3.2σ | 신규 진입 차단 5분
```

---

## 6. 비용 / 성능 요약

| 항목 | 값 |
|---|---|
| 데이터 | Hyperliquid WS ($0) |
| DB | SQLite ($0) |
| 서버 | VPS $10~20/월 또는 로컬 |
| AI 토큰 | ~$8/월 (에이전트당 1콜/4시간 통합) |
| **총 비용** | **$10~28/월** |
| | |
| 파이프라인 속도 | ~370ms (기존 650ms에서 단축, 패턴분석 포함) |
| 에이전트 수 | 4 Trading + 3 Meta = 7개 |
| 시그널 빈도 | 총 17~51/일 (4 에이전트 합산) |
| MDD 목표 | 10% (전체), 3~5% (개별) |
| PF 목표 | 2.0+ |

---

## 7. 기존 Step 0~7 → v5.0 고도화 요약

| 기존 | v5.0 | 고도화 내용 |
|---|---|---|
| Step 0 5가지 조건 | Phase 1 8가지 조건 | +펀딩비/OI/청산 극단, MDD 선행 게이트 |
| Step 0 고정 임계값 | 통계 기반 동적 | sigma/percentile → 시장 자동 적응 |
| Step 1 DNA 3개 | DNA 6개 | +펀딩/OI/청산밀도 (Hyperliquid 네이티브) |
| Step 1 고정 가중치 | 동적 가중치 | Evolver가 MDD/PF 기반 최적화 |
| Step 1 고정 경계값 | percentile 기반 | 시장 분포에서 자동 도출 |
| Step 2 복잡한 블렌딩 | 간소화된 전환 | 코어 로직 유지, 과도한 분기 제거 |
| Step 3 고정 S/R 가중치 | 동적 가중치 | Evolver 최적화 |
| Step 3 체제별 임계값 | ATR 기반 통일 | 모든 거리를 ATR 단위로 정규화 |
| Step 4 7 Types | 9 Types | +펀딩/OI 시그널 (T8) + 차트패턴 (T9) |
| Step 4 고정 42개 가중치 | 54개 동적 가중치 | Evolver가 진화 (T9 포함) |
| Step 5 10개 지표 | 8개 지표 | 중복 제거 + 펀딩 정합성 추가 |
| Step 5 고정 합격기준 | MDD+PF 동적 기준 | 상황에 따라 45~95점 변동 |
| Step 6 체제별 고정 파라미터 | 에이전트별 차별화 | S1~S4 각자 다른 출구 전략 |
| Step 6 고정 분할비율 | Evolver 최적화 | MDD/PF 기반 동적 조정 |
| Step 7 7단계 레버리지 | 6단계 + MDD 연동 | 간소화 + MDD 배수 통합 |
| Step 7 단일 시그널 | 4종 독립 시그널 | 에이전트별 독립 실행/주문 |
| 전체: 하드코딩 | 전체: 동적 | 모든 절대값 → 상대값/percentile |
| 전체: 단일 파이프라인 | 4×병렬 파이프라인 | 에이전트별 독립 실행 |
| 전체: 패턴 분석 없음 | 캔들스틱+차트패턴 | 22개 캔들스틱 + 8개 핵심 차트패턴(심볼별 최적화), 시너지 보너스 |
| 전체: AI 미활용 | Phase별 AI 리뷰 | 비동기 4시간 주기, 에이전트당 1콜, ~$8/월 |
| 전체: 포지션 독립 | 포트폴리오 노출 체크 | Phase 4에서 진입 시점 실시간 체크 + open risk budget |
| 전체: 수동 심볼 | Phase 0 심볼 선택 | 3-Tier 풀 + 유동성/성과 기반 동적 필터 |
| 전체: 비용 미반영 | 슬리피지/수수료/펀딩 모델 | 백테스트/Evolver에 보수적 비용 모델 적용 |
| 전체: 단순 fitness | Sortino + sqrt(trades) | 통계적 신뢰도 + 하방 리스크 중심 적합도 |

---

## 8. 구현 로드맵

> **상세 로드맵은 별도 문서로 관리**: [ROADMAP.md](./ROADMAP.md)
>
> 환경 설정, 구현 순서, Sprint 1~7 (Week 1~14+) Day별 작업/산출물/완료 기준 포함.

| Sprint | 기간 | 핵심 내용 | 주요 산출물 |
|--------|------|-----------|-------------|
| **S1** | Week 1~2 | 기반 + Data Collector | DB, WS 수집기, 24시간 수집 |
| **S2** | Week 3~4 | Phase 1 SAFETY + Phase 2 READ | Safety 8조건, 6-DNA 체제 분류 |
| **S3** | Week 5~6 | Phase 3 SCAN (변곡점+패턴) | T1~T8, 22개 캔들스틱, MTF Grade |
| **S4** | Week 7~8 | Phase 4~5 + 주문 + 포지션 관리 | GATE, EXECUTE, Trailing, Reconciliation |
| **S5** | Week 9~10 | 백테스트 + Paper Trading | 백테스터, 테스트넷 운영 개시 |
| **S6** | Week 11~12 | OpenClaw + Telegram + 차트패턴 | AI 7에이전트, 10 Topic 알림, Stage 2 패턴 |
| **S7** | Week 13~14+ | 검증 + 실전 전환 | Paper분석, VPS 배포, 소액 실전 |

**총 예상: 14주 / 버퍼 포함: 16~18주**

---

## 9. 운영/성능 안전장치 (피드백 #7)

### 9.1 SQLite 동시성 (피드백 #7-1)

```python
# [피드백] DB write contention 해결:
# Pipeline도 trades/positions에 write하므로
# "Data Collector만 write"라는 기존 설명과 충돌함.
#
# 해결 방안: 단일 writer 프로세스 통합
# Data Collector + Pipeline executor를 하나의 asyncio 프로세스에서 실행.
# 4개 에이전트를 순차 실행: S1→S2→S3→S4 (370ms×4=1.5초, 5분 간격이면 충분)
# → write contention 원천 차단

SQLITE_CONFIG = {
    # WAL(Write-Ahead Logging) 모드 강제
    "journal_mode": "WAL",

    # [피드백 반영] Writer = 단일 프로세스 (Collector + Pipeline + Position Manager)
    "writer": "main_process (collector + pipeline + position_manager)",

    # Readers: OpenClaw 에이전트만 read-only connection
    "readers": "guardian, evolver, reporter (OpenClaw)",
    "reader_mode": "read-only",

    # 최적: 파이프라인 실행 시 필요한 데이터를 메모리에 캐시
    "candle_cache": {
        "strategy": "5분봉 클로즈 시 해당 심볼의 최근 N봉을 dict로 캐시",
        "refresh": "매 5분봉 클로즈",
        "benefit": "파이프라인 실행 중 DB 접근 0회",
    },

    # [피드백] WAL checkpoint 관리
    # WAL 파일은 계속 커질 수 있음 → 주기적 checkpoint 필요
    "checkpoint": {
        "mode": "TRUNCATE",         # wal_checkpoint(TRUNCATE)
        "interval_minutes": 60,     # 1시간마다
        "on_startup": True,         # 봇 시작 시 checkpoint
    },
}

# DB 초기화 시
# conn.execute("PRAGMA journal_mode=WAL")
# conn.execute("PRAGMA busy_timeout=5000")  # 5초 대기 후 에러
# conn.execute("PRAGMA wal_autocheckpoint=1000")  # 1000 page마다 auto
```

### 9.2 주문 Idempotency (피드백 #7-2)

```python
def generate_signal_id(symbol, timeframes, candle_ts, direction, primary_type):
    """
    동일 캔들에서 동일 시그널이 재발행되는 것을 방지.
    재시작, 중복 이벤트, 네트워크 재전송 시 안전.
    """
    raw = f"{symbol}|{'_'.join(timeframes)}|{candle_ts}|{direction}|{primary_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

# 사용:
# 1. Signal 생성 시 signal_id 부여
# 2. trades 테이블에 INSERT → signal_id UNIQUE 제약
# 3. 이미 존재하면 → 중복 시그널 → 스킵
# 4. Hyperliquid 주문 전에도 "이 signal_id로 이미 주문 보냈는지" 체크

IDEMPOTENCY = {
    "signal_id_algo": "SHA256(symbol|timeframes|candle_ts|direction|type)[:16]",
    "db_unique_constraint": "trades.signal_id UNIQUE",
    "order_dedup": "in-memory set + DB fallback",
    "ttl": "24시간 후 in-memory set에서 제거 (DB에는 영구 보관)",
}
```

### 9.3 슬리피지/수수료/펀딩 모델 (피드백 #7-3)

```python
# "API비용 $0"은 수수료 면에서 맞지만, 실전에서 PF를 갉아먹는 요소:
# 1. 체결 슬리피지: 시장가 주문 시 호가 사이 미끄러짐
# 2. 펀딩비: 포지션 보유 중 8시간마다 정산
# 3. Hyperliquid 수수료: maker 0.01%, taker 0.035% (변동 가능)

COST_MODEL = {
    # ── 슬리피지 ──
    "slippage_bps": {
        "BTC":  1.5,    # 유동성 최고 → 슬리피지 최소
        "ETH":  2.0,
        "SOL":  3.0,
        "XRP":  3.0,
        "tier_3": 5.0,  # DOGE, AVAX 등 유동성 낮은 심볼
    },

    # ── 수수료 ──
    "fee_bps": {
        "taker": 3.5,   # 시장가
        "maker": 1.0,   # 지정가 (Pipeline은 대부분 시장가)
    },

    # ── 펀딩 ──
    "funding": {
        "interval_hours": 8,
        "estimation": "candles.funding_rate × notional × hold_time/8h",
    },

    # ── 백테스트/시뮬레이션 적용 ──
    "backtest_config": {
        "apply_slippage": True,   # 진입/청산 각각 적용 (왕복)
        "apply_fees": True,       # taker 기준
        "apply_funding": True,    # 보유 시간에 비례
        # [피드백] Stage별 슬리피지 배수 (변동성 급등 시 1.5x로는 부족)
        "conservative_mult_by_stage": {
            "NORMAL":  1.5,       # 평시
            "STAGE_3": 2.5,       # 변동성 높은 상황
            "STAGE_2": 3.0,       # (진입은 차단되지만 기존 포지션 청산 비용)
            "STAGE_1": 3.0,
        },
    },
}

# 예시: BTC LONG, notional $10,000, SL 1%, 보유 4시간
# 슬리피지: $10,000 × 1.5bps × 2(왕복) = $3.0
# 수수료:   $10,000 × 3.5bps × 2(왕복) = $7.0
# 펀딩:     $10,000 × 0.01% × (4/8) = $0.5
# 총 비용:  $10.5 (SL 시 손실 $100 + 비용 $10.5 = 실손 $110.5)
# → PF 계산 시 이 비용을 반드시 포함해야 실전과 일치

# Evolver의 fitness 계산에도 비용 반영
# realized_pnl = gross_pnl - slippage - fees - funding
```

### 9.4 Open Risk Budget 파라미터

```python
OPEN_RISK_PARAMS = {
    # 에이전트별 최대 열린 위험 (자본 대비)
    "s1": {"max_open_risk_pct": 0.03},  # 자본의 3%
    "s2": {"max_open_risk_pct": 0.04},
    "s3": {"max_open_risk_pct": 0.05},
    "s4": {"max_open_risk_pct": 0.05},

    # 포트폴리오 전체 최대 열린 위험
    "portfolio_max_open_risk_pct": 0.08,  # 전체 자본의 8%

    # 가용 예산 < 30% → 시그널 포기 (Phase 5에서)
    "min_available_pct": 0.30,
}
```
