"""
Telegram Notifier — Supergroup + Topics based notification system.
Uses httpx for Bot API calls. Rate-limited at 20 msg/min per topic.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

import httpx

from src.utils.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

# ═══ Topic IDs (set after creating Supergroup topics) ═══

TOPIC_IDS: dict[str, int | None] = {
    "signals": None,      # New signal alerts
    "fills": None,        # Order fill confirmations
    "exits": None,        # Position close notifications
    "safety": None,       # Safety stage changes, MDD alerts
    "errors": None,       # System errors
    "daily_report": None, # Daily PnL summary
    "system": None,       # System start/stop/health
}


def configure_topics(topic_map: dict[str, int]):
    """Configure topic IDs from environment or setup."""
    for key, tid in topic_map.items():
        if key in TOPIC_IDS:
            TOPIC_IDS[key] = tid


# ═══ Rate Limiter ═══

class _RateLimiter:
    """Simple rate limiter: max N messages per minute per topic."""

    def __init__(self, max_per_min: int = 20):
        self._max = max_per_min
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def can_send(self, topic: str) -> bool:
        now = time.time()
        self._timestamps[topic] = [
            t for t in self._timestamps[topic] if now - t < 60
        ]
        return len(self._timestamps[topic]) < self._max

    def record(self, topic: str):
        self._timestamps[topic].append(time.time())


_limiter = _RateLimiter(max_per_min=20)


# ═══ Send Message ═══

async def _send_message(
    text: str,
    topic: str = "system",
    parse_mode: str = "HTML",
) -> bool:
    """Send a message to a Telegram topic."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured, skipping message")
        return False

    if not _limiter.can_send(topic):
        logger.warning("Rate limit hit for topic: %s", topic)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }

    thread_id = TOPIC_IDS.get(topic)
    if thread_id is not None:
        payload["message_thread_id"] = thread_id

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                _limiter.record(topic)
                return True
            else:
                logger.warning("Telegram API error: %d %s", resp.status_code, resp.text[:200])
                return False
    except Exception:
        logger.exception("Failed to send Telegram message")
        return False


# ═══ Message Templates ═══

async def notify_signal(signal) -> bool:
    """Send new signal alert."""
    emoji = "🟢" if signal.direction == "LONG" else "🔴"
    tps = ""
    for i, tp in enumerate(signal.take_profits):
        tps += f"\n  TP{i+1}: ${tp['price']:,.2f} (RR={tp['rr']})"

    sl_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price * 100

    text = (
        f"{emoji} <b>NEW SIGNAL</b>\n"
        f"Agent: <code>{signal.agent_id}</code>\n"
        f"<b>{signal.direction} {signal.symbol}</b> @ ${signal.entry_price:,.2f}\n"
        f"SL: ${signal.stop_loss:,.2f} (-{sl_pct:.2f}%)\n"
        f"Leverage: {signal.leverage}x\n"
        f"Notional: ${signal.notional_usd:,.2f}"
        f"{tps}\n"
        f"Type: {signal.inflection_type} (score={signal.inflection_score:.0f})\n"
        f"Regime: {signal.regime} | MDD: {signal.mdd_mode}"
    )
    return await _send_message(text, topic="signals")


async def notify_fill(signal, fill_price: float, order_id: str) -> bool:
    """Send order fill confirmation."""
    text = (
        f"✅ <b>FILLED</b>\n"
        f"Agent: <code>{signal.agent_id}</code>\n"
        f"{signal.direction} {signal.symbol} @ ${fill_price:,.2f}\n"
        f"Order: <code>{order_id}</code>"
    )
    return await _send_message(text, topic="fills")


async def notify_exit(signal, close_price: float, pnl: float, reason: str) -> bool:
    """Send position close notification."""
    pnl_emoji = "💰" if pnl > 0 else "💸"
    text = (
        f"{pnl_emoji} <b>POSITION CLOSED</b>\n"
        f"Agent: <code>{signal.agent_id}</code>\n"
        f"{signal.direction} {signal.symbol}\n"
        f"Entry: ${signal.entry_price:,.2f} → Exit: ${close_price:,.2f}\n"
        f"PnL: <b>${pnl:+,.2f}</b>\n"
        f"Reason: {reason}"
    )
    return await _send_message(text, topic="exits")


async def notify_safety(stage: str, severity: float, mdd_mode: str, reason: str = "") -> bool:
    """Send safety alert."""
    text = (
        f"⚠️ <b>SAFETY ALERT</b>\n"
        f"Stage: <b>{stage}</b>\n"
        f"Severity: {severity:.1f}/240\n"
        f"MDD Mode: {mdd_mode}\n"
        f"{f'Reason: {reason}' if reason else ''}"
    )
    return await _send_message(text, topic="safety")


async def notify_error(error_msg: str, context: str = "") -> bool:
    """Send error notification."""
    text = (
        f"❌ <b>ERROR</b>\n"
        f"<code>{error_msg[:500]}</code>\n"
        f"{f'Context: {context}' if context else ''}"
    )
    return await _send_message(text, topic="errors")


async def notify_daily_report(report: dict) -> bool:
    """Send daily PnL report."""
    text = (
        f"📊 <b>DAILY REPORT</b>\n"
        f"Total PnL: <b>${report.get('total_pnl', 0):+,.2f}</b>\n"
        f"Trades: {report.get('total_trades', 0)} "
        f"(W:{report.get('wins', 0)} L:{report.get('losses', 0)})\n"
        f"Win Rate: {report.get('win_rate', 0):.1%}\n"
        f"Profit Factor: {report.get('profit_factor', 0):.2f}\n"
        f"MDD: {report.get('mdd', 0):.2%}\n"
        f"Equity: ${report.get('equity', 0):,.2f}"
    )
    return await _send_message(text, topic="daily_report")


async def notify_system(message: str) -> bool:
    """Send system status message."""
    text = f"🤖 <b>SYSTEM</b>\n{message}"
    return await _send_message(text, topic="system")
