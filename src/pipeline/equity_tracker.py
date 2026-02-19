"""
Equity Tracker — per-agent equity curve, MDD, rolling PF, consecutive loss tracking.
Updates agent_state for pipeline runner consumption.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentEquity:
    """Equity state for a single agent."""
    agent_id: str
    initial_capital: float = 0.0
    current_equity: float = 0.0
    peak_equity: float = 0.0
    current_mdd: float = 0.0  # 0.0~1.0
    # Trade tracking
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    # Rolling PF (last 20 trades)
    recent_profits: deque = field(default_factory=lambda: deque(maxlen=20))
    recent_losses: deque = field(default_factory=lambda: deque(maxlen=20))
    rolling_pf: float = 2.0
    # Equity history
    equity_snapshots: list = field(default_factory=list)  # [(timestamp, equity)]


class EquityTracker:
    """Tracks equity curves and MDD for all agents."""

    def __init__(self):
        self.agents: dict[str, AgentEquity] = {}

    def initialize_agent(self, agent_id: str, capital: float):
        """Set up tracking for an agent."""
        self.agents[agent_id] = AgentEquity(
            agent_id=agent_id,
            initial_capital=capital,
            current_equity=capital,
            peak_equity=capital,
        )
        logger.info("Equity tracker initialized: %s with $%.2f", agent_id, capital)

    def record_trade(self, agent_id: str, pnl: float):
        """Record a completed trade's PnL."""
        agent = self.agents.get(agent_id)
        if not agent:
            return

        agent.total_trades += 1
        agent.current_equity += pnl
        ts = int(time.time() * 1000)
        agent.equity_snapshots.append((ts, agent.current_equity))

        # Keep last 1000 snapshots
        if len(agent.equity_snapshots) > 1000:
            agent.equity_snapshots = agent.equity_snapshots[-1000:]

        # Win/loss tracking
        if pnl > 0:
            agent.wins += 1
            agent.consecutive_losses = 0
            agent.recent_profits.append(pnl)
        elif pnl < 0:
            agent.losses += 1
            agent.consecutive_losses += 1
            agent.recent_losses.append(abs(pnl))

        # Update peak and MDD
        if agent.current_equity > agent.peak_equity:
            agent.peak_equity = agent.current_equity

        if agent.peak_equity > 0:
            agent.current_mdd = (agent.peak_equity - agent.current_equity) / agent.peak_equity
        else:
            agent.current_mdd = 0.0

        # Update rolling PF
        gross_profit = sum(agent.recent_profits)
        gross_loss = sum(agent.recent_losses)
        agent.rolling_pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        if agent.rolling_pf == float("inf"):
            agent.rolling_pf = 10.0  # Cap

        logger.debug(
            "[%s] Trade: pnl=%.2f equity=%.2f mdd=%.4f pf=%.2f streak=%d",
            agent_id, pnl, agent.current_equity, agent.current_mdd,
            agent.rolling_pf, agent.consecutive_losses,
        )

    def get_agent_state_update(self, agent_id: str) -> dict:
        """Get state dict to update pipeline runner agent_state."""
        agent = self.agents.get(agent_id)
        if not agent:
            return {}

        return {
            "capital": agent.current_equity,
            "initial_capital": agent.initial_capital,
            "current_mdd": agent.current_mdd,
            "rolling_pf": agent.rolling_pf,
            "consecutive_losses": agent.consecutive_losses,
        }

    def get_portfolio_mdd(self) -> float:
        """Get combined portfolio MDD."""
        total_peak = sum(a.peak_equity for a in self.agents.values())
        total_current = sum(a.current_equity for a in self.agents.values())
        if total_peak <= 0:
            return 0.0
        return (total_peak - total_current) / total_peak

    def get_daily_report(self) -> dict:
        """Generate daily report data for all agents."""
        total_equity = 0.0
        total_pnl = 0.0
        total_wins = 0
        total_losses = 0
        gross_profit = 0.0
        gross_loss = 0.0

        for agent in self.agents.values():
            total_equity += agent.current_equity
            total_pnl += (agent.current_equity - agent.initial_capital)
            total_wins += agent.wins
            total_losses += agent.losses
            gross_profit += sum(agent.recent_profits)
            gross_loss += sum(agent.recent_losses)

        total_trades = total_wins + total_losses
        win_rate = total_wins / total_trades if total_trades > 0 else 0
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "equity": round(total_equity, 2),
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": win_rate,
            "profit_factor": round(min(pf, 99.99), 2),
            "mdd": round(self.get_portfolio_mdd(), 4),
            "agents": {
                aid: {
                    "equity": round(a.current_equity, 2),
                    "mdd": round(a.current_mdd, 4),
                    "trades": a.total_trades,
                    "pf": round(a.rolling_pf, 2),
                    "streak": a.consecutive_losses,
                }
                for aid, a in self.agents.items()
            },
        }
