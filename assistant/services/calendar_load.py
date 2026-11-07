"""Нагрузка календаря: встречи, время без встреч, работа вне рабочих часов — и «на что ушло время».

Источники в одном виде: зеркало Google (``stores/calendar_mirror``) или живой запрос, встречи Орбиты,
задачи со временем из ``calendar_tasks``. Правила (контекст, не оценка):

* встреча — событие, где кроме человека есть хотя бы один участник (переговорки не считаются);
  событие без участников — личный блок работы, он не мешает «времени без встреч»;
* отклонённые, отменённые, «свободен» (transparency) и события на весь день не считаются встречами;
  «нет на месте» на весь день — день выпадает, как отпуск;
* задачи Орбиты, продублированные в Google, берём из Орбиты (в Google они помечены — пропускаем);
* пересекающиеся встречи не складываются: считаем объединение интервалов;
* время без встреч — свободные окна от 2 часов подряд в рабочих часах (9–19) рабочего дня;
* вне рабочих часов — часы встреч до 9, после 19 и в выходные.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

WORK_START = 9
WORK_END = 19
FOCUS_MIN_BLOCK = 120  # минут подряд без встреч — «окно для работы»
LOW_FOCUS_MINUTES = 120  # меньше — день без такого окна


def _item(source: str, row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Строка зеркала (или as_gcal → row_from_gcal) → элемент нагрузки; None — не считаем вовсе."""
    if row.get("my_response") == "declined" or row.get("transparent"):
        return None
    kind = row.get("kind") or "meeting"
    if kind in ("task", "other"):
        return None
    if row.get("all_day") and kind != "ooo":
        return None
    if kind == "meeting" and int(row.get("others") or 0) == 0:
        kind = "block"
    if kind == "focus":
        kind = "block"
    return {
        "source": source,
        "kind": kind,
        "title": str(row.get("title") or "").strip() or "Без названия",
        "series": str(row.get("series_id") or ""),
        "start": row["start"],
        "end": row["end"],
        "all_day": bool(row.get("all_day")),
        "others": int(row.get("others") or 0),
        "emails": [e for e in str(row.get("other_emails") or "").split(",") if e],
    }


def _from_gcal(source: str, raw: dict[str, Any], start: datetime, end: datetime) -> Optional[dict[str, Any]]:
    from assistant.stores import calendar_mirror

    row = calendar_mirror.row_from_gcal(raw, start, end)
    if str(raw.get("status") or "") == "cancelled":
        return None
    return _item(source, {**row, "start": start, "end": end})


def collect(user_id: int, start: datetime, end: datetime, *, live: bool = False) -> dict[str, Any]:
    """Всё, что занимало время человека в [start, end). live — Google живым запросом (для Лео);
    иначе — зеркало (для показателей, без запросов к Google). ``covered_from`` — с какого момента
    у зеркала есть история (None — Google не подключён)."""
    from assistant.services import calendar as cal
    from assistant.services import calendar_sources, orbita_calendar
    from assistant.stores import calendar_mirror

    uid = int(user_id)
    items: list[dict[str, Any]] = []
    covered_from: Optional[datetime] = None
    google = False
    try:
        google = cal.has_google(uid)
    except Exception:
        google = False
    if google:
        if live:
            for e in calendar_sources.list_events_in_window(uid, start, end):
                it = _from_gcal("google", e["raw"], e["start"], e["end"]) if isinstance(e.get("raw"), dict) else None
                if it:
                    items.append(it)
            covered_from = start
        else:
            for r in calendar_mirror.events(uid, start, end):
                it = _item("google", r)
                if it:
                    items.append(it)
            covered_from = calendar_mirror.synced_from(uid)
    try:
        for ev in orbita_calendar.events_for(uid, start, end):
            s = datetime.fromisoformat(ev["start"]["dateTime"])
            e = datetime.fromisoformat(ev["end"]["dateTime"])
            it = _from_gcal("orbita", ev, s, e)
            if it:
                items.append(it)
    except Exception as e:
        print(f"[calendar_load] orbita events uid={uid} err={e!r}")
    items += _task_items(uid, start, end)
    items.sort(key=lambda x: x["start"])
    return {"items": items, "google": google, "covered_from": covered_from}


