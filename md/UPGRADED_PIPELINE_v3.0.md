# Upgraded Trading Pipeline Architecture v3.0
# AI-Enhanced Adaptive Trading System
# 2026-02-18

---

## 0. 설계 철학

```
"빠른 것은 코드로, 판단이 필요한 것은 AI로, 학습이 필요한 것은 ML로"
```

### 3대 원칙

1. **Critical Path 보호**: 기존 Step 0~7 파이프라인(650ms)의 실행 속도를 절대 저하시키지 않음
2. **비동기 AI 레이어**: AI는 별도 레이어에서 비동기로 동작하며, "설정값"만 파이프라인에 주입
3. **점진적 자율성**: Human-in-the-loop → AI-assisted → AI-supervised → Full-auto 4단계 진화

---

## 1. 전체 시스템 아키텍처

```
╔══════════════════════════════════════════════════════════════════════╗
║                    LAYER 0: DATA INFRASTRUCTURE                      ║
║                        (상시 실행, 밀리초 단위)                        ║
║                                                                      ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    ║
║  │Hyperliquid│  │ Binance  │  │  Bybit   │  │  News/Social     │    ║
║  │WebSocket │  │WebSocket │  │WebSocket │  │  Feed Aggregator │    ║
║  └─────┬────┘  └─────┬────┘  └─────┬────┘  └────────┬─────────┘    ║
║        │             │             │                 │               ║
║  ┌─────▼─────────────▼─────────────▼─────────────────▼───────────┐  ║
║  │                    Unified Data Bus (Redis Streams)            │  ║
║  │  OHLCV │ OrderBook │ Funding │ OI │ Liquidations │ News │ OnChain │
║  └──────────────────────────┬────────────────────────────────────┘  ║
╚═════════════════════════════╪════════════════════════════════════════╝
                              │
          ┌───────────────────┼───────────────────────┐
          │                   │                         │
          ▼                   ▼                         ▼
╔═══════════════╗  ╔══════════════════════╗  ╔═══════════════════════╗
║   LAYER 1     ║  ║      LAYER 2         ║  ║      LAYER 3          ║
║   AI BRAIN    ║  ║  EXECUTION PIPELINE  ║  ║   ML OPTIMIZER        ║
║               ║  ║                      ║  ║                       ║
║  OpenClaw     ║  ║  Step 0 → Step 7     ║  ║  Parameter Learning   ║
║  GPT Agents   ║──║  (기존 + Enhanced)    ║──║  Model Training       ║
║  비동기       ║  ║  650ms Critical Path ║  ║  Backtesting Engine   ║
║  분~시간 주기 ║  ║  동기, 실시간        ║  ║  비동기, 일~주 주기    ║
╚═══════════════╝  ╚══════════════════════╝  ╚═══════════════════════╝
          │                   │                         │
          └───────────────────┼─────────────────────────┘
                              ▼
╔══════════════════════════════════════════════════════════════════════╗
║                    LAYER 4: OPERATION & REPORTING                    ║
║                                                                      ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────┐   ║
║  │ Discord  │  │ Position │  │ Risk     │  │ Performance       │   ║
║  │ Gateway  │  │ Manager  │  │ Monitor  │  │ Dashboard         │   ║
║  └──────────┘  └──────────┘  └──────────┘  └───────────────────┘   ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## 2. LAYER 0: Data Infrastructure

### 2.1 개요

기존 시스템은 거래소 API에서 직접 데이터를 가져왔으나, 업그레이드 버전에서는 **중앙 데이터 버스(Redis Streams)** 를 통해 모든 레이어가 동일한 데이터를 구독하는 구조로 전환합니다.

### 2.2 데이터 소스

```
┌─────────────────────────────────────────────────────────────┐
│                      Data Sources                            │
│                                                               │
│  [Market Data]                    [Alternative Data]          │
│  ├ Hyperliquid WS (Primary)       ├ CryptoQuant (온체인)      │
│  │  ├ OHLCV (1m, 5m, 15m, 1h, 4h) ├ Glassnode (온체인)       │
│  │  ├ OrderBook L2 (Top 20)       ├ CoinGlass (청산/OI/펀딩) │
│  │  ├ Trades (체결 내역)           ├ Twitter/X API (심리)     │
│  │  ├ Funding Rate                ├ CoinDesk RSS (뉴스)      │
│  │  └ Liquidations                ├ Fear & Greed Index       │
│  │                                └ DXY, S&P500 (매크로)     │
│  ├ Binance WS (Secondary/Cross-check)                        │
│  └ Bybit WS (Tertiary/Cross-check)                           │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                   Redis Streams (Data Bus)                     │
│                                                                │
│  Stream: market:{symbol}:ohlcv      ← 1분봉 실시간            │
│  Stream: market:{symbol}:orderbook  ← 호가창 500ms 간격       │
│  Stream: market:{symbol}:trades     ← 체결 내역 실시간        │
│  Stream: market:{symbol}:funding    ← 펀딩비 1시간            │
│  Stream: market:{symbol}:oi         ← OI 1분 간격             │
│  Stream: market:{symbol}:liquidation ← 청산 이벤트 실시간     │
│  Stream: news:crypto                ← 뉴스 이벤트             │
│  Stream: sentiment:social           ← 소셜 감성 5분 집계      │
│  Stream: onchain:{symbol}           ← 온체인 15분 집계        │
│  Stream: macro:indices              ← DXY, S&P 1분            │
│                                                                │
│  TimeSeries DB (InfluxDB): 전체 이력 저장 + 백테스트 데이터    │
│  Cache (Redis Hash): 최신 스냅샷 O(1) 접근                    │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 멀티 심볼 지원

```python
SYMBOL_UNIVERSE = {
    # Tier 1: 항상 활성 (높은 유동성)
    "tier_1": ["BTC-USD", "ETH-USD"],

    # Tier 2: 조건부 활성 (중간 유동성)
    "tier_2": ["SOL-USD", "XRP-USD", "DOGE-USD", "AVAX-USD"],

    # Tier 3: AI가 판단하여 동적 추가/제거
    "tier_3": ["LINK-USD", "ADA-USD", "ARB-USD", "OP-USD", "SUI-USD", ...],
}

# 각 심볼별 독립 파이프라인 인스턴스
# Tier 1: 항상 실행
# Tier 2: AI Regime Advisor가 활성화 여부 결정
# Tier 3: AI Portfolio Manager가 기회 감지 시에만 활성화
```

### 2.4 거래소 추상화 레이어

```python
class ExchangeAdapter:
    """거래소를 추상화하여 Hyperliquid → Bybit 전환이 설정 변경만으로 가능"""

    def __init__(self, exchange: str = "hyperliquid"):
        self.exchange = exchange
        # Hyperliquid: 지갑 서명 (ECDSA)
        # Binance/Bybit: API Key/Secret

    async def place_order(self, symbol, side, size, leverage, order_type, price=None):
        """통합 주문 인터페이스"""

    async def get_position(self, symbol) -> Position:
        """현재 포지션 조회"""

    async def stream_market_data(self, symbol) -> AsyncIterator:
        """실시간 데이터 스트림"""

# Primary: Hyperliquid (sub-second 실행, KYC 불필요, 낮은 수수료)
# Fallback: Bybit (Hyperliquid 장애 시 자동 전환)
```

