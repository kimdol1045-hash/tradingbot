"""
Inflection Point Detection — T1-T8 types.
Each type scores 0~max_base based on conditions.
"""
from __future__ import annotations

import numpy as np

from src.utils.indicators import atr_single, candles_to_arrays, rsi

INFLECTION_TYPES = {
    "T1_SR_REACTION":        {"priority": 3, "max_base": 30},
    "T2_TRENDLINE_REACTION": {"priority": 4, "max_base": 25},
    "T3_BREAKOUT_RETEST":    {"priority": 2, "max_base": 30},
    "T4_TRENDLINE_BREAK":    {"priority": 1, "max_base": 30},
    "T5_POC_MAGNET":         {"priority": 5, "max_base": 20},
    "T6_DIVERGENCE":         {"priority": 6, "max_base": 20},
    "T7_VOLUME_EXPLOSION":   {"priority": 7, "max_base": 15},
    "T8_FUNDING_OI_SIGNAL":  {"priority": 5, "max_base": 20},
}


def detect_t1_sr_reaction(
    candles: list[dict], sr_levels: list[dict], atr_val: float,
) -> dict | None:
    """T1: Price reacts off a strong S/R level."""
    if not sr_levels or len(candles) < 2:
        return None

    close = candles[-1]["close"]
    prev_close = candles[-2]["close"]

    for level in sr_levels:
        distance = abs(close - level["price"])
        if distance > atr_val * 1.5:
            continue

        proximity = 1.0 - (distance / (atr_val * 1.5))
        strength_bonus = level["strength"] * 10

        if level["type"] == "support" and close > prev_close and close >= level["price"]:
            score = proximity * 20 + strength_bonus
            return {
                "type": "T1_SR_REACTION", "direction": "LONG",
                "score": min(score, 30), "level_price": level["price"],
            }
        elif level["type"] == "resistance" and close < prev_close and close <= level["price"]:
            score = proximity * 20 + strength_bonus
            return {
                "type": "T1_SR_REACTION", "direction": "SHORT",
                "score": min(score, 30), "level_price": level["price"],
            }
    return None


def detect_t2_trendline_reaction(
    candles: list[dict], trendlines: list[dict], atr_val: float,
) -> dict | None:
    """T2: Price bounces off a trendline."""
    if not trendlines or len(candles) < 2:
        return None

    close = candles[-1]["close"]
    n = len(candles) - 1

    for tl in trendlines:
        line_price = tl["slope"] * n + tl["intercept"]
        distance = abs(close - line_price)
        if distance > atr_val * 0.5:
            continue

        proximity = 1.0 - (distance / (atr_val * 0.5))
        r2_bonus = tl["r_squared"] * 5

        if tl["type"] == "support" and close > line_price:
            return {
                "type": "T2_TRENDLINE_REACTION", "direction": "LONG",
                "score": min(proximity * 20 + r2_bonus, 25),
            }
        elif tl["type"] == "resistance" and close < line_price:
            return {
                "type": "T2_TRENDLINE_REACTION", "direction": "SHORT",
                "score": min(proximity * 20 + r2_bonus, 25),
            }
    return None


def detect_t3_breakout_retest(
    candles: list[dict], sr_levels: list[dict], atr_val: float,
) -> dict | None:
    """T3: Price breaks an S/R level then retests it from the other side."""
    if not sr_levels or len(candles) < 5:
        return None

    close = candles[-1]["close"]
    closes = [c["close"] for c in candles[-5:]]

    for level in sr_levels:
        lp = level["price"]
        # Check if recent candles crossed the level
        crossed_above = any(c < lp for c in closes[:-2]) and closes[-2] > lp
        crossed_below = any(c > lp for c in closes[:-2]) and closes[-2] < lp

        distance = abs(close - lp)
        if distance > atr_val * 0.5:
            continue

        proximity = 1.0 - (distance / (atr_val * 0.5))

        if crossed_above and close >= lp:
            # Broke above resistance, retesting as support
            return {
                "type": "T3_BREAKOUT_RETEST", "direction": "LONG",
                "score": min(proximity * 20 + level["strength"] * 10, 30),
            }
        elif crossed_below and close <= lp:
            # Broke below support, retesting as resistance
            return {
                "type": "T3_BREAKOUT_RETEST", "direction": "SHORT",
                "score": min(proximity * 20 + level["strength"] * 10, 30),
            }
    return None


