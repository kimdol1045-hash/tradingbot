"""
Telegram Notifier — Supergroup + Topics based notification system.

Topics:
  signals      — 신규 시그널 발생
  fills        — 주문 체결 확인
  exits        — 포지션 청산 (PnL)
  safety       — 안전 단계 경고
  errors       — 시스템 에러
  daily_report — 일일 리포트 + 스크리너 리포트
  system       — 시스템 상태 + 양방향 자연어 대화

Bidirectional: system 토픽에서 자연어 질문을 받으면 AI가 시스템 데이터를
조회하여 한국어로 답변합니다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict

import httpx

from src.utils.config import OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

# ═══ Topic IDs — loaded from env vars (TELEGRAM_TOPIC_<NAME>) ═══

_TOPIC_KEYS = ["signals", "fills", "exits", "safety", "errors", "daily_report", "system"]

TELEGRAM_MAX_LENGTH = 4096


def _load_topic_ids() -> dict[str, int | None]:
    """Load topic thread IDs from environment variables."""
    result: dict[str, int | None] = {}
    for key in _TOPIC_KEYS:
        env_key = f"TELEGRAM_TOPIC_{key.upper()}"
        val = os.environ.get(env_key, "")
        if val.strip().isdigit():
            result[key] = int(val.strip())
        else:
            result[key] = None
    return result


TOPIC_IDS: dict[str, int | None] = _load_topic_ids()


def configure_topics(topic_map: dict[str, int]):
    """Configure topic IDs programmatically (overrides env)."""
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

_telegram_config_warned = False


# ═══ Send Message ═══

def _truncate_message(text: str, max_len: int = TELEGRAM_MAX_LENGTH) -> str:
    """Truncate message to fit Telegram's character limit."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n\n... (truncated)"


async def _send_message(
    text: str,
    topic: str = "system",
    parse_mode: str = "HTML",
) -> bool:
    """Send a message to a Telegram topic with retry on transient errors."""
    global _telegram_config_warned

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        if not _telegram_config_warned:
            logger.warning("Telegram not configured (missing BOT_TOKEN or CHAT_ID)")
            _telegram_config_warned = True
        return False

    if not _limiter.can_send(topic):
        logger.warning("Rate limit hit for topic: %s — message dropped", topic)
        return False

    # Truncate to Telegram max length
    text = _truncate_message(text)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }

    thread_id = TOPIC_IDS.get(topic)
    if thread_id is not None:
        payload["message_thread_id"] = thread_id

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    _limiter.record(topic)
                    return True
                elif resp.status_code == 429:
                    # Rate limited by Telegram — backoff and retry
                    retry_after = 5 * attempt
                    try:
                        retry_after = int(resp.json().get("parameters", {}).get("retry_after", retry_after))
                    except Exception:
                        pass
                    logger.warning(
                        "Telegram rate limited (429), retrying in %ds (attempt %d/%d)",
                        retry_after, attempt, max_retries,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                elif resp.status_code in (500, 502, 503, 504):
                    # Server error — retry with backoff
                    logger.warning(
                        "Telegram server error %d, retrying (attempt %d/%d)",
                        resp.status_code, attempt, max_retries,
                    )
                    await asyncio.sleep(2 * attempt)
                    continue
                else:
                    # Client error (401, 400, etc.) — don't retry
                    logger.error(
                        "Telegram API error %d: %s (topic=%s)",
                        resp.status_code, resp.text[:300], topic,
                    )
                    return False
        except httpx.TimeoutException:
            logger.warning("Telegram send timeout (attempt %d/%d)", attempt, max_retries)
            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)
                continue
        except Exception:
            logger.warning("Telegram send failed (attempt %d/%d)", attempt, max_retries, exc_info=True)
            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)
                continue

    logger.error("Telegram send failed after %d retries (topic=%s)", max_retries, topic)
    return False


# ═══════════════════════════════════════════
#  토픽별 메시지 템플릿
# ═══════════════════════════════════════════

# ── signals 토픽: 신규 시그널 ──

