"""
Phase 1: SAFETY — 극단 감시 + MDD 게이트.
8가지 조건 + 동적 임계값 + Stage 시스템 + ATR 급변 감지.
Returns SafetyResult with action field.
~50ms target.
"""

from __future__ import annotations

import logging
import math
import time

import numpy as np

from src.pipeline.models import SafetyResult
from src.utils.config import get_mdd_mode
from src.utils.indicators import atr_single, candles_to_arrays
from src.utils.market_stats import MarketStats

logger = logging.getLogger(__name__)

# ═══ Severity Calculation ═══

def _condition_score(ratio: float) -> float:
    """
    Non-linear severity score per condition.
    score = 30 × (1 - e^(-0.8 × (ratio - 1)))
    Max 30 per condition. 0 if ratio <= 1.
    """
    if ratio <= 1.0:
        return 0.0
    return 30.0 * (1.0 - math.exp(-0.8 * (ratio - 1.0)))


def _calculate_severity(conditions: dict[str, dict]) -> float:
    """Sum severity scores across all conditions. Max 240 (8 × 30)."""
    total = 0.0
    for cond in conditions.values():
        total += cond.get("score", 0.0)
    return min(total, 240.0)


# ═══ 8 Safety Conditions ═══

def _detect_price_spike(
    closes: np.ndarray, stats: MarketStats, params: dict,
) -> dict:
    """Price spike: current candle change exceeds N-sigma of distribution."""
    if len(closes) < 2:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    pct_change = abs((closes[-1] - closes[-2]) / closes[-2] * 100)
    sigma = params.get("price_spike_sigma", 3.5)
    threshold = stats.price_change_std * sigma if stats.price_change_std > 0 else 5.0
    ratio = pct_change / threshold if threshold > 0 else 0.0

    return {
        "triggered": ratio > 1.0,
        "ratio": round(ratio, 3),
        "score": _condition_score(ratio),
        "value": round(pct_change, 4),
        "threshold": round(threshold, 4),
    }


def _detect_spread_blowout(
    candles: list[dict], stats: MarketStats, params: dict,
) -> dict:
    """Spread exceeds 97th percentile."""
    spread = candles[-1].get("bid_ask_spread") if candles else None
    if spread is None or spread <= 0:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    threshold = stats.spread_p97 if stats.spread_p97 > 0 else 50.0
    ratio = spread / threshold

    return {
        "triggered": ratio > 1.0,
        "ratio": round(ratio, 3),
        "score": _condition_score(ratio),
        "value": round(spread, 2),
        "threshold": round(threshold, 2),
    }


def _detect_volatility_surge(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    stats: MarketStats, params: dict,
) -> dict:
    """Current candle range/close ratio exceeds 95th percentile."""
    if len(closes) < 1:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    current_range = (highs[-1] - lows[-1]) / closes[-1] * 100 if closes[-1] > 0 else 0
    threshold = stats.volatility_p95 if stats.volatility_p95 > 0 else 2.0
    ratio = current_range / threshold if threshold > 0 else 0.0

    return {
        "triggered": ratio > 1.0,
        "ratio": round(ratio, 3),
        "score": _condition_score(ratio),
        "value": round(current_range, 4),
        "threshold": round(threshold, 4),
    }


def _detect_orderbook_stress(
    candles: list[dict], stats: MarketStats, params: dict,
) -> dict:
    """Orderbook imbalance exceeds 95th percentile."""
    imbalance = candles[-1].get("orderbook_imbalance") if candles else None
    if imbalance is None:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    abs_imb = abs(imbalance)
    threshold = stats.imbalance_p95 if stats.imbalance_p95 > 0 else 0.8
    ratio = abs_imb / threshold if threshold > 0 else 0.0

    return {
        "triggered": ratio > 1.0,
        "ratio": round(ratio, 3),
        "score": _condition_score(ratio),
        "value": round(abs_imb, 4),
        "threshold": round(threshold, 4),
    }


def _detect_volume_anomaly(
    volumes: np.ndarray, stats: MarketStats, params: dict,
) -> dict:
    """Volume surge (>95pct) or drought (<5pct)."""
    if len(volumes) < 1 or stats.volume_mean <= 0:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    current_vol = volumes[-1]
    # Surge check
    surge_threshold = stats.volume_p95 if stats.volume_p95 > 0 else stats.volume_mean * 3
    surge_ratio = current_vol / surge_threshold if surge_threshold > 0 else 0.0

    # Drought check
    drought_threshold = stats.volume_p5 if stats.volume_p5 > 0 else stats.volume_mean * 0.3
    drought_ratio = drought_threshold / current_vol if current_vol > 0 else 0.0

    ratio = max(surge_ratio, drought_ratio)
    return {
        "triggered": ratio > 1.0,
        "ratio": round(ratio, 3),
        "score": _condition_score(ratio),
        "value": round(float(current_vol), 2),
        "surge_threshold": round(surge_threshold, 2),
        "drought_threshold": round(drought_threshold, 2),
    }