def detect_t4_trendline_break(
    candles: list[dict], trendlines: list[dict], atr_val: float,
) -> dict | None:
    """T4: Price breaks through a trendline with conviction."""
    if not trendlines or len(candles) < 3:
        return None

    n = len(candles) - 1
    close = candles[-1]["close"]
    volumes = [c["volume"] for c in candles[-3:]]
    avg_vol = np.mean(volumes)

    for tl in trendlines:
        line_price = tl["slope"] * n + tl["intercept"]
        prev_line = tl["slope"] * (n - 1) + tl["intercept"]
        prev_close = candles[-2]["close"]

        # Break above resistance TL
        if tl["type"] == "resistance" and prev_close < prev_line and close > line_price:
            vol_mult = candles[-1]["volume"] / avg_vol if avg_vol > 0 else 1.0
            score = 15 + min(vol_mult * 5, 15)
            return {
                "type": "T4_TRENDLINE_BREAK", "direction": "LONG",
                "score": min(score, 30), "vol_mult": round(vol_mult, 2),
            }
        # Break below support TL
        elif tl["type"] == "support" and prev_close > prev_line and close < line_price:
            vol_mult = candles[-1]["volume"] / avg_vol if avg_vol > 0 else 1.0
            score = 15 + min(vol_mult * 5, 15)
            return {
                "type": "T4_TRENDLINE_BREAK", "direction": "SHORT",
                "score": min(score, 30), "vol_mult": round(vol_mult, 2),
            }
    return None


def detect_t5_poc_magnet(
    candles: list[dict], vol_profile: dict, atr_val: float,
) -> dict | None:
    """T5: Price attracted to Volume Profile POC."""
    poc = vol_profile.get("poc_price", 0)
    if poc <= 0 or len(candles) < 2:
        return None

    close = candles[-1]["close"]
    prev_close = candles[-2]["close"]
    distance = abs(close - poc)

    if distance > atr_val * 2.0:
        return None

    # Moving toward POC
    prev_dist = abs(prev_close - poc)
    if distance >= prev_dist:
        return None

    proximity = 1.0 - (distance / (atr_val * 2.0))
    direction = "LONG" if close < poc else "SHORT"

    return {
        "type": "T5_POC_MAGNET", "direction": direction,
        "score": min(proximity * 20, 20), "poc_price": poc,
    }


def detect_t6_divergence(candles: list[dict], atr_val: float) -> dict | None:
    """T6: Price-RSI divergence using swing point comparison."""
    if len(candles) < 20:
        return None

    arr = candles_to_arrays(candles)
    closes = arr["close"]
    rsi_vals = rsi(closes, period=14)
    valid_rsi = rsi_vals[~np.isnan(rsi_vals)]
    if len(valid_rsi) < 10:
        return None

    # Use last 20 candles for swing detection
    window = 20
    rc = closes[-window:]
    rr = rsi_vals[-window:]
    if np.any(np.isnan(rr)):
        return None

    # Find swing lows and highs (local extrema with 2-bar lookback/ahead)
    swing_lows: list[int] = []
    swing_highs: list[int] = []
    for i in range(2, len(rc) - 2):
        if rc[i] <= min(rc[i - 2], rc[i - 1]) and rc[i] <= min(rc[i + 1], rc[i + 2]):
            swing_lows.append(i)
        if rc[i] >= max(rc[i - 2], rc[i - 1]) and rc[i] >= max(rc[i + 1], rc[i + 2]):
            swing_highs.append(i)

    # Bullish divergence: price lower low, RSI higher low
    if len(swing_lows) >= 2:
        i1, i2 = swing_lows[-2], swing_lows[-1]
        if rc[i2] < rc[i1] and rr[i2] > rr[i1]:
            rsi_strength = abs(rr[i2] - rr[i1])
            price_drop = abs(rc[i1] - rc[i2]) / atr_val if atr_val > 0 else 0
            score = rsi_strength * 1.0 + price_drop * 3
            return {
                "type": "T6_DIVERGENCE", "direction": "LONG",
                "score": min(score, 20),
            }

    # Bearish divergence: price higher high, RSI lower high
    if len(swing_highs) >= 2:
        i1, i2 = swing_highs[-2], swing_highs[-1]
        if rc[i2] > rc[i1] and rr[i2] < rr[i1]:
            rsi_strength = abs(rr[i1] - rr[i2])
            price_rise = abs(rc[i2] - rc[i1]) / atr_val if atr_val > 0 else 0
            score = rsi_strength * 1.0 + price_rise * 3
            return {
                "type": "T6_DIVERGENCE", "direction": "SHORT",
                "score": min(score, 20),
            }

    return None


