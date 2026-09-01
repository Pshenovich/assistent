"""Периодическая отправка напоминаний за 15 минут до встречи."""

from __future__ import annotations

import asyncio
import os

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from assistant.bot.access_gate import is_user_allowed
from assistant.services import meeting_reminders as mr
from assistant.stores import meeting_reminders_sent as sent_store
from assistant.stores import user_prefs


def reminders_enabled() -> bool:
    raw = os.getenv("MEETING_REMINDERS_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def poll_interval_sec() -> float:
    try:
        return max(30.0, float(os.getenv("MEETING_REMINDER_POLL_SEC", "60") or "60"))
    except ValueError:
        return 60.0


async def meeting_reminder_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not reminders_enabled():
        return
    bot = context.bot
    mins = mr.reminder_minutes_before()

    for user_id in await asyncio.to_thread(mr.iter_calendar_user_ids):
        if not is_user_allowed(user_id, None):
            continue
        if not user_prefs.meeting_reminders_enabled(user_id):
            continue
        try:
            due = await asyncio.to_thread(mr.collect_due_reminders, user_id)
        except Exception as e:
            print(f"[meeting_reminder] user={user_id} fetch err={e!r}")
            continue
        for ev in due:
            cal_id = str(ev.get("calendar_id") or "")
            ev_id = str(ev.get("event_id") or "")
            start_iso = str(ev.get("start_iso") or "")
            try:
                text, kb = mr.format_reminder_message(
                    ev, user_id=user_id, minutes_before=mins
                )
                await bot.send_message(
                    chat_id=int(user_id),
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=kb,
                )
                sent_store.mark_sent(user_id, cal_id, ev_id, start_iso)
                print(
                    f"[meeting_reminder] sent uid={user_id} cal={cal_id} ev={ev_id}"
                )
            except Exception as e:
                print(f"[meeting_reminder] send uid={user_id} ev={ev_id} err={e!r}")


def register_meeting_reminder_jobs(app) -> None:
    if not reminders_enabled():
        return
    if app.job_queue is None:
        print("[meeting_reminder] job_queue unavailable")
        return
    interval = poll_interval_sec()
    app.job_queue.run_repeating(
        meeting_reminder_tick,
        interval=interval,
        first=interval,
        name="meeting_reminder_15m",
    )
    print(f"[meeting_reminder] scheduled every {interval}s")
