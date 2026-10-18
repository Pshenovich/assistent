"""«Фокус»: проактивные подсказки с готовыми действиями (этап 1 — правила, без ИИ).

Подсказки считаются на лету из того, что уже есть у пользователя:
- просроченные задачи (свои и поставленные другим);
- встреча в ближайшие 90 минут — подготовиться (повестка);
- встреча прошла за последние 36 часов, а итогов нет (ни записи, ни заметки);
- в саммари встречи есть задачи, а в Leo их нет.

У каждой подсказки — источник, кнопки действий и ключ: скрытая (✕) больше не показывается.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

MAX_ITEMS = 7
PREP_WINDOW = timedelta(minutes=90)
NO_RESULTS_WINDOW = timedelta(hours=36)
SUMMARY_TASKS_WINDOW = timedelta(days=14)

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None
_RU_MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
_RU_WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


# ---------------------------------------------------------------- скрытые подсказки

def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    from assistant.stores import notes as notes_store

    path = str(notes_store._db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    notes_store._conn()
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_dismissed (
            owner_user_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            until TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, item_key)
        )
        """
    )
    _CONN.commit()
    return _CONN


def _now() -> datetime:
    return datetime.now(timezone.utc)


def dismiss(owner_user_id: Any, key: str, *, until: Optional[datetime] = None) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO proactive_dismissed (owner_user_id, item_key, until, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_user_id, item_key) DO UPDATE SET until = excluded.until
            """,
            (str(owner_user_id), str(key)[:300], until.isoformat() if until else "", _now().isoformat()),
        )
        conn.commit()


def dismissed_keys(owner_user_id: Any) -> set[str]:
    now = _now().isoformat()
    with _LOCK:
        rows = _conn().execute(
            "SELECT item_key, until FROM proactive_dismissed WHERE owner_user_id = ?",
            (str(owner_user_id),),
        ).fetchall()
    return {str(r["item_key"]) for r in rows if not r["until"] or str(r["until"]) > now}


# ---------------------------------------------------------------- время

def _parse(raw: Any) -> Optional[datetime]:
    if isinstance(raw, dict):
        raw = raw.get("dateTime") or raw.get("date")
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _is_all_day(ev: dict[str, Any]) -> bool:
    st = ev.get("start")
    return isinstance(st, dict) and not st.get("dateTime") and bool(st.get("date"))


def _hm(d: datetime, tz: Any) -> str:
    return d.astimezone(tz).strftime("%H:%M")


def _day(d: datetime, tz: Any, now: datetime) -> str:
    loc = d.astimezone(tz)
    today = now.astimezone(tz).date()
    if loc.date() == today:
        return "сегодня"
    if loc.date() == today - timedelta(days=1):
        return "вчера"
    if loc.date() == today + timedelta(days=1):
        return "завтра"
    return f"{_RU_WD[loc.weekday()]}, {loc.day} {_RU_MON[loc.month - 1]}"


def _key(*parts: Any) -> str:
    raw = ":".join(str(p) for p in parts)
    if len(raw) <= 120:
        return raw
    return raw[:60] + ":" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _others(ev: dict[str, Any]) -> list[str]:
    out = []
    for a in ev.get("attendees") or []:
        if not isinstance(a, dict) or a.get("self") or a.get("is_self"):
            continue
        name = str(a.get("name") or a.get("email") or "").strip()
        if name:
            out.append(name)
    return out


def _declined(ev: dict[str, Any]) -> bool:
    return str(ev.get("self_response_status") or "") == "declined"


def _event_ref(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ev.get("id"),
        "calendar_id": ev.get("calendar_id") or "primary",
        "summary": ev.get("summary") or "",
        "start": ev.get("start"),
        "end": ev.get("end"),
        "meet_url": ev.get("meet_url") or "",
        "attendees": ev.get("attendees") or [],
    }


# ---------------------------------------------------------------- правила

def rule_overdue(tasks: list[dict[str, Any]], now: datetime, tz: Any, uid: str) -> list[dict[str, Any]]:
    late = []
    for t in tasks:
        if t.get("done"):
            continue
        end = _parse(t.get("end_at")) or _parse(t.get("start_at"))
        if end and end < now - timedelta(minutes=30):
            late.append(t)
    if not late:
        return []
    late.sort(key=lambda t: str(t.get("start_at") or ""))
    ids = [int(t["id"]) for t in late]

    def who(t: dict[str, Any]) -> str:
        a = str(t.get("assignee_user_id") or "")
        if a and a != uid:
            return str(t.get("assignee_name") or "исполнитель")
        return "вы"

    if len(late) == 1:
        t = late[0]
        start = _parse(t.get("start_at"))
        title = f"Просрочено: «{t.get('title') or 'Задача'}»"
        src = f"{who(t)} · срок {_day(start, tz, now)}" if start else who(t)
        actions = [
            {"label": "Перенести на завтра", "act": "postpone", "primary": True, "payload": {"task_ids": ids}},
            {"label": "Открыть", "act": "open_task", "payload": {"task_id": ids[0]}},
        ]
    else:
        names: list[str] = []
        for t in late:
            n = who(t)
            if n not in names:
                names.append(n)
        title = f"{len(late)} задач{'и' if 2 <= len(late) % 10 <= 4 and not 12 <= len(late) % 100 <= 14 else ''} просрочены"
        if len(late) % 10 == 1 and len(late) % 100 != 11:
            title = f"{len(late)} задача просрочена"
        src = ", ".join(names[:4])
        actions = [
            {"label": "Перенести на завтра", "act": "postpone", "primary": True, "payload": {"task_ids": ids}},
            {"label": "Список", "act": "open_tasks", "payload": {"task_ids": ids}},
        ]
    return [
        {
            "key": _key("overdue", ",".join(str(i) for i in ids)),
            "type": "overdue",
            "tag": "⚠ просрочено",
            "urgency": 3,
            "title": title,
            "source": src,
            "ask": title + ". " + "; ".join(f"«{t.get('title')}» — {who(t)}" for t in late[:8]),
            "actions": actions,
        }
    ]


def rule_prep(events: list[dict[str, Any]], kinds: dict[str, dict[str, int]], now: datetime, tz: Any) -> list[dict[str, Any]]:
    out = []
    for ev in events:
        if _is_all_day(ev) or _declined(ev) or ev.get("entry_type") == "task":
            continue
        st = _parse(ev.get("start"))
        if not st or not (now < st <= now + PREP_WINDOW):
            continue
        others = _others(ev)
        if not others:
            continue
        mins = int((st - now).total_seconds() // 60)
        when = f"Через {mins} мин" if mins < 60 else f"В {_hm(st, tz)}"
        key = f"{ev.get('calendar_id') or 'primary'}:{ev.get('id')}"
        mats = kinds.get(key) or {}
        has_note = bool(mats.get("note"))
        out.append(
            {
                "key": _key("prep", key),
                "type": "prep",
                "tag": "📝 скоро встреча",
                "urgency": 3,
                "title": f"{when} — {ev.get('summary') or 'встреча'}",
                "source": ", ".join(others[:3]) + (" · есть заметка встречи" if has_note else " · повестки нет"),
                "ask": f"Подготовка к встрече «{ev.get('summary')}» {_hm(st, tz)} с {', '.join(others[:5])}",
                "event": _event_ref(ev),
                "actions": (
                    [{"label": "Материалы", "act": "open_event", "primary": True}]
                    if has_note
                    else [
                        {"label": "Повестка", "act": "event_note", "primary": True},
                        {"label": "Открыть встречу", "act": "open_event"},
                    ]
                ),
            }
        )
    return out


def rule_no_results(events: list[dict[str, Any]], kinds: dict[str, dict[str, int]], now: datetime, tz: Any) -> list[dict[str, Any]]:
    out = []
    for ev in events:
        if _is_all_day(ev) or _declined(ev) or ev.get("entry_type") == "task":
            continue
        st, en = _parse(ev.get("start")), _parse(ev.get("end"))
        if not st or not en or not (now - NO_RESULTS_WINDOW <= en <= now):
            continue
        if (en - st) < timedelta(minutes=15):
            continue
        others = _others(ev)
        if not others:
            continue
        key = f"{ev.get('calendar_id') or 'primary'}:{ev.get('id')}"
        if kinds.get(key):
            continue
        out.append(
            {
                "key": _key("nores", key),
                "type": "no_results",
                "tag": "🎙 без итогов",
                "urgency": 2,
                "title": f"«{ev.get('summary') or 'Встреча'}» прошла без итогов",
                "source": f"{_day(st, tz, now)} {_hm(st, tz)}–{_hm(en, tz)} · {', '.join(others[:3])}",
                "ask": f"Встреча «{ev.get('summary')}» {_day(st, tz, now)} {_hm(st, tz)} с {', '.join(others[:5])} — итогов нет",
                "event": _event_ref(ev),
                "_sort": en.isoformat(),
                "actions": [
                    {"label": "Надиктовать итоги", "act": "event_dictate", "primary": True},
                    {"label": "Запись", "act": "open_event"},
                ],
            }
        )
    out.sort(key=lambda x: x.pop("_sort"), reverse=True)
    return out[:3]


def rule_summary_tasks(notes: list[dict[str, Any]], has_tasks: dict[int, bool], now: datetime, tz: Any) -> list[dict[str, Any]]:
    out = []
    for n in notes:
        meta = n.get("meta") if isinstance(n.get("meta"), dict) else {}
        tasks = [t for t in meta.get("tasks") or [] if isinstance(t, dict) and str(t.get("task") or "").strip()]
        if not tasks:
            continue
        created = _parse(n.get("created_at"))
        if not created or created < now - SUMMARY_TASKS_WINDOW:
            continue
        nid = int(n["id"])
        if has_tasks.get(nid):
            continue
        lines = []
        for t in tasks[:12]:
            parts = [str(t.get("assignee") or "").strip(), str(t.get("task") or "").strip(), str(t.get("deadline") or "").strip()]
            lines.append(" — ".join(p for p in parts if p))
        title = str(n.get("title") or "Встреча")
        cnt = len(tasks)
        word = "задача" if cnt % 10 == 1 and cnt % 100 != 11 else ("задачи" if 2 <= cnt % 10 <= 4 and not 12 <= cnt % 100 <= 14 else "задач")
        out.append(
            {
                "key": _key("sumtasks", nid),
                "type": "summary_tasks",
                "tag": "✅ после встречи",
                "urgency": 2,
                "title": f"«{title}»: {cnt} {word} из встречи — в Orbita их нет",
                "source": f"транскрипция · {_day(created, tz, now)}",
                "ask": f"Задачи из встречи «{title}»: " + "; ".join(lines),
                "note": {"id": nid, "kind": "transcription", "title": title},
                "actions": [
                    {
                        "label": "Поставить всё",
                        "act": "assistant",
                        "primary": True,
                        "payload": {
                            "prompt": (
                                f"Поставь задачи из встречи «{title}». "
                                "Исполнителей возьми из контактов, сроки — из текста; если исполнитель или срок неясен — "
                                "поставь мне и скажи, что уточнить.\n" + "\n".join("— " + x for x in lines)
                            )
                        },
                    },
                    {"label": "Открыть", "act": "open_note"},
                ],
            }
        )
    return out


def rank(items: list[dict[str, Any]], dismissed: set[str]) -> list[dict[str, Any]]:
    out = [i for i in items if i["key"] not in dismissed]
    out.sort(key=lambda i: -int(i.get("urgency") or 0))
    return out[:MAX_ITEMS]


# ---------------------------------------------------------------- сборка

def build_feed(uid: int, events: list[dict[str, Any]], *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks
    from assistant.stores import meeting_links
    from assistant.stores import notes as notes_store

    from assistant.stores import user_prefs

    now = now or _now()
    tz = _tz_for(uid)
    prefs = user_prefs.proactive_prefs(uid)
    items: list[dict[str, Any]] = []
    try:
        tasks = calendar_tasks.list_tasks_in_window(uid, now - timedelta(days=30), now)
        items += rule_overdue(tasks, now, tz, str(uid))
    except Exception as e:
        print(f"[proactive] overdue failed uid={uid} err={e!r}")
    evs = [e for e in events if isinstance(e, dict) and e.get("id") and not str(e.get("id")).startswith("task-")]
    try:
        keys = [meeting_links.event_key(e.get("calendar_id"), e.get("id")) for e in evs]
        kinds = meeting_links.kinds_for_events(uid, keys)
    except Exception as e:
        print(f"[proactive] kinds failed uid={uid} err={e!r}")
        kinds = {}
    if prefs.get("before_meetings"):
        items += rule_prep(evs, kinds, now, tz)
    if prefs.get("after_meetings"):
        items += rule_no_results(evs, kinds, now, tz)
    try:
        notes = [
            n for n in notes_store.list_transcription_notes(uid, limit=40)
            if str(n.get("owner_user_id") or n.get("user_id") or uid) == str(uid)
        ]
        has_tasks = {int(n["id"]): bool(calendar_tasks.list_tasks_for_note(uid, n["id"])) for n in notes}
        if prefs.get("after_meetings"):
            items += rule_summary_tasks(notes, has_tasks, now, tz)
    except Exception as e:
        print(f"[proactive] summary tasks failed uid={uid} err={e!r}")
    try:
        from assistant.services import proactive_ai

        window = calendar_tasks.list_tasks_in_window(uid, now - timedelta(days=30), now + timedelta(days=30))
        cards = proactive_ai.insight_cards(uid, now=now, task_titles=[str(t.get("title") or "") for t in window])
        if not prefs.get("after_meetings"):
            cards = [c for c in cards if (c.get("note") or {}).get("kind") != "transcription"]
        items += cards
        if prefs.get("chats"):
            items += proactive_ai.chat_cards(uid, task_titles=[str(t.get("title") or "") for t in window])
        if prefs.get("weekly"):
            from assistant.services import proactive_reports

            wc = proactive_reports.weekly_card(uid, now=now)
            if wc:
                items.append(wc)
    except Exception as e:
        print(f"[proactive] insights failed uid={uid} err={e!r}")
    return rank(items, dismissed_keys(uid))


# ---------------------------------------------------------------- для бота: события и утренний бриф

def events_for_user(uid: int, t0: datetime, t1: datetime) -> list[dict[str, Any]]:
    """События календаря в форме, как их видит мини-приложение (без себя в участниках)."""
    from assistant.lib.meeting_links import extract_online_meeting_url
    from assistant.services import calendar_sources

    out = []
    for norm in calendar_sources.list_events_in_window(int(uid), t0, t1):
        raw = norm.get("raw") or {}
        if not isinstance(raw, dict):
            continue
        status = ""
        atts = []
        for a in raw.get("attendees") or []:
            if not isinstance(a, dict) or a.get("resource"):
                continue
            if a.get("self"):
                status = str(a.get("responseStatus") or "")
                continue
            atts.append({"email": str(a.get("email") or ""), "name": str(a.get("displayName") or a.get("email") or "")})
        out.append(
            {
                "id": str(norm.get("event_id") or raw.get("id") or ""),
                "calendar_id": str(norm.get("calendar_id") or "primary"),
                "summary": str(raw.get("summary") or ""),
                "start": raw.get("start") or {},
                "end": raw.get("end") or {},
                "attendees": atts,
                "meet_url": extract_online_meeting_url(raw) or "",
                "self_response_status": status,
            }
        )
    return out


def _brief_conn() -> sqlite3.Connection:
    conn = _conn()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS proactive_brief_sent (owner_user_id TEXT NOT NULL, day TEXT NOT NULL, sent_at TEXT NOT NULL, PRIMARY KEY (owner_user_id, day))"
    )
    return conn


def brief_already_sent(uid: int, day: str) -> bool:
    with _LOCK:
        return _brief_conn().execute(
            "SELECT 1 FROM proactive_brief_sent WHERE owner_user_id = ? AND day = ?", (str(uid), day)
        ).fetchone() is not None


def mark_brief_sent(uid: int, day: str) -> None:
    with _LOCK:
        conn = _brief_conn()
        conn.execute(
            "INSERT OR IGNORE INTO proactive_brief_sent (owner_user_id, day, sent_at) VALUES (?, ?, ?)",
            (str(uid), day, _now().isoformat()),
        )
        conn.commit()


def brief_due(local_now: datetime, brief_time: str) -> bool:
    """Пора слать бриф: наступило выбранное время, но прошло не больше 3 часов (не шлём вечером)."""
    try:
        hh, mm = (int(x) for x in str(brief_time or "08:30").split(":", 1))
    except ValueError:
        hh, mm = 8, 30
    target = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target <= local_now < target + timedelta(hours=3)


def brief_message(items: list[dict[str, Any]], today_events: list[dict[str, Any]], tz: Any, local_now: datetime) -> str:
    import html as _h

    wd = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][local_now.weekday()]
    lines = [f"<b>Доброе утро! {wd}, {local_now.day} {_RU_MON[local_now.month - 1]}</b>"]
    meets = [e for e in today_events if not _is_all_day(e) and not _declined(e)]
    meets.sort(key=lambda e: str((e.get("start") or {}).get("dateTime") or ""))
    if meets:
        ws = "встреча" if len(meets) % 10 == 1 and len(meets) % 100 != 11 else ("встречи" if 2 <= len(meets) % 10 <= 4 and not 12 <= len(meets) % 100 <= 14 else "встреч")
        lines.append(f"Сегодня {len(meets)} {ws}:")
        for e in meets[:5]:
            st = _parse(e.get("start"))
            lines.append(f"• {_hm(st, tz) if st else ''} {_h.escape(str(e.get('summary') or 'встреча'))}")
    if items:
        lines.append("")
        lines.append("<b>Главное:</b>")
        for it in items[:3]:
            lines.append(f"• {_h.escape(str(it.get('title') or ''))}")
    return "\n".join(lines)
