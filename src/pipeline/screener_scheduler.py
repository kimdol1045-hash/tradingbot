"""
Screener Scheduler — runs coin_screener periodically and reloads symbol pool.

Default: every 72 hours at UTC 03:00 (KST 12:00).
Configurable via SCREENER_INTERVAL_HOURS and SCREENER_START_HOUR_UTC env vars.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from src.notify.telegram import notify_system
from src.utils.config import (
    PROJECT_ROOT,
    SCREENER_INTERVAL_HOURS,
    SCREENER_MIN_VOLUME_M,
    SCREENER_START_HOUR_UTC,
    reload_symbol_pool,
)

logger = logging.getLogger(__name__)


class ScreenerScheduler:
    """Periodically runs the coin screener and updates the symbol pool."""

    _STATE_FILE = PROJECT_ROOT / ".screener_last_run"

    def __init__(
        self,
        interval_hours: int = SCREENER_INTERVAL_HOURS,
        start_hour_utc: int = SCREENER_START_HOUR_UTC,
        min_volume_m: float = SCREENER_MIN_VOLUME_M,
        collector=None,
    ):
        self.interval_hours = interval_hours
        self.start_hour_utc = start_hour_utc
        self.min_volume_m = min_volume_m
        self.collector = collector
        self._running = True
        self._last_run_ts: float = self._load_last_run_ts()
        self._output_path = str(PROJECT_ROOT / "symbols.json")

    def stop(self):
        self._running = False

    def _load_last_run_ts(self) -> float:
        try:
            return float(self._STATE_FILE.read_text().strip())
        except (FileNotFoundError, ValueError):
            return 0

    def _save_last_run_ts(self):
        try:
            self._STATE_FILE.write_text(str(self._last_run_ts))
        except OSError:
            logger.warning("Failed to persist screener last_run_ts")

    async def run_loop(self):
        """Main scheduler loop. Checks every 5 minutes if it's time to run."""
        logger.info(
            "Screener scheduler started (interval=%dh, start_hour=UTC %02d:00, min_vol=$%.0fM)",
            self.interval_hours,
            self.start_hour_utc,
            self.min_volume_m,
        )

        # Wait 2 minutes on startup to let the bot stabilize
        await asyncio.sleep(120)

        while self._running:
            try:
                if self._should_run():
                    await self._run_screening()
            except Exception:
                logger.exception("Screener scheduler error")
                try:
                    await notify_system("⚠️ 스크리너 스케줄러 오류 발생 — 다음 주기에 재시도")
                except Exception:
                    pass

            # Check every 5 minutes
            await asyncio.sleep(300)

    def _should_run(self) -> bool:
        """Determine if it's time to run the screener."""
        now = datetime.now(timezone.utc)

        # Check if current hour matches start hour
        if now.hour != self.start_hour_utc:
            return False

        # First run ever: go ahead
        if self._last_run_ts == 0:
            return True

        # Check if enough time has elapsed since last run
        elapsed_hours = (time.time() - self._last_run_ts) / 3600
        return elapsed_hours >= self.interval_hours - 1  # 1h grace for timing

    async def _run_screening(self):
        """Execute the full screening pipeline."""
        logger.info("━━━ Screener scheduler: starting scheduled screening ━━━")

        try:
            await notify_system("🔍 스크리너 자동 실행 시작")
        except Exception:
            pass

        t_start = time.perf_counter()

        # Import screener components (lazy to avoid circular imports)
        from scripts.coin_screener import (
            stage1_filter,
            stage2_backtest,
            stage3_classify,
            print_results,
            write_symbols_json,
            send_telegram_summary,
        )
        from src.exchange.hyperliquid import fetch_asset_contexts, get_info_client

        # Stage 1: Volume filter
        logger.info("Screener Stage 1: Fetching asset contexts...")
        info = get_info_client()
        contexts = fetch_asset_contexts(info)
        total_assets = len(contexts)

        min_vol = self.min_volume_m * 1_000_000
        candidates = stage1_filter(contexts, min_vol)
        logger.info(
            "Screener Stage 1: %d → %d candidates (vol $%.0fM+)",
            total_assets,
            len(candidates),
            self.min_volume_m,
        )

        if not candidates:
            logger.warning("Screener: no candidates passed filter")
            return

        # Stage 2: Full pipeline backtest
        logger.info("Screener Stage 2: backtesting %d coins...", len(candidates))
        backtest_results = await stage2_backtest(candidates)

        if not backtest_results:
            logger.warning("Screener: no backtest results")
            self._last_run_ts = time.time()
            self._save_last_run_ts()
            try:
                await notify_system("⚠️ 스크리너 완료: 백테스트 결과 없음 (다음 주기 재시도)")
            except Exception:
                pass
            return

        # Stage 3: Tier classification
        classified = stage3_classify(backtest_results)
        print_results(classified, total_assets, len(candidates))
        write_symbols_json(classified, self._output_path)

        # Reload symbol pool and detect new symbols
        from src.utils.config import ALL_SYMBOLS as old_all
        old_set = set(old_all)
        reload_symbol_pool()
        from src.utils.config import ALL_SYMBOLS as new_all
        new_set = set(new_all)
        added = list(new_set - old_set)
        logger.info("Symbol pool updated: %d symbols across %d tiers", len(new_all), len(reload_symbol_pool()))

        # Unsubscribe removed symbols
        removed = list(old_set - new_set)
        if removed and self.collector:
            logger.info("Removed symbols to unsubscribe: %s", removed)
            await self.collector.remove_symbols(removed)

        # Subscribe collector to any new symbols
        if added and self.collector:
            logger.info("New symbols to subscribe: %s", added)
            await self.collector.add_symbols(added)

        # Send Telegram summary
        elapsed_min = (time.perf_counter() - t_start) / 60
        await send_telegram_summary(classified, contexts, total_assets, len(candidates), elapsed_min)

        # Mark run time AFTER successful completion
        self._last_run_ts = time.time()
        self._save_last_run_ts()
        logger.info("━━━ Screener scheduler: completed in %.1f min ━━━", elapsed_min)