---

## 3. LAYER 1: AI Brain (OpenClaw Agents)

### 3.1 개요

AI Brain은 기존 파이프라인과 **완전히 비동기**로 동작합니다. 파이프라인의 Critical Path(650ms)에 절대 개입하지 않으며, 대신 **Directive(지시서)** 라는 JSON 설정을 주기적으로 생성하여 Redis에 저장합니다. 파이프라인은 매 사이클 시작 시 최신 Directive를 O(1)로 읽어 반영합니다.

```
┌─────────────────────────────────────────────────────────────┐
│                     AI BRAIN (OpenClaw)                       │
│                     Model: GPT-4o (OAuth)                     │
│                     Channel: Discord                          │
│                                                               │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐     │
│  │   NEWS       │  │   REGIME    │  │   PORTFOLIO      │     │
│  │   SENTINEL   │  │   ADVISOR   │  │   MANAGER        │     │
│  │             │  │             │  │                  │     │
│  │  상시 실행   │  │  4시간 주기  │  │  1시간 주기       │     │
│  │  뉴스/심리   │  │  체제 편향   │  │  심볼 선택/배분   │     │
│  │  해석        │  │  DNA 가중치  │  │  상관관계 분석    │     │
│  └──────┬──────┘  └──────┬──────┘  └────────┬─────────┘     │
│         │                │                   │               │
│  ┌──────▼────────────────▼───────────────────▼───────────┐  │
│  │                  Directive Store (Redis)                │  │
│  │                                                         │  │
│  │  directive:news_alert     ← 긴급 뉴스 알림             │  │
│  │  directive:regime_bias    ← 체제 편향 + DNA 가중치      │  │
│  │  directive:portfolio      ← 심볼별 자금 배분 비율       │  │
│  │  directive:risk_override  ← 긴급 리스크 오버라이드      │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌─────────────┐  ┌─────────────┐                            │
│  │   REVIEWER   │  │   TUNER     │                            │
│  │             │  │             │                            │
│  │  매일 00:00  │  │  주 1회     │                            │
│  │  거래 복기   │  │  파라미터   │                            │
│  │  패턴 분석   │  │  튜닝 제안  │                            │
│  └─────────────┘  └─────────────┘                            │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Agent: News Sentinel (뉴스 감시자)

```
실행 주기: 상시 (이벤트 드리븐)
모델: GPT-4o
입력 소스: CoinDesk RSS, Twitter/X, 거래소 공지, 규제기관 발표

역할:
  1. 실시간 뉴스/소셜 미디어를 수집하고 트레이딩 임팩트를 분석
  2. 중요도 판단: CRITICAL / HIGH / MEDIUM / LOW
  3. 영향받는 심볼과 방향성 예측
  4. Step 0에 사전 경고 전달 (가격이 움직이기 BEFORE)
```

**Directive 출력 스키마:**

```json
{
  "type": "news_alert",
  "timestamp": "2026-02-18T14:30:00Z",
  "ttl_seconds": 3600,
  "severity": "HIGH",
  "event": {
    "headline": "SEC, 이더리움 ETF 승인 결정 4월로 연기",
    "source": "reuters",
    "category": "regulation"
  },
  "impact": {
    "ETH-USD": {
      "direction": "bearish",
      "magnitude": 0.7,
      "expected_duration_hours": 4,
      "confidence": 0.85
    },
    "BTC-USD": {
      "direction": "slight_bearish",
      "magnitude": 0.3,
      "expected_duration_hours": 2,
      "confidence": 0.65
    }
  },
  "action": {
    "step0_severity_boost": 20,
    "block_new_entry": ["ETH-USD"],
    "tighten_trailing": ["ETH-USD"],
    "reduce_leverage_pct": 30
  },
  "reasoning": "SEC의 ETF 연기는 단기 약세 유발. 2024년 패턴 참조 시 발표 후 4~8시간 내 3~7% 하락 후 회복 경향."
}
```

**Step 0 연동:**

```python
# Step 0의 detect_extreme() 시작 부분에 추가
def detect_extreme(candle_1m, symbol):
    # [NEW] AI 뉴스 알림 확인 (O(1) Redis 읽기, ~0.1ms)
    news_alert = redis.hget(f"directive:news_alert:{symbol}")

    if news_alert and news_alert['severity'] in ['CRITICAL', 'HIGH']:
        # 심각도 점수에 AI 부스트 추가
        severity_score += news_alert['action']['step0_severity_boost']

        # 신규 진입 차단 플래그 설정
        if symbol in news_alert['action'].get('block_new_entry', []):
            global_state.set_entry_blocked(symbol,
                duration=news_alert['impact'][symbol]['expected_duration_hours'])

    # 기존 5가지 극단 조건 평가 (변경 없음)
    ...
```

### 3.3 Agent: Regime Advisor (체제 조언자)

```
실행 주기: 4시간마다 + 주요 이벤트 발생 시
모델: GPT-4o
입력: 최근 24시간 체제 이력, 뉴스 요약, 매크로 지표, 온체인 데이터

역할:
  1. 현재 시장 국면을 거시적으로 판단
  2. Step 1의 DNA 가중치와 체제 편향을 동적으로 조정
  3. 다음 4시간의 예상 시나리오 제시
```

**Directive 출력 스키마:**

```json
{
  "type": "regime_bias",
  "timestamp": "2026-02-18T16:00:00Z",
  "ttl_seconds": 14400,
  "market_assessment": {
    "macro_regime": "risk_on",
    "crypto_cycle_phase": "mid_bull",
    "dominant_narrative": "ETF 유입 지속 + 금리 인하 기대"
  },
  "dna_weight_override": {
    "BTC-USD": { "hurst": 0.45, "entropy": 0.25, "liquidation": 0.30 },
    "ETH-USD": { "hurst": 0.40, "entropy": 0.30, "liquidation": 0.30 },
    "SOL-USD": { "hurst": 0.35, "entropy": 0.35, "liquidation": 0.30 }
  },
  "regime_bias": {
    "BTC-USD": { "direction": "bullish", "strength": 0.6 },
    "ETH-USD": { "direction": "neutral", "strength": 0.0 },
    "SOL-USD": { "direction": "bullish", "strength": 0.4 }
  },
  "confidence_modifier": {
    "uptrend_boost": 1.10,
    "downtrend_dampen": 0.90
  },
  "scenarios": [
    {
      "probability": 0.55,
      "description": "BTC $100K 돌파 시도, ETF 순유입 지속",
      "action": "추세추종 가중치 강화"
    },
    {
      "probability": 0.30,
      "description": "FOMC 경계감에 $95K~$100K 횡보",
      "action": "레인지 전략 우선"
    },
    {
      "probability": 0.15,
      "description": "예상치 못한 악재로 $90K 이탈",
      "action": "방어적 모드, 레버리지 최소화"
    }
  ],
  "reasoning": "BTC ETF 3일 연속 순유입 $500M+, DXY 하락세 지속, 선물 미결제약정 상승 중이나 과열 수준은 아님. 단, FOMC 의사록(02/19) 전후 변동성 주의."
}
```

**Step 1 연동:**

```python
# Step 1의 DNA 계산 시 AI 편향 반영
def calculate_market_dna(candles, symbol, timeframe):
    # [NEW] AI Directive 로드 (O(1), ~0.1ms)
    regime_bias = redis.hget(f"directive:regime_bias")

    # 기존 DNA 계산 (변경 없음)
    hurst = calculate_hurst(candles)
    entropy = calculate_entropy(candles)
    liquidation = calculate_liquidation_pressure(candles)

    # [NEW] AI DNA 가중치 적용
    if regime_bias and symbol in regime_bias['dna_weight_override']:
        weights = regime_bias['dna_weight_override'][symbol]
    else:
        weights = DEFAULT_DNA_WEIGHTS  # 기존 기본값 fallback

    dna_score = (
        hurst * weights['hurst'] +
        entropy * weights['entropy'] +
        liquidation * weights['liquidation']
    )

    # [NEW] 체제 편향 적용 (확신도 수정자)
    if regime_bias and symbol in regime_bias.get('regime_bias', {}):
        bias = regime_bias['regime_bias'][symbol]
        # bullish bias → 상승 체제 확신도 부스트, 하락 체제 확신도 감소
        # 최대 ±10% 영향으로 제한 (AI가 기존 판단을 뒤집지 않도록)
        confidence = apply_regime_bias(confidence, bias, max_influence=0.10)

    return dna_score, regime, confidence