async def notify_signal(signal) -> bool:
    """신규 매매 시그널 알림."""
    try:
        emoji = "🟢" if signal.direction == "LONG" else "🔴"
        arrow = "📈" if signal.direction == "LONG" else "📉"
        sl_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price * 100 if signal.entry_price else 0

        tps = ""
        for i, tp in enumerate(signal.take_profits):
            rr = tp.get("rr", 0)
            tps += f"\n  ├ TP{i+1}: ${tp.get('price', 0):,.2f} (RR {rr:.1f}x)"

        text = (
            f"{emoji} <b>시그널 발생</b> {arrow}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"에이전트: <code>{signal.agent_id}</code>\n"
            f"코인: <b>{signal.symbol}</b> | 방향: <b>{signal.direction}</b>\n"
            f"진입가: ${signal.entry_price:,.2f}\n"
            f"손절가: ${signal.stop_loss:,.2f} ({sl_pct:.2f}%)\n"
            f"레버리지: {signal.leverage}x\n"
            f"포지션: ${signal.notional_usd:,.0f}"
            f"{tps}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"패턴: {signal.inflection_type} ({signal.inflection_score:.0f}점)\n"
            f"시장: {signal.regime} | MDD: {signal.mdd_mode}"
        )
        return await _send_message(text, topic="signals")
    except Exception:
        logger.warning("notify_signal failed", exc_info=True)
        return False


# ── fills 토픽: 주문 체결 ──

async def notify_fill(signal, fill_price: float, order_id: str) -> bool:
    """주문 체결 알림."""
    try:
        emoji = "🟢" if signal.direction == "LONG" else "🔴"
        text = (
            f"✅ <b>체결 완료</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"에이전트: <code>{signal.agent_id}</code>\n"
            f"{emoji} {signal.direction} <b>{signal.symbol}</b>\n"
            f"체결가: ${fill_price:,.2f}\n"
            f"레버리지: {signal.leverage}x | 사이즈: ${signal.notional_usd:,.0f}\n"
            f"주문ID: <code>{order_id[:16]}</code>"
        )
        return await _send_message(text, topic="fills")
    except Exception:
        logger.warning("notify_fill failed", exc_info=True)
        return False


# ── exits 토픽: 포지션 청산 ──

async def notify_exit(signal, close_price: float, pnl: float, reason: str) -> bool:
    """포지션 청산 알림 (PnL 포함)."""
    try:
        if pnl > 0:
            emoji = "💰"
            result = "수익"
        else:
            emoji = "💸"
            result = "손실"

        pnl_pct = ((close_price - signal.entry_price) / signal.entry_price * 100) if signal.entry_price else 0
        if signal.direction == "SHORT":
            pnl_pct = -pnl_pct

        reason_kr = {
            "TRAILING_SL": "트레일링 손절",
            "TIMEOUT": "시간 초과",
            "TP_HIT": "익절 도달",
            "SL_HIT": "손절 도달",
            "EMERGENCY": "긴급 청산",
            "MANUAL": "수동 청산",
        }.get(reason, reason)

        text = (
            f"{emoji} <b>청산 — {result}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"에이전트: <code>{signal.agent_id}</code>\n"
            f"{signal.direction} <b>{signal.symbol}</b>\n"
            f"진입: ${signal.entry_price:,.2f} → 청산: ${close_price:,.2f}\n"
            f"PnL: <b>${pnl:+,.2f}</b> ({pnl_pct:+.1f}%)\n"
            f"사유: {reason_kr}"
        )
        return await _send_message(text, topic="exits")
    except Exception:
        logger.warning("notify_exit failed", exc_info=True)
        return False


# ── safety 토픽: 안전 단계 경고 ──

