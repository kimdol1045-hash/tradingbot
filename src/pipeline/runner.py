"""
Pipeline Runner — Agent orchestrator.
Runs the 5-Phase pipeline for each agent on candle close.
S1 → S2 → S3 → S4 sequential execution per symbol.
"""
from __future__ import annotations

import json
import logging
import time

from src.exchange.hyperliquid import fetch_max_leverages
from src.notify.telegram import notify_safety, notify_signal
from src.pipeline.models import Signal
from src.utils.async_helpers import safe_fire_and_forget
from src.pipeline.phase1_safety import phase1_safety
from src.pipeline.phase2_read import phase2_read
from src.pipeline.phase3_scan import phase3_scan
from src.pipeline.phase4_gate import phase4_gate
from src.pipeline.phase5_execute import phase5_execute
from src.utils.config import AGENT_PROFILES, MDD_POLICIES, get_agent_symbols
from src.utils.indicators import atr_single, candles_to_arrays
from src.utils.market_stats import MarketStats, compute_stats
from src.utils.params import load_params

logger = logging.getLogger(__name__)


class LossCircuitBreaker:
    """포트폴리오 전체 연속 패배 차단기."""

    def __init__(self, max_consecutive: int = 5, cooldown_minutes: int = 120):
        self.max_consecutive = max_consecutive
        self.cooldown_minutes = cooldown_minutes
        self._consecutive_losses = 0
        self._paused_until = 0.0

    def record_trade_result(self, pnl: float):
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        if self._consecutive_losses >= self.max_consecutive:
            self._paused_until = time.time() + self.cooldown_minutes * 60
            logger.warning(
                "CIRCUIT BREAKER: %d consecutive losses → %dmin pause",
                self._consecutive_losses, self.cooldown_minutes,
            )
            self._consecutive_losses = 0

    def is_blocked(self) -> tuple[bool, str]:
        if time.time() < self._paused_until:
            remaining = (self._paused_until - time.time()) / 60
            return True, f"CIRCUIT_BREAKER: {remaining:.0f}min remaining"
        return False, ""