```

### 3.4 Agent: Portfolio Manager (포트폴리오 관리자)

```
실행 주기: 1시간마다
모델: GPT-4o
입력: 전 심볼 체제 상태, 포지션 현황, 상관관계 매트릭스, 자금 현황

역할:
  1. 심볼 유니버스 동적 관리 (Tier 3 심볼 활성화/비활성화)
  2. 심볼별 자금 배분 비율 결정
  3. 상관관계 기반 위험 분산
  4. 전체 포트폴리오 레벨 리스크 관리
```

**Directive 출력 스키마:**

```json
{
  "type": "portfolio",
  "timestamp": "2026-02-18T15:00:00Z",
  "ttl_seconds": 3600,
  "total_capital": 10000,
  "allocation": {
    "BTC-USD":  { "weight": 0.30, "max_positions": 2, "active": true },
    "ETH-USD":  { "weight": 0.20, "max_positions": 2, "active": true },
    "SOL-USD":  { "weight": 0.15, "max_positions": 1, "active": true },
    "XRP-USD":  { "weight": 0.10, "max_positions": 1, "active": true },
    "DOGE-USD": { "weight": 0.05, "max_positions": 1, "active": true },
    "AVAX-USD": { "weight": 0.05, "max_positions": 1, "active": true },
    "SUI-USD":  { "weight": 0.05, "max_positions": 1, "active": true },
    "RESERVED": { "weight": 0.10 }
  },
  "correlation_alerts": [
    {
      "pair": ["BTC-USD", "ETH-USD"],
      "correlation_30d": 0.92,
      "warning": "높은 상관관계 — 동시 동일 방향 포지션 시 실질 레버리지 과다",
      "max_combined_exposure": 0.40
    }
  ],
  "risk_limits": {
    "max_total_exposure": 0.80,
    "max_single_symbol": 0.30,
    "max_correlated_group": 0.40,
    "max_concurrent_positions": 6,
    "daily_loss_limit": 0.05
  }
}
```

### 3.5 Agent: Reviewer (거래 복기자)

```
실행 주기: 매일 00:00 UTC
모델: GPT-4o
입력: 지난 24시간 전체 거래 로그, 시그널 이력, 시장 이벤트

역할:
  1. 일일 거래 성과 분석
  2. 손실 거래의 원인 분석 (전략 문제 vs 시장 문제)
  3. 파라미터 조정 제안
  4. 패턴 발견 및 보고
```

**출력: Discord #daily-review 채널에 리포트 게시 + 조정 제안 Directive**

```json
{
  "type": "review",
  "date": "2026-02-18",
  "performance": {
    "total_pnl": 342.50,
    "pnl_pct": 2.74,
    "win_rate": 0.636,
    "total_trades": 11,
    "avg_rr_achieved": 2.1,
    "max_drawdown": -1.8,
    "best_trade": { "symbol": "SOL-USD", "pnl": 156.20, "type": "BREAKOUT_RETEST" },
    "worst_trade": { "symbol": "ETH-USD", "pnl": -89.30, "type": "SR_LEVEL_BOUNCE" }
  },
  "analysis": {
    "observations": [
      "Momentum 전략이 ETH에서 3연패 — ETH가 $2,800~$2,850 횡보 중, 추세추종 부적합",
      "Funding 전략 적중률 80%로 최고 성과 — 시장 과열 구간에서 특히 유효",
      "16:00~20:00 UTC 구간에서 승률 78% — 미국 시장 오픈 전후가 최적"
    ],
    "parameter_suggestions": [
      {
        "target": "step4.regime_weights.SIDEWAYS.type1",
        "current": 0.80,
        "suggested": 0.90,
        "reason": "횡보장에서 S/R 반응(Type 1)이 유일한 수익원, 가중치 상향 제안"
      },
      {
        "target": "step6.trailing.eth_atr_multiplier",
        "current": 1.0,
        "suggested": 1.3,
        "reason": "ETH 트레일링이 너무 타이트하여 조기 청산 다수 발생"
      }
    ]
  },
  "tomorrow_watch": [
    "02/19 21:30 FOMC 의사록 공개 — 발표 전후 1시간 신규 진입 보류 권장",
    "SOL 생태계 주요 에어드랍 예정 — 변동성 증가 예상"
  ]
}
```

### 3.6 Agent: Tuner (파라미터 튜너)

```
실행 주기: 주 1회 (일요일 00:00 UTC)
모델: GPT-4o
입력: 주간 거래 통계, Reviewer 일일 리포트 누적, 백테스트 결과

역할:
  1. Reviewer의 일일 제안을 주간 단위로 종합 판단
  2. 실제 파라미터 변경 여부를 최종 결정
  3. 변경 시 사유와 롤백 계획을 함께 기록
  4. 변경사항을 Discord #commands 채널에 게시하여 유저 승인 요청
