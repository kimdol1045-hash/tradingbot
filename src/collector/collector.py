"""
Data Collector — receives 5m candles via WebSocket, aggregates to 15m/1h/4h,
fetches auxiliary data (funding, OI, orderbook), stores to SQLite.
Includes in-memory candle cache for fast pipeline access.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

import aiosqlite

from src.collector.ws_client import HyperliquidWS
from src.exchange.hyperliquid import fetch_asset_contexts, fetch_candles, fetch_orderbook
from src.utils.config import ALL_SYMBOLS, TIMEFRAME_MINUTES
from src.utils.db import init_db, insert_candle, insert_candles_batch

logger = logging.getLogger(__name__)

# ═══ Candle Cache ═══

CACHE_MAX_SIZE = 200  # candles per symbol×timeframe


class CandleCache:
    """In-memory candle cache: symbol×timeframe → deque of dicts."""

    def __init__(self, max_size: int = CACHE_MAX_SIZE):
        self._max_size = max_size
        self._data: dict[str, list[dict]] = defaultdict(list)

    def _key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}:{timeframe}"

    def append(self, symbol: str, timeframe: str, candle: dict) -> None:
        key = self._key(symbol, timeframe)
        buf = self._data[key]
        if buf and buf[-1]["timestamp"] == candle["timestamp"]:
            buf[-1] = candle  # update existing
        else:
            buf.append(candle)
        if len(buf) > self._max_size:
            self._data[key] = buf[-self._max_size :]

    def get(self, symbol: str, timeframe: str, limit: int = 200) -> list[dict]:
        key = self._key(symbol, timeframe)
        return self._data[key][-limit:]

    def latest(self, symbol: str, timeframe: str) -> dict | None:
        key = self._key(symbol, timeframe)
        buf = self._data[key]
        return buf[-1] if buf else None


# ═══ Timeframe Aggregation ═══

class TFAggregator:
    """
    Aggregates 5m candles into higher timeframes.
    Tracks partial bars and emits completed bars.
    """

    def __init__(self):
        # {(symbol, timeframe): partial_candle}
        self._partials: dict[tuple[str, str], dict] = {}
        self._target_tfs = ["15m", "1h", "4h"]

    def _aligned_ts(self, ts_ms: int, tf_minutes: int) -> int:
        """Round down timestamp to TF boundary."""
        period_ms = tf_minutes * 60 * 1000
        return (ts_ms // period_ms) * period_ms

    def on_5m_candle(self, candle: dict) -> list[dict]:
        """
        Process a 5m candle and return list of completed higher-TF candles.
        Data aggregation rules:
          funding_rate    = last value
          open_interest   = last value (close snapshot)
          liquidation_vol = sum
          bid_ask_spread  = mean
          orderbook_imbalance = mean
        """
        completed = []
        symbol = candle["symbol"]
        ts = candle["timestamp"]

        for tf in self._target_tfs:
            tf_min = TIMEFRAME_MINUTES[tf]
            aligned = self._aligned_ts(ts, tf_min)
            key = (symbol, tf)
            partial = self._partials.get(key)

            if partial and partial["timestamp"] == aligned:
                # Update existing partial bar
                partial["high"] = max(partial["high"], candle["high"])
                partial["low"] = min(partial["low"], candle["low"])
                partial["close"] = candle["close"]
                partial["volume"] += candle["volume"]
                partial["_count"] += 1

                # Aux data aggregation
                if candle.get("funding_rate") is not None:
                    partial["funding_rate"] = candle["funding_rate"]  # last
                if candle.get("open_interest") is not None:
                    partial["open_interest"] = candle["open_interest"]  # last
                if candle.get("liquidation_vol") is not None:
                    partial["liquidation_vol"] = (
                        (partial.get("liquidation_vol") or 0) + candle["liquidation_vol"]
                    )
                if candle.get("bid_ask_spread") is not None:
                    partial["_spread_sum"] = (
                        partial.get("_spread_sum", 0) + candle["bid_ask_spread"]
                    )
                    partial["bid_ask_spread"] = partial["_spread_sum"] / partial["_count"]
                if candle.get("orderbook_imbalance") is not None:
                    partial["_imb_sum"] = (
                        partial.get("_imb_sum", 0) + candle["orderbook_imbalance"]
                    )
                    partial["orderbook_imbalance"] = partial["_imb_sum"] / partial["_count"]

                # Check if bar is complete
                expected = tf_min // 5
                if partial["_count"] >= expected:
                    out = {k: v for k, v in partial.items() if not k.startswith("_")}
                    completed.append(out)
                    del self._partials[key]
            else:
                # Start new partial bar (emit previous if exists)
                if partial:
                    out = {k: v for k, v in partial.items() if not k.startswith("_")}
                    completed.append(out)

                self._partials[key] = {
                    "symbol": symbol,
                    "timeframe": tf,
                    "timestamp": aligned,
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle["volume"],
                    "funding_rate": candle.get("funding_rate"),
                    "open_interest": candle.get("open_interest"),
                    "liquidation_vol": candle.get("liquidation_vol"),
                    "bid_ask_spread": candle.get("bid_ask_spread"),
                    "orderbook_imbalance": candle.get("orderbook_imbalance"),
                    "_count": 1,
                    "_spread_sum": candle.get("bid_ask_spread") or 0,
                    "_imb_sum": candle.get("orderbook_imbalance") or 0,
                }

        return completed


# ═══ Main Collector ═══

class DataCollector:
    """
    Main data collection orchestrator.
    - WebSocket for real-time 5m candles
    - REST polling for orderbook/funding/OI enrichment
    - TF aggregation (5m → 15m/1h/4h)
    - SQLite storage + in-memory cache
    """

    def __init__(self):
        self._ws: HyperliquidWS | None = None
        self._db: aiosqlite.Connection | None = None
        self.cache = CandleCache()
        self._aggregator = TFAggregator()
        self._running = False
        self._aux_data: dict[str, dict] = {}  # symbol → latest asset context
        self.on_candle_close_callback = None  # (symbol, timeframe) → called on candle close

    async def start(self, symbols: list[str] | None = None) -> None:
        """Initialize DB and start WebSocket subscriptions."""
        symbols = symbols or ALL_SYMBOLS
        self._db = await init_db()

        loop = asyncio.get_event_loop()
        self._ws = HyperliquidWS(loop=loop)
        self._ws.subscribe_all(symbols)
        self._running = True

        logger.info("DataCollector started: %d symbols", len(symbols))

    async def stop(self) -> None:
        """Clean shutdown."""
        self._running = False
        if self._ws:
            self._ws.disconnect()
        if self._db:
            await self._db.close()
        logger.info("DataCollector stopped")

    async def run_forever(self) -> None:
        """Main loop: process WS events + periodic REST enrichment."""
        if not self._ws or not self._db:
            raise RuntimeError("Call start() first")

        # Launch background tasks
        tasks = [
            asyncio.create_task(self._process_ws_events()),
            asyncio.create_task(self._poll_aux_data()),
            asyncio.create_task(self._wal_checkpoint()),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()

    async def _process_ws_events(self) -> None:
        """Process events from WebSocket queue."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self._ws.queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            event_type = msg["type"]
            data = msg["data"]

            try:
                if event_type == "candle":
                    await self._handle_candle(data)
                elif event_type == "asset_ctx":
                    self._handle_asset_ctx(data)
            except Exception:
                logger.exception("Error processing WS event: %s", event_type)

    async def _handle_candle(self, data: dict) -> None:
        """Process a 5m candle update from WS."""
        # SDK candle format: data has "data" key with candle info
        candle_data = data.get("data", data)
        if isinstance(candle_data, list):
            for c in candle_data:
                await self._process_single_candle(c)
        else:
            await self._process_single_candle(candle_data)

    async def _process_single_candle(self, raw: dict) -> None:
        """Process a single raw candle dict from WS."""
        symbol = raw.get("s", "")
        candle = {
            "symbol": symbol,
            "timeframe": "5m",
            "timestamp": int(raw.get("t", 0)),
            "open": float(raw.get("o", 0)),
            "high": float(raw.get("h", 0)),
            "low": float(raw.get("l", 0)),
            "close": float(raw.get("c", 0)),
            "volume": float(raw.get("v", 0)),
        }

        # Enrich with latest auxiliary data
        aux = self._aux_data.get(symbol, {})
        candle["funding_rate"] = aux.get("funding_rate")
        candle["open_interest"] = aux.get("open_interest")

        # Store 5m candle
        self.cache.append(symbol, "5m", candle)
        await insert_candle(self._db, candle)
        await self._db.commit()

        # Aggregate to higher timeframes
        completed = self._aggregator.on_5m_candle(candle)
        for htf_candle in completed:
            self.cache.append(htf_candle["symbol"], htf_candle["timeframe"], htf_candle)
            await insert_candle(self._db, htf_candle)
        if completed:
            await self._db.commit()
            for c in completed:
                logger.debug("Aggregated: %s %s @ %d", c["symbol"], c["timeframe"], c["timestamp"])

        # Trigger pipeline on 5m candle close
        if self.on_candle_close_callback:
            try:
                self.on_candle_close_callback(symbol, "5m")
            except Exception:
                logger.exception("Pipeline callback error for %s", symbol)

    def _handle_asset_ctx(self, data: dict) -> None:
        """Update cached auxiliary data from activeAssetCtx subscription."""
        ctx = data.get("data", data)
        if isinstance(ctx, dict) and "coin" in ctx:
            symbol = ctx["coin"]
            self._aux_data[symbol] = {
                "funding_rate": float(ctx.get("funding", 0)),
                "open_interest": float(ctx.get("openInterest", 0)),
                "mark_price": float(ctx.get("markPx", 0)),
            }

    async def _poll_aux_data(self) -> None:
        """Periodically fetch orderbook data via REST (every 30s)."""
        while self._running:
            try:
                info = self._ws.info
                # Fetch asset contexts for all symbols
                try:
                    asset_ctxs = fetch_asset_contexts(info)
                    self._aux_data.update(asset_ctxs)
                except Exception:
                    logger.exception("Failed to fetch asset contexts")

                # Fetch orderbook for enrichment
                for symbol in ALL_SYMBOLS:
                    try:
                        ob = fetch_orderbook(info, symbol)
                        if symbol in self._aux_data:
                            self._aux_data[symbol]["bid_ask_spread"] = ob["spread_bps"]
                            self._aux_data[symbol]["orderbook_imbalance"] = ob["imbalance"]
                        else:
                            self._aux_data[symbol] = {
                                "bid_ask_spread": ob["spread_bps"],
                                "orderbook_imbalance": ob["imbalance"],
                            }
                    except Exception:
                        logger.debug("Failed to fetch orderbook for %s", symbol)

            except Exception:
                logger.exception("Error in aux data polling")

            await asyncio.sleep(30)

    async def _wal_checkpoint(self) -> None:
        """Run WAL checkpoint every hour (TRUNCATE mode)."""
        while self._running:
            await asyncio.sleep(3600)
            try:
                await self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                logger.info("WAL checkpoint completed")
            except Exception:
                logger.exception("WAL checkpoint failed")

    async def backfill(
        self,
        symbols: list[str] | None = None,
        days: int | None = None,
    ) -> None:
        """
        Backfill historical candles via REST.
        Each TF uses its own lookback window matching market_stats requirements:
          5m: 7d, 15m: 14d, 1h: 30d, 4h: 90d
        """
        symbols = symbols or ALL_SYMBOLS
        info = self._ws.info if self._ws else None
        if not info:
            from src.exchange.hyperliquid import get_info_client
            info = get_info_client()

        # TF-specific lookback days (matches MarketStats TF_LOOKBACK_DAYS)
        tf_days = {"5m": 7, "15m": 14, "1h": 30, "4h": 90}
        if days is not None:
            # Override all TFs with the same value if explicitly set
            tf_days = {tf: days for tf in tf_days}

        end_ms = int(time.time() * 1000)

        for symbol in symbols:
            for tf, lookback in tf_days.items():
                start_ms = end_ms - (lookback * 24 * 60 * 60 * 1000)
                try:
                    candles = fetch_candles(info, symbol, tf, start_ms, end_ms)
                    if candles:
                        await insert_candles_batch(self._db, candles)
                        for c in candles:
                            self.cache.append(symbol, tf, c)
                        logger.info(
                            "Backfilled %s %s: %d candles (%dd)",
                            symbol, tf, len(candles), lookback,
                        )
                except Exception:
                    logger.exception("Backfill failed: %s %s", symbol, tf)