async def notify_safety(stage: str, severity: float, mdd_mode: str, reason: str = "") -> bool:
    """안전 단계 변경 알림."""
    stage_info = {
        "STAGE_1": ("🟡", "주의", "레버리지/사이즈 축소"),
        "STAGE_2": ("🟠", "경계", "신규 진입 제한, 손절 강화"),
        "STAGE_3": ("🔴", "위험", "최소 포지션만 허용"),
        "EMERGENCY": ("🚨", "긴급", "전체 청산 + 매매 중단"),
    }
    emoji, level, desc = stage_info.get(stage, ("⚠️", stage, ""))

    text = (
        f"{emoji} <b>안전 경고 — {level}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"단계: <b>{stage}</b>\n"
        f"위험도: {severity:.0f}/240\n"
        f"MDD 모드: {mdd_mode}\n"
        f"조치: {desc}"
    )
    if reason:
        # Limit reason length to prevent exceeding message limit
        text += f"\n원인: {reason[:200]}"
    return await _send_message(text, topic="safety")


# ── errors 토픽: 시스템 에러 ──

async def notify_error(error_msg: str, context: str = "") -> bool:
    """시스템 에러 알림."""
    text = (
        f"❌ <b>시스템 에러</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<code>{error_msg[:500]}</code>"
    )
    if context:
        text += f"\n컨텍스트: {context[:200]}"
    return await _send_message(text, topic="errors")


# ── daily_report 토픽: 일일 리포트 ──

async def notify_daily_report(report: dict) -> bool:
    """일일 성과 리포트."""
    pnl = report.get("total_pnl", 0)
    emoji = "📈" if pnl >= 0 else "📉"
    pf = report.get("profit_factor", 0)
    pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) and pf < 99 else "∞"
    wr = report.get("win_rate", 0)

    lines = [
        f"{emoji} <b>일일 리포트</b>",
        "━━━━━━━━━━━━━━━",
        f"총 PnL: <b>${pnl:+,.2f}</b>",
        f"총 거래: {report.get('total_trades', 0)}건 (승 {report.get('wins', 0)} / 패 {report.get('losses', 0)})",
        f"승률: {wr:.1%} | PF: {pf_str}",
        f"MDD: {report.get('mdd', 0):.2%}",
        f"총 자산: ${report.get('equity', 0):,.2f}",
    ]

    # Per-agent breakdown (limit to avoid exceeding 4096 chars)
    agents = report.get("agents", {})
    if agents:
        lines.append("")
        lines.append("<b>에이전트별</b>")
        for aid, a in sorted(agents.items()):
            eq = a.get("equity", 0)
            lines.append(
                f"  {aid}: ${eq:,.2f} | MDD {a.get('mdd', 0):.1%} | "
                f"{a.get('trades', 0)}건 | PF {a.get('pf', 0):.2f}"
            )

    return await _send_message("\n".join(lines), topic="daily_report")


# ── system 토픽: 시스템 메시지 ──

async def notify_system(message: str) -> bool:
    """시스템 상태 메시지."""
    text = f"🤖 <b>시스템</b>\n{message}"
    return await _send_message(text, topic="system")


# ═══════════════════════════════════════════
#  양방향 자연어 대화 핸들러 (system 토픽)
# ═══════════════════════════════════════════

# LLM config (shares OpenClaw/OpenAI settings with evolver)
_USE_OPENCLAW = os.getenv("USE_OPENCLAW", "false").lower() == "true"
_OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
_OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
_LLM_MODEL = os.getenv("TELEGRAM_LLM_MODEL", os.getenv("EVOLVER_MODEL", "gpt-4o"))

_CHAT_SYSTEM_PROMPT = """\
너는 자동매매 트레이딩 봇의 어시스턴트야. 사용자가 자연어로 질문하면 시스템 데이터를 조회해서 한국어로 답해줘.

사용 가능한 데이터:
1. 잔고(balance): 에이전트별 지갑 잔고 (perp, spot, 합계)
2. 포지션(positions): 현재 오픈된 포지션 목록
3. 거래내역(trades): 최근 체결/청산된 거래
4. PnL: 수익/손실 요약 (승률, PF, MDD)
5. 에이전트(agents): s1~s4 에이전트별 성과
6. 상태(status): 시스템 컴포넌트 상태
7. 매매 중지/재개: pause/resume 명령

응답 규칙:
- 간결하고 핵심만 전달 (Telegram 메시지)
- 숫자는 읽기 쉽게 포맷 ($1,234.56)
- HTML 태그 사용 가능: <b>, <code>, <i>
- 데이터가 없으면 솔직히 "아직 데이터가 없어요" 라고 답해
- 매매 중지/재개 요청은 확실한 표현일 때만 실행

system_data에 현재 시스템 상태가 JSON으로 제공됨.
사용자 질문에 맞는 정보를 찾아서 답해줘.
"""


