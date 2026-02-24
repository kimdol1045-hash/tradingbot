"""
Hyperliquid WebSocket client — real-time candle, orderbook, asset context subscriptions.
Uses hyperliquid-python-sdk's built-in WebSocketManager.
Wraps callbacks into asyncio-compatible bridge via queue.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from hyperliquid.info import Info
from hyperliquid.utils import constants

from src.utils.config import ALL_SYMBOLS, IS_TESTNET, TIMEFRAMES

logger = logging.getLogger(__name__)


class HyperliquidWS:
    """
    Manages WebSocket subscriptions to Hyperliquid.

    The SDK's WS runs in a background thread; we bridge events to asyncio
    via asyncio.Queue so the collector can await them.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None):
        base_url = constants.TESTNET_API_URL if IS_TESTNET else constants.MAINNET_API_URL
        self._info = Info(base_url, skip_ws=False)
        self._loop = loop or asyncio.get_event_loop()
        self._sub_ids: list[tuple[dict, int]] = []
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._running = False

    @property
    def queue(self) -> asyncio.Queue[dict]:
        return self._queue

    @property
    def info(self) -> Info:
        """Expose the Info client for REST calls."""
        return self._info

    def _enqueue(self, event_type: str, data: Any) -> None:
        """Thread-safe bridge: SDK callback → asyncio queue."""
        msg = {"type": event_type, "data": data}
        self._loop.call_soon_threadsafe(self._queue.put_nowait, msg)

    def subscribe_candles(self, symbols: list[str] | None = None) -> None:
        """Subscribe to candle updates for all symbols × base timeframe (5m)."""
        symbols = symbols or ALL_SYMBOLS
        for symbol in symbols:
            sub = {"type": "candle", "coin": symbol, "interval": "5m"}
            sid = self._info.subscribe(
                sub, lambda msg, s=symbol: self._enqueue("candle", msg)
            )
            self._sub_ids.append((sub, sid))
            logger.debug("Subscribed candle: %s 5m (sid=%d)", symbol, sid)

    def subscribe_asset_contexts(self, symbols: list[str] | None = None) -> None:
        """Subscribe to activeAssetCtx for funding/OI/mark price updates."""
        symbols = symbols or ALL_SYMBOLS
        for symbol in symbols:
            sub = {"type": "activeAssetCtx", "coin": symbol}
            sid = self._info.subscribe(
                sub, lambda msg, s=symbol: self._enqueue("asset_ctx", msg)
            )
            self._sub_ids.append((sub, sid))
            logger.debug("Subscribed asset context: %s (sid=%d)", symbol, sid)

    def subscribe_trades(self, symbols: list[str] | None = None) -> None:
        """Subscribe to trade stream (for liquidation detection via trade tags)."""
        symbols = symbols or ALL_SYMBOLS
        for symbol in symbols:
            sub = {"type": "trades", "coin": symbol}
            sid = self._info.subscribe(
                sub, lambda msg, s=symbol: self._enqueue("trades", msg)
            )
            self._sub_ids.append((sub, sid))
            logger.debug("Subscribed trades: %s (sid=%d)", symbol, sid)

    def subscribe_all(self, symbols: list[str] | None = None) -> None:
        """Subscribe to all data feeds needed for the pipeline."""
        self.subscribe_candles(symbols)
        self.subscribe_asset_contexts(symbols)
        logger.info(
            "All subscriptions active: %d symbols, %d total subs",
            len(symbols or ALL_SYMBOLS), len(self._sub_ids),
        )
        self._running = True

    def unsubscribe_symbols(self, symbols: list[str]) -> None:
        """Unsubscribe specific symbols from all feeds."""
        remove_set = set(symbols)
        remaining = []
        removed = 0
        for sub, sid in self._sub_ids:
            coin = sub.get("coin", "")
            if coin in remove_set:
                try:
                    self._info.unsubscribe(sub, sid)
                    removed += 1
                except Exception:
                    pass
            else:
                remaining.append((sub, sid))
        self._sub_ids = remaining
        logger.info("Unsubscribed %d subs for %d symbols: %s", removed, len(symbols), symbols)

    def unsubscribe_all(self) -> None:
        """Clean up all subscriptions."""
        for sub, sid in self._sub_ids:
            try:
                self._info.unsubscribe(sub, sid)
            except Exception:
                pass
        self._sub_ids.clear()
        self._running = False
        logger.info("All subscriptions removed")

    def disconnect(self) -> None:
        """Disconnect the WebSocket."""
        self.unsubscribe_all()
        try:
            self._info.disconnect_websocket()
        except Exception:
            pass
        logger.info("WebSocket disconnected")

    @property
    def is_running(self) -> bool:
        return self._running
