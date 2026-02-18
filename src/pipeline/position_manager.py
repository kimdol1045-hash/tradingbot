"""
Position Manager — order execution, monitoring, and lifecycle management.
Submits orders via OrderExecutor, monitors fills, updates trailing stops,
handles timeouts and emergency SL tightening.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from src.pipeline.models import Signal
from src.utils.config import AGENT_PROFILES

logger = logging.getLogger(__name__)


@dataclass
class ManagedPosition:
    """Tracks a live position from signal to close."""
    signal: Signal
    status: str = "pending"  # pending, open, closing, closed
    exchange_order_id: str = ""
    fill_price: float = 0.0
    fill_qty: float = 0.0
    filled_ts: int = 0
    tp_order_ids: list[str] = field(default_factory=list)
    sl_order_id: str = ""
    trailing_active: bool = False
    trailing_sl: float = 0.0
    tp_hits: int = 0
    remaining_qty: float = 0.0
    candles_held: int = 0
    pnl: float = 0.0
    close_reason: str = ""


class PositionManager:
    """Manages position lifecycle: entry → monitor → exit."""

    def __init__(self, executor=None, dry_run: bool = True):
        """
        Args:
            executor: OrderExecutor instance (or None for legacy mode)
            dry_run: If True, simulate orders without sending to exchange
        """
        self.executor = executor
        self.dry_run = dry_run
        self.positions: dict[str, ManagedPosition] = {}  # signal_id → position
        self._running = False

    def submit_signal(self, signal: Signal):
        """Queue a new signal for execution."""
        if signal.signal_id in self.positions:
            logger.warning("Duplicate signal: %s", signal.signal_id)
            return

        pos = ManagedPosition(
            signal=signal,
            remaining_qty=signal.notional_usd / signal.entry_price if signal.entry_price > 0 else 0,
        )
        self.positions[signal.signal_id] = pos
        logger.info(
            "Signal queued: %s %s %s lev=%.1fx entry=%.2f",
            signal.agent_id, signal.direction, signal.symbol,
            signal.leverage, signal.entry_price,
        )

        if self.dry_run:
            self._simulate_fill(pos)

    def _simulate_fill(self, pos: ManagedPosition):
        """Simulate immediate fill for dry-run mode."""
        sig = pos.signal
        pos.status = "open"
        pos.fill_price = sig.entry_price
        pos.fill_qty = pos.remaining_qty
        pos.filled_ts = int(time.time() * 1000)
        pos.exchange_order_id = f"DRY_{sig.signal_id[:8]}"

        logger.info(
            "DRY FILL: %s %s @ %.2f qty=%.6f",
            sig.direction, sig.symbol, pos.fill_price, pos.fill_qty,
        )

    async def _execute_entry(self, pos: ManagedPosition):
        """Send entry order via executor."""
        sig = pos.signal

        if self.executor is None:
            # Legacy fallback: simulate fill
            self._simulate_fill(pos)
            return

        try:
            result = await self.executor.execute_signal(sig)

            if result.success:
                pos.status = "open"
                pos.exchange_order_id = result.order_id
                pos.fill_price = result.fill_price
                pos.fill_qty = result.fill_qty
                pos.filled_ts = int(time.time() * 1000)
                pos.sl_order_id = result.sl_order_id
                pos.tp_order_ids = result.tp_order_ids
                logger.info(
                    "FILLED: %s %s @ %.2f (dry=%s)",
                    sig.direction, sig.symbol, pos.fill_price, result.dry_run,
                )
            else:
                pos.status = "closed"
                pos.close_reason = f"ORDER_FAILED:{result.error}"
                logger.error("Order failed for %s: %s", sig.signal_id[:12], result.error)
        except Exception:
            logger.exception("Executor error for signal %s", sig.signal_id[:12])
            pos.status = "closed"
            pos.close_reason = "EXECUTOR_ERROR"

    def update_trailing_stops(self, current_prices: dict[str, float]):
        """Update trailing stops for open positions that have hit TP1."""
        for pos in self.positions.values():
            if pos.status != "open" or not pos.trailing_active:
                continue

            sig = pos.signal
            current_price = current_prices.get(sig.symbol, 0)
            if current_price <= 0:
                continue

            # Get trailing ATR distance from params
            atr = sig.phase_snapshot.get("atr", 0) if sig.phase_snapshot else 0
            trailing_mult = 1.0  # Default
            if atr > 0:
                trailing_distance = atr * trailing_mult
            else:
                continue

            # Calculate new trailing SL
            if sig.direction == "LONG":
                new_trailing = current_price - trailing_distance
                if new_trailing > pos.trailing_sl:
                    pos.trailing_sl = new_trailing
                    logger.debug(
                        "Trailing SL updated: %s %.2f → %.2f",
                        sig.signal_id[:8], pos.trailing_sl, new_trailing,
                    )
                # Check if trailing SL hit
                if current_price <= pos.trailing_sl:
                    self._close_position(pos, current_price, "TRAILING_SL")
            else:  # SHORT
                new_trailing = current_price + trailing_distance
                if pos.trailing_sl == 0 or new_trailing < pos.trailing_sl:
                    pos.trailing_sl = new_trailing
                if current_price >= pos.trailing_sl:
                    self._close_position(pos, current_price, "TRAILING_SL")

    def check_timeouts(self):
        """Check if any position has exceeded max hold time."""
        now_ms = int(time.time() * 1000)
        for pos in self.positions.values():
            if pos.status != "open":
                continue

            sig = pos.signal
            # Estimate max hold from agent profile
            profile = AGENT_PROFILES.get(sig.agent_id)
            if not profile:
                continue

            # 5m candles per max hold
            candle_minutes = 5
            max_hold_ms = pos.candles_held * candle_minutes * 60 * 1000
            elapsed = now_ms - pos.filled_ts

            # Simple timeout: use agent-specific limits
            max_hold_limits = {"s1": 3 * 60 * 60 * 1000, "s2": 8 * 60 * 60 * 1000,
                               "s3": 24 * 60 * 60 * 1000, "s4": 84 * 60 * 60 * 1000}
            limit = max_hold_limits.get(sig.agent_id, 24 * 60 * 60 * 1000)

            if elapsed > limit:
                logger.info("Timeout: %s held %dms > %dms", sig.signal_id[:8], elapsed, limit)
                self._close_position(pos, sig.entry_price, "TIMEOUT")

    def emergency_tighten(self, stage: str):
        """Tighten SL for all open positions when Safety stage escalates."""
        if stage not in ("STAGE_1", "STAGE_2"):
            return

        for pos in self.positions.values():
            if pos.status != "open":
                continue

            sig = pos.signal
            # Tighten SL by 30%
            sl_dist = abs(pos.fill_price - sig.stop_loss)
            tighten = sl_dist * 0.3

            if sig.direction == "LONG":
                new_sl = sig.stop_loss + tighten
                if new_sl > sig.stop_loss:
                    sig.stop_loss = new_sl
            else:
                new_sl = sig.stop_loss - tighten
                if new_sl < sig.stop_loss:
                    sig.stop_loss = new_sl

            logger.warning(
                "Emergency SL tighten %s: new SL=%.2f (stage=%s)",
                sig.signal_id[:8], sig.stop_loss, stage,
            )

    def _close_position(self, pos: ManagedPosition, close_price: float, reason: str):
        """Mark position as closed and calculate PnL."""
        sig = pos.signal
        if sig.direction == "LONG":
            pos.pnl = (close_price - pos.fill_price) * pos.fill_qty
        else:
            pos.pnl = (pos.fill_price - close_price) * pos.fill_qty

        pos.status = "closed"
        pos.close_reason = reason
        logger.info(
            "CLOSED %s %s: pnl=%.2f reason=%s",
            sig.symbol, sig.signal_id[:8], pos.pnl, reason,
        )

        # Record exit in DB via executor
        if self.executor:
            pnl_pct = (pos.pnl / (sig.notional_usd / sig.leverage) * 100) if sig.leverage > 0 else 0
            asyncio.ensure_future(
                self.executor.record_exit(
                    sig.signal_id, close_price, pos.pnl, pnl_pct, reason,
                )
            )

    def get_open_positions(self, agent_id: str | None = None) -> list[ManagedPosition]:
        """Get all open positions, optionally filtered by agent."""
        result = []
        for pos in self.positions.values():
            if pos.status == "open":
                if agent_id is None or pos.signal.agent_id == agent_id:
                    result.append(pos)
        return result

    def get_open_risk(self, agent_id: str) -> float:
        """Calculate total open risk for an agent."""
        total_risk = 0.0
        for pos in self.get_open_positions(agent_id):
            sig = pos.signal
            sl_pct = abs(pos.fill_price - sig.stop_loss) / pos.fill_price if pos.fill_price > 0 else 0.02
            total_risk += sig.notional_usd * sl_pct
        return total_risk

    def get_pnl_summary(self, agent_id: str | None = None) -> dict:
        """Get PnL summary for closed positions."""
        wins = losses = 0
        total_pnl = 0.0
        gross_profit = gross_loss = 0.0

        for pos in self.positions.values():
            if pos.status != "closed":
                continue
            if agent_id and pos.signal.agent_id != agent_id:
                continue
            total_pnl += pos.pnl
            if pos.pnl > 0:
                wins += 1
                gross_profit += pos.pnl
            elif pos.pnl < 0:
                losses += 1
                gross_loss += abs(pos.pnl)

        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "total_pnl": round(total_pnl, 2),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / (wins + losses) if (wins + losses) > 0 else 0,
            "profit_factor": round(pf, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
        }

    async def monitor_loop(self, interval: float = 5.0):
        """Main monitoring loop — runs every `interval` seconds."""
        self._running = True
        logger.info("Position manager started (dry_run=%s)", self.dry_run)

        while self._running:
            try:
                # Process pending signals
                for pos in list(self.positions.values()):
                    if pos.status == "pending":
                        await self._execute_entry(pos)

                # Check timeouts
                self.check_timeouts()

                # Clean up old closed positions (keep last 100)
                closed = [k for k, v in self.positions.items() if v.status == "closed"]
                if len(closed) > 100:
                    for k in closed[:len(closed) - 100]:
                        del self.positions[k]

            except Exception:
                logger.exception("Position manager error")

            await asyncio.sleep(interval)

    def stop(self):
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Position manager stopped")
