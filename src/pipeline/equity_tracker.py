"""
Equity Tracker — per-agent equity curve, MDD, rolling PF, consecutive loss tracking.
Updates agent_state for pipeline runner consumption.
Persists equity snapshots to equity_curve table in SQLite.
Syncs real wallet balances from Hyperliquid periodically.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from src.utils.config import DB_PATH

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


@dataclass
class WalletBalance:
    """Cached wallet balance from Hyperliquid."""
    agent_id: str
    address: str
    perp_equity: float = 0.0       # Perps account value
    spot_balance: float = 0.0      # Spot USDC balance
    account_value: float = 0.0     # perp_equity + spot_balance
    unrealized_pnl: float = 0.0
    margin_used: float = 0.0
    withdrawable: float = 0.0
    last_sync: float = 0.0


class EquityTracker:
    """Tracks equity curves and MDD for all agents."""

    def __init__(self):
        self.agents: dict[str, AgentEquity] = {}
        self.wallet_balances: dict[str, WalletBalance] = {}
        self._wallet_addresses: dict[str, str] = {}  # agent_id → address
        self._sync_running = False
        self._initial_sync_done: set[str] = set()  # agents that completed first balance sync
        self._last_trade_ts: dict[str, float] = {}  # agent_id → last record_trade time
        self._trade_cooldown_sec: float = 30.0  # Skip balance overwrite if trade recorded within this window
        self._db = None  # Shared DB connection for equity writes
        self._db_lock = asyncio.Lock()  # Prevent concurrent writes

    def initialize_agent(self, agent_id: str, capital: float):
        """Set up tracking for an agent."""
        self.agents[agent_id] = AgentEquity(
            agent_id=agent_id,
            initial_capital=capital,
            current_equity=capital,
            peak_equity=capital,
        )
        logger.info("Equity tracker initialized: %s with $%.2f", agent_id, capital)

    async def record_trade(self, agent_id: str, pnl: float):
        """Record a completed trade's PnL and persist to DB."""
        agent = self.agents.get(agent_id)
        if not agent:
            return

        agent.total_trades += 1
        agent.current_equity += pnl
        self._last_trade_ts[agent_id] = time.time()
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
            raw_mdd = (agent.peak_equity - agent.current_equity) / agent.peak_equity
            agent.current_mdd = max(0.0, min(raw_mdd, 1.0))  # Clamp to [0, 1]
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

        # Persist to equity_curve table
        await self._flush_to_db(agent_id, ts, agent.current_equity, agent.current_mdd, agent.peak_equity)

    async def _ensure_db(self):
        """Lazily initialize shared DB connection."""
        if self._db is None:
            import aiosqlite
            self._db = await aiosqlite.connect(str(DB_PATH))
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA busy_timeout=5000")

    async def _flush_to_db(
        self, agent_id: str, ts: int, equity: float, drawdown: float, peak: float,
    ):
        """Write equity snapshot to SQLite equity_curve table."""
        async with self._db_lock:
            try:
                await self._ensure_db()
                await self._db.execute(
                    "INSERT OR REPLACE INTO equity_curve "
                    "(timestamp, agent_id, equity, drawdown, peak_equity) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ts, agent_id, round(equity, 2), round(drawdown, 6), round(peak, 2)),
                )
                await self._db.commit()
            except Exception:
                logger.warning("Failed to flush equity_curve for %s", agent_id, exc_info=True)
                # Reset connection on error
                try:
                    if self._db:
                        await self._db.close()
                except Exception:
                    pass
                self._db = None

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

    # ═══ Wallet Balance Sync ═══

    def set_wallet_addresses(self, addresses: dict[str, str]):
        """Set wallet addresses for balance monitoring. {agent_id: address}"""
        self._wallet_addresses = addresses
        for agent_id, addr in addresses.items():
            self.wallet_balances[agent_id] = WalletBalance(
                agent_id=agent_id, address=addr,
            )
        logger.info("Wallet addresses set: %s", {k: v[:10] + "..." for k, v in addresses.items()})

    async def _sync_one_balance(self, agent_id: str, address: str):
        """Fetch balance for one wallet from Hyperliquid (perps + spot)."""
        try:
            from src.exchange.hyperliquid import get_info_client
            info = get_info_client()

            # Perps account
            state = info.user_state(address)
            margin = state.get("marginSummary", {})
            perp_equity = float(margin.get("accountValue", 0))
            unrealized_pnl = float(margin.get("totalNtlPos", 0))
            margin_used = float(margin.get("totalMarginUsed", 0))
            withdrawable = float(margin.get("totalRawUsd", 0))

            # Spot account
            spot_balance = 0.0
            try:
                spot_state = info.spot_user_state(address)
                for bal in spot_state.get("balances", []):
                    if bal.get("coin") == "USDC":
                        spot_balance = float(bal.get("total", 0))
                        break
            except Exception:
                logger.debug("[%s] Spot balance fetch failed, skipping", agent_id)

            total_value = perp_equity + spot_balance

            wb = self.wallet_balances.get(agent_id)
            if wb:
                wb.perp_equity = perp_equity
                wb.spot_balance = spot_balance
                wb.account_value = total_value
                wb.unrealized_pnl = unrealized_pnl
                wb.margin_used = margin_used
                wb.withdrawable = withdrawable
                wb.last_sync = time.time()

            # Update in-memory equity to match real balance
            agent = self.agents.get(agent_id)
            if agent:
                # First sync: always reset to real balance (avoid false MDD from placeholder capital)
                if agent_id not in self._initial_sync_done:
                    agent.initial_capital = total_value
                    agent.current_equity = total_value
                    agent.peak_equity = total_value
                    agent.current_mdd = 0.0
                    self._initial_sync_done.add(agent_id)
                    logger.info("[%s] Initial balance sync: $%.2f (peak reset)", agent_id, total_value)
                elif total_value > 0:
                    # Skip equity overwrite if a trade was recently recorded
                    # (wallet balance hasn't reflected the trade yet)
                    last_trade = self._last_trade_ts.get(agent_id, 0)
                    if time.time() - last_trade < self._trade_cooldown_sec:
                        logger.debug("[%s] Balance sync skipped equity overwrite (recent trade)", agent_id)
                    else:
                        agent.current_equity = total_value
                    if total_value > agent.peak_equity:
                        agent.peak_equity = total_value
                    if agent.peak_equity > 0:
                        agent.current_mdd = max(0.0, min(
                            (agent.peak_equity - agent.current_equity) / agent.peak_equity, 1.0,
                        ))

            # Persist to equity_curve
            ts = int(time.time() * 1000)
            drawdown = agent.current_mdd if agent else 0.0
            peak = agent.peak_equity if agent else total_value
            await self._flush_to_db(agent_id, ts, total_value, drawdown, peak)

            logger.debug(
                "[%s] Balance sync: $%.2f (perp=$%.2f, spot=$%.2f, margin=$%.2f)",
                agent_id, total_value, perp_equity, spot_balance, margin_used,
            )
        except Exception:
            logger.warning("Balance sync failed for %s", agent_id, exc_info=True)

    async def sync_all_balances(self):
        """Sync balances for all configured wallets."""
        for agent_id, address in self._wallet_addresses.items():
            await self._sync_one_balance(agent_id, address)
            await asyncio.sleep(0.5)  # rate limit between calls

    async def balance_sync_loop(self, interval_sec: float = 60.0):
        """Periodically sync wallet balances. Runs as a coroutine."""
        self._sync_running = True
        logger.info("Balance sync started (interval=%ds, wallets=%d)",
                     int(interval_sec), len(self._wallet_addresses))

        # Initial sync after short delay
        await asyncio.sleep(5)

        while self._sync_running:
            try:
                await self.sync_all_balances()
            except Exception:
                logger.warning("Balance sync loop error", exc_info=True)
            await asyncio.sleep(interval_sec)

    def stop_sync(self):
        """Stop the balance sync loop."""
        self._sync_running = False

    def get_balances(self) -> dict[str, dict]:
        """Return current wallet balances for API consumption."""
        return {
            aid: {
                "agent_id": aid,
                "address": wb.address,
                "account_value": round(wb.account_value, 2),
                "perp_equity": round(wb.perp_equity, 2),
                "spot_balance": round(wb.spot_balance, 2),
                "unrealized_pnl": round(wb.unrealized_pnl, 2),
                "margin_used": round(wb.margin_used, 2),
                "withdrawable": round(wb.withdrawable, 2),
                "last_sync": int(wb.last_sync) if wb.last_sync else 0,
            }
            for aid, wb in self.wallet_balances.items()
        }

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
