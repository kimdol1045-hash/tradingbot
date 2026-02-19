"""
Main entry point — single-process runner.
Collector + Pipeline Runner + PositionManager + Evolver + EquityTracker
in one asyncio event loop.
Agents run sequentially: S1 → S2 → S3 → S4 per 5m candle close.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from src.collector.collector import DataCollector
from src.exchange.executor import OrderExecutor
from src.exchange.reconciliation import reconcile_on_startup
from src.notify.telegram import notify_error, notify_system
from src.openclaw.evolver import Evolver
from src.pipeline.equity_tracker import EquityTracker
from src.pipeline.position_manager import PositionManager
from src.pipeline.runner import PipelineRunner
from src.utils.config import AGENT_PROFILES, ALL_SYMBOLS, HYPERLIQUID_KEY, LOG_LEVEL
from src.utils.db import init_db
from src.utils.health import register_components, start_health_server

# ── Logging Setup ──

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s | %(name)-24s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# ── Configuration ──
TOTAL_CAPITAL = float(os.getenv("TOTAL_CAPITAL", "10000"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"


def _setup_global_exception_handler():
    """Install global handlers for uncaught exceptions."""
    def handle_exception(loop, context):
        msg = context.get("exception", context["message"])
        logger.error("Unhandled async exception: %s", msg, exc_info=context.get("exception"))
        asyncio.ensure_future(
            notify_error(f"Unhandled exception: {msg}")
        )

    def handle_sync_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    asyncio.get_event_loop().set_exception_handler(handle_exception)
    sys.excepthook = handle_sync_exception


async def main() -> None:
    # ── Global error handling ──
    _setup_global_exception_handler()

    # ── Database ──
    db = await init_db()

    # ── Initialize components ──
    collector = DataCollector()
    equity_tracker = EquityTracker()

    # Wallet address for exchange queries (derived from private key)
    wallet_address = ""
    if HYPERLIQUID_KEY and not DRY_RUN:
        try:
            import eth_account
            wallet_address = eth_account.Account.from_key(HYPERLIQUID_KEY).address
        except Exception:
            logger.warning("Could not derive wallet address from key")

    executor = OrderExecutor(db=db, dry_run=DRY_RUN, wallet_address=wallet_address)
    position_mgr = PositionManager(executor=executor, dry_run=DRY_RUN)
    pipeline = PipelineRunner(
        candle_cache=collector.cache,
        position_manager=position_mgr,
        db=db,
    )
    evolver = Evolver(equity_tracker=equity_tracker, position_manager=position_mgr)

    # Allocate capital per agent
    for agent_id, profile in AGENT_PROFILES.items():
        capital = TOTAL_CAPITAL * profile.capital_pct
        pipeline.set_capital(agent_id, capital)
        equity_tracker.initialize_agent(agent_id, capital)
        logger.info("Agent %s: $%.0f (%.0f%%)", agent_id, capital, profile.capital_pct * 100)

    # Wire callbacks
    collector.on_candle_close_callback = pipeline.on_candle_close

    # Register health check
    register_components(
        collector=collector,
        pipeline=pipeline,
        position_manager=position_mgr,
        equity_tracker=equity_tracker,
        evolver=evolver,
    )

    # ── Signal handlers ──
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(
                shutdown(collector, position_mgr, evolver, db),
            ),
        )

    try:
        # ── Reconciliation on startup ──
        logger.info("Running startup reconciliation...")
        recon = await reconcile_on_startup(executor, db)
        logger.info(
            "Reconciliation: matched=%d, stale=%d, orphaned=%d",
            recon["matched"], recon["stale"], recon["orphaned"],
        )

        # ── Start collector ──
        await collector.start(symbols=ALL_SYMBOLS)

        # Backfill from Hyperliquid
        logger.info("Backfilling historical data from Hyperliquid...")
        await collector.backfill(symbols=ALL_SYMBOLS)
        logger.info("Backfill complete, starting live trading")

        await notify_system(
            f"System started (dry_run={DRY_RUN})\n"
            f"Capital: ${TOTAL_CAPITAL:,.0f}\n"
            f"Agents: {', '.join(AGENT_PROFILES.keys())}\n"
            f"Symbols: {', '.join(ALL_SYMBOLS)}\n"
            f"Reconciliation: {recon['matched']} matched, {recon['stale']} stale, {recon['orphaned']} orphaned"
        )

        # Run all components concurrently
        await asyncio.gather(
            collector.run_forever(),
            position_mgr.monitor_loop(interval=5.0),
            evolver.run_loop(interval_hours=float(os.getenv("EVOLVER_INTERVAL_HOURS", "4"))),
            start_health_server(port=int(os.getenv("HEALTH_PORT", "8080"))),
        )
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.critical("Fatal error in main loop", exc_info=True)
        try:
            await notify_error("Fatal error — system shutting down")
        except Exception:
            pass
    finally:
        evolver.stop()
        position_mgr.stop()
        await collector.stop()
        await db.close()
        await notify_system("System stopped")


async def shutdown(
    collector: DataCollector,
    position_mgr: PositionManager,
    evolver: Evolver,
    db,
) -> None:
    logger.info("Shutdown signal received")
    evolver.stop()
    position_mgr.stop()
    await collector.stop()
    if db:
        await db.close()
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
