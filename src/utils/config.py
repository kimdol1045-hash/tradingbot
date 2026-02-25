"""
Configuration: environment loading, agent profiles, symbol pool, and system constants.
"""

from __future__ import annotations

import logging
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

_logger = logging.getLogger(__name__)


# ═══ Multi-Wallet Config ═══

@dataclass(frozen=True)
class WalletConfig:
    """Per-agent wallet configuration."""
    agent_id: str
    private_key: str
    wallet_address: str
    sub_account_address: str = ""  # For Hyperliquid sub-accounts


def get_wallet_configs() -> dict[str, WalletConfig]:
    """
    Load per-agent wallet configs from environment.

    Each agent has its own API key (HYPERLIQUID_KEY_Sx) from its own
    Hyperliquid account. Falls back to global HYPERLIQUID_KEY.
    Wallet address is derived from the key, or overridden by WALLET_ADDRESS_Sx.
    Returns {agent_id: WalletConfig}.
    """
    configs: dict[str, WalletConfig] = {}
    global_key = os.getenv("HYPERLIQUID_KEY", "")

    for agent_id in ("s1", "s2", "s3", "s4"):
        env_var = f"HYPERLIQUID_KEY_{agent_id.upper()}"
        key = os.getenv(env_var, "") or global_key

        if not key:
            _logger.warning("No key for agent %s (checked %s and HYPERLIQUID_KEY)", agent_id, env_var)
            continue

        try:
            import eth_account  # lazy import for test compatibility
            wallet = eth_account.Account.from_key(key)
            derived_address = wallet.address

            # Use WALLET_ADDRESS_Sx for balance monitoring if set, else derived
            env_addr = os.getenv(f"WALLET_ADDRESS_{agent_id.upper()}", "")
            wallet_address = env_addr or derived_address

            configs[agent_id] = WalletConfig(
                agent_id=agent_id,
                private_key=key,
                wallet_address=wallet_address,
            )
            _logger.info("Agent %s: %s...", agent_id, wallet_address[:12])
        except Exception:
            _logger.warning("Invalid key for agent %s (%s)", agent_id, env_var)

    return configs
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# ═══ Wallet Addresses (read-only balance monitoring) ═══

def get_wallet_addresses() -> dict[str, str]:
    """
    Load per-agent wallet addresses from environment.
    Used for balance monitoring — no private key needed.
    Falls back to addresses derived from HYPERLIQUID_KEY_Sx if WALLET_ADDRESS_Sx is not set.
    Returns {agent_id: address}.
    """
    addresses: dict[str, str] = {}
    for agent_id in ("s1", "s2", "s3", "s4"):
        addr = os.getenv(f"WALLET_ADDRESS_{agent_id.upper()}", "")
        if addr:
            addresses[agent_id] = addr
    return addresses


