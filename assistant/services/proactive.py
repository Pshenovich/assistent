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

from assistant.lib import sqlite_conn

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
    _CONN = sqlite_conn.connect(path)
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


def undismiss(owner_user_id: Any, key: str) -> None:
    """«Вернуть»: карточка снова может показаться."""
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM proactive_dismissed WHERE owner_user_id = ? AND item_key = ?",
                     (str(owner_user_id), str(key)[:300]))
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


def _posted_by(t: dict[str, Any], uid: str) -> bool:
    owner = str(t.get("owner_user_id") or "")
    assignee = str(t.get("assignee_user_id") or "")
    return owner == uid and bool(assignee) and assignee != uid


def rule_posted_overdue(tasks: list[dict[str, Any]], now: datetime, tz: Any) -> list[dict[str, Any]]:
    """Просроченные поручения другим — одна карточка с AWAITING_REPLY_HOUR (18:00) до конца дня."""
    from assistant.services.awaiting_reply import awaiting_hour

    local = now.astimezone(tz)
    if local.hour < awaiting_hour():
        return []
    late = []
    for t in tasks:
        if t.get("done") or t.get("owner_archived"):
            continue
        end = _parse(t.get("end_at")) or _parse(t.get("start_at"))
        if end and end < now - timedelta(minutes=30):
            late.append(t)
    if not late:
        return []
    late.sort(key=lambda t: str(t.get("start_at") or ""))
    ids = [int(t["id"]) for t in late]
    n = len(late)
    word = "поручение просрочено" if n % 10 == 1 and n % 100 != 11 else (
        "поручения просрочены" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "поручений просрочено")
    names: list[str] = []
    for t in late:
        nm = str(t.get("assignee_name") or "исполнитель")
        if nm not in names:
            names.append(nm)
    task_rows = []
    for t in late:
        start = _parse(t.get("start_at"))
        meta = str(t.get("assignee_name") or "исполнитель") + (f" · срок {_day(start, tz, now)}" if start else "")
        task_rows.append({"id": int(t["id"]), "text": str(t.get("title") or "Задача"), "meta": meta})
    title = f"{n} {word}"
    return [
        {
            "key": f"posted_overdue:{local.date().isoformat()}",
            "type": "posted_overdue",
            "tag": "⚠ поручения",
            "urgency": 3,
            "title": title,
            "source": ", ".join(names[:4]) + (f" и ещё {len(names) - 4}" if len(names) > 4 else ""),
            "ask": title + ". " + "; ".join(f"«{r['text']}» — {r['meta']} (task id {r['id']})" for r in task_rows[:20]),
            "tasks": task_rows,
            "actions": [
                {"label": "Напомнить всем", "act": "nudge_tasks", "primary": True, "payload": {"task_ids": ids}},
                {"label": "Поручения", "act": "open_posted"},
            ],
        }
    ]


def rule_task_inbox(uid: int, now: datetime) -> list[dict[str, Any]]:
    """Входящие поручения исполнителя — одна карточка «Вам поставили N задач», первой в ленте."""
    from assistant.lib.task_notify import owner_short_name, _tasks_word
    from assistant.stores import calendar_tasks

    inbox = calendar_tasks.list_inbox(uid, now=now)
    items, snoozed = inbox["items"], int(inbox["snoozed"])
    if not items:
        return []
    by_owner: dict[str, int] = {}
    for t in items:
        name = owner_short_name(t.get("owner_user_id"))
        by_owner[name] = by_owner.get(name, 0) + 1
    parts = [", ".join(f"{n} — {c}" for n, c in sorted(by_owner.items(), key=lambda kv: -kv[1]))]
    if snoozed:
        parts.append(f"ещё {snoozed} отложено")
    return [
        {
            "key": "task_inbox",
            "type": "task_inbox",
            "tag": "📥 входящие",
            "urgency": 9,
            "title": f"Вам поставили {_tasks_word(len(items))}",
            "source": " · ".join(parts),
            "count": len(items),
            "snoozed": snoozed,
            "actions": [{"label": "Разобрать", "act": "open_task_inbox", "primary": True}],
        }
    ]


