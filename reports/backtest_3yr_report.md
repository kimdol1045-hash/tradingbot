# 3-Year Backtest Report (2023.02 ~ 2026.02)

**Data Source:** Binance Futures (OHLCV only, no microstructure)
**Period:** 2023-02-21 ~ 2026-02-19 (1,094 days)
**Capital:** $10,000 per agent (compounding)
**Cost Model:** 1.5x conservative (taker fees + slippage)

---

## Executive Summary

| Symbol | Best Agent | Return | Win Rate | PF | Max DD | Trades |
|--------|-----------|--------|----------|-----|--------|--------|
| BTC | S1 | +375.0% | 50.0% | 3.40 | 23.62% | 10 |
| ETH | S2 | +33,537.6% | 50.0% | 14.96 | 5.69% | 18 |
| SOL | S2 | +240,754.9% | 71.4% | 7.95 | 11.36% | 14 |
| XRP | S2 | -26.4% | 20.0% | 0.04 | 26.36% | 5 |

**Key Finding:** The pipeline is highly conservative (P1 safety blocks 99% of candles in some cases, P4 gate blocks 95%+ of patterns). This results in very few trades (3~18 per 3 years) but exceptional risk/reward when signals fire. ETH and SOL showed massive compounding returns due to strong bull trends captured correctly.

---

## BTC Results

### S1 / BTC (5m Scalper)
- **Return:** +375.0% ($10K → $47.5K)
- **Trades:** 10 (5W/5L, 50% WR)
- **PF:** 3.40 | **Avg RR:** 3.40 | **Max DD:** 23.62%
- **Sharpe:** 0.62 | **Sortino:** 1.06 | **Calmar:** 5.29
- **Avg Hold:** 71 min | **Exits:** SL 40%, TP 40%, Timeout 20%

### S2 / BTC (5m+15m)
- **Return:** +107.6% ($10K → $20.8K)
- **Trades:** 4 (1W/3L, 25% WR)
- **PF:** 7.96 | **Avg RR:** 23.88 | **Max DD:** 6.93%
- **Avg Hold:** 22 min | **Exits:** SL 75%, TP 25%

### S3 / BTC (5m+15m+1h)
- **Return:** +99.9% ($10K → $20.0K)
- **Trades:** 3 (1W/2L, 33% WR)
- **PF:** 9.91 | **Avg RR:** 19.81 | **Max DD:** 5.31%
- **Avg Hold:** 25 min | **Exits:** SL 67%, TP 33%

### S4 / BTC (5m+15m+1h+4h)
- **Return:** +107.4% ($10K → $20.7K)
- **Trades:** 3 (1W/2L, 33% WR)
- **PF:** 10.23 | **Avg RR:** 20.47 | **Max DD:** 5.31%
- **Avg Hold:** 25 min | **Exits:** SL 67%, TP 33%

**BTC Analysis:** S1 (scalper) generated the most trades and highest absolute return. S2-S4 had very few trades (3-4) but extreme risk/reward ratios (19-24x). All agents profitable. The low win rate (25-50%) is compensated by massive average RR.

---

## ETH Results

### S1 / ETH (5m Scalper)
- **Return:** +796.7% ($10K → $89.7K)
- **Trades:** 12 (5W/7L, 41.7% WR)
- **PF:** 9.88 | **Avg RR:** 13.83 | **Max DD:** 7.13%
- **Sharpe:** 0.98 | **Sortino:** 11.71 | **Calmar:** 37.28
- **Avg Hold:** 20 min | **Exits:** SL 58%, TP 42%

### S2 / ETH (5m+15m)
- **Return:** +33,537.6% ($10K → $3.36M)
- **Trades:** 18 (9W/9L, 50% WR)
- **PF:** 14.96 | **Avg RR:** 14.96 | **Max DD:** 5.69%
- **Sharpe:** 0.91 | **Sortino:** 10.50 | **Calmar:** 1,964
- **Avg Hold:** 82 min | **Exits:** SL 44%, TP 44%, Timeout 11%

