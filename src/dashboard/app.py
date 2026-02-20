"""
Trading Dashboard — FastAPI backend.
Serves API endpoints for trade data and a single-page HTML dashboard.
Read-only access to SQLite DB.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from src.utils.config import AGENT_PROFILES, DB_PATH
from src.utils.db import get_read_connection
from src.utils.health import _build_status, _components

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app() -> FastAPI:
    app = FastAPI(title="Trading Dashboard", docs_url=None, redoc_url=None)

    # ── Serve HTML dashboard ──

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = TEMPLATES_DIR / "index.html"
        return HTMLResponse(html_path.read_text())

    # ── API: Summary ──

    @app.get("/api/summary")
    async def api_summary():
        db = await get_read_connection()
        try:
            # Total PnL
            row = await (await db.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE exit_time IS NOT NULL"
            )).fetchone()
            total_pnl = row[0]

            # Win rate
            row = await (await db.execute(
                "SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL"
            )).fetchone()
            total_trades = row[0]

            row = await (await db.execute(
                "SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL AND pnl_usd > 0"
            )).fetchone()
            wins = row[0]

            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

            # Open positions count
            row = await (await db.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'OPEN'"
            )).fetchone()
            open_positions = row[0]

            # Profit Factor
            row = await (await db.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE exit_time IS NOT NULL AND pnl_usd > 0"
            )).fetchone()
            gross_profit = row[0]

            row = await (await db.execute(
                "SELECT COALESCE(ABS(SUM(pnl_usd)), 0) FROM trades WHERE exit_time IS NOT NULL AND pnl_usd < 0"
            )).fetchone()
            gross_loss = row[0]

            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

            # MDD from equity_curve
            row = await (await db.execute(
                "SELECT COALESCE(MAX(drawdown), 0) FROM equity_curve"
            )).fetchone()
            max_drawdown = row[0]

            return {
                "total_pnl": round(total_pnl, 2),
                "win_rate": round(win_rate, 1),
                "total_trades": total_trades,
                "open_positions": open_positions,
                "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "∞",
                "max_drawdown": round(max_drawdown * 100, 2),
            }
        finally:
            await db.close()

    # ── API: Equity Curve ──

    @app.get("/api/equity")
    async def api_equity(
        agent_id: str | None = Query(None),
        hours: int = Query(168, ge=1),
    ):
        db = await get_read_connection()
        try:
            cutoff = int((time.time() - hours * 3600) * 1000)

            if agent_id:
                cursor = await db.execute(
                    "SELECT timestamp, agent_id, equity, drawdown FROM equity_curve "
                    "WHERE agent_id = ? AND timestamp > ? ORDER BY timestamp",
                    (agent_id, cutoff),
                )
            else:
                cursor = await db.execute(
                    "SELECT timestamp, agent_id, equity, drawdown FROM equity_curve "
                    "WHERE timestamp > ? ORDER BY timestamp",
                    (cutoff,),
                )

            rows = await cursor.fetchall()
            cols = ["timestamp", "agent_id", "equity", "drawdown"]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            await db.close()

    # ── API: Trades ──

    @app.get("/api/trades")
    async def api_trades(
        agent_id: str | None = Query(None),
        symbol: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
    ):
        db = await get_read_connection()
        try:
            conditions = []
            params: list = []

            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cursor = await db.execute(
                f"SELECT id, signal_id, agent_id, symbol, side, "
                f"entry_time, exit_time, entry_price, exit_price, "
                f"leverage, notional_usd, margin_usd, sl_pct, "
                f"pnl_usd, pnl_pct, regime, exit_reason "
                f"FROM trades {where} "
                f"ORDER BY COALESCE(exit_time, entry_time) DESC LIMIT ?",
                params + [limit],
            )

            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            await db.close()

    # ── API: Positions ──

    @app.get("/api/positions")
    async def api_positions():
        db = await get_read_connection()
        try:
            cursor = await db.execute(
                "SELECT id, agent_id, symbol, direction, entry_price, "
                "size_usd, sl_pct, entry_time, signal_id, status "
                "FROM positions WHERE status = 'OPEN' "
                "ORDER BY entry_time DESC"
            )
            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            now_ms = int(time.time() * 1000)
            result = []
            for r in rows:
                d = dict(zip(cols, r))
                d["elapsed_sec"] = (now_ms - d["entry_time"]) // 1000 if d["entry_time"] else 0
                result.append(d)
            return result
        finally:
            await db.close()

    # ── API: Agent Performance ──

    @app.get("/api/agents")
    async def api_agents():
        db = await get_read_connection()
        try:
            results = {}
            for agent_id, profile in AGENT_PROFILES.items():
                # Trade stats
                row = await (await db.execute(
                    "SELECT COUNT(*), "
                    "COALESCE(SUM(pnl_usd), 0), "
                    "SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) "
                    "FROM trades WHERE agent_id = ? AND exit_time IS NOT NULL",
                    (agent_id,),
                )).fetchone()
                total, pnl, wins = row

                # MDD
                row = await (await db.execute(
                    "SELECT COALESCE(MAX(drawdown), 0) FROM equity_curve WHERE agent_id = ?",
                    (agent_id,),
                )).fetchone()
                mdd = row[0]

                # Open positions
                row = await (await db.execute(
                    "SELECT COUNT(*) FROM positions WHERE agent_id = ? AND status = 'OPEN'",
                    (agent_id,),
                )).fetchone()
                open_pos = row[0]

                results[agent_id] = {
                    "agent_id": agent_id,
                    "description": profile.description,
                    "timeframes": profile.timeframes,
                    "capital_pct": profile.capital_pct,
                    "total_trades": total,
                    "pnl_usd": round(pnl, 2),
                    "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                    "max_drawdown": round(mdd * 100, 2),
                    "open_positions": open_pos,
                }

            return results
        finally:
            await db.close()

    # ── API: Wallet Balances ──

    @app.get("/api/balances")
    async def api_balances():
        et = _components.get("equity_tracker")
        if et and hasattr(et, "get_balances"):
            return et.get_balances()
        return {}

    # ── API: Health Proxy ──

    @app.get("/api/health")
    async def api_health():
        return _build_status()

    return app
