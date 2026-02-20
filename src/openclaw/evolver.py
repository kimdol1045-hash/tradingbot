"""
OpenClaw Evolver — Layer 2 AI parameter optimizer.
Uses GPT-4o via OpenAI API to analyze trading performance and
adjust agent parameters every 4-hour cycle.

Cycle: collect metrics → build prompt → call GPT-4o → parse adjustments → write params
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import httpx

from src.utils.config import OPENAI_API_KEY
from src.utils.params import load_params, save_params

logger = logging.getLogger(__name__)

# OpenClaw proxy (local gateway) or direct OpenAI API
OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
USE_OPENCLAW = os.getenv("USE_OPENCLAW", "false").lower() == "true"

OPENAI_API_URL = (
    f"{OPENCLAW_GATEWAY_URL}/v1/chat/completions"
    if USE_OPENCLAW
    else "https://api.openai.com/v1/chat/completions"
)
MODEL = os.getenv("EVOLVER_MODEL", "openclaw" if USE_OPENCLAW else "gpt-4o")
CYCLE_INTERVAL_HOURS = float(os.getenv("EVOLVER_INTERVAL_HOURS", "4"))

# ═══ Adjustable Parameter Ranges ═══
# Evolver can only adjust within these bounds

PARAM_BOUNDS = {
    "dna.weights.hurst": (0.05, 0.50),
    "dna.weights.entropy": (0.05, 0.40),
    "dna.weights.liquidation": (0.05, 0.30),
    "dna.weights.funding": (0.05, 0.30),
    "dna.weights.oi_momentum": (0.05, 0.30),
    "dna.weights.liq_density": (0.05, 0.20),
    "gate.base_pass_scores.SIDEWAYS": (55, 85),
    "gate.base_pass_scores.WEAK_UPTREND": (50, 80),
    "gate.base_pass_scores.STRONG_UPTREND": (45, 75),
    "gate.base_pass_scores.WEAK_DOWNTREND": (50, 80),
    "gate.base_pass_scores.STRONG_DOWNTREND": (45, 75),
    "gate.base_pass_scores.VOLATILE": (70, 95),
    "exit.sl_atr_mult": (1.0, 3.0),
    "exit.trailing_atr_mult": (0.5, 2.5),
}


# ═══ Metrics Collection ═══

def _collect_metrics(agent_id: str, equity_tracker, position_manager) -> dict:
    """Collect performance metrics for GPT analysis."""
    eq = equity_tracker.agents.get(agent_id)
    if not eq:
        return {}

    # Recent trade analysis
    recent_trades = []
    if position_manager:
        for pos in list(position_manager.positions.values())[-20:]:
            if pos.status == "closed" and pos.signal.agent_id == agent_id:
                recent_trades.append({
                    "direction": pos.signal.direction,
                    "symbol": pos.signal.symbol,
                    "pnl": round(pos.pnl, 2),
                    "regime": pos.signal.regime,
                    "inflection_type": pos.signal.inflection_type,
                    "inflection_score": pos.signal.inflection_score,
                    "leverage": pos.signal.leverage,
                    "close_reason": pos.close_reason,
                })

    return {
        "agent_id": agent_id,
        "equity": round(eq.current_equity, 2),
        "initial_capital": round(eq.initial_capital, 2),
        "total_pnl": round(eq.current_equity - eq.initial_capital, 2),
        "mdd": round(eq.current_mdd, 4),
        "total_trades": eq.total_trades,
        "wins": eq.wins,
        "losses": eq.losses,
        "win_rate": round(eq.wins / max(eq.total_trades, 1), 3),
        "rolling_pf": round(eq.rolling_pf, 2),
        "consecutive_losses": eq.consecutive_losses,
        "recent_trades": recent_trades,
    }


# ═══ Prompt Engineering ═══

SYSTEM_PROMPT = """You are an AI trading system parameter optimizer. You analyze trading performance data and suggest parameter adjustments to improve profitability while managing risk.

Rules:
1. Return ONLY valid JSON with the adjustments. No explanation.
2. Each adjustment must have "path" (dot notation), "current", and "new" value.
3. DNA weights must sum to 1.0 after adjustment.
4. Changes should be conservative (max ±20% per cycle).
5. If performance is good (PF > 2.0, win_rate > 55%), make minimal changes.
6. If MDD > 5%, tighten risk parameters.
7. If consecutive_losses > 3, reduce gate thresholds slightly to explore more.
8. Focus on the weakest metric first.

