"""Восстановление и периодическая проверка напоминаний из reminders.json."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from assistant.config import CALENDAR_TZ
from assistant.skills import reminders as reminders_skill
from assistant.stores import reminders_store


def poll_interval_sec() -> float:
    try:
        return max(30.0, float(os.getenv("REMINDER_POLL_SEC", "60") or "60"))
    except ValueError:
        return 60.0


def _calendar_tz() -> ZoneInfo:
    return ZoneInfo(CALENDAR_TZ)


def _active_reminders() -> list[dict]:
    rows: list[dict] = []
    for rec in reminders_store.load_reminders():
        if not isinstance(rec, dict):
            continue
        if rec.get("done"):
            continue
        rid = str(rec.get("id") or "").strip()
        if not rid:
            continue
        rows.append(rec)
    return rows


def sync_reminder_schedules(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Планирует job_queue для будущих напоминаний. Возвращает число запланированных."""
    tz = _calendar_tz()
    now = datetime.now(tz)
    scheduled = 0
    for rec in _active_reminders():
        when = reminders_store.parse_when_iso(rec.get("when_iso"), tz=tz)
        if when is None or when <= now:
            continue
        chat_id = int(rec.get("chat_id") or 0)
        user_id = int(rec.get("user_id") or 0)
        task = str(rec.get("task") or "").strip()
        if not chat_id:
            continue
        reminders_skill.schedule_reminder_job(
            context,
            reminder_id=str(rec.get("id") or ""),
            chat_id=chat_id,
            user_id=user_id,
            task=task,
            when=when,
            now=now,
        )
        scheduled += 1
    return scheduled


async def deliver_missed_reminders(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отправляет просроченные напоминания, которые ещё не уходили в чат."""
    tz = _calendar_tz()
    now = datetime.now(tz)
    sent = 0
    for rec in _active_reminders():
        if rec.get("reminder_message_id"):
            continue
        when = reminders_store.parse_when_iso(rec.get("when_iso"), tz=tz)
        if when is None or when > now:
            continue
        rid = str(rec.get("id") or "").strip()
        chat_id = int(rec.get("chat_id") or 0)
        task = str(rec.get("task") or "").strip()
        if not rid or not chat_id:
            continue
        try:
            await reminders_skill.deliver_reminder(
                context,
                reminder_id=rid,
                chat_id=chat_id,
                task=task,
            )
            sent += 1
            print(f"[reminder] delivered missed id={rid} chat={chat_id}")
        except Exception as e:
            print(f"[reminder] missed send id={rid} err={e!r}")
    return sent


async def bootstrap_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    scheduled = await asyncio.to_thread(sync_reminder_schedules, context)
    sent = await deliver_missed_reminders(context)
    if scheduled or sent:
        print(f"[reminder] bootstrap scheduled={scheduled} missed_sent={sent}")


async def reminder_poll_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.to_thread(sync_reminder_schedules, context)
    await deliver_missed_reminders(context)


def register_reminder_jobs(app) -> None:
    if app.job_queue is None:
        print("[reminder] job_queue unavailable")
        return
    app.job_queue.run_once(bootstrap_reminders, when=1, name="reminder_bootstrap")
    interval = poll_interval_sec()
    app.job_queue.run_repeating(
        reminder_poll_tick,
        interval=interval,
        first=interval,
        name="reminder_poll",
    )
    print(f"[reminder] bootstrap on start, poll every {interval}s")
