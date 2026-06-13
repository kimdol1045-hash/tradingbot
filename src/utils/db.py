"""
SQLite database initialization and access.
WAL mode for concurrent read (agents) / write (collector).
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

from src.utils.config import DB_PATH, SQLITE_CONFIG

logger = logging.getLogger(__name__)

# ── Schema DDL ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,       -- '5m','15m','1h','4h'
    timestamp       INTEGER NOT NULL,
    open            REAL NOT NULL,
    high            REAL NOT NULL,
    low             REAL NOT NULL,
    close           REAL NOT NULL,
    volume          REAL NOT NULL,
    funding_rate    REAL,
    open_interest   REAL,
    liquidation_vol REAL,
    bid_ask_spread  REAL,
    orderbook_imbalance REAL,
    PRIMARY KEY (symbol, timeframe, timestamp)
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       TEXT UNIQUE,
    agent_id        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_time      INTEGER,
    exit_time       INTEGER,
    entry_price     REAL,
    exit_price      REAL,
    leverage        REAL,
    notional_usd    REAL,
    margin_usd      REAL,
    sl_pct          REAL,
    pnl_usd         REAL,
    pnl_pct         REAL,
    regime          TEXT,
    inflection_type TEXT,
    inflection_score REAL,
    validation_score REAL,
    pattern_confirmations TEXT,   -- JSON array
    exit_reason     TEXT,
    params_version  TEXT
);

CREATE TABLE IF NOT EXISTS equity_curve (
    timestamp       INTEGER NOT NULL,
    agent_id        TEXT NOT NULL,
    equity          REAL NOT NULL,
    drawdown        REAL NOT NULL,
    peak_equity     REAL NOT NULL,
    PRIMARY KEY (timestamp, agent_id)
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,       -- 'LONG' or 'SHORT'
    entry_price     REAL NOT NULL,
    size_usd        REAL NOT NULL,
    sl_pct          REAL NOT NULL,
    entry_time      INTEGER NOT NULL,
    signal_id       TEXT UNIQUE,
    exchange_order_id TEXT,              -- Hyperliquid order ID for reconciliation
    status          TEXT NOT NULL DEFAULT 'OPEN',  -- 'OPEN','CLOSED'
    tp_hits         INTEGER NOT NULL DEFAULT 0,
    remaining_qty   REAL NOT NULL DEFAULT 0,
    last_tp_hit_ts  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pipeline_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       INTEGER NOT NULL,
    agent_id        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    phase_snapshot  TEXT NOT NULL,        -- JSON: phase-by-phase debug info
    signal_generated BOOLEAN NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_candles_sym_tf
    ON candles(symbol, timeframe, timestamp);

CREATE INDEX IF NOT EXISTS idx_trades_agent
    ON trades(agent_id, entry_time);

CREATE INDEX IF NOT EXISTS idx_positions_status
    ON positions(status, agent_id);

CREATE INDEX IF NOT EXISTS idx_pipeline_logs_ts
    ON pipeline_logs(timestamp, agent_id);
"""


async def init_db(db_path: Path | None = None) -> aiosqlite.Connection:
    """Initialize database with schema and WAL mode. Returns writer connection."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(path))
    await conn.execute(f"PRAGMA journal_mode={SQLITE_CONFIG['journal_mode']}")
    await conn.execute(f"PRAGMA busy_timeout={SQLITE_CONFIG['busy_timeout_ms']}")
    await conn.executescript(SCHEMA_SQL)

    # Migrations: add columns if missing (idempotent)
    try:
        await conn.execute(
            "ALTER TABLE positions ADD COLUMN last_tp_hit_ts REAL NOT NULL DEFAULT 0"
        )
    except Exception:
        pass  # Column already exists

    await conn.commit()

    logger.info("DB initialized: %s (WAL mode)", path)
    return conn


async def get_read_connection(db_path: Path | None = None) -> aiosqlite.Connection:
    """Get a read-only connection for agents."""
    path = db_path or DB_PATH
    # SQLite URI mode for read-only
    uri = f"file:{path}?mode=ro"
    conn = await aiosqlite.connect(uri, uri=True)
    await conn.execute(f"PRAGMA busy_timeout={SQLITE_CONFIG['busy_timeout_ms']}")
    return conn


async def insert_candles_batch(conn: aiosqlite.Connection, candles: list[dict]) -> None:
    """Insert multiple candles in a single transaction."""
    await conn.executemany(
        """
        INSERT OR REPLACE INTO candles
            (symbol, timeframe, timestamp, open, high, low, close, volume,
             funding_rate, open_interest, liquidation_vol,
             bid_ask_spread, orderbook_imbalance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                c["symbol"], c["timeframe"], c["timestamp"],
                c["open"], c["high"], c["low"], c["close"], c["volume"],
                c.get("funding_rate"), c.get("open_interest"),
                c.get("liquidation_vol"), c.get("bid_ask_spread"),
                c.get("orderbook_imbalance"),
            )
            for c in candles
        ],
    )
    await conn.commit()


async def insert_candle(conn: aiosqlite.Connection, candle: dict) -> None:
    """Insert or replace a single candle row."""
    await conn.execute(
        """
        INSERT OR REPLACE INTO candles
            (symbol, timeframe, timestamp, open, high, low, close, volume,
             funding_rate, open_interest, liquidation_vol,
             bid_ask_spread, orderbook_imbalance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candle["symbol"],
            candle["timeframe"],
            candle["timestamp"],
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
            candle["volume"],
            candle.get("funding_rate"),
            candle.get("open_interest"),
            candle.get("liquidation_vol"),
            candle.get("bid_ask_spread"),
            candle.get("orderbook_imbalance"),
        ),
    )


async def get_candles(
    conn: aiosqlite.Connection,
    symbol: str,
    timeframe: str,
    limit: int = 200,
    before_ts: int | None = None,
) -> list[dict]:
    """Fetch recent candles for a symbol/timeframe."""
    if before_ts:
        cursor = await conn.execute(
            """
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume,
                   funding_rate, open_interest, liquidation_vol,
                   bid_ask_spread, orderbook_imbalance
            FROM candles
            WHERE symbol = ? AND timeframe = ? AND timestamp <= ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (symbol, timeframe, before_ts, limit),
        )
    else:
        cursor = await conn.execute(
            """
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume,
                   funding_rate, open_interest, liquidation_vol,
                   bid_ask_spread, orderbook_imbalance
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (symbol, timeframe, limit),
        )
    rows = await cursor.fetchall()
    columns = [
        "symbol", "timeframe", "timestamp", "open", "high", "low", "close", "volume",
        "funding_rate", "open_interest", "liquidation_vol",
        "bid_ask_spread", "orderbook_imbalance",
    ]
    return [dict(zip(columns, row)) for row in reversed(rows)]


async def get_active_positions(
    conn: aiosqlite.Connection, agent_id: str | None = None
) -> list[dict]:
    """Get all open positions, optionally filtered by agent."""
    if agent_id:
        cursor = await conn.execute(
            "SELECT * FROM positions WHERE status = 'OPEN' AND agent_id = ?",
            (agent_id,),
        )
    else:
        cursor = await conn.execute(
            "SELECT * FROM positions WHERE status = 'OPEN'",
        )
    rows = await cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in rows]
