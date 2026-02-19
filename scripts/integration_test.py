"""
Integration Test — full pipeline end-to-end verification in DRY_RUN mode.

Tests:
  1. DB initialization
  2. Pipeline phases (synthetic data)
  3. Signal generation → executor → DB recording
  4. Reconciliation with empty exchange
  5. Position manager lifecycle
  6. Analyze script (pattern/correlation)

Usage:
    python scripts/integration_test.py
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Suppress most logging during tests
import logging
logging.basicConfig(level=logging.WARNING)

PASS = "✓"
FAIL = "✗"
results: list[tuple[str, bool, str]] = []


def report(name: str, passed: bool, detail: str = ""):
    results.append((name, passed, detail))
    mark = PASS if passed else FAIL
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))


async def test_db_init():
    """Test DB schema creation."""
    import aiosqlite
    db = await aiosqlite.connect(":memory:")
    from src.utils.db import SCHEMA_SQL
    await db.executescript(SCHEMA_SQL)

    # Check tables exist
    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await cursor.fetchall()}
    expected = {"candles", "trades", "positions", "equity_curve", "pipeline_logs"}
    missing = expected - tables
    report("DB schema", not missing, f"missing: {missing}" if missing else f"{len(expected)} tables")
    return db


async def test_pipeline_phases(db):
    """Test all 5 pipeline phases with synthetic data."""
    random.seed(42)

    # Generate candles with structure
    candles = []
    price = 50000.0
    for i in range(100):
        o = price
        if price > 50500:
            bias = -0.001
        elif price < 49500:
            bias = 0.001
        else:
            bias = random.gauss(0, 0.0002)
        c = price * (1 + bias + random.gauss(0, 0.002))
        h = max(o, c) * (1 + abs(random.gauss(0, 0.001)))
        l = min(o, c) * (1 - abs(random.gauss(0, 0.001)))
        candles.append({
            "symbol": "BTC", "timeframe": "5m",
            "timestamp": 1700000000000 + i * 300000,
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(c, 2),
            "volume": round(random.uniform(500, 3000), 2),
            "funding_rate": random.gauss(0.0001, 0.0003),
            "open_interest": random.uniform(1e8, 5e8),
            "liquidation_vol": random.uniform(0, 3000),
            "bid_ask_spread": 0.0003,
            "orderbook_imbalance": random.uniform(-0.2, 0.2),
        })
        price = c

    candles_by_tf = {"5m": candles}

    # Phase 1: SAFETY
    from src.pipeline.phase1_safety import phase1_safety
    from src.utils.market_stats import MarketStats
    state = {
        "current_mdd": 0.0, "current_stage": "NORMAL", "stage_entered_ts": 0,
        "capital": 10000, "open_risk": 0.0, "rolling_pf": 2.0,
        "consecutive_losses": 0, "open_positions_count": 0,
        "dna_history": {}, "current_regime": "SIDEWAYS", "grace_counter": 0,
        "avg_atr_7d": 0.0,
    }
    stats = MarketStats(symbol="BTC")
    try:
        safety = phase1_safety(candles, stats, state, {})
        report("Phase 1 SAFETY", True, f"stage={safety.stage}, blocked={safety.blocked}")
    except Exception as e:
        report("Phase 1 SAFETY", False, str(e))
        return candles_by_tf

    # Phase 2: READ
    from src.pipeline.phase2_read import phase2_read
    agent_config = {"agent_id": "s3", "symbol": "BTC", "timeframes": ["5m"]}
    try:
        regime = phase2_read(candles_by_tf, safety, agent_config, state, {})
        report("Phase 2 READ", True, f"regime={regime.regime}, conf={regime.confidence:.1f}")
    except Exception as e:
        report("Phase 2 READ", False, str(e))
        return candles_by_tf

    # Phase 3: SCAN
    from src.pipeline.phase3_scan import phase3_scan
    stats_dict = {"funding_p95": 0.001, "funding_p5": -0.001, "oi_change_p90": 5.0}
    try:
        scan = phase3_scan(candles_by_tf, regime, safety, agent_config, {}, stats_dict)
        report("Phase 3 SCAN", True, f"found={scan.found}, score={scan.score}, type={scan.primary_type}")
    except Exception as e:
        report("Phase 3 SCAN", False, str(e))
        return candles_by_tf

    # Phase 4: GATE (test with mock scan if needed)
    from src.pipeline.phase4_gate import phase4_gate
    try:
        if scan.found:
            gate = phase4_gate(candles, scan, regime, safety, state, {})
            report("Phase 4 GATE", True, f"passed={gate.passed}, score={gate.score}")
        else:
            report("Phase 4 GATE", True, "skipped (no scan)")
    except Exception as e:
        report("Phase 4 GATE", False, str(e))

    # Phase 5: tested via executor below
    return candles_by_tf


async def test_executor(db):
    """Test OrderExecutor in DRY_RUN mode."""
    from src.exchange.executor import OrderExecutor
    from src.pipeline.models import Signal

    executor = OrderExecutor(db=db, dry_run=True)

    sig = Signal(
        signal_id=f"test_{int(time.time())}",
        agent_id="s3", symbol="BTC", direction="LONG",
        entry_price=50000.0, stop_loss=49500.0,
        take_profits=[{"price": 51000, "ratio": 0.5}, {"price": 52000, "ratio": 0.5}],
        notional_usd=5000.0, leverage=5,
        phase_snapshot={"regime": "SIDEWAYS", "primary_type": "T1_SR_REACTION"},
    )

    # Execute
    result = await executor.execute_signal(sig)
    report("Executor DRY_RUN", result.success and result.dry_run, f"order={result.order_id[:20]}")

    # Idempotency
    result2 = await executor.execute_signal(sig)
    report("Executor idempotency", not result2.success and result2.error == "DUPLICATE_SIGNAL")

    # DB trade record
    cursor = await db.execute("SELECT COUNT(*) FROM trades WHERE signal_id = ?", (sig.signal_id,))
    count = (await cursor.fetchone())[0]
    report("DB trade recorded", count == 1)

    # DB position record
    cursor = await db.execute("SELECT status FROM positions WHERE signal_id = ?", (sig.signal_id,))
    row = await cursor.fetchone()
    report("DB position OPEN", row is not None and row[0] == "OPEN")

    # Record exit
    await executor.record_exit(sig.signal_id, 51000.0, 100.0, 2.0, "TP_FULL")
    cursor = await db.execute("SELECT status FROM positions WHERE signal_id = ?", (sig.signal_id,))
    row = await cursor.fetchone()
    report("DB position CLOSED", row is not None and row[0] == "CLOSED")

    return executor


async def test_reconciliation(executor, db):
    """Test reconciliation with no exchange positions."""
    from src.exchange.reconciliation import reconcile_on_startup

    # Insert a fake open position
    await db.execute(
        """INSERT INTO positions (agent_id, symbol, direction, entry_price, size_usd,
           sl_pct, entry_time, signal_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')""",
        ("s1", "ETH", "LONG", 3000.0, 1000.0, 0.02, int(time.time() * 1000), "stale_test_001"),
    )
    await db.commit()

    recon = await reconcile_on_startup(executor, db)
    report(
        "Reconciliation",
        recon["stale"] >= 1,
        f"matched={recon['matched']}, stale={recon['stale']}, orphan={recon['orphaned']}",
    )


