"""
Market statistics — distribution-based dynamic thresholds.
Computes percentiles and sigma values for Safety conditions.
TF-based lookback: 5m:7d, 15m:14d, 1h:30d, 4h:90d.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from src.utils.indicators import candles_to_arrays

logger = logging.getLogger(__name__)

# TF → lookback days
TF_LOOKBACK_DAYS: dict[str, int] = {
    "5m": 7,
    "15m": 14,
    "1h": 30,
    "4h": 90,
}

MIN_SAMPLES = 100  # minimum candles required for valid stats


@dataclass
class MarketStats:
    """Pre-computed distribution statistics for a symbol×timeframe."""

    symbol: str = ""
    timeframe: str = ""
    sample_count: int = 0

    # Price change distribution
    price_change_mean: float = 0.0
    price_change_std: float = 1.0

    # Spread distribution
    spread_p95: float = 0.0
    spread_p97: float = 0.0

    # Volatility (ATR ratio) distribution
    volatility_p95: float = 0.0
    volatility_divergence_p95: float = 2.0

    # Volume distribution
    volume_mean: float = 0.0
    volume_p95: float = 0.0
    volume_p5: float = 0.0

    # Orderbook imbalance distribution
    imbalance_p95: float = 0.8

    # Funding rate distribution
    funding_p97: float = 0.0
    funding_p3: float = 0.0

    # OI change distribution
    oi_change_p97: float = 0.0

    # Liquidation volume distribution
    liquidation_p95: float = 0.0

    valid: bool = False


def compute_stats(candles: list[dict], symbol: str = "", timeframe: str = "") -> MarketStats:
    """
    Compute distribution statistics from historical candles.
    Used by Phase 1 SAFETY for dynamic thresholds.
    """
    stats = MarketStats(symbol=symbol, timeframe=timeframe)

    if len(candles) < MIN_SAMPLES:
        logger.warning(
            "Insufficient data for stats: %s %s (%d < %d)",
            symbol, timeframe, len(candles), MIN_SAMPLES,
        )
        return stats

    arr = candles_to_arrays(candles)
    closes = arr["close"]
    highs = arr["high"]
    lows = arr["low"]
    volumes = arr["volume"]
    stats.sample_count = len(candles)

    # Price change distribution (% change per candle)
    pct_changes = np.diff(closes) / closes[:-1] * 100
    if len(pct_changes) > 0:
        stats.price_change_mean = float(np.mean(np.abs(pct_changes)))
        stats.price_change_std = float(np.std(pct_changes, ddof=1))

    # Volume distribution
    valid_vol = volumes[volumes > 0]
    if len(valid_vol) > 0:
        stats.volume_mean = float(np.mean(valid_vol))
        stats.volume_p95 = float(np.percentile(valid_vol, 95))
        stats.volume_p5 = float(np.percentile(valid_vol, 5))

    # Volatility: range / close ratio
    ranges = (highs - lows) / closes * 100
    valid_ranges = ranges[ranges > 0]
    if len(valid_ranges) > 0:
        stats.volatility_p95 = float(np.percentile(valid_ranges, 95))

    # Volatility divergence: 3-candle max range vs rolling ATR(14)
    if len(candles) >= 17:
        from src.utils.indicators import atr as calc_atr

        atr_values = calc_atr(highs, lows, closes, period=14)
        div_ratios = []
        for i in range(16, len(candles)):
            recent_range = max(highs[i - 2 : i + 1] - lows[i - 2 : i + 1])
            if not np.isnan(atr_values[i]) and atr_values[i] > 0:
                div_ratios.append(recent_range / atr_values[i])
        if div_ratios:
            stats.volatility_divergence_p95 = float(np.percentile(div_ratios, 95))

    # Spread distribution
    spreads = np.array([c.get("bid_ask_spread", 0) or 0 for c in candles], dtype=np.float64)
    valid_spreads = spreads[spreads > 0]
    if len(valid_spreads) > 0:
        stats.spread_p95 = float(np.percentile(valid_spreads, 95))
        stats.spread_p97 = float(np.percentile(valid_spreads, 97))

    # Orderbook imbalance distribution
    imbalances = np.array(
        [abs(c.get("orderbook_imbalance", 0) or 0) for c in candles], dtype=np.float64,
    )
    valid_imb = imbalances[imbalances > 0]
    if len(valid_imb) > 0:
        stats.imbalance_p95 = float(np.percentile(valid_imb, 95))

    # Funding rate distribution
    funding = np.array(
        [c.get("funding_rate", 0) or 0 for c in candles], dtype=np.float64,
    )
    valid_funding = funding[funding != 0]
    if len(valid_funding) > 0:
        stats.funding_p97 = float(np.percentile(valid_funding, 97))
        stats.funding_p3 = float(np.percentile(valid_funding, 3))

    # OI change distribution
    oi = np.array(
        [c.get("open_interest", 0) or 0 for c in candles], dtype=np.float64,
    )
    valid_oi = oi[oi > 0]
    if len(valid_oi) > 1:
        oi_changes = np.abs(np.diff(valid_oi) / valid_oi[:-1] * 100)
        if len(oi_changes) > 0:
            stats.oi_change_p97 = float(np.percentile(oi_changes, 97))

    # Liquidation volume distribution
    liq_vol = np.array(
        [c.get("liquidation_vol", 0) or 0 for c in candles], dtype=np.float64,
    )
    valid_liq = liq_vol[liq_vol > 0]
    if len(valid_liq) > 0:
        stats.liquidation_p95 = float(np.percentile(valid_liq, 95))

    stats.valid = True
    logger.debug(
        "Stats computed: %s %s (%d samples)", symbol, timeframe, stats.sample_count,
    )
    return stats
