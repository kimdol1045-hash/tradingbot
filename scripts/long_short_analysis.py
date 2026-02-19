"""Quick long/short breakdown analysis from backtest results."""
import asyncio
import logging

from src.backtest.engine import BacktestEngine
from src.backtest.__main__ import load_candles_from_db

async def main():
    logging.basicConfig(level=logging.WARNING)

    configs = [
        ("BTC", ["s1", "s2", "s3", "s4"]),
        ("ETH", ["s1", "s2", "s3", "s4"]),
        ("SOL", ["s2", "s3", "s4"]),
        ("XRP", ["s2", "s3", "s4"]),
    ]

    all_rows = []

    for symbol, agents in configs:
        candles_by_tf = await load_candles_from_db(symbol, ["5m", "15m", "1h", "4h"], 1095)
        engine = BacktestEngine(candles_by_tf, symbol, warmup=200, scan_every=6)
        results = engine.run(agent_ids=agents, capital_per_agent=10000.0)

        for agent_id, result in results.items():
            longs = [p for p in result.positions if p.signal.direction == "LONG"]
            shorts = [p for p in result.positions if p.signal.direction == "SHORT"]

            long_wins = [p for p in longs if p.net_pnl > 0]
            long_losses = [p for p in longs if p.net_pnl <= 0]
            short_wins = [p for p in shorts if p.net_pnl > 0]
            short_losses = [p for p in shorts if p.net_pnl <= 0]

            long_pnl = sum(p.net_pnl for p in longs)
            short_pnl = sum(p.net_pnl for p in shorts)

            all_rows.append({
                "symbol": symbol,
                "agent": agent_id.upper(),
                "total": len(result.positions),
                "longs": len(longs),
                "long_w": len(long_wins),
                "long_l": len(long_losses),
                "long_wr": f"{len(long_wins)/len(longs)*100:.0f}%" if longs else "-",
                "long_pnl": long_pnl,
                "shorts": len(shorts),
                "short_w": len(short_wins),
                "short_l": len(short_losses),
                "short_wr": f"{len(short_wins)/len(shorts)*100:.0f}%" if shorts else "-",
                "short_pnl": short_pnl,
            })

    # Print table
    print()
    print("=" * 110)
    print("  LONG / SHORT BREAKDOWN (3-Year Backtest, $10K capital)")
    print("=" * 110)

    for symbol in ["BTC", "ETH", "SOL", "XRP"]:
        rows = [r for r in all_rows if r["symbol"] == symbol]
        if not rows:
            continue

        print(f"\n── {symbol} {'─' * 100}")
        print(f"  {'Agent':<6} {'Total':>5}  │  {'LONG':>4} {'W':>3}/{'L':>3} {'WR':>5} {'PnL':>16}  │  {'SHORT':>5} {'W':>3}/{'L':>3} {'WR':>5} {'PnL':>16}  │  {'L/S Ratio':>9}")
        print(f"  {'─'*6} {'─'*5}  │  {'─'*4} {'─'*3} {'─'*3} {'─'*5} {'─'*16}  │  {'─'*5} {'─'*3} {'─'*3} {'─'*5} {'─'*16}  │  {'─'*9}")

        for r in rows:
            total = r["total"]
            if total == 0:
                print(f"  {r['agent']:<6} {0:>5}  │  No trades")
                continue

            ls_ratio = f"{r['longs']}/{r['shorts']}" if r['shorts'] > 0 else f"{r['longs']}/0"
            print(
                f"  {r['agent']:<6} {total:>5}  │  "
                f"{r['longs']:>4} {r['long_w']:>3}/{r['long_l']:>3} {r['long_wr']:>5} {r['long_pnl']:>14,.0f}$  │  "
                f"{r['shorts']:>5} {r['short_w']:>3}/{r['short_l']:>3} {r['short_wr']:>5} {r['short_pnl']:>14,.0f}$  │  "
                f"{ls_ratio:>9}"
            )

    # Summary
    total_longs = sum(r["longs"] for r in all_rows)
    total_shorts = sum(r["shorts"] for r in all_rows)
    total_long_pnl = sum(r["long_pnl"] for r in all_rows)
    total_short_pnl = sum(r["short_pnl"] for r in all_rows)
    long_wins_total = sum(r["long_w"] for r in all_rows)
    short_wins_total = sum(r["short_w"] for r in all_rows)

    print(f"\n{'=' * 110}")
    print(f"  TOTAL: {total_longs + total_shorts} trades = {total_longs} LONG + {total_shorts} SHORT")
    print(f"  LONG  → {long_wins_total}W/{total_longs - long_wins_total}L  PnL: ${total_long_pnl:,.0f}")
    print(f"  SHORT → {short_wins_total}W/{total_shorts - short_wins_total}L  PnL: ${total_short_pnl:,.0f}")
    print(f"{'=' * 110}")


if __name__ == "__main__":
    asyncio.run(main())
