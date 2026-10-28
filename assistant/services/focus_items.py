"""«Фокус»: разобранные пункты инсайтов — статус, правки, ссылка, отметка в исходнике.

Карточки Фокуса считаются на лету; здесь хранится только то, что пользователь с пунктом
сделал: выполнил, связал с задачей, отложил, скрыл (с причиной), сменил тип, уточнил поля.
Разобранный пункт больше не поднимается: ни по ключу, ни по похожему тексту (переформулировка
при повторном разборе заметки даёт новый ключ — ловим её по словам).

Исходник (саммари встречи или заметка) получает значок в конце строки пункта —
<span class="note-focus-mark" …>; транскрипт не трогаем. Значки вырезаются перед хешем
и разбором текста ИИ, чтобы отметка не запускала повторный разбор.
"""

from __future__ import annotations

import hashlib
import html as _h
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Optional

from assistant.lib import sqlite_conn

# Пункт ушёл из Фокуса насовсем.
CLOSED = ("completed", "linked", "converted", "dismissed", "retyped")
STATUSES = CLOSED + ("snoozed", "new")
REASONS = ("not_a_task", "not_relevant", "duplicate", "recognition_error", "not_me", "newsletter")
TYPES = ("task", "promise", "decision", "reminder", "thought")
# Тип, после которого пункт остаётся в Фокусе (обещание — тоже дело).
ACTIVE_TYPES = ("task", "promise")
FIELDS = ("assignee", "task", "deadline", "state", "link", "comment")
TITLES_WINDOW = timedelta(days=90)

# Реестр обязательств (B1): каждый пункт Фокуса пишется сразу при построении ленты.
# commitment_id общий у одной договорённости из разных источников (встреча, заметка, чат).
_REGISTRY_COLS = ("direction", "commitment_id", "quote", "due_iso", "seen_at")
DIRECTIONS = ("mine", "theirs", "unknown")
# Вопросы из чатов (chat_request) — не обязательства: их ведёт сводка «Ждут вашего ответа».
COMMITMENT_KINDS = ("summary_task", "agreement", "chat_promise", "mail_promise")
_DEDUP_WINDOW = timedelta(days=60)

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None
_RU_MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


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
        CREATE TABLE IF NOT EXISTS focus_items (
            owner_user_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            card_key TEXT NOT NULL DEFAULT '',
            source_json TEXT NOT NULL DEFAULT '{}',
            kind TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            who TEXT NOT NULL DEFAULT '',
            due TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            type TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            snooze_until TEXT NOT NULL DEFAULT '',
            fields_json TEXT NOT NULL DEFAULT '{}',
            link_json TEXT NOT NULL DEFAULT '{}',
            by_name TEXT NOT NULL DEFAULT '',
            history_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, item_key)
        )
        """
    )
    have = {r[1] for r in _CONN.execute("PRAGMA table_info(focus_items)").fetchall()}
    for col in _REGISTRY_COLS:
        if col not in have:
            _CONN.execute(f"ALTER TABLE focus_items ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    _CONN.execute("CREATE INDEX IF NOT EXISTS idx_focus_items_cid ON focus_items(owner_user_id, commitment_id)")
    _CONN.commit()
    return _CONN


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(raw: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in ("source_json", "fields_json", "link_json", "history_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k) or ("[]" if k == "history_json" else "{}"))
        except ValueError:
            d[k[:-5]] = [] if k == "history_json" else {}
    return d


def get(owner_user_id: Any, item_key: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute(
            "SELECT * FROM focus_items WHERE owner_user_id = ? AND item_key = ?",
            (str(owner_user_id), str(item_key)),
        ).fetchone()
    return _row(r) if r else None


def rows(owner_user_id: Any) -> list[dict[str, Any]]:
    with _LOCK:
        rs = _conn().execute("SELECT * FROM focus_items WHERE owner_user_id = ?", (str(owner_user_id),)).fetchall()
    return [_row(r) for r in rs]


def is_hidden(row: dict[str, Any], now: Optional[datetime] = None) -> bool:
    st = str(row.get("status") or "new")
    if st in CLOSED:
        return True
    if st == "snoozed":
        until = _parse(row.get("snooze_until"))
        return bool(until and until > (now or _now()))
    return False


def remember(owner_user_id: Any, item: dict[str, Any]) -> dict[str, Any]:
    """Снимок пункта карточки (текст, кто, срок, источник) — чтобы разбирать его в чате."""
    key = str(item.get("key") or "").strip()[:300]
    if not key:
        raise ValueError("Нет пункта")
    now = _now().isoformat()
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO focus_items (owner_user_id, item_key, card_key, source_json, kind, text, who, due, type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, item_key) DO UPDATE SET
                card_key = excluded.card_key, source_json = excluded.source_json, kind = excluded.kind,
                text = excluded.text, who = excluded.who, due = excluded.due
            """,
            (
                str(owner_user_id),
                key,
                str(item.get("card_key") or key)[:300],
                json.dumps(item.get("source") or {}, ensure_ascii=False),
                str(item.get("kind") or "")[:40],
                str(item.get("text") or "").strip()[:400],
                str(item.get("who") or "").strip()[:120],
                str(item.get("due") or "").strip()[:40],
                str(item.get("type") or "task")[:20],
                now,
                now,
            ),
        )
        conn.commit()
    return get(owner_user_id, key) or {}


