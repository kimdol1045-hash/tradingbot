"""
Pipeline Runner — Agent orchestrator.
Runs the 5-Phase pipeline for each agent on candle close.
S1 → S2 → S3 → S4 sequential execution per symbol.
"""
from __future__ import annotations

import json
import logging
import time

from src.pipeline.models import Signal
from src.pipeline.phase1_safety import phase1_safety
from src.pipeline.phase2_read import phase2_read
from src.pipeline.phase3_scan import phase3_scan
from src.pipeline.phase4_gate import phase4_gate
from src.pipeline.phase5_execute import phase5_execute
from src.utils.config import AGENT_PROFILES, get_agent_symbols
from src.utils.market_stats import MarketStats, compute_stats
from src.utils.params import load_params

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Runs the 5-phase pipeline for all agents."""

    def __init__(self, candle_cache, position_manager=None, db=None, advisor=None):
        """
        Args:
            candle_cache: CandleCache with get(symbol, tf) method
            position_manager: PositionManager for signal execution
            db: aiosqlite connection for pipeline logging (optional)
            advisor: MarketAdvisor for AI-based leverage/SL adjustment (optional)
        """
        self.cache = candle_cache
        self.pm = position_manager
        self.db = db
        self.advisor = advisor

        # Per-agent state
        self.agent_states: dict[str, dict] = {}
        for agent_id in AGENT_PROFILES:
            self.agent_states[agent_id] = {
                "current_mdd": 0.0,
                "current_stage": "NORMAL",
                "stage_entered_ts": 0,
                "current_regime": "SIDEWAYS",
                "grace_counter": 0,
                "dna_history": {},
                "capital": 0.0,
                "open_risk": 0.0,
                "rolling_pf": 2.0,
                "consecutive_losses": 0,
                "initial_capital": 0.0,
                "open_positions_count": 0,
                "avg_atr_7d": 0.0,
            }

        # Pre-computed stats per symbol
        self._stats_cache: dict[str, MarketStats] = {}
        self._stats_ts: dict[str, int] = {}

    def set_capital(self, agent_id: str, capital: float):
        """Set agent capital allocation."""
        if agent_id in self.agent_states:
            self.agent_states[agent_id]["capital"] = capital
            # initial_capital is set once and never changes (for 단리 mode)
            if self.agent_states[agent_id].get("initial_capital", 0.0) == 0.0:
                self.agent_states[agent_id]["initial_capital"] = capital

    def update_mdd(self, agent_id: str, mdd: float):
        """Update current MDD for agent."""
        if agent_id in self.agent_states:
            self.agent_states[agent_id]["current_mdd"] = mdd

    def _get_stats(self, symbol: str) -> MarketStats:
        """Get or recompute market stats (refresh every 30min)."""
        now_ms = int(time.time() * 1000)
        last = self._stats_ts.get(symbol, 0)

        if now_ms - last > 30 * 60 * 1000 or symbol not in self._stats_cache:
            candles = self.cache.get(symbol, "5m")
            if candles and len(candles) >= 100:
                self._stats_cache[symbol] = compute_stats(candles, symbol, "5m")
                self._stats_ts[symbol] = now_ms
            else:
                self._stats_cache[symbol] = MarketStats(symbol=symbol)

        return self._stats_cache[symbol]

    def _build_candles_by_tf(
        self, symbol: str, timeframes: list[str],
    ) -> dict[str, list[dict]]:
        """Build candles dict per timeframe from cache."""
        result = {}
        for tf in timeframes:
            candles = self.cache.get(symbol, tf)
            result[tf] = candles if candles else []
        return result

    def _update_stage_state(self, agent_id: str, stage: str):
        """Update stage tracking in agent state."""
        state = self.agent_states[agent_id]
        if stage != state["current_stage"]:
            state["current_stage"] = stage
            state["stage_entered_ts"] = int(time.time() * 1000)

    def _update_regime_state(self, agent_id: str, regime: str, grace_counter: int):
        """Update regime tracking."""
        state = self.agent_states[agent_id]
        state["current_regime"] = regime
        state["grace_counter"] = grace_counter

    def run_agent(self, agent_id: str, symbol: str) -> Signal | None:
        """
        Run full 5-Phase pipeline for one agent × one symbol.
        Returns Signal if a trade should be executed, else None.
        """
        t0 = time.perf_counter()
        profile = AGENT_PROFILES.get(agent_id)
        if not profile:
            return None

        state = self.agent_states[agent_id]
        params = load_params(agent_id)
        stats = self._get_stats(symbol)
        candles_by_tf = self._build_candles_by_tf(symbol, profile.timeframes)

        primary_tf = profile.timeframes[0]
        primary_candles = candles_by_tf.get(primary_tf, [])
        if len(primary_candles) < 30:
            return None

        agent_config = {
            "agent_id": agent_id,
            "symbol": symbol,
            "timeframes": list(profile.timeframes),
        }

        # ━━━━ Phase 1: SAFETY ━━━━
        try:
            safety = phase1_safety(primary_candles, stats, state, params)
        except Exception:
            logger.exception("[%s/%s] Phase 1 SAFETY error — defaulting to blocked", agent_id, symbol)
            return None

        self._update_stage_state(agent_id, safety.stage)

        if safety.action == "CLOSE_ALL_AND_HALT":
            logger.warning("[%s/%s] EMERGENCY: %s", agent_id, symbol, safety.reason)
            return None  # Position manager handles close-all separately

        if safety.blocked:
            logger.debug("[%s/%s] Blocked: %s", agent_id, symbol, safety.reason)
            return None

        # ━━━━ Phase 2: READ ━━━━
        try:
            regime = phase2_read(candles_by_tf, safety, agent_config, state, params)
        except Exception:
            logger.exception("[%s/%s] Phase 2 READ error — skipping", agent_id, symbol)
            return None

        # ━━━━ Phase 3: SCAN ━━━━
        try:
            stats_dict = {
                "funding_p95": stats.funding_p97,
                "funding_p5": stats.funding_p3,
                "oi_change_p90": stats.oi_change_p97,
            }
            scan = phase3_scan(candles_by_tf, regime, safety, agent_config, params, stats_dict)
        except Exception:
            logger.exception("[%s/%s] Phase 3 SCAN error — skipping", agent_id, symbol)
            return None

        if not scan.found:
            logger.debug("[%s/%s] No pattern (score=%.1f)", agent_id, symbol, scan.score)
            return None

        # ━━━━ Phase 4: GATE ━━━━
        try:
            gate_stats = {
                "spread_p90": stats.spread_p95,
                "imbalance_p90": stats.imbalance_p95,
            }
            gate = phase4_gate(primary_candles, scan, regime, safety, state, params, gate_stats)
        except Exception:
            logger.exception("[%s/%s] Phase 4 GATE error — skipping", agent_id, symbol)
            return None

        if not gate.passed:
            logger.debug(
                "[%s/%s] Gate fail: score=%.1f < %.1f (%s)",
                agent_id, symbol, gate.score, gate.pass_threshold, gate.reason,
            )
            return None

        # ━━━━ Phase 5: EXECUTE ━━━━
        try:
            signal = phase5_execute(scan, regime, safety, gate, agent_config, state, params)
        except Exception:
            logger.exception("[%s/%s] Phase 5 EXECUTE error — skipping", agent_id, symbol)
            return None

        elapsed = (time.perf_counter() - t0) * 1000
        if signal:
            logger.info(
                "[%s/%s] SIGNAL: %s lev=%.1fx entry=%.2f sl=%.2f (%.0fms)",
                agent_id, symbol, signal.direction, signal.leverage,
                signal.entry_price, signal.stop_loss, elapsed,
            )
        else:
            logger.debug("[%s/%s] No signal from Phase 5 (%.0fms)", agent_id, symbol, elapsed)

        # Structured pipeline log
        self._log_pipeline_snapshot(
            agent_id, symbol, safety, regime, scan, gate, signal, elapsed,
        )

        return signal

    def _log_pipeline_snapshot(
        self, agent_id: str, symbol: str,
        safety, regime, scan, gate, signal, elapsed_ms: float,
    ):
        """Write structured JSON snapshot to pipeline_logs table."""
        if self.db is None:
            return

        snapshot = {
            "elapsed_ms": round(elapsed_ms, 1),
            "p1_safety": {
                "stage": safety.stage,
                "mdd_mode": safety.mdd_mode,
                "blocked": safety.blocked,
                "reason": safety.reason,
            },
            "p2_read": {
                "regime": regime.regime,
                "confidence": round(regime.confidence, 2),
                "alignment": round(regime.alignment, 2),
                "in_transition": regime.in_transition,
            },
            "p3_scan": {
                "found": scan.found,
                "score": scan.score,
                "primary_type": scan.primary_type,
                "direction": scan.direction,
                "mtf_grade": scan.mtf_grade,
                "atr": round(scan.atr, 2) if scan.atr else 0,
            },
        }

        if scan.found:
            snapshot["p4_gate"] = {
                "passed": gate.passed,
                "score": gate.score,
                "threshold": gate.pass_threshold,
                "mdd_mode": gate.mdd_mode,
                "size_mult": gate.size_mult,
                "reason": gate.reason,
            }

        if signal:
            snapshot["p5_execute"] = {
                "direction": signal.direction,
                "entry": signal.entry_price,
                "sl": signal.stop_loss,
                "leverage": signal.leverage,
                "notional": signal.notional_usd,
                "tp_count": len(signal.take_profits),
            }

        import asyncio
        try:
            asyncio.get_event_loop().create_task(
                self._write_log(agent_id, symbol, snapshot, signal is not None)
            )
        except RuntimeError:
            pass  # No event loop running (e.g., in sync tests)

    async def _write_log(
        self, agent_id: str, symbol: str, snapshot: dict, signal_generated: bool,
    ):
        """Async DB write for pipeline log."""
        try:
            await self.db.execute(
                """
                INSERT INTO pipeline_logs (timestamp, agent_id, symbol, phase_snapshot, signal_generated)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(time.time() * 1000), agent_id, symbol, json.dumps(snapshot), signal_generated),
            )
            await self.db.commit()
        except Exception:
            logger.debug("Pipeline log write failed", exc_info=True)

    def on_candle_close(self, symbol: str, timeframe: str):
        """
        Called when a candle closes. Runs pipeline for all relevant agents.
        Only primary TF (5m) triggers pipeline execution.
        """
        if timeframe != "5m":
            return

        signals: list[Signal] = []
        agent_order = ["s1", "s2", "s3", "s4"]

        # Fetch AI advisor multipliers once per symbol (shared across agents)
        ai_lev_mult = 1.0
        ai_sl_mult = 1.0
        if self.advisor:
            advice = self.advisor.get_advice(symbol)
            ai_lev_mult = advice["ai_leverage_mult"]
            ai_sl_mult = advice["ai_sl_mult"]

        for agent_id in agent_order:
            profile = AGENT_PROFILES.get(agent_id)
            if not profile:
                continue

            # Check if this agent trades this symbol
            allowed_symbols = get_agent_symbols(agent_id)
            if symbol not in allowed_symbols:
                continue

            # Inject AI advisor multipliers into agent state
            self.agent_states[agent_id]["ai_leverage_mult"] = ai_lev_mult
            self.agent_states[agent_id]["ai_sl_mult"] = ai_sl_mult

            signal = self.run_agent(agent_id, symbol)
            if signal:
                signals.append(signal)

        # Submit signals to position manager
        if self.pm and signals:
            for sig in signals:
                self.pm.submit_signal(sig)

        return signals
