"""
Market Advisor — LLM-based dynamic leverage/SL adjustment.

Periodically analyzes market conditions across all symbols via GPT-4o,
outputting per-symbol multipliers that overlay on top of rule-based calculations.
Does NOT modify params.json — produces runtime multipliers only.

Safety: multipliers are hard-clamped, TTL-enforced, and default to 1.0 on failure.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import httpx
import numpy as np

from src.notify.telegram import notify_system
from src.utils.config import ALL_SYMBOLS, OPENAI_API_KEY

logger = logging.getLogger(__name__)

# ═══ Config ═══

_USE_OPENCLAW = os.getenv("USE_OPENCLAW", "false").lower() == "true"
_OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
_OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
MODEL = os.getenv("ADVISOR_MODEL", os.getenv("EVOLVER_MODEL", "gpt-4o"))

# Hard clamps
LEV_MULT_MIN, LEV_MULT_MAX = 0.3, 2.0
SL_MULT_MIN, SL_MULT_MAX = 0.5, 2.0

# TTL: if no update within this window, revert to 1.0
TTL_SECONDS = 2 * 3600  # 2 hours

DEFAULT_ADVICE = {"ai_leverage_mult": 1.0, "ai_sl_mult": 1.0, "reasoning": ""}

# ═══ LLM Prompts ═══

SYSTEM_PROMPT = """\
You are a crypto market risk advisor for a quantitative trading system.
Analyze current market conditions and output risk adjustment multipliers for each symbol.

Output strictly valid JSON:
{
  "assessments": [
    {
      "symbol": "BTC",
      "ai_leverage_mult": 1.0,
      "ai_sl_mult": 1.0,
      "reasoning": "brief reason"
    }
  ],
  "global_note": "overall assessment"
}

Multiplier guidelines:
- ai_leverage_mult (0.3 ~ 2.0): Multiplied against rule-based leverage.
  * High volatility, uncertainty, extreme funding -> 0.3-0.7 (reduce leverage)
  * Normal conditions -> 0.8-1.2
  * Clear trend, healthy volume, moderate volatility -> 1.3-2.0 (increase leverage)

- ai_sl_mult (0.5 ~ 2.0): Multiplied against ATR-based stop-loss distance.
  * Clean trend with momentum -> 0.5-0.8 (tighter SL, better RR)
  * Normal conditions -> 0.8-1.2
  * Choppy/volatile, potential wicks -> 1.3-2.0 (wider SL to avoid premature stops)