Output format:
{
  "adjustments": [
    {"path": "dna.weights.hurst", "current": 0.25, "new": 0.28, "reason": "brief reason"},
    ...
  ],
  "analysis": "One sentence summary"
}"""


def _build_user_prompt(metrics: dict, current_params: dict) -> str:
    """Build the user prompt with metrics and current params."""
    relevant_params = {
        "dna": current_params.get("dna", {}),
        "gate": {
            "base_pass_scores": current_params.get("gate", {}).get("base_pass_scores", {}),
        },
        "exit": {
            "sl_atr_mult": current_params.get("exit", {}).get("sl_atr_mult"),
            "trailing_atr_mult": current_params.get("exit", {}).get("trailing_atr_mult"),
        },
    }

    return json.dumps({
        "metrics": metrics,
        "current_params": relevant_params,
        "param_bounds": {k: list(v) for k, v in PARAM_BOUNDS.items()},
    }, indent=2)


# ═══ GPT-4o API Call ═══

async def _call_gpt(system_prompt: str, user_prompt: str) -> dict | None:
    """Call LLM via OpenClaw proxy or direct OpenAI API."""
    if USE_OPENCLAW:
        if not OPENCLAW_GATEWAY_TOKEN:
            logger.warning("OpenClaw gateway token not configured")
            return None
        auth_token = OPENCLAW_GATEWAY_TOKEN
    else:
        if not OPENAI_API_KEY:
            logger.warning("OpenAI API key not configured")
            return None
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
        "max_tokens": 1000,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(OPENAI_API_URL, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.error("OpenAI API error: %d %s", resp.status_code, resp.text[:200])
                return None

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
    except json.JSONDecodeError:
        logger.error("Failed to parse GPT response as JSON")
        return None
    except Exception:
        logger.exception("GPT API call failed")
        return None


# ═══ Apply Adjustments ═══

def _set_nested(d: dict, path: str, value):
    """Set a value in a nested dict using dot notation path."""
    keys = path.split(".")
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def _get_nested(d: dict, path: str, default=None):
    """Get a value from a nested dict using dot notation path."""
    keys = path.split(".")
    for key in keys:
        if isinstance(d, dict):
            d = d.get(key, default)
        else:
            return default
    return d


def _validate_and_apply(params: dict, adjustments: list[dict]) -> tuple[dict, list[str]]:
    """
    Validate adjustments against bounds and apply them.
    Returns (updated_params, list of applied change descriptions).
    """
    applied = []

    for adj in adjustments:
        path = adj.get("path", "")
        new_val = adj.get("new")
        reason = adj.get("reason", "")

        if path not in PARAM_BOUNDS:
            logger.warning("Skipping unknown param path: %s", path)
            continue

        lo, hi = PARAM_BOUNDS[path]
        if not isinstance(new_val, (int, float)):
            continue

        new_val = max(lo, min(float(new_val), hi))

        current = _get_nested(params, path)
        if current is not None:
            # Max ±20% change per cycle (with minimum absolute change for near-zero values)
            min_abs_change = (hi - lo) * 0.05  # At least 5% of the param range
            max_change = max(abs(current) * 0.20, min_abs_change)
            if abs(new_val - current) > max_change:
                new_val = current + max_change * (1 if new_val > current else -1)
            # Re-clamp after change limiting
            new_val = max(lo, min(new_val, hi))

        _set_nested(params, path, round(new_val, 4))
        applied.append(f"{path}: {current} → {new_val:.4f} ({reason})")

    # Ensure DNA weights sum to 1.0
    weights = params.get("dna", {}).get("weights", {})
    if weights:
        w_sum = sum(weights.values())
        if w_sum > 0 and abs(w_sum - 1.0) > 0.001:
            for k in weights:
                weights[k] = round(weights[k] / w_sum, 4)
            # Re-clamp weights to their bounds after normalization
            for k in weights:
                bound_key = f"dna.weights.{k}"
                if bound_key in PARAM_BOUNDS:
                    blo, bhi = PARAM_BOUNDS[bound_key]
                    weights[k] = max(blo, min(weights[k], bhi))
            applied.append(f"DNA weights re-normalized (sum was {w_sum:.4f})")

    return params, applied


# ═══ Main Evolver ═══

class Evolver:
    """Layer 2 AI: GPT-4o powered parameter evolution."""

    def __init__(self, equity_tracker=None, position_manager=None):
        self.equity_tracker = equity_tracker
        self.position_manager = position_manager
        self._running = False
        self._last_cycle_ts = 0
        self.cycle_history: list[dict] = []

    async def run_cycle(self, agent_id: str) -> dict | None:
        """Run one evolution cycle for an agent."""
        if not self.equity_tracker:
            return None

        metrics = _collect_metrics(agent_id, self.equity_tracker, self.position_manager)
        if not metrics or metrics.get("total_trades", 0) < 5:
            logger.info("[Evolver] %s: not enough trades (%d), skipping",
                        agent_id, metrics.get("total_trades", 0))
            return None

        params = load_params(agent_id)
        if not params:
            logger.warning("[Evolver] %s: no params file, skipping", agent_id)
            return None
        user_prompt = _build_user_prompt(metrics, params)

        logger.info("[Evolver] %s: calling GPT-4o...", agent_id)
        result = await _call_gpt(SYSTEM_PROMPT, user_prompt)

        if not result or "adjustments" not in result:
            logger.warning("[Evolver] %s: no adjustments from GPT", agent_id)
            return None

        adjustments = result.get("adjustments", [])
        analysis = result.get("analysis", "")

        params, applied = _validate_and_apply(params, adjustments)

        if applied:
            save_params(agent_id, params)
            logger.info("[Evolver] %s: applied %d adjustments", agent_id, len(applied))
            for desc in applied:
                logger.info("  → %s", desc)

        cycle_result = {
            "agent_id": agent_id,
            "timestamp": int(time.time() * 1000),
            "metrics_snapshot": metrics,
            "adjustments": adjustments,
            "applied": applied,
            "analysis": analysis,
        }
        self.cycle_history.append(cycle_result)

        # Keep last 50 cycles
        if len(self.cycle_history) > 50:
            self.cycle_history = self.cycle_history[-50:]

        return cycle_result

    async def run_all_agents(self):
        """Run evolution cycle for all agents."""
        for agent_id in ["s1", "s2", "s3", "s4"]:
            try:
                await self.run_cycle(agent_id)
            except Exception:
                logger.exception("[Evolver] Cycle failed for %s", agent_id)

    async def run_loop(self, interval_hours: float = CYCLE_INTERVAL_HOURS):
        """Continuous evolution loop."""
        self._running = True
        interval_seconds = interval_hours * 3600
        logger.info("[Evolver] Started (interval: %.1fh)", interval_hours)

        while self._running:
            await asyncio.sleep(interval_seconds)

            now = int(time.time() * 1000)
            self._last_cycle_ts = now

            logger.info("[Evolver] Starting cycle...")
            await self.run_all_agents()
            logger.info("[Evolver] Cycle complete")

    def stop(self):
        self._running = False
