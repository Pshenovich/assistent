"""Подписки Google Календаря (calendar_push): оформить новым, продлить истекающие (~раз в неделю)."""

from __future__ import annotations

import asyncio

from telegram.ext import ContextTypes

from assistant.lib import fanout
from assistant.services import calendar_push
from assistant.services import meeting_reminders as mr

INTERVAL_SEC = 6 * 3600


async def gcal_push_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    async def one(uid) -> None:
        res = await asyncio.to_thread(calendar_push.ensure_channels, int(uid))
        if res.get("created") or res.get("failed"):
            print(f"[gcal_push] uid={uid} {res}")

    users = await asyncio.to_thread(mr.iter_calendar_user_ids)
    # Подписка — несколько запросов к Google на человека: не больше 5 человек одновременно.
    await fanout.for_each_user(users, one, label="gcal_push", limit=5)


def register_gcal_push_jobs(app) -> None:
    if app.job_queue is None or not calendar_push.enabled():
        return
    app.job_queue.run_repeating(gcal_push_tick, interval=INTERVAL_SEC, first=90, name="gcal_push")
    print(f"[gcal_push] channels renew every {INTERVAL_SEC}s")
