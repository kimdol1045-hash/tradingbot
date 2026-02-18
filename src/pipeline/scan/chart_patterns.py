"""
Chart Pattern Detection — 8 major chart patterns.
Uses swing point analysis to identify formations.
"""
from __future__ import annotations

import numpy as np

from src.utils.indicators import candles_to_arrays


def _find_swings(
    highs: np.ndarray, lows: np.ndarray, order: int = 10,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Find swing highs and lows with given lookback order."""
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    n = len(highs)
    for i in range(order, n - order):
        if highs[i] == np.max(highs[i - order : i + order + 1]):
            swing_highs.append((i, float(highs[i])))
        if lows[i] == np.min(lows[i - order : i + order + 1]):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def _detect_double_bottom(swing_lows: list, closes: np.ndarray, atr: float) -> dict | None:
    """Two lows at similar price with a peak between them."""
    if len(swing_lows) < 2:
        return None
    for i in range(len(swing_lows) - 1):
        l1_idx, l1_price = swing_lows[i]
        l2_idx, l2_price = swing_lows[i + 1]
        if abs(l1_price - l2_price) <= atr * 0.5 and l2_idx - l1_idx >= 10:
            neckline = float(np.max(closes[l1_idx:l2_idx]))
            if closes[-1] > l2_price:
                return {
                    "name": "DOUBLE_BOTTOM", "direction": "LONG", "score": 17,
                    "neckline": neckline,
                    "target_distance": neckline - min(l1_price, l2_price),
                }
    return None


def _detect_double_top(swing_highs: list, closes: np.ndarray, atr: float) -> dict | None:
    """Two highs at similar price with a trough between them."""
    if len(swing_highs) < 2:
        return None
    for i in range(len(swing_highs) - 1):
        h1_idx, h1_price = swing_highs[i]
        h2_idx, h2_price = swing_highs[i + 1]
        if abs(h1_price - h2_price) <= atr * 0.5 and h2_idx - h1_idx >= 10:
            neckline = float(np.min(closes[h1_idx:h2_idx]))
            if closes[-1] < h2_price:
                return {
                    "name": "DOUBLE_TOP", "direction": "SHORT", "score": 17,
                    "neckline": neckline,
                    "target_distance": max(h1_price, h2_price) - neckline,
                }
    return None


def _detect_triple_bottom(swing_lows: list, closes: np.ndarray, atr: float) -> dict | None:
    """Three lows at similar price — strongest pattern."""
    if len(swing_lows) < 3:
        return None
    for i in range(len(swing_lows) - 2):
        l1, l2, l3 = swing_lows[i], swing_lows[i + 1], swing_lows[i + 2]
        prices = [l1[1], l2[1], l3[1]]
        if max(prices) - min(prices) <= atr * 0.5:
            neckline = float(np.max(closes[l1[0]:l3[0]]))
            return {
                "name": "TRIPLE_BOTTOM", "direction": "LONG", "score": 20,
                "neckline": neckline,
                "target_distance": neckline - min(prices),
            }
    return None


def _detect_head_and_shoulders(
    swing_highs: list, swing_lows: list, closes: np.ndarray, atr: float,
) -> dict | None:
    """Head and Shoulders: middle peak higher than two shoulders."""
    if len(swing_highs) < 3:
        return None
    for i in range(len(swing_highs) - 2):
        ls, head, rs = swing_highs[i], swing_highs[i + 1], swing_highs[i + 2]
        if (head[1] > ls[1] and head[1] > rs[1]
            and abs(ls[1] - rs[1]) <= atr * 1.0
            and head[1] - max(ls[1], rs[1]) >= atr * 0.5):
            neckline_vals = closes[ls[0]:rs[0]]
            neckline = float(np.min(neckline_vals)) if len(neckline_vals) > 0 else float(closes[-1])
            return {
                "name": "HEAD_AND_SHOULDERS", "direction": "SHORT", "score": 18,
                "neckline": neckline,
                "target_distance": head[1] - neckline,
            }
    return None


def _detect_inv_head_and_shoulders(
    swing_highs: list, swing_lows: list, closes: np.ndarray, atr: float,
) -> dict | None:
    """Inverse H&S: middle trough deeper than two shoulders."""
    if len(swing_lows) < 3:
        return None
    for i in range(len(swing_lows) - 2):
        ls, head, rs = swing_lows[i], swing_lows[i + 1], swing_lows[i + 2]
        if (head[1] < ls[1] and head[1] < rs[1]
            and abs(ls[1] - rs[1]) <= atr * 1.0
            and min(ls[1], rs[1]) - head[1] >= atr * 0.5):
            neckline_vals = closes[ls[0]:rs[0]]
            neckline = float(np.max(neckline_vals)) if len(neckline_vals) > 0 else float(closes[-1])
            return {
                "name": "INVERSE_HEAD_AND_SHOULDERS", "direction": "LONG", "score": 18,
                "neckline": neckline,
                "target_distance": neckline - head[1],
            }
    return None


def _detect_ascending_triangle(
    swing_highs: list, swing_lows: list, atr: float,
) -> dict | None:
    """Flat resistance with rising support."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None
    recent_highs = swing_highs[-3:]
    recent_lows = swing_lows[-3:]
    h_prices = [h[1] for h in recent_highs]
    l_prices = [l[1] for l in recent_lows]
    # Flat top: highs within ATR*0.3
    if max(h_prices) - min(h_prices) <= atr * 0.3:
        # Rising lows
        if all(l_prices[i] < l_prices[i + 1] for i in range(len(l_prices) - 1)):
            return {
                "name": "ASCENDING_TRIANGLE", "direction": "LONG", "score": 15,
                "resistance": float(np.mean(h_prices)),
                "target_distance": float(np.mean(h_prices)) - min(l_prices),
            }
    return None


def _detect_descending_triangle(
    swing_highs: list, swing_lows: list, atr: float,
) -> dict | None:
    """Flat support with falling resistance."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None
    recent_highs = swing_highs[-3:]
    recent_lows = swing_lows[-3:]
    h_prices = [h[1] for h in recent_highs]
    l_prices = [l[1] for l in recent_lows]
    # Flat bottom: lows within ATR*0.3
    if max(l_prices) - min(l_prices) <= atr * 0.3:
        # Falling highs
        if all(h_prices[i] > h_prices[i + 1] for i in range(len(h_prices) - 1)):
            return {
                "name": "DESCENDING_TRIANGLE", "direction": "SHORT", "score": 15,
                "support": float(np.mean(l_prices)),
                "target_distance": max(h_prices) - float(np.mean(l_prices)),
            }
    return None


def detect_chart_patterns(candles: list[dict], atr_val: float) -> list[dict]:
    """
    Detect chart patterns from candle data.
    Returns list of {name, direction, score, neckline?, target_distance}.
    """
    if len(candles) < 50:
        return []

    arr = candles_to_arrays(candles)
    highs, lows, closes = arr["high"], arr["low"], arr["close"]

    swing_highs, swing_lows = _find_swings(highs, lows, order=10)
    patterns: list[dict] = []

    for detect_fn in [
        lambda: _detect_triple_bottom(swing_lows, closes, atr_val),
        lambda: _detect_double_bottom(swing_lows, closes, atr_val),
        lambda: _detect_double_top(swing_highs, closes, atr_val),
        lambda: _detect_head_and_shoulders(swing_highs, swing_lows, closes, atr_val),
        lambda: _detect_inv_head_and_shoulders(swing_highs, swing_lows, closes, atr_val),
        lambda: _detect_ascending_triangle(swing_highs, swing_lows, atr_val),
        lambda: _detect_descending_triangle(swing_highs, swing_lows, atr_val),
    ]:
        result = detect_fn()
        if result:
            patterns.append(result)

    return patterns
