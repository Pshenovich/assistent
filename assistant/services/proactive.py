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
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_done (
            owner_user_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
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


# ---------------------------------------------------------------- «Уже есть»: заведено в другом сервисе

DONE_WINDOW = timedelta(days=60)
MAX_DONE_ITEMS = 50


def mark_done(owner_user_id: Any, card_key: str, items: list[dict[str, Any]], *, whole: bool = False) -> int:
    """Отметить задачи карточки как уже заведённые. whole — скрыть и саму карточку."""
    now = _now().isoformat()
    rows = []
    for it in items[:MAX_DONE_ITEMS]:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or "").strip()[:300]
        if iid:
            rows.append((str(owner_user_id), iid, str(it.get("text") or "").strip()[:300], now))
    with _LOCK:
        conn = _conn()
        conn.executemany(
            """
            INSERT INTO proactive_done (owner_user_id, item_key, title, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_user_id, item_key) DO UPDATE SET title = excluded.title, created_at = excluded.created_at
            """,
            rows,
        )
        conn.commit()
    if whole:
        dismiss(owner_user_id, card_key)
    return len(rows)


def _done_rows(owner_user_id: Any) -> list[sqlite3.Row]:
    since = (_now() - DONE_WINDOW).isoformat()
    with _LOCK:
        return _conn().execute(
            "SELECT item_key, title FROM proactive_done WHERE owner_user_id = ? AND created_at >= ?",
            (str(owner_user_id), since),
        ).fetchall()


def done_keys(owner_user_id: Any) -> set[str]:
    from assistant.services import focus_items

    return {str(r["item_key"]) for r in _done_rows(owner_user_id)} | focus_items.hidden_keys(owner_user_id)


def done_titles(owner_user_id: Any) -> list[str]:
    from assistant.services import focus_items

    legacy = [str(r["title"]) for r in _done_rows(owner_user_id) if str(r["title"] or "").strip()]
    return legacy + focus_items.handled_titles(owner_user_id)


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
    def due(t: dict[str, Any]) -> str:
        start = _parse(t.get("start_at"))
        return f"срок {_day(start, tz, now)}" if start else ""

    # Для «Списка» (отметить выполненные) и для ассистента: id и сроки, чтобы он показал карточки всех задач,
    # а не искал их наугад по названиям.
    task_rows = [
        {"id": int(t["id"]), "text": str(t.get("title") or "Задача"), "meta": " · ".join(x for x in (who(t), due(t)) if x)}
        for t in late
    ]
    return [
        {
            "key": _key("overdue", ",".join(str(i) for i in ids)),
            "type": "overdue",
            "tag": "⚠ просрочено",
            "urgency": 3,
            "title": title,
            "source": src,
            "ask": title + ". " + "; ".join(f"«{r['text']}» — {r['meta']} (task id {r['id']})" for r in task_rows[:20]),
            "tasks": task_rows,
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


def summary_task_id(note_id: int, line: str) -> str:
    return _key("sumtask", note_id, hashlib.sha1(line.encode("utf-8")).hexdigest()[:10])


def rule_summary_tasks(
    notes: list[dict[str, Any]],
    has_tasks: dict[int, Any],
    now: datetime,
    tz: Any,
    done: Optional[set[str]] = None,
    foreign: Optional[set[int]] = None,
) -> list[dict[str, Any]]:
    """has_tasks[note_id]: задачи, уже поставленные из этой встречи (названия или
    {title, by, task_id} — от любого участника); True — встреча разобрана целиком (старый вызов).

    Карточка у всех участников встречи одна: открытые задачи — в items, уже поставленные или
    выполненные кем-то — в closed_items (со статусом и автором), в счёт карточки не идут.
    foreign — встречи, которыми поделились: «мои» в них — про владельца, не про пользователя."""
    from assistant.services.meeting_share import task_state

    done = done or set()
    foreign = foreign or set()
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
        posted = has_tasks.get(nid)
        if posted is True:
            continue
        posted_list = [
            x if isinstance(x, dict) else {"title": str(x), "by": "", "task_id": None}
            for x in (posted if isinstance(posted, (list, tuple, set)) else [])
        ]
        items, closed = [], []
        for t in tasks:
            parts = [str(t.get("assignee") or "").strip(), str(t.get("task") or "").strip(), str(t.get("deadline") or "").strip()]
            line = " — ".join(p for p in parts if p)
            iid = summary_task_id(nid, line)
            st = task_state(t, posted_list)
            if st:  # уже поставил или выполнил кто-то из участников встречи
                closed.append({"id": iid, "text": line, **st})
                continue
            if iid in done:
                continue
            item = {"id": iid, "text": line}
            if nid not in foreign and t.get("owner_flag") == "mine":
                item["mine"] = True
            if t.get("quote") and float(t.get("quote_score") or 0) >= 0.6:
                item["quote"] = str(t["quote"])[:300]
            items.append(item)
        if not items:
            continue
        items.sort(key=lambda i: not i.get("mine"))  # свои — первыми
        lines = [i["text"] for i in items[:12]]
        title = str(n.get("title") or "Встреча")
        cnt = len(items)
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
                "items": items,
                "closed_items": closed,
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
                    {"label": "✓ Сделано" + ("…" if cnt > 1 else ""), "act": "mark_done_pick" if cnt > 1 else "mark_done"},
                ],
            }
        )
    return out


