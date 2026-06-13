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

from src.ai.market_advisor import MarketAdvisor
from src.collector.collector import DataCollector
from src.exchange.executor import OrderExecutor
from src.exchange.hyperliquid import fetch_wallet_balance, get_info_client
from src.exchange.reconciliation import reconcile_on_startup
from src.notify.telegram import command_handler, notify_daily_report, notify_error, notify_system
from src.openclaw.evolver import Evolver
from src.pipeline.equity_tracker import EquityTracker
from src.pipeline.position_manager import PositionManager
from src.pipeline.runner import PipelineRunner
from src.pipeline.screener_scheduler import ScreenerScheduler
from src.utils import config as cfg
from src.utils.config import (
    AGENT_PROFILES,
    LOG_LEVEL,
    get_wallet_configs,
    reload_symbol_pool,
)
from src.dashboard.app import create_app
from src.utils.db import init_db
from src.utils.health import register_components, start_health_server

# ── Logging Setup ──

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s | %(name)-24s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)
logger = logging.getLogger("main")

# ── Configuration ──
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ADVISOR_ENABLED = os.getenv("ADVISOR_ENABLED", "true").lower() == "true"
ADVISOR_INTERVAL_HOURS = float(os.getenv("ADVISOR_INTERVAL_HOURS", "1"))


def _setup_global_exception_handler():
    """Install global handlers for uncaught exceptions."""
    def handle_exception(loop, context):
        msg = context.get("exception", context["message"])
        logger.error("Unhandled async exception: %s", msg, exc_info=context.get("exception"))
        from src.utils.async_helpers import safe_fire_and_forget
        safe_fire_and_forget(
            notify_error(f"Unhandled exception: {msg}"),
            name="notify_error",
        )

    def handle_sync_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    asyncio.get_event_loop().set_exception_handler(handle_exception)
    sys.excepthook = handle_sync_exception