```

**중요: Tuner가 직접 파라미터를 변경하지 않음. Discord에 제안을 게시하고 유저 승인 후 반영.**

```
[Discord #tuning-proposals]

Tuner: 주간 파라미터 조정 제안 (2026-02-16 ~ 02-22)

제안 1: ETH Step 6 트레일링 ATR 배수 1.0 → 1.3
  근거: 5일간 ETH 트레일링 조기 청산 7회, 평균 -$45 기회비용 손실
  예상 효과: 조기 청산 50% 감소, 평균 수익 +12%
  리스크: 큰 반전 시 수익 반납 증가
  롤백 조건: 1주 후 성과 악화 시 자동 복귀

제안 2: SIDEWAYS 체제 Type 1 가중치 0.80 → 0.90
  근거: ...

[승인] [거부] [수정 후 승인]
```

### 3.7 OpenClaw 설정

```json5
// ~/.openclaw/openclaw.json
{
  auth: {
    openai: {
      type: "oauth"
      // openclaw onboard 시 OAuth 플로우로 자동 설정
    }
  },

  agents: {
    list: [
      {
        id: "news-sentinel",
        workspace: "~/.openclaw/workspace-trading/news-sentinel",
        agentDir: "~/.openclaw/agents/news-sentinel/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec"] }
      },
      {
        id: "regime-advisor",
        workspace: "~/.openclaw/workspace-trading/regime-advisor",
        agentDir: "~/.openclaw/agents/regime-advisor/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec"] }
      },
      {
        id: "portfolio-manager",
        workspace: "~/.openclaw/workspace-trading/portfolio-manager",
        agentDir: "~/.openclaw/agents/portfolio-manager/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read", "exec", "write"] }
      },
      {
        id: "reviewer",
        workspace: "~/.openclaw/workspace-trading/reviewer",
        agentDir: "~/.openclaw/agents/reviewer/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read"] }
      },
      {
        id: "tuner",
        workspace: "~/.openclaw/workspace-trading/tuner",
        agentDir: "~/.openclaw/agents/tuner/agent",
        model: "openai/gpt-4o",
        tools: { allow: ["read"] }
      }
    ]
  },

  tools: {
    agentToAgent: {
      enabled: true,
      allow: ["news-sentinel", "regime-advisor", "portfolio-manager", "reviewer", "tuner"]
    }
  },

  channels: {
    discord: {
      token: "BOT_TOKEN",
      guilds: {
        "GUILD_ID": {
          requireMention: false,
          channels: {
            "dashboard":        { allow: true },
            "live-trades":      { allow: true },
            "agent-news":       { allow: true },
            "agent-regime":     { allow: true },
            "agent-portfolio":  { allow: true },
            "daily-review":     { allow: true },
            "tuning-proposals": { allow: true },
            "alerts":           { allow: true },
            "commands":         { allow: true }
          }
        }
      }
    }
  },

  bindings: [
    { agentId: "news-sentinel",    match: { channel: "discord", peer: { kind: "channel", id: "NEWS_CH_ID" } } },
    { agentId: "regime-advisor",   match: { channel: "discord", peer: { kind: "channel", id: "REGIME_CH_ID" } } },
    { agentId: "portfolio-manager",match: { channel: "discord", peer: { kind: "channel", id: "PORTFOLIO_CH_ID" } } },
    { agentId: "reviewer",         match: { channel: "discord", peer: { kind: "channel", id: "REVIEW_CH_ID" } } },
    { agentId: "tuner",            match: { channel: "discord", peer: { kind: "channel", id: "TUNING_CH_ID" } } }
  ]
}
```

---

## 4. LAYER 2: Execution Pipeline (Enhanced Step 0~7)

### 4.0 Directive 통합 구조

모든 Step은 사이클 시작 시 Redis에서 최신 Directive를 O(1)로 읽습니다. Directive가 없거나 TTL 만료 시 기존 기본값으로 fallback합니다. **AI가 죽어도 파이프라인은 기존대로 동작합니다.**

```python
class DirectiveManager:
    """AI Directive를 파이프라인에 안전하게 주입하는 관리자"""

    def __init__(self, redis_client):
        self.redis = redis_client
        self.cache = {}  # 로컬 캐시 (Redis 장애 대비)

    def get(self, directive_type: str, symbol: str = None) -> dict | None:
        """
        Directive를 가져옴. 없거나 만료 시 None 반환.
        파이프라인은 None이면 기존 기본값 사용.
        """
        key = f"directive:{directive_type}"
        if symbol:
            key += f":{symbol}"

        data = self.redis.hget(key)
        if data and not self._is_expired(data):
            self.cache[key] = data  # 로컬 캐시 업데이트
            return data

        # Redis 장애 시 로컬 캐시 사용 (최대 30분)
        cached = self.cache.get(key)
        if cached and not self._is_stale(cached, max_age=1800):
            return cached

        return None  # fallback → 기존 기본값 사용

    def _is_expired(self, data: dict) -> bool:
        age = time.time() - data['timestamp']
        return age > data.get('ttl_seconds', 3600)
```

### 4.1 Enhanced Step 0: 극단 시장 감시 + 뉴스 사전 경고

```
기존 Step 0 (120ms)
──────────────────────────────────
[변경 없음] 5가지 극단 조건 평가
[변경 없음] 심각도 점수 계산 (0~150)
[변경 없음] 원인 추론 (5가지 Type)
[변경 없음] Stage 관리 (1~4 + Normal)

추가 (+ ~0.2ms)
──────────────────────────────────
[NEW] News Sentinel Directive 확인
  → CRITICAL/HIGH 뉴스 시:
    - severity_score에 AI 부스트 추가
    - 특정 심볼 신규 진입 차단
    - 기존 포지션 트레일링 타이트닝
    - 차단 해제 시점을 AI가 판단 (기존: 가격 기반만)
  → 뉴스 없으면: 기존과 100% 동일하게 동작
```

**핵심 차이**: 기존에는 가격이 이미 움직인 후에만 극단을 감지했지만, 이제는 **뉴스 발생 시점에 사전 방어**가 가능합니다.

### 4.2 Enhanced Step 1: 시장 DNA 분석 + AI 체제 편향

```
기존 Step 1 (200ms)
──────────────────────────────────
[변경 없음] 4개 타임프레임 병렬 DNA 계산
[변경 없음] Hurst / Entropy / Liquidation
[변경 없음] MTF 정렬 분석
[변경 없음] 체제 분류 (6가지)

수정 (추가 ~0.2ms)
──────────────────────────────────
[MODIFIED] DNA 가중치: 고정값 → Directive 우선, 없으면 기본값
  기존: hurst=0.40, entropy=0.30, liquidation=0.30 (고정)
  변경: regime_bias directive에서 심볼별 가중치 로드

[NEW] 체제 확신도에 AI 편향 적용
  - AI가 bullish bias 0.6 → 상승 체제 확신도 +6%, 하락 체제 확신도 -6%
  - max_influence = 0.10 (최대 ±10%) → AI가 기존 판단을 뒤집을 수 없음
  - AI가 방향을 바꾸는 게 아니라, 같은 방향일 때 확신을 강화하는 역할
```

### 4.3 Enhanced Step 2: 체제 전환 + 적응형 히스테리시스

```
기존 Step 2 (16ms)
──────────────────────────────────
[변경 없음] 전환 감지
[변경 없음] 블렌딩 실행
[변경 없음] 상태 추적

