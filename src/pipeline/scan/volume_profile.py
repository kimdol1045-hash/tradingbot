"""
Volume Profile — histogram of volume at price bins.
POC (Point of Control) = price level with highest volume.
Value Area = 70% of total volume range.
"""
from __future__ import annotations

import numpy as np

from src.utils.indicators import candles_to_arrays


def detect_volume_profile(candles: list[dict], params: dict) -> dict:
    """
    Build volume profile from candles.

    Returns:
        {poc_price, value_area_high, value_area_low, bins, profile}
    """
    if len(candles) < 5:
        return {"poc_price": 0.0, "value_area_high": 0.0, "value_area_low": 0.0}

    # For 5-19 candles: simplified POC using VWAP-like approach
    if len(candles) < 20:
        closes = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        total_vol = sum(volumes) or 1.0
        vwap = sum(c * v for c, v in zip(closes, volumes)) / total_vol
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        return {
            "poc_price": vwap,
            "value_area_high": max(highs),
            "value_area_low": min(lows),
        }

    arr = candles_to_arrays(candles)
    closes = arr["close"]
    highs = arr["high"]
    lows = arr["low"]
    volumes = arr["volume"]

    num_bins = params.get("num_bins", 50)
    price_min = float(np.min(lows))
    price_max = float(np.max(highs))

    if price_max <= price_min:
        return {"poc_price": float(closes[-1]), "value_area_high": price_max, "value_area_low": price_min}

    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_volumes = np.zeros(num_bins)

    # Distribute each candle's volume across the bins it touches
    for i in range(len(candles)):
        candle_low = float(lows[i])
        candle_high = float(highs[i])
        vol = float(volumes[i])
        if candle_high <= candle_low:
            continue
        for b in range(num_bins):
            if bin_edges[b + 1] >= candle_low and bin_edges[b] <= candle_high:
                overlap_low = max(bin_edges[b], candle_low)
                overlap_high = min(bin_edges[b + 1], candle_high)
                fraction = (overlap_high - overlap_low) / (candle_high - candle_low)
                bin_volumes[b] += vol * fraction

    # POC: bin with highest volume
    poc_idx = int(np.argmax(bin_volumes))
    poc_price = float((bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2)

    # Value Area: 70% of total volume, expanding outward from POC
    total_vol = bin_volumes.sum()
    target_vol = total_vol * 0.70

    va_low_idx = poc_idx
    va_high_idx = poc_idx
    accumulated = bin_volumes[poc_idx]

    while accumulated < target_vol and (va_low_idx > 0 or va_high_idx < num_bins - 1):
        expand_low = bin_volumes[va_low_idx - 1] if va_low_idx > 0 else 0
        expand_high = bin_volumes[va_high_idx + 1] if va_high_idx < num_bins - 1 else 0
        if expand_low >= expand_high and va_low_idx > 0:
            va_low_idx -= 1
            accumulated += expand_low
        elif va_high_idx < num_bins - 1:
            va_high_idx += 1
            accumulated += expand_high
        else:
            va_low_idx -= 1
            accumulated += expand_low

    return {
        "poc_price": poc_price,
        "value_area_high": float(bin_edges[va_high_idx + 1]),
        "value_area_low": float(bin_edges[va_low_idx]),
    }