async def daily_report_loop(equity_tracker: EquityTracker, hour_kst: int = 9):
    """Send daily performance report at a fixed KST hour."""
    from datetime import datetime, timedelta, timezone

    kst = timezone(timedelta(hours=9))
    await asyncio.sleep(30)  # Wait for initial data

    while True:
        now = datetime.now(kst)
        target = now.replace(hour=hour_kst, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_sec = (target - now).total_seconds()
        logger.info("Daily report scheduled at %s KST (%.0fs)", target.strftime("%H:%M"), wait_sec)
        await asyncio.sleep(wait_sec)

        try:
            report = equity_tracker.get_daily_report()
            await notify_daily_report(report)
            logger.info("Daily report sent: PnL=$%.2f trades=%d", report.get("total_pnl", 0), report.get("total_trades", 0))
        except Exception:
            logger.warning("Daily report failed", exc_info=True)


async def main() -> None:
    # ── Global error handling ──
    _setup_global_exception_handler()

    # ── Database ──
    db = await init_db()

    # ── Initialize components ──
    collector = DataCollector()
    equity_tracker = EquityTracker()

    # Load per-agent wallet configs (multi-wallet or legacy fallback)
    wallet_configs = get_wallet_configs() if not DRY_RUN else {}
    if wallet_configs:
        logger.info(
            "Multi-wallet mode: %d wallets (%s)",
            len(wallet_configs),
            ", ".join(f"{k}={v.wallet_address[:10]}..." for k, v in wallet_configs.items()),
        )
    elif not DRY_RUN:
        logger.warning("No wallet keys configured — live trading unavailable")

    executor = OrderExecutor(db=db, dry_run=DRY_RUN, wallet_configs=wallet_configs)
    position_mgr = PositionManager(
        executor=executor, dry_run=DRY_RUN,
        equity_tracker=equity_tracker,
        candle_cache=collector.cache,
    )
    advisor = MarketAdvisor(
        candle_cache=collector.cache,
        interval_hours=ADVISOR_INTERVAL_HOURS,
    ) if ADVISOR_ENABLED else None
    pipeline = PipelineRunner(
        candle_cache=collector.cache,
        position_manager=position_mgr,
        db=db,
        advisor=advisor,
        equity_tracker=equity_tracker,
    )
    evolver = Evolver(equity_tracker=equity_tracker, position_manager=position_mgr)
    screener = ScreenerScheduler(collector=collector)

    # Load symbol pool from symbols.json if available
    reload_symbol_pool()
    # Re-read ALL_SYMBOLS after reload (import binding doesn't track reassignment)
    from src.utils.config import ALL_SYMBOLS as _reloaded_symbols
    all_symbols = _reloaded_symbols

    # Allocate capital per agent from wallet balances
    wallet_addresses: dict[str, str] = {}
    if wallet_configs and not DRY_RUN:
        try:
            info = get_info_client()
        except Exception:
            logger.error("Failed to connect to Hyperliquid API", exc_info=True)
            info = None
        for agent_id, wc in wallet_configs.items():
            wallet_addresses[agent_id] = wc.wallet_address
            if info:
                try:
                    balance = fetch_wallet_balance(info, wc.wallet_address)
                except Exception:
                    logger.warning("Failed to fetch balance for %s", agent_id, exc_info=True)
                    balance = 0.0
            else:
                balance = 0.0
            pipeline.set_capital(agent_id, balance)
            equity_tracker.initialize_agent(agent_id, balance)
            await equity_tracker.restore_from_db(agent_id)
            logger.info("Agent %s: $%.2f (%s...)", agent_id, balance, wc.wallet_address[:12])
    else:
        for agent_id in AGENT_PROFILES:
            pipeline.set_capital(agent_id, 0)
            equity_tracker.initialize_agent(agent_id, 0)

    # Wire callbacks
    collector.on_candle_close_callback = pipeline.on_candle_close
    equity_tracker.add_trade_callback(pipeline.circuit_breaker.record_trade_result)

    # Register health check
    register_components(
        collector=collector,
        pipeline=pipeline,
        position_manager=position_mgr,
        equity_tracker=equity_tracker,
        evolver=evolver,
        advisor=advisor,
        screener=screener,
    )

    # Set up wallet addresses for balance monitoring
    if wallet_addresses:
        equity_tracker.set_wallet_addresses(wallet_addresses)
        logger.info("Balance monitoring: %d wallets configured", len(wallet_addresses))

    # Register Telegram command handler components
    command_handler.set_components({
        "equity_tracker": equity_tracker,
        "position_manager": position_mgr,
        "pipeline": pipeline,
        "collector": collector,
    })

    # ── Signal handlers ──
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(
                shutdown(collector, position_mgr, evolver, advisor, screener, db),
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

        # Restore matched positions into PositionManager in-memory tracking
        await position_mgr.restore_from_db(db)

        # ── Start collector ──
        await collector.start(symbols=all_symbols)

        # Backfill from Hyperliquid
        logger.info("Backfilling historical data from Hyperliquid...")
        await collector.backfill(symbols=all_symbols)
        logger.info("Backfill complete")

        # Re-place missing SL trigger orders (after backfill to avoid rate limit clash)
        for _attempt in range(3):
            try:
                await position_mgr.ensure_exchange_orders()
                break
            except Exception:
                logger.warning("ensure_exchange_orders failed (attempt %d/3), retrying in 5s", _attempt + 1)
                await asyncio.sleep(5)
        logger.info("Starting live trading")

        total_capital = sum(a.current_equity for a in equity_tracker.agents.values())
        mode = "LIVE (메인넷)" if not DRY_RUN else "DRY RUN (모의매매)"
        agent_lines = "\n".join(
            f"  {aid}: ${a.current_equity:,.2f}"
            for aid, a in equity_tracker.agents.items()
        )
        tier_lines = "\n".join(
            f"  {tier} ({len(syms)}): {', '.join(syms)}"
            for tier, syms in cfg.SYMBOL_POOL.items()
        )
        await notify_system(
            f"시스템 시작 [{mode}]\n"
            f"총 자본: ${total_capital:,.2f}\n"
            f"{agent_lines}\n"
            f"매매 심볼 (스크리너 선별):\n{tier_lines}\n"
            f"어드바이저: {'ON' if advisor else 'OFF'}"
            f"{f' ({ADVISOR_INTERVAL_HOURS}h)' if advisor else ''}"
        )

        # Dashboard server (uvicorn embedded)
        import uvicorn

        dashboard_app = create_app()
        dashboard_port = int(os.getenv("DASHBOARD_PORT", "8501"))
        uvi_config = uvicorn.Config(
            dashboard_app, host="0.0.0.0", port=dashboard_port, log_level="warning",
        )
        dashboard_server = uvicorn.Server(uvi_config)

        # Run all components concurrently
        coros = [
            collector.run_forever(),
            position_mgr.monitor_loop(interval=5.0),
            evolver.run_loop(interval_hours=float(os.getenv("EVOLVER_INTERVAL_HOURS", "4"))),
            start_health_server(port=int(os.getenv("HEALTH_PORT", "8080"))),
            screener.run_loop(),
            dashboard_server.serve(),
        ]
        if advisor:
            coros.append(advisor.run_loop())
        if wallet_addresses:
            balance_interval = float(os.getenv("BALANCE_SYNC_INTERVAL", "60"))
            coros.append(equity_tracker.balance_sync_loop(interval_sec=balance_interval))
        daily_hour_kst = int(os.getenv("DAILY_REPORT_HOUR_KST", "9"))
        coros.append(daily_report_loop(equity_tracker, hour_kst=daily_hour_kst))
        coros.append(command_handler.poll_loop())
        logger.info("Dashboard listening on http://0.0.0.0:%d", dashboard_port)
        await asyncio.gather(*coros)
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.critical("Fatal error in main loop", exc_info=True)
        try:
            await notify_error("Fatal error — system shutting down")
        except Exception:
            pass
    finally:
        equity_tracker.stop_sync()
        command_handler.stop()
        evolver.stop()
        screener.stop()
        if advisor:
            advisor.stop()
        position_mgr.stop()
        await collector.stop()
        await db.close()
        await notify_system("System stopped")


async def shutdown(
    collector: DataCollector,
    position_mgr: PositionManager,
    evolver: Evolver,
    advisor: MarketAdvisor | None,
    screener: ScreenerScheduler,
    db,
) -> None:
    logger.info("Shutdown signal received")
    evolver.stop()
    screener.stop()
    if advisor:
        advisor.stop()
    position_mgr.stop()
    await collector.stop()
    if db:
        await db.close()
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
