"""
Backtest Report — text-based performance summary.
"""
from __future__ import annotations

import datetime

from src.backtest.metrics import BacktestMetrics


def format_report(metrics: BacktestMetrics) -> str:
    """Generate a text summary report from backtest metrics."""
    m = metrics
    lines: list[str] = []

    # Header
    start = _ts_to_str(m.start_ts) if m.start_ts else "N/A"
    end = _ts_to_str(m.end_ts) if m.end_ts else "N/A"
    lines.append("=" * 60)
    lines.append(f"  BACKTEST REPORT: {m.agent_id.upper()} / {m.symbol}")
    lines.append(f"  Period: {start} → {end} ({m.days:.0f} days)")
    lines.append("=" * 60)

    # Capital
    lines.append("")
    lines.append("── Capital ──")
    lines.append(f"  Initial:       ${m.initial_capital:>12,.2f}")
    lines.append(f"  Final:         ${m.final_equity:>12,.2f}")
    lines.append(f"  Net PnL:       ${m.net_pnl:>12,.2f} ({m.total_return_pct:+.2f}%)")
    lines.append(f"  Costs:         ${m.total_costs:>12,.2f}")

    # Trades
    lines.append("")
    lines.append("── Trades ──")
    lines.append(f"  Total:         {m.total_trades:>6}")
    lines.append(f"  Wins:          {m.wins:>6}  ({m.win_rate:.1f}%)")
    lines.append(f"  Losses:        {m.losses:>6}")
    if m.breakeven:
        lines.append(f"  Breakeven:     {m.breakeven:>6}")
    lines.append(f"  Avg PnL/trade: ${m.avg_pnl_per_trade:>10,.2f}")

    # Win/Loss stats
    lines.append("")
    lines.append("── Win/Loss ──")
    lines.append(f"  Avg Win:       ${m.avg_win:>10,.2f}")
    lines.append(f"  Avg Loss:      ${m.avg_loss:>10,.2f}")
    lines.append(f"  Avg RR:        {m.avg_rr:>10.2f}")
    lines.append(f"  Largest Win:   ${m.largest_win:>10,.2f}")
    lines.append(f"  Largest Loss:  ${m.largest_loss:>10,.2f}")

    # Risk
    lines.append("")
    lines.append("── Risk ──")
    lines.append(f"  Max Drawdown:  {m.max_drawdown_pct:>9.2f}%  (${m.max_drawdown_usd:,.2f})")
    pf_str = f"{m.profit_factor:.2f}" if m.profit_factor < 100 else "INF"
    lines.append(f"  Profit Factor: {pf_str:>10}")
    lines.append(f"  Sharpe Ratio:  {m.sharpe_ratio:>10.2f}")
    lines.append(f"  Sortino Ratio: {m.sortino_ratio:>10.2f}")
    lines.append(f"  Calmar Ratio:  {m.calmar_ratio:>10.2f}")

    # Streaks
    lines.append("")
    lines.append("── Streaks ──")
    lines.append(f"  Max Win Streak:  {m.max_consecutive_wins:>4}")
    lines.append(f"  Max Loss Streak: {m.max_consecutive_losses:>4}")

    # Duration
    lines.append("")
    lines.append("── Duration (5m candles) ──")
    lines.append(f"  Avg Hold:      {m.avg_hold_candles:>8.1f}  ({m.avg_hold_candles * 5:.0f} min)")
    lines.append(f"  Avg Win Hold:  {m.avg_win_hold:>8.1f}  ({m.avg_win_hold * 5:.0f} min)")
    lines.append(f"  Avg Loss Hold: {m.avg_loss_hold:>8.1f}  ({m.avg_loss_hold * 5:.0f} min)")

    # Close reasons
    if m.close_reasons:
        lines.append("")
        lines.append("── Close Reasons ──")
        for reason, count in sorted(m.close_reasons.items(), key=lambda x: -x[1]):
            pct = count / m.total_trades * 100
            lines.append(f"  {reason:<20} {count:>4} ({pct:.1f}%)")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


def format_comparison(all_metrics: list[BacktestMetrics]) -> str:
    """Generate side-by-side comparison of multiple agents."""
    if not all_metrics:
        return "No results."

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("  AGENT COMPARISON")
    lines.append("=" * 70)
    lines.append("")

    # Header row
    header = f"{'Metric':<22}"
    for m in all_metrics:
        header += f"  {m.agent_id.upper():>10}"
    lines.append(header)
    lines.append("-" * (22 + 12 * len(all_metrics)))

    # Rows
    rows = [
        ("Trades", lambda m: f"{m.total_trades:>10}"),
        ("Win Rate %", lambda m: f"{m.win_rate:>9.1f}%"),
        ("Net PnL $", lambda m: f"${m.net_pnl:>9,.0f}"),
        ("Return %", lambda m: f"{m.total_return_pct:>9.1f}%"),
        ("Profit Factor", lambda m: f"{min(m.profit_factor, 99.9):>10.2f}"),
        ("Max DD %", lambda m: f"{m.max_drawdown_pct:>9.2f}%"),
        ("Sharpe", lambda m: f"{m.sharpe_ratio:>10.2f}"),
        ("Sortino", lambda m: f"{m.sortino_ratio:>10.2f}"),
        ("Avg RR", lambda m: f"{m.avg_rr:>10.2f}"),
        ("Max Loss Streak", lambda m: f"{m.max_consecutive_losses:>10}"),
        ("Avg Hold (min)", lambda m: f"{m.avg_hold_candles * 5:>10.0f}"),
    ]

    for label, fmt in rows:
        row = f"{label:<22}"
        for m in all_metrics:
            row += f"  {fmt(m)}"
        lines.append(row)

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def _ts_to_str(ts_ms: int) -> str:
    """Convert timestamp ms to readable string."""
    return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M")
