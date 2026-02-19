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
    get_exchange_client,
    get_info_client,
    get_user_state,
    place_entry_with_sl_tp,
    place_market_order,
    place_trigger_order,
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
            # Multi-wallet mode
            if agent_id not in self._exchanges:
                wc = self.wallet_configs[agent_id]
                client = get_exchange_client(private_key=wc.private_key)
                if client is None:
                    logger.error("Exchange client unavailable for agent %s", agent_id)
                else:
                    self._exchanges[agent_id] = client
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
            # Lazy init on demand
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
        size = signal.notional_usd / signal.entry_price if signal.entry_price > 0 else 0
        if size <= 0:
            return OrderResult(success=False, error="INVALID_SIZE")

        # Step 1: Update leverage
        try:
            await asyncio.to_thread(
                update_leverage, exchange, signal.symbol, int(signal.leverage),
            )
        except Exception:
            logger.exception("Failed to update leverage for %s", signal.symbol)
            # Non-fatal: continue with current leverage

        # Step 2: Place entry + SL + TP atomically
        tp_prices = [tp["price"] for tp in signal.take_profits]
        tp_sizes = [size * tp["ratio"] for tp in signal.take_profits]

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw = await asyncio.to_thread(
                    place_entry_with_sl_tp,
                    exchange,
                    signal.symbol,
                    is_buy,
                    size,
                    signal.entry_price,  # limit price (IOC)
                    signal.stop_loss,
                    tp_prices,
                    tp_sizes,
                )

                status = raw.get("status", "")
                if status == "ok":
                    statuses = raw.get("response", {}).get("data", {}).get("statuses", [])
                    order_ids = _extract_order_ids(statuses)

                    result = OrderResult(
                        success=True,
                        order_id=order_ids[0] if order_ids else "",
                        fill_price=signal.entry_price,  # will be updated from fill
                        fill_qty=size,
                        sl_order_id=order_ids[1] if len(order_ids) > 1 else "",
                        tp_order_ids=order_ids[2:] if len(order_ids) > 2 else [],
                    )

                    logger.info(
                        "LIVE FILL: %s %s %s @ ~%.2f qty=%.6f (attempt %d)",
                        signal.agent_id, signal.direction, signal.symbol,
                        signal.entry_price, size, attempt,
                    )

                    await self._record_trade(signal, result)
                    return result

                # Non-OK status
                error_msg = raw.get("response", {}).get("data", str(raw))
                logger.warning(
                    "Order attempt %d/%d failed: %s", attempt, MAX_RETRIES, error_msg,
                )

            except Exception as e:
                logger.exception("Order attempt %d/%d exception", attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_S * attempt)

        return OrderResult(success=False, error="MAX_RETRIES_EXCEEDED")

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

    async def get_exchange_positions(self, agent_id: str = "") -> list[dict]:
        """Fetch current positions from exchange for a specific agent wallet."""
        address = self._get_address(agent_id)
        if self.dry_run or not address:
            return []

        self._ensure_info()
        if self._info is None:
            return []

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
            return []

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
