"""
Phase 2: READ — 시장 체제 분류 + 전환 핸들링.
6-DNA 계산 (Hurst, Entropy, Liquidation, Funding, OI Momentum, Liquidation Density),
z-score 정규화, 에이전트별 Hurst window, regime classification, hysteresis.
Returns RegimeResult.
~100ms target.
"""

from __future__ import annotations

import logging

import numpy as np

from src.pipeline.models import RegimeResult
from src.utils.indicators import (
    candles_to_arrays,
    hurst_exponent,
    price_direction,
    rolling_zscore,
    shannon_entropy,
)

logger = logging.getLogger(__name__)

# ═══ Raw-to-Score Fallback (no history) ═══

def _raw_to_score(component: str, raw_value: float) -> float:
    """
    Map raw DNA value directly to 0~100 when z-score history is unavailable.
    Each component has its own natural range.
    """
    if component == "hurst":
        # hurst * direction: range approx [-1.0, +1.0]
        # +1 = strong uptrend, -1 = strong downtrend, 0 = random/sideways
        return float(np.clip((raw_value + 1.0) / 2.0 * 100, 0, 100))
    elif component == "entropy":
        # range [0, 1]: high = uncertain. Invert so high entropy → low score
        return float(np.clip((1.0 - raw_value) * 100, 0, 100))
    elif component == "liquidation":
        # range [0, 1]: high = risky. Invert so high pressure → low score
        return float(np.clip((1.0 - raw_value) * 100, 0, 100))
    elif component == "funding":
        # range [-1, +1]: positive = short favorable
        return float(np.clip((raw_value + 1.0) / 2.0 * 100, 0, 100))
    elif component == "oi_momentum":
        # range [-1, +1]: positive = bullish
        return float(np.clip((raw_value + 1.0) / 2.0 * 100, 0, 100))
    elif component == "liq_density":
        # range [0, 1]: high = dangerous. Invert
        return float(np.clip((1.0 - raw_value) * 100, 0, 100))
    else:
        return 50.0


# ═══ Regime Definitions ═══

REGIMES = {
    "STRONG_UPTREND": 5,
    "WEAK_UPTREND": 4,
    "SIDEWAYS": 3,
    "WEAK_DOWNTREND": 2,
    "STRONG_DOWNTREND": 1,
    "VOLATILE": 0,
}

REGIME_DISTANCE = {
    ("STRONG_UPTREND", "WEAK_UPTREND"): 1,
    ("WEAK_UPTREND", "SIDEWAYS"): 1,
    ("SIDEWAYS", "WEAK_DOWNTREND"): 1,
    ("WEAK_DOWNTREND", "STRONG_DOWNTREND"): 1,
}

DEFAULT_TRANSITION_PARAMS = {
    "base_grace": 3,
    "min_grace": 1,
    "max_grace": 6,
    "high_conf_threshold": 0.85,
    "low_conf_threshold": 0.55,
    "strong_alignment_threshold": 0.70,
    "conflict_alignment_threshold": 0.30,
    "volatile_entry_grace": 1,
    "volatile_exit_grace": 4,
    "blend_candles_per_distance": 1,
    "min_blend": 2,
    "max_blend": 5,
}


# ═══ DNA Calculations ═══

def _calculate_funding_pressure(candles: list[dict]) -> float:
    """
    Funding pressure: cumulative funding direction.
    Positive (long-heavy) → short favorable → negative signal.
    Range: -1.0 to +1.0.
    """
    funding_rates = [c.get("funding_rate", 0) or 0 for c in candles[-20:]]
    if not funding_rates or all(f == 0 for f in funding_rates):
        return 0.0

    avg_funding = np.mean(funding_rates)
    max_abs = max(abs(f) for f in funding_rates) if funding_rates else 1.0
    if max_abs == 0:
        return 0.0
    return float(np.clip(-avg_funding / max_abs, -1.0, 1.0))


