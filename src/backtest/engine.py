"""
Backtest Engine — candle replay + position simulation.

Replays historical candles through the 5-Phase pipeline,
simulates order fills and SL/TP/trailing stop exits using actual OHLCV data.

Usage:
    from src.backtest.engine import BacktestEngine
    engine = BacktestEngine(candles_by_tf, symbol="BTC")
    results = engine.run(agent_ids=["s3"])
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from src.pipeline.models import Signal
from src.pipeline.phase1_safety import phase1_safety
from src.pipeline.phase2_read import phase2_read
from src.pipeline.phase3_scan import phase3_scan
from src.pipeline.phase4_gate import phase4_gate
from src.pipeline.phase5_execute import phase5_execute
from src.utils.config import AGENT_PROFILES, COST_MODEL, TIMEFRAME_MINUTES
from src.utils.market_stats import MarketStats, compute_stats
from src.utils.params import load_params

logger = logging.getLogger(__name__)


# ═══ Simulated Candle Cache ═══

class ReplayCache:
    """
    Candle cache that accumulates candles during replay.
    Mimics the real collector's cache interface: .get(symbol, tf) → list[dict]
    """

    def __init__(self, max_size: int = 300):
        self._data: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        self._max_size = max_size

    def push(self, symbol: str, tf: str, candle: dict):
        """Add a candle to the cache (chronological order)."""
        buf = self._data[symbol][tf]
        buf.append(candle)
        if len(buf) > self._max_size:
            self._data[symbol][tf] = buf[-self._max_size:]

    def get(self, symbol: str, tf: str) -> list[dict]:
        """Get cached candles (oldest → newest)."""
        return self._data[symbol][tf]

    def size(self, symbol: str, tf: str) -> int:
        return len(self._data[symbol][tf])


# ═══ Simulated Position ═══

@dataclass
class SimPosition:
    """A simulated position for backtesting."""
    signal: Signal
    fill_price: float = 0.0
    fill_ts: int = 0
    close_price: float = 0.0
    close_ts: int = 0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    status: str = "open"  # open, closed
    close_reason: str = ""
    trailing_active: bool = False
    trailing_sl: float = 0.0
    candles_held: int = 0
    tp_hit_index: int = 0  # which TP level was hit (0 = none)
    high_water: float = 0.0  # best price seen (for trailing)
    cost_usd: float = 0.0  # slippage + fees

    @property
    def net_pnl(self) -> float:
        return self.pnl_usd - self.cost_usd


# ═══ Cost Model ═══

def _calculate_costs(signal: Signal, fill_price: float, close_price: float, stage: str) -> float:
    """Calculate realistic trading costs (slippage + fees)."""
    symbol = signal.symbol
    notional = signal.notional_usd

    # Slippage (entry + exit)
    slip_bps = COST_MODEL["slippage_bps"].get(symbol, COST_MODEL["slippage_bps"]["DEFAULT"])
    slippage = notional * slip_bps / 10000 * 2  # both sides

    # Taker fees (entry + exit)
    fee_bps = COST_MODEL["fee_bps"]["taker"]
    fees = notional * fee_bps / 10000 * 2

    # Conservative multiplier for backtest
    mult = COST_MODEL.get("backtest_conservative_mult", 1.5)

    return (slippage + fees) * mult


# ═══ Position Simulator ═══

def _simulate_position(
    pos: SimPosition,
    future_candles: list[dict],
    max_hold_candles: int,
) -> SimPosition:
    """
    Simulate a position's lifecycle using future candle data.
    Checks SL/TP/trailing on each candle's OHLCV.
    """
    sig = pos.signal
    sl = sig.stop_loss
    # Sort TPs by distance from entry (nearest first)
    # LONG: ascending (lower TP = nearer), SHORT: descending (higher TP = nearer)
    tps = sorted(
        sig.take_profits,
        key=lambda t: t.get("price", 0),
        reverse=(sig.direction == "SHORT"),
    )
    remaining_ratio = 1.0
    realized_parts: list[float] = []

    for i, candle in enumerate(future_candles):
        if pos.status == "closed":
            break

        pos.candles_held = i + 1
        high = candle["high"]
        low = candle["low"]
        close = candle["close"]

        # Update high water mark
        if sig.direction == "LONG":
            pos.high_water = max(pos.high_water, high)
        else:
            pos.high_water = min(pos.high_water, low) if pos.high_water > 0 else low

        # ── Check SL hit ──
        sl_hit = False
        if sig.direction == "LONG" and low <= sl:
            sl_hit = True
            exit_price = sl  # assume filled at SL
        elif sig.direction == "SHORT" and high >= sl:
            sl_hit = True
            exit_price = sl

        if sl_hit:
            pos.close_price = exit_price
            pos.close_ts = candle["timestamp"]
            pos.status = "closed"
            pos.close_reason = "TRAILING_SL" if pos.trailing_active else "SL"
            break

        # ── Check TP levels ──
        for tp_idx, tp in enumerate(tps):
            if tp_idx < pos.tp_hit_index:
                continue
            tp_price = tp.get("price", 0)
            tp_ratio = tp.get("ratio", 0)

            tp_hit = False
            if sig.direction == "LONG" and high >= tp_price:
                tp_hit = True
            elif sig.direction == "SHORT" and low <= tp_price:
                tp_hit = True

            if tp_hit:
                # Partial close
                part_pnl = _pnl_for_direction(sig.direction, pos.fill_price, tp_price) * (
                    sig.notional_usd / pos.fill_price
                ) * tp_ratio
                realized_parts.append(part_pnl)
                remaining_ratio -= tp_ratio
                pos.tp_hit_index = tp_idx + 1

                # Activate trailing after first TP
                if not pos.trailing_active:
                    pos.trailing_active = True
                    # Move SL to breakeven + small buffer
                    if sig.direction == "LONG":
                        sl = pos.fill_price + (pos.fill_price * 0.001)
                        pos.trailing_sl = sl
                    else:
                        sl = pos.fill_price - (pos.fill_price * 0.001)
                        pos.trailing_sl = sl

                if remaining_ratio <= 0.02:
                    pos.close_price = tp_price
                    pos.close_ts = candle["timestamp"]
                    pos.status = "closed"
                    pos.close_reason = "TP_FULL"
                    break

        if pos.status == "closed":
            break

        # ── Update trailing stop ──
        if pos.trailing_active:
            atr = sig.phase_snapshot.get("atr", 0) if sig.phase_snapshot else 0
            trail_dist = atr * 1.5 if atr > 0 else abs(pos.fill_price - sig.stop_loss) * 0.5

            if sig.direction == "LONG":
                new_trail = pos.high_water - trail_dist
                if new_trail > sl:
                    sl = new_trail
                    pos.trailing_sl = sl
            else:
                new_trail = pos.high_water + trail_dist
                if new_trail < sl or sl == 0:
                    sl = new_trail
                    pos.trailing_sl = sl

        # ── Check max hold ──
        if pos.candles_held >= max_hold_candles:
            pos.close_price = close
            pos.close_ts = candle["timestamp"]
            pos.status = "closed"
            pos.close_reason = "TIMEOUT"
            break

    # If still open after all future candles, close at last close
    if pos.status == "open" and future_candles:
        last = future_candles[-1]
        pos.close_price = last["close"]
        pos.close_ts = last["timestamp"]
        pos.status = "closed"
        pos.close_reason = "END_OF_DATA"

    # Calculate final PnL
    if pos.close_price > 0 and remaining_ratio > 0.02:
        remaining_pnl = _pnl_for_direction(sig.direction, pos.fill_price, pos.close_price) * (
            sig.notional_usd / pos.fill_price
        ) * remaining_ratio
        realized_parts.append(remaining_pnl)

    pos.pnl_usd = sum(realized_parts)

    # Costs
    stage = sig.phase_snapshot.get("stage", "NORMAL") if sig.phase_snapshot else "NORMAL"
    pos.cost_usd = _calculate_costs(sig, pos.fill_price, pos.close_price, stage)

    # PnL percent
    if sig.notional_usd > 0:
        pos.pnl_pct = pos.net_pnl / (sig.notional_usd / sig.leverage) * 100

    return pos


def _pnl_for_direction(direction: str, entry: float, exit_price: float) -> float:
    """Raw price PnL per unit."""
    if direction == "LONG":
        return exit_price - entry
    else:
        return entry - exit_price


# ═══ Main Engine ═══

@dataclass
class BacktestResult:
    """Complete backtest result for one agent × one symbol."""
    agent_id: str
    symbol: str
    positions: list[SimPosition] = field(default_factory=list)
    signals_generated: int = 0
    candles_processed: int = 0
    start_ts: int = 0
    end_ts: int = 0
    elapsed_sec: float = 0.0


class BacktestEngine:
    """
    Replays historical candles through the 5-Phase pipeline.

    Args:
        candles_by_tf: Dict of {timeframe: [candle_dicts]} — must be sorted by timestamp asc.
        symbol: The symbol being backtested.
        warmup: Number of 5m candles to feed before generating signals (builds indicators).
    """

    def __init__(
        self,
        candles_by_tf: dict[str, list[dict]],
        symbol: str,
        warmup: int = 200,
        scan_every: int = 6,
    ):
        self.candles_by_tf = candles_by_tf
        self.symbol = symbol
        self.warmup = warmup
        self.scan_every = scan_every  # run Phase 2-5 every N candles (6 = every 30m)
        self._cache = ReplayCache(max_size=300)

    def run(
        self,
        agent_ids: list[str] | None = None,
        capital_per_agent: float = 10000.0,
    ) -> dict[str, BacktestResult]:
        """
        Run backtest for specified agents.

        Args:
            agent_ids: List of agent IDs to test (default: all 4)
            capital_per_agent: Starting capital per agent

        Returns:
            Dict of {agent_id: BacktestResult}
        """
        agent_ids = agent_ids or ["s1", "s2", "s3", "s4"]
        primary_candles = self.candles_by_tf.get("5m", [])

        if len(primary_candles) < self.warmup + 50:
            logger.error(
                "Not enough 5m candles: %d (need %d+ for warmup + backtest)",
                len(primary_candles), self.warmup + 50,
            )
            return {}

        results: dict[str, BacktestResult] = {}

        for agent_id in agent_ids:
            profile = AGENT_PROFILES.get(agent_id)
            if not profile:
                logger.warning("Unknown agent: %s", agent_id)
                continue

            t0 = time.perf_counter()
            result = self._run_agent(agent_id, capital_per_agent)
            result.elapsed_sec = time.perf_counter() - t0

            results[agent_id] = result
            logger.info(
                "[BT] %s/%s: %d signals, %d trades, %.1fs",
                agent_id, self.symbol,
                result.signals_generated, len(result.positions),
                result.elapsed_sec,
            )

        return results

    def _run_agent(self, agent_id: str, capital: float) -> BacktestResult:
        """Run backtest for a single agent."""
        t0 = time.perf_counter()
        profile = AGENT_PROFILES[agent_id]
        params = load_params(agent_id)
        result = BacktestResult(agent_id=agent_id, symbol=self.symbol)

        # Build fresh cache
        cache = ReplayCache(max_size=300)

        # Agent state (mirrors runner.py)
        # total_capital set high: backtest is single-symbol, exposure limit shouldn't block
        state = {
            "current_mdd": 0.0,
            "current_stage": "NORMAL",
            "stage_entered_ts": 0,
            "current_regime": "SIDEWAYS",
            "grace_counter": 0,
            "capital": capital,
            "total_capital": capital / profile.capital_pct,
            "initial_capital": capital,
            "open_risk": 0.0,
            "rolling_pf": 2.0,
            "consecutive_losses": 0,
            "open_positions_count": 0,
            "avg_atr_7d": 0.0,
            "dna_history": {},
        }

        # Equity tracking
        equity = capital
        peak_equity = capital

        # Build TF index maps (timestamp → index) for faster lookup
        tf_indices: dict[str, dict[int, int]] = {}
        for tf, candles in self.candles_by_tf.items():
            tf_indices[tf] = {c["timestamp"]: i for i, c in enumerate(candles)}

        # Max hold candles per agent
        max_hold_map = {"s1": 36, "s2": 96, "s3": 288, "s4": 1008}  # in 5m candles
        max_hold = max_hold_map.get(agent_id, 288)

        primary_candles = self.candles_by_tf["5m"]

        # Stats cache
        stats: MarketStats | None = None
        stats_ts = 0

        # Pre-fill higher TF caches during warmup
        # We need to feed ALL TF candles that have timestamps <= current 5m candle
        for tf in profile.timeframes:
            if tf == "5m":
                continue
            tf_candles = self.candles_by_tf.get(tf, [])
            if not tf_candles:
                continue
            warmup_end_ts = primary_candles[self.warmup - 1]["timestamp"] if self.warmup < len(primary_candles) else 0
            for c in tf_candles:
                if c["timestamp"] <= warmup_end_ts:
                    cache.push(self.symbol, tf, c)

        total_candles = len(primary_candles)
        log_interval = max(total_candles // 10, 10000)
        scan_every = self.scan_every
        debug_counts = {
            "p1_blocked": 0, "p1_passed": 0,
            "p3_no_pattern": 0, "p3_found": 0,
            "p4_blocked": 0, "p4_passed": 0,
            "p5_no_signal": 0, "p5_signal": 0,
        }

        # Main replay loop
        for i, candle in enumerate(primary_candles):
            cache.push(self.symbol, "5m", candle)

            # Also push any higher TF candles that close at this timestamp
            # Use pre-built tf_indices for O(1) lookup instead of linear scan
            for tf in profile.timeframes:
                if tf == "5m":
                    continue
                idx = tf_indices.get(tf, {}).get(candle["timestamp"])
                if idx is not None:
                    cache.push(self.symbol, tf, self.candles_by_tf[tf][idx])

            result.candles_processed += 1

            # Skip warmup period
            if i < self.warmup:
                continue

            # Progress logging
            if i % log_interval == 0:
                elapsed = time.perf_counter() - t0
                pct = i / total_candles * 100
                logger.info(
                    "[BT] %s/%s: %d/%d candles (%.0f%%), %d signals, %.0fs",
                    agent_id, self.symbol, i, total_candles, pct,
                    result.signals_generated, elapsed,
                )

            # Only run full pipeline every scan_every candles (Phase 1 safety is cheap)
            if (i - self.warmup) % scan_every != 0:
                continue

            # Refresh stats every 200 scan intervals
            if i - stats_ts > 200 * scan_every or stats is None:
                cache_5m = cache.get(self.symbol, "5m")
                if len(cache_5m) >= 100:
                    stats = compute_stats(cache_5m, self.symbol, "5m")
                    stats_ts = i
                else:
                    stats = MarketStats(symbol=self.symbol)

            # ━━━━ Run pipeline ━━━━
            candles_by_tf_current = {}
            for tf in profile.timeframes:
                candles_by_tf_current[tf] = cache.get(self.symbol, tf)

            primary_buf = candles_by_tf_current.get("5m", [])
            if len(primary_buf) < 30:
                continue

            agent_config = {
                "agent_id": agent_id,
                "symbol": self.symbol,
                "timeframes": list(profile.timeframes),
            }

            # Phase 1: SAFETY
            safety = phase1_safety(primary_buf, stats, state, params)
            if safety.stage != state["current_stage"]:
                state["current_stage"] = safety.stage
                state["stage_entered_ts"] = candle["timestamp"]

            if safety.blocked:
                debug_counts["p1_blocked"] += 1
                continue
            debug_counts["p1_passed"] += 1

            # Phase 2: READ
            regime = phase2_read(candles_by_tf_current, safety, agent_config, state, params)

            # Phase 3: SCAN
            stats_dict = {
                "funding_p95": stats.funding_p97 if stats else 0,
                "funding_p5": stats.funding_p3 if stats else 0,
                "oi_change_p90": stats.oi_change_p97 if stats else 0,
            }
            scan = phase3_scan(candles_by_tf_current, regime, safety, agent_config, params, stats_dict)

            if not scan.found:
                debug_counts["p3_no_pattern"] += 1
                continue
            debug_counts["p3_found"] += 1

            # Phase 4: GATE
            gate_stats = {
                "spread_p90": stats.spread_p95 if stats else 0,
                "imbalance_p90": stats.imbalance_p95 if stats else 0,
            }
            gate = phase4_gate(primary_buf, scan, regime, safety, state, params, gate_stats)

            if not gate.passed:
                debug_counts["p4_blocked"] += 1
                if debug_counts["p4_blocked"] <= 3:
                    logger.info(
                        "[BT] %s/%s P4 reject #%d: score=%.1f thresh=%.1f regime=%s reason=%s scan_score=%.1f",
                        agent_id, self.symbol, debug_counts["p4_blocked"],
                        gate.score, gate.pass_threshold, regime.regime,
                        gate.reason, scan.score,
                    )
                continue
            debug_counts["p4_passed"] += 1

            # Phase 5: EXECUTE
            signal = phase5_execute(scan, regime, safety, gate, agent_config, state, params)
            if not signal:
                debug_counts["p5_no_signal"] += 1
                continue
            debug_counts["p5_signal"] += 1

            result.signals_generated += 1

            # ━━━━ Simulate position ━━━━
            # Use remaining candles for simulation
            future_5m = primary_candles[i + 1:]
            if not future_5m:
                continue

            pos = SimPosition(
                signal=signal,
                fill_price=signal.entry_price,
                fill_ts=candle["timestamp"],
                high_water=signal.entry_price,
            )

            pos = _simulate_position(pos, future_5m, max_hold)
            result.positions.append(pos)

            # Update equity state
            net = pos.net_pnl
            equity += net
            if equity > peak_equity:
                peak_equity = equity

            mdd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
            state["current_mdd"] = mdd
            state["capital"] = equity

            if net > 0:
                state["consecutive_losses"] = 0
            elif net < 0:
                state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1

            # Update rolling PF
            closed_trades = result.positions
            if len(closed_trades) >= 2:
                recent = closed_trades[-20:]
                gp = sum(p.net_pnl for p in recent if p.net_pnl > 0)
                gl = sum(abs(p.net_pnl) for p in recent if p.net_pnl < 0)
                state["rolling_pf"] = gp / gl if gl > 0 else 10.0

        # Set time range
        if primary_candles:
            result.start_ts = primary_candles[self.warmup]["timestamp"] if self.warmup < len(primary_candles) else 0
            result.end_ts = primary_candles[-1]["timestamp"]

        # Debug: log pipeline funnel
        logger.info(
            "[BT] %s/%s pipeline funnel: P1(block=%d pass=%d) P3(miss=%d found=%d) "
            "P4(block=%d pass=%d) P5(miss=%d signal=%d)",
            agent_id, self.symbol,
            debug_counts["p1_blocked"], debug_counts["p1_passed"],
            debug_counts["p3_no_pattern"], debug_counts["p3_found"],
            debug_counts["p4_blocked"], debug_counts["p4_passed"],
            debug_counts["p5_no_signal"], debug_counts["p5_signal"],
        )

        return result
