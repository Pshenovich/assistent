"""Периодическая отправка meeting bot на запланированные Zoom-встречи."""

from __future__ import annotations

import asyncio
import os

from telegram.ext import ContextTypes

from assistant.services import meeting_record_reconcile as reconcile
from assistant.services import meeting_record_schedule as sched


def scheduler_enabled() -> bool:
    if not sched.service_available():
        return False
    raw = os.getenv("MEETING_BOT_SCHEDULER_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def poll_interval_sec() -> float:
    try:
        return max(15.0, float(os.getenv("MEETING_BOT_POLL_SEC", "30") or "30"))
    except ValueError:
        return 30.0


async def meeting_bot_scheduler_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not scheduler_enabled():
        return
    try:
        n_dispatch = await asyncio.to_thread(sched.dispatch_due_jobs)
        n_poll = await asyncio.to_thread(reconcile.poll_joining_jobs)
        if n_dispatch or n_poll:
            print(f"[meeting_bot_scheduler] dispatched={n_dispatch} notified={n_poll}")
    except Exception as e:
        print(f"[meeting_bot_scheduler] err={e!r}")


def register_meeting_bot_scheduler_jobs(app) -> None:
    if not scheduler_enabled():
        return
    interval = poll_interval_sec()
    app.job_queue.run_repeating(
        meeting_bot_scheduler_tick,
        interval=interval,
        first=10.0,
        name="meeting_bot_scheduler",
    )
    print(f"[meeting_bot_scheduler] enabled interval={interval}s")
