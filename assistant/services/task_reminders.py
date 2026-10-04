"""Напоминания за N минут до календарной задачи Leo."""

from __future__ import annotations

import html
import os
from datetime import datetime, timedelta
from typing import Any

from assistant.services import meeting_reminders as mr
from assistant.stores import calendar_tasks as calendar_tasks_store
from assistant.stores import meeting_reminders_sent as sent_store

TASK_REMINDER_CAL = "leo-tasks"


def reminder_minutes_before() -> int:
    raw = (os.getenv("TASK_REMINDER_MINUTES_BEFORE") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return mr.reminder_minutes_before()


def reminder_window_sec() -> float:
    raw = (os.getenv("TASK_REMINDER_WINDOW_SEC") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return mr.reminder_window_sec()


def iter_task_user_ids() -> list[int]:
    ids = set(mr.iter_calendar_user_ids())
    try:
        ids.update(calendar_tasks_store.list_owner_user_ids())
    except Exception:
        pass
    return sorted(ids)


def format_reminder_message(
    task: dict[str, Any], *, user_id: int, minutes_before: int
) -> str:
    from assistant.services.calendar import format_event_when
    from assistant.stores.calendar_tasks import _parse_dt

    title = html.escape(str(task.get("title") or "Задача"))
    start = _parse_dt(task.get("start_at"))
    end = _parse_dt(task.get("end_at"))
    when = ""
    if start is not None and end is not None:
        when = format_event_when(start, end, user_id)
    lines = [
        f"Через {minutes_before} минут у вас задача:",
        f"<b>{title}</b>",
    ]
    if when:
        lines.append(f"Время: {html.escape(when)}")
    return "\n".join(lines)


def collect_due_reminders(
    user_id: int, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    from assistant.services.calendar import _tz_for

    tz = _tz_for(user_id)
    now = now or datetime.now(tz)
    mins = reminder_minutes_before()
    window = reminder_window_sec() / 2.0
    target_sec = float(mins) * 60.0
    horizon = now + timedelta(minutes=mins + 12)
    due: list[dict[str, Any]] = []
    for row in calendar_tasks_store.list_tasks_in_window(user_id, now, horizon):
        if row.get("done"):
            continue
        tid = str(row.get("id") or "")
        start = calendar_tasks_store._parse_dt(row.get("start_at"))
        if not tid or start is None:
            continue
        start_iso = str(row.get("start_at") or "")
        if sent_store.was_sent(user_id, TASK_REMINDER_CAL, f"task-{tid}", start_iso):
            continue
        delta_sec = (start - now).total_seconds()
        if abs(delta_sec - target_sec) <= window:
            due.append(row)
    return due
