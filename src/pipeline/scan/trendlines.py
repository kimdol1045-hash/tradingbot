"""
Trendline Detection — swing-point based linear regression.
Connects recent swing highs (resistance TL) or swing lows (support TL).
R^2 > 0.80 required.
"""
from __future__ import annotations

import numpy as np

from src.utils.indicators import candles_to_arrays


def _find_swing_points(
    highs: np.ndarray, lows: np.ndarray, order: int = 5,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (swing_highs, swing_lows) as list of (index, price)."""
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    n = len(highs)
    if n < 2 * order + 1:
        return swing_highs, swing_lows

    for i in range(order, n - order):
        if highs[i] == np.max(highs[i - order : i + order + 1]):
            swing_highs.append((i, float(highs[i])))
        if lows[i] == np.min(lows[i - order : i + order + 1]):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def _fit_line(points: list[tuple[int, float]]) -> dict | None:
    """Fit a line y = mx + b and return slope, intercept, R^2."""
    if len(points) < 3:
        return None

    x = np.array([p[0] for p in points], dtype=np.float64)
    y = np.array([p[1] for p in points], dtype=np.float64)

    coeffs = np.polyfit(x, y, 1)
    slope, intercept = coeffs[0], coeffs[1]

    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "r_squared": float(r_squared),
        "points": points,
        "start_idx": int(x[0]),
        "end_idx": int(x[-1]),
    }


def detect_trendlines(candles: list[dict], params: dict) -> list[dict]:
    """
    Detect valid trendlines from candle data.

    Returns list of trendlines:
        {type, slope, intercept, r_squared, current_price_at_line, points}
    """
    if len(candles) < 30:
        return []

    arr = candles_to_arrays(candles)
    highs, lows = arr["high"], arr["low"]
    min_r2 = params.get("min_r_squared", 0.80)

    swing_highs, swing_lows = _find_swing_points(highs, lows, order=5)
    trendlines: list[dict] = []

    # Try fitting resistance trendline (connecting swing highs)
    if len(swing_highs) >= 3:
        # Use most recent swing highs
        recent_highs = swing_highs[-6:]
        fit = _fit_line(recent_highs)
        if fit and fit["r_squared"] >= min_r2:
            n = len(candles) - 1
            current_at_line = fit["slope"] * n + fit["intercept"]
            fit["type"] = "resistance"
            fit["current_price"] = float(current_at_line)
            trendlines.append(fit)

    # Try fitting support trendline (connecting swing lows)
    if len(swing_lows) >= 3:
        recent_lows = swing_lows[-6:]
        fit = _fit_line(recent_lows)
        if fit and fit["r_squared"] >= min_r2:
            n = len(candles) - 1
            current_at_line = fit["slope"] * n + fit["intercept"]
            fit["type"] = "support"
            fit["current_price"] = float(current_at_line)
            trendlines.append(fit)

    return trendlines