def _calculate_oi_momentum(candles: list[dict]) -> float:
    """
    OI Momentum: OI change direction weighted by price direction.
    OI↑ + Price↑ = bullish (+), OI↑ + Price↓ = bearish (-).
    Range: -1.0 to +1.0.
    """
    oi_values = [c.get("open_interest", 0) or 0 for c in candles[-20:]]
    close_values = [c["close"] for c in candles[-20:]]

    valid_oi = [v for v in oi_values if v > 0]
    if len(valid_oi) < 2:
        return 0.0

    oi_arr = np.array(oi_values, dtype=np.float64)
    close_arr = np.array(close_values, dtype=np.float64)

    # OI change rate
    oi_change = (oi_arr[-1] - oi_arr[0]) / oi_arr[0] if oi_arr[0] > 0 else 0.0
    # Price change
    price_change = (close_arr[-1] - close_arr[0]) / close_arr[0] if close_arr[0] > 0 else 0.0

    # Direction alignment
    if oi_change > 0 and price_change > 0:
        momentum = min(oi_change * 10, 1.0)  # Bullish
    elif oi_change > 0 and price_change < 0:
        momentum = -min(oi_change * 10, 1.0)  # Bearish
    elif oi_change < 0:
        momentum = 0.0  # OI decreasing = neutral
    else:
        momentum = 0.0

    return float(np.clip(momentum, -1.0, 1.0))


def _calculate_liquidation_pressure(candles: list[dict], window: int = 20) -> float:
    """
    Liquidation pressure composite.
    norm_vol×0.5 + sharp_moves×0.3 + volume_signal×0.2.
    Range: 0.0 to 1.0.
    """
    recent = candles[-window:]
    arr = candles_to_arrays(recent)
    closes = arr["close"]
    volumes = arr["volume"]
    highs = arr["high"]
    lows = arr["low"]

    if len(closes) < 5:
        return 0.0

    # Normalized volatility
    returns = np.diff(closes) / closes[:-1]
    vol = np.std(returns) if len(returns) > 1 else 0.0
    norm_vol = min(vol * 100, 1.0)  # Scale to 0~1

    # Sharp moves (count of > 2σ moves)
    if len(returns) > 1 and np.std(returns) > 0:
        z_scores = np.abs((returns - np.mean(returns)) / np.std(returns))
        sharp_ratio = np.sum(z_scores > 2.0) / len(z_scores)
    else:
        sharp_ratio = 0.0

    # Volume signal (recent vs average)
    vol_mean = np.mean(volumes) if len(volumes) > 0 else 1.0
    vol_signal = min(float(volumes[-1]) / vol_mean, 3.0) / 3.0 if vol_mean > 0 else 0.0

    pressure = norm_vol * 0.5 + sharp_ratio * 0.3 + vol_signal * 0.2
    return float(np.clip(pressure, 0.0, 1.0))


def _calculate_liquidation_density(candles: list[dict]) -> float:
    """
    Liquidation density: concentration of recent liquidations.
    Higher = more cascade risk. Range: 0.0 to 1.0.
    """
    liq_vols = [c.get("liquidation_vol", 0) or 0 for c in candles[-20:]]
    if not liq_vols or all(v == 0 for v in liq_vols):
        return 0.0

    total = sum(liq_vols)
    if total == 0:
        return 0.0

    # Concentration: what fraction of total liquidation is in the last 5 candles
    recent = sum(liq_vols[-5:])
    concentration = recent / total

    # Scale by absolute magnitude (compare to earlier period)
    earlier = sum(liq_vols[:10]) / 10 if len(liq_vols) >= 10 else 1.0
    magnitude = min(sum(liq_vols[-5:]) / 5 / max(earlier, 1e-10), 3.0) / 3.0

    density = concentration * 0.6 + magnitude * 0.4
    return float(np.clip(density, 0.0, 1.0))