def _prep_mail(ev: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Последние ветки переписки с участниками встречи (если подключена почта)."""
    uid = ev.get("_owner_uid")
    if not uid:
        return []
    try:
        from assistant.services import mail_source
        from assistant.services.calendar import event_attendee_emails

        return mail_source.prep_threads(int(uid), event_attendee_emails(ev), now=now)
    except Exception as e:
        print(f"[proactive] prep mail failed err={e!r}")
        return []


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
        threads = _prep_mail(ev, now)
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
                "source": ", ".join(others[:3]) + (" · есть заметка встречи" if has_note else " · повестки нет")
                + (f" · переписка: «{threads[0]['subject']}»" + (f" и ещё {len(threads) - 1}" if len(threads) > 1 else "")
                   if threads else ""),
                "ask": f"Подготовка к встрече «{ev.get('summary')}» {_hm(st, tz)} с {', '.join(others[:5])}"
                + ("".join(f"\nПереписка: «{t['subject']}» ({t['from']})" for t in threads)),
                "event": _event_ref(ev),
                "mail_threads": threads,
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


def summary_task_id(note_id: int, line: str, item_id: str = "") -> str:
    """Ключ задачи саммари: по её ID (не меняется при правке текста), у старых саммари — по тексту строки."""
    from assistant.lib.note_items import ITEM_ID_RE

    if ITEM_ID_RE.match(str(item_id or "")):
        return _key("sumtask", note_id, str(item_id))
    return _key("sumtask", note_id, hashlib.sha1(line.encode("utf-8")).hexdigest()[:10])


def card_groups(nid: int, state: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Группы от Лео, у которых в карточке ≥2 пункта: в стопке «Разобрать» они — одна карточка."""
    mine = {it.get("item") for it in items if it.get("item")}
    out = []
    for g in state.get("groups") or []:
        members = list(g["items"]) + ([g["parent"]] if g.get("parent") else [])
        if sum(1 for m in members if m in mine) >= 2:
            out.append({**g, "note_id": nid})
    return out


def _plural(n: int, one: str, few: str, many: str) -> str:
    return one if n % 10 == 1 and n % 100 != 11 else (few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many)


def rule_summary_tasks(
    notes: list[dict[str, Any]],
    has_tasks: dict[int, Any],
    now: datetime,
    tz: Any,
    done: Optional[set[str]] = None,
    foreign: Optional[set[int]] = None,
    viewer: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Карточка «Задачи встречи · N из M»: пункты, по которым решать этому пользователю.

    has_tasks[note_id]: задачи, уже поставленные из этой встречи (названия или {title, by, task_id}
    — от любого участника); True — встреча разобрана целиком (старый вызов).
    viewer — кто смотрит: в карточке только пункты, где он исполнитель (или владелец записи, если
    исполнитель неясен, не в Лео или отказался); нет таких — нет карточки. Без viewer — все открытые.
    Разобранные кем-то — в closed_items (со статусом и автором), в счёт карточки не идут."""
    from assistant.services import meeting_tasks
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
        state = meeting_tasks.rows(n, posted_list)
        items, closed, waiting = [], [], []
        for t, st_row in zip(tasks, state["items"]):
            parts = [str(t.get("assignee") or "").strip(), str(t.get("task") or "").strip(), str(t.get("deadline") or "").strip()]
            line = " — ".join(p for p in parts if p)
            iid = summary_task_id(nid, line, t.get("id"))
            st = task_state(t, posted_list) if st_row["code"] != "declined" else None
            if st:  # уже поставил или выполнил кто-то из участников встречи (отказ исполнителя — снова открыт)
                closed.append({"id": iid, "text": line, **({"item": t["id"]} if t.get("id") else {}), **st})
                continue
            if st_row["code"] not in meeting_tasks.UNDECIDED:
                continue  # в работе / в трекере и т.п. — по таблице
            if viewer and st_row.get("decider") and str(st_row["decider"]) != str(viewer):
                continue  # решать не ему — пункт у исполнителя или владельца записи
            if iid in done:
                continue
            item = {"id": iid, "text": line}
            if t.get("id"):
                item["item"] = str(t["id"])
            if viewer and str(st_row.get("decider") or "") == str(viewer) and str(t.get("assignee") or "").strip():
                item["mine"] = True
            elif nid not in foreign and t.get("owner_flag") == "mine":
                item["mine"] = True
            if st_row["code"] == "declined":
                item["declined"] = st_row["label"]
            if t.get("quote") and float(t.get("quote_score") or 0) >= 0.6:
                item["quote"] = str(t["quote"])[:300]
            items.append(item)
            waiting.append((str(t.get("task") or "").strip(), st_row))
        if not items:
            continue
        order = sorted(range(len(items)), key=lambda i: not items[i].get("mine"))  # свои — первыми
        items = [items[i] for i in order]
        waiting = [waiting[i] for i in order]
        lines = [i["text"] for i in items[:12]]
        title = str(n.get("title") or "Встреча")
        cnt = len(items)
        short = []
        for text, st_row in waiting[:2]:
            words = text.split()
            label = " ".join(words[:3]) + ("…" if len(words) > 3 else "")
            if st_row["code"] == "declined":
                who = str(st_row.get("who") or "исполнитель").split()[0]
                label += f" ({who} отказ{'ался' if not who.endswith('а') and not who.endswith('я') else 'алась'})"
            short.append(label)
        more = f" и ещё {cnt - 2}" if cnt > 2 else ""
        head = f"{cnt} {_plural(cnt, 'пункт ждёт', 'пункта ждут', 'пунктов ждут')} вашего решения: " + ", ".join(short) + more
        out.append(
            {
                "key": _key("sumtasks", nid),
                "type": "summary_tasks",
                "tag": f"Задачи встречи · {state['decided']}/{state['total']}",  # «из» не влезает в подпись карточки
                "urgency": 2,
                "title": head,
                "sub": _day(created, tz, now),
                "source": title,
                "ask": f"Задачи из встречи «{title}»: " + "; ".join(lines),
                "note": {"id": nid, "kind": "transcription", "title": title, "anchor": "tasks"},
                "items": items,
                "groups": card_groups(nid, state, items),
                "closed_items": closed,
                "decided": state["decided"],
                "total": state["total"],
                "created_at": created.isoformat(),
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
        # Поручения другим — не «перенести на завтра» в течение дня, а одна сводка вечером («Напомнить всем»).
        posted = [t for t in tasks if _posted_by(t, str(uid))]
        items += rule_overdue([t for t in tasks if not _posted_by(t, str(uid))], now, tz, str(uid))
        items += rule_posted_overdue(posted, now, tz)
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
        items += rule_prep([{**e, "_owner_uid": uid} for e in evs], kinds, now, tz)
        try:
            from assistant.services import meeting_topics

            card = meeting_topics.link_card(uid, evs, now)  # «связать в одну тему?» — для брифа
            if card:
                items.append(card)
        except Exception as e:
            print(f"[proactive] topic link failed uid={uid} err={e!r}")
    if prefs.get("after_meetings"):
        items += rule_no_results(evs, kinds, now, tz)
    try:
        from assistant.services.meeting_share import posted_for_note

        notes = notes_store.list_transcription_notes(uid, limit=40)  # свои и те, которыми поделились
        foreign = {int(n["id"]) for n in notes if str(n.get("owner_user_id") or n.get("user_id") or uid) != str(uid)}
        has_tasks = {int(n["id"]): posted_for_note(n) for n in notes}
        if prefs.get("after_meetings"):
            items += rule_summary_tasks(notes, has_tasks, now, tz, done_keys(uid), foreign, viewer=str(uid))
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
        from assistant.services import awaiting_reply, mail_source

        card = awaiting_reply.awaiting_card(uid, now=now, chats=bool(prefs.get("chats")))
        if card:
            items.append(card)
        try:
            items += mail_source.mail_cards(uid, task_titles=[str(t.get("title") or "") for t in window], now=now)
        except Exception as e:
            print(f"[proactive] mail cards failed uid={uid} err={e!r}")
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
    out = rank(items, dismissed_keys(uid))
    try:
        # Входящие — вне ранжирования и «скрыть»: пропадают, только когда всё разобрано.
        inbox = rule_task_inbox(uid, now)
        if inbox:
            out = inbox + out[: MAX_ITEMS - 1]
    except Exception as e:
        print(f"[proactive] task inbox failed uid={uid} err={e!r}")
    return out


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
    inbox = [it for it in items if it.get("type") == "task_inbox"]
    items = [it for it in items if it.get("type") != "task_inbox"]
    if inbox:
        from assistant.lib.task_notify import inbox_webapp_url

        it = inbox[0]
        url = _h.escape(inbox_webapp_url(), quote=True)
        lines.append("")
        lines.append(f'📥 {_h.escape(str(it.get("title") or ""))} ({_h.escape(str(it.get("source") or ""))}). <a href="{url}">Разобрать</a>')
    # Задачи встреч, которые ждут решения больше суток, — отдельной строкой на встречу, со ссылкой на таблицу.
    stale = [
        it for it in items
        if it.get("type") == "summary_tasks" and (_parse(it.get("created_at")) or local_now) <= local_now - timedelta(hours=24)
    ]
    items = [it for it in items if it not in stale]
    if stale:
        from assistant.stores.note_members import note_webapp_url

        lines.append("")
        for it in stale[:3]:
            note = it.get("note") or {}
            n = len(it.get("items") or [])
            url = _h.escape(note_webapp_url(int(note["id"])) + "&tasks=1", quote=True) if note.get("id") else ""
            what = f"{n} {_plural(n, 'пункт ждёт', 'пункта ждут', 'пунктов ждут')} вашего решения больше суток"
            link = f' <a href="{url}">Открыть таблицу</a>' if url else ""
            lines.append(f"🗂 «{_h.escape(str(note.get('title') or 'Встреча'))}»: {what}.{link}")
    if items:
        lines.append("")
        lines.append("<b>Главное:</b>")
        for it in items[:3]:
            lines.append(f"• {_h.escape(str(it.get('title') or ''))}")
    return "\n".join(lines)