def _detect_funding_extreme(
    candles: list[dict], stats: MarketStats, params: dict,
) -> dict:
    """Funding rate exceeds 97th or below 3rd percentile."""
    funding = candles[-1].get("funding_rate") if candles else None
    if funding is None:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    if funding > 0 and stats.funding_p97 > 0:
        ratio = funding / stats.funding_p97
    elif funding < 0 and stats.funding_p3 < 0:
        ratio = funding / stats.funding_p3
    else:
        ratio = 0.0

    return {
        "triggered": ratio > 1.0,
        "ratio": round(ratio, 3),
        "score": _condition_score(ratio),
        "value": round(funding, 6),
    }


def _detect_oi_shock(
    candles: list[dict], stats: MarketStats, params: dict,
) -> dict:
    """OI change rate exceeds 97th percentile."""
    if len(candles) < 2:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    oi_now = candles[-1].get("open_interest")
    oi_prev = candles[-2].get("open_interest")
    if not oi_now or not oi_prev or oi_prev <= 0:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    oi_change_pct = abs((oi_now - oi_prev) / oi_prev * 100)
    threshold = stats.oi_change_p97 if stats.oi_change_p97 > 0 else 5.0
    ratio = oi_change_pct / threshold if threshold > 0 else 0.0

    return {
        "triggered": ratio > 1.0,
        "ratio": round(ratio, 3),
        "score": _condition_score(ratio),
        "value": round(oi_change_pct, 4),
        "threshold": round(threshold, 4),
    }


def _detect_liquidation_cascade(
    candles: list[dict], stats: MarketStats, params: dict,
) -> dict:
    """Liquidation volume exceeds 95th percentile.
    NOTE: Currently inactive — liquidation_vol is always 0 (no trades WS subscription).
    Will activate when trades WS feed is added."""
    liq_vol = candles[-1].get("liquidation_vol") if candles else None
    if not liq_vol or liq_vol <= 0:
        return {"triggered": False, "ratio": 0.0, "score": 0.0}

    threshold = stats.liquidation_p95 if stats.liquidation_p95 > 0 else liq_vol * 2
    ratio = liq_vol / threshold if threshold > 0 else 0.0

    return {
        "triggered": ratio > 1.0,
        "ratio": round(ratio, 3),
        "score": _condition_score(ratio),
        "value": round(liq_vol, 2),
        "threshold": round(threshold, 2),
    }


# ═══ ATR Divergence Detection ═══

def _detect_atr_divergence(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    stats: MarketStats,
) -> tuple[float | None, bool]:
    """
    Detect ATR divergence: recent 3-candle range vs ATR(14).
    Returns (volatility_override, volatility_alert).
    """
    if len(closes) < 14:
        return None, False

    atr_val = atr_single(highs, lows, closes, period=14)
    if atr_val <= 0:
        return None, False

    # Max range of recent 3 candles
    recent_range = float(np.max(highs[-3:] - lows[-3:]))
    divergence = recent_range / atr_val

    threshold = stats.volatility_divergence_p95 if stats.volatility_divergence_p95 > 0 else 2.0
    if divergence >= threshold:
        return recent_range, True
    return None, False


# ═══ Stage System ═══

STAGE_THRESHOLDS = {
    "STAGE_1": 160,
    "STAGE_2": 120,
    "STAGE_3": 80,
}

STAGE_ORDER = ["NORMAL", "STAGE_3", "STAGE_2", "STAGE_1"]

# Min hold times (minutes) before downgrade
STAGE_HOLD_TIMES = {
    "STAGE_1": 5,
    "STAGE_2": 15,
    "STAGE_3": 30,
}

STAGE_HOLD_TIMES_CAUTION = {
    "STAGE_1": 10,
    "STAGE_2": 30,
    "STAGE_3": 60,
}


def _determine_stage(severity: float) -> str:
    """Determine stage from severity score."""
    if severity >= STAGE_THRESHOLDS["STAGE_1"]:
        return "STAGE_1"
    if severity >= STAGE_THRESHOLDS["STAGE_2"]:
        return "STAGE_2"
    if severity >= STAGE_THRESHOLDS["STAGE_3"]:
        return "STAGE_3"
    return "NORMAL"