def _calculate_dna(
    candles: list[dict],
    dna_params: dict,
    history: dict[str, list[float]] | None = None,
) -> dict:
    """
    Calculate 6-DNA components + weighted composite score.
    All components are z-score normalized to 0~100.

    Returns:
        {score, components, weights_used, confidence}
    """
    weights = dna_params.get("weights", {
        "hurst": 0.25, "entropy": 0.20, "liquidation": 0.15,
        "funding": 0.15, "oi_momentum": 0.15, "liq_density": 0.10,
    })
    hurst_window = dna_params.get("hurst_window", 100)

    arr = candles_to_arrays(candles)
    closes = arr["close"]

    # Raw DNA components
    raw = {}
    raw["hurst"] = hurst_exponent(closes, window=hurst_window) * price_direction(closes)
    raw["entropy"] = shannon_entropy(closes, window=20, bins=10)
    raw["liquidation"] = _calculate_liquidation_pressure(candles)
    raw["funding"] = _calculate_funding_pressure(candles)
    raw["oi_momentum"] = _calculate_oi_momentum(candles)
    raw["liq_density"] = _calculate_liquidation_density(candles)

    # Z-score normalize each component (or use raw fallback)
    history = history or {}
    components = {}
    has_history = False
    for key, val in raw.items():
        hist = np.array(history.get(key, []), dtype=np.float64)
        if len(hist) >= 10:
            components[key] = rolling_zscore(val, hist)
            has_history = True
        else:
            # Fallback: map raw value directly to 0~100 scale
            components[key] = _raw_to_score(key, val)

    # Re-normalize weights to sum = 1.0
    w_sum = sum(weights.get(k, 0) for k in components)
    if w_sum <= 0:
        w_sum = 1.0
    norm_weights = {k: weights.get(k, 0) / w_sum for k in components}

    # Weighted composite
    score = sum(components[k] * norm_weights[k] for k in components)

    return {
        "score": score,
        "components": components,
        "raw": raw,
        "weights_used": norm_weights,
    }


# ═══ Regime Classification ═══

def _classify_regime(dna: dict) -> tuple[str, float]:
    """
    Classify market regime from DNA composite score.
    Score is on 0~100 scale (z-score normalized).

    Confidence = how deeply the score sits within the detected regime's range.
    Center of range → 1.0, boundary → 0.0.

    Returns (regime_name, confidence).
    """
    score = dna["score"]
    entropy_norm = dna["components"].get("entropy", 50)
    liq_norm = dna["components"].get("liquidation", 50)

    # VOLATILE check: high entropy + high liquidation pressure
    if entropy_norm > 80 and liq_norm > 80:
        vol_intensity = ((entropy_norm - 80) / 20 + (liq_norm - 80) / 20) / 2
        return "VOLATILE", min(vol_intensity, 1.0)

    # Direction-based classification + regime-specific confidence
    if score >= 70:
        regime = "STRONG_UPTREND"
        confidence = min((score - 70) / 15, 1.0)      # 70→0.0, 85→1.0
    elif score >= 58:
        regime = "WEAK_UPTREND"
        confidence = 1.0 - abs(score - 64) / 6         # 64→1.0, 58/70→0.0
    elif score >= 42:
        regime = "SIDEWAYS"
        confidence = 1.0 - abs(score - 50) / 8          # 50→1.0, 42/58→0.0
    elif score >= 30:
        regime = "WEAK_DOWNTREND"
        confidence = 1.0 - abs(score - 36) / 6         # 36→1.0, 30/42→0.0
    else:
        regime = "STRONG_DOWNTREND"
        confidence = min((30 - score) / 15, 1.0)       # 15→1.0, 30→0.0

    return regime, max(0.0, min(confidence, 1.0))


# ═══ MTF Integration ═══

