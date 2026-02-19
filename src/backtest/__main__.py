"""
Backtest CLI — run backtests from command line.

Usage:
    # From DB (requires historical data loaded)
    python -m src.backtest --symbol BTC --agents s3 s4 --days 90

    # All agents, all tier-1 symbols
    python -m src.backtest --symbol BTC ETH --agents s1 s2 s3 s4 --days 60
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from src.backtest.engine import BacktestEngine
from src.backtest.metrics import calculate_metrics
from src.backtest.report import format_comparison, format_report
from src.utils.config import TIMEFRAME_MINUTES
from src.utils.db import get_candles, init_db


async def load_candles_from_db(
    symbol: str,
    timeframes: list[str],
    days: int,
) -> dict[str, list[dict]]:
    """Load candle data from SQLite database."""
    db = await init_db()

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)

    candles_by_tf: dict[str, list[dict]] = {}

    for tf in timeframes:
        # Calculate how many candles we need
        tf_minutes = TIMEFRAME_MINUTES.get(tf, 5)
        max_candles = (days * 24 * 60) // tf_minutes + 100  # extra for safety

        cursor = await db.execute(
            """
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume,
                   funding_rate, open_interest, liquidation_vol,
                   bid_ask_spread, orderbook_imbalance
            FROM candles
            WHERE symbol = ? AND timeframe = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (symbol, tf, start_ms),
        )
        rows = await cursor.fetchall()
        columns = [
            "symbol", "timeframe", "timestamp", "open", "high", "low", "close", "volume",
            "funding_rate", "open_interest", "liquidation_vol",
            "bid_ask_spread", "orderbook_imbalance",
        ]
        candles_by_tf[tf] = [dict(zip(columns, row)) for row in rows]
        logging.info("Loaded %s %s: %d candles", symbol, tf, len(candles_by_tf[tf]))

    await db.close()
    return candles_by_tf


def generate_synthetic_candles(
    symbol: str,
    days: int = 30,
    base_price: float = 50000.0,
) -> dict[str, list[dict]]:
    """Generate synthetic candle data for testing (no DB required)."""
    import random
    random.seed(42)

    candles_by_tf: dict[str, list[dict]] = {}

    for tf in ["5m", "15m", "1h", "4h"]:
        tf_minutes = TIMEFRAME_MINUTES[tf]
        tf_ms = tf_minutes * 60 * 1000
        num_candles = (days * 24 * 60) // tf_minutes

        start_ts = int(time.time() * 1000) - (days * 86400 * 1000)
        candles = []
        price = base_price

        for i in range(num_candles):
            ts = start_ts + i * tf_ms

            # Random walk with slight upward drift and volatility clusters
            volatility = 0.002 + 0.003 * abs(random.gauss(0, 1))
            drift = 0.00001 * tf_minutes  # slight upward bias
            change = random.gauss(drift, volatility)

            open_p = price
            close_p = price * (1 + change)
            high_p = max(open_p, close_p) * (1 + abs(random.gauss(0, 0.001)))
            low_p = min(open_p, close_p) * (1 - abs(random.gauss(0, 0.001)))
            volume = random.uniform(100, 10000) * (base_price / 50000)

            candles.append({
                "symbol": symbol,
                "timeframe": tf,
                "timestamp": ts,
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": round(volume, 2),
                "funding_rate": random.gauss(0.0001, 0.0003),
                "open_interest": random.uniform(1e8, 5e8),
                "liquidation_vol": random.uniform(0, 1e6),
                "bid_ask_spread": random.uniform(0.0001, 0.001),
                "orderbook_imbalance": random.uniform(-0.3, 0.3),
            })

            price = close_p

        candles_by_tf[tf] = candles

    return candles_by_tf


async def main():
    parser = argparse.ArgumentParser(description="Run backtest on historical data")
    parser.add_argument("--symbol", nargs="+", default=["BTC"], help="Symbols to backtest")
    parser.add_argument("--agents", nargs="+", default=["s3"], help="Agent IDs")
    parser.add_argument("--days", type=int, default=60, help="Days of history")
    parser.add_argument("--capital", type=float, default=10000.0, help="Capital per agent")
    parser.add_argument("--warmup", type=int, default=200, help="Warmup candles")
    parser.add_argument("--scan-every", type=int, default=6, help="Run pipeline every N candles (6=30m)")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic data (no DB)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-24s | %(levelname)-5s | %(message)s",
    )

    all_metrics = []

    for symbol in args.symbol:
        print(f"\n{'='*60}")
        print(f"  Backtesting {symbol} with agents: {', '.join(args.agents)}")
        print(f"  Period: {args.days} days, Capital: ${args.capital:,.0f}")
        print(f"{'='*60}\n")

        # Load data
        if args.synthetic:
            base = {"BTC": 50000, "ETH": 3000, "SOL": 150, "XRP": 0.6}.get(symbol, 1000)
            candles_by_tf = generate_synthetic_candles(symbol, args.days, base)
            logging.info("Generated synthetic data for %s (%d days)", symbol, args.days)
        else:
            timeframes = ["5m", "15m", "1h", "4h"]
            candles_by_tf = await load_candles_from_db(symbol, timeframes, args.days)

        # Check data
        n5m = len(candles_by_tf.get("5m", []))
        if n5m < args.warmup + 50:
            print(f"  Not enough 5m candles for {symbol}: {n5m} (need {args.warmup + 50}+)")
            continue

        # Run backtest
        engine = BacktestEngine(candles_by_tf, symbol, warmup=args.warmup, scan_every=args.scan_every)
        results = engine.run(agent_ids=args.agents, capital_per_agent=args.capital)

        # Print reports
        for agent_id, result in results.items():
            metrics = calculate_metrics(result, initial_capital=args.capital)
            all_metrics.append(metrics)
            print(format_report(metrics))
            print()

    # Comparison table (if multiple agents/symbols)
    if len(all_metrics) > 1:
        print(format_comparison(all_metrics))

    # Summary
    print(f"\nTotal backtests: {len(all_metrics)}")


if __name__ == "__main__":
    asyncio.run(main())
