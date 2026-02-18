"""
Phase 4: GATE — MDD/PF 중심 리스크 검증.
Technical indicator scoring, MDD adjustments, cost model,
exposure check, rolling PF anti-stall.
Returns GateResult.
~50ms target.
"""
from __future__ import annotations

import logging

import numpy as np

from src.pipeline.models import GateResult
from src.utils.config import COST_MODEL, MDD_POLICIES
from src.utils.indicators import adx, candles_to_arrays, ichimoku, rsi

logger = logging.getLogger(__name__)

# ═══ PF Adjustments ═══

PF_ADJUSTMENTS = {
    "above_2.5": -5,
    "2.0_to_2.5": 0,
    "1.5_to_2.0": 5,
    "1.0_to_1.5": 10,
    "below_1.0": 15,
}


def _pf_tier(rolling_pf: float) -> str:
    if rolling_pf >= 2.5:
        return "above_2.5"
    if rolling_pf >= 2.0:
        return "2.0_to_2.5"
    if rolling_pf >= 1.5:
        return "1.5_to_2.0"
    if rolling_pf >= 1.0:
        return "1.0_to_1.5"
    return "below_1.0"


# ═══ Technical Indicator Scoring (8 items, 100 total) ═══

def _score_rsi(candles: list[dict], direction: str) -> float:
    """RSI evaluation: 0~20 points."""
    arr = candles_to_arrays(candles)
    rsi_vals = rsi(arr["close"], period=14)
    valid = rsi_vals[~np.isnan(rsi_vals)]
    if len(valid) == 0:
        return 10.0

    current_rsi = float(valid[-1])

    if direction == "LONG":
        if current_rsi < 30:
            return 18.0  # Oversold = good for long
        elif current_rsi < 45:
            return 14.0
        elif current_rsi < 55:
            return 10.0
        elif current_rsi < 70:
            return 6.0
        else:
            return 2.0  # Overbought = bad for long
    else:  # SHORT
        if current_rsi > 70:
            return 18.0
        elif current_rsi > 55:
            return 14.0
        elif current_rsi > 45:
            return 10.0
        elif current_rsi > 30:
            return 6.0
        else:
            return 2.0


def _score_volume_cvd(candles: list[dict], direction: str) -> float:
    """Volume/CVD alignment: 0~10 points."""
    if len(candles) < 5:
        return 5.0

    # Simple CVD proxy: sum of (close > open ? +vol : -vol)
    cvd = 0.0
    for c in candles[-10:]:
        if c["close"] > c["open"]:
            cvd += c["volume"]
        else:
            cvd -= c["volume"]

    if direction == "LONG" and cvd > 0:
        return 8.0
    elif direction == "SHORT" and cvd < 0:
        return 8.0
    elif abs(cvd) < sum(c["volume"] for c in candles[-10:]) * 0.1:
        return 5.0  # Neutral
    else:
        return 2.0


def _score_htf_consensus(regime_result, direction: str) -> float:
    """HTF agreement: 0~15 points."""
    alignment = regime_result.alignment
    regime = regime_result.regime

    # Check regime agrees with direction
    bullish_regimes = {"STRONG_UPTREND", "WEAK_UPTREND"}
    bearish_regimes = {"STRONG_DOWNTREND", "WEAK_DOWNTREND"}

    if direction == "LONG" and regime in bullish_regimes:
        base = 12.0
    elif direction == "SHORT" and regime in bearish_regimes:
        base = 12.0
    elif regime == "SIDEWAYS":
        base = 7.0
    elif regime == "VOLATILE":
        base = 3.0
    else:
        base = 5.0

    return min(base * (0.5 + alignment * 0.5), 15.0)


def _score_funding_alignment(candles: list[dict], direction: str) -> float:
    """Funding alignment: 0~10 points."""
    funding = candles[-1].get("funding_rate", 0) or 0

    # Counter-trend funding is favorable
    if direction == "LONG" and funding < 0:
        return 8.0  # Short-heavy funding, good for long
    elif direction == "SHORT" and funding > 0:
        return 8.0  # Long-heavy funding, good for short
    elif abs(funding) < 0.0001:
        return 5.0  # Neutral
    else:
        return 3.0


