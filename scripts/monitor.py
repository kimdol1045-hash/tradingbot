"""
Background monitor — checks bot health every 5 minutes.
Sends Telegram alert if issues detected.
"""
import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notify.telegram import notify_system
from src.exchange.hyperliquid import get_info_client, fetch_wallet_balance

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | monitor | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("monitor")

CHECK_INTERVAL = 300  # 5 minutes
BOT_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "bot.log")

# Wallet addresses for balance checks
WALLET_ADDRESSES = {
    "s1": os.getenv("WALLET_ADDRESS_S1", ""),
    "s2": os.getenv("WALLET_ADDRESS_S2", ""),
    "s3": os.getenv("WALLET_ADDRESS_S3", ""),
    "s4": os.getenv("WALLET_ADDRESS_S4", ""),
}

# Track state across checks
last_log_size = 0
last_balances: dict[str, float] = {}
consecutive_stale = 0


def check_bot_process() -> str | None:
    """Check if bot process is alive."""
    import subprocess
    result = subprocess.run(
        ["pgrep", "-f", "python.*src/main.py"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return "봇 프로세스가 종료되었습니다! 재시작이 필요합니다."
    return None


def check_log_growth() -> str | None:
    """Check if bot log is still being written to."""
    global last_log_size, consecutive_stale
    try:
        size = os.path.getsize(BOT_LOG)
        if last_log_size > 0 and size == last_log_size:
            consecutive_stale += 1
            if consecutive_stale >= 6:  # 30 minutes no log activity
                consecutive_stale = 0
                return "봇 로그가 15분간 업데이트 없음 — WS 연결 확인 필요"
        else:
            consecutive_stale = 0
        last_log_size = size
    except FileNotFoundError:
        return "봇 로그 파일을 찾을 수 없습니다"
    return None


def check_recent_errors() -> str | None:
    """Check for critical errors in recent log."""
    try:
        with open(BOT_LOG, "rb") as f:
            # Read last 10KB
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 10240))
            content = f.read().decode("utf-8", errors="replace")

        lines = content.split("\n")
        # Only flag truly critical errors (not backfill rate limits or minor tracebacks)
        critical_errors = [line for line in lines if any(kw in line for kw in [
            "EMERGENCY", "CLOSE_ALL", "HALTED", "Fatal error",
        ])]

        if critical_errors:
            last_error = critical_errors[-1][:150]
            return f"크리티컬 에러 감지: {last_error}"
    except Exception:
        pass
    return None


async def check_balances() -> str | None:
    """Check if any wallet balance dropped significantly."""
    global last_balances
    try:
        info = get_info_client()
        alerts = []
        for agent_id, addr in WALLET_ADDRESSES.items():
            if not addr:
                continue
            try:
                balance = fetch_wallet_balance(info, addr)
                prev = last_balances.get(agent_id, 0)

                # Alert if balance dropped >20% since last check (and had a previous reading)
                if prev > 1.0 and balance < prev * 0.80:
                    alerts.append(
                        f"{agent_id}: ${prev:.2f} → ${balance:.2f} (-{(1 - balance/prev)*100:.1f}%)"
                    )

                last_balances[agent_id] = balance
            except Exception:
                pass

        if alerts:
            return "잔고 급감 감지:\n" + "\n".join(alerts)
    except Exception:
        pass
    return None


async def run_monitor():
    """Main monitoring loop."""
    logger.info("Monitor started (interval=%ds)", CHECK_INTERVAL)
    await notify_system("📡 모니터링 시작됨 (5분 간격)")

    while True:
        await asyncio.sleep(CHECK_INTERVAL)

        issues = []

        # 1. Process alive?
        issue = check_bot_process()
        if issue:
            issues.append(issue)

        # 2. Log growing?
        issue = check_log_growth()
        if issue:
            issues.append(issue)

        # 3. Recent critical errors?
        issue = check_recent_errors()
        if issue:
            issues.append(issue)

        # 4. Balance check
        issue = await check_balances()
        if issue:
            issues.append(issue)

        if issues:
            msg = "⚠️ <b>모니터링 알림</b>\n━━━━━━━━━━━━━━━\n" + "\n\n".join(issues)
            logger.warning("Issues found: %s", issues)
            await notify_system(msg)
        else:
            logger.info("All checks OK (balances: %s)",
                        {k: f"${v:.2f}" for k, v in last_balances.items()})


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    # Reload wallet addresses after dotenv
    for aid in ("s1", "s2", "s3", "s4"):
        addr = os.getenv(f"WALLET_ADDRESS_{aid.upper()}", "")
        if addr:
            WALLET_ADDRESSES[aid] = addr

    asyncio.run(run_monitor())