def _save(owner_user_id: Any, row: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    now = _now().isoformat()
    hist = list(row.get("history") or [])[-30:]
    hist.append({**event, "at": now})
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            UPDATE focus_items SET status = ?, type = ?, reason = ?, snooze_until = ?, fields_json = ?, link_json = ?,
                by_name = ?, history_json = ?, updated_at = ?
            WHERE owner_user_id = ? AND item_key = ?
            """,
            (
                str(row.get("status") or "new"),
                str(row.get("type") or ""),
                str(row.get("reason") or ""),
                str(row.get("snooze_until") or ""),
                json.dumps(row.get("fields") or {}, ensure_ascii=False),
                json.dumps(row.get("link") or {}, ensure_ascii=False),
                str(row.get("by_name") or ""),
                json.dumps(hist, ensure_ascii=False),
                now,
                str(owner_user_id),
                str(row["item_key"]),
            ),
        )
        conn.commit()
    return get(owner_user_id, row["item_key"]) or row


# ---------------------------------------------------------------- действия

def _recent_task_for(owner_user_id: Any, row: dict[str, Any]) -> Optional[int]:
    """«В задачу» через ассистента: задачу ставит ИИ, id сюда не приходит — берём только что
    созданную задачу с похожим названием, чтобы в исходнике встал чип задачи, а не просто «✓»."""
    from assistant.stores import calendar_tasks as tasks_store

    since = (_now() - timedelta(minutes=15)).replace(microsecond=0).isoformat()
    try:
        recent = tasks_store.list_recent_owner_tasks(owner_user_id, since)
    except Exception as e:
        print(f"[focus_items] recent tasks failed uid={owner_user_id} err={e!r}")
        return None
    want = str((row.get("fields") or {}).get("task") or row.get("text") or "")
    best, best_s = None, 0.0
    for t in recent:
        s = _score(want, str(t.get("title") or ""))
        if s > best_s:
            best, best_s = t, s
    return int(best["id"]) if best and best_s >= 0.5 else None


def _task_title(task_id: Any) -> str:
    from assistant.stores import calendar_tasks as tasks_store

    try:
        row = tasks_store._get_task_row(int(task_id))
    except Exception:
        return ""
    return str((row or {}).get("title") or "").strip()[:200]


def apply(owner_user_id: Any, item_key: str, op: str, *, by: str = "", **kw: Any) -> dict[str, Any]:
    """Действие с пунктом: done / link / snooze / dismiss / retype / update / converted / undo."""
    row = get(owner_user_id, item_key)
    if not row:
        raise ValueError("Пункт не найден — обновите Фокус")
    row = dict(row)
    event: dict[str, Any] = {"op": op, "by": by}
    row["by_name"] = by or row.get("by_name") or ""
    if op == "done":
        row.update(status="completed", reason="", snooze_until="")
    elif op == "converted":
        row.update(status="converted", reason="", snooze_until="")
        task_id = kw.get("task_id") or _recent_task_for(owner_user_id, row)
        if task_id:
            row["link"] = {**(row.get("link") or {}), "task_id": int(task_id)}
            title = _task_title(task_id)
            if title:
                row["link"]["title"] = title  # чип в заметке — с названием, которое дали задаче
    elif op == "link":
        url = str(kw.get("url") or "").strip()
        task_id = kw.get("task_id")
        if not url and not task_id:
            raise ValueError("Нужна ссылка или задача")
        if url and not re.match(r"^https?://", url, re.I):
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        link: dict[str, Any] = {"url": url[:1000]} if url else {}
        if task_id:
            link["task_id"] = int(task_id)
        if task_id or kw.get("title"):
            link["title"] = str(kw.get("title") or "").strip()[:200]
        row.update(status="linked", link=link, reason="", snooze_until="")
        event["link"] = link
    elif op == "snooze":
        until = _parse(kw.get("until"))
        if not until or until <= _now():
            raise ValueError("Выберите время в будущем")
        row.update(status="snoozed", snooze_until=until.isoformat())
        event["until"] = row["snooze_until"]
    elif op == "dismiss":
        reason = str(kw.get("reason") or "not_relevant")
        if reason not in REASONS:
            raise ValueError("Неизвестная причина")
        row.update(status="dismissed", reason=reason, snooze_until="")
        event["reason"] = reason
    elif op == "retype":
        t = str(kw.get("type") or "")
        if t not in TYPES:
            raise ValueError("Неизвестный тип")
        row["type"] = t
        event["type"] = t
        if t == "reminder":
            until = _parse(kw.get("until"))
            if not until or until <= _now():
                raise ValueError("Выберите, когда напомнить")
            row.update(status="snoozed", snooze_until=until.isoformat())
        elif t in ACTIVE_TYPES:
            row.update(status="new", snooze_until="")
        else:
            row.update(status="retyped", snooze_until="")
    elif op == "update":
        fields = dict(row.get("fields") or {})
        for k, v in (kw.get("fields") or {}).items():
            if k in FIELDS:
                fields[k] = str(v or "").strip()[:400]
        row["fields"] = fields
        event["fields"] = kw.get("fields") or {}
        if fields.get("link") and re.match(r"^https?://", fields["link"], re.I):
            row["link"] = {**(row.get("link") or {}), "url": fields["link"]}
        if fields.get("state") == "done":
            row.update(status="completed", snooze_until="")
    elif op == "undo":
        row.update(status="new", reason="", snooze_until="", type=row.get("type") or "task")
        if row.get("type") not in ACTIVE_TYPES:
            row["type"] = "task"
    else:
        raise ValueError("Неизвестное действие")
    saved = _save(owner_user_id, row, event)
    if op == "dismiss" and saved.get("reason") == "not_me":
        # «Не моя» — личное: в общем саммари пункт открыт, но решать его теперь владельцу записи
        _route_not_me(owner_user_id, saved)
        return saved
    try:
        sync_source(owner_user_id, saved)
    except Exception as e:  # отметка в исходнике — бонус, не повод провалить действие
        print(f"[focus_items] source sync failed uid={owner_user_id} key={item_key} err={e!r}")
    return saved


def _route_not_me(viewer_uid: Any, row: dict[str, Any]) -> None:
    src = row.get("source") or {}
    if src.get("kind") != "note" or not src.get("id"):
        return
    try:
        from assistant.services import meeting_tasks
        from assistant.stores import notes as notes_store

        note = notes_store.get_accessible_note(viewer_uid, int(src["id"]), with_sharing=False)
        tasks = ((note or {}).get("meta") or {}).get("tasks") or []
        i = _meta_task_index(tasks, row) if tasks else -1
        if i >= 0 and tasks[i].get("id"):
            meeting_tasks.not_me(viewer_uid, int(src["id"]), str(tasks[i]["id"]))
    except Exception as e:
        print(f"[focus_items] not_me route failed uid={viewer_uid} key={row.get('item_key')} err={e!r}")


def task_done_changed(owner_user_id: Any, task_id: int, done: bool, by: str = "") -> int:
    """Задачу, поставленную из пункта, выполнили (или вернули в работу) — в саммари пункт «Выполнено»."""
    with _LOCK:
        found = [
            _row(r)
            for r in _conn().execute(
                "SELECT * FROM focus_items WHERE owner_user_id = ? AND link_json LIKE ?",
                (str(owner_user_id), f'%"task_id": {int(task_id)}%'),
            ).fetchall()
        ]
    n = 0
    for row in found:
        if int((row.get("link") or {}).get("task_id") or 0) != int(task_id):
            continue
        want = "completed" if done else "converted"
        if row.get("status") not in ("converted", "completed", "linked") or row.get("status") == want:
            continue
        row = dict(row, status=want)
        if by:
            row["by_name"] = by
        saved = _save(owner_user_id, row, {"op": "task_done" if done else "task_undone", "by": by})
        try:
            sync_source(owner_user_id, saved)
        except Exception as e:
            print(f"[focus_items] task done sync failed uid={owner_user_id} task={task_id} err={e!r}")
        n += 1
    return n


def handled_titles(owner_user_id: Any, now: Optional[datetime] = None) -> list[str]:
    """Тексты разобранных пунктов: похожие формулировки из других материалов не поднимаем."""
    now = now or _now()
    since = (now - TITLES_WINDOW).isoformat()
    out = []
    for r in rows(owner_user_id):
        if str(r.get("updated_at") or "") < since or not is_hidden(r, now):
            continue
        for t in (r.get("text"), (r.get("fields") or {}).get("task")):
            if str(t or "").strip():
                out.append(str(t))
    return out


def hidden_keys(owner_user_id: Any, now: Optional[datetime] = None) -> set[str]:
    return {str(r["item_key"]) for r in rows(owner_user_id) if is_hidden(r, now)}


# ---------------------------------------------------------------- карточки Фокуса

_TYPE_TAGS = {"promise": "🤝 обещание", "reminder": "🔔 напоминание"}


def _due_label(raw: str) -> str:
    s = str(raw or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{int(m.group(3))} {_RU_MON[int(m.group(2)) - 1]}"
    return s


def apply_to_feed(owner_user_id: Any, items: list[dict[str, Any]], now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Убрать разобранные пункты из карточек, подставить правки (исполнитель, срок, тип)."""
    state = {str(r["item_key"]): r for r in rows(owner_user_id)}
    if not state:
        return items
    now = now or _now()
    dups = duplicate_keys(owner_user_id, now)
    for k in dups:  # дубль другой договорённости ведём как скрытый
        if k in state:
            state[k] = {**state[k], "status": "dismissed", "reason": "duplicate"}
    out = []
    for card in items:
        sub = card.get("items") or []
        if card.get("type") == "summary_tasks" and sub:
            left = [i for i in sub if not (state.get(str(i.get("id"))) and is_hidden(state[str(i.get("id"))], now))]
            if not left:
                continue
            if len(left) != len(sub):
                card = {**card, "items": left}
                n = len(left)
                word = "задача" if n % 10 == 1 and n % 100 != 11 else ("задачи" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "задач")
                title = str((card.get("note") or {}).get("title") or "Встреча")
                card["title"] = f"«{title}»: {n} {word} из встречи — в Orbita их нет"
                card["actions"] = [
                    {**a, "label": "✓ Сделано" + ("…" if n > 1 else ""), "act": "mark_done_pick" if n > 1 else "mark_done"}
                    if a.get("act") in ("mark_done", "mark_done_pick") else a
                    for a in card.get("actions") or []
                ]
            out.append(card)
            continue
        r = state.get(str(card.get("key")))
        if not r:
            out.append(card)
            continue
        if is_hidden(r, now):
            continue
        card = dict(card)
        f = r.get("fields") or {}
        if r.get("type") in _TYPE_TAGS:
            card["tag"] = _TYPE_TAGS[r["type"]]
        if f.get("task") or f.get("assignee"):
            who = f.get("assignee") or r.get("who") or ""
            text = f.get("task") or r.get("text") or card.get("title") or ""
            card["title"] = f"{who} — {text}" if who and not who.lower() in ("я", "мне") else text
        if f.get("deadline"):
            base = str(card.get("source") or "").split(" · срок ")[0]
            card["source"] = f"{base} · срок {_due_label(f['deadline'])}"
        out.append(card)
    return out


def item_from_card(card: dict[str, Any], *, with_closed: bool = False) -> list[dict[str, Any]]:
    """Пункты карточки Фокуса в виде снимков для remember().

    with_closed — и задачи встречи, которые уже поставил или выполнил кто-то из участников (для чата)."""
    src: dict[str, Any] = {}
    if card.get("note"):
        src = {**card["note"], "kind": "note", "note_kind": card["note"].get("kind") or "note"}
    elif card.get("chat"):
        src = {**card["chat"], "kind": "chat"}
    elif card.get("mail"):
        m = card["mail"]
        src = {"kind": "mail", "id": m.get("mail_key"), "from": m.get("from") or "", "thread": m.get("thread") or ""}
    src["label"] = str(card.get("source") or "")[:200]
    url = next((str((a.get("payload") or {}).get("url") or "") for a in card.get("actions") or []
                if a.get("act") == "url"), "")
    if url:
        src["url"] = url  # ссылка на сообщение — футер-источник открывает его
    out = []
    if card.get("type") == "awaiting_reply":
        for i in card.get("items") or []:
            if str(i.get("id") or "").startswith("mail:"):  # письмо без ответа
                out.append({
                    "key": i.get("id"), "card_key": card.get("key"), "kind": "mail_request", "text": i.get("text"),
                    "who": "", "due": "", "type": "task",
                    "source": {"kind": "mail", "id": i.get("mail_key"), "from": i.get("from") or "",
                               "label": str(card.get("source") or "")[:200]},
                })
                continue
            parts = str(i.get("id") or "").split(":")  # chat:<cid>:<day>:<hash>
            chat_src = {"kind": "chat", "id": int(parts[1]) if len(parts) > 2 and parts[1].lstrip("-").isdigit() else None,
                        "label": str(card.get("source") or "")[:200], "url": str(i.get("url") or "")}
            out.append({
                "key": i.get("id"), "card_key": card.get("key"), "kind": "chat_request",
                "text": i.get("text"), "who": "", "due": "", "source": chat_src, "type": "task",
            })
        return out
    if card.get("type") == "summary_tasks":
        for i in list(card.get("items") or []) + (list(card.get("closed_items") or []) if with_closed else []):
            parts = [p.strip() for p in str(i.get("text") or "").split(" — ")]
            who, task, due = (parts + ["", "", ""])[:3] if len(parts) >= 3 else (
                (parts[0], parts[1], "") if len(parts) == 2 else ("", parts[0] if parts else "", "")
            )
            out.append({
                "key": i.get("id"), "card_key": card.get("key"), "kind": "summary_task",
                "text": task or i.get("text"), "who": who, "due": due, "type": "task",
                "source": {**src, "item": i["item"]} if i.get("item") else src,
                "mine": bool(i.get("mine")), "quote": str(i.get("quote") or ""),
            })
        return out
    meta = card.get("_item") or {}
    out.append({
        "key": card.get("key"),
        "card_key": card.get("key"),
        "kind": str(card.get("type") or ""),
        "text": meta.get("text") or card.get("title") or "",
        "who": meta.get("who") or "",
        "due": meta.get("due") or "",
        "source": {**src, "item": meta["item"]} if meta.get("item") else src,
        "type": "promise" if str(card.get("type") or "") in ("chat_promise", "mail_promise") else "task",
        "quote": str(meta.get("quote") or ""),
    })
    return out


def public(row: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    f = row.get("fields") or {}
    src = row.get("source") or {}
    status = row.get("status") or "new"
    if status == "snoozed" and not is_hidden(row, now):
        status = "new"  # срок «Отложить» прошёл — пункт снова открыт (и в Фокусе, и в карточке чата)
    return {
        "key": row["item_key"],
        "card_key": row.get("card_key") or row["item_key"],
        "kind": row.get("kind") or "",
        "type": row.get("type") or "task",
        "status": status,
        "reason": row.get("reason") or "",
        "snooze_until": row.get("snooze_until") or "",
        "text": f.get("task") or row.get("text") or "",
        "assignee": f.get("assignee") or row.get("who") or "",
        "deadline": f.get("deadline") or row.get("due") or "",
        "state": f.get("state") or "",
        "comment": f.get("comment") or "",
        "link": row.get("link") or {},
        "source": {k: src.get(k) for k in ("kind", "note_kind", "id", "item", "title", "label", "date", "url") if src.get(k) is not None},
        "by": row.get("by_name") or "",
        "updated_at": row.get("updated_at") or "",
    }


def describe(row: dict[str, Any]) -> str:
    """Строка для истории ассистента: какой пункт разбирается и что с ним уже сделано."""
    p = public(row)
    st = {
        "new": "не разобран", "completed": "выполнен", "linked": "связан с задачей", "converted": "поставлена задача",
        "dismissed": "скрыт", "retyped": f"тип изменён на {p['type']}", "snoozed": "отложен",
    }.get(p["status"], p["status"])
    who = f"{p['assignee']} — " if p["assignee"] else ""
    due = f", срок {p['deadline']}" if p["deadline"] else ""
    return f"пункт Фокуса «{who}{p['text']}{due}» (focus key: {p['key']}; {st})"


# ---------------------------------------------------------------- отметка в исходнике

MARK_RE = re.compile(r'<span[^>]*class="note-focus-mark"[^>]*>.*?</span>', re.S | re.I)
_MARK_ATTR_RE = re.compile(r'data-leo-mark(?:-(\w+))?="([^"]*)"')


def mark_id(item_key: str) -> str:
    return hashlib.sha1(str(item_key).encode("utf-8")).hexdigest()[:12]


def strip_marks(html_text: str) -> str:
    return MARK_RE.sub("", str(html_text or ""))


def _mark_attrs(span: str) -> dict[str, str]:
    out = {}
    for m in _MARK_ATTR_RE.finditer(span):
        out[m.group(1) or "status"] = _h.unescape(m.group(2))
    return out


def _short_date(iso: str) -> str:
    d = _parse(iso)
    return d.strftime("%d.%m") if d else ""


_DISMISS_LABEL = {"not_a_task": "не задача", "duplicate": "дубль", "not_relevant": "неактуально",
                  "recognition_error": "ошибка распознавания", "not_me": "не мне"}
_RETYPE_LABEL = {"promise": "обещание", "decision": "решение", "thought": "мысль", "reminder": "напоминание"}


def marks_to_text(html_text: str) -> str:
    """Для ИИ: значок → «[выполнено: Вова, 20.10]», чтобы по документу было видно, что сделано."""

    def repl(m: re.Match[str]) -> str:
        a = _mark_attrs(m.group(0))
        st = a.get("status", "")
        if st == "task":
            head = ("задача выполнена" if a.get("done") else "поставлена задача") + (f" «{a['label']}»" if a.get("label") else "")
        elif st in ("dismissed", "retyped"):
            head = ("скрыто: " if st == "dismissed" else "тип изменён: ") + a.get("label", "")
        else:
            head = {"done": "выполнено", "linked": "связано с задачей", "snoozed": "отложено"}.get(st, "отмечено")
        bits = [x for x in (a.get("by"), _short_date(a.get("at", ""))) if x]
        url = f" {a['url']}" if a.get("url") else ""
        return f" [{head}{': ' + ', '.join(bits) if bits else ''}{url}]"

    return MARK_RE.sub(repl, str(html_text or ""))


def mark_html(row: dict[str, Any]) -> str:
    """Значок пункта в исходнике.

    Поставлена / связана задача в Лео — чип с названием задачи (открывает её);
    выполнено — «✓»; ссылка наружу — «Задача»; «не задача», «дубль», смена типа — серая пометка,
    чтобы в заметке было видно: пункт разобран, заводить его не нужно."""
    status = str(row.get("status") or "")
    link = row.get("link") or {}
    task_id = link.get("task_id")
    label = ""
    if task_id and status in ("converted", "linked", "completed"):
        st = "task"
        label = str(link.get("title") or "").strip() or public(row)["text"]
    elif status in ("completed", "converted"):
        st = "done"
    elif status == "linked":
        st = "linked"
    elif status == "dismissed":
        st, label = "dismissed", _DISMISS_LABEL.get(str(row.get("reason") or ""), "скрыто")
    elif status == "retyped":
        st, label = "retyped", _RETYPE_LABEL.get(str(row.get("type") or ""), "")
        if not label:
            return ""
    else:
        return ""
    attrs = {
        "": st,
        "item": mark_id(row["item_key"]),
        "by": str(row.get("by_name") or ""),
        "at": str(row.get("updated_at") or ""),
    }
    url = str(link.get("url") or "")
    if url:
        attrs["url"] = url
    if task_id:
        attrs["task"] = str(task_id)
    if label:
        attrs["label"] = label
    if st == "task" and status == "completed":
        attrs["done"] = "1"
    a = " ".join(f'data-leo-mark{"-" + k if k else ""}="{_h.escape(v, quote=True)}"' for k, v in attrs.items())
    # Внутри только текст (без вложенных span — MARK_RE их не переварит); иконку чипа рисует CSS.
    inner = {"done": "✓", "linked": "Задача"}.get(st) or _h.escape(label)
    return f'<span class="note-focus-mark" {a} contenteditable="false">{inner}</span>'


def _words(s: str) -> set[str]:
    return {w[:4] for w in re.findall(r"[a-zа-яё0-9]{4,}", str(s or "").lower().replace("ё", "е"))}


def _score(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


class _Blocks(HTMLParser):
    """Строки документа (li / p / заголовки) и место для значка в конце каждой."""

    BLOCKS = ("li", "p", "h1", "h2", "h3", "h4", "h5", "h6", "td")

    def __init__(self, src: str) -> None:
        super().__init__(convert_charrefs=True)
        self.src = src
        self._line_starts = [0]
        for m in re.finditer("\n", src):
            self._line_starts.append(m.end())
        self.stack: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []

    def _off(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("ul", "ol") and self.stack and self.stack[-1]["tag"] == "li" and self.stack[-1]["at"] is None:
            self.stack[-1]["at"] = self._off()
        if tag in self.BLOCKS:
            self.stack.append({"tag": tag, "text": "", "at": None})

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.BLOCKS:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                b = self.stack.pop(i)
                if b["at"] is None:
                    b["at"] = self._off()
                self.blocks.append(b)
                return

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1]["at"] is None:
            self.stack[-1]["text"] += data


_HTML_BODY_RE = re.compile(r"\s*<(p|h[1-6]|ul|ol|div|table|blockquote|pre)\b", re.I)


def _norm_line(s: str) -> str:
    return " ".join(re.findall(r"[a-zа-яё0-9]+", str(s or "").lower().replace("ё", "е")))


def place_mark(html_text: str, row: dict[str, Any], tasks: Optional[list[Any]] = None) -> tuple[str, bool]:
    """Снять старый значок пункта и поставить новый в конец самой похожей строки.

    Сначала — строка по ID пункта; нет его (или строки с ним) — ID задачи саммари из tasks
    (meta.tasks), к которой относится пункт: в саммари те же слова бывают и в «Решениях», и в
    «Задачах», чип должен встать на строку задачи. Дальше строку ищем по цитате и тексту пункта
    (заголовки — не кандидаты). Не нашлась (ИИ пересказал мысль своими словами), а пункт стал
    задачей — чип задачи встаёт отдельной строкой в конце: иначе из заметки не видно, что по ней
    уже поставлена задача."""
    mid = mark_id(row["item_key"])
    one = r'<span[^>]*class="note-focus-mark"[^>]*data-leo-mark-item="%s"[^>]*>.*?</span>' % mid
    src = re.sub(r"<p>\s*" + one + r"\s*</p>", "", str(html_text or ""), flags=re.S)
    src = re.sub(one, "", src, flags=re.S)
    mark = mark_html(row)
    if not mark:
        return src, src != html_text
    if not _HTML_BODY_RE.match(src):
        # Тело в Markdown (старое саммари встречи): HTML-значок в нём покажется сырым текстом.
        return src, src != html_text
    from assistant.lib import note_items

    line = note_items.find_line(src, str((row.get("source") or {}).get("item") or ""))
    if not line and tasks:
        i = _meta_task_index(tasks, row)
        if i >= 0:
            line = note_items.find_line(src, str(tasks[i].get("id") or ""))
    if line:  # строка пункта по её ID — как бы её ни правили
        at = int(line["at"])
        return src[:at].rstrip() + " " + mark + src[at:], True
    p = public(row)
    wants = [
        " ".join(x for x in (p["assignee"], row.get("text") or p["text"]) if x),
        str(row.get("quote") or ""),
    ]
    wants = [w for w in wants if w.strip()]
    quote = _norm_line(row.get("quote") or "")
    parser = _Blocks(src)
    try:
        parser.feed(src)
        parser.close()
    except Exception:
        return src, src != html_text
    best, best_score = None, 0.0
    for b in parser.blocks:
        if b["tag"].startswith("h"):
            continue  # «📋 Задачи» — одно слово, «совпадает» с любым пунктом
        if len(quote) >= 12 and quote in _norm_line(b["text"]):
            best, best_score = b, 1.0
            break
        s = max((_score(w, b["text"]) for w in wants), default=0.0)
        if s > best_score:
            best, best_score = b, s
    if best and best_score >= 0.5:
        at = int(best["at"])
        return src[:at].rstrip() + " " + mark + src[at:], True
    if 'data-leo-mark="task"' not in mark:
        return src, src != html_text
    return src.rstrip() + "<p>" + mark + "</p>", True


def task_renamed(owner_user_id: Any, task_id: int, title: str) -> int:
    """Задачу переименовали — чип в заметке-источнике показывает новое название.

    Связь пункта с задачей держится по task_id, подпись — снимок, его и обновляем."""
    title = str(title or "").strip()[:200]
    if not title:
        return 0
    with _LOCK:
        rows = [
            _row(r)
            for r in _conn().execute(
                "SELECT * FROM focus_items WHERE owner_user_id = ? AND link_json LIKE ?",
                (str(owner_user_id), f'%"task_id": {int(task_id)}%'),
            ).fetchall()
        ]
    n = 0
    for row in rows:
        link = dict(row.get("link") or {})
        if int(link.get("task_id") or 0) != int(task_id) or link.get("title") == title:
            continue
        link["title"] = title
        row["link"] = link
        with _LOCK:
            _conn().execute(
                "UPDATE focus_items SET link_json = ? WHERE owner_user_id = ? AND item_key = ?",
                (json.dumps(link, ensure_ascii=False), str(owner_user_id), row["item_key"]),
            )
            _conn().commit()
        try:
            sync_source(owner_user_id, row)
        except Exception as e:
            print(f"[focus_items] rename sync failed uid={owner_user_id} task={task_id} err={e!r}")
        n += 1
    return n


def sync_source(owner_user_id: Any, row: dict[str, Any]) -> None:
    """Отметить пункт в исходнике: значок в тексте + задача в meta саммари (выполнена, ссылка, правки)."""
    src = row.get("source") or {}
    if src.get("kind") != "note" or not src.get("id"):
        return  # пункты из чатов: исходник — переписка в Telegram, её не трогаем
    from assistant.stores import notes as notes_store

    # встречей могли поделиться: пишем в заметку владельца — отметку видят все участники
    note = notes_store.get_accessible_note(owner_user_id, int(src["id"]), with_sharing=False)
    if not note:
        return
    owner_user_id = str(note.get("owner_user_id") or note.get("user_id") or owner_user_id)
    note = notes_store.get_note(owner_user_id, int(src["id"]))
    if not note:
        return
    body = str(note.get("body") or "")
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    tasks = meta.get("tasks") if isinstance(meta.get("tasks"), list) else None
    meta_changed = _sync_meta_task(meta, row)
    from assistant.services import meeting_tasks

    if tasks and meeting_tasks.has_table(body) and _meta_task_index(tasks, row) >= 0:
        # задачи саммари — таблица: значок не нужен, статус — в ячейках строки
        new_body = meeting_tasks.refresh_body(body, meeting_tasks.rows({**note, "meta": meta}))
        changed = new_body != body
    else:
        new_body, changed = place_mark(body, row, tasks)
    if changed or meta_changed:
        notes_store.update_note(
            owner_user_id,
            int(src["id"]),
            body=new_body if changed else None,
            meta=meta if meta_changed else None,
        )
        _notify_participants(owner_user_id, int(src["id"]))


def _notify_participants(note_owner: str, note_id: int) -> None:
    """Владельцу и участникам встречи — перечитать Фокус: карточка задач у всех одна."""
    try:
        from assistant.realtime import notify as realtime_notify
        from assistant.stores import note_members

        uids = [note_owner] + [str(m.get("member_user_id") or "") for m in note_members.list_member_rows(note_owner, note_id)]
        realtime_notify.focus_changed(note_id, uids)
    except Exception as e:
        print(f"[focus_items] focus notify failed note={note_id} err={e!r}")


def _meta_task_index(tasks: list[Any], row: dict[str, Any]) -> int:
    """Задача саммари (индекс в meta.tasks), к которой относится пункт; -1 — не нашлась."""
    want = " ".join(x for x in (row.get("who"), row.get("text")) if x)
    item_id = str((row.get("source") or {}).get("item") or "")
    best_i, best_s = -1, 0.0
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        if item_id and str(t.get("id") or "") == item_id:
            return i  # задача саммари с тем же ID — как бы её ни переформулировали
        s = _score(want, f"{t.get('assignee') or ''} {t.get('task') or ''}")
        if s > best_s:
            best_i, best_s = i, s
    return best_i if best_s >= 0.5 else -1


def _sync_meta_task(meta: dict[str, Any], row: dict[str, Any]) -> bool:
    tasks = meta.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return False
    p = public(row)
    best_i = _meta_task_index(tasks, row)
    if best_i < 0:
        return False
    t = dict(tasks[best_i])
    before = json.dumps(t, ensure_ascii=False, sort_keys=True)
    f = row.get("fields") or {}
    if f.get("assignee"):
        t["assignee"] = f["assignee"]
        if "assignee_basis" in t:  # саммари v2: ручной исполнитель важнее карты спикеров
            t["assignee_basis"] = "named"
            t["assignee_name"] = f["assignee"]
            t["assignee_unresolved"] = False
    if f.get("task"):
        t["task"] = f["task"]
    if f.get("deadline"):
        t["deadline"] = f["deadline"]
    if f.get("comment"):
        t["comment"] = f["comment"]
    st = row.get("status")
    t["completed"] = st in ("completed", "converted")
    t["focus_status"] = st
    if st in ("completed", "converted", "linked", "dismissed"):
        t["completed_by"] = row.get("by_name") or ""
        t["completed_at"] = row.get("updated_at") or ""
    else:
        t.pop("completed_by", None)
        t.pop("completed_at", None)
    if st == "dismissed" and row.get("reason"):
        t["focus_reason"] = str(row["reason"])
    else:
        t.pop("focus_reason", None)
    if p["link"]:
        t["link"] = p["link"]
    else:
        t.pop("link", None)
    if row.get("type") and row.get("type") != "task":
        t["type"] = row["type"]
    if json.dumps(t, ensure_ascii=False, sort_keys=True) == before:
        return False
    tasks = list(tasks)
    tasks[best_i] = t
    meta["tasks"] = tasks
    if row.get("type") == "decision" and st == "retyped":
        dec = [str(x) for x in meta.get("decisions") or []]
        line = p["text"]
        if line and line not in dec:
            meta["decisions"] = dec + [line]
    return True


# ---------------------------------------------------------------- реестр обязательств (B1)

_ME_WORDS = {"я", "мне", "меня", "мной", "сам", "сама"}


def direction_for(item: dict[str, Any], owner_names: list[str]) -> str:
    """mine — за пользователем; theirs — за другими; unknown — исполнитель не ясен («Спикер N»)."""
    kind = str(item.get("kind") or "")
    if kind in ("chat_promise", "chat_request", "mail_promise", "mail_request") or item.get("mine"):
        return "mine"
    who = str(item.get("who") or "").strip()
    if not who or who.lower().startswith(("спикер", "speaker")):
        return "unknown"
    if who.lower() in _ME_WORDS:
        return "mine"
    from assistant.lib.summary_normalize import match_person

    if owner_names and match_person(who, [{"name": n, "aliases": []} for n in owner_names]):
        return "mine"
    return "theirs"


def _due_iso(raw: str) -> str:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(raw or ""))
    return m.group(0) if m else ""


def _same_commitment(a: dict[str, Any], b: dict[str, Any]) -> bool:
    from assistant.lib.summary_normalize import tasks_duplicate

    ta = {"task": str(a.get("text") or ""), "assignee": str(a.get("who") or ""), "quote": "", "speaker": "", "assignee_basis": ""}
    tb = {"task": str(b.get("text") or ""), "assignee": str(b.get("who") or ""), "quote": "", "speaker": "", "assignee_basis": ""}
    if not ta["task"] or not tb["task"]:
        return False
    for t in (ta, tb):
        if t["assignee"].lower() in _ME_WORDS or t["assignee"].lower().startswith(("спикер", "speaker")):
            t["assignee"] = ""
    return tasks_duplicate(ta, tb)


def _owner_names(owner_user_id: Any) -> list[str]:
    try:
        from assistant.lib.meeting_context import _owner

        name, aliases = _owner(int(owner_user_id))
        return [n for n in [name, *aliases] if n]
    except Exception:
        return []


def ingest(owner_user_id: Any, cards: list[dict[str, Any]], now: Optional[datetime] = None) -> int:
    """Записать обязательства из карточек ленты; статус и правки пользователя не трогаем."""
    now = now or _now()
    owner = str(owner_user_id)
    names = _owner_names(owner_user_id)
    existing = {str(r["item_key"]): r for r in rows(owner)}
    recent = [r for r in existing.values() if str(r.get("created_at") or "") >= (now - _DEDUP_WINDOW).isoformat()
              and r.get("commitment_id")]
    n = 0
    ts = now.isoformat()
    with _LOCK:
        conn = _conn()
        for card in cards:
            if card.get("type") not in ("summary_tasks", "agreement", "chat_promise", "mail_promise"):
                continue
            for it in item_from_card(card):
                key = str(it.get("key") or "").strip()[:300]
                kind = "agreement" if it.get("kind") == "agreement" else str(it.get("kind") or "")
                if not key or kind not in COMMITMENT_KINDS:
                    continue
                cur = existing.get(key)
                direction = direction_for(it, names)
                quote = str(it.get("quote") or "")[:300]
                due_iso = _due_iso(it.get("due") or "")
                if cur and cur.get("commitment_id"):
                    conn.execute(
                        "UPDATE focus_items SET seen_at = ?, direction = ?, quote = CASE WHEN quote = '' THEN ? ELSE quote END,"
                        " due_iso = CASE WHEN due_iso = '' THEN ? ELSE due_iso END WHERE owner_user_id = ? AND item_key = ?",
                        (ts, direction, quote, due_iso, owner, key),
                    )
                    continue
                src_id = str((it.get("source") or {}).get("id") or "")
                twin = next(
                    (r for r in recent if str((r.get("source") or {}).get("id") or "") != src_id and _same_commitment(r, it)),
                    None,
                )
                cid = str(twin["commitment_id"]) if twin else "c_" + hashlib.sha1(f"{owner}:{key}".encode()).hexdigest()[:12]
                if cur:  # пункт уже разбирали руками — дописываем реестровые поля
                    conn.execute(
                        "UPDATE focus_items SET commitment_id = ?, direction = ?, quote = ?, due_iso = ?, seen_at = ?"
                        " WHERE owner_user_id = ? AND item_key = ?",
                        (cid, direction, quote, due_iso, ts, owner, key),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO focus_items (owner_user_id, item_key, card_key, source_json, kind, text, who, due, type,
                            created_at, updated_at, direction, commitment_id, quote, due_iso, seen_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (owner, key, str(it.get("card_key") or key)[:300], json.dumps(it.get("source") or {}, ensure_ascii=False),
                         kind[:40], str(it.get("text") or "").strip()[:400], str(it.get("who") or "").strip()[:120],
                         str(it.get("due") or "").strip()[:40], str(it.get("type") or "task")[:20], ts, ts,
                         direction, cid, quote, due_iso, ts),
                    )
                row = {"item_key": key, "commitment_id": cid, "text": it.get("text"), "who": it.get("who"),
                       "source": it.get("source") or {}, "created_at": ts}
                recent.append(row)
                existing[key] = row
                n += 1
        conn.commit()
    return n


def duplicate_keys(owner_user_id: Any, now: Optional[datetime] = None) -> set[str]:
    """Пункты, чья договорённость уже закрыта или показана в другом источнике (раньше записанном)."""
    now = now or _now()
    by_cid: dict[str, list[dict[str, Any]]] = {}
    for r in rows(owner_user_id):
        if r.get("commitment_id"):
            by_cid.setdefault(str(r["commitment_id"]), []).append(r)
    out: set[str] = set()
    for group in by_cid.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: str(r.get("created_at") or ""))
        closed = any(str(r.get("status") or "") in CLOSED for r in group)
        for i, r in enumerate(group):
            if closed and not is_hidden(r, now):
                out.add(str(r["item_key"]))  # закрыли в одном месте — не показываем в другом
            elif not closed and i > 0:
                out.add(str(r["item_key"]))  # первая запись — основная, остальные дубли
    return out


def commitments(
    owner_user_id: Any, *, direction: str = "", status: str = "open", now: Optional[datetime] = None, limit: int = 100,
) -> list[dict[str, Any]]:
    """Обязательства, по одному на commitment_id: текст, кто, срок, статус, источники, цитата."""
    now = now or _now()
    today = now.date().isoformat()
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows(owner_user_id):
        if r.get("commitment_id") and r.get("kind") in COMMITMENT_KINDS:
            groups.setdefault(str(r["commitment_id"]), []).append(r)
    out = []
    for cid, group in groups.items():
        group.sort(key=lambda r: str(r.get("created_at") or ""))
        main = group[0]
        closed_row = next((r for r in group if str(r.get("status") or "") in CLOSED), None)
        snoozed = all(is_hidden(r, now) and str(r.get("status")) == "snoozed" for r in group)
        p = public(closed_row or main, now)
        st = "closed" if closed_row else ("snoozed" if snoozed else "open")
        due_iso = next((str(r.get("due_iso")) for r in group if r.get("due_iso")), "") or _due_iso(p["deadline"])
        if st == "open" and due_iso and due_iso < today:
            st = "overdue"
        d = str(main.get("direction") or "unknown")
        if direction and d != direction:
            continue
        if status == "open" and st not in ("open", "overdue"):
            continue
        if status in ("overdue", "closed", "snoozed") and st != status:
            continue
        out.append({
            "id": cid, "text": p["text"], "who": p["assignee"], "direction": d, "status": st,
            "closed_as": p["status"] if closed_row else "", "deadline": p["deadline"], "due_iso": due_iso,
            "quote": next((str(r.get("quote")) for r in group if r.get("quote")), ""),
            "link": p["link"], "created_at": str(main.get("created_at") or ""),
            "updated_at": max(str(r.get("updated_at") or "") for r in group),
            "sources": [public(r, now)["source"] for r in group],
            "keys": [str(r["item_key"]) for r in group],
        })
    out.sort(key=lambda c: (c["status"] != "overdue", c["due_iso"] or "9999", c["created_at"]))
    return out[:limit]