수정
──────────────────────────────────
[MODIFIED] 히스테리시스 유예 기간: 고정 규칙 → ML 모델 예측
  기존: if confidence > 0.85: grace -= 1  (규칙 기반)
  변경: grace = ml_model.predict(features) (ML 예측)
         단, 기존 규칙의 ±2캔들 범위 내로 제한 (안전장치)

  ML 모델: LightGBM (학습 데이터: 백테스트 전환 이력)
  Features: regime_distance, confidence, mtf_alignment,
            volatility_percentile, hour_of_day, recent_false_transitions
  추론 시간: ~0.5ms (파이프라인 영향 미미)

  Fallback: ML 모델 로드 실패 시 기존 규칙 100% 사용

[MODIFIED] 블렌딩 기간: 고정 → 유동성 기반 동적 조정
  기존: blend_period = regime_distance + 1 (고정 공식)
  변경: blend_period = base + liquidity_adjustment + ai_modifier

  liquidity_adjustment:
    - 24시간 평균 볼륨 대비 현재 볼륨
    - 유동성 높으면: 블렌딩 빠르게 (시장이 효율적으로 반영)
    - 유동성 낮으면: 블렌딩 느리게 (노이즈 가능성 높음)
```

### 4.4 Enhanced Step 3: 차트 구조 분석 + ML S/R 품질

```
기존 Step 3 (55ms)
──────────────────────────────────
[변경 없음] 병렬 처리 (S/R, 추세선, Volume Profile)
[변경 없음] 컨텍스트 생성
[변경 없음] 허위 돌파 감지

수정
──────────────────────────────────
[MODIFIED] S/R 강도 계산: 고정 가중치 → ML 학습 가중치
  기존: strength = touch × 0.5 + volume × 0.25 + recency × 0.25
  변경: strength = ml_sr_model.predict(features)

  추가 Features (기존 3개 + 4개):
    - touch_count          (기존)
    - avg_volume            (기존)
    - recency               (기존)
    - regime_at_formation   (레벨 형성 시 체제) [NEW]
    - approach_velocity     (접근 속도 — 빠르면 돌파 확률↑) [NEW]
    - estimated_liquidations (해당 레벨 근처 청산 물량 추정) [NEW]
    - time_since_formation  (형성 후 경과 시간) [NEW]

  Target: { held_probability: float, expected_bounce_pct: float }

  ML 모델: XGBoost (백테스트 데이터로 학습)
  추론 시간: ~1ms per level (5개 레벨 = ~5ms)
  Fallback: 기존 고정 가중치 공식

[NEW] 온체인 청산 맵 통합
  - CoinGlass / Hyblock 데이터에서 청산 집중 가격대 추출
  - 대규모 청산 물량이 있는 가격대 → S/R과 겹치면 강도 부스트
  - 청산 연쇄가 예상되는 구간 → Step 0에 사전 경고
```

### 4.5 Enhanced Step 4: 변곡점 감지 + 최적화된 가중치

```
기존 Step 4 (85ms)
──────────────────────────────────
[변경 없음] 7가지 Type 병렬 감지
[변경 없음] 130점 스코어링 시스템
[변경 없음] MTF 수렴 분석

수정
──────────────────────────────────
[MODIFIED] 체제별 Type 가중치 매트릭스: 수동 → 자동 최적화
  기존: 42개 가중치 (7 Types × 6 Regimes) 수동 설정
  변경: Bayesian Optimization으로 분기마다 재최적화

  최적화 목적함수: Sharpe Ratio (백테스트 기반)
  제약 조건:
    - VOLATILE 체제: 모든 Type ≤ 0.3 (기존 안전장치 유지)
    - Type 7 단독: ≤ 0.5 (기존 제한 유지)
    - 각 가중치: 0.0 ~ 1.0 범위

  실행 주기: 분기 1회 (충분한 샘플 확보 후)
  실행 장소: Layer 3 (ML Optimizer)에서 오프라인 실행
  적용: 최적화 결과를 설정 파일에 반영 (유저 승인 후)

[NEW] 심리 지표 보너스 (최대 +10점)
  - Fear & Greed Index 극단값 (≤15 또는 ≥85) 감지 시
  - 소셜 미디어 감성 극단값 감지 시
  - 극도의 공포에서 Long 변곡점 → +10 보너스
  - 극도의 탐욕에서 Short 변곡점 → +10 보너스
  - 데이터 소스: Layer 0 sentiment stream
```

### 4.6 Enhanced Step 5: 상태 검증 + 심리 지표 통합

```
기존 Step 5 (40ms)
──────────────────────────────────
[변경 없음] 10가지 검증 지표
[변경 없음] 체제별 합격 기준
[변경 없음] Primary/Secondary Type 검증

수정
──────────────────────────────────
[NEW] 11번째 검증 지표: 심리/온체인 일관성 검증 (10점)

  Long 진입 시:
    - Fear & Greed ≤ 30 (공포 구간) → +5
    - 펀딩비 음수 (숏 과다) → +3
    - 거래소 BTC 순유출 (호들) → +2

  Short 진입 시:
    - Fear & Greed ≥ 75 (탐욕 구간) → +5
    - 펀딩비 과다 양수 (롱 과다) → +3
    - 거래소 BTC 순유입 (매도 준비) → +2

  중립 구간: 0점 (영향 없음)

  데이터 없음: 0점 (페널티 없음, 기존 10가지로만 판단)

[MODIFIED] Portfolio Manager 리스크 체크 추가
  - 동일 심볼 기존 포지션 확인
  - 상관관계 높은 심볼 그룹 노출도 확인
  - 전체 포트폴리오 노출도 확인
  - 한도 초과 시: 진입 차단 (점수 무관)
```

### 4.7 Enhanced Step 6: 출구 전략 + RL 최적화

```
기존 Step 6 (38ms)
──────────────────────────────────
[변경 없음] 4가지 우선순위 손절가 계산
[변경 없음] 3단계 익절가 계산
[변경 없음] Flash Crash / Gap 방어

수정
──────────────────────────────────
[NEW] RL Exit Advisor (포지션 보유 중 비동기 실행)

  진입 시: 기존 Step 6 로직으로 SL/TP 설정 (변경 없음)
  보유 중: RL Agent가 매 캔들마다 출구 판단을 비동기로 제공

  RL Agent:
    State: [unrealized_pnl, hold_duration, current_regime,
            atr_change, volume_trend, mtf_alignment,
            distance_to_tp1, distance_to_sl, funding_rate]

    Action: HOLD | PARTIAL_25 | PARTIAL_50 | FULL_EXIT |
            TIGHTEN_TRAIL | WIDEN_TRAIL

    Reward: realized_pnl * risk_adjusted_factor
            - max_drawdown_penalty
            - holding_time_penalty

    학습: PPO (Proximal Policy Optimization)
    학습 데이터: 백테스트 + Paper Trading 이력
    추론 시간: ~2ms (경량 모델, ONNX Runtime)

  안전장치:
    - RL 판단이 기존 SL을 넘어서는 손실을 허용하지 않음
    - RL이 "HOLD"를 선택해도 기존 SL/TP 트리거 시 강제 실행
    - RL Agent 장애 시: 기존 Step 6 로직 100% 유지

