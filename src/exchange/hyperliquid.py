"""
Hyperliquid API wrapper — REST + WebSocket helpers.
Thin layer over hyperliquid-python-sdk for data fetching and order execution.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from src.utils.config import HYPERLIQUID_KEY, HYPERLIQUID_SECRET, IS_TESTNET

logger = logging.getLogger(__name__)


def _get_base_url() -> str:
    return constants.TESTNET_API_URL if IS_TESTNET else constants.MAINNET_API_URL


def get_info_client(skip_ws: bool = True, max_retries: int = 5) -> Info:
    """Create a read-only Info client with retry on rate limit."""
    for attempt in range(1, max_retries + 1):
        try:
            return Info(_get_base_url(), skip_ws=skip_ws)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning("Info client rate limited, retrying in %ds (%d/%d)", wait, attempt, max_retries)
                time.sleep(wait)
            else:
                raise


def get_exchange_client(
    private_key: str | None = None,
    account_address: str | None = None,
) -> Exchange | None:
    """Create an Exchange client for order execution. Returns None if no key.

    Args:
        private_key: Hex private key. Falls back to global HYPERLIQUID_KEY if None.
        account_address: Sub-account address for Hyperliquid sub-accounts.
    """
    key = private_key or HYPERLIQUID_KEY
    if not key:
        logger.warning("No HYPERLIQUID_KEY set, exchange client unavailable")
        return None
    wallet = eth_account.Account.from_key(key)
    return Exchange(wallet, _get_base_url(), account_address=account_address)


# ═══ Data Fetching ═══

def fetch_candles(
    info: Info,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int | None = None,
    max_retries: int = 3,
) -> list[dict]:
    """
    Fetch OHLCV candles via REST with retry on rate limit.
    Returns list of dicts with keys: timestamp, open, high, low, close, volume.
    """
    if end_ms is None:
        end_ms = int(time.time() * 1000)
    raw = None
    for attempt in range(1, max_retries + 1):
        try:
            raw = info.candles_snapshot(symbol, interval, start_ms, end_ms)
            break
        except Exception as e:
            if "429" in str(e) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning("fetch_candles rate limited (%s %s), retrying in %ds (%d/%d)", symbol, interval, wait, attempt, max_retries)
                time.sleep(wait)
            else:
                raise
    if raw is None:
        return []
    candles = []
    for c in raw:
        candles.append({
            "symbol": symbol,
            "timeframe": interval,
            "timestamp": int(c["t"]),
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c["v"]),
        })
    return candles


def fetch_all_mids(info: Info) -> dict[str, float]:
    """Fetch mid prices for all symbols. Returns {symbol: mid_price}."""
    mids = info.all_mids()
    if not mids:
        return {}
    return {k: float(v) for k, v in mids.items()}


def fetch_asset_contexts(info: Info) -> dict[str, dict]:
    """
    Fetch live market context for all assets.
    Returns {symbol: {funding, open_interest, mark_price, day_volume, ...}}.
    """
    result = info.meta_and_asset_ctxs()
    meta = result[0]
    ctxs = result[1]
    universe = meta["universe"]

    asset_map = {}
    for i, ctx in enumerate(ctxs):
        symbol = universe[i]["name"]
        asset_map[symbol] = {
            "funding_rate": float(ctx.get("funding", 0)),
            "open_interest": float(ctx.get("openInterest", 0)),
            "mark_price": float(ctx.get("markPx", 0)),
            "oracle_price": float(ctx.get("oraclePx", 0)),
            "day_volume": float(ctx.get("dayNtlVlm", 0)),
            "premium": float(ctx.get("premium") or 0),
        }
    return asset_map


def fetch_orderbook(info: Info, symbol: str, depth: int = 5) -> dict:
    """
    Fetch L2 orderbook and compute spread + imbalance.
    Returns {best_bid, best_ask, spread_bps, imbalance, bid_vol, ask_vol}.
    """
    l2 = info.l2_snapshot(symbol)
    bids = l2["levels"][0]
    asks = l2["levels"][1]

    if not bids or not asks:
        return {
            "best_bid": 0.0, "best_ask": 0.0,
            "spread_bps": 0.0, "imbalance": 0.0,
            "bid_vol": 0.0, "ask_vol": 0.0,
        }

    best_bid = float(bids[0]["px"])
    best_ask = float(asks[0]["px"])
    mid = (best_bid + best_ask) / 2.0
    spread_bps = ((best_ask - best_bid) / mid) * 10000 if mid > 0 else 0.0

    bid_vol = sum(float(b["sz"]) for b in bids[:depth])
    ask_vol = sum(float(a["sz"]) for a in asks[:depth])
    total_vol = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": spread_bps,
        "imbalance": imbalance,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
    }


def fetch_funding_history(
    info: Info,
    symbol: str,
    start_ms: int,
    end_ms: int | None = None,
) -> list[dict]:
    """Fetch historical funding rates for a symbol."""
    raw = info.funding_history(symbol, start_ms, end_ms)
    return [
        {
            "timestamp": int(r["time"]),
            "symbol": symbol,
            "funding_rate": float(r["fundingRate"]),
        }
        for r in raw
    ]


# ═══ Order Execution ═══

def place_market_order(
    exchange: Exchange,
    symbol: str,
    is_buy: bool,
    size: float,
    slippage: float = 0.05,
) -> dict[str, Any]:
    """Place a market order. Returns order result dict."""
    result = exchange.market_open(symbol, is_buy, size, slippage=slippage)
    logger.info(
        "Market order: %s %s %.4f → %s",
        "BUY" if is_buy else "SELL", symbol, size, result.get("status"),
    )
    return result


def place_trigger_order(
    exchange: Exchange,
    symbol: str,
    is_buy: bool,
    size: float,
    trigger_price: float,
    tpsl: str,
    is_market: bool = True,
) -> dict[str, Any]:
    """
    Place a TP or SL trigger order (reduce-only).
    tpsl: 'tp' or 'sl'
    """
    trigger_price = round_price(trigger_price)
    order_type = {
        "trigger": {
            "triggerPx": float(trigger_price),
            "isMarket": is_market,
            "tpsl": tpsl,
        }
    }
    result = exchange.order(
        symbol,
        is_buy,
        size,
        trigger_price,
        order_type,
        reduce_only=True,
    )
    logger.info(
        "Trigger order (%s): %s %s %.4f @ trigger %.2f → %s",
        tpsl.upper(), "BUY" if is_buy else "SELL", symbol, size, trigger_price,
        result.get("status"),
    )
    return result


def place_entry_with_sl_tp(
    exchange: Exchange,
    symbol: str,
    is_buy: bool,
    size: float,
    limit_price: float,
    sl_price: float,
    tp_prices: list[float],
    tp_sizes: list[float],
) -> dict[str, Any]:
    """
    Place entry + SL + multiple TP orders atomically.
    Uses positionTpsl grouping for position-level TP/SL.
    """
    close_side = not is_buy
    # Round all prices to 5 significant figures (Hyperliquid requirement)
    limit_price = round_price(limit_price)
    sl_price = round_price(sl_price)
    tp_prices = [round_price(p) for p in tp_prices]

    orders = [
        {
            "coin": symbol,
            "is_buy": is_buy,
            "sz": size,
            "limit_px": limit_price,
            "order_type": {"limit": {"tif": "Ioc"}},
            "reduce_only": False,
        },
        {
            "coin": symbol,
            "is_buy": close_side,
            "sz": size,
            "limit_px": sl_price,
            "order_type": {
                "trigger": {
                    "triggerPx": float(sl_price),
                    "isMarket": True,
                    "tpsl": "sl",
                }
            },
            "reduce_only": True,
        },
    ]
    for tp_px, tp_sz in zip(tp_prices, tp_sizes):
        orders.append({
            "coin": symbol,
            "is_buy": close_side,
            "sz": tp_sz,
            "limit_px": tp_px,
            "order_type": {
                "trigger": {
                    "triggerPx": float(tp_px),
                    "isMarket": True,
                    "tpsl": "tp",
                }
            },
            "reduce_only": True,
        })

    result = exchange.bulk_orders(orders, grouping="positionTpsl")
    logger.info(
        "Entry+SL+TP: %s %s %.4f, SL=%.2f, TPs=%s → %s",
        "LONG" if is_buy else "SHORT", symbol, size, sl_price,
        tp_prices, result.get("status"),
    )
    return result


def cancel_order(exchange: Exchange, symbol: str, oid: int) -> dict[str, Any]:
    """Cancel an order by ID."""
    return exchange.cancel(symbol, oid)


def update_leverage(
    exchange: Exchange, symbol: str, leverage: int, is_cross: bool = True
) -> dict[str, Any]:
    """Update leverage for a symbol."""
    return exchange.update_leverage(leverage, symbol, is_cross)


def get_user_state(info: Info, address: str, max_retries: int = 3) -> dict[str, Any]:
    """Get user's positions, margin, and withdrawable balance.

    Retries on 429 rate limit with exponential backoff.
    Raises on persistent failure so callers can distinguish API error from empty state.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return info.user_state(address)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "get_user_state rate limited, retrying in %ds (%d/%d)",
                    wait, attempt, max_retries,
                )
                time.sleep(wait)
            else:
                raise


