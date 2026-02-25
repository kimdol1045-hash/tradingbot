"""
Phase 3: SCAN — 차트 구조 + 변곡점 + 패턴 분석 통합 탐지.
S/R, trendlines, volume profile, T1-T8 inflection, candlestick/chart patterns.
Returns ScanResult.
~150ms target.
"""
from __future__ import annotations

import logging

import numpy as np

from src.pipeline.models import PatternMatch, PatternResult, ScanResult
from src.pipeline.scan.candlestick import detect_candlestick_patterns
from src.pipeline.scan.chart_patterns import detect_chart_patterns
from src.pipeline.scan.inflection import detect_inflections
from src.pipeline.scan.sr_levels import detect_sr_levels
from src.pipeline.scan.trendlines import detect_trendlines
from src.pipeline.scan.volume_profile import detect_volume_profile
from src.utils.indicators import atr_single, candles_to_arrays

logger = logging.getLogger(__name__)

# ═══ Regime-Type Weights ═══

REGIME_TYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "STRONG_UPTREND":   {"t1": 0.80, "t2": 0.90, "t3": 0.95, "t4": 0.60, "t5": 0.50, "t6": 0.85, "t7": 0.30, "t8": 0.70},
    "WEAK_UPTREND":     {"t1": 0.85, "t2": 0.75, "t3": 0.80, "t4": 0.50, "t5": 0.55, "t6": 0.70, "t7": 0.25, "t8": 0.65},
    "SIDEWAYS":         {"t1": 0.90, "t2": 0.30, "t3": 0.40, "t4": 0.20, "t5": 0.70, "t6": 0.50, "t7": 0.15, "t8": 0.80},
    "WEAK_DOWNTREND":   {"t1": 0.80, "t2": 0.70, "t3": 0.75, "t4": 0.85, "t5": 0.50, "t6": 0.80, "t7": 0.40, "t8": 0.65},
    "STRONG_DOWNTREND": {"t1": 0.70, "t2": 0.80, "t3": 0.90, "t4": 0.95, "t5": 0.40, "t6": 0.90, "t7": 0.50, "t8": 0.70},
    "VOLATILE":         {"t1": 0.20, "t2": 0.10, "t3": 0.15, "t4": 0.10, "t5": 0.15, "t6": 0.10, "t7": 0.05, "t8": 0.40},
}

# ═══ Pattern Synergy ═══

SYNERGY_COMBOS: dict[tuple[str, str], int] = {
    ("T4_TRENDLINE_BREAK", "T7_VOLUME_EXPLOSION"): 15,
    ("T3_BREAKOUT_RETEST", "T7_VOLUME_EXPLOSION"): 15,
    ("T1_SR_REACTION", "T7_VOLUME_EXPLOSION"): 12,
    ("T2_TRENDLINE_REACTION", "T7_VOLUME_EXPLOSION"): 10,
    ("T4_TRENDLINE_BREAK", "T6_DIVERGENCE"): 12,
    ("T3_BREAKOUT_RETEST", "T2_TRENDLINE_REACTION"): 12,
    ("T1_SR_REACTION", "T6_DIVERGENCE"): 10,
    ("T1_SR_REACTION", "T2_TRENDLINE_REACTION"): 8,
    ("T3_BREAKOUT_RETEST", "T6_DIVERGENCE"): 8,
    ("T1_SR_REACTION", "T8_FUNDING_OI_SIGNAL"): 10,
    ("T8_FUNDING_OI_SIGNAL", "T3_BREAKOUT_RETEST"): 10,
    ("T8_FUNDING_OI_SIGNAL", "T6_DIVERGENCE"): 12,
}

CANDLESTICK_SYNERGY = {"tier_1": 5, "tier_2": 8, "tier_3": 12}

MTF_BONUS = {"A": 15, "B": 10, "C": 5, "D": -3, "F": -8, "NONE": 0}

PATTERN_BONUS_CAP = 25
DIRECTION_CONFLICT_PENALTY = -10


# ═══ MTF Grade ═══

def _calculate_mtf_grade(
    primary_direction: str | None,
    candles_by_tf: dict[str, list[dict]],
    primary_tf: str,
) -> str:
    """
    Calculate MTF convergence grade.
    A: 3+ TF agree, B: 2 TF, C: 1 TF, D: weak divergence, F: strong divergence.
    """
    if primary_direction is None:
        return "NONE"

    tfs = [tf for tf in candles_by_tf if tf != primary_tf and len(candles_by_tf[tf]) >= 5]
    if not tfs:
        return "NONE"

    agreements = 0
    for tf in tfs:
        candles = candles_by_tf[tf]
        close = candles[-1]["close"]
        prev_close = candles[-2]["close"] if len(candles) >= 2 else close
        tf_dir = "LONG" if close > prev_close else "SHORT"
        if tf_dir == primary_direction:
            agreements += 1

    total = len(tfs)
    if total == 0:
        return "NONE"

    ratio = agreements / total
    if ratio >= 0.9:
        return "A"
    elif ratio >= 0.65:
        return "B"
    elif ratio >= 0.4:
        return "C"
    elif ratio >= 0.2:
        return "D"
    else:
        return "F"


