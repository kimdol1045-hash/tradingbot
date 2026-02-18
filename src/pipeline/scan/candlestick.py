"""
Candlestick Pattern Detection — 22 patterns across 3 tiers.
Tier 1: single candle (+5~10)
Tier 2: 2-candle combos (+8~15)
Tier 3: 3-candle combos (+12~18)
"""
from __future__ import annotations

import numpy as np

from src.utils.indicators import candles_to_arrays


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _upper_shadow(h: float, o: float, c: float) -> float:
    return h - max(o, c)


def _lower_shadow(l: float, o: float, c: float) -> float:
    return min(o, c) - l


def _is_bullish(o: float, c: float) -> bool:
    return c > o


def _is_bearish(o: float, c: float) -> bool:
    return c < o


def _candle_range(h: float, l: float) -> float:
    return h - l


# ═══ Tier 1: Single Candle Patterns ═══

def _detect_hammer(o, h, l, c, avg_range):
    body = _body(o, c)
    lower = _lower_shadow(l, o, c)
    upper = _upper_shadow(h, o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.5:
        return None
    if lower >= body * 2 and upper <= body * 0.3 and body > 0:
        return {"name": "HAMMER", "direction": "LONG", "tier": 1, "score": 8}
    return None


def _detect_inverted_hammer(o, h, l, c, avg_range):
    body = _body(o, c)
    lower = _lower_shadow(l, o, c)
    upper = _upper_shadow(h, o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.5:
        return None
    if upper >= body * 2 and lower <= body * 0.3 and body > 0:
        return {"name": "INVERTED_HAMMER", "direction": "LONG", "tier": 1, "score": 7}
    return None


def _detect_shooting_star(o, h, l, c, avg_range):
    body = _body(o, c)
    lower = _lower_shadow(l, o, c)
    upper = _upper_shadow(h, o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.5:
        return None
    if upper >= body * 2 and lower <= body * 0.3 and _is_bearish(o, c) and body > 0:
        return {"name": "SHOOTING_STAR", "direction": "SHORT", "tier": 1, "score": 8}
    return None


def _detect_hanging_man(o, h, l, c, avg_range):
    body = _body(o, c)
    lower = _lower_shadow(l, o, c)
    upper = _upper_shadow(h, o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.5:
        return None
    if lower >= body * 2 and upper <= body * 0.3 and _is_bearish(o, c) and body > 0:
        return {"name": "HANGING_MAN", "direction": "SHORT", "tier": 1, "score": 7}
    return None


def _detect_doji(o, h, l, c, avg_range):
    body = _body(o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.3:
        return None
    if body <= rng * 0.1 and rng > 0:
        return {"name": "DOJI", "direction": "NEUTRAL", "tier": 1, "score": 5}
    return None


def _detect_dragonfly_doji(o, h, l, c, avg_range):
    body = _body(o, c)
    lower = _lower_shadow(l, o, c)
    upper = _upper_shadow(h, o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.3:
        return None
    if body <= rng * 0.1 and lower >= rng * 0.7 and upper <= rng * 0.1:
        return {"name": "DRAGONFLY_DOJI", "direction": "LONG", "tier": 1, "score": 8}
    return None


def _detect_gravestone_doji(o, h, l, c, avg_range):
    body = _body(o, c)
    lower = _lower_shadow(l, o, c)
    upper = _upper_shadow(h, o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.3:
        return None
    if body <= rng * 0.1 and upper >= rng * 0.7 and lower <= rng * 0.1:
        return {"name": "GRAVESTONE_DOJI", "direction": "SHORT", "tier": 1, "score": 8}
    return None


def _detect_marubozu_bull(o, h, l, c, avg_range):
    body = _body(o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.8:
        return None
    upper = _upper_shadow(h, o, c)
    lower = _lower_shadow(l, o, c)
    if _is_bullish(o, c) and body >= rng * 0.9 and upper <= rng * 0.05 and lower <= rng * 0.05:
        return {"name": "MARUBOZU_BULL", "direction": "LONG", "tier": 1, "score": 9}
    return None


def _detect_marubozu_bear(o, h, l, c, avg_range):
    body = _body(o, c)
    rng = _candle_range(h, l)
    if rng < avg_range * 0.8:
        return None
    upper = _upper_shadow(h, o, c)
    lower = _lower_shadow(l, o, c)
    if _is_bearish(o, c) and body >= rng * 0.9 and upper <= rng * 0.05 and lower <= rng * 0.05:
        return {"name": "MARUBOZU_BEAR", "direction": "SHORT", "tier": 1, "score": 9}
    return None


# ═══ Tier 2: 2-Candle Patterns ═══

def _detect_bullish_engulfing(candles, avg_range):
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    if (_is_bearish(prev["open"], prev["close"])
        and _is_bullish(curr["open"], curr["close"])
        and curr["close"] > prev["open"]
        and curr["open"] < prev["close"]
        and _body(curr["open"], curr["close"]) > _body(prev["open"], prev["close"])):
        return {"name": "BULLISH_ENGULFING", "direction": "LONG", "tier": 2, "score": 12}
    return None


def _detect_bearish_engulfing(candles, avg_range):
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    if (_is_bullish(prev["open"], prev["close"])
        and _is_bearish(curr["open"], curr["close"])
        and curr["close"] < prev["open"]
        and curr["open"] > prev["close"]
        and _body(curr["open"], curr["close"]) > _body(prev["open"], prev["close"])):
        return {"name": "BEARISH_ENGULFING", "direction": "SHORT", "tier": 2, "score": 12}
    return None


def _detect_tweezer_bottom(candles, avg_range):
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    tol = avg_range * 0.05
    if abs(prev["low"] - curr["low"]) <= tol and _is_bearish(prev["open"], prev["close"]) and _is_bullish(curr["open"], curr["close"]):
        return {"name": "TWEEZER_BOTTOM", "direction": "LONG", "tier": 2, "score": 10}
    return None


def _detect_tweezer_top(candles, avg_range):
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    tol = avg_range * 0.05
    if abs(prev["high"] - curr["high"]) <= tol and _is_bullish(prev["open"], prev["close"]) and _is_bearish(curr["open"], curr["close"]):
        return {"name": "TWEEZER_TOP", "direction": "SHORT", "tier": 2, "score": 10}
    return None


def _detect_piercing_line(candles, avg_range):
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    prev_mid = (prev["open"] + prev["close"]) / 2
    if (_is_bearish(prev["open"], prev["close"])
        and _is_bullish(curr["open"], curr["close"])
        and curr["open"] < prev["close"]
        and curr["close"] > prev_mid
        and curr["close"] < prev["open"]):
        return {"name": "PIERCING_LINE", "direction": "LONG", "tier": 2, "score": 10}
    return None


def _detect_dark_cloud_cover(candles, avg_range):
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    prev_mid = (prev["open"] + prev["close"]) / 2
    if (_is_bullish(prev["open"], prev["close"])
        and _is_bearish(curr["open"], curr["close"])
        and curr["open"] > prev["close"]
        and curr["close"] < prev_mid
        and curr["close"] > prev["open"]):
        return {"name": "DARK_CLOUD_COVER", "direction": "SHORT", "tier": 2, "score": 10}
    return None


# ═══ Tier 3: 3-Candle Patterns ═══

def _detect_morning_star(candles, avg_range):
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    c2_body = _body(c2["open"], c2["close"])
    c1_body = _body(c1["open"], c1["close"])
    if (_is_bearish(c1["open"], c1["close"])
        and c1_body > avg_range * 0.5
        and c2_body < avg_range * 0.3
        and _is_bullish(c3["open"], c3["close"])
        and c3["close"] > (c1["open"] + c1["close"]) / 2):
        return {"name": "MORNING_STAR", "direction": "LONG", "tier": 3, "score": 15}
    return None


def _detect_evening_star(candles, avg_range):
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    c2_body = _body(c2["open"], c2["close"])
    c1_body = _body(c1["open"], c1["close"])
    if (_is_bullish(c1["open"], c1["close"])
        and c1_body > avg_range * 0.5
        and c2_body < avg_range * 0.3
        and _is_bearish(c3["open"], c3["close"])
        and c3["close"] < (c1["open"] + c1["close"]) / 2):
        return {"name": "EVENING_STAR", "direction": "SHORT", "tier": 3, "score": 15}
    return None


def _detect_three_white_soldiers(candles, avg_range):
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if (all(_is_bullish(c["open"], c["close"]) for c in [c1, c2, c3])
        and c2["close"] > c1["close"] and c3["close"] > c2["close"]
        and all(_body(c["open"], c["close"]) > avg_range * 0.4 for c in [c1, c2, c3])):
        return {"name": "THREE_WHITE_SOLDIERS", "direction": "LONG", "tier": 3, "score": 16}
    return None


def _detect_three_black_crows(candles, avg_range):
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if (all(_is_bearish(c["open"], c["close"]) for c in [c1, c2, c3])
        and c2["close"] < c1["close"] and c3["close"] < c2["close"]
        and all(_body(c["open"], c["close"]) > avg_range * 0.4 for c in [c1, c2, c3])):
        return {"name": "THREE_BLACK_CROWS", "direction": "SHORT", "tier": 3, "score": 16}
    return None


def _detect_three_inside_up(candles, avg_range):
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if (_is_bearish(c1["open"], c1["close"])
        and _is_bullish(c2["open"], c2["close"])
        and c2["close"] < c1["open"] and c2["open"] > c1["close"]
        and _is_bullish(c3["open"], c3["close"])
        and c3["close"] > c1["open"]):
        return {"name": "THREE_INSIDE_UP", "direction": "LONG", "tier": 3, "score": 13}
    return None


def _detect_three_inside_down(candles, avg_range):
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if (_is_bullish(c1["open"], c1["close"])
        and _is_bearish(c2["open"], c2["close"])
        and c2["close"] > c1["open"] and c2["open"] < c1["close"]
        and _is_bearish(c3["open"], c3["close"])
        and c3["close"] < c1["open"]):
        return {"name": "THREE_INSIDE_DOWN", "direction": "SHORT", "tier": 3, "score": 13}
    return None


# ═══ Main Detection ═══

def detect_candlestick_patterns(candles: list[dict]) -> list[dict]:
    """
    Detect all candlestick patterns in the most recent candles.
    Returns list of {name, direction, tier, score}.
    """
    if len(candles) < 3:
        return []

    arr = candles_to_arrays(candles)
    ranges = arr["high"] - arr["low"]
    avg_range = float(np.mean(ranges[-20:])) if len(ranges) >= 20 else float(np.mean(ranges))

    curr = candles[-1]
    o, h, l, c = curr["open"], curr["high"], curr["low"], curr["close"]

    patterns: list[dict] = []

    # Tier 1
    for detect_fn in [
        _detect_hammer, _detect_inverted_hammer, _detect_shooting_star,
        _detect_hanging_man, _detect_doji, _detect_dragonfly_doji,
        _detect_gravestone_doji, _detect_marubozu_bull, _detect_marubozu_bear,
    ]:
        result = detect_fn(o, h, l, c, avg_range)
        if result:
            patterns.append(result)

    # Tier 2
    for detect_fn in [
        _detect_bullish_engulfing, _detect_bearish_engulfing,
        _detect_tweezer_bottom, _detect_tweezer_top,
        _detect_piercing_line, _detect_dark_cloud_cover,
    ]:
        result = detect_fn(candles, avg_range)
        if result:
            patterns.append(result)

    # Tier 3
    for detect_fn in [
        _detect_morning_star, _detect_evening_star,
        _detect_three_white_soldiers, _detect_three_black_crows,
        _detect_three_inside_up, _detect_three_inside_down,
    ]:
        result = detect_fn(candles, avg_range)
        if result:
            patterns.append(result)

    return patterns