async def test_position_manager(db):
    """Test PositionManager with executor integration."""
    from src.exchange.executor import OrderExecutor
    from src.pipeline.position_manager import PositionManager
    from src.pipeline.models import Signal

    executor = OrderExecutor(db=db, dry_run=True)
    pm = PositionManager(executor=executor, dry_run=True)

    sig = Signal(
        signal_id=f"pm_test_{int(time.time())}",
        agent_id="s2", symbol="ETH", direction="SHORT",
        entry_price=3000.0, stop_loss=3100.0,
        take_profits=[{"price": 2900, "ratio": 1.0}],
        notional_usd=3000.0, leverage=3,
        phase_snapshot={},
    )

    pm.submit_signal(sig)
    # Wait for async execution
    await asyncio.sleep(0.1)

    open_pos = pm.get_open_positions("s2")
    report("PositionManager submit", len(open_pos) == 1, f"open={len(open_pos)}")

    risk = pm.get_open_risk("s2")
    report("PositionManager risk", risk > 0, f"risk=${risk:.2f}")


async def test_pipeline_logging(db):
    """Test structured pipeline logging to DB."""
    # Insert a test log
    snapshot = {
        "elapsed_ms": 15.2,
        "p1_safety": {"stage": "NORMAL", "blocked": False},
        "p2_read": {"regime": "SIDEWAYS", "confidence": 45.0},
        "p3_scan": {"found": True, "score": 42.5, "primary_type": "T1_SR_REACTION"},
    }
    await db.execute(
        "INSERT INTO pipeline_logs (timestamp, agent_id, symbol, phase_snapshot, signal_generated) VALUES (?, ?, ?, ?, ?)",
        (int(time.time() * 1000), "s3", "BTC", json.dumps(snapshot), 1),
    )
    await db.commit()

    cursor = await db.execute("SELECT COUNT(*) FROM pipeline_logs")
    count = (await cursor.fetchone())[0]
    report("Pipeline logging", count >= 1, f"{count} log entries")

    # Verify JSON is readable
    cursor = await db.execute("SELECT phase_snapshot FROM pipeline_logs LIMIT 1")
    row = await cursor.fetchone()
    parsed = json.loads(row[0])
    report("Pipeline log JSON", "p1_safety" in parsed and "p3_scan" in parsed)


async def run_all():
    print("=" * 60)
    print("  INTEGRATION TEST SUITE")
    print("=" * 60)

    print("\n── DB ──")
    db = await test_db_init()

    print("\n── Pipeline Phases ──")
    await test_pipeline_phases(db)

    print("\n── Order Executor ──")
    executor = await test_executor(db)

    print("\n── Reconciliation ──")
    await test_reconciliation(executor, db)

    print("\n── Position Manager ──")
    await test_position_manager(db)

    print("\n── Pipeline Logging ──")
    await test_pipeline_logging(db)

    await db.close()

    # Summary
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    total = len(results)

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
    print(f"{'=' * 60}")

    if failed > 0:
        print("\n  Failed tests:")
        for name, p, detail in results:
            if not p:
                print(f"    {FAIL} {name}: {detail}")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all())
    sys.exit(0 if success else 1)