Principles:
- Be conservative by default (1.0 = no change).
- Only increase leverage in clearly favorable conditions.
- When BTC is volatile, altcoins are typically more affected.
- Extreme funding rates suggest crowded positioning -> reduce leverage.
- High ATR ratio (vs average) = elevated volatility -> reduce leverage, widen SL.
- Low volume relative to average suggests low liquidity -> reduce leverage.
"""


# ═══ LLM Call (reuses Evolver pattern) ═══

async def _call_llm(system_prompt: str, user_prompt: str) -> dict | None:
    """Call LLM (OpenClaw or OpenAI) and parse JSON response."""
    if _USE_OPENCLAW:
        if not _OPENCLAW_GATEWAY_TOKEN:
            logger.warning("[MarketAdvisor] OpenClaw gateway token not configured")
            return None
        api_url = f"{_OPENCLAW_GATEWAY_URL}/v1/chat/completions"
        auth_token = _OPENCLAW_GATEWAY_TOKEN
    else:
        if not OPENAI_API_KEY:
            logger.warning("[MarketAdvisor] OpenAI API key not configured")
            return None
        api_url = OPENAI_API_URL
        auth_token = OPENAI_API_KEY

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 1500,
    }
    if not _USE_OPENCLAW:
        payload["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(api_url, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.error("[MarketAdvisor] LLM API error: %d %s", resp.status_code, resp.text[:200])
                return None

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
    except json.JSONDecodeError:
        logger.error("[MarketAdvisor] Failed to parse LLM response as JSON")
        return None
    except Exception:
        logger.exception("[MarketAdvisor] LLM API call failed")
        return None


# ═══ MarketAdvisor ═══

class MarketAdvisor:
    """Periodic LLM-based market analysis -> leverage/SL multipliers."""

    def __init__(self, candle_cache, interval_hours: float = 1.0):
        self.cache = candle_cache
        self.interval_hours = interval_hours
        self._running = False
        self._advice: dict[str, dict] = {}  # symbol -> {ai_leverage_mult, ai_sl_mult, reasoning}
        self._last_update_ts: float = 0.0

    # ── Lifecycle ──

    async def run_loop(self):
        """Continuous advisory loop — add to asyncio.gather in main."""
        self._running = True
        interval_seconds = self.interval_hours * 3600
        logger.info("[MarketAdvisor] Started (interval: %.1fh, model: %s)", self.interval_hours, MODEL)

        while self._running:
            await asyncio.sleep(interval_seconds)
            try:
                await self.run_cycle()
            except Exception:
                logger.exception("[MarketAdvisor] Cycle failed")

    def stop(self):
        self._running = False

    # ── Core Cycle ──

    async def run_cycle(self):
        """Single analysis cycle: snapshot -> LLM -> parse -> store."""
        snapshot = self._build_market_snapshot()
        if not snapshot:
            logger.warning("[MarketAdvisor] No market data available, skipping cycle")
            return

        prompt = self._build_prompt(snapshot)
        result = await _call_llm(SYSTEM_PROMPT, prompt)
        if result is None:
            logger.warning("[MarketAdvisor] LLM call failed, keeping previous advice")
            return

        parsed = self._parse_response(result)
        if not parsed:
            logger.warning("[MarketAdvisor] Failed to parse LLM response")
            return

        self._advice = parsed
        self._last_update_ts = time.time()

        symbols_summary = ", ".join(
            f"{s}(L={a['ai_leverage_mult']:.1f},SL={a['ai_sl_mult']:.1f})"
            for s, a in sorted(parsed.items())
        )
        logger.info("[MarketAdvisor] Updated %d symbols: %s", len(parsed), symbols_summary)

        global_note = result.get("global_note", "")
        try:
            await notify_system(
                f"[MarketAdvisor] Updated {len(parsed)} symbols\n"
                f"{symbols_summary}\n"
                f"{global_note}"
            )
        except Exception:
            pass  # Non-critical

    # ── Market Snapshot ──

    def _build_market_snapshot(self) -> list[dict] | None:
        """Build compact market data snapshot for all symbols from 5m candles."""
        snapshots = []

        for symbol in ALL_SYMBOLS:
            candles = self.cache.get(symbol, "5m", limit=200)
            if len(candles) < 50:
                continue

            closes = np.array([c["close"] for c in candles], dtype=np.float64)
            highs = np.array([c["high"] for c in candles], dtype=np.float64)
            lows = np.array([c["low"] for c in candles], dtype=np.float64)
            volumes = np.array([c["volume"] for c in candles], dtype=np.float64)

            current_price = closes[-1]

            # Price changes (1h = 12 candles, 4h = 48 candles of 5m)
            change_1h = (closes[-1] / closes[-12] - 1) * 100 if len(closes) >= 12 else 0.0
            change_4h = (closes[-1] / closes[-48] - 1) * 100 if len(closes) >= 48 else 0.0

            # ATR: true range calculation
            tr = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(
                    np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:] - closes[:-1]),
                ),
            )
            # Recent ATR (last 14 bars ~1.2h) vs average ATR (all available)
            recent_atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))
            avg_atr = float(np.mean(tr))
            atr_ratio = recent_atr / avg_atr if avg_atr > 0 else 1.0

            # Volume: recent 1h vs overall average
            recent_vol = float(np.mean(volumes[-12:])) if len(volumes) >= 12 else float(np.mean(volumes))
            avg_vol = float(np.mean(volumes))
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

            # Latest auxiliary data
            latest = candles[-1]
            funding = latest.get("funding_rate") or 0.0
            spread = latest.get("bid_ask_spread") or 0.0

            # OI change over last 1h
            oi_values = [c.get("open_interest") or 0 for c in candles[-12:]]
            oi_nonzero = [v for v in oi_values if v > 0]
            if len(oi_nonzero) >= 2:
                oi_change_pct = (oi_nonzero[-1] / oi_nonzero[0] - 1) * 100
            else:
                oi_change_pct = 0.0

            snapshots.append({
                "symbol": symbol,
                "price": round(current_price, 2),
                "change_1h_pct": round(change_1h, 2),
                "change_4h_pct": round(change_4h, 2),
                "atr_ratio_vs_avg": round(atr_ratio, 2),
                "volume_ratio_vs_avg": round(vol_ratio, 2),
                "funding_rate_pct": round(funding * 100, 4),
                "oi_change_1h_pct": round(oi_change_pct, 2),
                "spread_bps": round(spread, 2),
            })

        return snapshots if snapshots else None

    # ── Prompt Builder ──

    def _build_prompt(self, snapshot: list[dict]) -> str:
        """Build user prompt from market snapshot."""
        lines = [f"Market snapshot (UTC {time.strftime('%Y-%m-%d %H:%M')}):\n"]

        for s in snapshot:
            lines.append(
                f"Symbol: {s['symbol']}\n"
                f"  Price: ${s['price']:,.2f} | 1h: {s['change_1h_pct']:+.2f}% | 4h: {s['change_4h_pct']:+.2f}%\n"
                f"  ATR ratio (vs avg): {s['atr_ratio_vs_avg']:.2f} | Volume ratio: {s['volume_ratio_vs_avg']:.2f}\n"
                f"  Funding: {s['funding_rate_pct']:+.4f}% | OI change 1h: {s['oi_change_1h_pct']:+.2f}% | Spread: {s['spread_bps']:.1f}bps"
            )

        lines.append(f"\nProvide risk assessment for all {len(snapshot)} symbols.")
        return "\n".join(lines)

    # ── Response Parser ──

    def _parse_response(self, raw: dict) -> dict[str, dict] | None:
        """Parse and validate LLM response, clamp multipliers."""
        assessments = raw.get("assessments")
        if not isinstance(assessments, list):
            logger.error("[MarketAdvisor] Invalid response: missing 'assessments' list")
            return None

        result: dict[str, dict] = {}
        for item in assessments:
            symbol = item.get("symbol", "")
            if not symbol:
                continue

            lev_mult = float(item.get("ai_leverage_mult", 1.0))
            sl_mult = float(item.get("ai_sl_mult", 1.0))

            # Hard clamp
            lev_mult = max(LEV_MULT_MIN, min(lev_mult, LEV_MULT_MAX))
            sl_mult = max(SL_MULT_MIN, min(sl_mult, SL_MULT_MAX))

            result[symbol] = {
                "ai_leverage_mult": round(lev_mult, 2),
                "ai_sl_mult": round(sl_mult, 2),
                "reasoning": str(item.get("reasoning", ""))[:200],
            }

        return result if result else None

    # ── Public API ──

    def get_advice(self, symbol: str) -> dict:
        """
        Get current advice for a symbol.

        Returns {ai_leverage_mult, ai_sl_mult, reasoning}.
        Falls back to 1.0 multipliers if no data or TTL expired.
        """
        # TTL check: revert to defaults if stale
        if self._last_update_ts > 0 and (time.time() - self._last_update_ts) > TTL_SECONDS:
            return dict(DEFAULT_ADVICE)

        return self._advice.get(symbol, dict(DEFAULT_ADVICE))