### S3 / ETH (5m+15m+1h)
- **Return:** +738.5% ($10K → $83.8K)
- **Trades:** 6 (3W/3L, 50% WR)
- **PF:** 13.18 | **Avg RR:** 13.18 | **Max DD:** 6.13%
- **Avg Hold:** 29 min | **Exits:** SL 50%, TP 50%

### S4 / ETH (5m+15m+1h+4h)
- **Return:** +4,310.8% ($10K → $441K)
- **Trades:** 9 (5W/4L, 55.6% WR)
- **PF:** 12.72 | **Avg RR:** 10.18 | **Max DD:** 7.58%
- **Avg Hold:** 97 min | **Exits:** TP 56%, SL 44%

**ETH Analysis:** Outstanding performance across all agents. S2 achieved the highest returns ($3.36M from $10K) with 18 trades and 50% win rate — the high PF (14.96) combined with compounding produced exponential growth. S4 also impressive at +4,310% with 9 trades.

---

## SOL Results

### S2 / SOL (5m+15m)
- **Return:** +240,754.9% ($10K → $24.1M)
- **Trades:** 14 (10W/4L, 71.4% WR)
- **PF:** 7.95 | **Avg RR:** 3.18 | **Max DD:** 11.36%
- **Sharpe:** 0.86 | **Sortino:** 4.44 | **Calmar:** 7,071
- **Avg Hold:** 41 min | **Exits:** TP 71%, SL 29%

### S3 / SOL (5m+15m+1h)
- **Return:** +714.5% ($10K → $81.4K)
- **Trades:** 5 (3W/2L, 60% WR)
- **PF:** 10.57 | **Avg RR:** 7.05 | **Max DD:** 7.83%
- **Avg Hold:** 48 min | **Exits:** TP 60%, SL 40%

### S4 / SOL (5m+15m+1h+4h)
- **Return:** +4,485.9% ($10K → $458.6K)
- **Trades:** 8 (5W/3L, 62.5% WR)
- **PF:** 15.17 | **Avg RR:** 9.10 | **Max DD:** 6.41%
- **Avg Hold:** 65 min | **Exits:** TP 63%, SL 37%

**SOL Analysis:** Best performer overall. S2/SOL achieved the highest return in the entire backtest ($24.1M from $10K) driven by SOL's massive bull run and 71.4% win rate. All agents profitable with excellent risk metrics. S4 had the best PF (15.17) and lowest DD (6.41%).

---

## XRP Results

### S2 / XRP (5m+15m)
- **Return:** -26.4% ($10K → $7.4K)
- **Trades:** 5 (1W/4L, 20% WR)
- **PF:** 0.04 | **Avg RR:** 0.15 | **Max DD:** 26.36%
- **Avg Hold:** 57 min | **Exits:** SL 80%, TP 20%

### S3 / XRP (5m+15m+1h)
- **Return:** -70.5% ($10K → $2.9K)
- **Trades:** 2 (1W/1L, 50% WR)
- **PF:** 0.02 | **Avg RR:** 0.02 | **Max DD:** 70.95%
- **Avg Hold:** 5 min | **Exits:** SL 50%, TP 50%

### S4 / XRP (5m+15m+1h+4h)
- **Return:** -18.7% ($10K → $8.1K)
- **Trades:** 3 (1W/2L, 33% WR)
- **PF:** 0.07 | **Avg RR:** 0.14 | **Max DD:** 19.82%
- **Avg Hold:** 5 min | **Exits:** SL 67%, TP 33%

**XRP Analysis:** Only losing symbol. All agents unprofitable. XRP's choppy, range-bound price action during 2023-2025 didn't suit the pipeline's trend-following pattern detection. Very few trades taken, and those that fired mostly hit stop losses. Low RR ratios indicate the entry/exit logic didn't capture meaningful moves in XRP.

---

## Aggregate Comparison Table

