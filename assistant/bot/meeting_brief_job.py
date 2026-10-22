"""B3: бриф перед встречей в Telegram (MEETING_BRIEFS_ENABLED, MEETING_BRIEF_MINUTES)."""

from __future__ import annotations

import asyncio

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from assistant.bot.access_gate import is_user_allowed
from assistant.bot.meeting_reminder_job import poll_interval_sec
from assistant.services import meeting_brief as mb
from assistant.services import meeting_reminders as mr
from assistant.stores import meeting_reminders_sent as sent_store
from assistant.stores import user_prefs


async def meeting_brief_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    for user_id in await asyncio.to_thread(mr.iter_calendar_user_ids):
        if not mb.briefs_enabled_for(user_id) or not is_user_allowed(user_id, None):
            continue
        if not user_prefs.meeting_reminders_enabled(user_id):
            continue
        try:
            due = await asyncio.to_thread(mb.events_due_for_brief, user_id)
        except Exception as e:
            print(f"[meeting_brief] user={user_id} fetch err={e!r}")
            continue
        for ev in due:
            cal_id = str(ev.get("calendar_id") or "")
            ev_id = str(ev.get("event_id") or "")
            start_iso = str(ev.get("start_iso") or "")
            # отмечаем сразу: бриф не должен приходить дважды даже при ошибке модели
            sent_store.mark_sent(user_id, cal_id, mb.brief_sent_id(ev_id), start_iso)
            try:
                text = await asyncio.to_thread(mb.build_brief_message, user_id, ev)
                if not text:
                    print(f"[meeting_brief] skip uid={user_id} ev={ev_id}: no history")
                    continue
                await context.bot.send_message(
                    chat_id=int(user_id), text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
                print(f"[meeting_brief] sent uid={user_id} ev={ev_id}")
            except Exception as e:
                print(f"[meeting_brief] send uid={user_id} ev={ev_id} err={e!r}")


def register_meeting_brief_jobs(app) -> None:
    if app.job_queue is None:
        return
    interval = poll_interval_sec()
    app.job_queue.run_repeating(meeting_brief_tick, interval=interval, first=interval + 7, name="meeting_brief")
    print(f"[meeting_brief] scheduled every {interval}s")
