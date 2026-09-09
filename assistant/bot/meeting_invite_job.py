"""Периодическая рассылка RSVP по входящим приглашениям Google Calendar."""

from __future__ import annotations

import asyncio
import os

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from assistant.bot.access_gate import is_user_allowed
from assistant.services import meeting_invites as inv
from assistant.services import meeting_reminders as mr
from assistant.stores import meeting_invites_notified as notified


def poll_interval_sec() -> float:
    try:
        return max(30.0, float(os.getenv("MEETING_INVITE_POLL_SEC", "60") or "60"))
    except ValueError:
        return 60.0


async def meeting_invite_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not inv.invites_enabled():
        return
    bot = context.bot
    for user_id in await asyncio.to_thread(mr.iter_calendar_user_ids):
        if not is_user_allowed(user_id, None):
            continue
        try:
            pending = await asyncio.to_thread(inv.collect_pending_incoming_invites, user_id)
        except Exception as e:
            print(f"[meeting_invite] user={user_id} fetch err={e!r}")
            continue
        for ev in pending:
            ev_id = str(ev.get("event_id") or "")
            if not ev_id or notified.was_notified(user_id, ev_id):
                continue
            try:
                text, token = inv.prepare_incoming_invite(user_id, ev)
                await bot.send_message(
                    chat_id=int(user_id),
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=inv.build_invite_keyboard(token),
                )
                notified.mark_notified(user_id, ev_id)
                print(f"[meeting_invite] incoming uid={user_id} ev={ev_id}")
            except Exception as e:
                print(f"[meeting_invite] incoming uid={user_id} ev={ev_id} err={e!r}")


def register_meeting_invite_jobs(app) -> None:
    if not inv.invites_enabled():
        return
    if app.job_queue is None:
        print("[meeting_invite] job_queue unavailable")
        return
    interval = poll_interval_sec()
    app.job_queue.run_repeating(
        meeting_invite_tick,
        interval=interval,
        first=min(20.0, interval),
        name="meeting_invite_incoming",
    )
    print(f"[meeting_invite] scheduled every {interval}s")
