"""
Async utility helpers — safe fire-and-forget task wrapper.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def safe_fire_and_forget(coro, *, name: str = ""):
    """
    Schedule a coroutine as a task with proper exception logging.

    Unlike bare asyncio.ensure_future(), this ensures exceptions are
    logged instead of silently swallowed.
    """
    try:
        loop = asyncio.get_event_loop()
        if not loop.is_running():
            return
        task = loop.create_task(coro)
    except RuntimeError:
        return  # No running event loop (e.g., in sync tests)

    def _on_done(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            label = f" [{name}]" if name else ""
            logger.error("Async task%s failed: %s", label, exc, exc_info=exc)

    task.add_done_callback(_on_done)
    return task
