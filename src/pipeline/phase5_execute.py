"""
Phase 5: EXECUTE — 시그널 생성 + 레버리지 + 포지션 사이징.
Stop loss, take profit, trailing config, cost-adjusted entry.
Returns Signal.
~30ms target.
"""
from __future__ import annotations

import hashlib
import logging
import time

from src.pipeline.models import Signal

logger = logging.getLogger(__name__)

# ═══ Leverage Calculation ═══

CONSECUTIVE_LOSS_DECAY = [1.0, 0.85, 0.65, 0.45, 0.25]


def _confidence_tier(confidence: float, thresholds: dict) -> str:
    if confidence >= thresholds.get("high", 0.80):
        return "high"
    elif confidence >= thresholds.get("medium", 0.60):
        return "medium"
    return "low"


def calculate_leverage(
    regime_result,
    gate_result,
    safety_result,
    scan_result,
    agent_state: dict,
    params: dict,
) -> float:
    """
    6-step leverage calculation.

    1. Regime × confidence base
    2. Inflection score adjustment
    3. Stage constraint
    4. ATR volatility adjustment
    5. MDD multiplier
    6. Consecutive loss decay
    """
    lev_params = params.get("leverage", {})
    table = lev_params.get("table", {})
    conf_thresholds = lev_params.get("confidence_thresholds", {"high": 0.80, "medium": 0.60})
    max_leverage = lev_params.get("max_leverage", 5)

    # Step 1: Regime × confidence
    regime_row = table.get(regime_result.regime, table.get("SIDEWAYS", {"high": 2, "medium": 1, "low": 1}))
    tier = _confidence_tier(regime_result.confidence, conf_thresholds)
    lev = float(regime_row.get(tier, 1))

    # Step 2: Inflection score adjustment
    if scan_result.score >= 90:
        lev *= 1.25
    elif scan_result.score >= 80:
        lev *= 1.10
    elif scan_result.score < 70:
        lev *= 0.85

    # Step 3: Stage constraint
    if safety_result.stage == "STAGE_3":
        lev = min(lev, 3.0)

    # Step 4: ATR volatility adjustment
    avg_atr_7d = agent_state.get("avg_atr_7d", scan_result.atr)
    if avg_atr_7d > 0:
        atr_ratio = scan_result.atr / avg_atr_7d
        if atr_ratio >= 2.0:
            lev *= 0.5
        elif atr_ratio >= 1.5:
            lev *= 0.7

    # Step 5: MDD multiplier
    lev *= gate_result.leverage_mult

    # Step 6: Consecutive loss decay
    streak = agent_state.get("consecutive_losses", 0)
    decay = CONSECUTIVE_LOSS_DECAY[min(streak, len(CONSECUTIVE_LOSS_DECAY) - 1)]
    lev *= decay

    lev = max(1.0, min(lev, float(max_leverage)))
    return round(lev, 1)


# ═══ Stop Loss ═══