def _similar(a: set[str], b: set[str]) -> bool:
    common = len(a & b)
    return common >= 2 and common / max(1, min(len(a), len(b))) >= 0.7


def dedupe(items: list[dict[str, Any]], dismissed: set[str]) -> list[dict[str, Any]]:
    """Один и тот же пункт из разных дней чата / разных материалов — одна карточка.

    Остаётся самая срочная и свежая; если любую из копий скрыли — скрыты все.
    """
    from assistant.services.proactive_ai import _words

    groups: list[tuple[set[str], list[dict[str, Any]]]] = []
    for c in items:
        if not c.get("items") or c.get("type") == "summary_tasks":
            groups.append((set(), [c]))
            continue
        w = _words(str((c.get("_item") or {}).get("text") or c.get("title") or ""))
        for gw, g in groups:
            if gw and _similar(w, gw):
                g.append(c)
                break
        else:
            groups.append((w, [c]))
    out = []
    for _, g in groups:
        if len(g) > 1 and any(x["key"] in dismissed for x in g):
            continue
        out.append(max(g, key=lambda x: (int(x.get("urgency") or 0), str((x.get("chat") or {}).get("date") or ""))))
    return out


def rank(items: list[dict[str, Any]], dismissed: set[str]) -> list[dict[str, Any]]:
    out = [i for i in dedupe(items, dismissed) if i["key"] not in dismissed]
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
        from assistant.services.meeting_share import posted_for_note

        notes = notes_store.list_transcription_notes(uid, limit=40)  # свои и те, которыми поделились
        foreign = {int(n["id"]) for n in notes if str(n.get("owner_user_id") or n.get("user_id") or uid) != str(uid)}
        has_tasks = {int(n["id"]): posted_for_note(n) for n in notes}
        if prefs.get("after_meetings"):
            items += rule_summary_tasks(notes, has_tasks, now, tz, done_keys(uid), foreign)
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
            # вопросы к пользователю — не карточками в течение дня, а одной сводкой вечером (awaiting_reply)
            items += [c for c in proactive_ai.chat_cards(uid, task_titles=[str(t.get("title") or "") for t in window])
                      if c.get("type") != "chat_request"]
            from assistant.services import awaiting_reply

            card = awaiting_reply.awaiting_card(uid, now=now)
            if card:
                items.append(card)
        if prefs.get("weekly"):
            from assistant.services import proactive_reports

            wc = proactive_reports.weekly_card(uid, now=now)
            if wc:
                items.append(wc)
    except Exception as e:
        print(f"[proactive] insights failed uid={uid} err={e!r}")
    try:
        from assistant.services import focus_items

        focus_items.ingest(uid, items, now)  # реестр обязательств (B1)
        items = focus_items.apply_to_feed(uid, items, now)
    except Exception as e:
        print(f"[proactive] focus items failed uid={uid} err={e!r}")
    try:
        from assistant.services import risk_rules

        items += risk_rules.risk_cards(uid, now=now)  # B7: риски по реестру
    except Exception as e:
        print(f"[proactive] risks failed uid={uid} err={e!r}")
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