def _score_ichimoku(candles: list[dict], direction: str) -> float:
    """Ichimoku Cloud evaluation: 0~15 points."""
    arr = candles_to_arrays(candles)
    if len(arr["close"]) < 52:
        return 7.5  # Not enough data → neutral

    ichi = ichimoku(arr["high"], arr["low"], arr["close"])

    if direction == "LONG":
        if ichi["above_cloud"] and ichi["tenkan_above_kijun"]:
            return 14.0  # Strong bullish alignment
        elif ichi["above_cloud"]:
            return 11.0  # Price above cloud but TK cross weak
        elif ichi["in_cloud"] and ichi["tenkan_above_kijun"]:
            return 8.0   # Emerging from cloud
        elif ichi["in_cloud"]:
            return 5.0   # Undecided
        else:
            return 2.0   # Below cloud = bearish for long
    else:  # SHORT
        if ichi["below_cloud"] and not ichi["tenkan_above_kijun"]:
            return 14.0  # Strong bearish alignment
        elif ichi["below_cloud"]:
            return 11.0
        elif ichi["in_cloud"] and not ichi["tenkan_above_kijun"]:
            return 8.0
        elif ichi["in_cloud"]:
            return 5.0
        else:
            return 2.0   # Above cloud = bullish for short


def _score_adx(candles: list[dict], direction: str) -> float:
    """ADX trend strength evaluation: 0~10 points."""
    arr = candles_to_arrays(candles)
    if len(arr["close"]) < 30:
        return 5.0  # Not enough data → neutral

    adx_result = adx(arr["high"], arr["low"], arr["close"], period=14)
    adx_val = adx_result["adx"]
    plus_di = adx_result["plus_di"]
    minus_di = adx_result["minus_di"]

    # Direction alignment
    di_aligned = (direction == "LONG" and plus_di > minus_di) or \
                 (direction == "SHORT" and minus_di > plus_di)

    if adx_val >= 25:
        # Strong trend
        return 9.0 if di_aligned else 2.0
    elif adx_val >= 20:
        # Moderate trend
        return 7.0 if di_aligned else 4.0
    else:
        # Weak/no trend — slightly favor as range conditions can work
        return 5.0


def _score_liq_risk(candles: list[dict]) -> float:
    """Liquidation risk evaluation: 0~10 points. Higher = safer."""
    if len(candles) < 10:
        return 5.0

    # Recent liquidation volume relative to normal trading volume
    recent = candles[-10:]
    avg_volume = np.mean([c["volume"] for c in recent]) or 1.0
    avg_liq = np.mean([c.get("liquidation_vol", 0) or 0 for c in recent])

    # Liquidation ratio: high liquidation = risky environment
    liq_ratio = avg_liq / avg_volume if avg_volume > 0 else 0

    # Open interest changes — rapid OI increase can mean leveraged buildup
    oi_values = [c.get("open_interest", 0) or 0 for c in recent]
    if oi_values[0] > 0:
        oi_change = (oi_values[-1] - oi_values[0]) / oi_values[0]
    else:
        oi_change = 0.0

    # Score: low liq + moderate OI = safe (high score)
    score = 7.0  # base

    # Penalize high liquidation activity
    if liq_ratio > 0.5:
        score -= 4.0
    elif liq_ratio > 0.2:
        score -= 2.0
    elif liq_ratio > 0.1:
        score -= 1.0

    # Penalize rapid OI buildup (potential cascade risk)
    if abs(oi_change) > 0.15:
        score -= 2.0
    elif abs(oi_change) > 0.08:
        score -= 1.0

    return float(np.clip(score, 0.0, 10.0))


def _score_atr_regime(candles: list[dict], scan_atr: float) -> float:
    """ATR regime: 0~10 points. Moderate ATR = best."""
    arr = candles_to_arrays(candles)
    closes = arr["close"]
    if len(closes) < 20 or closes[-1] <= 0:
        return 5.0

    atr_pct = scan_atr / closes[-1] * 100
    # Sweet spot: 0.3~1.5% ATR
    if 0.3 <= atr_pct <= 1.5:
        return 9.0
    elif 0.1 <= atr_pct <= 3.0:
        return 6.0
    else:
        return 3.0