def calculate_stop_loss(
    direction: str,
    entry_price: float,
    atr: float,
    sr_levels: list[dict],
    safety_result,
    mdd_mode: str,
    params: dict,
) -> float:
    """
    Calculate stop loss price.
    Uses nearest S/R level or ATR-based default, with MDD tightening.
    """
    exit_params = params.get("exit", {})
    sl_atr_mult = exit_params.get("sl_atr_mult", 1.5)
    max_loss_pct = exit_params.get("max_loss_pct", 0.02)
    mdd_tighten = exit_params.get("mdd_tighten", {
        "normal": 1.0, "caution": 0.85, "defensive": 0.7, "survival": 0.5,
    })

    # ATR-based raw SL
    raw_distance = atr * sl_atr_mult

    # Check for nearby S/R levels
    for level in sr_levels:
        if direction == "LONG" and level["type"] == "support":
            sr_dist = entry_price - level["price"]
            if 0 < sr_dist < raw_distance * 2:
                raw_distance = min(raw_distance, sr_dist + atr * 0.1)
                break
        elif direction == "SHORT" and level["type"] == "resistance":
            sr_dist = level["price"] - entry_price
            if 0 < sr_dist < raw_distance * 2:
                raw_distance = min(raw_distance, sr_dist + atr * 0.1)
                break

    # ATR divergence override
    effective_atr = atr
    if safety_result.volatility_override:
        effective_atr = max(atr, safety_result.volatility_override * 0.7)
        raw_distance = max(raw_distance, effective_atr * sl_atr_mult)

    # MDD tightening
    tighten_mult = mdd_tighten.get(mdd_mode, 1.0)
    sl_distance = raw_distance * tighten_mult

    # Calculate SL price
    if direction == "LONG":
        sl = entry_price - sl_distance
    else:
        sl = entry_price + sl_distance

    # Enforce max loss
    max_dist = entry_price * max_loss_pct
    if direction == "LONG":
        sl = max(sl, entry_price - max_dist)
    else:
        sl = min(sl, entry_price + max_dist)

    return round(sl, 2)


# ═══ Take Profit ═══

def calculate_take_profits(
    direction: str,
    entry_price: float,
    sl_price: float,
    atr: float,
    pattern_target_atr: float | None,
    params: dict,
) -> list[dict]:
    """
    Calculate take profit levels based on RR ratios.
    """
    exit_params = params.get("exit", {})
    tp_levels = exit_params.get("tp_levels", 2)
    split_ratios = exit_params.get("split_ratios", [60, 40])

    sl_distance = abs(entry_price - sl_price)
    if sl_distance <= 0:
        return []

    # RR-based TP calculation
    tp_configs = []
    for i in range(1, tp_levels + 1):
        rr_key = f"tp{i}_rr"
        rr_range = exit_params.get(rr_key, [1.5 * i, 2.0 * i])
        rr = (rr_range[0] + rr_range[1]) / 2  # Use midpoint
        tp_configs.append({"rr": rr, "ratio": split_ratios[i - 1] if i - 1 < len(split_ratios) else 0})

    # Adjust TP1 if pattern target is closer
    if pattern_target_atr is not None and len(tp_configs) > 0:
        pattern_distance = pattern_target_atr * atr
        tp1_distance = tp_configs[0]["rr"] * sl_distance
        if pattern_distance < tp1_distance and pattern_distance > sl_distance:
            tp_configs[0]["rr"] = pattern_distance / sl_distance

    # Build TP list
    tps: list[dict] = []
    for tp in tp_configs:
        tp_distance = tp["rr"] * sl_distance
        if direction == "LONG":
            price = entry_price + tp_distance
        else:
            price = entry_price - tp_distance
        tps.append({
            "price": round(price, 2),
            "ratio": tp["ratio"],
            "rr": round(tp["rr"], 2),
        })

    return tps


# ═══ Position Sizing ═══

def calculate_position_size(
    entry_price: float,
    sl_price: float,
    leverage: float,
    capital: float,
    gate_result,
    params: dict,
) -> tuple[float, float, float]:
    """
    Fixed-loss position sizing.

    Returns (notional_usd, margin_usd, order_qty).
    """
    exit_params = params.get("exit", {})
    risk_per_trade = exit_params.get("risk_per_trade", 0.01)

    sl_pct = abs(entry_price - sl_price) / entry_price if entry_price > 0 else 0.01
    if sl_pct <= 0:
        sl_pct = 0.01

    # R = max loss in USD
    r = capital * risk_per_trade * gate_result.size_mult

    # Core formula: notional = R / sl_pct
    notional_usd = r / sl_pct
    margin_usd = notional_usd / leverage if leverage > 0 else notional_usd
    order_qty = notional_usd / entry_price if entry_price > 0 else 0

    return round(notional_usd, 2), round(margin_usd, 2), round(order_qty, 6)


