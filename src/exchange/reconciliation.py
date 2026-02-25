"""
Restart Reconciliation — sync exchange state with DB on startup.

On restart:
  1. Fetch open positions from exchange (per-wallet)
  2. Compare with DB position records
  3. Handle orphans (exchange has, DB doesn't) → close or adopt
  4. Handle stale records (DB has, exchange doesn't) → mark closed
  5. Re-place SL/TP orders for surviving positions
"""
from __future__ import annotations

import logging
from collections import defaultdict

import aiosqlite

from src.exchange.executor import OrderExecutor

logger = logging.getLogger(__name__)


def _best_agent_for_symbol(agent_ids: list[str], symbol: str) -> str:
    """Find the best agent_id for a given symbol based on tier policy.

    Falls back to first agent_id if no match found.
    """
    if not agent_ids or agent_ids == [""]:
        return agent_ids[0] if agent_ids else ""

    try:
        from src.utils.config import get_agent_symbols
        for agent_id in agent_ids:
            if symbol in get_agent_symbols(agent_id):
                return agent_id
    except Exception:
        pass

    return agent_ids[0]


def _group_agents_by_wallet(executor: OrderExecutor) -> dict[str, list[str]]:
    """Group agent IDs by wallet address for per-wallet reconciliation.

    Returns {wallet_address: [agent_id, ...]}.
    """
    wallet_to_agents: dict[str, list[str]] = defaultdict(list)
    all_addresses = executor.get_all_addresses()

    if all_addresses:
        for agent_id, address in all_addresses.items():
            wallet_to_agents[address].append(agent_id)
    elif executor.wallet_address:
        # Legacy single-wallet mode
        wallet_to_agents[executor.wallet_address].append("")

    return dict(wallet_to_agents)


async def _reconcile_wallet(
    executor: OrderExecutor,
    db: aiosqlite.Connection,
    wallet_address: str,
    agent_ids: list[str],
    exchange_positions: list[dict],
) -> dict:
    """Reconcile a single wallet's positions against DB.

    Returns summary dict: {matched, orphaned, stale, errors}
    """
    summary = {"matched": 0, "orphaned": 0, "stale": 0, "errors": 0}

    exchange_map: dict[str, dict] = {}
    for pos in exchange_positions:
        key = f"{pos['symbol']}_{pos['direction']}"
        exchange_map[key] = pos

    # Build agent_id filter for DB query
    if agent_ids and agent_ids != [""]:
        placeholders = ",".join("?" for _ in agent_ids)
        agent_filter = f"AND agent_id IN ({placeholders})"
        query_params = tuple(agent_ids)
    else:
        agent_filter = ""
        query_params = ()

    # Get open positions from DB for these agents
    cursor = await db.execute(
        "SELECT signal_id, agent_id, symbol, direction, entry_price, size_usd, "
        f"exchange_order_id FROM positions WHERE status = 'OPEN' {agent_filter}",
        query_params,
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
                "Reconcile MATCH: %s %s %s (signal=%s, wallet=%s)",
                db_pos["agent_id"], db_pos["direction"], db_pos["symbol"],
                db_pos["signal_id"][:12], wallet_address[:10],
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

    # Check for orphaned positions (exchange has, DB doesn't)
    for key, ex_pos in exchange_map.items():
        if key not in db_keys:
            summary["orphaned"] += 1

            # Try to find the best agent for this orphan's symbol based on tier policy
            close_agent = _best_agent_for_symbol(agent_ids, ex_pos["symbol"])

            logger.warning(
                "Reconcile ORPHAN: %s %s size=%.6f on exchange but not in DB (wallet=%s, agent=%s)",
                ex_pos["direction"], ex_pos["symbol"], abs(ex_pos["size"]),
                wallet_address[:10], close_agent,
            )
            # Close orphaned position for safety
            try:
                result = await executor.close_position(
                    ex_pos["symbol"],
                    ex_pos["direction"],
                    abs(ex_pos["size"]),
                    reason="RECONCILE_ORPHAN",
                    agent_id=close_agent,
                )
                if not result.success:
                    logger.error("Failed to close orphan: %s", result.error)
                    summary["errors"] += 1
            except Exception:
                logger.exception("Error closing orphan position")
                summary["errors"] += 1

    return summary


async def reconcile_on_startup(
    executor: OrderExecutor,
    db: aiosqlite.Connection,
) -> dict:
    """
    Reconcile exchange positions with DB on restart.

    Supports multi-wallet mode: reconciles each wallet independently.

    Returns summary dict:
        {matched: int, orphaned: int, stale: int, errors: int}
    """
    total = {"matched": 0, "orphaned": 0, "stale": 0, "errors": 0}

    wallet_groups = _group_agents_by_wallet(executor)

    if not wallet_groups:
        # No wallets configured (dry_run or no keys).
        # Still reconcile DB positions against empty exchange state.
        wallet_groups = {"": [""]}

    for wallet_address, agent_ids in wallet_groups.items():
        # Fetch positions for this wallet (use first agent_id)
        first_agent = agent_ids[0] if agent_ids else ""
        exchange_positions = await executor.get_exchange_positions(first_agent)

        # API failure → None: skip this wallet entirely to preserve positions
        if exchange_positions is None:
            logger.error(
                "API FAILURE: cannot fetch positions for wallet %s (agents=%s) "
                "— skipping reconciliation to protect existing positions",
                wallet_address[:10], agent_ids,
            )
            total["errors"] += 1
            continue

        logger.info(
            "Reconciling wallet %s... (agents=%s, exchange_positions=%d)",
            wallet_address[:10], agent_ids, len(exchange_positions),
        )

        wallet_summary = await _reconcile_wallet(
            executor, db, wallet_address, agent_ids, exchange_positions,
        )

        for k in total:
            total[k] += wallet_summary[k]

    await db.commit()

    logger.info(
        "Reconciliation complete: matched=%d, stale=%d, orphaned=%d, errors=%d",
        total["matched"], total["stale"], total["orphaned"], total["errors"],
    )
    return total
