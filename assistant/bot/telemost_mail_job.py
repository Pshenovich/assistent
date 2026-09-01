"""Периодическая проверка почты на конспекты Телемоста."""

from __future__ import annotations

import asyncio

from telegram.ext import Application, ContextTypes

from assistant.integrations import telemost_mail_ingest


def scheduler_enabled() -> bool:
    return telemost_mail_ingest.enabled()


def poll_interval_sec() -> float:
    return telemost_mail_ingest.poll_interval_sec()


async def telemost_mail_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not scheduler_enabled():
        return
    try:
        n = await asyncio.to_thread(telemost_mail_ingest.poll_all_users)
        if n:
            print(f"[telemost_mail] processed={n}")
    except Exception as e:
        print(f"[telemost_mail] err={e!r}")


def register_telemost_mail_jobs(app: Application) -> None:
    if not scheduler_enabled():
        return
    interval = poll_interval_sec()
    app.job_queue.run_repeating(
        telemost_mail_tick,
        interval=interval,
        first=30.0,
        name="telemost_mail_ingest",
    )
    print(f"[telemost_mail] enabled interval={interval}s")
