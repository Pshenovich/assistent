"""Знакомство, сценарий «Голова забита делами»: одно сообщение → превью встреч, задач и напоминаний.

LLM только предлагает пункты; даты и время проверяет код. Ничего не создаётся,
пока человек не нажмёт «Добавить всё».
"""

from __future__ import annotations

import html
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

MAX_ITEMS = 12
_KIND_ORDER = {"meeting": 0, "task": 1, "reminder": 2}
_KIND_ICON = {"meeting": "📅", "task": "✅", "reminder": "⏰"}
_WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _parse_date(raw: Any, today: date) -> date | None:
    s = str(raw or "").strip()[:10]
    if not s:
        return None
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return None
    # Только ближайшие две недели — как в списке days, который видела модель.
    if d < today or d > today + timedelta(days=14):
        return None
    return d


def _parse_time(raw: Any) -> dt_time | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        hh, mm = s.split(":", 1)
        return dt_time(int(hh), int(mm[:2]))
    except (ValueError, TypeError):
        return None


def build_plan(raw_items: list[dict[str, Any]], *, now: datetime, tz: ZoneInfo) -> list[dict[str, Any]]:
    """Сырые пункты модели → проверенный план: kind, title, start (ISO), end (ISO|None), all_day."""
    today = now.date()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for it in raw_items:
        kind = str(it.get("kind") or "").strip().lower()
        title = " ".join(str(it.get("title") or "").split())[:200]
        if kind not in _KIND_ORDER or not title:
            continue
        day = _parse_date(it.get("date"), today)
        at = _parse_time(it.get("time"))
        # Встречу без времени не угадываем — это просто дело на день.
        if kind == "meeting" and at is None:
            kind = "task"
        if at is not None:
            start = datetime.combine(day or today, at, tzinfo=tz)
            if day is None and start <= now:
                start += timedelta(days=1)
        elif kind == "reminder":
            start = (
                datetime.combine(day, dt_time(9, 0), tzinfo=tz)
                if day is not None and day > today
                else now.replace(second=0, microsecond=0) + timedelta(hours=1)
            )
        else:
            start = datetime.combine(day or today, dt_time(0, 0), tzinfo=tz)
        end: datetime | None = None
        if kind == "meeting":
            end_t = _parse_time(it.get("end_time"))
            end = datetime.combine(start.date(), end_t, tzinfo=tz) if end_t else None
            if end is None or end <= start:
                try:
                    dur = int(it.get("duration_min") or 0)
                except (TypeError, ValueError):
                    dur = 0
                end = start + timedelta(minutes=dur if 5 <= dur <= 12 * 60 else 60)
        key = (kind, title.lower(), start.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "kind": kind,
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat() if end else None,
                "all_day": kind == "task" and at is None,
            }
        )
        if len(out) >= MAX_ITEMS:
            break
    out.sort(key=lambda x: (x["start"][:10], _KIND_ORDER[x["kind"]], x["start"]))
    return out


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "Сегодня"
    if d == today + timedelta(days=1):
        return "Завтра"
    return f"{_WD[d.weekday()].capitalize()}, {d.day:02d}.{d.month:02d}"


def _item_line(it: dict[str, Any]) -> str:
    start = datetime.fromisoformat(it["start"])
    when = "" if it.get("all_day") else start.strftime("%H:%M")
    if it.get("end") and it["kind"] == "meeting":
        when += "–" + datetime.fromisoformat(it["end"]).strftime("%H:%M")
    head = f"{when} " if when else ""
    return f"{_KIND_ICON[it['kind']]} {head}{html.escape(it['title'])}"


def plan_html(plan: list[dict[str, Any]], *, today: date) -> str:
    """Пункты по дням, HTML для Telegram."""
    lines: list[str] = []
    cur: date | None = None
    for it in plan:
        d = datetime.fromisoformat(it["start"]).date()
        if d != cur:
            if lines:
                lines.append("")
            lines.append(f"<b>{_day_label(d, today)}</b>")
            cur = d
        lines.append(_item_line(it))
    return "\n".join(lines)


def counts_label(plan: list[dict[str, Any]]) -> str:
    from collections import Counter

    c = Counter(it["kind"] for it in plan)
    parts = []
    for kind, forms in (
        ("meeting", ("встреча", "встречи", "встреч")),
        ("task", ("задача", "задачи", "задач")),
        ("reminder", ("напоминание", "напоминания", "напоминаний")),
    ):
        n = c.get(kind, 0)
        if n:
            parts.append(f"{n} {_plural(n, forms)}")
    return ", ".join(parts)


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def commit_plan(
    user_id: int,
    chat_id: int,
    plan: list[dict[str, Any]],
    *,
    tz: ZoneInfo,
    telegram_username: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Создаёт пункты. Возвращает (созданные, не получилось); напоминания — с reminder_id для планировщика."""
    from assistant.services import calendar as cal_svc
    from assistant.stores import calendar_tasks, reminders_store

    done: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for it in plan:
        try:
            if it["kind"] == "meeting":
                cal_svc.create_event(
                    int(user_id),
                    {"title": it["title"], "start": it["start"], "end": it["end"], "duration_min": 60},
                    telegram_username=telegram_username,
                )
                done.append(it)
            elif it["kind"] == "task":
                calendar_tasks.create_task(
                    int(user_id),
                    title=it["title"],
                    start_at=it["start"],
                    assignee_user_id=int(user_id),
                    all_day=bool(it.get("all_day")),
                    tz=tz,
                )
                done.append(it)
            else:
                rid = reminders_store.add_reminder(
                    user_id=int(user_id),
                    chat_id=int(chat_id),
                    task=it["title"],
                    when_iso=it["start"],
                )
                done.append({**it, "reminder_id": rid})
        except Exception as e:
            print(f"[onboarding_day] create failed uid={user_id} kind={it.get('kind')} err={e!r}")
            failed.append(it)
    return done, failed