def fetch_wallet_balance(info: Info, address: str) -> float:
    """Fetch wallet's total account value (equity) in USD.

    Checks both perps and spot accounts for unified account compatibility.
    """
    state = info.user_state(address)
    margin = state.get("marginSummary", {})
    perp_value = float(margin.get("accountValue", 0))

    # Also check spot USDC for unified accounts
    spot_usdc = 0.0
    try:
        spot_state = info.spot_user_state(address)
        for bal in spot_state.get("balances", []):
            if bal.get("coin") == "USDC":
                spot_usdc = float(bal.get("total", 0))
                break
    except Exception:
        pass

    # Unified accounts: spot includes held perp margin, don't double count
    if spot_usdc > 0 and perp_value > 0:
        return spot_usdc
    return perp_value + spot_usdc


def get_open_orders(info: Info, address: str) -> list[dict]:
    """Get user's open orders."""
    return info.open_orders(address)


def get_frontend_open_orders(info: Info, address: str, max_retries: int = 3) -> list[dict]:
    """Get user's open orders including trigger (SL/TP) orders.

    Retries on 429 rate limit with exponential backoff.
    Raises on persistent failure so callers can distinguish API error from empty state.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return info.frontend_open_orders(address)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "get_frontend_open_orders rate limited, retrying in %ds (%d/%d)",
                    wait, attempt, max_retries,
                )
                time.sleep(wait)
            else:
                raise


# ═══ Asset Metadata Cache (szDecimals, maxLeverage) ═══

_asset_meta_cache: dict[str, dict] = {}
_asset_meta_ts: float = 0.0
_ASSET_META_TTL = 3600  # Refresh every 1 hour


def _refresh_asset_meta(info: Info | None = None) -> dict[str, dict]:
    """Fetch and cache per-asset metadata (szDecimals, maxLeverage)."""
    global _asset_meta_cache, _asset_meta_ts

    now = time.time()
    if _asset_meta_cache and (now - _asset_meta_ts) < _ASSET_META_TTL:
        return _asset_meta_cache

    if info is None:
        info = get_info_client()

    try:
        meta = info.meta()
        universe = meta.get("universe", [])
        result = {}
        for asset in universe:
            name = asset.get("name", "")
            if name:
                result[name] = {
                    "szDecimals": int(asset.get("szDecimals", 0)),
                    "maxLeverage": int(asset.get("maxLeverage", 50)),
                }
        _asset_meta_cache = result
        _asset_meta_ts = now
        logger.debug("Asset meta cache refreshed: %d assets", len(result))
    except Exception:
        logger.warning("Failed to fetch asset meta", exc_info=True)

    return _asset_meta_cache


def round_price(price: float, sig_figs: int = 5) -> float:
    """Round price to N significant figures (Hyperliquid requires max 5).

    Caps decimal places so total significant figures never exceeds sig_figs.
    """
    import math
    if price == 0:
        return 0.0
    decimals = max(0, sig_figs - 1 - int(math.floor(math.log10(abs(price)))))
    return round(price, decimals)


def round_size(symbol: str, size: float, info: Info | None = None) -> float:
    """Round order size to the asset's szDecimals precision."""
    meta = _refresh_asset_meta(info)
    asset = meta.get(symbol, {})
    decimals = asset.get("szDecimals", 0)
    factor = 10 ** decimals
    return int(size * factor) / factor  # Truncate (not round up)


