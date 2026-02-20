"""
Historical Data Loader — bulk download of long-term candle data.
Supports Hyperliquid (native) + Binance Futures (for pre-Hyperliquid history).
Chunked requests to handle API limits.

Usage:
    python -m src.collector.historical --years 5 --source binance
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from src.utils.config import ALL_SYMBOLS, TIMEFRAME_MINUTES
from src.utils.db import init_db, insert_candles_batch

logger = logging.getLogger(__name__)

# ═══ Binance Futures Data Source ═══

BINANCE_FUTURES_URL = "https://fapi.binance.com/fapi/v1/klines"

# Binance symbol mapping (special cases only; default is {SYM}USDT)
SYMBOL_MAP_BINANCE = {
    "1000PEPE": "1000PEPEUSDT",
    "1000SHIB": "1000SHIBUSDT",
    "1000BONK": "1000BONKUSDT",
    "1000FLOKI": "1000FLOKIUSDT",
}


def _to_binance_symbol(symbol: str) -> str:
    """Convert Hyperliquid symbol to Binance Futures symbol."""
    return SYMBOL_MAP_BINANCE.get(symbol, f"{symbol}USDT")

# Binance TF mapping
TF_MAP_BINANCE = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}

# Max candles per Binance request
BINANCE_LIMIT = 1500


async def fetch_binance_candles(
    symbol: str, timeframe: str, start_ms: int, end_ms: int,
) -> list[dict]:
    """Fetch candles from Binance Futures in chunks. Auto-maps symbol to {SYM}USDT."""
    binance_symbol = _to_binance_symbol(symbol)
    binance_tf = TF_MAP_BINANCE.get(timeframe, timeframe)
    tf_ms = TIMEFRAME_MINUTES.get(timeframe, 5) * 60 * 1000
    all_candles: list[dict] = []

    current_start = start_ms

    async with httpx.AsyncClient(timeout=30) as client:
        while current_start < end_ms:
            params = {
                "symbol": binance_symbol,
                "interval": binance_tf,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": BINANCE_LIMIT,
            }

            try:
                resp = await client.get(BINANCE_FUTURES_URL, params=params)

                # Rate limit handling: back off and retry
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", "60"))
                    logger.warning("Binance rate limit 429, waiting %ds...", wait)
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code == 400:
                    # Invalid symbol — coin doesn't exist on Binance
                    logger.debug("Binance: %s not found (400)", binance_symbol)
                    return []

                if resp.status_code != 200:
                    logger.error("Binance API error %s: %d", binance_symbol, resp.status_code)
                    break

                data = resp.json()
                if not data:
                    break

                for k in data:
                    candle = {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "timestamp": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    }
                    all_candles.append(candle)

                # Move to next chunk
                last_ts = int(data[-1][0])
                current_start = last_ts + tf_ms

                logger.debug(
                    "Binance %s %s: fetched %d candles (total: %d)",
                    symbol, timeframe, len(data), len(all_candles),
                )

                # Rate limit: stay well under 1200 req/min
                await asyncio.sleep(0.3)

            except Exception:
                logger.exception("Binance fetch error: %s %s", symbol, timeframe)
                await asyncio.sleep(2)
                current_start += tf_ms * BINANCE_LIMIT  # Skip chunk on error

    return all_candles


# ═══ Hyperliquid Historical (chunked) ═══

async def fetch_hyperliquid_candles_chunked(
    symbol: str, timeframe: str, start_ms: int, end_ms: int,
    info=None,
) -> list[dict]:
    """Fetch candles from Hyperliquid REST API in chunks.

    Args:
        info: Optional pre-created Info client. Creates one if not provided.
    """
    from src.exchange.hyperliquid import fetch_candles, get_info_client

    if info is None:
        info = get_info_client()
    tf_ms = TIMEFRAME_MINUTES.get(timeframe, 5) * 60 * 1000
    # Larger chunks = fewer API calls. HL API can return 5000+ candles per request.
    chunk_size = 5000
    chunk_ms = chunk_size * tf_ms
    all_candles: list[dict] = []

    current_start = start_ms
    max_retries = 8

    while current_start < end_ms:
        chunk_end = min(current_start + chunk_ms, end_ms)
        retries = 0
        success = False

        while retries < max_retries and not success:
            try:
                # Run synchronous fetch_candles in a thread with 30s timeout
                candles = await asyncio.wait_for(
                    asyncio.to_thread(fetch_candles, info, symbol, timeframe, current_start, chunk_end),
                    timeout=30,
                )
                if candles:
                    all_candles.extend(candles)
                    logger.debug(
                        "HL %s %s: fetched %d candles (total: %d)",
                        symbol, timeframe, len(candles), len(all_candles),
                    )
                success = True
                await asyncio.sleep(1.0)  # Rate limit — 1 req/s is safe
            except asyncio.TimeoutError:
                retries += 1
                logger.warning(
                    "HL %s %s: timeout (30s), retry %d/%d",
                    symbol, timeframe, retries, max_retries,
                )
                await asyncio.sleep(2)
            except Exception as e:
                retries += 1
                is_429 = "429" in str(e)
                wait = min(2 ** retries * 2, 60) if is_429 else 2
                if retries < max_retries:
                    logger.warning(
                        "HL %s %s: retry %d/%d (wait %ds): %s",
                        symbol, timeframe, retries, max_retries, wait,
                        str(e)[:100],
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("HL %s %s: giving up after %d retries", symbol, timeframe, max_retries)

        current_start = chunk_end

    return all_candles


# ═══ Main Loader ═══

async def load_historical(
    symbols: list[str] | None = None,
    years: int = 3,
    source: str = "hyperliquid",
    timeframes: list[str] | None = None,
) -> dict[str, int]:
    """
    Download and store historical candle data.

    Args:
        symbols: List of symbols (default: ALL_SYMBOLS)
        years: Years of history (default: 3, Hyperliquid max ~2-3 years)
        source: 'hyperliquid' (primary) or 'binance' (for older data)
        timeframes: List of timeframes (default: all)

    Returns:
        dict of {symbol_tf: candle_count}
    """
    symbols = symbols or ALL_SYMBOLS
    timeframes = timeframes or ["5m", "15m", "1h", "4h"]

    db = await init_db()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (years * 365 * 24 * 60 * 60 * 1000)

    results: dict[str, int] = {}

    for symbol in symbols:
        for tf in timeframes:
            key = f"{symbol}_{tf}"
            logger.info("Loading %s %s (%d years from %s)...", symbol, tf, years, source)

            if source == "binance":
                candles = await fetch_binance_candles(symbol, tf, start_ms, end_ms)
            elif source == "hyperliquid":
                candles = await fetch_hyperliquid_candles_chunked(symbol, tf, start_ms, end_ms)
            else:
                logger.error("Unknown source: %s", source)
                continue

            if candles:
                # Batch insert (chunk to avoid memory issues)
                batch_size = 5000
                for i in range(0, len(candles), batch_size):
                    batch = candles[i : i + batch_size]
                    await insert_candles_batch(db, batch)
                await db.commit()
                results[key] = len(candles)
                logger.info("Loaded %s %s: %d candles", symbol, tf, len(candles))
            else:
                results[key] = 0
                logger.warning("No data for %s %s", symbol, tf)

            # Pause between TF downloads to avoid rate limits
            await asyncio.sleep(2)

    await db.close()
    return results


# ═══ CLI Entry Point ═══

async def _cli_main():
    import argparse

    parser = argparse.ArgumentParser(description="Load historical candle data")
    parser.add_argument("--years", type=int, default=5, help="Years of history")
    parser.add_argument("--source", choices=["binance", "hyperliquid"], default="binance")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--timeframes", nargs="+", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-24s | %(levelname)-5s | %(message)s",
    )

    results = await load_historical(
        symbols=args.symbols,
        years=args.years,
        source=args.source,
        timeframes=args.timeframes,
    )

    total = sum(results.values())
    print(f"\nTotal candles loaded: {total:,}")
    for key, count in results.items():
        print(f"  {key}: {count:,}")


if __name__ == "__main__":
    asyncio.run(_cli_main())
