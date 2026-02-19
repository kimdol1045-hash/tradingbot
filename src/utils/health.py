"""
Health Check — lightweight status reporter.
Exposes system health via a simple HTTP endpoint on port 8080.
Used by systemd watchdog, Docker HEALTHCHECK, and monitoring.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from asyncio import StreamReader, StreamWriter

logger = logging.getLogger(__name__)

# Global references (set by main.py at startup)
_components: dict = {}
_start_time: float = 0.0


def register_components(
    collector=None,
    pipeline=None,
    position_manager=None,
    equity_tracker=None,
    evolver=None,
    advisor=None,
) -> None:
    """Register live component references for health reporting."""
    global _start_time
    _start_time = time.time()
    if collector:
        _components["collector"] = collector
    if pipeline:
        _components["pipeline"] = pipeline
    if position_manager:
        _components["position_manager"] = position_manager
    if equity_tracker:
        _components["equity_tracker"] = equity_tracker
    if evolver:
        _components["evolver"] = evolver
    if advisor:
        _components["advisor"] = advisor


def _build_status() -> dict:
    """Build health status dict."""
    now = time.time()
    uptime = now - _start_time if _start_time else 0

    status = {
        "status": "ok",
        "uptime_seconds": int(uptime),
        "uptime_human": _fmt_duration(uptime),
        "timestamp": int(now * 1000),
        "checks": {},
    }

    # Collector
    collector = _components.get("collector")
    if collector:
        ws_connected = getattr(collector, "_ws_connected", False)
        last_msg_ts = getattr(collector, "_last_msg_ts", 0)
        age = now - last_msg_ts if last_msg_ts else -1
        status["checks"]["collector"] = {
            "ws_connected": ws_connected,
            "last_message_age_sec": round(age, 1) if age >= 0 else None,
            "healthy": ws_connected and (age < 60 if age >= 0 else True),
        }

    # Position Manager
    pm = _components.get("position_manager")
    if pm:
        open_count = sum(
            1 for p in pm.positions.values() if p.status in ("pending", "open")
        )
        status["checks"]["positions"] = {
            "open_count": open_count,
            "dry_run": pm.dry_run,
            "healthy": True,
        }

    # Equity Tracker
    et = _components.get("equity_tracker")
    if et:
        portfolio_mdd = et.get_portfolio_mdd() if hasattr(et, "get_portfolio_mdd") else 0
        status["checks"]["equity"] = {
            "portfolio_mdd": round(portfolio_mdd, 4),
            "healthy": portfolio_mdd < 0.10,
        }

    # Evolver
    evolver = _components.get("evolver")
    if evolver:
        last_cycle = evolver._last_cycle_ts
        cycles_run = len(evolver.cycle_history)
        status["checks"]["evolver"] = {
            "cycles_run": cycles_run,
            "last_cycle_ts": last_cycle,
            "healthy": True,
        }

    # Market Advisor
    advisor = _components.get("advisor")
    if advisor:
        last_update = advisor._last_update_ts
        age = now - last_update if last_update > 0 else -1
        status["checks"]["advisor"] = {
            "symbols_tracked": len(advisor._advice),
            "last_update_age_sec": round(age, 1) if age >= 0 else None,
            "healthy": True,
        }

    # Overall health
    all_healthy = all(
        c.get("healthy", True) for c in status["checks"].values()
    )
    status["status"] = "ok" if all_healthy else "degraded"

    return status


def _fmt_duration(seconds: float) -> str:
    """Format seconds as human readable duration."""
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


# ═══ Simple HTTP Server ═══

async def _handle_request(reader: StreamReader, writer: StreamWriter) -> None:
    """Handle a single HTTP request."""
    try:
        data = await asyncio.wait_for(reader.read(1024), timeout=5.0)
        request_line = data.decode().split("\r\n")[0] if data else ""

        if "GET /health" in request_line or "GET / " in request_line:
            status = _build_status()
            body = json.dumps(status, indent=2)
            code = 200 if status["status"] == "ok" else 503
            response = (
                f"HTTP/1.1 {code} {'OK' if code == 200 else 'Service Unavailable'}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
                f"{body}"
            )
        else:
            response = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

        writer.write(response.encode())
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def start_health_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the health check HTTP server."""
    server = await asyncio.start_server(_handle_request, host, port)
    logger.info("Health check server listening on %s:%d", host, port)
    async with server:
        await server.serve_forever()
