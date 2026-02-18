"""
Backtest Metrics — performance calculation from backtest results.

Calculates:
  - Profit Factor, Win Rate, Average RR
  - Max Drawdown (peak-to-trough equity)
  - Sharpe Ratio, Sortino Ratio
  - Max consecutive wins/losses
  - Trade duration stats
  - Equity curve
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.backtest.engine import BacktestResult, SimPosition


@dataclass
class BacktestMetrics:
    """Calculated metrics for a backtest run."""
    agent_id: str = ""
    symbol: str = ""

    # Capital
    initial_capital: float = 0.0
    final_equity: float = 0.0
    total_return_pct: float = 0.0

    # Trades
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0

    # PnL
    total_pnl: float = 0.0
    total_costs: float = 0.0
    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_rr: float = 0.0  # avg_win / avg_loss
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_pnl_per_trade: float = 0.0

    # Risk
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    calmar_ratio: float = 0.0  # annual return / max DD

    # Ratios
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0

    # Streaks
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    # Duration (in 5m candles)
    avg_hold_candles: float = 0.0
    avg_win_hold: float = 0.0
    avg_loss_hold: float = 0.0

    # Close reasons
    close_reasons: dict[str, int] = field(default_factory=dict)

    # Equity curve: [(timestamp, equity)]
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    # Time range
    start_ts: int = 0
    end_ts: int = 0
    days: float = 0.0


def calculate_metrics(
    result: BacktestResult,
    initial_capital: float = 10000.0,
) -> BacktestMetrics:
    """Calculate comprehensive metrics from a backtest result."""
    m = BacktestMetrics(
        agent_id=result.agent_id,
        symbol=result.symbol,
        initial_capital=initial_capital,
        start_ts=result.start_ts,
        end_ts=result.end_ts,
    )

    positions = [p for p in result.positions if p.status == "closed"]
    m.total_trades = len(positions)

    if m.total_trades == 0:
        m.final_equity = initial_capital
        return m

    # Time range
    if result.end_ts > result.start_ts:
        m.days = (result.end_ts - result.start_ts) / (86400 * 1000)

    # ── Build equity curve and trade stats ──
    equity = initial_capital
    peak = initial_capital
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    returns: list[float] = []

    win_streak = 0
    loss_streak = 0
    max_win_streak = 0
    max_loss_streak = 0

    win_holds: list[int] = []
    loss_holds: list[int] = []

    m.equity_curve.append((result.start_ts, equity))

    for pos in positions:
        net = pos.net_pnl
        equity += net
        returns.append(net / initial_capital)  # normalize to initial capital

        m.equity_curve.append((pos.close_ts, equity))

        # Peak and DD
        if equity > peak:
            peak = equity
        dd_usd = peak - equity
        dd_pct = dd_usd / peak if peak > 0 else 0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        # PnL stats
        m.total_pnl += pos.pnl_usd
        m.total_costs += pos.cost_usd
        m.net_pnl += net

        if net > 0:
            m.wins += 1
            m.gross_profit += net
            m.largest_win = max(m.largest_win, net)
            win_streak += 1
            loss_streak = 0
            max_win_streak = max(max_win_streak, win_streak)
            win_holds.append(pos.candles_held)
        elif net < 0:
            m.losses += 1
            m.gross_loss += abs(net)
            m.largest_loss = max(m.largest_loss, abs(net))
            loss_streak += 1
            win_streak = 0
            max_loss_streak = max(max_loss_streak, loss_streak)
            loss_holds.append(pos.candles_held)
        else:
            m.breakeven += 1

        # Close reasons
        reason = pos.close_reason or "UNKNOWN"
        m.close_reasons[reason] = m.close_reasons.get(reason, 0) + 1

    # ── Derived metrics ──
    m.final_equity = equity
    m.total_return_pct = ((equity - initial_capital) / initial_capital) * 100
    m.max_drawdown_pct = max_dd_pct * 100
    m.max_drawdown_usd = max_dd_usd

    m.win_rate = m.wins / m.total_trades * 100 if m.total_trades > 0 else 0
    m.profit_factor = m.gross_profit / m.gross_loss if m.gross_loss > 0 else float("inf")
    m.avg_pnl_per_trade = m.net_pnl / m.total_trades if m.total_trades > 0 else 0

    m.avg_win = m.gross_profit / m.wins if m.wins > 0 else 0
    m.avg_loss = m.gross_loss / m.losses if m.losses > 0 else 0
    m.avg_rr = m.avg_win / m.avg_loss if m.avg_loss > 0 else float("inf")

    m.max_consecutive_wins = max_win_streak
    m.max_consecutive_losses = max_loss_streak

    # Duration
    all_holds = [p.candles_held for p in positions]
    m.avg_hold_candles = sum(all_holds) / len(all_holds) if all_holds else 0
    m.avg_win_hold = sum(win_holds) / len(win_holds) if win_holds else 0
    m.avg_loss_hold = sum(loss_holds) / len(loss_holds) if loss_holds else 0

    # Calmar ratio (annualized return / max DD)
    if m.days > 0 and max_dd_pct > 0:
        annual_return = (m.total_return_pct / 100) * (365 / m.days)
        m.calmar_ratio = annual_return / max_dd_pct

    # Sharpe & Sortino (annualized, assuming 5m candles ≈ 288/day)
    if len(returns) >= 10:
        avg_ret = sum(returns) / len(returns)
        std_ret = _std(returns)

        # Annualization: trades per day × 365
        trades_per_day = m.total_trades / m.days if m.days > 0 else 1
        annualize = math.sqrt(trades_per_day * 365) if trades_per_day > 0 else 1

        # Sharpe
        if std_ret > 0:
            m.sharpe_ratio = (avg_ret / std_ret) * annualize

        # Sortino (downside deviation only)
        down_returns = [r for r in returns if r < 0]
        if down_returns:
            down_std = _std(down_returns)
            if down_std > 0:
                m.sortino_ratio = (avg_ret / down_std) * annualize

    return m


def _std(values: list[float]) -> float:
    """Standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)
