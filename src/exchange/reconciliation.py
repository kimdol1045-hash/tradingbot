"""
Restart Reconciliation — sync exchange state with DB on startup.

On restart:
  1. Fetch open positions from exchange
  2. Compare with DB position records
  3. Handle orphans (exchange has, DB doesn't) → close or adopt
  4. Handle stale records (DB has, exchange doesn't) → mark closed
  5. Re-place SL/TP orders for surviving positions
"""
from __future__ import annotations

import logging

import aiosqlite

from src.exchange.executor import OrderExecutor

logger = logging.getLogger(__name__)


async def reconcile_on_startup(
    executor: OrderExecutor,
    db: aiosqlite.Connection,
) -> dict:
    """
    Reconcile exchange positions with DB on restart.

    Returns summary dict:
        {matched: int, orphaned: int, stale: int, errors: int}
    """
    summary = {"matched": 0, "orphaned": 0, "stale": 0, "errors": 0}

    # 1. Get positions from exchange
    exchange_positions = await executor.get_exchange_positions()
    exchange_map: dict[str, dict] = {}
    for pos in exchange_positions:
        key = f"{pos['symbol']}_{pos['direction']}"
        exchange_map[key] = pos

    # 2. Get open positions from DB
    cursor = await db.execute(
        "SELECT signal_id, agent_id, symbol, direction, entry_price, size_usd, "
        "exchange_order_id FROM positions WHERE status = 'OPEN'",
    )
    rows = await cursor.fetchall()
    columns = ["signal_id", "agent_id", "symbol", "direction",
               "entry_price", "size_usd", "exchange_order_id"]
    db_positions = [dict(zip(columns, row)) for row in rows]

    db_keys: set[str] = set()
    for db_pos in db_positions:
        key = f"{db_pos['symbol']}_{db_pos['direction']}"
        db_keys.add(key)

        if key in exchange_map:
            # Matched: position exists on both sides
            summary["matched"] += 1
            logger.info(
                "Reconcile MATCH: %s %s %s (signal=%s)",
                db_pos["agent_id"], db_pos["direction"], db_pos["symbol"],
                db_pos["signal_id"][:12],
            )
        else:
            # Stale: DB says open but exchange doesn't have it
            summary["stale"] += 1
            logger.warning(
                "Reconcile STALE: %s %s %s not on exchange, marking closed",
                db_pos["agent_id"], db_pos["direction"], db_pos["symbol"],
            )
            try:
                await db.execute(
                    "UPDATE positions SET status = 'CLOSED' WHERE signal_id = ?",
                    (db_pos["signal_id"],),
                )
                await db.execute(
                    "UPDATE trades SET exit_reason = 'RECONCILE_STALE' "
                    "WHERE signal_id = ? AND exit_reason IS NULL",
                    (db_pos["signal_id"],),
                )
            except Exception:
                logger.exception("Error marking stale position")
                summary["errors"] += 1

    # 3. Check for orphaned positions (exchange has, DB doesn't)
    for key, ex_pos in exchange_map.items():
        if key not in db_keys:
            summary["orphaned"] += 1
            logger.warning(
                "Reconcile ORPHAN: %s %s size=%.6f on exchange but not in DB",
                ex_pos["direction"], ex_pos["symbol"], abs(ex_pos["size"]),
            )
            # Close orphaned position for safety
            try:
                result = await executor.close_position(
                    ex_pos["symbol"],
                    ex_pos["direction"],
                    abs(ex_pos["size"]),
                    reason="RECONCILE_ORPHAN",
                )
                if not result.success:
                    logger.error("Failed to close orphan: %s", result.error)
                    summary["errors"] += 1
            except Exception:
                logger.exception("Error closing orphan position")
                summary["errors"] += 1

    await db.commit()

    logger.info(
        "Reconciliation complete: matched=%d, stale=%d, orphaned=%d, errors=%d",
        summary["matched"], summary["stale"], summary["orphaned"], summary["errors"],
    )
    return summary
