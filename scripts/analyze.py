"""
Performance Analysis — post-mortem analysis of trading results from DB.

Usage:
    python scripts/analyze.py                    # All agents, all time
    python scripts/analyze.py --days 30          # Last 30 days
    python scripts/analyze.py --agent s3         # Single agent
    python scripts/analyze.py --patterns         # Pattern win-rate breakdown
    python scripts/analyze.py --correlation      # Agent correlation analysis
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ═══ Per-Agent Metrics ═══

def compute_agent_metrics(conn: sqlite3.Connection, agent_id: str | None, days: int | None) -> list[dict]:
    """Compute performance metrics per agent."""
    where_clauses = ["exit_reason IS NOT NULL"]
    params: list = []

    if agent_id:
        where_clauses.append("agent_id = ?")
        params.append(agent_id)

    if days:
        import time
        cutoff_ms = int((time.time() - days * 86400) * 1000)
        where_clauses.append("entry_time >= ?")
        params.append(cutoff_ms)

    where = " AND ".join(where_clauses)

    cursor = conn.execute(
        f"""
        SELECT agent_id, signal_id, side, entry_time, exit_time,
               entry_price, exit_price, leverage, notional_usd, margin_usd,
               pnl_usd, pnl_pct, regime, inflection_type, inflection_score,
               validation_score, exit_reason
        FROM trades
        WHERE {where}
        ORDER BY agent_id, entry_time
        """,
        params,
    )
    rows = [dict(r) for r in cursor.fetchall()]

    if not rows:
        return []

    # Group by agent
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_agent[r["agent_id"]].append(r)

    results = []
    for aid, trades in sorted(by_agent.items()):
        results.append(_metrics_for_trades(aid, trades))

    return results


def _metrics_for_trades(agent_id: str, trades: list[dict]) -> dict:
    """Calculate metrics for a list of closed trades."""
    n = len(trades)
    pnls = [t["pnl_usd"] or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    gross_profit = sum(wins)
    gross_loss = sum(abs(l) for l in losses)
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    win_rate = len(wins) / n * 100 if n > 0 else 0

    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    avg_rr = avg_win / avg_loss if avg_loss > 0 else float("inf")

    # Max drawdown on equity curve
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    initial_capital = sum(t.get("margin_usd") or 0 for t in trades[:1]) or 10000
    max_dd_pct = max_dd / (initial_capital + peak) * 100 if (initial_capital + peak) > 0 else 0

    # Sharpe & Sortino
    sharpe = sortino = 0.0
    if len(pnls) >= 5:
        avg_r = sum(pnls) / len(pnls)
        std_r = _std(pnls)
        if std_r > 0:
            sharpe = avg_r / std_r * math.sqrt(252)
        down = [p for p in pnls if p < 0]
        down_std = _std(down) if down else 0
        if down_std > 0:
            sortino = avg_r / down_std * math.sqrt(252)

    # Streaks
    max_win_streak = max_loss_streak = 0
    ws = ls = 0
    for p in pnls:
        if p > 0:
            ws += 1; ls = 0
            max_win_streak = max(max_win_streak, ws)
        elif p < 0:
            ls += 1; ws = 0
            max_loss_streak = max(max_loss_streak, ls)
        else:
            ws = ls = 0

    # Hold duration (avg in minutes)
    hold_mins = []
    for t in trades:
        if t["entry_time"] and t["exit_time"]:
            hold_mins.append((t["exit_time"] - t["entry_time"]) / 60000)
    avg_hold = sum(hold_mins) / len(hold_mins) if hold_mins else 0

    # Close reasons
    reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[t["exit_reason"] or "UNKNOWN"] += 1

    return {
        "agent_id": agent_id,
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / n, 2) if n > 0 else 0,
        "profit_factor": round(min(pf, 99.9), 2),
        "avg_rr": round(min(avg_rr, 99.9), 2),
        "max_dd_usd": round(max_dd, 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "avg_hold_min": round(avg_hold, 0),
        "close_reasons": dict(reasons),
    }


# ═══ Pattern Win-Rate Analysis ═══

def pattern_analysis(conn: sqlite3.Connection, days: int | None) -> list[dict]:
    """Analyze win rates by inflection type."""
    where = "exit_reason IS NOT NULL"
    params: list = []
    if days:
        import time
        cutoff_ms = int((time.time() - days * 86400) * 1000)
        where += " AND entry_time >= ?"
        params.append(cutoff_ms)

    cursor = conn.execute(
        f"""
        SELECT inflection_type, side, pnl_usd, inflection_score, validation_score
        FROM trades
        WHERE {where} AND inflection_type IS NOT NULL AND inflection_type != ''
        ORDER BY inflection_type
        """,
        params,
    )
    rows = [dict(r) for r in cursor.fetchall()]

    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[r["inflection_type"]].append(r)

    results = []
    for itype, trades in sorted(by_type.items()):
        n = len(trades)
        wins = sum(1 for t in trades if (t["pnl_usd"] or 0) > 0)
        total_pnl = sum(t["pnl_usd"] or 0 for t in trades)
        avg_score = sum(t["inflection_score"] or 0 for t in trades) / n if n > 0 else 0

        results.append({
            "type": itype,
            "trades": n,
            "wins": wins,
            "win_rate": round(wins / n * 100, 1) if n > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / n, 2) if n > 0 else 0,
            "avg_score": round(avg_score, 1),
        })

    return results


# ═══ Agent Correlation Analysis ═══

def correlation_analysis(conn: sqlite3.Connection, days: int | None) -> dict:
    """Analyze correlation between agents."""
    where = "exit_reason IS NOT NULL"
    params: list = []
    if days:
        import time
        cutoff_ms = int((time.time() - days * 86400) * 1000)
        where += " AND entry_time >= ?"
        params.append(cutoff_ms)

    cursor = conn.execute(
        f"""
        SELECT agent_id, signal_id, symbol, side, entry_time, exit_time, pnl_usd
        FROM trades
        WHERE {where}
        ORDER BY entry_time
        """,
        params,
    )
    rows = [dict(r) for r in cursor.fetchall()]

    if not rows:
        return {"overlap_matrix": {}, "pnl_correlation": {}}

    # Simultaneous position overlap
    agents = sorted(set(r["agent_id"] for r in rows))
    overlap: dict[str, dict[str, int]] = {a: {b: 0 for b in agents} for a in agents}

    for i, t1 in enumerate(rows):
        for t2 in rows[i + 1:]:
            if t1["agent_id"] == t2["agent_id"]:
                continue
            if t1["entry_time"] and t2["entry_time"] and t1["exit_time"] and t2["exit_time"]:
                # Check time overlap
                if t1["entry_time"] < t2["exit_time"] and t2["entry_time"] < t1["exit_time"]:
                    # Same direction on same symbol?
                    if t1["symbol"] == t2["symbol"] and t1["side"] == t2["side"]:
                        overlap[t1["agent_id"]][t2["agent_id"]] += 1
                        overlap[t2["agent_id"]][t1["agent_id"]] += 1

    # PnL series correlation (daily granularity)
    daily_pnl: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if r["exit_time"]:
            day = r["exit_time"] // 86400000
            daily_pnl[r["agent_id"]][day] += r["pnl_usd"] or 0

    # Compute pairwise correlation
    pnl_corr: dict[str, float] = {}
    for i, a1 in enumerate(agents):
        for a2 in agents[i + 1:]:
            days_both = set(daily_pnl[a1].keys()) & set(daily_pnl[a2].keys())
            if len(days_both) < 5:
                continue
            s1 = [daily_pnl[a1][d] for d in sorted(days_both)]
            s2 = [daily_pnl[a2][d] for d in sorted(days_both)]
            corr = _pearson(s1, s2)
            pnl_corr[f"{a1}↔{a2}"] = round(corr, 3)

    return {"overlap_matrix": overlap, "pnl_correlation": pnl_corr}


# ═══ Report Formatting ═══

def print_agent_report(metrics_list: list[dict]):
    if not metrics_list:
        print("  No closed trades found.")
        return

    for m in metrics_list:
        print(f"\n{'=' * 55}")
        print(f"  AGENT: {m['agent_id'].upper()}")
        print(f"{'=' * 55}")
        print(f"  Trades:        {m['total_trades']:>6}  (W:{m['wins']} / L:{m['losses']})")
        print(f"  Win Rate:      {m['win_rate']:>6.1f}%")
        print(f"  Net PnL:       ${m['total_pnl']:>10,.2f}  (avg ${m['avg_pnl']:,.2f})")
        print(f"  Profit Factor: {m['profit_factor']:>10.2f}")
        print(f"  Avg RR:        {m['avg_rr']:>10.2f}")
        print(f"  Max Drawdown:  ${m['max_dd_usd']:>10,.2f}  ({m['max_dd_pct']:.2f}%)")
        print(f"  Sharpe:        {m['sharpe']:>10.2f}")
        print(f"  Sortino:       {m['sortino']:>10.2f}")
        print(f"  Max Win Streak:  {m['max_win_streak']:>4}")
        print(f"  Max Loss Streak: {m['max_loss_streak']:>4}")
        print(f"  Avg Hold:      {m['avg_hold_min']:>8.0f} min")
        if m["close_reasons"]:
            print(f"  Close Reasons:")
            for reason, count in sorted(m["close_reasons"].items(), key=lambda x: -x[1]):
                pct = count / m["total_trades"] * 100
                print(f"    {reason:<20} {count:>4} ({pct:.1f}%)")

    # Comparison table
    if len(metrics_list) > 1:
        print(f"\n{'=' * 65}")
        print("  COMPARISON")
        print(f"{'=' * 65}")
        header = f"{'Metric':<18}"
        for m in metrics_list:
            header += f"  {m['agent_id'].upper():>10}"
        print(header)
        print("-" * (18 + 12 * len(metrics_list)))

        rows = [
            ("Trades", lambda m: f"{m['total_trades']:>10}"),
            ("Win Rate %", lambda m: f"{m['win_rate']:>9.1f}%"),
            ("Net PnL $", lambda m: f"${m['total_pnl']:>9,.0f}"),
            ("Profit Factor", lambda m: f"{m['profit_factor']:>10.2f}"),
            ("Max DD %", lambda m: f"{m['max_dd_pct']:>9.2f}%"),
            ("Sharpe", lambda m: f"{m['sharpe']:>10.2f}"),
            ("Sortino", lambda m: f"{m['sortino']:>10.2f}"),
            ("Avg RR", lambda m: f"{m['avg_rr']:>10.2f}"),
        ]
        for label, fmt in rows:
            row = f"{label:<18}"
            for m in metrics_list:
                row += f"  {fmt(m)}"
            print(row)
        print(f"{'=' * 65}")


def print_pattern_report(patterns: list[dict]):
    if not patterns:
        print("  No pattern data found.")
        return

    print(f"\n{'=' * 65}")
    print("  PATTERN WIN-RATE ANALYSIS")
    print(f"{'=' * 65}")
    print(f"{'Pattern':<25} {'Trades':>6} {'Wins':>5} {'WR%':>6} {'PnL':>10} {'Avg':>8} {'Score':>6}")
    print("-" * 65)
    for p in sorted(patterns, key=lambda x: -x["total_pnl"]):
        print(
            f"{p['type']:<25} {p['trades']:>6} {p['wins']:>5} "
            f"{p['win_rate']:>5.1f}% ${p['total_pnl']:>9,.0f} "
            f"${p['avg_pnl']:>7,.0f} {p['avg_score']:>5.1f}"
        )
    print(f"{'=' * 65}")


def print_correlation_report(corr: dict):
    overlap = corr.get("overlap_matrix", {})
    pnl_corr = corr.get("pnl_correlation", {})

    if not overlap and not pnl_corr:
        print("  Not enough data for correlation analysis.")
        return

    print(f"\n{'=' * 55}")
    print("  AGENT CORRELATION")
    print(f"{'=' * 55}")

    if overlap:
        agents = sorted(overlap.keys())
        print("\n  Simultaneous Same-Direction Positions:")
        header = f"{'':>6}"
        for a in agents:
            header += f" {a:>5}"
        print(header)
        for a1 in agents:
            row = f"  {a1:>4}"
            for a2 in agents:
                row += f" {overlap[a1][a2]:>5}"
            print(row)

    if pnl_corr:
        print("\n  Daily PnL Correlation:")
        for pair, corr_val in sorted(pnl_corr.items()):
            bar = "█" * int(abs(corr_val) * 20)
            sign = "+" if corr_val > 0 else "-"
            print(f"  {pair:<10} {corr_val:>7.3f}  {sign}{bar}")

    print(f"\n{'=' * 55}")


# ═══ Helpers ═══

def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if sx == 0 or sy == 0:
        return 0.0
    return sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (sx * sy)


# ═══ CLI ═══

def main():
    parser = argparse.ArgumentParser(description="Analyze trading performance from DB")
    parser.add_argument("--days", type=int, default=None, help="Last N days")
    parser.add_argument("--agent", type=str, default=None, help="Filter by agent ID")
    parser.add_argument("--patterns", action="store_true", help="Show pattern win-rate breakdown")
    parser.add_argument("--correlation", action="store_true", help="Show agent correlation analysis")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return

    conn = get_connection()

    # Count total trades
    cursor = conn.execute("SELECT COUNT(*) FROM trades WHERE exit_reason IS NOT NULL")
    total = cursor.fetchone()[0]
    print(f"\nDatabase: {DB_PATH}")
    print(f"Total closed trades: {total}")

    if total == 0:
        print("No trades to analyze. Run the system first or load historical data.")
        conn.close()
        return

    # Agent metrics
    period = f"last {args.days} days" if args.days else "all time"
    agent_filter = f"agent {args.agent}" if args.agent else "all agents"
    print(f"Period: {period}, Filter: {agent_filter}")

    metrics = compute_agent_metrics(conn, args.agent, args.days)
    print_agent_report(metrics)

    # Pattern analysis
    if args.patterns:
        patterns = pattern_analysis(conn, args.days)
        print_pattern_report(patterns)

    # Correlation
    if args.correlation:
        corr = correlation_analysis(conn, args.days)
        print_correlation_report(corr)

    conn.close()


if __name__ == "__main__":
    main()
