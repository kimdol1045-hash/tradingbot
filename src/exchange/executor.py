"""
Order Executor — bridges pipeline signals to Hyperliquid exchange.

Handles:
  - Signal → market order + SL/TP trigger orders
  - DRY_RUN mode (log only)
  - DB persistence with signal_id idempotency
  - Retry logic for transient exchange errors
  - Async wrapper around sync SDK
  - Multi-wallet: per-agent Exchange clients
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from src.exchange.hyperliquid import (
    cancel_order,
    get_exchange_client,
    get_frontend_open_orders,
    get_info_client,
    get_max_leverage,
    get_user_state,
    place_entry_with_sl_tp,
    place_market_order,
    place_trigger_order,
    round_price,
    round_size,
    update_leverage,
)
from src.pipeline.models import Signal
from src.utils.config import HYPERLIQUID_KEY, IS_TESTNET, WalletConfig

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_S = 1.0


@dataclass
class OrderResult:
    """Result of an order execution attempt."""
    success: bool = False
    order_id: str = ""
    fill_price: float = 0.0
    fill_qty: float = 0.0
    sl_order_id: str = ""
    tp_order_ids: list[str] = field(default_factory=list)
    error: str = ""
    dry_run: bool = False


class OrderExecutor:
    """
    Executes trading signals on Hyperliquid.

    Supports multi-wallet mode (per-agent wallets) and legacy single-wallet mode.

    Args:
        db: aiosqlite connection for trade persistence
        dry_run: If True, simulate orders without sending to exchange
        wallet_address: Legacy single wallet address (backwards compat)
        wallet_configs: Per-agent wallet configs {agent_id: WalletConfig}
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        dry_run: bool = True,
        wallet_address: str = "",
        wallet_configs: dict[str, WalletConfig] | None = None,
    ):
        self.db = db
        self.dry_run = dry_run
        self.wallet_configs = wallet_configs or {}

        # Per-agent exchange clients (lazy init)
        self._exchanges: dict[str, Any] = {}
        self._addresses: dict[str, str] = {}

        # Legacy single-wallet fallback
        self._legacy_address = wallet_address
        self._legacy_exchange = None

        # Shared read-only Info client
        self._info = None

        # Populate addresses from wallet_configs
        for agent_id, wc in self.wallet_configs.items():
            self._addresses[agent_id] = wc.wallet_address

        # If no wallet_configs, use legacy address
        if not self._addresses and wallet_address:
            self._addresses[""] = wallet_address

    @property
    def wallet_address(self) -> str:
        """Legacy compat: return first available wallet address."""
        if self._legacy_address:
            return self._legacy_address
        if self._addresses:
            return next(iter(self._addresses.values()))
        return ""

    def _ensure_info(self):
        """Lazily initialize shared Info client."""
        if self._info is None:
            self._info = get_info_client(skip_ws=True)

    def _ensure_clients(self, agent_id: str = ""):
        """Lazily initialize exchange client for a specific agent."""
        self._ensure_info()

        if self.dry_run:
            return

        if agent_id and agent_id in self.wallet_configs:
            # Per-agent API key mode (each agent has its own Hyperliquid account)
            if agent_id not in self._exchanges:
                wc = self.wallet_configs[agent_id]
                client = get_exchange_client(private_key=wc.private_key)
                if client is None:
                    logger.error("Exchange client unavailable for agent %s", agent_id)
                else:
                    self._exchanges[agent_id] = client
                    logger.info("Agent %s: exchange client ready (%s...)", agent_id, wc.wallet_address[:12])
        else:
            # Legacy single-wallet mode
            if self._legacy_exchange is None:
                self._legacy_exchange = get_exchange_client()
                if self._legacy_exchange is None:
                    logger.error("Exchange client unavailable (no API key?)")

    def _get_exchange(self, agent_id: str = ""):
        """Get Exchange client for agent. Falls back to legacy client."""
        if agent_id and agent_id in self._exchanges:
            return self._exchanges[agent_id]
        if agent_id and agent_id in self.wallet_configs:
            self._ensure_clients(agent_id)
            return self._exchanges.get(agent_id)
        return self._legacy_exchange

    def _get_address(self, agent_id: str = "") -> str:
        """Get wallet address for agent. Falls back to legacy address."""
        if agent_id and agent_id in self._addresses:
            return self._addresses[agent_id]
        return self._legacy_address

    def get_all_addresses(self) -> dict[str, str]:
        """Return {agent_id: wallet_address} for all configured wallets."""
        return dict(self._addresses)

    async def execute_signal(self, signal: Signal) -> OrderResult:
        """
        Execute a signal: place entry + SL + TP orders.

        Uses signal.agent_id to select the correct wallet.
        Idempotent: checks DB for existing signal_id before executing.
        """
        # Idempotency check
        if await self._signal_exists(signal.signal_id):
            logger.warning("Signal %s already executed, skipping", signal.signal_id[:12])
            return OrderResult(success=False, error="DUPLICATE_SIGNAL")

        if self.dry_run:
            return await self._execute_dry_run(signal)

        return await self._execute_live(signal)

    async def _execute_dry_run(self, signal: Signal) -> OrderResult:
        """Simulate order execution without touching exchange."""
        result = OrderResult(
            success=True,
            order_id=f"DRY_{signal.signal_id[:8]}_{int(time.time())}",
            fill_price=signal.entry_price,
            fill_qty=signal.notional_usd / signal.entry_price if signal.entry_price > 0 else 0,
            sl_order_id=f"DRY_SL_{signal.signal_id[:8]}",
            tp_order_ids=[f"DRY_TP{i}_{signal.signal_id[:8]}" for i in range(len(signal.take_profits))],
            dry_run=True,
        )

        logger.info(
            "DRY_RUN FILL: %s %s %s @ %.2f qty=%.6f lev=%.1fx SL=%.2f",
            signal.agent_id, signal.direction, signal.symbol,
            result.fill_price, result.fill_qty, signal.leverage, signal.stop_loss,
        )

        # Record in DB
        await self._record_trade(signal, result)
        return result

    async def _execute_live(self, signal: Signal) -> OrderResult:
        """Execute order on Hyperliquid exchange with retries."""
        agent_id = signal.agent_id
        self._ensure_clients(agent_id)

        exchange = self._get_exchange(agent_id)
        if exchange is None:
            return OrderResult(success=False, error="NO_EXCHANGE_CLIENT")

        is_buy = signal.direction == "LONG"
        raw_size = signal.notional_usd / signal.entry_price if signal.entry_price > 0 else 0
        size = round_size(signal.symbol, raw_size, self._info)
        if size <= 0:
            return OrderResult(success=False, error="INVALID_SIZE")

        # Step 1: Update leverage (clamped to exchange max)
        try:
            max_lev = get_max_leverage(signal.symbol, self._info)
            target_lev = min(int(signal.leverage), max_lev)
            if target_lev != int(signal.leverage):
                logger.info(
                    "Leverage clamped %s: %dx → %dx (exchange max=%dx)",
                    signal.symbol, int(signal.leverage), target_lev, max_lev,
                )
                signal.leverage = float(target_lev)
            await asyncio.to_thread(
                update_leverage, exchange, signal.symbol, target_lev,
            )
        except Exception:
            logger.exception("Failed to update leverage for %s", signal.symbol)
            # Non-fatal: continue with current leverage

        # Step 2: Market entry order
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw = await asyncio.to_thread(
                    place_market_order,
                    exchange,
                    signal.symbol,
                    is_buy,
                    size,
                    0.01,  # 1% slippage
                )

                status = raw.get("status", "")
                if status != "ok":
                    error_msg = raw.get("response", {}).get("data", str(raw))
                    logger.warning(
                        "Entry attempt %d/%d failed: %s", attempt, MAX_RETRIES, error_msg,
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY_S * attempt)
                    continue

                # Check fill
                statuses = raw.get("response", {}).get("data", {}).get("statuses", [])
                if statuses:
                    first = statuses[0] if isinstance(statuses, list) else statuses
                    if isinstance(first, dict) and "error" in first:
                        logger.warning(
                            "Entry rejected: %s %s %s — %s",
                            signal.agent_id, signal.symbol, signal.direction, first["error"],
                        )
                        return OrderResult(success=False, error=f"ENTRY_REJECTED: {first['error']}")

                order_ids = _extract_order_ids(statuses)
                entry_oid = order_ids[0] if order_ids else ""
                if not entry_oid:
                    logger.warning("No entry order ID for %s %s", signal.symbol, signal.direction)
                    return OrderResult(success=False, error="NO_ENTRY_ORDER_ID")

                logger.info(
                    "ENTRY FILLED: %s %s %s @ ~%.6f qty=%.6f (attempt %d)",
                    signal.agent_id, signal.direction, signal.symbol,
                    signal.entry_price, size, attempt,
                )

                # Step 3: Place SL trigger order (separate from entry)
                sl_oid = ""
                close_side = not is_buy
                try:
                    sl_raw = await asyncio.to_thread(
                        place_trigger_order,
                        exchange, signal.symbol, close_side, size,
                        signal.stop_loss, "sl",
                    )
                    if sl_raw.get("status") == "ok":
                        sl_statuses = sl_raw.get("response", {}).get("data", {}).get("statuses", [])
                        sl_ids = _extract_order_ids(sl_statuses)
                        sl_oid = sl_ids[0] if sl_ids else ""
                    else:
                        logger.warning("SL order failed for %s: %s", signal.symbol, sl_raw)
                except Exception:
                    logger.exception("SL order failed for %s", signal.symbol)

                # Step 4: TP is handled by position_manager (market order on TP hit)
                # No exchange-side TP trigger orders — avoids double execution

                result = OrderResult(
                    success=True,
                    order_id=entry_oid,
                    fill_price=signal.entry_price,
                    fill_qty=size,
                    sl_order_id=sl_oid,
                    tp_order_ids=[],
                )
                await self._record_trade(signal, result)
                return result

            except Exception as e:
                logger.exception("Order attempt %d/%d exception", attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_S * attempt)

        return OrderResult(success=False, error="MAX_RETRIES_EXCEEDED")

    async def update_sl_order(
        self, symbol: str, direction: str, size: float,
        old_sl_order_id: str, new_sl_price: float, agent_id: str = "",
    ) -> str:
        """Cancel existing SL and place a new one. Returns new SL order_id."""
        if self.dry_run:
            new_id = f"DRY_SL_{int(time.time())}"
            logger.info(
                "DRY_RUN SL update: %s %s SL→%.2f", direction, symbol, new_sl_price,
            )
            return new_id

        self._ensure_clients(agent_id)
        exchange = self._get_exchange(agent_id)
        if exchange is None:
            logger.error("No exchange client for SL update")
            return ""

        # Cancel old SL
        if old_sl_order_id and old_sl_order_id.isdigit():
            try:
                await asyncio.to_thread(
                    cancel_order, exchange, symbol, int(old_sl_order_id),
                )
            except Exception:
                logger.warning("Failed to cancel old SL %s", old_sl_order_id, exc_info=True)

        # Place new SL trigger order
        is_buy = direction == "SHORT"  # close SHORT → BUY
        size = round_size(symbol, size, self._info)
        if size <= 0:
            logger.error("SL update failed: size rounds to 0 for %s", symbol)
            return ""
        try:
            raw = await asyncio.to_thread(
                place_trigger_order, exchange, symbol, is_buy, size,
                new_sl_price, "sl",
            )
            if raw.get("status") == "ok":
                statuses = raw.get("response", {}).get("data", {}).get("statuses", [])
                ids = _extract_order_ids(statuses)
                new_id = ids[0] if ids else ""
                logger.info("SL updated: %s %s → %.2f (id=%s)", symbol, direction, new_sl_price, new_id)
                return new_id
        except Exception:
            logger.exception("Failed to place new SL for %s", symbol)

        return ""

    async def cancel_orders(
        self, symbol: str, order_ids: list[str], agent_id: str = "",
    ) -> bool:
        """Cancel a list of orders. Returns True if all cancelled."""
        if self.dry_run or not order_ids:
            return True

        self._ensure_clients(agent_id)
        exchange = self._get_exchange(agent_id)
        if exchange is None:
            return False

        success = True
        for oid in order_ids:
            try:
                oid_int = int(oid) if oid.isdigit() else 0
                if oid_int > 0:
                    await asyncio.to_thread(exchange.cancel, symbol, oid_int)
            except Exception:
                logger.exception("Failed to cancel order %s", oid)
                success = False
        return success

    async def close_position(
        self, symbol: str, direction: str, size: float,
        reason: str = "", agent_id: str = "",
    ) -> OrderResult:
        """Close a position with a market order."""
        if self.dry_run:
            logger.info("DRY_RUN CLOSE: %s %s size=%.6f reason=%s", direction, symbol, size, reason)
            return OrderResult(success=True, dry_run=True)

        self._ensure_clients(agent_id)
        exchange = self._get_exchange(agent_id)
        if exchange is None:
            return OrderResult(success=False, error="NO_EXCHANGE_CLIENT")

        is_buy = direction == "SHORT"  # close SHORT → BUY, close LONG → SELL
        size = round_size(symbol, size, self._info)
        if size <= 0:
            return OrderResult(success=False, error="INVALID_SIZE_AFTER_ROUND")
        try:
            raw = await asyncio.to_thread(
                place_market_order, exchange, symbol, is_buy, size,
            )
            if raw.get("status") == "ok":
                logger.info("CLOSED: %s %s size=%.6f reason=%s", direction, symbol, size, reason)
                return OrderResult(success=True)
            return OrderResult(success=False, error=str(raw))
        except Exception as e:
            logger.exception("Close position error")
            return OrderResult(success=False, error=str(e))

    async def get_exchange_positions(self, agent_id: str = "") -> list[dict] | None:
        """Fetch current positions from exchange for a specific agent wallet.

        Returns:
            list[dict]: Positions on success (may be empty if genuinely no positions).
            None: On API failure — callers MUST treat None as "unknown state"
                  and skip any reconciliation/SL logic to avoid false liquidation.
        """
        address = self._get_address(agent_id)
        if self.dry_run or not address:
            return []

        self._ensure_info()
        if self._info is None:
            return None

        try:
            state = await asyncio.to_thread(
                get_user_state, self._info, address,
            )
            positions = []
            for pos in state.get("assetPositions", []):
                p = pos.get("position", {})
                size = float(p.get("szi", 0))
                if abs(size) > 0:
                    positions.append({
                        "symbol": p.get("coin", ""),
                        "size": size,
                        "direction": "LONG" if size > 0 else "SHORT",
                        "entry_price": float(p.get("entryPx", 0)),
                        "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
                        "leverage": float(p.get("leverage", {}).get("value", 1)),
                        "margin_used": float(p.get("marginUsed", 0)),
                    })
            return positions
        except Exception:
            logger.exception("Failed to fetch exchange positions for %s", agent_id or "legacy")
            return None

    async def get_trigger_orders(self, agent_id: str = "") -> list[dict]:
        """Fetch open trigger orders (SL/TP) for an agent wallet."""
        address = self._get_address(agent_id)
        if self.dry_run or not address:
            return []

        self._ensure_info()
        if self._info is None:
            return []

        try:
            orders = await asyncio.to_thread(
                get_frontend_open_orders, self._info, address,
            )
            return orders
        except Exception:
            logger.exception("Failed to fetch trigger orders for %s", agent_id or "legacy")
            return []

    async def place_sl_tp_for_position(
        self,
        symbol: str,
        direction: str,
        size: float,
        sl_price: float,
        take_profits: list[dict],
        agent_id: str = "",
    ) -> tuple[str, list[str]]:
        """Place SL and TP trigger orders for an existing position.

        Pass sl_price=0 to skip SL placement (e.g. when SL already exists).
        Pass take_profits=[] to skip TP placement.

        Returns (sl_order_id, [tp_order_ids]).
        """
        if self.dry_run:
            sl_id = f"DRY_SL_{int(time.time())}" if sl_price > 0 else ""
            tp_ids = [f"DRY_TP{i}_{int(time.time())}" for i in range(len(take_profits))]
            logger.info("DRY_RUN SL/TP restore: %s %s SL=%.2f TPs=%d", direction, symbol, sl_price, len(take_profits))
            return sl_id, tp_ids

        self._ensure_clients(agent_id)
        exchange = self._get_exchange(agent_id)
        if exchange is None:
            logger.error("No exchange client for SL/TP placement (%s)", agent_id)
            return "", []

        close_side = direction == "SHORT"  # close SHORT → BUY, close LONG → SELL
        size = round_size(symbol, size, self._info)
        if size <= 0:
            logger.error("SL/TP placement: size rounds to 0 for %s", symbol)
            return "", []

        # Place SL
        sl_oid = ""
        if sl_price > 0:
            try:
                sl_raw = await asyncio.to_thread(
                    place_trigger_order, exchange, symbol, close_side, size,
                    sl_price, "sl",
                )
                if sl_raw.get("status") == "ok":
                    sl_statuses = sl_raw.get("response", {}).get("data", {}).get("statuses", [])
                    sl_ids = _extract_order_ids(sl_statuses)
                    sl_oid = sl_ids[0] if sl_ids else ""
                    logger.info("SL restored: %s %s @ %.2f (id=%s)", symbol, direction, sl_price, sl_oid)
                else:
                    logger.warning("SL restore failed for %s: %s", symbol, sl_raw)
            except Exception:
                logger.exception("SL restore failed for %s", symbol)

        # Place TPs
        tp_oids: list[str] = []
        for tp in take_profits:
            tp_sz = round_size(symbol, size * tp["ratio"] / 100.0, self._info)
            if tp_sz <= 0:
                continue
            try:
                tp_raw = await asyncio.to_thread(
                    place_trigger_order, exchange, symbol, close_side, tp_sz,
                    tp["price"], "tp",
                )
                if tp_raw.get("status") == "ok":
                    tp_statuses = tp_raw.get("response", {}).get("data", {}).get("statuses", [])
                    tp_ids = _extract_order_ids(tp_statuses)
                    if tp_ids:
                        tp_oids.append(tp_ids[0])
                        logger.info("TP restored: %s %s @ %.2f (id=%s)", symbol, direction, tp["price"], tp_ids[0])
                    else:
                        logger.warning("TP restore failed for %s @ %.2f: %s", symbol, tp["price"], tp_raw)
                else:
                    logger.warning("TP restore failed for %s @ %.2f: %s", symbol, tp["price"], tp_raw)
            except Exception:
                logger.exception("TP restore failed for %s @ %.2f", symbol, tp["price"])

        return sl_oid, tp_oids

    # ── DB Persistence ──

    async def _signal_exists(self, signal_id: str) -> bool:
        """Check if a signal has already been executed."""
        cursor = await self.db.execute(
            "SELECT 1 FROM trades WHERE signal_id = ?", (signal_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def _record_trade(self, signal: Signal, result: OrderResult):
        """Record trade in DB (entry only — exit updated later)."""
        try:
            await self.db.execute(
                """
                INSERT OR IGNORE INTO trades
                    (signal_id, agent_id, symbol, side, entry_time, entry_price,
                     leverage, notional_usd, margin_usd, sl_pct,
                     regime, inflection_type, inflection_score,
                     validation_score, pattern_confirmations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.agent_id,
                    signal.symbol,
                    signal.direction,
                    int(time.time() * 1000),
                    result.fill_price,
                    signal.leverage,
                    signal.notional_usd,
                    signal.notional_usd / signal.leverage if signal.leverage > 0 else 0,
                    abs(signal.entry_price - signal.stop_loss) / signal.entry_price if signal.entry_price > 0 else 0,
                    signal.phase_snapshot.get("regime", "") if signal.phase_snapshot else "",
                    signal.phase_snapshot.get("primary_type", "") if signal.phase_snapshot else "",
                    signal.phase_snapshot.get("scan_score", 0) if signal.phase_snapshot else 0,
                    signal.phase_snapshot.get("gate_score", 0) if signal.phase_snapshot else 0,
                    str(signal.phase_snapshot.get("confirmations", [])) if signal.phase_snapshot else "[]",
                ),
            )

            # Also record in positions table
            await self.db.execute(
                """
                INSERT OR IGNORE INTO positions
                    (agent_id, symbol, direction, entry_price, size_usd,
                     sl_pct, entry_time, signal_id, exchange_order_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
                """,
                (
                    signal.agent_id,
                    signal.symbol,
                    signal.direction,
                    result.fill_price,
                    signal.notional_usd,
                    abs(signal.entry_price - signal.stop_loss) / signal.entry_price if signal.entry_price > 0 else 0,
                    int(time.time() * 1000),
                    signal.signal_id,
                    result.order_id,
                ),
            )

            await self.db.commit()
        except Exception:
            logger.exception("Failed to record trade for %s", signal.signal_id[:12])

    async def record_exit(
        self, signal_id: str, exit_price: float, pnl_usd: float,
        pnl_pct: float, exit_reason: str,
    ):
        """Update trade record with exit info."""
        try:
            now_ms = int(time.time() * 1000)
            await self.db.execute(
                """
                UPDATE trades
                SET exit_time = ?, exit_price = ?, pnl_usd = ?,
                    pnl_pct = ?, exit_reason = ?
                WHERE signal_id = ?
                """,
                (now_ms, exit_price, pnl_usd, pnl_pct, exit_reason, signal_id),
            )
            await self.db.execute(
                "UPDATE positions SET status = 'CLOSED' WHERE signal_id = ?",
                (signal_id,),
            )
            await self.db.commit()
        except Exception:
            logger.exception("Failed to record exit for %s", signal_id[:12])


def _extract_order_ids(statuses: list) -> list[str]:
    """Extract order IDs from bulk_orders response statuses."""
    ids = []
    for s in statuses:
        if isinstance(s, dict):
            resting = s.get("resting", {})
            filled = s.get("filled", {})
            oid = resting.get("oid") or filled.get("oid") or ""
            ids.append(str(oid))
        else:
            ids.append("")
    return ids
