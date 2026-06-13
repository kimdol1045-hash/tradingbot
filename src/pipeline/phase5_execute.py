"""
Phase 5: EXECUTE — 시그널 생성 + 레버리지 + 포지션 사이징.
Stop loss, take profit, trailing config, cost-adjusted entry.
Returns Signal.
~30ms target.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

from src.exchange.hyperliquid import round_price
from src.pipeline.models import Signal
from src.utils.config import EXPOSURE_LIMITS

logger = logging.getLogger(__name__)

# ═══ Leverage Calculation ═══

CONSECUTIVE_LOSS_DECAY = [1.0, 0.90, 0.80, 0.70, 0.60]


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

    # Step 2: Inflection score adjustment (no penalty for <70, gate already filters)
    if scan_result.score >= 90:
        lev *= 1.25
    elif scan_result.score >= 80:
        lev *= 1.10

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

    # Step 5+6: MDD and consecutive loss — apply strongest decay only (not both)
    mdd_decay = gate_result.leverage_mult
    streak = agent_state.get("consecutive_losses", 0)
    loss_decay = CONSECUTIVE_LOSS_DECAY[min(streak, len(CONSECUTIVE_LOSS_DECAY) - 1)]
    combined_decay = min(mdd_decay, loss_decay)
    lev *= combined_decay

    # Step 7: AI Market Advisor multiplier (skip in survival/emergency)
    if gate_result.mdd_mode not in ("survival", "emergency"):
        lev *= agent_state.get("ai_leverage_mult", 1.0)

    # Min 3x for futures (allows low-leverage coins like CC)
    min_lev = 3.0
    lev = max(min_lev, min(lev, float(max_leverage)))
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
    agent_state: dict | None = None,
    regime: str = "SIDEWAYS",
) -> float:
    """
    Calculate stop loss price.
    Uses nearest S/R level or ATR-based default, with MDD tightening.
    """
    exit_params = params.get("exit", {})
    sl_atr_mult = exit_params.get("sl_atr_mult", 1.5)

    # AI Market Advisor adjustment (skip in survival/emergency)
    if agent_state and mdd_mode not in ("survival", "emergency"):
        sl_atr_mult *= agent_state.get("ai_sl_mult", 1.0)
    max_loss_pct = exit_params.get("max_loss_pct", 0.02)
    # MDD SL adjustment: widen SL in caution/defensive (reduce leverage + wider SL = correct conservative approach)
    mdd_sl_adjust = exit_params.get("mdd_sl_adjust", {
        "normal": 1.0, "caution": 1.15, "defensive": 1.3, "survival": 1.0,
    })

    # Regime-aware minimum SL distance — SIDEWAYS/VOLATILE need wider floor
    _MIN_SL_BY_REGIME = {
        "STRONG_UPTREND": 0.012,    # 1.2% — 강한 트렌드는 타이트 OK
        "STRONG_DOWNTREND": 0.012,
        "WEAK_UPTREND": 0.015,      # 1.5% — 기존 기본값
        "WEAK_DOWNTREND": 0.015,
        "SIDEWAYS": 0.020,          # 2.0% — noise 대비 여유 확보
        "VOLATILE": 0.025,          # 2.5% — 높은 변동성
    }
    default_min = exit_params.get("min_sl_pct", 0.015)
    min_sl_pct = _MIN_SL_BY_REGIME.get(regime, default_min)
    min_distance = entry_price * min_sl_pct

    # ATR-based raw SL
    raw_distance = max(atr * sl_atr_mult, min_distance)

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

    # Re-enforce minimum after S/R adjustment
    raw_distance = max(raw_distance, min_distance)

    # ATR divergence override
    effective_atr = atr
    if safety_result.volatility_override:
        effective_atr = max(atr, safety_result.volatility_override * 0.7)
        raw_distance = max(raw_distance, effective_atr * sl_atr_mult)

    # MDD SL adjustment (widen in caution/defensive)
    sl_adjust_mult = mdd_sl_adjust.get(mdd_mode, 1.0)
    sl_distance = raw_distance * sl_adjust_mult

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

    return round_price(sl)


# ═══ Dynamic RR Factor ═══


def _compute_rr_factor(
    direction: str,
    regime: str,
    confidence: float,
    alignment: float,
    scan_score: float,
    mdd_mode: str,
    consecutive_losses: int,
) -> float:
    """
    Compute RR interpolation factor in [0.0, 1.0].

    0.0 = conservative (use rr_range min), 1.0 = aggressive (use rr_range max).
    Weighted combination of 6 sub-scores.
    """
    # 1. Regime-direction alignment (weight 0.30)
    _REGIME_DIR = {
        ("STRONG_UPTREND", "LONG"): 1.0,
        ("STRONG_DOWNTREND", "SHORT"): 1.0,
        ("WEAK_UPTREND", "LONG"): 0.6,
        ("WEAK_DOWNTREND", "SHORT"): 0.6,
        ("SIDEWAYS", "LONG"): 0.3,
        ("SIDEWAYS", "SHORT"): 0.3,
        ("VOLATILE", "LONG"): 0.3,
        ("VOLATILE", "SHORT"): 0.3,
    }
    regime_score = _REGIME_DIR.get((regime, direction), 0.0)

    # 2. Confidence (weight 0.15) — already 0.0–1.0
    conf_score = max(0.0, min(1.0, confidence))

    # 3. MTF alignment (weight 0.15) — already 0.0–1.0
    mtf_score = max(0.0, min(1.0, alignment))

    # 4. Scan score (weight 0.15) — map 60→0.0, 100→1.0
    scan_s = max(0.0, min(1.0, (scan_score - 60) / 40))

    # 5. MDD mode (weight 0.15)
    _MDD_SCORES = {"normal": 1.0, "caution": 0.6, "defensive": 0.3, "survival": 0.0}
    mdd_score = _MDD_SCORES.get(mdd_mode, 0.5)

    # 6. Consecutive losses (weight 0.10) — 0→1.0, 1→0.8, 2→0.6, 3→0.4, 4+→0.2
    _LOSS_SCORES = [1.0, 0.8, 0.6, 0.4, 0.2]
    loss_score = _LOSS_SCORES[min(consecutive_losses, len(_LOSS_SCORES) - 1)]

    rr_factor = (
        0.30 * regime_score
        + 0.15 * conf_score
        + 0.15 * mtf_score
        + 0.15 * scan_s
        + 0.15 * mdd_score
        + 0.10 * loss_score
    )
    return max(0.0, min(1.0, rr_factor))


# ═══ Take Profit ═══

def calculate_take_profits(
    direction: str,
    entry_price: float,
    sl_price: float,
    atr: float,
    pattern_target_atr: float | None,
    params: dict,
    rr_factor: float = 0.5,
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
        rr = rr_range[0] + rr_factor * (rr_range[1] - rr_range[0])
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
            "price": round_price(price),
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
    *,
    initial_capital: float = 0.0,
    confidence: float = 0.0,
    inflection_score: float = 0.0,
) -> tuple[float, float, float]:
    """
    Margin-based position sizing.

    margin = capital × margin_pct (고정 25%)
    notional = margin × leverage
    order_qty = notional / entry_price

    Returns (notional_usd, margin_usd, order_qty).
    """
    margin_pct = EXPOSURE_LIMITS.get("margin_pct_per_position", 0.25)

    # 고정 25% 마진 — MDD/gate 조정은 레버리지에서 처리
    margin_usd = capital * margin_pct
    notional_usd = margin_usd * leverage
    order_qty = notional_usd / entry_price if entry_price > 0 else 0

    return round(notional_usd, 2), round(margin_usd, 2), round(order_qty, 6)


# ═══ Signal ID ═══

def _generate_signal_id(agent_id: str, symbol: str, direction: str, ts: int) -> str:
    nonce = os.urandom(4).hex()
    raw = f"{agent_id}_{symbol}_{direction}_{ts}_{nonce}"
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

    # Clamp to exchange per-coin max leverage
    exchange_max_lev = agent_state.get("max_leverage_map", {}).get(symbol)
    if exchange_max_lev:
        if leverage > exchange_max_lev:
            logger.debug("Clamping leverage %s: %.1f → %d (exchange limit)", symbol, leverage, exchange_max_lev)
            leverage = float(exchange_max_lev)
        # Skip if exchange max leverage is below our minimum requirement
        min_lev = 5.0 if gate_result.mdd_mode != "survival" else 3.0
        if leverage < min_lev:
            logger.info(
                "[%s/%s] Exchange max leverage %dx < min %.0fx, skipping",
                agent_id, symbol, exchange_max_lev, min_lev,
            )
            return None

    # ━━━━ 5B: Stop Loss ━━━━
    # Use 24h ATR (from runner) for SL — wider, more stable than 14-period 5m ATR
    sl_atr_map = agent_state.get("sl_atr_map", {})
    atr = sl_atr_map.get(symbol, scan_result.atr)
    if atr <= 0:
        atr = scan_result.atr  # fallback to scan ATR
    if atr <= 0:
        logger.debug("[%s/%s] ATR is zero, cannot compute SL", agent_id, symbol)
        return None

    sl = calculate_stop_loss(
        direction, entry_price, atr,
        scan_result.sr_levels, safety_result,
        gate_result.mdd_mode, params,
        agent_state=agent_state,
        regime=regime_result.regime,
    )

    # Guard: SL must be different from entry
    sl_distance = abs(entry_price - sl)
    if sl_distance < entry_price * 0.0001:  # Less than 1 bps
        logger.debug("[%s/%s] SL too close to entry (%.2f vs %.2f)", agent_id, symbol, sl, entry_price)
        return None

    # ━━━━ 5C: Take Profits ━━━━
    rr_factor = _compute_rr_factor(
        direction=direction,
        regime=regime_result.regime,
        confidence=regime_result.confidence,
        alignment=regime_result.alignment,
        scan_score=scan_result.score,
        mdd_mode=gate_result.mdd_mode,
        consecutive_losses=agent_state.get("consecutive_losses", 0),
    )
    tps = calculate_take_profits(
        direction, entry_price, sl, atr,
        scan_result.pattern_target_atr, params,
        rr_factor=rr_factor,
    )

    if not tps:
        logger.debug("[%s/%s] No valid TP levels generated", agent_id, symbol)
        return None

    # ━━━━ 5D: Position Sizing ━━━━
    # margin = capital × 25% × size_mult, notional = margin × leverage
    capital = agent_state.get("capital", 10000)
    notional_usd, margin_usd, order_qty = calculate_position_size(
        entry_price, sl, leverage, capital, gate_result, params,
    )

    # ━━━━ 5E: Exposure & Risk Budget Checks ━━━━

    # 5E-1: Position count limits
    open_count = agent_state.get("open_positions_count", 0)
    max_per_agent = EXPOSURE_LIMITS["max_positions_per_agent"]
    if open_count >= max_per_agent:
        logger.info("Agent %s at max positions (%d/%d)", agent_id, open_count, max_per_agent)
        return None

    portfolio_count = agent_state.get("portfolio_positions_count", 0)
    max_portfolio = EXPOSURE_LIMITS["max_portfolio_positions"]
    if portfolio_count >= max_portfolio:
        logger.info("Portfolio at max positions (%d/%d)", portfolio_count, max_portfolio)
        return None

    # 5E-2: Per-symbol duplicate check
    max_per_symbol = EXPOSURE_LIMITS["max_positions_per_symbol"]
    symbol_count = agent_state.get("symbol_positions", {}).get(symbol, 0)
    if symbol_count >= max_per_symbol:
        logger.debug("Agent %s already has %d position(s) on %s", agent_id, symbol_count, symbol)
        return None

    # 5E-3: Single-coin exposure check
    max_coin_pct = EXPOSURE_LIMITS["max_single_coin_exposure_pct"]
    coin_exposure = agent_state.get("coin_exposure_usd", {}).get(symbol, 0.0)
    total_capital = agent_state.get("total_capital", capital)
    if total_capital > 0 and (coin_exposure + notional_usd) / total_capital > max_coin_pct:
        logger.info(
            "Single-coin exposure limit: %s would be %.1f%% of capital",
            symbol, (coin_exposure + notional_usd) / total_capital * 100,
        )
        return None

    # 5E-4: Total margin usage check (에이전트 마진 합산 100% 초과 방지)
    current_margin = agent_state.get("total_margin_used", 0.0)
    if capital > 0 and (current_margin + margin_usd) / capital > 0.95:
        logger.info("Agent %s margin near limit: $%.0f + $%.0f / $%.0f", agent_id, current_margin, margin_usd, capital)
        return None

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
        phase_snapshot={
            # Phase 1: Safety
            "stage": safety_result.stage,
            "mdd_mode": gate_result.mdd_mode,
            "severity": safety_result.severity,
            # Phase 2: Regime
            "regime": regime_result.regime,
            "regime_confidence": round(regime_result.confidence, 2),
            "mtf_alignment": round(regime_result.alignment, 2),
            # Phase 3: Scan
            "scan_score": scan_result.score,
            "primary_type": scan_result.primary_type,
            "mtf_grade": scan_result.mtf_grade,
            "atr": atr,
            "confirmations": confirmation_names,
            # Phase 4: Gate
            "gate_score": gate_result.score,
            "gate_threshold": gate_result.pass_threshold,
            "gate_size_mult": gate_result.size_mult,
            "rolling_pf": gate_result.rolling_pf,
            # Phase 5: Execute
            "rr_factor": round(rr_factor, 3),
            "sl_pct": round(abs(entry_price - sl) / entry_price * 100, 2) if entry_price > 0 else 0,
            "capital": capital,
        },
    )
