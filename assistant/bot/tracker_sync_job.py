"""Дубли задач Орбиты в трекерах (Битрикс24): раз в 5 минут подтянуть стадию, срок и исполнителя."""

from __future__ import annotations

import asyncio

from telegram.ext import ContextTypes

INTERVAL_SEC = 5 * 60


async def tracker_sync_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.services import tracker_sync

    changed = await asyncio.to_thread(tracker_sync.sync_due)
    if changed:
        print(f"[tracker_sync] tasks updated from trackers: {changed}")


def register_tracker_sync_jobs(app) -> None:
    if app.job_queue is None:
        return
    app.job_queue.run_repeating(tracker_sync_tick, interval=INTERVAL_SEC, first=180, name="tracker_sync")
    print(f"[tracker_sync] every {INTERVAL_SEC}s")