# ═══ Signal ID ═══

def _generate_signal_id(agent_id: str, symbol: str, direction: str, ts: int) -> str:
    raw = f"{agent_id}_{symbol}_{direction}_{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═══ Main Phase 5 Entry ═══

def phase5_execute(
    scan_result,
    regime_result,
    safety_result,
    gate_result,
    agent_config: dict,
    agent_state: dict,
    params: dict,
) -> Signal | None:
    """
    Phase 5: EXECUTE — build complete trading signal.

    Args:
        scan_result: ScanResult from Phase 3
        regime_result: RegimeResult from Phase 2
        safety_result: SafetyResult from Phase 1
        gate_result: GateResult from Phase 4
        agent_config: {agent_id, symbol, timeframes, ...}
        agent_state: {capital, consecutive_losses, avg_atr_7d, ...}
        params: Agent parameters

    Returns:
        Signal or None if blocked
    """
    if not gate_result.passed:
        return None

    agent_id = agent_config.get("agent_id", "s1")
    symbol = agent_config.get("symbol", "BTC")
    direction = scan_result.direction
    entry_price = scan_result.entry_price

    if not direction or entry_price <= 0:
        return None

    # ━━━━ 5A: Leverage ━━━━
    leverage = calculate_leverage(
        regime_result, gate_result, safety_result, scan_result, agent_state, params,
    )

    # ━━━━ 5B: Stop Loss ━━━━
    sl = calculate_stop_loss(
        direction, entry_price, scan_result.atr,
        scan_result.sr_levels, safety_result,
        gate_result.mdd_mode, params,
    )

    # ━━━━ 5C: Take Profits ━━━━
    tps = calculate_take_profits(
        direction, entry_price, sl, scan_result.atr,
        scan_result.pattern_target_atr, params,
    )

    # ━━━━ 5D: Position Sizing ━━━━
    capital = agent_state.get("capital", 10000)
    notional_usd, margin_usd, order_qty = calculate_position_size(
        entry_price, sl, leverage, capital, gate_result, params,
    )

    # ━━━━ 5E: Open Risk Budget Check ━━━━
    exit_params = params.get("exit", {})
    max_open_risk_pct = exit_params.get("max_open_risk_pct", 0.05)
    sl_pct = abs(entry_price - sl) / entry_price if entry_price > 0 else 0.02
    new_risk = notional_usd * sl_pct
    current_risk = agent_state.get("open_risk", 0.0)
    max_risk = capital * max_open_risk_pct

    if current_risk + new_risk > max_risk:
        available = max(max_risk - current_risk, 0)
        if available < new_risk * 0.3:
            logger.info("Open risk budget exhausted: %.2f + %.2f > %.2f", current_risk, new_risk, max_risk)
            return None
        reduction = available / new_risk
        notional_usd = round(notional_usd * reduction, 2)
        margin_usd = round(notional_usd / leverage, 2) if leverage > 0 else notional_usd
        order_qty = round(notional_usd / entry_price, 6) if entry_price > 0 else 0

    ts = int(time.time() * 1000)
    signal_id = _generate_signal_id(agent_id, symbol, direction, ts)

    confirmation_names = []
    if scan_result.patterns and scan_result.patterns.confirmation_names:
        confirmation_names = scan_result.patterns.confirmation_names

    return Signal(
        signal_id=signal_id,
        agent_id=agent_id,
        symbol=symbol,
        direction=direction,
        entry_price=entry_price,
        stop_loss=sl,
        take_profits=tps,
        leverage=leverage,
        notional_usd=notional_usd,
        margin_usd=margin_usd,
        regime=regime_result.regime,
        confidence=regime_result.confidence,
        inflection_type=scan_result.primary_type or "",
        inflection_score=scan_result.score,
        validation_score=gate_result.score,
        mdd_mode=gate_result.mdd_mode,
        pattern_confirmations=confirmation_names,
        timestamp=ts,
    )