[MODIFIED] 분할 익절 비율: 고정 → 동적
  기존: [40/35/25] (STRONG_UPTREND 고정)
  변경: RL Agent의 PARTIAL 판단에 따라 동적 조정

  예: TP1 도달 후 RL이 "추세 강함, HOLD" 판단
      → 기존이면 40% 청산하지만, RL이 25%만 청산하도록 조정
      → 최대 조정 범위: 기존 비율의 ±50% (40% → 20~60%)
```

### 4.8 Enhanced Step 7: 시그널 생성 + 멀티 심볼 통합

```
기존 Step 7 (45ms)
──────────────────────────────────
[변경 없음] 시그널 방향 결정
[변경 없음] 7단계 레버리지 계산
[변경 없음] 연속 손실 보호

수정
──────────────────────────────────
[NEW] 8단계 레버리지 조정: 포트폴리오 레벨 조정

  기존 7단계 (체제 → 신뢰도 → 안전성 → 변곡점 → 극단 → 변동성 → 청산압력)
  + 8단계: Portfolio Manager Directive 기반 조정

  portfolio_directive = directive_manager.get('portfolio')

  if portfolio_directive:
      # 전체 노출도가 한도의 80% 이상이면 레버리지 감소
      current_exposure = calculate_total_exposure()
      max_exposure = portfolio_directive['risk_limits']['max_total_exposure']

      if current_exposure / max_exposure > 0.80:
          leverage *= 0.70  # 30% 감소

      # 상관관계 높은 심볼 그룹 체크
      correlated_exposure = calculate_correlated_exposure(symbol, portfolio_directive)
      max_correlated = portfolio_directive['risk_limits']['max_correlated_group']

      if correlated_exposure / max_correlated > 0.70:
          leverage *= 0.80  # 20% 감소

[MODIFIED] 시그널 송출: 단일 채널 → 멀티 채널 동시
  기존: Webhook / Telegram (선택)
  변경:
    - Hyperliquid API → 즉시 주문 실행
    - Discord #live-trades → 실시간 체결 알림
    - InfluxDB → 성과 추적 데이터 저장
    - Redis Stream → RL Agent / Reviewer 학습 데이터 피드

[NEW] 심볼 간 시그널 충돌 해소
  동시에 BTC Long + ETH Short 시그널이 발생하면:
  1. 상관관계 확인 (0.92 → 높은 상관)
  2. 높은 상관 + 반대 방향 = 모순
  3. 해소 규칙:
     - 확신도 높은 쪽 우선
     - 확신도 동일하면 Tier 높은 심볼 우선
     - 둘 다 실행하되 레버리지 50% 감소 (헤지로 처리)
```

---

## 5. LAYER 3: ML Optimizer

### 5.1 개요

Layer 3는 오프라인(비실시간)으로 동작하며, 백테스트 데이터를 기반으로 ML 모델을 학습하고 파이프라인의 파라미터를 최적화합니다.

```
┌─────────────────────────────────────────────────────────────┐
│                    ML OPTIMIZER LAYER                         │
│                    (일~주 단위, 오프라인)                       │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              Backtesting Engine                       │    │
│  │                                                       │    │
│  │  - InfluxDB에서 과거 데이터 로드                       │    │
│  │  - Step 0~7 전체 파이프라인을 과거 데이터로 재실행     │    │
│  │  - 슬리피지/수수료 실측 모델 반영                      │    │
│  │  - 결과: 전체 거래 이력 + 성과 지표                    │    │
│  └──────────────────────┬───────────────────────────────┘    │
│                         │                                     │
│  ┌──────────▼──────────┐  ┌───────────────────────────┐     │
│  │  Adaptive           │  │  Weight Optimizer          │     │
│  │  Hysteresis         │  │                            │     │
│  │  Trainer            │  │  Step 4 체제별 가중치       │     │
│  │                     │  │  Bayesian Optimization     │     │
│  │  LightGBM 학습     │  │  분기 1회 실행             │     │
│  │  매주 재학습        │  │                            │     │
│  └─────────────────────┘  └───────────────────────────┘     │
│                                                               │
│  ┌─────────────────────┐  ┌───────────────────────────┐     │
│  │  S/R Quality        │  │  RL Exit                   │     │
│  │  Predictor          │  │  Trainer                   │     │
│  │                     │  │                            │     │
│  │  XGBoost 학습       │  │  PPO 학습                  │     │
│  │  매주 재학습        │  │  매일 경험 리플레이 학습    │     │
│  └─────────────────────┘  └───────────────────────────┘     │
│                                                               │
│  출력: 학습된 모델 파일 (.onnx / .pkl)                        │
│        → Layer 2 파이프라인이 로드하여 추론에 사용             │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 ML 모델 목록

| 모델 | 알고리즘 | 학습 주기 | 추론 시간 | 적용 위치 |
|---|---|---|---|---|
| AdaptiveHysteresis | LightGBM | 매주 | ~0.5ms | Step 2 유예 기간 |
| SRQualityPredictor | XGBoost | 매주 | ~1ms/level | Step 3 S/R 강도 |
| WeightOptimizer | Bayesian Opt | 분기 | 오프라인 | Step 4 가중치 매트릭스 |
| ExitOptimizer | PPO (RL) | 매일 | ~2ms | Step 6 출구 판단 |

### 5.3 모델 배포 파이프라인

```
학습 완료 → ONNX 변환 → Shadow Mode 테스트 (1일)
  → 기존 모델 대비 성능 비교 → 개선 시 교체, 악화 시 유지

Shadow Mode: 새 모델이 예측을 하지만, 실제 거래에는 기존 모델 사용
  → 로그만 기록하여 성능 비교 (A/B 테스트)
```

---

## 6. LAYER 4: Operation & Reporting

### 6.1 Discord 서버 구조

```
Trading Bot Server
│
├── CATEGORY: Live Trading
│   ├── #live-trades        ← 실시간 체결 알림 (자동, Step 7에서 직접 전송)
│   ├── #positions          ← 현재 보유 포지션 요약 (매 1시간 업데이트)
│   └── #alerts             ← 긴급 알림 (청산 위험, Flash Crash, 연속 손실)
│
├── CATEGORY: AI Agents
│   ├── #agent-news         ← News Sentinel 뉴스 분석 결과
│   ├── #agent-regime       ← Regime Advisor 체제 판단 (4시간마다)
│   └── #agent-portfolio    ← Portfolio Manager 배분 변경 사항
│
├── CATEGORY: Analysis
│   ├── #daily-review       ← Reviewer 일일 복기 리포트
│   ├── #weekly-summary     ← 주간 성과 요약
│   └── #tuning-proposals   ← Tuner 파라미터 변경 제안 (유저 승인용)
│
├── CATEGORY: Control
│   ├── #commands           ← 유저 명령 채널
│   │   ├ "전략 중지"         → 전체 거래 즉시 중단
│   │   ├ "ETH 진입 차단"     → 특정 심볼 차단
│   │   ├ "레버리지 최대 5배"  → 글로벌 레버리지 제한
│   │   ├ "상태"              → 전체 시스템 상태 요약
│   │   ├ "포지션"            → 현재 포지션 상세
│   │   └ "수익"              → 오늘/이번주/이번달 PnL
│   └── #system-logs        ← 시스템 에러/경고 로그
│
└── CATEGORY: Dashboard
    └── #dashboard          ← 유저가 주로 보는 종합 채널
```