def _calculate_tech_score(
    candles: list[dict],
    direction: str,
    regime_result,
    scan_atr: float,
) -> float:
    """Total technical indicator score: 0~100."""
    rsi_score = _score_rsi(candles, direction)              # 0~20
    vol_score = _score_volume_cvd(candles, direction)       # 0~10
    htf_score = _score_htf_consensus(regime_result, direction)  # 0~15
    funding_score = _score_funding_alignment(candles, direction)  # 0~10
    atr_score = _score_atr_regime(candles, scan_atr)        # 0~10

    # Ichimoku Cloud: 0~15
    ichimoku_score = _score_ichimoku(candles, direction)
    # ADX trend strength: 0~10
    adx_score = _score_adx(candles, direction)
    # Liquidation risk: 0~10
    liq_risk_score = _score_liq_risk(candles)

    return rsi_score + vol_score + htf_score + funding_score + atr_score + ichimoku_score + adx_score + liq_risk_score


# ═══ Penalties ═══

def _calculate_penalty(candles: list[dict], regime_result, stats: dict) -> float:
    """Calculate penalty (max -30)."""
    penalty = 0.0

    # Spread blowout
    spread = candles[-1].get("bid_ask_spread", 0) or 0
    spread_p90 = stats.get("spread_p90", 100.0)
    if spread > spread_p90:
        penalty -= 8.0

    # Orderbook imbalance
    imbalance = abs(candles[-1].get("orderbook_imbalance", 0) or 0)
    imbalance_p90 = stats.get("imbalance_p90", 0.8)
    if imbalance > imbalance_p90:
        penalty -= 5.0

    # Regime transition instability
    if regime_result.in_transition and regime_result.blend_progress < 0.5:
        penalty -= 10.0

    return max(penalty, -30.0)


# ═══ Exposure Check ═══

def _check_exposure(
    scan_result, agent_state: dict, params: dict,
) -> tuple[bool, float]:
    """
    Check portfolio exposure limits.
    Returns (blocked, size_mult_reduction).
    """
    exposure = params.get("exposure", {})
    hard_block_pct = exposure.get("hard_block_pct", 0.30)
    reduce_pct = exposure.get("reduce_pct", 0.20)
    min_size_mult = exposure.get("min_size_mult", 0.30)
    estimated_sl = exposure.get("estimated_sl_pct", 0.02)

    capital = agent_state.get("capital", 10000)
    open_risk = agent_state.get("open_risk", 0.0)

    # Estimate new position risk
    new_risk = scan_result.entry_price * estimated_sl if scan_result.entry_price > 0 else 0
    risk_pct = (open_risk + new_risk) / capital if capital > 0 else 0

    if risk_pct >= hard_block_pct:
        return True, 0.0

    if risk_pct >= reduce_pct:
        reduction = max(1.0 - (risk_pct - reduce_pct) / (hard_block_pct - reduce_pct), min_size_mult)
        return False, reduction

    return False, 1.0


# ═══ Main Phase 4 Entry ═══

