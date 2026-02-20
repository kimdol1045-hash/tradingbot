#!/usr/bin/env python3
"""
Coin Screener — backtest-based coin selection for 5Phase pipeline.

Screens Hyperliquid assets by running full pipeline backtests and
classifying coins into tiers based purely on backtest performance.

Usage:
    python scripts/coin_screener.py                        # Full run
    python scripts/coin_screener.py --top 5                # Top 5 only (test)
    python scripts/coin_screener.py --min-volume 10        # Min $10M volume
    python scripts/coin_screener.py --symbol BTC ETH SOL   # Specific coins
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.engine import BacktestEngine
from src.backtest.metrics import calculate_metrics
from src.collector.historical import fetch_binance_candles, fetch_hyperliquid_candles_chunked
from src.exchange.hyperliquid import fetch_asset_contexts, get_info_client
from src.notify.telegram import _send_message, notify_system
from src.utils.config import AGENT_PROFILES, TIMEFRAME_MINUTES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

AGENT_IDS = ["s1", "s2", "s3", "s4"]
TIMEFRAMES = ["5m", "15m", "1h", "4h"]

# ═══ Scoring weights ═══

SCORE_WEIGHTS = {
    "pf": 0.35,
    "win_rate": 0.20,
    "sharpe": 0.20,
    "mdd_inv": 0.15,
    "trade_count": 0.10,
}

# ═══ Tier thresholds (overridable via env: TIER1_PF, TIER1_WR, TIER1_MDD, etc.) ═══

TIER_THRESHOLDS = {
    "tier_1": {
        "pf": float(os.getenv("TIER1_PF", "1.5")),
        "win_rate": float(os.getenv("TIER1_WR", "45.0")),
        "mdd": float(os.getenv("TIER1_MDD", "8.0")),
    },
    "tier_2": {
        "pf": float(os.getenv("TIER2_PF", "1.2")),
        "win_rate": float(os.getenv("TIER2_WR", "40.0")),
        "mdd": float(os.getenv("TIER2_MDD", "10.0")),
    },
    "tier_3": {
        "pf": float(os.getenv("TIER3_PF", "1.0")),
        "win_rate": float(os.getenv("TIER3_WR", "35.0")),
        "mdd": float(os.getenv("TIER3_MDD", "15.0")),
    },
}


# ═══ Stage 1: Volume Filter ═══

def stage1_filter(
    contexts: dict[str, dict],
    min_volume: float,
    symbols: list[str] | None = None,
) -> list[str]:
    """Filter coins by minimum daily volume. Returns sorted symbol list."""
    if symbols:
        # User specified symbols — only validate they exist
        valid = [s for s in symbols if s in contexts]
        missing = set(symbols) - set(valid)
        if missing:
            logger.warning("Symbols not found on Hyperliquid: %s", missing)
        return valid

    candidates = []
    for sym, ctx in contexts.items():
        if ctx["day_volume"] >= min_volume and ctx["mark_price"] > 0:
            candidates.append((sym, ctx["day_volume"]))

    # Sort by volume descending
    candidates.sort(key=lambda x: -x[1])
    return [sym for sym, _ in candidates]


# ═══ Stage 2: Full Pipeline Backtest ═══

async def fetch_all_timeframes(
    symbol: str, start_ms: int, end_ms: int, info=None,
) -> dict[str, list[dict]]:
    """Download full history for all 4 timeframes.

    Uses Binance Futures as primary source (years of 5m data).
    Falls back to Hyperliquid if Binance doesn't have the coin.
    """
    candles_by_tf: dict[str, list[dict]] = {}
    source = "binance"

    for tf in TIMEFRAMES:
        # Try Binance first (much more historical data)
        candles = await fetch_binance_candles(symbol, tf, start_ms, end_ms)

        if not candles:
            # Fallback to Hyperliquid (limited to ~5000 candles)
            source = "hyperliquid"
            candles = await fetch_hyperliquid_candles_chunked(
                symbol, tf, start_ms, end_ms, info=info,
            )

        candles_by_tf[tf] = candles
        logger.info("  %s %s: %d candles (%s)", symbol, tf, len(candles), source)

    return candles_by_tf


def run_backtest_all_agents(
    symbol: str,
    candles_by_tf: dict[str, list[dict]],
) -> dict[str, dict]:
    """Run backtest for all 4 agents on one coin. Returns {agent_id: metrics_dict}."""
    engine = BacktestEngine(candles_by_tf, symbol=symbol)
    results = engine.run(agent_ids=AGENT_IDS)

    agent_metrics = {}
    for agent_id, result in results.items():
        m = calculate_metrics(result)
        agent_metrics[agent_id] = {
            "pf": round(m.profit_factor, 2) if m.profit_factor != float("inf") else 99.0,
            "win_rate": round(m.win_rate, 1),
            "trades": m.total_trades,
            "mdd": round(m.max_drawdown_pct, 1),
            "sharpe": round(m.sharpe_ratio, 2),
            "sortino": round(m.sortino_ratio, 2),
            "calmar": round(m.calmar_ratio, 2),
            "net_pnl": round(m.net_pnl, 2),
            "return_pct": round(m.total_return_pct, 1),
            "days": round(m.days, 0),
        }

    return agent_metrics


async def stage2_backtest(
    candidates: list[str],
    top: int = 0,
) -> dict[str, dict]:
    """
    Run full pipeline backtests on all candidates.
    Returns {symbol: {agent_metrics, best_agent, best_metrics, days}}.
    """
    if top > 0:
        candidates = candidates[:top]

    now_ms = int(time.time() * 1000)
    # Go back 1 year — Binance has 3+ years but 1yr is sufficient for screening
    start_ms = now_ms - (365 * 24 * 60 * 60 * 1000)

    # Share one Info client across all downloads
    info = get_info_client()
    results: dict[str, dict] = {}
    total = len(candidates)

    for idx, symbol in enumerate(candidates):
        logger.info("[%d/%d] %s: downloading history...", idx + 1, total, symbol)

        try:
            candles_by_tf = await fetch_all_timeframes(symbol, start_ms, now_ms, info=info)
        except Exception:
            logger.exception("Failed to download %s, skipping", symbol)
            continue

        # Check data sufficiency — need at least 90 days (25,920 5m candles)
        n_5m = len(candles_by_tf.get("5m", []))
        min_candles = 25_000
        if n_5m < min_candles:
            logger.warning(
                "%s: only %d 5m candles (%d days), skipping (need %d+ / 90 days)",
                symbol, n_5m, n_5m // 288, min_candles,
            )
            continue

        days = n_5m / 288  # 288 5m candles per day
        logger.info("[%d/%d] %s (%d days, 4 TF): running backtest...", idx + 1, total, symbol, int(days))

        try:
            agent_metrics = run_backtest_all_agents(symbol, candles_by_tf)
        except Exception:
            logger.exception("Backtest failed for %s, skipping", symbol)
            continue

        if not agent_metrics:
            logger.warning("%s: no agent results, skipping", symbol)
            continue

        # Find best agent (by composite score)
        best_agent = None
        best_score = -1
        for aid, am in agent_metrics.items():
            if am.get("trades", 0) == 0:
                continue
            score = _compute_composite_score(am)
            if score > best_score:
                best_score = score
                best_agent = aid

        if best_agent is None:
            logger.warning("%s: no agent scored, skipping", symbol)
            continue

        results[symbol] = {
            "agents": agent_metrics,
            "best_agent": best_agent,
            "best_metrics": agent_metrics[best_agent],
            "best_score": round(best_score, 1),
            "days": int(days),
        }

        # Print per-agent results
        for aid in AGENT_IDS:
            am = agent_metrics.get(aid, {})
            if not am or am.get("trades", 0) == 0:
                logger.info("  %s: no trades", aid)
                continue
            best_marker = " ← best" if aid == best_agent else ""
            logger.info(
                "  %s: PF=%.2f WR=%.0f%% trades=%d MDD=%.1f%% sharpe=%.2f%s",
                aid, am["pf"], am["win_rate"], am["trades"],
                am["mdd"], am["sharpe"], best_marker,
            )

    return results


# ═══ Stage 3: Tier Classification ═══

def _compute_composite_score(m: dict) -> float:
    """Compute composite score from agent metrics. Returns 0-100."""
    pf = min(m.get("pf", 0), 5.0)  # Cap at 5
    win_rate = m.get("win_rate", 0)
    sharpe = m.get("sharpe", 0)
    mdd = m.get("mdd", 100)
    trades = m.get("trades", 0)

    # PF score: 0-100 (1.0=0, 2.0=50, 3.0+=100)
    pf_score = max(0, min(100, (pf - 1.0) * 50))

    # Win rate score: 0-100 (30%=0, 50%=50, 70%+=100)
    wr_score = max(0, min(100, (win_rate - 30) * 2.5))

    # Sharpe score: 0-100 (0=0, 1.5=50, 3.0+=100)
    sharpe_score = max(0, min(100, sharpe * 33.3))

    # MDD inverse score: 0-100 (lower MDD = better, 0%=100, 5%=50, 10%+=0)
    mdd_score = max(0, min(100, (10 - mdd) * 10))

    # Trade count score: penalize too few (<10) and too many (>500)
    if trades < 5:
        trade_score = 0
    elif trades < 10:
        trade_score = trades * 10  # 5→50, 10→100
    elif trades <= 500:
        trade_score = 100
    else:
        trade_score = max(50, 100 - (trades - 500) * 0.1)

    score = (
        pf_score * SCORE_WEIGHTS["pf"]
        + wr_score * SCORE_WEIGHTS["win_rate"]
        + sharpe_score * SCORE_WEIGHTS["sharpe"]
        + mdd_score * SCORE_WEIGHTS["mdd_inv"]
        + trade_score * SCORE_WEIGHTS["trade_count"]
    )

    return score


def _classify_tier(m: dict) -> str:
    """Classify a coin's tier based on its best agent metrics."""
    pf = m.get("pf", 0)
    wr = m.get("win_rate", 0)
    mdd = m.get("mdd", 100)
    trades = m.get("trades", 0)

    # Exclude: PF < 1.0 or too few trades
    if pf < 1.0 or trades < 5:
        return "excluded"

    # Tier 1
    t1 = TIER_THRESHOLDS["tier_1"]
    if pf >= t1["pf"] and wr >= t1["win_rate"] and mdd <= t1["mdd"]:
        return "tier_1"

    # Tier 2
    t2 = TIER_THRESHOLDS["tier_2"]
    if pf >= t2["pf"] and wr >= t2["win_rate"] and mdd <= t2["mdd"]:
        return "tier_2"

    # Tier 3
    t3 = TIER_THRESHOLDS["tier_3"]
    if pf >= t3["pf"] and wr >= t3["win_rate"] and mdd <= t3.get("mdd", 15.0):
        return "tier_3"

    return "excluded"