class PipelineRunner:
    """Runs the 5-phase pipeline for all agents."""

    def __init__(self, candle_cache, position_manager=None, db=None, advisor=None, equity_tracker=None):
        """
        Args:
            candle_cache: CandleCache with get(symbol, tf) method
            position_manager: PositionManager for signal execution
            db: aiosqlite connection for pipeline logging (optional)
            advisor: MarketAdvisor for AI-based leverage/SL adjustment (optional)
            equity_tracker: EquityTracker for rolling PF/MDD/streak updates (optional)
        """
        self.cache = candle_cache
        self.pm = position_manager
        self.db = db
        self.advisor = advisor
        self.equity_tracker = equity_tracker

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
                "rolling_pf": None,  # Populated from DB on startup via equity_tracker.restore_from_db
                "consecutive_losses": 0,
                "initial_capital": 0.0,
                "open_positions_count": 0,
                "avg_atr_7d": 0.0,
            }

        # Portfolio-level circuit breaker
        self.circuit_breaker = LossCircuitBreaker(max_consecutive=5, cooldown_minutes=120)

        # Emergency halt tracking: {agent_id: halt_until_ts_ms}
        self._halt_until: dict[str, int] = {}

        # Pre-computed stats per symbol
        self._stats_cache: dict[str, MarketStats] = {}
        self._stats_ts: dict[str, int] = {}

        # Exchange max leverage cache (shared across agents)
        self._max_leverage_map: dict[str, int] = {}
        self._max_lev_ts: float = 0.0

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

    def _refresh_max_leverages(self):
        """Refresh exchange max leverage map (every 1 hour)."""
        now = time.time()
        if now - self._max_lev_ts > 3600 or not self._max_leverage_map:
            try:
                self._max_leverage_map = fetch_max_leverages()
                self._max_lev_ts = now
                logger.debug("Max leverage map refreshed: %d assets", len(self._max_leverage_map))
            except Exception:
                logger.debug("Max leverage refresh failed", exc_info=True)

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

    def run_agent(self, agent_id: str, symbol: str) -> Signal | None:
        """
        Run full 5-Phase pipeline for one agent × one symbol.
        Returns Signal if a trade should be executed, else None.
        """
        t0 = time.perf_counter()
        profile = AGENT_PROFILES.get(agent_id)
        if not profile:
            return None

        # Check emergency halt
        halt_ts = self._halt_until.get(agent_id, 0)
        if halt_ts > 0:
            now_ms = int(time.time() * 1000)
            if now_ms < halt_ts:
                remaining_h = (halt_ts - now_ms) / 3_600_000
                logger.debug("[%s/%s] Halted — %.1fh remaining", agent_id, symbol, remaining_h)
                return None
            # Halt expired, resume
            del self._halt_until[agent_id]
            logger.info("[%s] Emergency halt expired, resuming trading", agent_id)

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

        prev_stage = state.get("current_stage", "NORMAL")
        self._update_stage_state(agent_id, safety.stage)

        # Notify on safety stage change (escalation only)
        if safety.stage != prev_stage and safety.stage != "NORMAL":
            safe_fire_and_forget(
                notify_safety(safety.stage, safety.severity, safety.mdd_mode, safety.reason),
                name="notify_safety",
            )

            # Emergency tighten SL on stage escalation
            if self.pm and safety.stage in ("STAGE_1", "STAGE_2"):
                self.pm.emergency_tighten(safety.stage)

        if safety.action == "CLOSE_ALL_AND_HALT":
            logger.warning("[%s/%s] EMERGENCY: %s", agent_id, symbol, safety.reason)
            # Close all positions and set halt timer
            if self.pm and agent_id not in self._halt_until:
                safe_fire_and_forget(
                    self.pm.close_all_positions(agent_id, reason="MDD_EMERGENCY"),
                    name="emergency_close_all",
                )
                halt_hours = MDD_POLICIES.get("emergency", {}).get("halt_hours", 24)
                self._halt_until[agent_id] = int(time.time() * 1000) + int(halt_hours * 3600 * 1000)
                logger.warning("[%s] HALTED for %d hours (until %d)", agent_id, halt_hours, self._halt_until[agent_id])
            return None

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
                "funding_p95": max(stats.funding_p97, 0.0001),
                "funding_p5": min(stats.funding_p3, -0.0001),
                "oi_change_p90": max(stats.oi_change_p97, 3.0),
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
                "spread_p90": max(stats.spread_p95, 0.01),
                "imbalance_p90": max(stats.imbalance_p95, 0.3),
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
            # Send Telegram signal notification
            safe_fire_and_forget(notify_signal(signal), name="notify_signal")
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

        safe_fire_and_forget(
            self._write_log(agent_id, symbol, snapshot, signal is not None),
            name="pipeline_log",
        )

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

        # Portfolio circuit breaker check
        blocked, reason = self.circuit_breaker.is_blocked()
        if blocked:
            logger.info("Trading paused: %s", reason)
            return []

        signals: list[Signal] = []
        batch_symbols: set[str] = set()  # Track symbols already signaled in this batch
        agent_order = ["s1", "s2", "s3", "s4"]

        # Refresh exchange max leverage cache
        self._refresh_max_leverages()

        # Fetch AI advisor multipliers once per symbol (shared across agents)
        ai_lev_mult = 1.0
        ai_sl_mult = 1.0
        if self.advisor:
            advice = self.advisor.get_advice(symbol)
            ai_lev_mult = advice["ai_leverage_mult"]
            ai_sl_mult = advice["ai_sl_mult"]

        # Update avg_atr_7d EMA and 24h ATR from latest 5m candles
        candles_5m = self.cache.get(symbol, "5m", limit=300)
        if candles_5m and len(candles_5m) >= 30:
            arr = candles_to_arrays(candles_5m)
            current_atr = atr_single(arr["high"], arr["low"], arr["close"], period=14)

            # 24h ATR (288 × 5m candles) for SL calculation — wider, more stable
            atr_24h = atr_single(arr["high"], arr["low"], arr["close"], period=min(288, len(candles_5m) - 1))

            if current_atr > 0:
                for aid in agent_order:
                    if aid in self.agent_states:
                        prev = self.agent_states[aid].get("avg_atr_7d", 0.0)
                        if prev <= 0:
                            self.agent_states[aid]["avg_atr_7d"] = current_atr
                        else:
                            alpha = 0.01  # ~100 candle smoothing
                            self.agent_states[aid]["avg_atr_7d"] = prev * (1 - alpha) + current_atr * alpha
                        # Store 24h ATR per symbol for SL calculation
                        if atr_24h > 0:
                            sl_atr_map = self.agent_states[aid].setdefault("sl_atr_map", {})
                            sl_atr_map[symbol] = atr_24h

        for agent_id in agent_order:
            profile = AGENT_PROFILES.get(agent_id)
            if not profile:
                continue

            # Skip agents with no capital (disabled)
            if self.agent_states.get(agent_id, {}).get("capital", 0) <= 0:
                continue

            # Check if this agent trades this symbol
            allowed_symbols = get_agent_symbols(agent_id)
            if symbol not in allowed_symbols:
                continue

            # Sync equity tracker state → agent_states (rolling_pf, consecutive_losses, MDD)
            if self.equity_tracker:
                et_update = self.equity_tracker.get_agent_state_update(agent_id)
                if et_update:
                    self.agent_states[agent_id]["rolling_pf"] = et_update.get("rolling_pf")
                    self.agent_states[agent_id]["consecutive_losses"] = et_update.get("consecutive_losses", 0)
                    self.agent_states[agent_id]["current_mdd"] = et_update.get("current_mdd", 0.0)
                    # Update capital from real wallet balance if available
                    if et_update.get("capital", 0) > 0:
                        self.agent_states[agent_id]["capital"] = et_update["capital"]

            # Inject open position count, risk, and exposure into agent state
            if self.pm:
                open_pos = self.pm.get_open_positions(agent_id)
                all_open = self.pm.get_open_positions()  # all agents
                self.agent_states[agent_id]["open_positions_count"] = len(open_pos)
                self.agent_states[agent_id]["open_risk"] = self.pm.get_open_risk(agent_id)
                self.agent_states[agent_id]["portfolio_positions_count"] = len(all_open)

                # Per-symbol position counts and exposure
                sym_positions: dict[str, int] = {}
                coin_exposure: dict[str, float] = {}
                for pos in open_pos:
                    s = pos.signal.symbol
                    sym_positions[s] = sym_positions.get(s, 0) + 1
                    coin_exposure[s] = coin_exposure.get(s, 0.0) + pos.signal.notional_usd
                self.agent_states[agent_id]["symbol_positions"] = sym_positions
                self.agent_states[agent_id]["coin_exposure_usd"] = coin_exposure

                # Total margin used by this agent
                total_margin = sum(pos.signal.margin_usd for pos in open_pos)
                self.agent_states[agent_id]["total_margin_used"] = total_margin

                # Total capital across all agents
                total_capital = sum(
                    self.agent_states[aid].get("capital", 0)
                    for aid in self.agent_states
                )
                self.agent_states[agent_id]["total_capital"] = total_capital

            # Skip if this agent already has a pending/open position on this symbol
            if self.pm and self.pm.has_active_position(agent_id, symbol):
                continue

            # Skip if this agent+symbol is in rejection cooldown (recent margin failure)
            if self.pm and self.pm.is_rejected_cooldown(agent_id, symbol):
                logger.debug("[%s/%s] Skipped: rejection cooldown active", agent_id, symbol)
                continue

            # Skip if this agent+symbol is in SL cooldown (recent SL hit)
            if self.pm and self.pm.is_sl_cooldown(agent_id, symbol):
                logger.debug("[%s/%s] Skipped: SL cooldown active (2h)", agent_id, symbol)
                continue

            # Cross-agent symbol limit: max 1 agent per symbol
            if self.pm and self.pm.count_cross_agent_positions(symbol) >= 1:
                logger.debug("[%s/%s] Skipped: another agent already on this symbol", agent_id, symbol)
                continue

            # Also block if another agent already signaled this symbol in this batch
            if symbol in batch_symbols:
                logger.debug("[%s/%s] Skipped: another agent signaled this symbol in same batch", agent_id, symbol)
                continue

            # Inject AI advisor multipliers, max leverage map, and coin count into agent state
            self.agent_states[agent_id]["ai_leverage_mult"] = ai_lev_mult
            self.agent_states[agent_id]["ai_sl_mult"] = ai_sl_mult
            self.agent_states[agent_id]["max_leverage_map"] = self._max_leverage_map
            self.agent_states[agent_id]["num_available_coins"] = len(allowed_symbols)

            signal = self.run_agent(agent_id, symbol)
            if signal:
                signals.append(signal)
                batch_symbols.add(symbol)

        # Submit signals to position manager
        if self.pm and signals:
            for sig in signals:
                self.pm.submit_signal(sig)

        return signals
