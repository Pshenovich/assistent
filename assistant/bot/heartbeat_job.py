"""Пульс бота для /healthz: если очередь задач бота встала, пульс перестаёт обновляться."""

from __future__ import annotations

import asyncio

from telegram.ext import ContextTypes

from assistant.lib import heartbeat

INTERVAL_SEC = 60


async def heartbeat_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    name = (context.job.data if context.job else None) or "bot"
    try:
        await asyncio.to_thread(heartbeat.beat, name)
    except Exception as e:
        print(f"[heartbeat] write failed err={e!r}")


def register_heartbeat_jobs(app, *, name: str = "bot") -> None:
    if app.job_queue is None:
        print("[heartbeat] job_queue unavailable")
        return
    app.job_queue.run_repeating(
        heartbeat_tick, interval=INTERVAL_SEC, first=1, name=f"{name}_heartbeat", data=name
    )