# ═══ Scoring ═══

def _compute_scan_score(
    inflections: list[dict],
    candlestick_pats: list[dict],
    chart_pats: list[dict],
    regime: str,
    mtf_grade: str,
    params: dict,
) -> tuple[float, str | None, str | None, list[str]]:
    """
    Compute final scan score from all detected patterns.

    Returns (score, primary_type, direction, confirmation_names).
    """
    if not inflections:
        return 0.0, None, None, []

    # Primary inflection (highest priority = lowest number)
    primary = inflections[0]
    primary_type = primary["type"]
    direction = primary["direction"]

    # Base score from primary inflection
    type_key = primary_type.lower().split("_")[0]  # "t1", "t2", etc.
    regime_weights = REGIME_TYPE_WEIGHTS.get(regime, REGIME_TYPE_WEIGHTS["SIDEWAYS"])
    regime_mult = regime_weights.get(type_key, 0.5)
    # Quality floor: strong inflections keep at least 40% of their raw score
    base_score = primary["score"] * max(regime_mult, 0.40)

    # Secondary inflection bonus
    secondary_bonus = 0.0
    confirmation_names: list[str] = []
    for inf in inflections[1:]:
        if inf["direction"] == direction:
            combo_key = (primary_type, inf["type"])
            combo_bonus = SYNERGY_COMBOS.get(combo_key, 0)
            if combo_bonus == 0:
                combo_key = (inf["type"], primary_type)
                combo_bonus = SYNERGY_COMBOS.get(combo_key, 0)
            secondary_bonus += inf["score"] * 0.3 + combo_bonus
            confirmation_names.append(inf["type"])
        elif inf["direction"] != "NEUTRAL":
            secondary_bonus += DIRECTION_CONFLICT_PENALTY

    # Candlestick pattern synergy
    pattern_bonus = 0.0
    for pat in candlestick_pats:
        if pat["direction"] == direction or pat["direction"] == "NEUTRAL":
            tier_key = f"tier_{pat['tier']}"
            pattern_bonus += CANDLESTICK_SYNERGY.get(tier_key, 0)
            confirmation_names.append(pat["name"])
        elif pat["direction"] != "NEUTRAL":
            pattern_bonus += DIRECTION_CONFLICT_PENALTY

    # Chart pattern synergy
    for pat in chart_pats:
        if pat["direction"] == direction:
            pattern_bonus += 15
            confirmation_names.append(pat["name"])
        elif pat["direction"] != "NEUTRAL":
            pattern_bonus += DIRECTION_CONFLICT_PENALTY

    pattern_bonus = min(pattern_bonus, PATTERN_BONUS_CAP)

    # MTF bonus
    mtf_bonus = MTF_BONUS.get(mtf_grade, 0)

    # T7 solo cap
    t7_solo_cap = params.get("t7_solo_cap", 50)
    if primary_type == "T7_VOLUME_EXPLOSION" and len(confirmation_names) == 0:
        total = min(base_score + mtf_bonus, t7_solo_cap)
    else:
        total = base_score + secondary_bonus + pattern_bonus + mtf_bonus

    score = max(0.0, min(total, 100.0))
    return score, primary_type, direction, confirmation_names


# ═══ Main Phase 3 Entry ═══