async def _call_llm(system_prompt: str, user_prompt: str) -> str | None:
    """Call LLM (OpenClaw or OpenAI) for natural language response."""
    if _USE_OPENCLAW:
        if not _OPENCLAW_GATEWAY_TOKEN:
            logger.warning("OpenClaw gateway token not configured for chat LLM")
            return None
        api_url = f"{_OPENCLAW_GATEWAY_URL}/v1/chat/completions"
        auth_token = _OPENCLAW_GATEWAY_TOKEN
    else:
        if not OPENAI_API_KEY:
            logger.warning("OpenAI API key not configured for chat LLM")
            return None
        api_url = "https://api.openai.com/v1/chat/completions"
        auth_token = OPENAI_API_KEY

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(api_url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                try:
                    return data["choices"][0]["message"]["content"]
                except (KeyError, IndexError):
                    logger.warning("Unexpected LLM response format: %s", str(data)[:200])
                    return None
            logger.warning("LLM API error: %d %s", resp.status_code, resp.text[:300])
    except Exception:
        logger.warning("LLM call failed", exc_info=True)
    return None


# ── Command patterns (word-boundary matching to avoid false positives) ──

_PAUSE_PATTERNS = [
    re.compile(r"\b(pause|stop)\b", re.IGNORECASE),
    re.compile(r"(매매|거래|트레이딩|봇).*(중지|멈춰|정지)"),
    re.compile(r"(중지|멈춰|정지).*(매매|거래|트레이딩|봇)"),
]
_RESUME_PATTERNS = [
    re.compile(r"\b(resume|start)\b", re.IGNORECASE),
    re.compile(r"(매매|거래|트레이딩|봇).*(재개|시작|켜)"),
    re.compile(r"(재개|시작|켜).*(매매|거래|트레이딩|봇)"),
]


class TelegramChatHandler:
    """
    system 토픽에서 자연어 메시지를 받아 AI로 답변하는 핸들러.
    Long-polling (getUpdates) 방식.
    """

    def __init__(self):
        self._offset: int = 0
        self._running: bool = False
        self._paused: bool = False
        self._components: dict = {}

    def set_components(self, components: dict):
        """Register system components for data access."""
        self._components = components

    @property
    def paused(self) -> bool:
        return self._paused

    async def _reply(self, chat_id: int, text: str, thread_id: int | None = None):
        """Reply to a message."""
        if not TELEGRAM_BOT_TOKEN:
            return
        text = _truncate_message(text)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json=payload)
        except Exception:
            logger.warning("Reply failed", exc_info=True)

    async def _get_updates(self) -> list[dict]:
        """Long-poll for new messages."""
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {
            "offset": self._offset,
            "timeout": 30,
            "allowed_updates": '["message"]',
        }
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    return resp.json().get("result", [])
        except httpx.TimeoutException:
            pass
        except Exception:
            logger.warning("getUpdates error", exc_info=True)
        return []

    def _gather_system_data(self) -> dict:
        """Collect current system state for LLM context."""
        data: dict = {}

        # Balances
        et = self._components.get("equity_tracker")
        if et and hasattr(et, "get_balances"):
            data["balances"] = et.get_balances()

        # PnL / daily report
        if et and hasattr(et, "get_daily_report"):
            data["daily_report"] = et.get_daily_report()

        # Open positions
        pm = self._components.get("position_manager")
        if pm:
            open_pos = pm.get_open_positions()
            data["open_positions"] = []
            for pos in open_pos:
                sig = pos.signal
                elapsed_min = (time.time() * 1000 - pos.filled_ts) / 60000 if pos.filled_ts else 0
                data["open_positions"].append({
                    "agent_id": sig.agent_id,
                    "symbol": sig.symbol,
                    "direction": sig.direction,
                    "entry_price": pos.fill_price,
                    "stop_loss": sig.stop_loss,
                    "leverage": sig.leverage,
                    "notional_usd": sig.notional_usd,
                    "elapsed_min": round(elapsed_min),
                })

        # System status
        try:
            from src.utils.health import _build_status
            status = _build_status()
            data["system_status"] = {
                "status": status.get("status"),
                "uptime_sec": status.get("uptime_sec", 0),
                "components": {
                    k: v.get("ok", False)
                    for k, v in status.get("components", {}).items()
                },
            }
        except Exception:
            pass

        data["paused"] = self._paused
        return data

    async def _get_recent_trades(self, limit: int = 10) -> list[dict]:
        """Fetch recent trades from DB."""
        try:
            from src.utils.db import get_read_connection
            db = await get_read_connection()
            try:
                cursor = await db.execute(
                    "SELECT agent_id, symbol, side, entry_price, exit_price, "
                    "pnl_usd, pnl_pct, exit_reason "
                    "FROM trades WHERE exit_time IS NOT NULL "
                    "ORDER BY exit_time DESC LIMIT ?",
                    (limit,),
                )
                rows = await cursor.fetchall()
                cols = ["agent_id", "symbol", "side", "entry_price", "exit_price",
                        "pnl_usd", "pnl_pct", "exit_reason"]
                return [dict(zip(cols, r)) for r in rows]
            finally:
                await db.close()
        except Exception:
            return []

    async def _handle_message(self, user_text: str, chat_id: int, thread_id: int | None):
        """Process a natural language message and respond."""
        user_lower = user_text.lower().strip()

        # Quick actions — use regex for precise matching (avoid false positives)
        if any(p.search(user_text) for p in _PAUSE_PATTERNS):
            self._paused = True
            pm = self._components.get("position_manager")
            if pm:
                pm.dry_run = True
            await self._reply(chat_id, "⏸ <b>매매 일시중지됨</b>\n신규 주문이 차단됩니다.", thread_id)
            await notify_system("⏸ 매매 일시중지 (Telegram)")
            return

        if any(p.search(user_text) for p in _RESUME_PATTERNS):
            self._paused = False
            pm = self._components.get("position_manager")
            if pm:
                pm.dry_run = False
            await self._reply(chat_id, "▶️ <b>매매 재개됨</b>\n실시간 주문이 활성화됩니다.", thread_id)
            await notify_system("▶️ 매매 재개 (Telegram)")
            return

        # Gather system data for LLM context
        system_data = self._gather_system_data()

        # Include recent trades if question seems trade-related
        if any(kw in user_lower for kw in ["거래", "매매", "trade", "내역", "기록", "최근"]):
            system_data["recent_trades"] = await self._get_recent_trades()

        # Build LLM prompt
        data_json = json.dumps(system_data, ensure_ascii=False, default=str, indent=2)
        user_prompt = f"system_data:\n{data_json}\n\n사용자 질문: {user_text}"

        # Try LLM
        response = await _call_llm(_CHAT_SYSTEM_PROMPT, user_prompt)

        if response:
            await self._reply(chat_id, response, thread_id)
        else:
            # Fallback: simple keyword matching
            fallback = await self._fallback_response(user_lower, system_data)
            await self._reply(chat_id, fallback, thread_id)

    async def _fallback_response(self, text: str, data: dict) -> str:
        """Fallback response when LLM is unavailable."""
        # Balance query
        if any(kw in text for kw in ["잔고", "잔액", "balance", "계좌"]):
            balances = data.get("balances", {})
            if not balances:
                return "💰 잔고 데이터가 아직 없어요."

            # Check if asking about specific agent
            for aid in ["s1", "s2", "s3", "s4"]:
                if aid in text:
                    b = balances.get(aid, {})
                    if b:
                        return (
                            f"💰 <b>{aid} 잔고</b>\n"
                            f"합계: ${b.get('account_value', 0):,.2f}\n"
                            f"Perp: ${b.get('perp_equity', 0):,.2f}\n"
                            f"Spot: ${b.get('spot_balance', 0):,.2f}"
                        )
                    return f"{aid} 잔고 정보가 없어요."

            # All balances
            lines = ["💰 <b>전체 잔고</b>", ""]
            total = 0.0
            for aid, b in sorted(balances.items()):
                val = b.get("account_value", 0)
                total += val
                lines.append(f"{aid}: ${val:,.2f}")
            lines.append(f"\n합계: <b>${total:,.2f}</b>")
            return "\n".join(lines)

        # Position query
        if any(kw in text for kw in ["포지션", "position", "열려", "오픈"]):
            positions = data.get("open_positions", [])
            if not positions:
                return "📊 오픈 포지션 없어요."
            lines = [f"📊 <b>오픈 포지션 ({len(positions)}건)</b>", ""]
            for p in positions:
                emoji = "🟢" if p["direction"] == "LONG" else "🔴"
                lines.append(
                    f"{emoji} {p['agent_id']} | {p['symbol']} {p['direction']}\n"
                    f"  진입 ${p['entry_price']:,.2f} | {p['leverage']}x | {p['elapsed_min']}분"
                )
            return "\n".join(lines)

        # PnL query
        if any(kw in text for kw in ["수익", "pnl", "손익", "성과", "실적"]):
            report = data.get("daily_report", {})
            if not report:
                return "📈 수익 데이터가 아직 없어요."
            pnl = report.get("total_pnl", 0)
            return (
                f"{'📈' if pnl >= 0 else '📉'} <b>수익 요약</b>\n"
                f"PnL: <b>${pnl:+,.2f}</b>\n"
                f"거래: {report.get('total_trades', 0)}건 | "
                f"승률: {report.get('win_rate', 0):.1%}"
            )

        # Status query
        if any(kw in text for kw in ["상태", "status", "어때", "괜찮"]):
            status = data.get("system_status", {})
            uptime = status.get("uptime_sec", 0)
            h, m = int(uptime // 3600), int((uptime % 3600) // 60)
            comps = status.get("components", {})
            all_ok = all(comps.values()) if comps else False
            return (
                f"📡 <b>시스템 상태</b>\n"
                f"상태: {'✅ 정상' if all_ok else '⚠️ 점검 필요'}\n"
                f"가동: {h}시간 {m}분\n"
                f"매매: {'⏸ 중지' if data.get('paused') else '▶️ 활성'}"
            )

        return "🤔 질문을 이해하지 못했어요. 잔고, 포지션, 수익, 상태 등을 물어보세요."

    async def poll_loop(self, interval: float = 1.0):
        """Main polling loop — system 토픽에서 메시지를 수신합니다."""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("Telegram not configured, chat handler disabled")
            return

        self._running = True
        system_thread_id = TOPIC_IDS.get("system")
        logger.info(
            "Telegram chat handler started (system topic thread_id=%s)",
            system_thread_id,
        )

        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    update_id = update["update_id"]

                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = msg.get("chat", {}).get("id")
                    thread_id = msg.get("message_thread_id")

                    # Only process messages from our chat
                    if str(chat_id) != str(TELEGRAM_CHAT_ID):
                        self._offset = update_id + 1
                        continue

                    # Only respond in system topic (or general chat if no topics)
                    if system_thread_id is not None and thread_id != system_thread_id:
                        self._offset = update_id + 1
                        continue

                    # Skip empty or bot messages
                    if not text or msg.get("from", {}).get("is_bot"):
                        self._offset = update_id + 1
                        continue

                    logger.info("Telegram chat: %s", text[:100])

                    # Process message BEFORE updating offset
                    try:
                        await self._handle_message(text, chat_id, thread_id)
                    except Exception:
                        logger.warning("Failed to handle message: %s", text[:100], exc_info=True)

                    # Update offset AFTER successful processing
                    self._offset = update_id + 1

            except Exception:
                logger.warning("Chat poll error", exc_info=True)
                await asyncio.sleep(5)

            await asyncio.sleep(interval)

    def stop(self):
        self._running = False


# Singleton instance
command_handler = TelegramChatHandler()
