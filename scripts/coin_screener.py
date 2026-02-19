#!/usr/bin/env python3
"""
Coin Screener — analyze Hyperliquid assets for trading suitability.

Fetches all listed coins, computes suitability metrics (Hurst exponent,
volume, spread, ATR/price ratio, pattern formation frequency), ranks them,
and outputs tier classifications to symbols.json.

Usage:
    python scripts/coin_screener.py                    # Full analysis
    python scripts/coin_screener.py --min-volume 10    # Min $10M daily volume
    python scripts/coin_screener.py --top 20           # Show top 20
    python scripts/coin_screener.py --output symbols.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.exchange.hyperliquid import fetch_asset_contexts, fetch_candles, get_info_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══ Hurst Exponent ═══

def compute_hurst(prices: list[float], max_lag: int = 100) -> float:
    """Compute Hurst exponent via R/S analysis.

    H > 0.5 → trending (persistent)
    H = 0.5 → random walk
    H < 0.5 → mean-reverting
    """
    if len(prices) < max_lag * 2:
        return 0.5

    log_returns = np.diff(np.log(prices))
    lags = range(10, min(max_lag, len(log_returns) // 2))
    rs_values = []
    lag_values = []

    for lag in lags:
        rs_list = []
        for start in range(0, len(log_returns) - lag, lag):
            chunk = log_returns[start:start + lag]
            mean_chunk = np.mean(chunk)
            cumdev = np.cumsum(chunk - mean_chunk)
            r = np.max(cumdev) - np.min(cumdev)
            s = np.std(chunk, ddof=1)
            if s > 0:
                rs_list.append(r / s)
        if rs_list:
            rs_values.append(np.log(np.mean(rs_list)))
            lag_values.append(np.log(lag))

    if len(rs_values) < 3:
        return 0.5

    # Linear regression: log(R/S) = H * log(n) + c
    coeffs = np.polyfit(lag_values, rs_values, 1)
    return float(np.clip(coeffs[0], 0.0, 1.0))


# ═══ Spread Analysis ═══

def estimate_spread_bps(candles: list[dict]) -> float:
    """Estimate average spread from OHLC data (proxy: high-low / mid)."""
    if not candles:
        return float("inf")

    spreads = []
    for c in candles[-2000:]:  # Last ~7 days of 5m candles
        mid = (c["high"] + c["low"]) / 2
        if mid > 0:
            spread = (c["high"] - c["low"]) / mid * 10000
            spreads.append(spread)

    return float(np.median(spreads)) if spreads else float("inf")


# ═══ ATR / Price Ratio ═══

def compute_atr_ratio(candles: list[dict], period: int = 14) -> float:
    """Compute ATR / price ratio (normalized volatility)."""
    if len(candles) < period + 1:
        return 0.0

    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    if len(trs) < period:
        return 0.0

    atr = float(np.mean(trs[-period:]))
    price = candles[-1]["close"]
    return atr / price if price > 0 else 0.0


# ═══ Pattern Frequency Proxy ═══

def estimate_pattern_frequency(candles: list[dict]) -> float:
    """Estimate how frequently price forms actionable patterns.

    Uses a simplified detection: count significant reversals relative to ATR.
    Higher frequency = more trading opportunities.
    """
    if len(candles) < 100:
        return 0.0

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    # Compute ATR for threshold
    trs = []
    for i in range(1, len(candles)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr = float(np.mean(trs[-14:])) if trs else 1.0

    # Count swing points (local extremes exceeding 1.5 * ATR)
    swing_count = 0
    threshold = atr * 1.5
    lookback = 5

    for i in range(lookback, len(closes) - lookback):
        # Local high
        if closes[i] == max(closes[i - lookback:i + lookback + 1]):
            if closes[i] - min(closes[i - lookback:i]) > threshold:
                swing_count += 1
        # Local low
        if closes[i] == min(closes[i - lookback:i + lookback + 1]):
            if max(closes[i - lookback:i]) - closes[i] > threshold:
                swing_count += 1

    # Normalize to daily frequency (candles / 288 for 5m bars per day)
    days = len(candles) / 288
    return swing_count / days if days > 0 else 0.0


# ═══ Composite Score ═══

def compute_score(metrics: dict) -> float:
    """Compute composite suitability score (0-100).

    Weights:
      - Hurst trending (>0.5): 25%
      - Volume (log scale): 25%
      - Low spread: 20%
      - ATR ratio (moderate volatility): 15%
      - Pattern frequency: 15%
    """
    score = 0.0

    # Hurst: prefer >0.55 (trending), penalize <0.45 (mean-reverting)
    h = metrics.get("hurst", 0.5)
    if h >= 0.55:
        score += 25 * min((h - 0.5) / 0.15, 1.0)
    elif h < 0.45:
        score += 25 * max((h - 0.35) / 0.1, 0.0)
    else:
        score += 12  # Neutral

    # Volume: log scale, $50M+ = perfect
    vol = metrics.get("day_volume", 0)
    if vol > 0:
        log_vol = math.log10(max(vol, 1))
        # $5M = 6.7, $50M = 7.7, $500M = 8.7
        vol_score = min((log_vol - 6.7) / 2.0, 1.0)
        score += 25 * max(vol_score, 0.0)

    # Spread: lower is better. <5 bps = perfect, >30 bps = 0
    spread = metrics.get("spread_bps", 50)
    spread_score = max(1.0 - (spread - 3) / 27, 0.0)
    score += 20 * spread_score

    # ATR ratio: prefer moderate volatility (0.5-2% per 5m bar)
    atr_r = metrics.get("atr_ratio", 0) * 100  # Convert to %
    if 0.3 <= atr_r <= 2.0:
        score += 15
    elif atr_r < 0.3:
        score += 15 * (atr_r / 0.3)
    else:
        score += 15 * max(1.0 - (atr_r - 2.0) / 3.0, 0.0)

    # Pattern frequency: more is better (3-8 swings/day ideal)
    pf = metrics.get("pattern_freq", 0)
    if 3 <= pf <= 8:
        score += 15
    elif pf < 3:
        score += 15 * (pf / 3)
    else:
        score += 15 * max(1.0 - (pf - 8) / 8.0, 0.3)

    return round(score, 1)


def classify_tier(score: float) -> str:
    if score >= 70:
        return "tier_1"
    elif score >= 55:
        return "tier_2"
    elif score >= 40:
        return "tier_3"
    return "tier_4"


# ═══ Main ═══

def main():
    parser = argparse.ArgumentParser(description="Coin suitability screener for Hyperliquid")
    parser.add_argument("--min-volume", type=float, default=5.0, help="Min daily volume in $M (default: 5)")
    parser.add_argument("--top", type=int, default=0, help="Show only top N coins (0=all)")
    parser.add_argument("--output", type=str, default="", help="Output JSON file path")
    parser.add_argument("--candle-days", type=int, default=90, help="Days of candle history to fetch (default: 90)")
    args = parser.parse_args()

    info = get_info_client()
    logger.info("Fetching asset contexts from Hyperliquid...")
    contexts = fetch_asset_contexts(info)
    logger.info("Found %d assets", len(contexts))

    # Filter by minimum volume
    min_vol = args.min_volume * 1_000_000
    candidates = {
        sym: ctx for sym, ctx in contexts.items()
        if ctx["day_volume"] >= min_vol and ctx["mark_price"] > 0
    }
    logger.info("%d assets pass minimum volume filter ($%.0fM)", len(candidates), args.min_volume)

    # Analyze each candidate
    results = []
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.candle_days * 24 * 60 * 60 * 1000

    for i, (symbol, ctx) in enumerate(sorted(candidates.items(), key=lambda x: -x[1]["day_volume"])):
        logger.info("[%d/%d] Analyzing %s (vol=$%.1fM)...", i + 1, len(candidates), symbol, ctx["day_volume"] / 1e6)

        try:
            candles = fetch_candles(info, symbol, "5m", start_ms, now_ms)
        except Exception:
            logger.warning("Failed to fetch candles for %s, skipping", symbol)
            continue

        if len(candles) < 500:
            logger.warning("%s: only %d candles, skipping (need 500+)", symbol, len(candles))
            continue

        prices = [c["close"] for c in candles]
        hurst = compute_hurst(prices)
        spread = estimate_spread_bps(candles)
        atr_ratio = compute_atr_ratio(candles)
        pat_freq = estimate_pattern_frequency(candles)

        metrics = {
            "symbol": symbol,
            "mark_price": ctx["mark_price"],
            "day_volume": ctx["day_volume"],
            "open_interest": ctx["open_interest"],
            "funding_rate": ctx["funding_rate"],
            "hurst": round(hurst, 3),
            "spread_bps": round(spread, 1),
            "atr_ratio": round(atr_ratio, 5),
            "pattern_freq": round(pat_freq, 1),
            "candle_count": len(candles),
        }

        score = compute_score(metrics)
        metrics["score"] = score
        metrics["tier"] = classify_tier(score)

        results.append(metrics)
        logger.info(
            "  %s: score=%.1f tier=%s hurst=%.3f spread=%.1f atr=%.4f patterns=%.1f/day",
            symbol, score, metrics["tier"], hurst, spread, atr_ratio, pat_freq,
        )

        # Rate limiting
        time.sleep(0.2)

    # Sort by score descending
    results.sort(key=lambda x: -x["score"])

    if args.top > 0:
        results = results[:args.top]

    # Print summary table
    print("\n" + "=" * 90)
    print(f"{'Rank':<5} {'Symbol':<8} {'Score':<7} {'Tier':<8} {'Hurst':<7} {'Vol($M)':<9} "
          f"{'Spread':<8} {'ATR%':<8} {'Pat/Day':<8}")
    print("-" * 90)
    for i, r in enumerate(results, 1):
        print(
            f"{i:<5} {r['symbol']:<8} {r['score']:<7.1f} {r['tier']:<8} {r['hurst']:<7.3f} "
            f"{r['day_volume']/1e6:<9.1f} {r['spread_bps']:<8.1f} "
            f"{r['atr_ratio']*100:<8.3f} {r['pattern_freq']:<8.1f}"
        )
    print("=" * 90)

    # Tier summary
    tier_counts = {}
    for r in results:
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1
    print(f"\nTier distribution: {tier_counts}")

    # Output to JSON
    output_path = args.output or str(PROJECT_ROOT / "symbols.json")
    symbol_pool = {}
    for r in results:
        tier = r["tier"]
        if tier not in symbol_pool:
            symbol_pool[tier] = []
        symbol_pool[tier].append(r["symbol"])

    output_data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "candle_days": args.candle_days,
        "min_volume_usd": min_vol,
        "total_analyzed": len(results),
        "symbol_pool": symbol_pool,
        "details": results,
    }

    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)
    logger.info("Results written to %s", output_path)


if __name__ == "__main__":
    main()
