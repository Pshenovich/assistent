"""Периодическая синхронизация подключённой почты и разбор писем для «Фокуса»."""

from __future__ import annotations

import asyncio

from telegram.ext import Application, ContextTypes

from assistant.services import mail_source


async def mail_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not mail_source.ingest_enabled():
        return
    try:
        n = await asyncio.to_thread(mail_source.poll_all_users)
        if n:
            print(f"[mail] new={n}")
    except Exception as e:
        print(f"[mail] err={e!r}")


def register_mail_jobs(app: Application) -> None:
    if not mail_source.ingest_enabled():
        return
    interval = mail_source.poll_interval_sec()
    app.job_queue.run_repeating(mail_tick, interval=interval, first=60.0, name="mail_ingest")
    print(f"[mail] enabled interval={interval}s")