def _safe_int(env_key: str, default: int) -> int:
    """Parse int env var, return default on invalid value."""
    raw = os.getenv(env_key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        _logger.warning("Invalid int for %s=%r, using default %d", env_key, raw, default)
        return default


def _safe_float(env_key: str, default: float) -> float:
    """Parse float env var, return default on invalid value."""
    raw = os.getenv(env_key, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("Invalid float for %s=%r, using default %.2f", env_key, raw, default)
        return default


# ═══ Screener Schedule ═══

SCREENER_INTERVAL_HOURS = _safe_int("SCREENER_INTERVAL_HOURS", 72)
SCREENER_START_HOUR_UTC = _safe_int("SCREENER_START_HOUR_UTC", 3)
SCREENER_MIN_VOLUME_M = _safe_float("SCREENER_MIN_VOLUME_M", 1.0)

# ═══ Symbol Pool ═══

SYMBOL_POOL: dict[str, list[str]] = {
    "tier_1": ["BTC", "ETH"],
    "tier_2": ["SOL", "XRP"],
    "tier_3": ["DOGE", "AVAX", "LINK"],
}

ALL_SYMBOLS: list[str] = [s for tier in SYMBOL_POOL.values() for s in tier]


def reload_symbol_pool() -> dict[str, list[str]]:
    """Reload SYMBOL_POOL and ALL_SYMBOLS from symbols.json if it exists."""
    global SYMBOL_POOL, ALL_SYMBOLS
    import json

    symbols_path = PROJECT_ROOT / "symbols.json"
    if not symbols_path.exists():
        _logger.warning("symbols.json not found, keeping default pool")
        return SYMBOL_POOL

    try:
        with open(symbols_path) as f:
            data = json.load(f)

        pool = data.get("symbol_pool", {})
        if not pool:
            _logger.warning("symbols.json has empty symbol_pool, keeping default")
            return SYMBOL_POOL

        # Validate structure: each tier must be a list of strings
        valid_pool: dict[str, list[str]] = {}
        for tier, symbols in pool.items():
            if not isinstance(symbols, list):
                _logger.warning("symbols.json tier '%s' is not a list, skipping", tier)
                continue
            valid_symbols = [s for s in symbols if isinstance(s, str) and s]
            if valid_symbols:
                valid_pool[tier] = valid_symbols

        if not valid_pool:
            _logger.warning("symbols.json has no valid tiers, keeping default")
            return SYMBOL_POOL

        SYMBOL_POOL = valid_pool
        ALL_SYMBOLS = [s for tier in SYMBOL_POOL.values() for s in tier]
        _logger.info(
            "Symbol pool reloaded: %s",
            {k: len(v) for k, v in SYMBOL_POOL.items()},
        )
        return SYMBOL_POOL
    except Exception:
        _logger.warning("Failed to reload symbols.json, keeping default", exc_info=True)
        return SYMBOL_POOL

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
        capital_pct=0.25,
        allowed_tiers=["tier_1", "tier_2"],
        max_symbols=12,
        max_open_risk_pct=0.03,
        description="Scalper (5m only)",
    ),
    "s2": AgentProfile(
        agent_id="s2",
        timeframes=["5m", "15m"],
        capital_pct=0.25,
        allowed_tiers=["tier_1", "tier_2"],
        max_symbols=12,
        max_open_risk_pct=0.04,
        description="Short-term trader (5m+15m)",
    ),
    "s3": AgentProfile(
        agent_id="s3",
        timeframes=["5m", "15m", "1h"],
        capital_pct=0.25,
        allowed_tiers=["tier_1", "tier_2", "tier_3"],
        max_symbols=20,
        max_open_risk_pct=0.05,
        description="Swing trader (5m+15m+1h)",
    ),
    "s4": AgentProfile(
        agent_id="s4",
        timeframes=["5m", "15m", "1h", "4h"],
        capital_pct=0.25,
        allowed_tiers=["tier_1", "tier_2"],
        max_symbols=12,
        max_open_risk_pct=0.05,
        description="Position trader (5m+15m+1h+4h)",
    ),
}


def get_agent_symbols(agent_id: str) -> list[str]:
    """Return allowed symbols for an agent based on tier policy."""
    profile = AGENT_PROFILES[agent_id]
    symbols = []
    for tier in profile.allowed_tiers:
        symbols.extend(SYMBOL_POOL.get(tier, []))
    result = symbols[: profile.max_symbols]
    if not result:
        _logger.warning("Agent %s has no symbols (tiers=%s, pool keys=%s)", agent_id, profile.allowed_tiers, list(SYMBOL_POOL.keys()))
    return result


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
        "range": [0.05, 1.00],
        "leverage_mult": 0.4,
        "size_mult": 0.5,
        "score_adj": 15,
        "allowed_regimes": ["STRONG_UPTREND", "STRONG_DOWNTREND"],
    },
}


def get_mdd_mode(drawdown_pct: float) -> str:
    """Determine MDD mode from current drawdown percentage (0.0~1.0)."""
    if drawdown_pct < 0:
        return "normal"
    for mode, policy in MDD_POLICIES.items():
        r = policy.get("range")
        if r and r[0] <= drawdown_pct < r[1]:
            return mode
    return "survival"


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
    "portfolio_max_open_risk_pct": 0.20,
    "min_available_pct": 0.10,
}

# ═══ SQLite Config ═══

SQLITE_CONFIG = {
    "journal_mode": "WAL",
    "busy_timeout_ms": 5000,
}

# ═══ Exposure Limits ═══

EXPOSURE_LIMITS = {
    "max_positions_per_agent": 4,
    "max_positions_per_symbol": 1,
    "max_portfolio_positions": 16,
    "max_single_coin_exposure_pct": 0.50,
    "margin_pct_per_position": 0.25,
}