def _find_best_agents(agents: dict[str, dict], threshold_pf: float = 1.0) -> list[str]:
    """Return list of agents that perform well for this coin (PF > threshold)."""
    good = []
    for aid, m in agents.items():
        if m.get("pf", 0) >= threshold_pf and m.get("trades", 0) >= 5:
            good.append(aid)
    # Sort by composite score descending
    good.sort(key=lambda a: _compute_composite_score(agents[a]), reverse=True)
    return good


def stage3_classify(backtest_results: dict[str, dict]) -> list[dict]:
    """Classify coins into tiers based on backtest results. Returns sorted list."""
    classified = []

    for symbol, data in backtest_results.items():
        best_m = data["best_metrics"]
        tier = _classify_tier(best_m)
        best_agents = _find_best_agents(data["agents"])

        classified.append({
            "symbol": symbol,
            "tier": tier,
            "score": data["best_score"],
            "best_agent": data["best_agent"],
            "best_agents": best_agents,
            "days": data["days"],
            "best_metrics": best_m,
            "all_agents": data["agents"],
        })

    # Sort: tier_1 first, then by score descending
    tier_order = {"tier_1": 0, "tier_2": 1, "tier_3": 2, "excluded": 3}
    classified.sort(key=lambda x: (tier_order.get(x["tier"], 9), -x["score"]))

    return classified