### 6.2 #dashboard 실시간 출력 예시

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PORTFOLIO STATUS | 2026-02-18 14:32 UTC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Capital: $10,450.00 (+4.5% MTD)
 Today PnL: +$342.50 (+3.4%)
 Active Positions: 4/6
 AI Regime: Trending Bullish (conf 0.82)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 POSITIONS
┌────────┬──────┬─────┬─────────┬───────┬──────────┐
│ Symbol │ Side │ Lev │ Entry   │ PnL%  │ Status   │
├────────┼──────┼─────┼─────────┼───────┼──────────┤
│ BTC    │ LONG │ 7x  │ $97,432 │ +2.8% │ TP1 hit  │
│ SOL    │ LONG │ 5x  │ $148.30 │ +1.2% │ Trailing │
│ ETH    │ SHORT│ 3x  │ $2,845  │ +0.4% │ Running  │
│ XRP    │ LONG │ 4x  │ $0.632  │ -0.3% │ Running  │
└────────┴──────┴─────┴─────────┴───────┴──────────┘

 LAST SIGNAL (2 min ago)
 BTC-USD LONG 7x @ $97,432
 ├ Regime: STRONG_UPTREND (conf 0.88)
 ├ Inflection: SR_SUPPORT + BREAKOUT_RETEST (92pts)
 ├ Validation: 78/100 (PASSED)
 ├ AI Bias: Bullish +0.6 (ETF inflow, DXY falling)
 ├ SL: $96,100 (-1.4%) | TP1: $98,800 (+1.4%)
 ├ TP2: $100,200 (+2.8%) | TP3: $102,500 (+5.2%)
 └ Portfolio Exposure: 62% / 80% max

 NEXT EVENTS
 - FOMC Minutes: 02/19 21:30 UTC (7h away)
   News Sentinel: HIGH impact expected, entry pause recommended
 - BTC Funding: -0.008% (shorts paying, bullish signal)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 6.3 Performance Tracking

```python
# InfluxDB에 저장되는 성과 데이터
metrics = {
    # 거래별
    "trade": {
        "symbol", "side", "leverage", "entry_price", "exit_price",
        "pnl_usd", "pnl_pct", "rr_achieved", "hold_duration",
        "regime_at_entry", "inflection_type", "inflection_score",
        "validation_score", "exit_reason", "sl_distance", "tp_hit"
    },

    # 일별 집계
    "daily": {
        "total_pnl", "win_rate", "avg_rr", "max_drawdown",
        "trade_count", "avg_leverage", "exposure_time_pct"
    },

    # AI 에이전트별
    "agent_accuracy": {
        "news_sentinel_hit_rate",      # 뉴스 예측 정확도
        "regime_advisor_accuracy",     # 체제 편향 정확도
        "portfolio_sharpe_vs_equal",   # 배분 전략 vs 균등 배분
        "rl_exit_vs_fixed"             # RL 출구 vs 고정 출구
    }
}
```

---

## 7. 전체 데이터 흐름 (하나의 시그널 생명주기)

```
[T=0] 1분봉 캔들 클로즈 (BTC-USD)
  │
  │ Redis Stream → 파이프라인 트리거
  ▼
[T+0.1ms] Directive 로드
  │ directive:news_alert     → "뉴스 없음"
  │ directive:regime_bias    → "bullish +0.6, DNA weights adjusted"
  │ directive:portfolio      → "BTC 30% 배분, 현재 노출 45%"
  ▼
[T+120ms] Step 0: 극단 체크
  │ → 5가지 조건 정상, 뉴스 알림 없음 → Stage: Normal
  ▼
[T+320ms] Step 1: DNA 분석
  │ → AI DNA 가중치 적용: hurst=0.45, entropy=0.25, liquidation=0.30
  │ → 체제: STRONG_UPTREND (확신도 0.88, AI bias로 +0.05 부스트)
  ▼
[T+336ms] Step 2: 전환 체크
  │ → ML 히스테리시스: 전환 불필요 (동일 체제 유지 중)
  ▼
[T+391ms] Step 3: 차트 구조
  │ → ML S/R: $97,200 지지 (강도 0.91, 청산물량 $42M 집중)
  │ → 추세선: 4시간 상승 추세 유효 (R²=0.94)
  │ → 컨텍스트: NEAR_SUPPORT (신뢰도 0.85)
  ▼
[T+476ms] Step 4: 변곡점
  │ → Type 1 (S/R 반응) Primary + Type 3 (재테스트) Secondary
  │ → 기본 92점 + 심리 보너스 +5 (Fear & Greed = 28) = 97점
  │ → MTF Grade A (4개 TF 모두 상승)
  ▼
[T+516ms] Step 5: 검증
  │ → 10가지 지표 + 심리/온체인 일관성 = 78점 (PASSED, 기준 65)
  │ → 포트폴리오 체크: BTC 노출 15% / 최대 30% → OK
  │ → 상관 그룹 체크: BTC+ETH 노출 25% / 최대 40% → OK
  ▼
[T+554ms] Step 6: 출구 전략
  │ → SL: $96,100 (추세선 - ATR×0.8)
  │ → TP1: $98,800 (가까운 저항) / TP2: $100,200 / TP3: $102,500
  │ → 분할: 35/35/30 (AI regime bullish → TP3 비중 증가)
  ▼
[T+599ms] Step 7: 시그널 생성 + 레버리지
  │ → 8단계 레버리지: 기본 8x → 안전성(+1.3) → 변곡점(+1.2)
  │     → 포트폴리오(OK) → 최종 7x
  │ → 시그널 검증: PASS
  ▼
[T+650ms] 시그널 송출 (병렬)
  ├→ Hyperliquid API: LONG BTC-USD 7x Market Order
  ├→ Discord #live-trades: 체결 알림
  ├→ InfluxDB: 거래 기록 저장
  └→ Redis: RL Agent 학습 데이터 피드

[T+1200ms] Hyperliquid 체결 확인
  → 포지션 모니터링 시작
  → RL Exit Advisor 활성화 (매 캔들마다 출구 판단)
```

---

## 8. 성능 목표

| 지표 | 기존 (v2.x) | 업그레이드 (v3.0) | 변화 |
|---|---|---|---|
| 파이프라인 속도 | ~650ms | **~660ms** | +10ms (ML 추론) |
| 승률 (예상) | 58.3% | **62~65%** | 뉴스+심리 반영 |
| 평균 R:R | 1:2.4 | **1:2.8~3.2** | RL 출구 최적화 |
| 최대 연속 손실 | 4회 | **3회 (목표)** | 포트폴리오 분산 |
| 일일 시그널 수 | 8.7 (단일 심볼) | **25~40 (멀티)** | 멀티 심볼 |
| 지원 심볼 | 4개 고정 | **10+ 동적** | AI 동적 관리 |
| 뉴스 반응 시간 | 불가능 | **~30초** | News Sentinel |
| 파라미터 적응 | 수동 | **자동 (ML)** | 주간 재학습 |
| 거래소 | Binance/OKX/Bybit | **Hyperliquid (Primary)** | 속도+비용 |
| AI 비용 (월) | $0 | **~$50~100** | GPT-4o 호출 |