def _task_items(uid: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    from assistant.stores import calendar_tasks

    out = []
    me = str(uid)
    for t in calendar_tasks.list_involved_tasks(me, limit=3000):
        assignee = str(t.get("assignee_user_id") or "")
        mine = assignee == me or (str(t.get("owner_user_id") or "") == me and assignee in ("", me))
        if not mine or t.get("all_day") or t.get("outcome") == "cancelled" or t.get("assignee_triage") == "declined":
            continue
        try:
            s = datetime.fromisoformat(str(t.get("start_at") or ""))
            e = datetime.fromisoformat(str(t.get("end_at") or ""))
        except ValueError:
            continue
        if s.tzinfo is None or e <= s or e - s > timedelta(hours=12) or not (s < end and e > start):
            continue
        out.append({"source": "task", "kind": "task", "title": str(t.get("title") or "Задача"), "series": "",
                    "start": s, "end": e, "all_day": False, "others": 0, "emails": []})
    return out


# ---------- интервалы ----------


def _union(spans: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _minutes(spans: Iterable[tuple[datetime, datetime]]) -> float:
    return sum((e - s).total_seconds() for s, e in spans) / 60


def _clip(spans: list[tuple[datetime, datetime]], a: datetime, b: datetime) -> list[tuple[datetime, datetime]]:
    return [(max(s, a), min(e, b)) for s, e in spans if s < b and e > a]


def _day_bounds(d: date, tz) -> tuple[datetime, datetime, datetime, datetime]:
    day0 = datetime.combine(d, time.min, tzinfo=tz)
    return day0, day0 + timedelta(days=1), day0.replace(hour=WORK_START), day0.replace(hour=WORK_END)


def day_stats(items: list[dict[str, Any]], d: date, tz) -> dict[str, float]:
    day0, day1, w0, w1 = _day_bounds(d, tz)
    meetings = _union(_clip([(i["start"], i["end"]) for i in items if i["kind"] == "meeting"], day0, day1))
    in_work = _clip(meetings, w0, w1)
    meet = _minutes(meetings)
    after = meet - _minutes(in_work) if d.weekday() < 5 else meet
    focus = 0.0
    cur = w0
    for s, e in in_work + [(w1, w1)]:
        gap = (s - cur).total_seconds() / 60
        if gap >= FOCUS_MIN_BLOCK:
            focus += gap
        cur = max(cur, e)
    tasks = _minutes(_union(_clip([(i["start"], i["end"]) for i in items if i["kind"] == "task"], day0, day1)))
    return {"meeting": meet, "after_hours": after, "focus": focus, "tasks": tasks}


def summary(
    user_id: int,
    *,
    start: datetime,
    end: datetime,
    now: Optional[datetime] = None,
    tz: ZoneInfo | timezone = timezone.utc,
    absences: Iterable[dict[str, str]] = (),
    live: bool = False,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Нагрузка за прошедшую часть [start, end): по рабочим дням без отпуска и «нет на месте»."""
    now = now or datetime.now(timezone.utc)
    end = min(end, now)
    data = data or collect(user_id, start, end, live=live)
    items = data["items"]
    covered = data.get("covered_from")
    if not data["google"] and not any(i["source"] == "orbita" for i in items):
        return {"has_calendar": False}
    first = start
    if data["google"] and covered is not None and covered > start:
        first = covered
    if data["google"] and covered is None:
        return {"has_calendar": True, "workdays": 0, "covered_from": None}
    absences = list(absences)
    ooo_days = set()
    for i in items:
        if i["kind"] == "ooo":
            d, last = i["start"].astimezone(tz).date(), (i["end"] - timedelta(seconds=1)).astimezone(tz).date()
            while d <= last:
                ooo_days.add(d)
                d += timedelta(days=1)
    d = first.astimezone(tz).date()
    last = (end - timedelta(seconds=1)).astimezone(tz).date()
    workdays = 0
    tot = Counter()
    low_focus = 0
    while d <= last:
        st = day_stats(items, d, tz)
        if d.weekday() >= 5:
            tot["after_hours"] += st["meeting"]  # выходные — всё вне рабочего времени
        elif not _absent(d, absences) and d not in ooo_days:
            workdays += 1
            for k in ("meeting", "after_hours", "focus", "tasks"):
                tot[k] += st[k]
            low_focus += st["focus"] < LOW_FOCUS_MINUTES
        d += timedelta(days=1)
    meetings = [i for i in items if i["kind"] == "meeting" and first <= i["start"] < end]
    per_day = (lambda m: round(m / 60 / workdays, 1)) if workdays else (lambda m: None)
    return {
        "has_calendar": True,
        "covered_from": first.isoformat() if first > start else None,
        "workdays": workdays,
        "meetings": len(meetings),
        "meeting_hours": round(tot["meeting"] / 60, 1),
        "meeting_hours_per_day": per_day(tot["meeting"]),
        "focus_hours_per_day": per_day(tot["focus"]),
        "low_focus_days": low_focus,
        "after_hours": round(tot["after_hours"] / 60, 1),
        "task_hours": round(tot["tasks"] / 60, 1),
        "enough_data": workdays >= 5,
    }


def _absent(d: date, absences: list[dict[str, str]]) -> bool:
    iso = d.isoformat()
    return any(a.get("from", "") <= iso <= a.get("to", "") for a in absences)


def breakdown(items: list[dict[str, Any]], *, start: datetime, end: datetime, names: dict[str, str] | None = None,
              limit: int = 10) -> dict[str, Any]:
    """На что ушло время: по сериям/названиям, по людям, по видам. Только для самого человека."""
    names = names or {}
    by_title: Counter = Counter()
    by_person: Counter = Counter()
    by_kind: Counter = Counter()
    for i in items:
        if i["kind"] == "ooo" or i["all_day"]:
            continue
        s, e = max(i["start"], start), min(i["end"], end)
        if e <= s:
            continue
        m = (e - s).total_seconds() / 60
        by_kind[i["kind"]] += m
        by_title[i["title"]] += m
        if i["kind"] == "meeting":
            for em in i["emails"]:
                by_person[names.get(em) or em] += m
    h = lambda m: round(m / 60, 1)  # noqa: E731
    label = {"meeting": "встречи", "block": "свои блоки в календаре", "task": "задачи в календаре"}
    return {
        "by_kind_hours": {label.get(k, k): h(v) for k, v in by_kind.most_common()},
        "top_titles_hours": [[k, h(v)] for k, v in by_title.most_common(limit)],
        "top_people_hours": [[k, h(v)] for k, v in by_person.most_common(limit)],
    }