# ═══ Output ═══

def print_results(classified: list[dict], total_assets: int, n_candidates: int):
    """Print formatted results to console."""
    # Stage 1 summary
    excluded_count = total_assets - n_candidates
    print(f"\n{'═' * 60}")
    print(f" STAGE 1: Filter ({total_assets} assets → {n_candidates} candidates)")
    print(f"{'═' * 60}")
    print(f"Excluded {excluded_count} coins (volume filter)")

    # Stage 2 summary
    print(f"\n{'═' * 60}")
    print(f" STAGE 2: Full Pipeline Backtest")
    print(f"{'═' * 60}")
    for entry in classified:
        sym = entry["symbol"]
        days = entry["days"]
        print(f"\n  {sym} ({days} days, 4 TF):")
        for aid in AGENT_IDS:
            am = entry["all_agents"].get(aid, {})
            if not am or am.get("trades", 0) == 0:
                print(f"    {aid}: no trades")
                continue
            marker = " ← best" if aid == entry["best_agent"] else ""
            print(
                f"    {aid}: PF={am['pf']:.2f} WR={am['win_rate']:.0f}% "
                f"trades={am['trades']} MDD={am['mdd']:.1f}% "
                f"sharpe={am['sharpe']:.2f}{marker}"
            )

    # Stage 3: Tier classification
    tiers: dict[str, list[str]] = {"tier_1": [], "tier_2": [], "tier_3": [], "excluded": []}
    for entry in classified:
        tier = entry["tier"]
        info = f"{entry['symbol']} → best:{entry['best_agent']}"
        tiers.setdefault(tier, []).append(info)

    print(f"\n{'═' * 60}")
    print(f" STAGE 3: Tier Classification")
    print(f"{'═' * 60}")
    for tier_name in ["tier_1", "tier_2", "tier_3", "excluded"]:
        items = tiers.get(tier_name, [])
        if items:
            print(f"\n  {tier_name} ({len(items)}): {', '.join(items)}")


