"""
S/R Level Detection — pivot-based clustering with ATR.
Finds local swing highs/lows, clusters nearby levels,
scores by touch count / volume / recency.
"""
from __future__ import annotations

import numpy as np

from src.utils.indicators import atr_single, candles_to_arrays


def _find_pivots(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    volumes: np.ndarray, timestamps: np.ndarray, order: int = 5,
) -> list[dict]:
    """Find local swing highs and lows using rolling window comparison."""
    pivots: list[dict] = []
    n = len(highs)
    if n < 2 * order + 1:
        return pivots

    for i in range(order, n - order):
        # Swing High: high[i] > all neighbors
        if highs[i] == np.max(highs[i - order : i + order + 1]):
            pivots.append({
                "type": "resistance",
                "price": float(highs[i]),
                "index": i,
                "volume": float(volumes[i]),
                "timestamp": int(timestamps[i]),
            })
        # Swing Low: low[i] < all neighbors
        if lows[i] == np.min(lows[i - order : i + order + 1]):
            pivots.append({
                "type": "support",
                "price": float(lows[i]),
                "index": i,
                "volume": float(volumes[i]),
                "timestamp": int(timestamps[i]),
            })
    return pivots


def _cluster_pivots(pivots: list[dict], cluster_dist: float) -> list[dict]:
    """Cluster nearby pivots into S/R zones."""
    if not pivots:
        return []

    sorted_pivots = sorted(pivots, key=lambda p: p["price"])
    clusters: list[list[dict]] = []
    current_cluster: list[dict] = [sorted_pivots[0]]

    for p in sorted_pivots[1:]:
        if abs(p["price"] - current_cluster[-1]["price"]) <= cluster_dist:
            current_cluster.append(p)
        else:
            clusters.append(current_cluster)
            current_cluster = [p]
    clusters.append(current_cluster)

    levels: list[dict] = []
    for cluster in clusters:
        prices = [p["price"] for p in cluster]
        volumes_c = [p["volume"] for p in cluster]
        timestamps_c = [p["timestamp"] for p in cluster]
        level_type = "support" if sum(1 for p in cluster if p["type"] == "support") > len(cluster) / 2 else "resistance"
        levels.append({
            "price": float(np.mean(prices)),
            "type": level_type,
            "touch_count": len(cluster),
            "avg_volume": float(np.mean(volumes_c)),
            "latest_ts": max(timestamps_c),
            "earliest_ts": min(timestamps_c),
        })
    return levels


def _score_levels(
    levels: list[dict], current_ts: int, params: dict,
) -> list[dict]:
    """Score S/R levels by touch count, volume, recency."""
    weights = params.get("strength_weights", {
        "touch_count": 0.45, "avg_volume": 0.30, "recency": 0.25,
    })

    if not levels:
        return []

    # Normalize each component to 0~1
    max_touch = max(l["touch_count"] for l in levels)
    max_vol = max(l["avg_volume"] for l in levels)
    max_age_ms = max((current_ts - l["earliest_ts"]) for l in levels)

    for level in levels:
        t_norm = level["touch_count"] / max_touch if max_touch > 0 else 0
        v_norm = level["avg_volume"] / max_vol if max_vol > 0 else 0
        age = current_ts - level["latest_ts"]
        r_norm = 1.0 - (age / max_age_ms) if max_age_ms > 0 else 1.0

        level["strength"] = round(
            t_norm * weights["touch_count"]
            + v_norm * weights["avg_volume"]
            + r_norm * weights["recency"],
            4,
        )
    return levels


def detect_sr_levels(candles: list[dict], params: dict) -> list[dict]:
    """
    Detect and score Support/Resistance levels.

    Returns list of {price, type, strength, touch_count, ...},
    sorted by strength descending, capped at max_levels.
    """
    if len(candles) < 20:
        return []

    arr = candles_to_arrays(candles)
    highs, lows, closes = arr["high"], arr["low"], arr["close"]
    volumes, timestamps = arr["volume"], arr["timestamp"]

    atr_val = atr_single(highs, lows, closes, period=14)
    cluster_dist = atr_val * params.get("cluster_atr_ratio", 0.5)

    pivots = _find_pivots(highs, lows, closes, volumes, timestamps, order=5)
    levels = _cluster_pivots(pivots, cluster_dist)

    current_ts = int(timestamps[-1])
    levels = _score_levels(levels, current_ts, params)

    # Filter by minimum strength
    min_strength = params.get("min_strength", 0.35)
    levels = [l for l in levels if l["strength"] >= min_strength]

    # Sort and cap
    levels.sort(key=lambda l: l["strength"], reverse=True)
    max_levels = params.get("max_levels", 5)
    return levels[:max_levels]
