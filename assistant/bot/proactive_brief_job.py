"""Утренний бриф «Фокуса» в Telegram: раз в день в выбранное время (по часовому поясу пользователя)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from assistant.bot.access_gate import is_user_allowed


def brief_enabled() -> bool:
    return os.getenv("PROACTIVE_BRIEF_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def poll_interval_sec() -> float:
    try:
        return max(60.0, float(os.getenv("PROACTIVE_BRIEF_POLL_SEC", "300") or "300"))
    except ValueError:
        return 300.0


def _user_ids() -> list[int]:
    from assistant.services import meeting_reminders as mr
    from assistant.stores import calendar_tasks

    ids = set(mr.iter_calendar_user_ids())
    try:
        ids |= set(calendar_tasks.list_owner_user_ids())
    except Exception:
        pass
    return sorted(int(i) for i in ids)


def _collect(uid: int) -> tuple[str, bool] | None:
    """Текст брифа или None, если слать не нужно. Второе значение — есть ли что показать."""
    from assistant.services import proactive, proactive_ai
    from assistant.services.calendar import _tz_for
    from assistant.stores import user_prefs

    prefs = user_prefs.proactive_prefs(uid)
    if not (prefs.get("brief") and prefs.get("focus")):
        return None
    tz = _tz_for(uid)
    local_now = datetime.now(tz)
    day = local_now.date().isoformat()
    if proactive.brief_already_sent(uid, day) or not proactive.brief_due(local_now, prefs.get("brief_time")):
        return None
    try:
        proactive_ai.run_pending(uid)
    except Exception as e:
        print(f"[brief] ai uid={uid} err={e!r}")
    now = proactive._now()
    try:
        events = proactive.events_for_user(uid, now - timedelta(days=2), now + timedelta(days=1))
    except Exception:
        events = []
    items = proactive.build_feed(uid, events, now=now)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = [e for e in events if (lambda s: s is not None and start <= s.astimezone(tz) < start + timedelta(days=1))(proactive._parse(e.get("start")))]
    proactive.mark_brief_sent(uid, day)
    if not items and not today:
        return ("", False)
    return (proactive.brief_message(items, today, tz, local_now), True)


def _weekly(uid: int) -> str | None:
    """Пятница 18:00–23:00 по времени пользователя, включены «Итоги недели», отчёта за неделю ещё нет."""
    from assistant.services import proactive_reports
    from assistant.services.calendar import _tz_for
    from assistant.stores import user_prefs

    prefs = user_prefs.proactive_prefs(uid)
    if not prefs.get("weekly"):
        return None
    local = datetime.now(_tz_for(uid))
    if local.weekday() != 4 or not (18 <= local.hour < 23):
        return None
    monday = (local - timedelta(days=local.weekday())).date().isoformat()
    if proactive_reports.report_note_id(uid, f"report:week:{monday}"):
        return None
    note = proactive_reports.weekly(uid)
    return str(note.get("title") or "Итоги недели")


async def proactive_brief_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not brief_enabled():
        return
    from assistant.lib.webapp_public import webapp_entry_url

    for uid in await asyncio.to_thread(_user_ids):
        if not is_user_allowed(uid, None):
            continue
        try:
            title = await asyncio.to_thread(_weekly, uid)
            if title:
                kb_w = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть итоги", web_app=WebAppInfo(url=webapp_entry_url()))]])
                await context.bot.send_message(chat_id=uid, text=f"<b>{title}</b> — готово: встречи, решения, что висит и что на следующую неделю. Заметка в проекте «От ассистента».", parse_mode=ParseMode.HTML, reply_markup=kb_w)
        except Exception as e:
            print(f"[weekly] uid={uid} err={e!r}")
        try:
            res = await asyncio.to_thread(_collect, uid)
        except Exception as e:
            print(f"[brief] uid={uid} err={e!r}")
            continue
        if not res or not res[1]:
            continue
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть Фокус", web_app=WebAppInfo(url=webapp_entry_url()))]])
        try:
            await context.bot.send_message(chat_id=uid, text=res[0], parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
            print(f"[brief] sent uid={uid}")
        except Exception as e:
            print(f"[brief] send uid={uid} err={e!r}")


def register_proactive_brief_jobs(app) -> None:
    if not brief_enabled() or app.job_queue is None:
        return
    interval = poll_interval_sec()
    app.job_queue.run_repeating(proactive_brief_tick, interval=interval, first=min(90.0, interval), name="proactive_brief")