def _can_recover_stage(
    current_stage: str,
    stage_entered_ts: int,
    mdd_mode: str,
) -> str:
    """
    Check if stage can be downgraded (recovered).
    Stages can only transition sequentially: STAGE_1 → 2 → 3 → NORMAL.
    """
    if current_stage == "NORMAL":
        return "NORMAL"

    hold_times = STAGE_HOLD_TIMES_CAUTION if mdd_mode in ("caution", "defensive") else STAGE_HOLD_TIMES
    min_hold_ms = hold_times.get(current_stage, 5) * 60 * 1000
    elapsed = int(time.time() * 1000) - stage_entered_ts

    if elapsed < min_hold_ms:
        return current_stage  # Hold current stage

    # Step down one level
    idx = STAGE_ORDER.index(current_stage)
    if idx > 0:
        return STAGE_ORDER[idx - 1]
    return "NORMAL"


# ═══ Main Phase 1 Entry ═══

def phase1_safety(
    candles: list[dict],
    stats: MarketStats,
    agent_state: dict,
    params: dict,
) -> SafetyResult:
    """
    Phase 1: SAFETY — 극단 감시 + MDD 게이트.

    Args:
        candles: Recent candles for the primary timeframe (5m)
        stats: Pre-computed market statistics
        agent_state: {current_mdd, current_stage, stage_entered_ts, consecutive_losses, ...}
        params: Agent safety parameters from params.json

    Returns:
        SafetyResult with all fields populated
    """
    if not candles or len(candles) < 2:
        return SafetyResult(
            blocked=True, stage="NORMAL", severity=0.0, mdd_mode="normal",
            action="BLOCK_NEW", reason="INSUFFICIENT_DATA",
        )

    arr = candles_to_arrays(candles)
    closes = arr["close"]
    highs = arr["high"]
    lows = arr["low"]
    volumes = arr["volume"]

    # ━━━━ 1A: MDD 선행 게이트 ━━━━
    current_mdd = agent_state.get("current_mdd", 0.0)
    mdd_mode = get_mdd_mode(current_mdd)

    # ━━━━ 1A-2: ATR 급변 감지 ━━━━
    vol_override, vol_alert = _detect_atr_divergence(highs, lows, closes, stats)

    # ━━━━ 1B: 8가지 극단 조건 ━━━━
    safety_params = params.get("safety", {})

    conditions = {
        "price_spike": _detect_price_spike(closes, stats, safety_params),
        "spread_blow": _detect_spread_blowout(candles, stats, safety_params),
        "vol_surge": _detect_volatility_surge(highs, lows, closes, stats, safety_params),
        "book_stress": _detect_orderbook_stress(candles, stats, safety_params),
        "volume_anomaly": _detect_volume_anomaly(volumes, stats, safety_params),
        "funding_extreme": _detect_funding_extreme(candles, stats, safety_params),
        "oi_shock": _detect_oi_shock(candles, stats, safety_params),
        "liquidation_cascade": _detect_liquidation_cascade(candles, stats, safety_params),
    }

    # ━━━━ 1C: 심각도 계산 ━━━━
    severity = _calculate_severity(conditions)

    # ━━━━ 1D: Stage 판정 ━━━━
    proposed_stage = _determine_stage(severity)
    current_stage = agent_state.get("current_stage", "NORMAL")
    stage_entered_ts = agent_state.get("stage_entered_ts", 0)

    # Stage can only escalate instantly, but recovery requires hold time
    current_idx = STAGE_ORDER.index(current_stage)
    proposed_idx = STAGE_ORDER.index(proposed_stage)

    if proposed_idx > current_idx:
        # Escalation: immediate
        stage = proposed_stage
    elif proposed_idx < current_idx:
        # Recovery: check hold time
        stage = _can_recover_stage(current_stage, stage_entered_ts, mdd_mode)
    else:
        stage = current_stage

    # ━━━━ Determine action ━━━━
    if stage in ("STAGE_1", "STAGE_2"):
        blocked = True
        action = "BLOCK_NEW"
        reason = stage
    elif stage == "STAGE_3":
        blocked = False
        action = "REDUCE_LEV"
        reason = None
    else:
        blocked = False
        action = "ALLOW"
        reason = None

    return SafetyResult(
        blocked=blocked,
        stage=stage,
        severity=severity,
        mdd_mode=mdd_mode,
        action=action,
        reason=reason,
        conditions=conditions,
        volatility_override=vol_override,
        volatility_alert=vol_alert,
    )