def detect_t7_volume_explosion(candles: list[dict]) -> dict | None:
    """T7: Current volume >> recent average."""
    if len(candles) < 20:
        return None

    volumes = [c["volume"] for c in candles[-20:]]
    avg_vol = np.mean(volumes[:-1])
    current_vol = volumes[-1]

    if avg_vol <= 0:
        return None

    ratio = current_vol / avg_vol
    if ratio < 2.0:
        return None

    close = candles[-1]["close"]
    prev_close = candles[-2]["close"]
    direction = "LONG" if close > prev_close else "SHORT"

    return {
        "type": "T7_VOLUME_EXPLOSION", "direction": direction,
        "score": min((ratio - 1.0) * 5, 15), "vol_ratio": round(ratio, 2),
    }


def detect_t8_funding_oi(
    candles: list[dict], stats_p95_funding: float, stats_p5_funding: float,
    stats_oi_change_p90: float, atr_val: float,
) -> dict | None:
    """T8: Extreme funding rate or OI squeeze signal."""
    if len(candles) < 2:
        return None

    candle = candles[-1]
    funding = candle.get("funding_rate", 0) or 0
    oi_now = candle.get("open_interest", 0) or 0
    oi_prev = candles[-2].get("open_interest", 0) or 0

    score = 0.0
    direction = None

    # Extreme funding → counter-trade
    if stats_p95_funding > 0 and funding > stats_p95_funding:
        score += 12
        direction = "SHORT"
    elif stats_p5_funding < 0 and funding < stats_p5_funding:
        score += 12
        direction = "LONG"

    # OI surge with price stagnation = squeeze
    if oi_prev > 0 and oi_now > 0:
        oi_change = abs(oi_now - oi_prev) / oi_prev
        price_change = abs(candle["close"] - candles[-2]["close"])
        if oi_change > 0.03 and price_change < atr_val * 0.3:
            score += 8
            if direction is None:
                direction = "LONG" if funding > 0 else "SHORT"

    if score < 5 or direction is None:
        return None

    return {
        "type": "T8_FUNDING_OI_SIGNAL", "direction": direction,
        "score": min(score, 20),
    }


def detect_inflections(
    candles: list[dict],
    sr_levels: list[dict],
    trendlines: list[dict],
    vol_profile: dict,
    atr_val: float,
    stats: dict,
) -> list[dict]:
    """
    Run all inflection detectors (T1-T8).
    Returns list of detected inflection points sorted by priority.
    """
    detections: list[dict] = []

    results = [
        detect_t1_sr_reaction(candles, sr_levels, atr_val),
        detect_t2_trendline_reaction(candles, trendlines, atr_val),
        detect_t3_breakout_retest(candles, sr_levels, atr_val),
        detect_t4_trendline_break(candles, trendlines, atr_val),
        detect_t5_poc_magnet(candles, vol_profile, atr_val),
        detect_t6_divergence(candles, atr_val),
        detect_t7_volume_explosion(candles),
        detect_t8_funding_oi(
            candles,
            stats.get("funding_p95", 0.001),
            stats.get("funding_p5", -0.001),
            stats.get("oi_change_p90", 5.0),
            atr_val,
        ),
    ]

    for r in results:
        if r is not None:
            priority = INFLECTION_TYPES.get(r["type"], {}).get("priority", 10)
            r["priority"] = priority
            detections.append(r)

    detections.sort(key=lambda d: d["priority"])
    return detections
