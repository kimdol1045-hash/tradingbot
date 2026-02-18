"""
Configuration: environment loading, agent profiles, symbol pool, and system constants.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ── Load .env ──
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH)

# ── Paths ──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("DB_PATH", "data/trades.db"))
if not DB_PATH.is_absolute():
    DB_PATH = PROJECT_ROOT / DB_PATH
PARAMS_DIR = PROJECT_ROOT / "params"

# ── Environment ──
ENV = os.getenv("ENV", "local")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
IS_TESTNET = os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"

# ── API Keys ──
HYPERLIQUID_KEY = os.getenv("HYPERLIQUID_KEY", "")
HYPERLIQUID_SECRET = os.getenv("HYPERLIQUID_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# ═══ Symbol Pool ═══

SYMBOL_POOL: dict[str, list[str]] = {
    "tier_1": ["BTC", "ETH"],
    "tier_2": ["SOL", "XRP"],
    "tier_3": ["DOGE", "AVAX", "LINK"],
}

ALL_SYMBOLS: list[str] = [s for tier in SYMBOL_POOL.values() for s in tier]

# ═══ Timeframes ═══

TIMEFRAMES = ["5m", "15m", "1h", "4h"]

TIMEFRAME_MINUTES: dict[str, int] = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
}


# ═══ Agent Profiles ═══

@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    timeframes: list[str] = field(default_factory=list)
    capital_pct: float = 0.0
    allowed_tiers: list[str] = field(default_factory=list)
    max_symbols: int = 2
    max_open_risk_pct: float = 0.03
    description: str = ""


AGENT_PROFILES: dict[str, AgentProfile] = {
    "s1": AgentProfile(
        agent_id="s1",
        timeframes=["5m"],
        capital_pct=0.15,
        allowed_tiers=["tier_1"],
        max_symbols=2,
        max_open_risk_pct=0.03,
        description="Scalper (5m only)",
    ),
    "s2": AgentProfile(
        agent_id="s2",
        timeframes=["5m", "15m"],
        capital_pct=0.25,
        allowed_tiers=["tier_1", "tier_2"],
        max_symbols=4,
        max_open_risk_pct=0.04,
        description="Short-term trader (5m+15m)",
    ),
    "s3": AgentProfile(
        agent_id="s3",
        timeframes=["5m", "15m", "1h"],
        capital_pct=0.30,
        allowed_tiers=["tier_1", "tier_2", "tier_3"],
        max_symbols=6,
        max_open_risk_pct=0.05,
        description="Swing trader (5m+15m+1h)",
    ),
    "s4": AgentProfile(
        agent_id="s4",
        timeframes=["5m", "15m", "1h", "4h"],
        capital_pct=0.30,
        allowed_tiers=["tier_1", "tier_2"],
        max_symbols=4,
        max_open_risk_pct=0.05,
        description="Position trader (5m+15m+1h+4h)",
    ),
}


def get_agent_symbols(agent_id: str) -> list[str]:
    """Return allowed symbols for an agent based on tier policy."""
    profile = AGENT_PROFILES[agent_id]
    symbols = []
    for tier in profile.allowed_tiers:
        symbols.extend(SYMBOL_POOL[tier])
    return symbols[: profile.max_symbols]


# ═══ MDD Policies ═══

MDD_POLICIES: dict[str, dict] = {
    "normal": {
        "range": [0.00, 0.03],
        "leverage_mult": 1.0,
        "size_mult": 1.0,
        "score_adj": 0,
    },
    "caution": {
        "range": [0.03, 0.05],
        "leverage_mult": 0.7,
        "size_mult": 0.7,
        "score_adj": 5,
    },
    "defensive": {
        "range": [0.05, 0.08],
        "leverage_mult": 0.4,
        "size_mult": 0.5,
        "score_adj": 15,
        "allowed_regimes": ["STRONG_UPTREND", "STRONG_DOWNTREND"],
    },
    "survival": {
        "range": [0.08, 0.10],
        "leverage_mult": 0.2,
        "size_mult": 0.3,
        "score_adj": 25,
        "max_positions": 1,
    },
    "emergency": {
        "range": [0.10, 1.00],
        "action": "CLOSE_ALL_AND_HALT",
        "halt_hours": 24,
    },
}


def get_mdd_mode(drawdown_pct: float) -> str:
    """Determine MDD mode from current drawdown percentage (0.0~1.0)."""
    for mode, policy in MDD_POLICIES.items():
        r = policy.get("range")
        if r and r[0] <= drawdown_pct < r[1]:
            return mode
    return "emergency"


# ═══ Cost Model ═══

COST_MODEL = {
    "slippage_bps": {
        "BTC": 1.5,
        "ETH": 2.0,
        "SOL": 3.0,
        "XRP": 3.0,
        "DEFAULT": 5.0,
    },
    "fee_bps": {
        "taker": 3.5,
        "maker": 1.0,
    },
    "funding_interval_hours": 8,
    "backtest_conservative_mult": 1.5,
}

# ═══ Open Risk Budget ═══

OPEN_RISK_PARAMS = {
    "portfolio_max_open_risk_pct": 0.08,
    "min_available_pct": 0.30,
}

# ═══ SQLite Config ═══

SQLITE_CONFIG = {
    "journal_mode": "WAL",
    "busy_timeout_ms": 5000,
}
