"""
Technical indicators — pure numpy calculations on candle arrays.
Used by Phase 1-3 of the pipeline.
"""

from __future__ import annotations

import numpy as np


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Average True Range.
    Returns array same length as input (first `period` values are NaN).
    """
    h = np.asarray(highs, dtype=np.float64)
    l = np.asarray(lows, dtype=np.float64)
    c = np.asarray(closes, dtype=np.float64)

    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]

    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))

    result = np.full_like(tr, np.nan)
    if len(tr) < period:
        return result

    result[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period

    return result


def atr_single(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Return the latest ATR value."""
    values = atr(highs, lows, closes, period)
    valid = values[~np.isnan(values)]
    return float(valid[-1]) if len(valid) > 0 else 0.0


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index. Returns array with NaN padding."""
    c = np.asarray(closes, dtype=np.float64)
    deltas = np.diff(c)

    result = np.full(len(c), np.nan)
    if len(deltas) < period:
        return result

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    result[period] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            result[i + 1] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    return result


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    v = np.asarray(values, dtype=np.float64)
    result = np.full_like(v, np.nan)
    if len(v) < period:
        return result

    result[period - 1] = np.mean(v[:period])
    mult = 2.0 / (period + 1)
    for i in range(period, len(v)):
        result[i] = v[i] * mult + result[i - 1] * (1.0 - mult)
    return result


def sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    v = np.asarray(values, dtype=np.float64)
    result = np.full_like(v, np.nan)
    if len(v) < period:
        return result
    cumsum = np.cumsum(v)
    cumsum[period:] = cumsum[period:] - cumsum[:-period]
    result[period - 1 :] = cumsum[period - 1 :] / period
    return result


def macd(
    closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD line, signal line, histogram."""
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line[~np.isnan(macd_line)], signal)

    # Pad signal line to align with macd_line
    padded_signal = np.full_like(macd_line, np.nan)
    valid_start = np.argmax(~np.isnan(macd_line))
    if len(signal_line) > 0:
        padded_signal[valid_start + signal - 1 : valid_start + len(signal_line) + signal - 1] = signal_line[signal - 1:]

    histogram = macd_line - padded_signal
    return macd_line, padded_signal, histogram


def hurst_exponent(closes: np.ndarray, window: int = 100) -> float:
    """
    Hurst exponent via R/S analysis.
    H > 0.5: trending, H < 0.5: mean-reverting, H ≈ 0.5: random walk.
    Returns value in range [0.0, 1.0].
    """
    c = np.asarray(closes, dtype=np.float64)
    if len(c) < window:
        return 0.5  # default: random walk

    series = c[-window:]
    returns = np.diff(np.log(series))
    if len(returns) < 10:
        return 0.5

    # R/S for multiple sub-periods
    rs_values = []
    for n in [int(window * r) for r in [0.25, 0.5, 0.75, 1.0]]:
        n = max(n, 4)
        if n > len(returns):
            continue
        sub = returns[:n]
        mean_sub = np.mean(sub)
        deviate = np.cumsum(sub - mean_sub)
        r = np.max(deviate) - np.min(deviate)
        s = np.std(sub, ddof=1)
        if s > 0:
            rs_values.append((np.log(n), np.log(r / s)))

    if len(rs_values) < 2:
        return 0.5

    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])
    slope = np.polyfit(x, y, 1)[0]
    return float(np.clip(slope, 0.0, 1.0))


def shannon_entropy(closes: np.ndarray, window: int = 20, bins: int = 10) -> float:
    """
    Shannon entropy of price returns.
    Higher entropy = more uncertain/random. Range [0, 1] normalized.
    """
    c = np.asarray(closes, dtype=np.float64)
    if len(c) < window + 1:
        return 0.5

    returns = np.diff(c[-window - 1 :]) / c[-window - 1 : -1]
    counts, _ = np.histogram(returns, bins=bins)
    probs = counts / counts.sum()
    probs = probs[probs > 0]

    max_entropy = np.log2(bins) if bins > 1 else 1.0
    entropy = -np.sum(probs * np.log2(probs))
    return float(np.clip(entropy / max_entropy, 0.0, 1.0))


def price_direction(closes: np.ndarray, window: int = 20) -> float:
    """Return +1.0 for uptrend, -1.0 for downtrend based on linear regression slope."""
    c = np.asarray(closes, dtype=np.float64)
    if len(c) < window:
        return 0.0
    segment = c[-window:]
    x = np.arange(window, dtype=np.float64)
    slope = np.polyfit(x, segment, 1)[0]
    mean_price = np.mean(segment)
    if mean_price == 0:
        return 0.0
    norm_slope = slope / mean_price * window
    return float(np.clip(norm_slope * 10, -1.0, 1.0))


def rolling_zscore(value: float, values_history: np.ndarray) -> float:
    """
    Compute z-score of `value` against historical distribution.
    Maps to 0~100 scale.
    """
    if len(values_history) < 2:
        return 50.0
    mean = np.mean(values_history)
    std = np.std(values_history, ddof=1)
    if std < 1e-10:
        return 50.0
    z = (value - mean) / std
    # Map z-score [-3, 3] → [0, 100]
    return float(np.clip((z + 3) / 6 * 100, 0, 100))


def percentile_rank(value: float, values_history: np.ndarray) -> float:
    """Return percentile rank (0~100) of value within history."""
    if len(values_history) == 0:
        return 50.0
    return float(np.sum(values_history <= value) / len(values_history) * 100)


def candles_to_arrays(candles: list[dict]) -> dict[str, np.ndarray]:
    """Convert list of candle dicts to numpy arrays."""
    if not candles:
        return {
            "open": np.array([]),
            "high": np.array([]),
            "low": np.array([]),
            "close": np.array([]),
            "volume": np.array([]),
            "timestamp": np.array([]),
        }
    return {
        "open": np.array([c["open"] for c in candles], dtype=np.float64),
        "high": np.array([c["high"] for c in candles], dtype=np.float64),
        "low": np.array([c["low"] for c in candles], dtype=np.float64),
        "close": np.array([c["close"] for c in candles], dtype=np.float64),
        "volume": np.array([c["volume"] for c in candles], dtype=np.float64),
        "timestamp": np.array([c["timestamp"] for c in candles], dtype=np.int64),
    }