def write_symbols_json(classified: list[dict], output_path: str):
    """Write results to symbols.json."""
    symbol_pool: dict[str, list[str]] = {}
    details: list[dict] = []

    for entry in classified:
        tier = entry["tier"]
        if tier != "excluded":
            symbol_pool.setdefault(tier, []).append(entry["symbol"])

        details.append({
            "symbol": entry["symbol"],
            "tier": tier,
            "score": entry["score"],
            "best_agent": entry["best_agent"],
            "best_agents": entry["best_agents"],
            "days_tested": entry["days"],
            "metrics": entry["best_metrics"],
            "all_agents": entry["all_agents"],
        })

    output_data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "full_pipeline_backtest",
        "total_analyzed": len(classified),
        "symbol_pool": symbol_pool,
        "details": details,
    }

    # Atomic write: temp file + rename to prevent corruption
    import tempfile
    out_dir = os.path.dirname(output_path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix=".tmp", prefix="symbols_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(output_data, f, indent=2)
            f.write("\n")
        os.rename(tmp_path, output_path)
        logger.info("Written to %s (atomic)", output_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ═══ Telegram Notification ═══

def _format_volume(vol: float) -> str:
    """Format volume in human readable Korean format."""
    if vol >= 1_000_000_000:
        return f"${vol / 1_000_000_000:.1f}B"
    elif vol >= 1_000_000:
        return f"${vol / 1_000_000:.0f}M"
    else:
        return f"${vol:,.0f}"


async def send_telegram_summary(
    classified: list[dict],
    contexts: dict[str, dict],
    total_assets: int,
    n_candidates: int,
    elapsed_min: float,
):
    """Send screening results to Telegram in Korean with detailed metrics."""
    tier_coins: dict[str, list] = {"tier_1": [], "tier_2": [], "tier_3": [], "excluded": []}
    for entry in classified:
        tier_coins[entry["tier"]].append(entry)

    # ── Message 1: Overview + Tier 1 + Tier 2 ──
    lines = [
        "🔍 <b>코인 스크리너 리포트</b>",
        "",
        "📊 <b>분석 개요</b>",
        f"• 전체 {total_assets}개 → 거래량 필터($1M+) → {n_candidates}개 후보",
        f"• 백테스트: {len(classified)}개 코인 × 4 에이전트 (s1~s4)",
    ]

    if classified:
        days = classified[0].get("days", 0)
        lines.append(f"• 테스트 기간: {days}일 | TF: 5m, 15m, 1h, 4h")

    # Build tier descriptions dynamically from TIER_THRESHOLDS
    t1 = TIER_THRESHOLDS["tier_1"]
    t2 = TIER_THRESHOLDS["tier_2"]
    t3 = TIER_THRESHOLDS["tier_3"]
    tier1_desc = f"PF≥{t1['pf']:.1f} | 승률≥{t1['win_rate']:.0f}% | MDD≤{t1['mdd']:.0f}%"
    tier2_desc = f"PF≥{t2['pf']:.1f} | 승률≥{t2['win_rate']:.0f}% | MDD≤{t2['mdd']:.0f}%"
    tier3_desc = f"PF≥{t3['pf']:.1f} | 승률≥{t3['win_rate']:.0f}% | MDD≤{t3.get('mdd', 15.0):.0f}%"

    lines.extend([
        "",
        "📋 <b>티어 분류 기준</b>",
        f"• 1티어: {tier1_desc}",
        f"• 2티어: {tier2_desc}",
        f"• 3티어: {tier3_desc}",
        f"• 제외: PF&lt;{t3['pf']:.1f} 또는 거래 5건 미만",
    ])

    # ── Tier 1 ──
    t1 = tier_coins["tier_1"]
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏆 <b>1티어 — 최우수 ({len(t1)}개)</b>",
    ])
    if t1:
        for e in t1:
            _append_coin_detail(lines, e, contexts, tier="tier_1")
    else:
        lines.append("  해당 없음")

    # ── Tier 2 ──
    t2 = tier_coins["tier_2"]
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🥈 <b>2티어 — 우수 ({len(t2)}개)</b>",
    ])
    if t2:
        for e in t2:
            _append_coin_detail(lines, e, contexts, tier="tier_2")
    else:
        lines.append("  해당 없음")

    msg1 = "\n".join(lines)

    # ── Message 2: Tier 3 + Excluded + Duration ──
    lines2 = [
        f"🥉 <b>3티어 — 양호 ({len(tier_coins['tier_3'])}개)</b>",
    ]
    t3 = tier_coins["tier_3"]
    if t3:
        for e in t3:
            m = e["best_metrics"]
            lines2.append(
                f"▸ {e['symbol']} | PF {m['pf']:.2f} | 승률 {m['win_rate']:.0f}% "
                f"| MDD {m['mdd']:.1f}% | {e['best_agent']} | {e['score']:.1f}점"
            )
    else:
        lines2.append("  해당 없음")

    # ── Excluded ──
    exc = tier_coins["excluded"]
    lines2.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"❌ <b>제외 ({len(exc)}개)</b>",
        f"사유: PF&lt;{TIER_THRESHOLDS['tier_3']['pf']:.1f} 또는 거래 5건 미만",
    ])
    if exc:
        exc_names = [e["symbol"] for e in exc]
        for i in range(0, len(exc_names), 8):
            chunk = exc_names[i : i + 8]
            lines2.append(f"  {', '.join(chunk)}")

    lines2.extend(["", f"⏱ 소요시간: {elapsed_min:.1f}분"])

    msg2 = "\n".join(lines2)

    # ── Send to daily_report topic ──
    try:
        await _send_message(msg1, topic="daily_report")
        await asyncio.sleep(1)
        await _send_message(msg2, topic="daily_report")
        logger.info("Screener report sent to daily_report topic")
    except Exception:
        logger.warning("Failed to send Telegram notification", exc_info=True)


