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

from src.notify.telegram import notify_exit, notify_fill, notify_tp_hit, notify_tp_timeout
from src.pipeline.models import Signal
from src.utils.async_helpers import safe_fire_and_forget
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
    last_tp_hit_ts: float = 0.0   # Unix timestamp of last TP hit (for timeout)
    remaining_qty: float = 0.0
    candles_held: int = 0
    pnl: float = 0.0
    close_reason: str = ""
    high_water: float = 0.0       # best price since entry (for trailing)
    _sl_dirty: bool = False       # needs exchange SL update


class PositionManager:
    """Manages position lifecycle: entry → monitor → exit."""

    def __init__(self, executor=None, dry_run: bool = True, equity_tracker=None, candle_cache=None):
        """
        Args:
            executor: OrderExecutor instance (or None for legacy mode)
            dry_run: If True, simulate orders without sending to exchange
            equity_tracker: EquityTracker instance for equity curve persistence
            candle_cache: CandleCache for latest price data
        """
        self.executor = executor
        self.dry_run = dry_run
        self.equity_tracker = equity_tracker
        self.candle_cache = candle_cache
        self.positions: dict[str, ManagedPosition] = {}  # signal_id → position
        self._running = False
        # Rejection cooldown: {(agent_id, symbol): expiry_ts}
        self._rejection_cooldown: dict[tuple[str, str], float] = {}
        self._rejection_cooldown_sec: float = 1800.0  # 30 min cooldown after margin rejection
        # SL cooldown: {(agent_id, symbol): expiry_ts} — prevent re-entry after SL hit
        self._sl_cooldown: dict[tuple[str, str], float] = {}
        self._sl_cooldown_sec: float = 7200.0  # 2 hours

    def is_rejected_cooldown(self, agent_id: str, symbol: str) -> bool:
        """Check if agent+symbol is in rejection cooldown (recent margin failure)."""
        key = (agent_id, symbol)
        expiry = self._rejection_cooldown.get(key, 0)
        if expiry and time.time() < expiry:
            return True
        # Clean up expired entry
        self._rejection_cooldown.pop(key, None)
        return False

    def is_sl_cooldown(self, agent_id: str, symbol: str) -> bool:
        """Check if agent+symbol is in SL cooldown (recent SL hit)."""
        key = (agent_id, symbol)
        expiry = self._sl_cooldown.get(key, 0)
        if expiry and time.time() < expiry:
            return True
        self._sl_cooldown.pop(key, None)
        return False

    def count_cross_agent_positions(self, symbol: str) -> int:
        """Count how many agents have an open position on this symbol."""
        agents = set()
        for pos in self.positions.values():
            if pos.status in ("pending", "open") and pos.signal.symbol == symbol:
                agents.add(pos.signal.agent_id)
        return len(agents)

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

        # Telegram fill notification
        safe_fire_and_forget(
            notify_fill(sig, pos.fill_price, pos.exchange_order_id),
            name="notify_fill",
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

                # Telegram fill notification
                safe_fire_and_forget(
                    notify_fill(sig, pos.fill_price, pos.exchange_order_id),
                    name="notify_fill",
                )
            else:
                pos.status = "closed"
                pos.close_reason = f"ORDER_FAILED:{result.error}"
                logger.error("Order failed for %s: %s", sig.signal_id[:12], result.error)
                # Set cooldown on rejection to prevent repeated signals
                err_lower = result.error.lower() if result.error else ""
                if "insufficient margin" in err_lower or "minimum value" in err_lower:
                    key = (sig.agent_id, sig.symbol)
                    self._rejection_cooldown[key] = time.time() + self._rejection_cooldown_sec
                    logger.info(
                        "Rejection cooldown set: %s %s (%.0fs)",
                        sig.agent_id, sig.symbol, self._rejection_cooldown_sec,
                    )
        except Exception:
            logger.exception("Executor error for signal %s", sig.signal_id[:12])
            pos.status = "closed"
            pos.close_reason = "EXECUTOR_ERROR"

    def _fetch_current_prices(self) -> dict[str, float]:
        """Get latest prices from candle cache for all open position symbols."""
        prices: dict[str, float] = {}
        if not self.candle_cache:
            return prices
        for pos in self.positions.values():
            if pos.status != "open":
                continue
            sym = pos.signal.symbol
            if sym not in prices:
                candle = self.candle_cache.latest(sym, "5m")
                if candle:
                    prices[sym] = candle["close"]
        return prices

    def _check_tp_hits(self, prices: dict[str, float]):
        """Check if any TP level has been hit → execute partial close via market order."""
        for pos in list(self.positions.values()):
            if pos.status != "open":
                continue
            sig = pos.signal
            price = prices.get(sig.symbol, 0)
            if price <= 0:
                continue

            # Update high water mark
            if sig.direction == "LONG":
                pos.high_water = max(pos.high_water, price)
            else:
                pos.high_water = min(pos.high_water, price) if pos.high_water > 0 else price

            # Check TP levels not yet hit
            tps = sig.take_profits
            tp_hits_before = pos.tp_hits
            while pos.tp_hits < len(tps):
                tp = tps[pos.tp_hits]
                tp_price = tp["price"]
                hit = (sig.direction == "LONG" and price >= tp_price) or \
                      (sig.direction == "SHORT" and price <= tp_price)
                if not hit:
                    break

                pos.tp_hits += 1
                pos.last_tp_hit_ts = time.time()
                ratio = tp.get("ratio", 0)
                close_qty = pos.fill_qty * ratio / 100.0 if ratio > 0 else 0

                logger.info(
                    "TP%d hit: %s %s @ %.4f (target=%.4f) closing %.1f%% (qty=%.6f)",
                    pos.tp_hits, sig.symbol, sig.signal_id[:8], price, tp_price,
                    ratio, close_qty,
                )

                # Execute partial close via market order
                if close_qty > 0 and self.executor and not self.dry_run:
                    safe_fire_and_forget(
                        self._execute_tp_close(pos, close_qty, price, pos.tp_hits, ratio),
                        name=f"tp{pos.tp_hits}_close",
                    )

                # Calculate PnL for this TP slice
                if sig.direction == "LONG":
                    tp_pnl = (price - pos.fill_price) * close_qty
                else:
                    tp_pnl = (pos.fill_price - price) * close_qty

                # Telegram TP hit notification → exits topic
                safe_fire_and_forget(
                    notify_tp_hit(sig, pos.tp_hits, tp_price, price, ratio, close_qty, tp_pnl),
                    name="notify_tp_hit",
                )

                # Update remaining qty
                pos.remaining_qty = max(pos.remaining_qty - close_qty, 0)

                # On first TP hit: activate trailing, move SL to breakeven + 0.1%
                if pos.tp_hits == 1:
                    pos.trailing_active = True
                    be_offset = pos.fill_price * 0.001
                    if sig.direction == "LONG":
                        pos.trailing_sl = pos.fill_price + be_offset
                    else:
                        pos.trailing_sl = pos.fill_price - be_offset
                    pos._sl_dirty = True
                    logger.info(
                        "Trailing activated: %s SL→%.2f (breakeven+0.1%%)",
                        sig.signal_id[:8], pos.trailing_sl,
                    )

                # All TPs hit → close remaining position
                if pos.tp_hits >= len(tps) and pos.remaining_qty > 0:
                    self._close_position(pos, price, "TP_ALL_HIT")

            # Persist TP state once after all hits processed (avoids race condition)
            if pos.tp_hits > tp_hits_before:
                safe_fire_and_forget(
                    self._update_tp_state(sig.signal_id, pos.tp_hits, pos.remaining_qty, pos.last_tp_hit_ts),
                    name="update_tp_state",
                )

    async def _execute_tp_close(self, pos: ManagedPosition, close_qty: float, price: float, tp_level: int, ratio: int):
        """Execute TP partial close via market order on exchange."""
        sig = pos.signal
        # Stagger TP orders to avoid duplicate nonce errors
        if tp_level > 1:
            await asyncio.sleep(0.5 * (tp_level - 1))
        try:
            result = await self.executor.close_position(
                sig.symbol, sig.direction, abs(close_qty),
                reason=f"TP{tp_level}", agent_id=sig.agent_id,
            )
            if result.success:
                logger.info(
                    "TP%d CLOSED: %s %s %.1f%% qty=%.6f",
                    tp_level, sig.symbol, sig.signal_id[:8], ratio, close_qty,
                )
            else:
                logger.warning(
                    "TP%d close failed: %s %s — %s",
                    tp_level, sig.symbol, sig.signal_id[:8], result.error,
                )
        except Exception:
            logger.exception("TP%d close error: %s %s", tp_level, sig.symbol, sig.signal_id[:8])

    async def _update_tp_state(self, signal_id: str, tp_hits: int, remaining_qty: float, last_tp_hit_ts: float = 0.0):
        """Persist TP state to DB so restarts don't re-fire TPs."""
        try:
            import aiosqlite
            from src.utils.config import DB_PATH
            async with aiosqlite.connect(str(DB_PATH)) as db:
                await db.execute(
                    "UPDATE positions SET tp_hits = ?, remaining_qty = ?, last_tp_hit_ts = ? WHERE signal_id = ?",
                    (tp_hits, remaining_qty, last_tp_hit_ts, signal_id),
                )
                await db.commit()
        except Exception:
            logger.warning("Failed to update TP state for %s", signal_id[:8], exc_info=True)

    def update_trailing_stops(self, current_prices: dict[str, float]):
        """Update trailing stops for open positions that have hit TP1."""
        from src.utils.params import load_params

        for pos in self.positions.values():
            if pos.status != "open" or not pos.trailing_active:
                continue

            sig = pos.signal
            current_price = current_prices.get(sig.symbol, 0)
            if current_price <= 0:
                continue

            # Get trailing ATR distance from params
            atr = sig.phase_snapshot.get("atr", 0) if sig.phase_snapshot else 0
            params = load_params(sig.agent_id)
            trailing_mult = params.get("trailing_stop", {}).get("trailing_atr_mult", 1.0)
            if atr > 0:
                trailing_distance = atr * trailing_mult
            else:
                continue

            # Calculate new trailing SL
            if sig.direction == "LONG":
                new_trailing = current_price - trailing_distance
                if new_trailing > pos.trailing_sl:
                    pos.trailing_sl = new_trailing
                    pos._sl_dirty = True
                    logger.debug(
                        "Trailing SL updated: %s → %.2f (price=%.2f)",
                        sig.signal_id[:8], new_trailing, current_price,
                    )
                # Check if trailing SL hit
                if current_price <= pos.trailing_sl:
                    self._close_position(pos, current_price, "TRAILING_SL")
            else:  # SHORT
                new_trailing = current_price + trailing_distance
                if pos.trailing_sl == 0 or new_trailing < pos.trailing_sl:
                    pos.trailing_sl = new_trailing
                    pos._sl_dirty = True
                    logger.debug(
                        "Trailing SL updated: %s → %.2f (price=%.2f)",
                        sig.signal_id[:8], new_trailing, current_price,
                    )
                if current_price >= pos.trailing_sl:
                    self._close_position(pos, current_price, "TRAILING_SL")

    async def _sync_sl_to_exchange(self):
        """Push updated SL orders to exchange for dirty positions."""
        if not self.executor:
            return
        for pos in list(self.positions.values()):
            if pos.status != "open" or not pos._sl_dirty:
                continue
            try:
                new_sl_id = await self.executor.update_sl_order(
                    pos.signal.symbol,
                    pos.signal.direction,
                    abs(pos.remaining_qty),
                    pos.sl_order_id,
                    pos.trailing_sl,
                    agent_id=pos.signal.agent_id,
                )
                if new_sl_id:
                    pos.sl_order_id = new_sl_id
                pos._sl_dirty = False
            except Exception:
                logger.warning("SL sync failed: %s", pos.signal.signal_id[:8], exc_info=True)

    def check_timeouts(self, prices: dict[str, float]):
        """Close positions that haven't hit the next TP within the agent-specific timeout."""
        from src.utils.params import load_params

        now = time.time()
        for pos in list(self.positions.values()):
            if pos.status != "open" or pos.tp_hits == 0 or pos.last_tp_hit_ts == 0:
                continue
            if pos.tp_hits >= len(pos.signal.take_profits):
                continue  # All TPs already hit

            params = load_params(pos.signal.agent_id)
            timeout = params.get("exit", {}).get("tp_timeout_sec", 10800)
            elapsed = now - pos.last_tp_hit_ts
            if elapsed < timeout:
                continue

            price = prices.get(pos.signal.symbol, 0)
            if price <= 0:
                continue

            logger.info(
                "TP_TIMEOUT: %s %s — TP%d hit %.0fs ago (timeout=%ds), closing remaining qty=%.6f",
                pos.signal.symbol, pos.signal.signal_id[:8],
                pos.tp_hits, elapsed, timeout, pos.remaining_qty,
            )

            # Execute market close on exchange
            if pos.remaining_qty > 0 and self.executor and not self.dry_run:
                safe_fire_and_forget(
                    self.executor.close_position(
                        pos.signal.symbol, pos.signal.direction, abs(pos.remaining_qty),
                        reason="TP_TIMEOUT", agent_id=pos.signal.agent_id,
                    ),
                    name="tp_timeout_close",
                )

            # Telegram notification
            safe_fire_and_forget(
                notify_tp_timeout(pos.signal, pos.tp_hits, elapsed, pos.remaining_qty, price),
                name="notify_tp_timeout",
            )

            self._close_position(pos, price, "TP_TIMEOUT")

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
                    pos.trailing_sl = new_sl
            else:
                new_sl = sig.stop_loss - tighten
                if new_sl < sig.stop_loss:
                    sig.stop_loss = new_sl
                    pos.trailing_sl = new_sl

            pos._sl_dirty = True
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

        # Clear rejection cooldown for this agent+symbol (margin freed up)
        self._rejection_cooldown.pop((sig.agent_id, sig.symbol), None)

        # Set SL cooldown to prevent rapid re-entry after stop loss
        if reason == "SL_HIT":
            key = (sig.agent_id, sig.symbol)
            self._sl_cooldown[key] = time.time() + self._sl_cooldown_sec
            logger.info(
                "SL cooldown set: %s %s (%.0fs)",
                sig.agent_id, sig.symbol, self._sl_cooldown_sec,
            )

        # Record exit in DB via executor
        if self.executor:
            margin = (sig.notional_usd / sig.leverage) if sig.leverage > 0 and sig.notional_usd > 0 else 0
            pnl_pct = (pos.pnl / margin * 100) if margin > 0 else 0
            safe_fire_and_forget(
                self.executor.record_exit(
                    sig.signal_id, close_price, pos.pnl, pnl_pct, reason,
                ),
                name="record_exit",
            )

        # Telegram exit notification
        safe_fire_and_forget(
            notify_exit(sig, close_price, pos.pnl, reason),
            name="notify_exit",
        )

        # Update equity tracker (persists to equity_curve table)
        if self.equity_tracker:
            safe_fire_and_forget(
                self.equity_tracker.record_trade(sig.agent_id, pos.pnl),
                name="record_trade",
            )

    def get_open_positions(self, agent_id: str | None = None) -> list[ManagedPosition]:
        """Get all open positions, optionally filtered by agent."""
        result = []
        for pos in self.positions.values():
            if pos.status == "open":
                if agent_id is None or pos.signal.agent_id == agent_id:
                    result.append(pos)
        return result

    def has_active_position(self, agent_id: str, symbol: str) -> bool:
        """Check if agent has a pending or open position on this symbol."""
        for pos in self.positions.values():
            if pos.status in ("pending", "open") and \
               pos.signal.agent_id == agent_id and pos.signal.symbol == symbol:
                return True
        return False

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

    async def _check_sl_hits(self):
        """Detect SL hits by comparing tracked positions against exchange.

        If a position exists in-memory but NOT on the exchange, it was
        closed by the exchange-side SL trigger order → mark as SL_HIT.
        Runs every 6th monitor cycle (~30s at 5s interval) to limit API calls.
        """
        if not self.executor or self.dry_run:
            return

        open_positions = self.get_open_positions()
        if not open_positions:
            return

        # Group by agent_id
        agent_positions: dict[str, list[ManagedPosition]] = {}
        for pos in open_positions:
            agent_positions.setdefault(pos.signal.agent_id, []).append(pos)

        for agent_id, positions in agent_positions.items():
            try:
                exchange_pos = await self.executor.get_exchange_positions(agent_id)

                # API failure → None: skip SL check to avoid false SL_HIT
                if exchange_pos is None:
                    logger.warning(
                        "SL check skipped for %s: API failure (positions preserved)",
                        agent_id,
                    )
                    continue

                exchange_keys = {
                    f"{p['symbol']}_{p['direction']}" for p in exchange_pos
                }

                for pos in positions:
                    sig = pos.signal
                    key = f"{sig.symbol}_{sig.direction}"
                    if key not in exchange_keys:
                        # Position gone from exchange → SL triggered
                        close_price = sig.stop_loss  # best estimate
                        logger.info(
                            "SL_HIT detected: %s %s %s (not on exchange)",
                            agent_id, sig.direction, sig.symbol,
                        )
                        self._close_position(pos, close_price, "SL_HIT")
            except Exception:
                logger.debug("SL check failed for %s", agent_id, exc_info=True)

    async def monitor_loop(self, interval: float = 5.0):
        """Main monitoring loop — runs every `interval` seconds."""
        self._running = True
        self._cycle_count = 0
        logger.info("Position manager started (dry_run=%s, cache=%s)", self.dry_run, self.candle_cache is not None)

        while self._running:
            try:
                # Process pending signals
                for pos in list(self.positions.values()):
                    if pos.status == "pending":
                        await self._execute_entry(pos)

                # Dynamic SL/TP management
                prices = self._fetch_current_prices()
                if prices:
                    self._check_tp_hits(prices)
                    self.update_trailing_stops(prices)
                    self.check_timeouts(prices)
                    await self._sync_sl_to_exchange()

                # Check SL hits via exchange (every 6th cycle ≈ 30s)
                self._cycle_count += 1
                if self._cycle_count % 6 == 0:
                    await self._check_sl_hits()

                # Clean up old closed positions (keep last 100)
                closed = [k for k, v in self.positions.items() if v.status == "closed"]
                if len(closed) > 100:
                    for k in closed[:len(closed) - 100]:
                        del self.positions[k]

            except Exception:
                logger.exception("Position manager error")

            await asyncio.sleep(interval)

    async def close_all_positions(self, agent_id: str, reason: str = "EMERGENCY"):
        """Close all open positions for an agent. Used by CLOSE_ALL_AND_HALT."""
        positions = self.get_open_positions(agent_id)
        if not positions:
            logger.info("No open positions to close for %s", agent_id)
            return 0

        closed = 0
        for pos in positions:
            try:
                sig = pos.signal
                # Use current entry price as close price (best estimate before exchange confirms)
                close_price = pos.fill_price if pos.fill_price > 0 else sig.entry_price

                if self.executor and not self.dry_run:
                    result = await self.executor.close_position(
                        sig.symbol, sig.direction, abs(pos.fill_qty),
                        reason=reason, agent_id=agent_id,
                    )
                    if not result.success:
                        logger.error(
                            "Failed to close %s %s for %s: %s",
                            sig.symbol, sig.signal_id[:8], agent_id, result.error,
                        )
                        continue

                self._close_position(pos, close_price, reason)
                closed += 1
            except Exception:
                logger.exception("Error closing position %s for %s", pos.signal.signal_id[:8], agent_id)

        logger.warning(
            "EMERGENCY CLOSE: %s — closed %d/%d positions",
            agent_id, closed, len(positions),
        )
        return closed

    async def restore_from_db(self, db):
        """Restore open positions from DB into in-memory tracking.

        Called after reconciliation on startup so that get_open_positions(),
        has_active_position(), and Telegram queries work correctly.
        Also recalculates take_profit levels for each position.
        """
        from src.pipeline.phase5_execute import calculate_take_profits
        from src.utils.params import load_params

        try:
            cursor = await db.execute(
                "SELECT p.signal_id, p.agent_id, p.symbol, p.direction, "
                "p.entry_price, p.size_usd, p.sl_pct, p.entry_time, p.exchange_order_id, "
                "t.leverage, t.notional_usd, t.margin_usd, "
                "p.tp_hits, p.remaining_qty, p.last_tp_hit_ts "
                "FROM positions p "
                "LEFT JOIN trades t ON p.signal_id = t.signal_id "
                "WHERE p.status = 'OPEN'"
            )
            rows = await cursor.fetchall()

            restored = 0
            seen_keys: set[tuple] = set()  # (agent_id, symbol, direction) dedup
            for row in rows:
                (signal_id, agent_id, symbol, direction, entry_price,
                 size_usd, sl_pct, entry_time, exchange_order_id,
                 leverage, notional_usd, margin_usd,
                 db_tp_hits, db_remaining_qty, db_last_tp_hit_ts) = row

                if signal_id in self.positions:
                    continue  # Already tracked

                # Deduplicate: only one position per agent+symbol+direction
                dedup_key = (agent_id, symbol, direction)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                # Reconstruct minimal Signal
                sl_distance = (entry_price or 0) * (sl_pct or 0.02)
                if direction == "LONG":
                    stop_loss = (entry_price or 0) - sl_distance
                else:
                    stop_loss = (entry_price or 0) + sl_distance

                # Recalculate take_profits from agent params
                params = load_params(agent_id or "s1")
                tps = calculate_take_profits(
                    direction or "LONG",
                    entry_price or 0,
                    stop_loss,
                    0,     # atr not needed when pattern_target_atr is None
                    None,  # pattern_target_atr
                    params,
                )

                signal = Signal(
                    signal_id=signal_id,
                    agent_id=agent_id or "",
                    symbol=symbol or "",
                    direction=direction or "LONG",
                    entry_price=entry_price or 0,
                    stop_loss=stop_loss,
                    take_profits=tps,
                    leverage=leverage or 3.0,
                    notional_usd=notional_usd or size_usd or 0,
                    margin_usd=margin_usd or 0,
                    timestamp=entry_time or 0,
                )

                fill_qty = (notional_usd or size_usd or 0) / entry_price if entry_price else 0

                pos = ManagedPosition(
                    signal=signal,
                    status="open",
                    exchange_order_id=exchange_order_id or "",
                    fill_price=entry_price or 0,
                    fill_qty=fill_qty,
                    filled_ts=entry_time or 0,
                    remaining_qty=db_remaining_qty if db_remaining_qty and db_remaining_qty > 0 else fill_qty,
                    tp_hits=db_tp_hits or 0,
                    last_tp_hit_ts=db_last_tp_hit_ts or 0.0,
                )

                # Restore trailing stop state if TP1+ was already hit
                if pos.tp_hits >= 1 and entry_price:
                    pos.trailing_active = True
                    be_offset = entry_price * 0.001
                    if direction == "LONG":
                        pos.trailing_sl = entry_price - be_offset  # below entry (breakeven lock)
                    else:
                        pos.trailing_sl = entry_price + be_offset  # above entry (breakeven lock)

                self.positions[signal_id] = pos
                restored += 1

            if restored:
                logger.info("Restored %d open positions from DB (with TP levels)", restored)
        except Exception:
            logger.exception("Failed to restore positions from DB")

    async def ensure_exchange_orders(self):
        """Check and re-place missing SL trigger orders for all open positions.

        Also cleans up duplicate SL orders per symbol (keeps only the latest).
        TP is handled by position_manager via market orders (not exchange triggers).
        """
        if not self.executor or self.dry_run:
            return

        # Group positions by agent_id
        agent_positions: dict[str, list[ManagedPosition]] = {}
        for pos in self.positions.values():
            if pos.status != "open":
                continue
            agent_positions.setdefault(pos.signal.agent_id, []).append(pos)

        if not agent_positions:
            return

        total_placed = 0
        total_cancelled = 0
        for agent_id, positions in agent_positions.items():
            try:
                trigger_orders = await self.executor.get_trigger_orders(agent_id)
            except Exception:
                logger.exception("Failed to fetch trigger orders for %s", agent_id)
                continue

            # Group SL orders by symbol — detect duplicates
            sl_orders_by_symbol: dict[str, list[dict]] = {}
            for order in trigger_orders:
                coin = order.get("coin", "")
                order_type = order.get("orderType", "")
                if "Stop" in order_type or order_type == "Stop Market":
                    sl_orders_by_symbol.setdefault(coin, []).append(order)

            # Cancel duplicate SL orders (keep the last one per symbol)
            for coin, orders in sl_orders_by_symbol.items():
                if len(orders) <= 1:
                    continue
                # Keep the latest, cancel the rest
                sorted_orders = sorted(orders, key=lambda o: o.get("oid", 0))
                to_cancel = sorted_orders[:-1]
                for order in to_cancel:
                    try:
                        from src.exchange.hyperliquid import cancel_order
                        exchange = self.executor._get_exchange(agent_id)
                        if exchange:
                            import asyncio
                            await asyncio.to_thread(cancel_order, exchange, coin, order["oid"])
                            total_cancelled += 1
                    except Exception:
                        logger.debug("Failed to cancel duplicate SL %s", order.get("oid"))

            # Check which symbols now have SL orders
            symbols_with_sl = set(sl_orders_by_symbol.keys())

            for pos in positions:
                sig = pos.signal
                if sig.symbol in symbols_with_sl:
                    continue  # SL already exists

                sl_price = pos.trailing_sl if pos.trailing_active and pos.trailing_sl > 0 else sig.stop_loss
                logger.warning(
                    "Missing SL: %s %s %s — placing SL @ %.4f",
                    agent_id, sig.direction, sig.symbol, sl_price,
                )
                sl_id, _ = await self.executor.place_sl_tp_for_position(
                    sig.symbol, sig.direction, pos.remaining_qty or pos.fill_qty,
                    sl_price, [],  # TP handled by bot, not exchange
                    agent_id=agent_id,
                )
                pos.sl_order_id = sl_id
                total_placed += 1

        if total_cancelled:
            logger.info("Cleaned up %d duplicate SL orders", total_cancelled)
        if total_placed:
            logger.info("Re-placed SL orders for %d positions", total_placed)
        else:
            logger.info("All positions have SL orders on exchange")

    def stop(self):
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Position manager stopped")
