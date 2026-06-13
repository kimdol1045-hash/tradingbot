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
from collections import deque
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
    rolling_pf: float | None = None  # None = no trade history yet
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
        self._on_trade_callbacks: list = []

    def add_trade_callback(self, callback):
        """Register a callback to be called with pnl on each trade."""
        self._on_trade_callbacks.append(callback)

    def initialize_agent(self, agent_id: str, capital: float):
        """Set up tracking for an agent."""
        self.agents[agent_id] = AgentEquity(
            agent_id=agent_id,
            initial_capital=capital,
            current_equity=capital,
            peak_equity=capital,
        )
        logger.info("Equity tracker initialized: %s with $%.2f", agent_id, capital)

    async def restore_from_db(self, agent_id: str):
        """Load recent trades from DB to restore rolling_pf.

        Called once per agent at startup so that PF is calculated from real data
        instead of a hardcoded default.

        NOTE: consecutive_losses is NOT restored — it starts fresh at 0.
        Reason: old losing streaks were produced by old rules; new quality rules
        fundamentally change signal selection, so penalizing new rules with old
        streak data is incorrect.  PF is restored because it reflects market
        conditions (not just rule quality).
        """
        agent = self.agents.get(agent_id)
        if not agent:
            return

        try:
            await self._ensure_db()
            cursor = await self._db.execute(
                "SELECT pnl_usd FROM trades "
                "WHERE agent_id = ? AND exit_reason IS NOT NULL "
                "AND pnl_usd IS NOT NULL AND pnl_usd != 0 "
                "ORDER BY exit_time DESC LIMIT 20",
                (agent_id,),
            )
            rows = await cursor.fetchall()
        except Exception:
            logger.warning("Failed to restore PF from DB for %s", agent_id, exc_info=True)
            return

        if not rows:
            logger.info("[%s] No trade history in DB — rolling_pf stays None (neutral)", agent_id)
            return

        # Populate deques (reverse to chronological order)
        for (pnl,) in reversed(rows):
            if pnl > 0:
                agent.recent_profits.append(pnl)
            elif pnl < 0:
                agent.recent_losses.append(abs(pnl))

        # Calculate actual PF
        gross_profit = sum(agent.recent_profits)
        gross_loss = sum(agent.recent_losses)
        if gross_loss > 0:
            agent.rolling_pf = gross_profit / gross_loss
        elif gross_profit > 0:
            agent.rolling_pf = 10.0  # Cap: all wins
        # else: both 0 → stays None

        # consecutive_losses stays at 0 (fresh start with new rules)

        logger.info(
            "[%s] Restored from DB: rolling_pf=%s, consecutive_losses=%d (fresh), trades=%d",
            agent_id,
            f"{agent.rolling_pf:.2f}" if agent.rolling_pf is not None else "None",
            agent.consecutive_losses,
            len(rows),
        )

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
        if gross_loss > 0:
            agent.rolling_pf = min(gross_profit / gross_loss, 10.0)
        elif gross_profit > 0:
            agent.rolling_pf = 10.0  # All wins, cap
        else:
            agent.rolling_pf = None  # No profit or loss data yet

        logger.debug(
            "[%s] Trade: pnl=%.2f equity=%.2f mdd=%.4f pf=%s streak=%d",
            agent_id, pnl, agent.current_equity, agent.current_mdd,
            f"{agent.rolling_pf:.2f}" if agent.rolling_pf is not None else "None",
            agent.consecutive_losses,
        )

        # Notify trade callbacks (circuit breaker etc.)
        for cb in self._on_trade_callbacks:
            try:
                cb(pnl)
            except Exception:
                logger.debug("Trade callback error", exc_info=True)

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
            margin_used = float(margin.get("totalMarginUsed", 0))
            withdrawable = float(margin.get("totalRawUsd", 0))

            # Sum per-position unrealized PnL
            unrealized_pnl = 0.0
            for pos in state.get("assetPositions", []):
                p = pos.get("position", {})
                unrealized_pnl += float(p.get("unrealizedPnl", 0))

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

            # Unified accounts: spot includes held margin, so don't double count
            # Use spot as total when available (perp margin comes from spot)
            if spot_balance > 0 and perp_equity > 0:
                total_value = spot_balance  # spot_total includes held perp margin
            else:
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

        # Initial sync after backfill completes (avoid API rate limit overlap)
        await asyncio.sleep(180)

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
        total_initial = 0.0
        total_pnl = 0.0
        total_wins = 0
        total_losses = 0
        gross_profit = 0.0
        gross_loss = 0.0
        best_trade = 0.0
        worst_trade = 0.0

        for agent in self.agents.values():
            total_equity += agent.current_equity
            total_initial += agent.initial_capital
            total_pnl += (agent.current_equity - agent.initial_capital)
            total_wins += agent.wins
            total_losses += agent.losses
            gross_profit += sum(agent.recent_profits)
            gross_loss += sum(agent.recent_losses)
            if agent.recent_profits:
                best_trade = max(best_trade, max(agent.recent_profits))
            if agent.recent_losses:
                worst_trade = min(worst_trade, -max(agent.recent_losses))

        total_trades = total_wins + total_losses
        win_rate = total_wins / total_trades if total_trades > 0 else 0
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        roi = (total_pnl / total_initial * 100) if total_initial > 0 else 0

        # Wallet balance info (margin usage)
        total_margin_used = 0.0
        total_unrealized = 0.0
        for wb in self.wallet_balances.values():
            total_margin_used += wb.margin_used
            total_unrealized += wb.unrealized_pnl

        # TP hit statistics from DB
        tp_stats = self._get_tp_stats()

        # Deep trade analysis for parameter tuning
        deep_analysis = self._get_deep_analysis()

        return {
            "equity": round(total_equity, 2),
            "initial_capital": round(total_initial, 2),
            "total_pnl": round(total_pnl, 2),
            "roi_pct": round(roi, 2),
            "total_trades": total_trades,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": win_rate,
            "profit_factor": round(min(pf, 99.99), 2),
            "mdd": round(self.get_portfolio_mdd(), 4),
            "best_trade": round(best_trade, 2),
            "worst_trade": round(worst_trade, 2),
            "margin_used": round(total_margin_used, 2),
            "unrealized_pnl": round(total_unrealized, 2),
            "tp_stats": tp_stats,
            "deep_analysis": deep_analysis,
            "agents": {
                aid: {
                    "initial": round(a.initial_capital, 2),
                    "equity": round(a.current_equity, 2),
                    "pnl": round(a.current_equity - a.initial_capital, 2),
                    "roi": round((a.current_equity - a.initial_capital) / a.initial_capital * 100, 2) if a.initial_capital > 0 else 0,
                    "mdd": round(a.current_mdd, 4),
                    "trades": a.total_trades,
                    "wins": a.wins,
                    "losses": a.losses,
                    "pf": round(min(a.rolling_pf, 99.99), 2) if a.rolling_pf is not None else None,
                    "streak": a.consecutive_losses,
                }
                for aid, a in self.agents.items()
            },
        }

    def _get_tp_stats(self) -> dict:
        """Get TP hit statistics from DB."""
        try:
            import sqlite3
            from src.utils.config import DB_PATH
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.execute("""
                SELECT
                    count(*) as total,
                    sum(CASE WHEN p.tp_hits >= 1 THEN 1 ELSE 0 END) as tp1,
                    sum(CASE WHEN p.tp_hits >= 2 THEN 1 ELSE 0 END) as tp2,
                    sum(CASE WHEN p.tp_hits >= 3 THEN 1 ELSE 0 END) as tp3
                FROM positions p
                LEFT JOIN trades t ON p.signal_id = t.signal_id
                WHERE t.exit_reason IS NULL
                   OR t.exit_reason NOT IN ('RECONCILE_STALE', 'DUPLICATE_CLEANUP')
            """)
            row = cursor.fetchone()
            conn.close()
            if row:
                total = row[0] or 1
                return {
                    "total": row[0],
                    "tp1": row[1] or 0,
                    "tp2": row[2] or 0,
                    "tp3": row[3] or 0,
                    "tp1_pct": round((row[1] or 0) / total * 100),
                    "tp2_pct": round((row[2] or 0) / total * 100),
                    "tp3_pct": round((row[3] or 0) / total * 100),
                }
        except Exception:
            logger.debug("Failed to get TP stats", exc_info=True)
        return {"total": 0, "tp1": 0, "tp2": 0, "tp3": 0, "tp1_pct": 0, "tp2_pct": 0, "tp3_pct": 0}

    def _get_deep_analysis(self) -> dict:
        """Collect deep trade analysis data for parameter tuning insights."""
        _EXCLUDE = "('RECONCILE_STALE', 'DUPLICATE_CLEANUP')"
        try:
            import sqlite3
            from src.utils.config import DB_PATH
            conn = sqlite3.connect(str(DB_PATH))

            # 1. Exit reason breakdown
            cursor = conn.execute(f"""
                SELECT exit_reason, count(*) as cnt
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_reason NOT IN {_EXCLUDE}
                GROUP BY exit_reason ORDER BY cnt DESC
            """)
            exit_reasons = {r[0]: r[1] for r in cursor.fetchall()}

            # 2. Per-pattern: exit reason distribution + avg scores
            cursor = conn.execute(f"""
                SELECT inflection_type,
                       count(*) as total,
                       sum(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                       round(avg(CASE WHEN pnl_usd > 0 THEN validation_score END), 1) as avg_gate_win,
                       round(avg(CASE WHEN pnl_usd <= 0 THEN validation_score END), 1) as avg_gate_loss,
                       sum(CASE WHEN exit_reason = 'SL_HIT' THEN 1 ELSE 0 END) as sl_hits,
                       round(sum(pnl_usd), 2) as total_pnl
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_reason NOT IN {_EXCLUDE}
                  AND inflection_type IS NOT NULL AND inflection_type != ''
                GROUP BY inflection_type ORDER BY total DESC
            """)
            patterns = []
            for row in cursor.fetchall():
                itype, total, wins, avg_gw, avg_gl, sl_hits, pnl = row
                wr = round(wins / total * 100) if total > 0 else 0
                sl_rate = round(sl_hits / total * 100) if total > 0 else 0
                patterns.append({
                    "type": itype, "total": total, "wr": wr, "pnl": pnl or 0,
                    "sl_rate": sl_rate, "avg_gate_win": avg_gw, "avg_gate_loss": avg_gl,
                })

            # 3. Regime × direction combo
            cursor = conn.execute(f"""
                SELECT regime, side,
                       count(*) as total,
                       sum(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                       round(sum(pnl_usd), 2) as total_pnl
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_reason NOT IN {_EXCLUDE}
                  AND regime IS NOT NULL AND regime != ''
                GROUP BY regime, side ORDER BY total DESC
            """)
            regime_dir = []
            for row in cursor.fetchall():
                regime, side, total, wins, pnl = row
                wr = round(wins / total * 100) if total > 0 else 0
                regime_dir.append({"regime": regime, "side": side, "total": total, "wr": wr, "pnl": pnl or 0})

            # 4. Gate score bins → win rate
            cursor = conn.execute(f"""
                SELECT
                    CASE
                        WHEN validation_score < 40 THEN '<40'
                        WHEN validation_score < 55 THEN '40-55'
                        WHEN validation_score < 70 THEN '55-70'
                        ELSE '70+'
                    END as bin,
                    count(*) as total,
                    sum(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_reason NOT IN {_EXCLUDE}
                GROUP BY bin ORDER BY bin
            """)
            gate_bins = []
            for row in cursor.fetchall():
                b, total, wins = row
                wr = round(wins / total * 100) if total > 0 else 0
                gate_bins.append({"bin": b, "total": total, "wr": wr})

            # 5. SL distance: wins vs losses
            cursor = conn.execute(f"""
                SELECT
                    round(avg(CASE WHEN pnl_usd > 0 THEN sl_pct END) * 100, 2) as avg_sl_win,
                    round(avg(CASE WHEN pnl_usd <= 0 THEN sl_pct END) * 100, 2) as avg_sl_loss
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_reason NOT IN {_EXCLUDE}
            """)
            row = cursor.fetchone()
            sl_analysis = {"avg_sl_win": row[0] or 0, "avg_sl_loss": row[1] or 0}

            # 6. Hold time: wins vs losses (minutes)
            cursor = conn.execute(f"""
                SELECT
                    round(avg(CASE WHEN pnl_usd > 0 THEN (exit_time - entry_time) / 60000.0 END), 1) as avg_min_win,
                    round(avg(CASE WHEN pnl_usd <= 0 THEN (exit_time - entry_time) / 60000.0 END), 1) as avg_min_loss
                FROM trades
                WHERE exit_time IS NOT NULL AND exit_reason NOT IN {_EXCLUDE}
                  AND entry_time > 0 AND exit_time > entry_time
            """)
            row = cursor.fetchone()
            hold_time = {"avg_min_win": row[0] or 0, "avg_min_loss": row[1] or 0}

            conn.close()
            return {
                "exit_reasons": exit_reasons,
                "patterns": patterns,
                "regime_dir": regime_dir,
                "gate_bins": gate_bins,
                "sl_analysis": sl_analysis,
                "hold_time": hold_time,
            }
        except Exception:
            logger.debug("Failed to get deep analysis", exc_info=True)
        return {}
