"""Календарь для ИИ в обсуждениях мини-приложения (tool calling).

Чтение (встречи, слоты, занятость, статистика) — сразу; создание и перенос встреч,
задачи и напоминания — сразу, с записью в журнал (кнопка «Отменить» в ответе);
удаление — только предложение, пользователь подтверждает кнопкой.

Всегда работает с календарём спрашивающего (uid из авторизации), а не владельца заметки.
Тяжёлые зависимости usage_server (синхронизация задач с Google, уведомления) передаются
через GptCalendarContext — модуль не импортирует usage_server.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Optional

MAX_RANGE_DAYS = 62
MAX_EVENTS_OUT = 120


# ---------------------------------------------------------------- журнал действий

def _conn() -> sqlite3.Connection:
    from assistant.stores import notes as notes_store

    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gpt_calendar_actions (
            id TEXT PRIMARY KEY,
            owner_uid INTEGER NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            before_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def _lock():
    from assistant.stores import notes as notes_store

    return notes_store._LOCK  # type: ignore[attr-defined]


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def record_action(
    owner_uid: int,
    kind: str,
    status: str,
    *,
    title: str,
    payload: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> str:
    aid = uuid.uuid4().hex[:16]
    now = _now_iso()
    with _lock():
        conn = _conn()
        conn.execute(
            "INSERT INTO gpt_calendar_actions (id, owner_uid, kind, status, title, payload_json, before_json, result_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                aid,
                int(owner_uid),
                kind,
                status,
                title[:300],
                json.dumps(payload or {}, ensure_ascii=False, default=str),
                json.dumps(before or {}, ensure_ascii=False, default=str),
                json.dumps(result or {}, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )
        conn.commit()
    return aid


def get_action(action_id: str) -> dict[str, Any] | None:
    with _lock():
        row = _conn().execute(
            "SELECT * FROM gpt_calendar_actions WHERE id = ?", (str(action_id or ""),)
        ).fetchone()
    if not row:
        return None
    keys = [
        "id", "owner_uid", "kind", "status", "title",
        "payload_json", "before_json", "result_json", "created_at", "updated_at",
    ]
    d = dict(zip(keys, tuple(row)))
    for k in ("payload_json", "before_json", "result_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k) or "{}")
        except (TypeError, ValueError):
            d[k[:-5]] = {}
    return d


def set_action_status(action_id: str, status: str, *, result: dict[str, Any] | None = None) -> None:
    with _lock():
        conn = _conn()
        if result is None:
            conn.execute(
                "UPDATE gpt_calendar_actions SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now_iso(), action_id),
            )
        else:
            conn.execute(
                "UPDATE gpt_calendar_actions SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                (status, json.dumps(result, ensure_ascii=False, default=str), _now_iso(), action_id),
            )
        conn.commit()


def public_action(a: dict[str, Any], *, viewer_uid: int | None = None) -> dict[str, Any]:
    """Карточка для клиента — без снимков «до»."""
    res = a.get("result") or {}
    return {
        "id": a["id"],
        "kind": a["kind"],
        "status": a["status"],
        "title": a.get("title") or "",
        "when": res.get("when") or (a.get("payload") or {}).get("when") or "",
        "detail": res.get("detail") or (a.get("payload") or {}).get("detail") or "",
        "link": res.get("html_link") or "",
        "mine": viewer_uid is not None and int(a.get("owner_uid") or 0) == int(viewer_uid),
    }


# ---------------------------------------------------------------- контекст

@dataclass
class GptCalendarContext:
    uid: int
    telegram_username: str | None = None
    display_name: str = ""
    sync_task_google: Optional[Callable[[int, dict, str], Any]] = None
    notify_task_assignee: Optional[Callable[..., Any]] = None
    actions: list[str] = field(default_factory=list)
    created_events: list[dict[str, Any]] = field(default_factory=list)
    updated_events: list[dict[str, Any]] = field(default_factory=list)


def has_google_calendar(uid: int) -> bool:
    try:
        from assistant.integrations.google_calendar_oauth import user_token_path

        return user_token_path(int(uid)).is_file()
    except Exception:
        return False


def _tz(uid: int):
    from assistant.services.calendar import _tz_for

    return _tz_for(int(uid))


def now_prompt_line(uid: int) -> str:
    tz = _tz(uid)
    now = datetime.now(tz)
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return (
        f"Сейчас {now.strftime('%Y-%m-%d %H:%M')} ({days[now.weekday()]}), "
        f"часовой пояс пользователя {getattr(tz, 'key', str(tz))}."
    )


SYSTEM_ADDON = """У тебя есть доступ к календарю пользователя через инструменты (calendar_*, task_create, reminder_create, contacts_find, propose_delete).
- На вопросы про встречи, задачи, напоминания, занятость, свободное время, «с кем», «на что уходит время» — сначала посмотри данные инструментами, не выдумывай.
- Встречу/перенос/задачу/напоминание создавай сразу инструментом, если просьба понятна (время, участники). Если инструмент вернул конфликт или «контакт не найден» — не повторяй вслепую: предложи пользователю варианты времени из ответа или попроси уточнить.
- Удаление никогда не выполняй сам: вызови propose_delete — пользователь подтвердит кнопкой. id бери из calendar_events/reminders_list; если не знаешь id — передай title (название), найду сам.
- id встреч для изменения бери из calendar_events, не придумывай.
- Участников указывай именами из контактов; если не уверен — contacts_find.
- После действий коротко напиши, что сделал (время в формате «чт 16 окт, 15:00–15:30»). Кнопки «Отменить»/«Удалить» пользователь увидит сам под ответом."""


# ---------------------------------------------------------------- схемы инструментов

def _fn(name: str, desc: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


_DATE = {"type": "string", "description": "Дата YYYY-MM-DD"}
_DT = {"type": "string", "description": "Дата и время начала YYYY-MM-DDTHH:MM (локальное время пользователя)"}
_PEOPLE = {"type": "array", "items": {"type": "string"}, "description": "Имена или @ники из контактов"}

TOOLS: list[dict[str, Any]] = [
    _fn("calendar_events", "Встречи и задачи пользователя за период (включительно).",
        {"start_date": _DATE, "end_date": _DATE, "query": {"type": "string", "description": "Фильтр по названию (необязательно)"}},
        ["start_date", "end_date"]),
    _fn("calendar_free_slots", "Свободные окна в рабочий день; с участниками — общие свободные окна.",
        {"date": _DATE, "duration_min": {"type": "integer", "default": 30}, "with_people": _PEOPLE}, ["date"]),
    _fn("calendar_availability", "Занятость пользователя и людей из контактов в день (без названий чужих встреч).",
        {"date": _DATE, "people": _PEOPLE}, ["date", "people"]),
    _fn("calendar_time_stats", "Статистика времени во встречах за период: часы, по людям, по названиям, по дням недели.",
        {"start_date": _DATE, "end_date": _DATE}, ["start_date", "end_date"]),
    _fn("contacts_find", "Найти контакты пользователя по имени/нику (пусто — все).",
        {"query": {"type": "string"}}),
    _fn("reminders_list", "Предстоящие напоминания пользователя.", {}),
    _fn("calendar_create_event", "Создать встречу в Google Календаре (сразу). Проверяет конфликты.",
        {"title": {"type": "string"}, "start": _DT, "duration_min": {"type": "integer", "default": 30},
         "attendees": _PEOPLE, "description": {"type": "string"}, "location": {"type": "string"},
         "online": {"type": "string", "enum": ["auto", "zoom", "telemost", "none"], "default": "auto"},
         "force": {"type": "boolean", "description": "true — создать, даже если время занято (только если пользователь явно согласился)"}},
        ["title", "start"]),
    _fn("calendar_update_event", "Изменить встречу (перенос, название, участники) — сразу.",
        {"event_id": {"type": "string"}, "calendar_id": {"type": "string"}, "start": _DT,
         "duration_min": {"type": "integer"}, "title": {"type": "string"},
         "attendees": {**_PEOPLE, "description": "Полный новый список участников (заменяет)"},
         "add_attendees": {**_PEOPLE, "description": "Добавить к текущим"},
         "description": {"type": "string"}, "location": {"type": "string"}},
        ["event_id"]),
    _fn("task_create", "Поставить задачу (себе или контакту) на дату/время — сразу.",
        {"title": {"type": "string"}, "start": _DT, "duration_min": {"type": "integer", "default": 30},
         "all_day": {"type": "boolean"}, "assignee": {"type": "string", "description": "Имя/ник из контактов; пусто — себе"},
         "description": {"type": "string"}},
        ["title", "start"]),
    _fn("reminder_create", "Создать напоминание в Telegram — сразу.",
        {"text": {"type": "string"}, "when": _DT}, ["text", "when"]),
    _fn("propose_delete", "Предложить удалить встречу/задачу/напоминание. НЕ удаляет — пользователь подтвердит кнопкой.",
        {"kind": {"type": "string", "enum": ["event", "task", "reminder"]}, "id": {"type": "string"},
         "title": {"type": "string", "description": "Название, если id неизвестен"},
         "calendar_id": {"type": "string"}},
        ["kind"]),
]


# ---------------------------------------------------------------- хелперы

def _parse_day(s: Any) -> date:
    return date.fromisoformat(str(s or "").strip()[:10])


def _window(uid: int, start_date: Any, end_date: Any) -> tuple[datetime, datetime]:
    tz = _tz(uid)
    d0 = _parse_day(start_date)
    d1 = _parse_day(end_date or start_date)
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days > MAX_RANGE_DAYS:
        d1 = d0 + timedelta(days=MAX_RANGE_DAYS)
    return datetime.combine(d0, time.min, tzinfo=tz), datetime.combine(d1 + timedelta(days=1), time.min, tzinfo=tz)


def _fmt_dt(dt: datetime | None, uid: int) -> str:
    if not isinstance(dt, datetime):
        return ""
    return dt.astimezone(_tz(uid)).strftime("%Y-%m-%d %H:%M")


_WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _iso_local(uid: int, raw: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = _tz(uid)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def human_when(start: datetime | None, end: datetime | None, uid: int) -> str:
    if not isinstance(start, datetime):
        return ""
    tz = _tz(uid)
    s = start.astimezone(tz)
    out = f"{_WD[s.weekday()]} {s.day} {_MON[s.month - 1]}, {s.strftime('%H:%M')}"
    if isinstance(end, datetime):
        out += "–" + end.astimezone(tz).strftime("%H:%M")
    return out


def _contacts(ctx: GptCalendarContext) -> list[dict[str, Any]]:
    try:
        from assistant.stores import contacts_store

        return list(contacts_store.load_contacts(ctx.uid, ctx.telegram_username) or [])
    except Exception:
        return []


def _email_names(ctx: GptCalendarContext) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in _contacts(ctx):
        em = str(c.get("email") or "").strip().lower()
        if em:
            out[em] = str(c.get("name") or em)
    return out


def _resolve_people(ctx: GptCalendarContext, names: list[str]) -> tuple[list[str], list[str]]:
    from assistant.lib.calendar_attendees import resolve_attendee_names

    clean = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not clean:
        return [], []
    att, missing = resolve_attendee_names(ctx.uid, clean, telegram_username=ctx.telegram_username)
    return [a["email"] for a in att if a.get("email")], missing


def _parse_start(ctx: GptCalendarContext, raw: Any) -> datetime:
    s = str(raw or "").strip().replace(" ", "T")
    dt = datetime.fromisoformat(s)
    tz = _tz(ctx.uid)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _ok(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= 24000 else text[:24000] + "…(обрезано)"


# ---------------------------------------------------------------- чтение

def _events_in_window(ctx: GptCalendarContext, t0: datetime, t1: datetime, query: str = "") -> list[dict[str, Any]]:
    from assistant.services import calendar_sources

    return calendar_sources.list_events_in_window(ctx.uid, t0, t1, query=query or None)


def tool_calendar_events(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    t0, t1 = _window(ctx.uid, args.get("start_date"), args.get("end_date"))
    names = _email_names(ctx)
    out: list[dict[str, Any]] = []
    if has_google_calendar(ctx.uid):
        for ev in _events_in_window(ctx, t0, t1, str(args.get("query") or "")):
            raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
            atts = []
            for a in raw.get("attendees") or []:
                if not isinstance(a, dict) or a.get("self"):
                    continue
                em = str(a.get("email") or "").strip().lower()
                atts.append(names.get(em) or str(a.get("displayName") or em))
            item = {
                "type": "meeting",
                "id": ev.get("event_id"),
                "calendar_id": ev.get("calendar_id"),
                "title": ev.get("summary"),
                "start": _fmt_dt(ev.get("start"), ctx.uid),
                "end": _fmt_dt(ev.get("end"), ctx.uid),
            }
            if atts:
                item["attendees"] = atts[:12]
            if raw.get("location"):
                item["location"] = str(raw.get("location"))[:120]
            out.append(item)
    try:
        from assistant.stores import calendar_tasks

        for t in calendar_tasks.list_tasks_in_window(ctx.uid, t0, t1):
            out.append(
                {
                    "type": "task",
                    "id": str(t.get("id")),
                    "title": t.get("title"),
                    "start": _fmt_dt(_iso_local(ctx.uid, t.get("start_at")), ctx.uid),
                    "done": bool(t.get("done")),
                    "assignee": t.get("assignee_name") or "",
                }
            )
    except Exception as e:
        print(f"[gpt_calendar] tasks_list err={e!r}")
    out.sort(key=lambda x: str(x.get("start") or ""))
    note = None if has_google_calendar(ctx.uid) else "Google Календарь не подключён — показаны только задачи Leo."
    res: dict[str, Any] = {"items": out[:MAX_EVENTS_OUT], "total": len(out)}
    if note:
        res["note"] = note
    return _ok(res)


def tool_calendar_free_slots(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import calendar as cal

    if not has_google_calendar(ctx.uid):
        return _err("Google Календарь не подключён (Профиль → Календарь).")
    day = _parse_day(args.get("date")).isoformat()
    emails, missing = _resolve_people(ctx, args.get("with_people") or [])
    dur = int(args.get("duration_min") or 30)
    slots = cal.joint_free_slots_day(ctx.uid, day, emails, duration_min=dur)
    res: dict[str, Any] = {"date": day, "duration_min": dur, "slots": slots[:24]}
    if missing:
        res["not_found_in_contacts"] = missing
    if not slots:
        res["note"] = "Свободных окон нет (или выходной)."
    return _ok(res)


def tool_calendar_availability(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import calendar as cal

    if not has_google_calendar(ctx.uid):
        return _err("Google Календарь не подключён (Профиль → Календарь).")
    day = _parse_day(args.get("date")).isoformat()
    emails, missing = _resolve_people(ctx, args.get("people") or [])
    data = cal.availability_for_attendees(ctx.uid, day, emails, telegram_username=ctx.telegram_username)
    if missing:
        data["not_found_in_contacts"] = missing
    return _ok(data)


def tool_calendar_time_stats(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    if not has_google_calendar(ctx.uid):
        return _err("Google Календарь не подключён (Профиль → Календарь).")
    t0, t1 = _window(ctx.uid, args.get("start_date"), args.get("end_date"))
    names = _email_names(ctx)
    total_min = 0
    count = 0
    by_person: Counter = Counter()
    by_title: Counter = Counter()
    by_weekday: Counter = Counter()
    longest: list[tuple[int, str, str]] = []
    for ev in _events_in_window(ctx, t0, t1):
        st, en = ev.get("start"), ev.get("end")
        if not isinstance(st, datetime) or not isinstance(en, datetime):
            continue
        mins = int((en - st).total_seconds() // 60)
        if mins <= 0 or mins >= 24 * 60:  # события на весь день не считаем
            continue
        raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
        count += 1
        total_min += mins
        title = str(ev.get("summary") or "Встреча").strip()
        by_title[title] += mins
        by_weekday[_WD[st.astimezone(_tz(ctx.uid)).weekday()]] += mins
        for a in raw.get("attendees") or []:
            if not isinstance(a, dict) or a.get("self"):
                continue
            em = str(a.get("email") or "").strip().lower()
            by_person[names.get(em) or str(a.get("displayName") or em)] += mins
        longest.append((mins, title, _fmt_dt(st, ctx.uid)))
    workdays = sum(1 for i in range((t1 - t0).days) if (t0 + timedelta(days=i)).weekday() < 5)
    hours = round(total_min / 60, 1)
    res = {
        "period": f"{t0.date()} — {(t1 - timedelta(days=1)).date()}",
        "meetings": count,
        "hours_in_meetings": hours,
        "share_of_workdays_8h": f"{round(100 * total_min / max(1, workdays * 480))}%",
        "top_people_hours": [[k, round(v / 60, 1)] for k, v in by_person.most_common(10)],
        "top_titles_hours": [[k, round(v / 60, 1)] for k, v in by_title.most_common(10)],
        "by_weekday_hours": {k: round(v / 60, 1) for k, v in by_weekday.items()},
        "longest": [[t, d, m] for m, t, d in sorted(longest, reverse=True)[:5]],
    }
    return _ok(res)


def tool_contacts_find(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    q = str(args.get("query") or "").strip().lower().lstrip("@")
    rows = []
    for c in _contacts(ctx):
        hay = " ".join(
            [str(c.get("name") or ""), str(c.get("telegram_username") or ""), " ".join(c.get("aliases") or [])]
        ).lower()
        if q and q not in hay:
            continue
        rows.append(
            {
                "name": c.get("name"),
                "telegram": ("@" + str(c.get("telegram_username"))) if c.get("telegram_username") else "",
                "has_email": bool(str(c.get("email") or "").strip()),
            }
        )
    return _ok({"contacts": rows[:50], "total": len(rows)})


def tool_reminders_list(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.stores import reminders_store

    now = datetime.now(_tz(ctx.uid))
    items = []
    for r in reminders_store.reminders_for_user(ctx.uid):
        when = _iso_local(ctx.uid, r.get("when_iso"))
        if r.get("done") or not when or when < now:
            continue
        items.append({"id": r.get("id"), "text": r.get("task"), "when": _fmt_dt(when, ctx.uid)})
    items.sort(key=lambda x: str(x.get("when") or ""))
    return _ok({"reminders": items[:50]})


# ---------------------------------------------------------------- изменения

def _attach_online(ctx: GptCalendarContext, created: dict[str, Any], online: str) -> str:
    from assistant.lib import telemost_gcal, zoom_gcal

    online = (online or "auto").lower()
    try:
        if online == "zoom" and zoom_gcal.user_has_zoom(ctx.uid):
            zoom_gcal.attach_zoom_to_event(
                ctx.uid, created["event_id"], created["start"], created["end"],
                str(created.get("summary") or ""), calendar_id=created.get("calendar_id"),
            )
            return "Zoom"
        if online == "telemost" and telemost_gcal.user_has_telemost(ctx.uid):
            telemost_gcal.attach_telemost_to_event(
                ctx.uid, created["event_id"], str(created.get("summary") or ""),
                calendar_id=created.get("calendar_id"),
            )
            return "Телемост"
        if online == "auto":
            zoom_gcal.try_auto_attach_after_create(ctx.uid, created)
            telemost_gcal.try_auto_attach_after_create(ctx.uid, created)
    except Exception as e:
        print(f"[gpt_calendar] online_attach err={e!r}")
    return ""


def tool_calendar_create_event(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import calendar as cal

    if not has_google_calendar(ctx.uid):
        return _err("Google Календарь не подключён (Профиль → Календарь) — создать встречу нельзя.")
    title = str(args.get("title") or "").strip() or "Встреча"
    try:
        start = _parse_start(ctx, args.get("start"))
    except (TypeError, ValueError):
        return _err("Неверное время начала, нужно YYYY-MM-DDTHH:MM.")
    dur = max(15, min(8 * 60, int(args.get("duration_min") or 30)))
    people = [str(p).strip() for p in (args.get("attendees") or []) if str(p).strip()]
    parsed: dict[str, Any] = {
        "title": title,
        "start": start.isoformat(),
        "duration_min": dur,
        "attendee_names": people,
    }
    if args.get("description"):
        parsed["description"] = str(args["description"])
    if args.get("location"):
        parsed["location"] = str(args["location"])
    missing = cal.unresolved_attendee_names(ctx.uid, parsed, telegram_username=ctx.telegram_username)
    if missing:
        return _err(
            "Не нашёл почту в контактах для: " + ", ".join(missing)
            + ". Уточни у пользователя или создай без них (уберёт из attendees)."
        )
    if not args.get("force"):
        check = cal.validate_meeting_slot(ctx.uid, parsed, start, telegram_username=ctx.telegram_username)
        if not check.get("ok"):
            alts = [_fmt_dt(a, ctx.uid) for a in (check.get("alt_starts") or [])[:6]]
            return _ok(
                {
                    "created": False,
                    "conflict": True,
                    "owner_busy": bool(check.get("owner_conflict")),
                    "attendees_busy": check.get("attendee_busy") or [],
                    "alternatives": alts,
                    "hint": "Предложи пользователю другое время из alternatives или спроси, ставить ли поверх (force=true).",
                }
            )
    created = cal.create_event(ctx.uid, parsed, telegram_username=ctx.telegram_username)
    online = _attach_online(ctx, created, str(args.get("online") or "auto"))
    when = human_when(created.get("start"), created.get("end"), ctx.uid)
    who = ", ".join(people)
    detail = " · ".join(x for x in [("с " + who) if who else "", online] if x)
    aid = record_action(
        ctx.uid, "event_create", "done",
        title=str(created.get("summary") or title),
        payload={"when": when, "detail": detail},
        result={
            "event_id": created.get("event_id"),
            "calendar_id": created.get("calendar_id"),
            "html_link": created.get("html_link"),
            "when": when,
            "detail": detail,
        },
    )
    ctx.actions.append(aid)
    ctx.created_events.append(created)
    return _ok({"created": True, "event_id": created.get("event_id"), "when": when, "attendees": people, "online": online})


def _event_snapshot(ctx: GptCalendarContext, event_id: str, calendar_id: str | None) -> dict[str, Any]:
    from assistant.services import calendar as cal

    ev = cal.get_event(ctx.uid, event_id, calendar_id=calendar_id)
    return {
        "title": ev.get("summary") or "",
        "start": (ev.get("start") or {}).get("dateTime") or "",
        "end": (ev.get("end") or {}).get("dateTime") or "",
        "description": ev.get("description") or "",
        "location": ev.get("location") or "",
        "attendees": cal.event_attendee_emails(ev),
    }


def tool_calendar_update_event(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.lib import zoom_gcal
    from assistant.services import calendar as cal

    if not has_google_calendar(ctx.uid):
        return _err("Google Календарь не подключён.")
    eid = str(args.get("event_id") or "").strip()
    cal_id = str(args.get("calendar_id") or "").strip() or None
    if not eid:
        return _err("Нужен event_id — найди встречу через calendar_events.")
    try:
        before = _event_snapshot(ctx, eid, cal_id)
    except Exception as e:
        return _err(f"Встреча не найдена: {e}")
    parsed: dict[str, Any] = {}
    if args.get("title"):
        parsed["title"] = str(args["title"]).strip()
    if args.get("start"):
        try:
            st = _parse_start(ctx, args.get("start"))
        except (TypeError, ValueError):
            return _err("Неверное время, нужно YYYY-MM-DDTHH:MM.")
        parsed["start"] = st.isoformat()
        if args.get("duration_min"):
            parsed["duration_min"] = int(args["duration_min"])
        else:
            try:
                b0 = datetime.fromisoformat(str(before["start"]).replace("Z", "+00:00"))
                b1 = datetime.fromisoformat(str(before["end"]).replace("Z", "+00:00"))
                parsed["duration_min"] = max(15, int((b1 - b0).total_seconds() // 60))
            except (TypeError, ValueError):
                parsed["duration_min"] = 60
    for k in ("description", "location"):
        if k in args and args.get(k) is not None:
            parsed[k] = str(args.get(k) or "")
    names = None
    if args.get("attendees") is not None:
        names = [str(p).strip() for p in args.get("attendees") or [] if str(p).strip()]
    if args.get("add_attendees"):
        add = [str(p).strip() for p in args.get("add_attendees") or [] if str(p).strip()]
        add_emails, missing = _resolve_people(ctx, add)
        if missing:
            return _err("Не нашёл почту в контактах для: " + ", ".join(missing))
        parsed["attendees"] = sorted(set(before["attendees"]) | set(add_emails))
    if names is not None:
        emails, missing = _resolve_people(ctx, names)
        if missing:
            return _err("Не нашёл почту в контактах для: " + ", ".join(missing))
        parsed["attendees"] = emails
    if not parsed:
        return _err("Нечего менять.")
    ev = cal.update_event(ctx.uid, eid, parsed, calendar_id=cal_id)
    try:
        zoom_gcal.sync_zoom_after_calendar_update(ctx.uid, eid, parsed, calendar_id=cal_id)
    except Exception as e:
        print(f"[gpt_calendar] zoom_sync err={e!r}")
    when = human_when(ev.get("start"), ev.get("end"), ctx.uid)
    aid = record_action(
        ctx.uid, "event_update", "done",
        title=str(ev.get("summary") or before.get("title") or "Встреча"),
        payload={"when": when, "detail": "перенесено" if parsed.get("start") else "изменено"},
        before={**before, "event_id": eid, "calendar_id": cal_id},
        result={"event_id": eid, "calendar_id": cal_id, "html_link": ev.get("html_link"), "when": when,
                "detail": "перенесено" if parsed.get("start") else "изменено"},
    )
    ctx.actions.append(aid)
    ctx.updated_events.append(ev)
    return _ok({"updated": True, "when": when})


def _resolve_assignee(ctx: GptCalendarContext, name: str) -> tuple[int, str, str] | str:
    """(uid, email, display) или текст ошибки."""
    raw = str(name or "").strip()
    if not raw or raw.lower() in {"я", "мне", "себе", "self", "me"}:
        return ctx.uid, "", ctx.display_name or "Я"
    from assistant.lib.calendar_attendees import contact_display_name, find_contact_by_name
    from assistant.stores import note_members

    contact = find_contact_by_name(ctx.uid, raw, telegram_username=ctx.telegram_username)
    if not contact:
        return f"Контакт «{raw}» не найден."
    try:
        assignee_uid, contact = note_members.resolve_contact_telegram_id(
            owner_user_id=ctx.uid,
            email=str(contact.get("email") or "") or None,
            telegram_user_id=contact.get("telegram_user_id"),
            telegram_username=contact.get("telegram_username"),
            owner_username=ctx.telegram_username,
        )
    except ValueError:
        return f"У контакта «{contact_display_name(contact)}» нет Telegram в Leo — задачу ему не поставить."
    return int(assignee_uid), str(contact.get("email") or ""), contact_display_name(contact)


def tool_task_create(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.stores import calendar_tasks

    title = str(args.get("title") or "").strip()
    if not title:
        return _err("Нужно название задачи.")
    try:
        start = _parse_start(ctx, args.get("start"))
    except (TypeError, ValueError):
        return _err("Неверное время, нужно YYYY-MM-DDTHH:MM.")
    who = _resolve_assignee(ctx, str(args.get("assignee") or ""))
    if isinstance(who, str):
        return _err(who)
    a_uid, a_email, a_name = who
    dur = max(15, int(args.get("duration_min") or 30))
    row = calendar_tasks.create_task(
        ctx.uid,
        title=title,
        description=str(args.get("description") or ""),
        start_at=start.isoformat(),
        end_at=(start + timedelta(minutes=dur)).isoformat(),
        assignee_user_id=a_uid,
        assignee_email=a_email,
        assignee_name=a_name,
        all_day=bool(args.get("all_day")),
        tz=_tz(ctx.uid),
    )
    if ctx.sync_task_google:
        try:
            row = ctx.sync_task_google(ctx.uid, row, "create") or row
        except Exception as e:
            print(f"[gpt_calendar] task_sync err={e!r}")
    delegated = int(a_uid) != int(ctx.uid)
    if delegated and ctx.notify_task_assignee:
        try:
            ctx.notify_task_assignee(assignee_user_id=a_uid, task=row, assigner_name=ctx.display_name or "Участник")
        except Exception as e:
            print(f"[gpt_calendar] task_notify err={e!r}")
    when = human_when(start, None if args.get("all_day") else start + timedelta(minutes=dur), ctx.uid)
    detail = ("для " + a_name) if delegated else "себе"
    aid = record_action(
        ctx.uid, "task_create", "done", title=title,
        payload={"when": when, "detail": detail},
        result={"task_id": row.get("id"), "when": when, "detail": detail},
    )
    ctx.actions.append(aid)
    return _ok({"created": True, "task_id": row.get("id"), "when": when, "assignee": a_name})


def tool_reminder_create(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.stores import reminders_store

    text = str(args.get("text") or "").strip()
    if not text:
        return _err("Нужен текст напоминания.")
    try:
        when = _parse_start(ctx, args.get("when"))
    except (TypeError, ValueError):
        return _err("Неверное время, нужно YYYY-MM-DDTHH:MM.")
    rid = reminders_store.add_reminder(
        user_id=ctx.uid, chat_id=ctx.uid, task=text, when_iso=when.isoformat(), checklist=None
    )
    hw = human_when(when, None, ctx.uid)
    aid = record_action(
        ctx.uid, "reminder_create", "done", title=text,
        payload={"when": hw, "detail": "напоминание в Telegram"},
        result={"reminder_id": rid, "when": hw, "detail": "напоминание в Telegram"},
    )
    ctx.actions.append(aid)
    return _ok({"created": True, "when": hw})


def _find_target_by_title(ctx: GptCalendarContext, kind: str, title: str) -> tuple[str, str | None] | None:
    """Найти ближайшую встречу/задачу/напоминание по названию (на 60 дней вперёд и 7 назад)."""
    q = str(title or "").strip().lower()
    if not q:
        return None
    tz = _tz(ctx.uid)
    t0 = datetime.now(tz) - timedelta(days=7)
    t1 = datetime.now(tz) + timedelta(days=60)
    if kind == "task":
        from assistant.stores import calendar_tasks

        for t in calendar_tasks.list_tasks_in_window(ctx.uid, t0, t1):
            if q in str(t.get("title") or "").lower():
                return str(t.get("id")), None
    elif kind == "event" and has_google_calendar(ctx.uid):
        for ev in _events_in_window(ctx, t0, t1):
            if q in str(ev.get("summary") or "").lower():
                return str(ev.get("event_id")), str(ev.get("calendar_id") or "") or None
    elif kind == "reminder":
        from assistant.stores import reminders_store

        for rm in reminders_store.reminders_for_user(ctx.uid):
            if not rm.get("done") and q in str(rm.get("task") or "").lower():
                return str(rm.get("id")), None
    return None


def tool_propose_delete(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    kind = str(args.get("kind") or "").strip()
    oid = str(args.get("id") or "").strip()
    if kind not in ("event", "task", "reminder"):
        return _err("Нужен kind: event|task|reminder.")
    if kind == "task" and oid and not oid.isdigit():
        oid = ""
    if not oid or (kind == "task" and not _task_exists(ctx, oid)):
        found = _find_target_by_title(ctx, kind, str(args.get("title") or args.get("id") or ""))
        if not found:
            return _err("Не нашёл, что удалять — уточни название или посмотри calendar_events.")
        oid, cal_found = found
        if cal_found and not args.get("calendar_id"):
            args = {**args, "calendar_id": cal_found}
    title, when = "", ""
    payload: dict[str, Any] = {"target_id": oid}
    try:
        if kind == "event":
            from assistant.services import calendar as cal

            cal_id = str(args.get("calendar_id") or "").strip() or None
            ev = cal.get_event(ctx.uid, oid, calendar_id=cal_id)
            title = str(ev.get("summary") or "Встреча")
            st = datetime.fromisoformat(str((ev.get("start") or {}).get("dateTime") or "").replace("Z", "+00:00"))
            en = datetime.fromisoformat(str((ev.get("end") or {}).get("dateTime") or "").replace("Z", "+00:00"))
            when = human_when(st, en, ctx.uid)
            payload["calendar_id"] = cal_id
        elif kind == "task":
            from assistant.stores import calendar_tasks

            t = calendar_tasks.get_task_for_user(ctx.uid, oid)
            if not t:
                return _err("Задача не найдена.")
            title = str(t.get("title") or "Задача")
            when = human_when(_iso_local(ctx.uid, t.get("start_at")), None, ctx.uid)
        else:
            from assistant.stores import reminders_store

            r = reminders_store.get_reminder_by_id(oid)
            if not r or int(r.get("user_id") or 0) != ctx.uid:
                return _err("Напоминание не найдено.")
            title = str(r.get("task") or "Напоминание")
            when = human_when(_iso_local(ctx.uid, r.get("when_iso")), None, ctx.uid)
    except ValueError:
        pass
    except Exception as e:
        return _err(f"Не нашёл, что удалять: {e}")
    payload.update({"when": when, "detail": "удалить?"})
    aid = record_action(ctx.uid, f"{kind}_delete", "proposed", title=title or "Без названия", payload=payload)
    ctx.actions.append(aid)
    return _ok({"proposed": True, "note": "Пользователь подтвердит удаление кнопкой под ответом."})


def _task_exists(ctx: GptCalendarContext, oid: str) -> bool:
    try:
        from assistant.stores import calendar_tasks

        return bool(calendar_tasks.get_task_for_user(ctx.uid, oid))
    except Exception:
        return False


EXECUTORS: dict[str, Callable[[GptCalendarContext, dict[str, Any]], str]] = {
    "calendar_events": tool_calendar_events,
    "calendar_free_slots": tool_calendar_free_slots,
    "calendar_availability": tool_calendar_availability,
    "calendar_time_stats": tool_calendar_time_stats,
    "contacts_find": tool_contacts_find,
    "reminders_list": tool_reminders_list,
    "calendar_create_event": tool_calendar_create_event,
    "calendar_update_event": tool_calendar_update_event,
    "task_create": tool_task_create,
    "reminder_create": tool_reminder_create,
    "propose_delete": tool_propose_delete,
}


def make_runner(ctx: GptCalendarContext) -> Callable[[str, dict[str, Any]], str]:
    def run(name: str, args: dict[str, Any]) -> str:
        fn = EXECUTORS.get(name)
        if not fn:
            return _err(f"Неизвестный инструмент {name}")
        try:
            out = fn(ctx, args if isinstance(args, dict) else {})
        except Exception as e:  # noqa: BLE001 — ошибка календаря уходит модели текстом
            print(f"[gpt_calendar] tool={name} failed uid={ctx.uid} err={e!r}")
            out = _err(f"Не получилось: {e}")
        failed = out.startswith('{"error')
        print(f"[gpt_calendar] tool={name} uid={ctx.uid} ok={not failed}")
        return out

    return run


# ---------------------------------------------------------------- отмена / подтверждение

def undo_action(a: dict[str, Any]) -> dict[str, Any]:
    from assistant.services import calendar as cal

    uid = int(a["owner_uid"])
    res = a.get("result") or {}
    kind = a["kind"]
    if kind == "event_create":
        cal.delete_event(uid, str(res.get("event_id")), calendar_id=res.get("calendar_id"))
    elif kind == "event_update":
        b = a.get("before") or {}
        parsed: dict[str, Any] = {"title": b.get("title") or "", "attendees": list(b.get("attendees") or [])}
        if b.get("start"):
            parsed["start"] = b["start"]
            parsed["end"] = b.get("end") or ""
        parsed["description"] = b.get("description") or ""
        parsed["location"] = b.get("location") or ""
        cal.update_event(uid, str(b.get("event_id")), parsed, calendar_id=b.get("calendar_id"))
    elif kind == "task_create":
        from assistant.stores import calendar_tasks

        task = calendar_tasks.get_task_for_user(uid, res.get("task_id"))
        if task:
            try:
                cal.delete_task_event(uid, task)
            except Exception as e:
                print(f"[gpt_calendar] undo task gcal err={e!r}")
            calendar_tasks.delete_task(uid, res.get("task_id"))
    elif kind == "reminder_create":
        from assistant.stores import reminders_store

        reminders_store.remove_reminder(str(res.get("reminder_id")))
    else:
        raise ValueError("Это действие нельзя отменить")
    return {**res, "detail": "отменено"}


def apply_delete(a: dict[str, Any]) -> dict[str, Any]:
    uid = int(a["owner_uid"])
    p = a.get("payload") or {}
    oid = str(p.get("target_id") or "")
    kind = a["kind"]
    if kind == "event_delete":
        from assistant.services import calendar as cal

        cal.delete_event(uid, oid, calendar_id=p.get("calendar_id"))
    elif kind == "task_delete":
        from assistant.services import calendar as cal
        from assistant.stores import calendar_tasks

        task = calendar_tasks.get_task_for_user(uid, oid)
        if task:
            try:
                cal.delete_task_event(uid, task)
            except Exception as e:
                print(f"[gpt_calendar] delete task gcal err={e!r}")
        calendar_tasks.delete_task(uid, oid)
    elif kind == "reminder_delete":
        from assistant.stores import reminders_store

        reminders_store.remove_reminder(oid)
    else:
        raise ValueError("Неизвестное действие")
    return {"when": p.get("when") or "", "detail": "удалено"}


def actions_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for i in ids:
        a = get_action(i)
        if a:
            out.append(a)
    return out