# ═══ Max Leverage Cache ═══

_max_leverage_cache: dict[str, int] = {}
_max_leverage_ts: float = 0.0
_MAX_LEVERAGE_TTL = 3600  # Refresh every 1 hour


def fetch_max_leverages(info: Info | None = None) -> dict[str, int]:
    """
    Fetch max leverage per coin from Hyperliquid meta endpoint.
    Returns {symbol: max_leverage}. Cached for 1 hour.
    """
    global _max_leverage_cache, _max_leverage_ts

    now = time.time()
    if _max_leverage_cache and (now - _max_leverage_ts) < _MAX_LEVERAGE_TTL:
        return _max_leverage_cache

    if info is None:
        info = get_info_client()

    try:
        meta = info.meta()
        universe = meta.get("universe", [])
        result = {}
        for asset in universe:
            name = asset.get("name", "")
            max_lev = int(asset.get("maxLeverage", 50))
            if name:
                result[name] = max_lev
        _max_leverage_cache = result
        _max_leverage_ts = now
        logger.debug("Max leverage cache refreshed: %d assets", len(result))
    except Exception:
        logger.warning("Failed to fetch max leverages", exc_info=True)

    return _max_leverage_cache


def get_max_leverage(symbol: str, info: Info | None = None) -> int:
    """Get max leverage for a specific coin. Returns 50 if unknown."""
    cache = fetch_max_leverages(info)
    return cache.get(symbol, 50)