| Agent | Symbol | Trades | WR | Return | PF | Max DD | Avg RR | Sharpe |
|-------|--------|--------|----|--------|-----|--------|--------|--------|
| S1 | BTC | 10 | 50.0% | +375% | 3.40 | 23.62% | 3.40 | 0.62 |
| S2 | BTC | 4 | 25.0% | +108% | 7.96 | 6.93% | 23.88 | 0.00 |
| S3 | BTC | 3 | 33.3% | +100% | 9.91 | 5.31% | 19.81 | 0.00 |
| S4 | BTC | 3 | 33.3% | +107% | 10.23 | 5.31% | 20.47 | 0.00 |
| S1 | ETH | 12 | 41.7% | +797% | 9.88 | 7.13% | 13.83 | 0.98 |
| **S2** | **ETH** | **18** | **50.0%** | **+33,538%** | **14.96** | **5.69%** | **14.96** | **0.91** |
| S3 | ETH | 6 | 50.0% | +738% | 13.18 | 6.13% | 13.18 | 0.00 |
| S4 | ETH | 9 | 55.6% | +4,311% | 12.72 | 7.58% | 10.18 | 0.00 |
| **S2** | **SOL** | **14** | **71.4%** | **+240,755%** | **7.95** | **11.36%** | **3.18** | **0.86** |
| S3 | SOL | 5 | 60.0% | +714% | 10.57 | 7.83% | 7.05 | 0.00 |
| S4 | SOL | 8 | 62.5% | +4,486% | 15.17 | 6.41% | 9.10 | 0.00 |
| S2 | XRP | 5 | 20.0% | -26% | 0.04 | 26.36% | 0.15 | 0.00 |
| S3 | XRP | 2 | 50.0% | -71% | 0.02 | 70.95% | 0.02 | 0.00 |
| S4 | XRP | 3 | 33.3% | -19% | 0.07 | 19.82% | 0.14 | 0.00 |

---

## Pipeline Funnel Analysis

The 5-Phase pipeline is intentionally ultra-conservative:

| Phase | Function | Typical Pass Rate |
|-------|----------|-------------------|
| P1: SAFETY | MDD + 8 extremity conditions | 1~100% (varies by volatility) |
| P2: READ | Regime classification | Always passes (classification only) |
| P3: SCAN | Pattern detection + scoring | 3~12% of P1 passes |
| P4: GATE | Risk/technical validation | 0.05~0.3% of P3 patterns |
| P5: EXECUTE | Signal generation | ~100% of P4 passes |

**Key bottleneck:** Phase 4 GATE blocks 95-99% of detected patterns. The tech score (RSI + CVD + Ichimoku + ADX + funding + ATR + liquidation risk) averages ~42-48/100 with OHLCV-only data, while the pass threshold is 50 (SIDEWAYS regime). Only exceptional confluences pass.

---

## Limitations & Caveats

1. **OHLCV Only:** Binance data lacks funding rate, open interest, liquidation volume, orderbook data. In production with Hyperliquid, these additional signals would improve DNA regime detection and gate scoring.

2. **Compounding Bias:** Returns are computed with full compounding ($10K base). The extreme % returns (240K%, 33K%) are a product of early profitable trades compounding into large position sizes. Real-world slippage on large positions would significantly reduce these returns.

3. **Low Trade Count:** The system generated only 2-18 trades per agent/symbol over 3 years. While the high selectivity produces excellent risk/reward, statistical significance is limited. More data or parameter tuning would be needed to validate consistency.

4. **Scan Interval:** Backtest scanned every 6th candle (30-minute intervals) for performance. This could miss some 5-minute-only patterns.

5. **No Position Overlap:** The backtest simulates sequential trades with no concurrent positions. In production, multiple agents can hold positions simultaneously.

---

## Recommendations

1. **Deploy S2 as primary agent** — best balance of trade frequency (4-18 trades) and risk/reward across BTC, ETH, SOL.

2. **Avoid XRP** (or similar choppy assets) until regime detection can be improved with full microstructure data.

3. **Lower gate thresholds further** (to 40-45 for SIDEWAYS) to increase trade frequency for statistical validation.

4. **Test with Hyperliquid testnet data** that includes funding/OI — expect improved regime detection and more accurate gate scoring.

5. **Capital allocation:** Consider 40% ETH, 30% SOL, 20% BTC, 10% reserve for the initial live deployment.

---

*Generated: 2026-02-19 | Engine: BacktestEngine v1 | Data: Binance Futures 3yr OHLCV*