def _calculate_alignment(tf_results: dict[str, tuple[str, float]]) -> float:
    """
    Calculate MTF alignment: how much do timeframes agree?
    Returns 0.0 (complete conflict) to 1.0 (perfect alignment).
    """
    if len(tf_results) <= 1:
        return 1.0

    regimes = [REGIMES.get(r, 3) for r, _ in tf_results.values()]
    # Agreement = 1 - normalized range of regime values
    regime_range = max(regimes) - min(regimes)
    max_possible = 5  # STRONG_UPTREND(5) - VOLATILE(0)
    alignment = 1.0 - (regime_range / max_possible)
    return max(alignment, 0.0)


def _apply_mtf_priority(
    tf_results: dict[str, tuple[str, float]],
    alignment: float,
    mtf_weights: dict[str, float],
) -> tuple[str, float]:
    """
    Weighted combination of TF regimes.
    Returns (final_regime, final_confidence).
    """
    if not tf_results:
        return "SIDEWAYS", 0.0

    # Weighted regime score
    weighted_score = 0.0
    weighted_conf = 0.0
    w_sum = 0.0
    for tf, (regime, conf) in tf_results.items():
        w = mtf_weights.get(tf, 0.0)
        weighted_score += REGIMES.get(regime, 3) * w
        weighted_conf += conf * w
        w_sum += w

    if w_sum > 0:
        weighted_score /= w_sum
        weighted_conf /= w_sum

    # Map back to regime
    if weighted_score >= 4.5:
        regime = "STRONG_UPTREND"
    elif weighted_score >= 3.5:
        regime = "WEAK_UPTREND"
    elif weighted_score >= 2.5:
        regime = "SIDEWAYS"
    elif weighted_score >= 1.5:
        regime = "WEAK_DOWNTREND"
    elif weighted_score >= 0.5:
        regime = "STRONG_DOWNTREND"
    else:
        regime = "VOLATILE"

    # Confidence amplification by alignment
    conf_mult = 0.8 + alignment * 0.4  # 0.8~1.2
    final_conf = min(weighted_conf * conf_mult, 1.0)

    return regime, final_conf


# ═══ Transition Handling ═══

def _handle_transition(
    current_regime: str,
    proposed_regime: str,
    proposed_confidence: float,
    alignment: float,
    grace_counter: int,
    transition_params: dict | None = None,
) -> dict:
    """
    Hysteresis-based regime transition.
    Returns {regime, confidence, in_transition, blend_progress, new_grace_counter}.
    """
    params = transition_params or DEFAULT_TRANSITION_PARAMS

    # Same regime = no transition
    if current_regime == proposed_regime:
        return {
            "regime": current_regime,
            "confidence": proposed_confidence,
            "in_transition": False,
            "blend_progress": 0.0,
            "grace_counter": 0,
        }

    # Calculate required grace period
    grace = params["base_grace"]

    if proposed_confidence >= params["high_conf_threshold"]:
        grace -= 1
    elif proposed_confidence <= params["low_conf_threshold"]:
        grace += 2

    if alignment >= params["strong_alignment_threshold"]:
        grace -= 1
    elif alignment <= params["conflict_alignment_threshold"]:
        grace += 2

    # VOLATILE special handling
    if proposed_regime == "VOLATILE":
        grace = params["volatile_entry_grace"]
    elif current_regime == "VOLATILE":
        grace = params["volatile_exit_grace"]

    grace = max(params["min_grace"], min(grace, params["max_grace"]))

    # Check if grace period is met
    new_counter = grace_counter + 1
    if new_counter >= grace:
        # Transition complete
        # Calculate blend duration
        current_val = REGIMES.get(current_regime, 3)
        proposed_val = REGIMES.get(proposed_regime, 3)
        distance = abs(current_val - proposed_val)
        blend_total = min(
            max(distance * params["blend_candles_per_distance"], params["min_blend"]),
            params["max_blend"],
        )
        return {
            "regime": proposed_regime,
            "confidence": proposed_confidence,
            "in_transition": True,
            "blend_progress": 1.0 / blend_total,
            "grace_counter": 0,
        }
    else:
        # Still in grace period
        return {
            "regime": current_regime,
            "confidence": proposed_confidence * 0.9,  # Slight confidence reduction
            "in_transition": True,
            "blend_progress": new_counter / grace,
            "grace_counter": new_counter,
        }