---

## 9. 구현 로드맵

### Phase 0: 기반 구축 (2~3주)
```
[Week 1~2]
├ Hyperliquid 계정 + 테스트넷 설정
├ Redis + InfluxDB 인프라 구축
├ 기존 Step 0~7을 Python 패키지로 구조화
├ ExchangeAdapter 구현 (Hyperliquid SDK 래핑)
├ 데이터 수집기 구현 (WebSocket → Redis Streams)
└ 기존 로직 백테스트 실행 (최소 6개월 데이터)

[Week 3]
├ 백테스트 결과 분석
├ 기존 파라미터 검증 (추정치 vs 실측치)
└ 기준 성과(baseline) 확정
```

### Phase 1: 코어 업그레이드 (2~3주)
```
[Week 4~5]
├ DirectiveManager 구현
├ News Sentinel Agent (OpenClaw) 설정 + SOUL.md
├ Step 0에 뉴스 알림 통합
├ Discord 서버 구조 + 봇 설정
└ #live-trades, #alerts 채널 자동 알림

[Week 6]
├ Regime Advisor Agent 설정
├ Step 1에 AI 편향 통합
├ Portfolio Manager Agent 설정
├ 멀티 심볼 파이프라인 확장
└ Paper Trading 시작 (Hyperliquid 테스트넷)
```

### Phase 2: ML 통합 (3~4주)
```
[Week 7~8]
├ AdaptiveHysteresis (LightGBM) 학습 + Step 2 통합
├ SRQualityPredictor (XGBoost) 학습 + Step 3 통합
├ Shadow Mode 테스트 (ML 모델 vs 기존 규칙)
└ Paper Trading 성과 분석

[Week 9~10]
├ RL Exit Optimizer (PPO) 학습 + Step 6 통합
├ Weight Optimizer (Bayesian) 실행 + Step 4 반영
├ Reviewer + Tuner Agent 설정
└ Paper Trading 종합 평가
```

### Phase 3: 실전 전환 (2~3주)
```
[Week 11~12]
├ 소액 실전 ($100~$500, Hyperliquid 메인넷)
├ 슬리피지/수수료 실측
├ AI Agent 정확도 추적
├ 시스템 안정성 모니터링
└ 파라미터 미세 조정

[Week 13+]
├ 자본 점진적 확대
├ ML 모델 정기 재학습 루틴 확립
├ 주간/월간 성과 리뷰 루틴 확립
└ 장기 운영 모드 진입
```

---

## 10. 안전장치 요약

```
┌─────────────────────────────────────────────────────────────┐
│                   SAFETY NET (5중 방어)                       │
│                                                               │
│  Layer 1: AI가 죽어도 파이프라인은 동작                        │
│    → Directive 없으면 기존 기본값으로 fallback                 │
│    → AI는 조언만, 최종 실행은 코드                             │
│                                                               │
│  Layer 2: 기존 Step 0~7 안전장치 100% 유지                    │
│    → Step 0 극단 감지 + 4단계 Stage                           │
│    → Step 5 다중 검증                                         │
│    → Step 7 연속 손실 보호 + 긴급 중단                        │
│                                                               │
│  Layer 3: 포트폴리오 레벨 리스크 관리 (NEW)                    │
│    → 전체 노출도 한도                                         │
│    → 상관관계 기반 집중도 제한                                 │
│    → 일일 손실 한도                                           │
│                                                               │
│  Layer 4: ML 모델 안전장치 (NEW)                              │
│    → ML 예측값은 기존 규칙의 ±범위 내로 제한                   │
│    → Shadow Mode 검증 후에만 배포                              │
│    → 모델 장애 시 기존 규칙으로 즉시 fallback                  │
│                                                               │
│  Layer 5: 유저 최종 통제 (NEW)                                │
│    → Discord #commands로 즉시 중단 가능                       │
│    → 파라미터 변경은 유저 승인 필수                             │
│    → 일일/주간 리포트로 투명한 상태 공유                       │
│                                                               │
│  Circuit Breaker (절대 조건):                                 │
│    → 일일 손실 -10% → 24시간 전체 거래 중단                   │
│    → 연속 5회 손실 → 12시간 중단                               │
│    → Flash Crash (ATR 300%+) → 즉시 전 포지션 50% 청산        │
│    → API 장애 3회 연속 → 거래소 전환 또는 중단                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 11. 기술 스택 요약

| 영역 | 기술 | 용도 |
|---|---|---|
| 언어 | Python 3.12+ | 전체 시스템 |
| 비동기 | asyncio + uvloop | 데이터 수집, 송출 |
| 거래소 | Hyperliquid Python SDK | 주문 실행 |
| 데이터 버스 | Redis Streams | 실시간 데이터 파이프라인 |
| 캐시 | Redis Hash | Directive, 최신 스냅샷 |
| 시계열 DB | InfluxDB | 과거 데이터, 성과 추적 |
| ML 학습 | LightGBM, XGBoost, Stable-Baselines3 (PPO) | 오프라인 학습 |
| ML 추론 | ONNX Runtime | 파이프라인 내 경량 추론 |
| AI Agent | OpenClaw + GPT-4o (OAuth) | 뉴스, 체제, 포트폴리오, 복기 |
| 메시징 | Discord (OpenClaw Gateway) | 알림, 명령, 리포트 |
| 모니터링 | Grafana + InfluxDB | 성과 대시보드 |
| 컨테이너 | Docker Compose | 전체 서비스 오케스트레이션 |

---

## 12. 기존 시스템 vs 업그레이드 비교

| 영역 | 기존 v2.x | 업그레이드 v3.0 |
|---|---|---|
| 시장 인식 | 가격/볼륨만 | + 뉴스, 심리, 온체인, 매크로 |
| 파라미터 | 전부 하드코딩 | ML 자동 적응 + AI 동적 조정 |
| 체제 판단 | 기술 지표만 | + AI 거시적 판단 (±10% 영향) |
| S/R 강도 | 고정 가중치 | ML 학습 가중치 |
| 변곡점 가중치 | 수동 42개 | 자동 최적화 (분기 1회) |
| 출구 전략 | 규칙 기반 고정 | RL 동적 최적화 |
| 심볼 | 4개 고정 | 10+ 동적 (AI 관리) |
| 거래소 | Binance/OKX/Bybit | Hyperliquid (Primary) |
| 리스크 | 단일 포지션 | 포트폴리오 레벨 |
| 모니터링 | 로그 파일 | Discord 실시간 + Grafana |
| 복기 | 없음 | AI 일일 자동 복기 |
| 뉴스 반응 | 불가능 | 30초 이내 사전 방어 |
| 파이프라인 속도 | 650ms | 660ms (+10ms ML 추론) |
| 비용 | $0 | ~$50~100/월 (AI) |