def _append_coin_detail(
    lines: list[str],
    entry: dict,
    contexts: dict[str, dict],
    tier: str,
):
    """Append detailed coin info lines with pass/fail markers."""
    m = entry["best_metrics"]
    sym = entry["symbol"]
    vol = contexts.get(sym, {}).get("day_volume", 0)
    vol_str = _format_volume(vol)

    thresholds = TIER_THRESHOLDS.get(tier, TIER_THRESHOLDS["tier_3"])

    pf_ok = m["pf"] >= thresholds.get("pf", 1.0)
    wr_ok = m["win_rate"] >= thresholds.get("win_rate", 35.0)
    mdd_ok = m["mdd"] <= thresholds.get("mdd", 100.0)

    pf_mark = "✅" if pf_ok else "⚠️"
    wr_mark = "✅" if wr_ok else "⚠️"
    mdd_mark = "✅" if mdd_ok else "⚠️"

    lines.extend([
        "",
        f"▸ <b>{sym}</b> (거래량 {vol_str}/일)",
        f"  PF {m['pf']:.2f} {pf_mark} | 승률 {m['win_rate']:.0f}% {wr_mark} | MDD {m['mdd']:.1f}% {mdd_mark}",
        f"  Sharpe {m['sharpe']:.2f} | 거래 {m['trades']}건 | 수익 {m['return_pct']:+.0f}%",
        f"  → {entry['best_agent']} 에이전트 | 종합 {entry['score']:.1f}점",
    ])