# ═══ Main Phase 2 Entry ═══

def phase2_read(
    candles_by_tf: dict[str, list[dict]],
    safety_result,
    agent_config: dict,
    agent_state: dict,
    params: dict,
) -> RegimeResult:
    """
    Phase 2: READ — 시장 체제 분류.

    Args:
        candles_by_tf: {timeframe: [candle_dicts]}
        safety_result: SafetyResult from Phase 1
        agent_config: {timeframes, mtf_weights, agent_id}
        agent_state: {current_regime, grace_counter, dna_history, ...}
        params: Agent parameters from params.json

    Returns:
        RegimeResult
    """
    # STAGE_1: early return
    if safety_result.stage == "STAGE_1":
        return RegimeResult(
            regime="VOLATILE", confidence=0.0, alignment=0.0,
            tf_results={}, in_transition=False, blend_progress=0.0,
        )

    timeframes = agent_config.get("timeframes", ["5m"])
    mtf_weights = params.get("mtf_weights", {"5m": 1.0})
    dna_params = params.get("dna", {})
    dna_history = agent_state.get("dna_history", {})

    # ━━━━ 2B: TF별 DNA 계산 + 히스토리 누적 ━━━━
    DNA_HISTORY_MAXLEN = 200  # rolling window per component per TF
    tf_results = {}
    for tf in timeframes:
        candles = candles_by_tf.get(tf, [])
        if not candles or len(candles) < 20:
            tf_results[tf] = ("SIDEWAYS", 0.0)
            continue

        tf_hist = dna_history.get(tf, {})
        dna = _calculate_dna(candles, dna_params, tf_hist)
        regime, confidence = _classify_regime(dna)
        tf_results[tf] = (regime, confidence)

        # Accumulate raw DNA values into history for z-score normalization
        if tf not in dna_history:
            dna_history[tf] = {}
        for key, val in dna["raw"].items():
            if key not in dna_history[tf]:
                dna_history[tf][key] = []
            dna_history[tf][key].append(float(val))
            # Trim to rolling window
            if len(dna_history[tf][key]) > DNA_HISTORY_MAXLEN:
                dna_history[tf][key] = dna_history[tf][key][-DNA_HISTORY_MAXLEN:]

    # ━━━━ 2C: MTF 통합 ━━━━
    if len(timeframes) == 1:
        # S1: single TF
        regime, confidence = tf_results.get(timeframes[0], ("SIDEWAYS", 0.0))
        alignment = 1.0
        confidence *= 0.85  # 15% discount for single TF
    else:
        # S2~S4: MTF weighted
        alignment = _calculate_alignment(tf_results)
        regime, confidence = _apply_mtf_priority(tf_results, alignment, mtf_weights)

    # ━━━━ 2D: 전환 핸들링 ━━━━
    current_regime = agent_state.get("current_regime", "SIDEWAYS")
    grace_counter = agent_state.get("grace_counter", 0)
    transition_params = params.get("transition", DEFAULT_TRANSITION_PARAMS)

    transition = _handle_transition(
        current_regime, regime, confidence, alignment,
        grace_counter, transition_params,
    )

    # ━━━━ 2E: agent_state 업데이트 (regime + grace_counter) ━━━━
    agent_state["current_regime"] = transition["regime"]
    agent_state["grace_counter"] = transition["grace_counter"]

    # STAGE_3: confidence penalty
    final_confidence = transition["confidence"]
    if safety_result.stage == "STAGE_3":
        final_confidence *= 0.85

    return RegimeResult(
        regime=transition["regime"],
        confidence=final_confidence,
        alignment=alignment,
        tf_results={tf: {"regime": r, "confidence": c} for tf, (r, c) in tf_results.items()},
        in_transition=transition["in_transition"],
        blend_progress=transition["blend_progress"],
    )