def phase3_scan(
    candles_by_tf: dict[str, list[dict]],
    regime_result,
    safety_result,
    agent_config: dict,
    params: dict,
    stats: dict | None = None,
) -> ScanResult:
    """
    Phase 3: SCAN — pattern detection and scoring.

    Args:
        candles_by_tf: {timeframe: candle_list}
        regime_result: RegimeResult from Phase 2
        safety_result: SafetyResult from Phase 1
        agent_config: {timeframes, agent_id, ...}
        params: Agent parameters from params.json
        stats: Market stats dict for T8 thresholds

    Returns:
        ScanResult
    """
    primary_tf = agent_config.get("timeframes", ["5m"])[0]
    candles = candles_by_tf.get(primary_tf, [])

    if not candles or len(candles) < 30:
        return ScanResult(found=False, primary_type=None, score=0.0, mtf_grade="NONE")

    # STAGE_2 blocks new scans
    if safety_result.stage == "STAGE_2":
        return ScanResult(found=False, primary_type=None, score=0.0, mtf_grade="NONE")

    arr = candles_to_arrays(candles)
    highs, lows, closes = arr["high"], arr["low"], arr["close"]
    atr_val = atr_single(highs, lows, closes, period=14)
    if atr_val <= 0:
        return ScanResult(found=False, primary_type=None, score=0.0, mtf_grade="NONE")

    scan_params = params.get("scan", {})
    regime = regime_result.regime
    stats = stats or {}

    # ━━━━ 3A: S/R Detection ━━━━
    sr_params = scan_params.get("sr", {
        "cluster_atr_ratio": 0.5, "max_levels": 5, "min_strength": 0.35,
    })
    sr_levels = detect_sr_levels(candles, sr_params)

    # ━━━━ 3B: Trendlines ━━━━
    tl_params = scan_params.get("trendline", {"min_r_squared": 0.80})
    trendlines = detect_trendlines(candles, tl_params)

    # ━━━━ 3C: Volume Profile ━━━━
    vp_params = scan_params.get("volume_profile", {"num_bins": 50})
    vol_profile = detect_volume_profile(candles, vp_params)

    # ━━━━ 3D: Inflection Points (T1-T8) ━━━━
    inflections = detect_inflections(
        candles, sr_levels, trendlines, vol_profile, atr_val, stats,
    )

    # ━━━━ 3E: Candlestick Patterns ━━━━
    use_candlestick = params.get("pattern_policy", {}).get("candlestick_enabled", True)
    candlestick_pats = detect_candlestick_patterns(candles) if use_candlestick else []

    # ━━━━ 3F: Chart Patterns ━━━━
    use_chart = params.get("pattern_policy", {}).get("chart_pattern_enabled", False)
    chart_pats = detect_chart_patterns(candles, atr_val) if use_chart else []

    # ━━━━ 3G: MTF Grade ━━━━
    primary_direction = inflections[0]["direction"] if inflections else None
    mtf_grade = _calculate_mtf_grade(primary_direction, candles_by_tf, primary_tf)

    # ━━━━ 3H: Final Score ━━━━
    score, primary_type, direction, confirmation_names = _compute_scan_score(
        inflections, candlestick_pats, chart_pats, regime, mtf_grade, scan_params,
    )

    # Minimum score check — adaptive by regime
    # Trending regimes: patterns are more reliable → lower threshold
    # Volatile/sideways: need more confirmation → higher threshold
    default_regime_min = {
        "STRONG_UPTREND": 20, "WEAK_UPTREND": 25, "SIDEWAYS": 30,
        "WEAK_DOWNTREND": 25, "STRONG_DOWNTREND": 20, "VOLATILE": 40,
    }
    regime_min_scores = scan_params.get("regime_min_scores", default_regime_min)
    default_min = scan_params.get("min_score", 25)
    min_score = regime_min_scores.get(regime, default_min)
    found = score >= min_score and primary_type is not None

    # Data-driven SIDEWAYS regime filters (historically losing signal types)
    _SIDEWAYS_BLOCKED = {"T5_POC_MAGNET", "T4_TRENDLINE_BREAK"}
    if found and primary_type in _SIDEWAYS_BLOCKED and regime == "SIDEWAYS":
        logger.info("%s blocked in SIDEWAYS regime (data-driven filter)", primary_type)
        found = False

    # Build pattern result
    pattern_target_atr = None
    for cp in chart_pats:
        if cp.get("target_distance") and cp["direction"] == direction:
            pattern_target_atr = cp["target_distance"] / atr_val if atr_val > 0 else None
            break

    # Build PatternMatch list for patterns
    all_patterns: list[PatternMatch] = []
    for inf in inflections:
        all_patterns.append(PatternMatch(
            name=inf["type"], direction=inf["direction"],
            tier=0, score=inf["score"],
        ))
    for pat in candlestick_pats:
        all_patterns.append(PatternMatch(
            name=pat["name"], direction=pat["direction"],
            tier=pat["tier"], score=pat["score"],
        ))
    for pat in chart_pats:
        all_patterns.append(PatternMatch(
            name=pat["name"], direction=pat["direction"],
            tier=0, score=pat["score"],
        ))

    # Compute actual synergy bonus (secondary inflections + pattern bonuses, capped)
    actual_synergy = 0.0
    if inflections and direction:
        primary_inf = inflections[0]
        for inf in inflections[1:]:
            if inf["direction"] == direction:
                combo_key = (primary_inf["type"], inf["type"])
                combo_bonus = SYNERGY_COMBOS.get(combo_key, 0)
                if combo_bonus == 0:
                    combo_key = (inf["type"], primary_inf["type"])
                    combo_bonus = SYNERGY_COMBOS.get(combo_key, 0)
                actual_synergy += combo_bonus
        for pat in candlestick_pats:
            if pat["direction"] == direction or pat["direction"] == "NEUTRAL":
                tier_key = f"tier_{pat['tier']}"
                actual_synergy += CANDLESTICK_SYNERGY.get(tier_key, 0)
        for pat in chart_pats:
            if pat["direction"] == direction:
                actual_synergy += 15
        actual_synergy = min(actual_synergy, PATTERN_BONUS_CAP)

    patterns = PatternResult(
        candlestick=candlestick_pats,
        chart=chart_pats,
        synergy_bonus=actual_synergy,
        confirmation_names=confirmation_names,
    )

    return ScanResult(
        found=found,
        primary_type=primary_type,
        direction=direction,
        score=round(score, 2),
        mtf_grade=mtf_grade,
        patterns=patterns,
        atr=atr_val,
        pattern_target_atr=pattern_target_atr,
        sr_levels=sr_levels,
        trendlines=trendlines,
        vol_profile=vol_profile,
        entry_price=float(closes[-1]),
    )