# ═══ Main ═══

async def async_main():
    parser = argparse.ArgumentParser(description="Backtest-based coin screener")
    parser.add_argument("--min-volume", type=float, default=1.0,
                        help="Min daily volume in $M (default: 1)")
    parser.add_argument("--top", type=int, default=0,
                        help="Only backtest top N coins by volume (0=all)")
    parser.add_argument("--symbol", nargs="+", default=None,
                        help="Specific symbols to backtest")
    parser.add_argument("--output", type=str, default="",
                        help="Output JSON path (default: symbols.json)")
    parser.add_argument("--tier1-pf", type=float, default=None,
                        help="Tier 1 min PF (overrides env/default)")
    parser.add_argument("--tier2-pf", type=float, default=None,
                        help="Tier 2 min PF (overrides env/default)")
    parser.add_argument("--tier3-pf", type=float, default=None,
                        help="Tier 3 min PF (overrides env/default)")
    args = parser.parse_args()

    # Apply CLI threshold overrides
    if args.tier1_pf is not None:
        TIER_THRESHOLDS["tier_1"]["pf"] = args.tier1_pf
    if args.tier2_pf is not None:
        TIER_THRESHOLDS["tier_2"]["pf"] = args.tier2_pf
    if args.tier3_pf is not None:
        TIER_THRESHOLDS["tier_3"]["pf"] = args.tier3_pf
    logger.info("Tier thresholds: T1=%s, T2=%s, T3=%s", TIER_THRESHOLDS["tier_1"], TIER_THRESHOLDS["tier_2"], TIER_THRESHOLDS["tier_3"])

    t_start = time.perf_counter()

    # Stage 1: Volume filter
    logger.info("Stage 1: Fetching asset contexts...")
    info = get_info_client()
    contexts = fetch_asset_contexts(info)
    total_assets = len(contexts)
    logger.info("Found %d assets on Hyperliquid", total_assets)

    min_vol = args.min_volume * 1_000_000
    candidates = stage1_filter(contexts, min_vol, args.symbol)
    logger.info("Stage 1 complete: %d → %d candidates (min vol $%.0fM)",
                total_assets, len(candidates), args.min_volume)

    if not candidates:
        logger.error("No candidates passed Stage 1 filter")
        return

    # Stage 2: Full pipeline backtest
    logger.info("Stage 2: Running full pipeline backtests...")
    backtest_results = await stage2_backtest(candidates, top=args.top)

    if not backtest_results:
        logger.error("No coins produced backtest results")
        return

    # Stage 3: Tier classification
    logger.info("Stage 3: Classifying tiers...")
    classified = stage3_classify(backtest_results)

    # Output
    print_results(classified, total_assets, len(candidates))
    output_path = args.output or str(PROJECT_ROOT / "symbols.json")
    write_symbols_json(classified, output_path)

    elapsed = time.perf_counter() - t_start
    elapsed_min = elapsed / 60
    logger.info("Total time: %.1f minutes", elapsed_min)

    # Send Telegram notification
    await send_telegram_summary(classified, contexts, total_assets, len(candidates), elapsed_min)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
