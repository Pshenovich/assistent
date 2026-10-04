"""Периодическая отправка напоминаний за 15 минут до задачи."""

from __future__ import annotations

import asyncio
import os

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from assistant.bot.access_gate import is_user_allowed
from assistant.services import task_reminders as tr
from assistant.stores import meeting_reminders_sent as sent_store
from assistant.stores import user_prefs


def reminders_enabled() -> bool:
    raw = os.getenv("TASK_REMINDERS_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def poll_interval_sec() -> float:
    try:
        return max(30.0, float(os.getenv("TASK_REMINDER_POLL_SEC", "60") or "60"))
    except ValueError:
        return 60.0


async def task_reminder_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not reminders_enabled():
        return
    bot = context.bot
    mins = tr.reminder_minutes_before()

    for user_id in await asyncio.to_thread(tr.iter_task_user_ids):
        if not is_user_allowed(user_id, None):
            continue
        if not user_prefs.task_reminders_enabled(user_id):
            continue
        try:
            due = await asyncio.to_thread(tr.collect_due_reminders, user_id)
        except Exception as e:
            print(f"[task_reminder] user={user_id} fetch err={e!r}")
            continue
        for task in due:
            tid = str(task.get("id") or "")
            start_iso = str(task.get("start_at") or "")
            try:
                text = tr.format_reminder_message(
                    task, user_id=user_id, minutes_before=mins
                )
                await bot.send_message(
                    chat_id=int(user_id),
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                sent_store.mark_sent(
                    user_id, tr.TASK_REMINDER_CAL, f"task-{tid}", start_iso
                )
                print(f"[task_reminder] sent uid={user_id} task={tid}")
            except Exception as e:
                print(f"[task_reminder] send uid={user_id} task={tid} err={e!r}")


def register_task_reminder_jobs(app) -> None:
    if not reminders_enabled():
        return
    if app.job_queue is None:
        print("[task_reminder] job_queue unavailable")
        return
    interval = poll_interval_sec()
    app.job_queue.run_repeating(
        task_reminder_tick,
        interval=interval,
        first=interval,
        name="task_reminder_15m",
    )
    print(f"[task_reminder] scheduled every {interval}s")