def phase4_gate(
    candles: list[dict],
    scan_result,
    regime_result,
    safety_result,
    agent_state: dict,
    params: dict,
    stats: dict | None = None,
) -> GateResult:
    """
    Phase 4: GATE — risk validation and filtering.

    Args:
        candles: Primary timeframe candles
        scan_result: ScanResult from Phase 3
        regime_result: RegimeResult from Phase 2
        safety_result: SafetyResult from Phase 1
        agent_state: {capital, open_risk, rolling_pf, consecutive_losses, ...}
        params: Agent parameters
        stats: Market stats for penalty thresholds

    Returns:
        GateResult
    """
    stats = stats or {}

    # No pattern found → fail
    if not scan_result.found:
        return GateResult(
            passed=False, reason="NO_PATTERN",
            mdd_mode=safety_result.mdd_mode,
        )

    direction = scan_result.direction

    # ━━━━ 4A: MDD mode adjustments ━━━━
    mdd_mode = safety_result.mdd_mode
    mdd_policy = MDD_POLICIES.get(mdd_mode, MDD_POLICIES["normal"])
    leverage_mult = mdd_policy.get("leverage_mult", 1.0)
    size_mult = mdd_policy.get("size_mult", 1.0)
    score_adj = mdd_policy.get("score_adj", 0)

    # Defensive mode: only strong trends allowed
    if mdd_mode == "defensive":
        allowed = mdd_policy.get("allowed_regimes", [])
        if allowed and regime_result.regime not in allowed:
            return GateResult(
                passed=False, reason="REGIME_NOT_ALLOWED_IN_DEFENSIVE",
                mdd_mode=mdd_mode, leverage_mult=leverage_mult, size_mult=size_mult,
            )

    # Survival mode: max 1 position
    if mdd_mode == "survival":
        max_pos = mdd_policy.get("max_positions", 1)
        current_positions = agent_state.get("open_positions_count", 0)
        if current_positions >= max_pos:
            return GateResult(
                passed=False, reason="MAX_POSITIONS_SURVIVAL",
                mdd_mode=mdd_mode, leverage_mult=leverage_mult, size_mult=size_mult,
            )

    # ━━━━ 4B: Exposure check ━━━━
    exposure_blocked, exposure_mult = _check_exposure(scan_result, agent_state, params.get("gate", {}))
    if exposure_blocked:
        return GateResult(
            passed=False, reason="EXPOSURE_HARD_BLOCK",
            mdd_mode=mdd_mode, leverage_mult=leverage_mult, size_mult=size_mult,
        )
    size_mult *= exposure_mult

    # ━━━━ 4C: Technical scoring ━━━━
    tech_score = _calculate_tech_score(candles, direction, regime_result, scan_result.atr)

    # ━━━━ 4D: Inflection/MTF bonus ━━━━
    inflection_bonus = 0.0
    if scan_result.score >= 90:
        inflection_bonus = 5.0
    elif scan_result.score >= 80:
        inflection_bonus = 3.0
    elif scan_result.score < 70:
        inflection_bonus = -5.0

    mtf_modifier = {"A": 3, "B": 2, "C": 1, "D": -1, "F": -3, "NONE": 0}
    mtf_bonus = mtf_modifier.get(scan_result.mtf_grade, 0)

    # ━━━━ 4E: Penalties ━━━━
    penalty = _calculate_penalty(candles, regime_result, stats)

    # ━━━━ 4F: Final score ━━━━
    raw_score = tech_score + inflection_bonus + mtf_bonus + penalty
    final_score = max(0.0, min(raw_score, 100.0))

    # ━━━━ 4G: Pass threshold (dynamic) ━━━━
    gate_params = params.get("gate", {})
    base_pass_scores = gate_params.get("base_pass_scores", {
        "STRONG_UPTREND": 55, "WEAK_UPTREND": 60, "SIDEWAYS": 70,
        "WEAK_DOWNTREND": 60, "STRONG_DOWNTREND": 55, "VOLATILE": 80,
    })
    base = base_pass_scores.get(regime_result.regime, 65)

    rolling_pf = agent_state.get("rolling_pf", 2.0)
    pf_adj = PF_ADJUSTMENTS.get(_pf_tier(rolling_pf), 0)

    pass_threshold = max(45.0, min(base + score_adj + pf_adj, 95.0))

    # PF Anti-Stall: if PF < 1.0, allow exploration trades
    pf_anti_stall = gate_params.get("pf_anti_stall", {})
    if rolling_pf < 1.0 and pf_anti_stall.get("prefer_size_over_threshold", True):
        exploration_mult = pf_anti_stall.get("exploration_size_mult", 0.3)
        size_mult *= exploration_mult
        pass_threshold = max(pass_threshold - 10, 45.0)

    passed = final_score >= pass_threshold

    return GateResult(
        passed=passed,
        reason=None if passed else "SCORE_BELOW_THRESHOLD",
        score=round(final_score, 2),
        pass_threshold=round(pass_threshold, 2),
        mdd_mode=mdd_mode,
        leverage_mult=leverage_mult,
        size_mult=round(size_mult, 4),
        rolling_pf=rolling_pf,
    )
