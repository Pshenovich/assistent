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

# Пункт ушёл из Фокуса насовсем.
CLOSED = ("completed", "linked", "converted", "dismissed", "retyped")
STATUSES = CLOSED + ("snoozed", "new")
REASONS = ("not_a_task", "not_relevant", "duplicate", "recognition_error")
TYPES = ("task", "promise", "decision", "reminder", "thought")
# Тип, после которого пункт остаётся в Фокусе (обещание — тоже дело).
ACTIVE_TYPES = ("task", "promise")
FIELDS = ("assignee", "task", "deadline", "state", "link", "comment")
TITLES_WINDOW = timedelta(days=90)

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
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
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
        if kw.get("task_id"):
            row["link"] = {**(row.get("link") or {}), "task_id": int(kw["task_id"])}
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
    try:
        sync_source(owner_user_id, saved)
    except Exception as e:  # отметка в исходнике — бонус, не повод провалить действие
        print(f"[focus_items] source sync failed uid={owner_user_id} key={item_key} err={e!r}")
    return saved


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


def item_from_card(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Пункты карточки Фокуса в виде снимков для remember()."""
    src: dict[str, Any] = {}
    if card.get("note"):
        src = {**card["note"], "kind": "note", "note_kind": card["note"].get("kind") or "note"}
    elif card.get("chat"):
        src = {**card["chat"], "kind": "chat"}
    src["label"] = str(card.get("source") or "")[:200]
    out = []
    if card.get("type") == "summary_tasks":
        for i in card.get("items") or []:
            parts = [p.strip() for p in str(i.get("text") or "").split(" — ")]
            who, task, due = (parts + ["", "", ""])[:3] if len(parts) >= 3 else (
                (parts[0], parts[1], "") if len(parts) == 2 else ("", parts[0] if parts else "", "")
            )
            out.append({
                "key": i.get("id"), "card_key": card.get("key"), "kind": "summary_task",
                "text": task or i.get("text"), "who": who, "due": due, "source": src, "type": "task",
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
        "source": src,
        "type": "promise" if str(card.get("type") or "") == "chat_promise" else "task",
    })
    return out


def public(row: dict[str, Any]) -> dict[str, Any]:
    f = row.get("fields") or {}
    src = row.get("source") or {}
    return {
        "key": row["item_key"],
        "card_key": row.get("card_key") or row["item_key"],
        "kind": row.get("kind") or "",
        "type": row.get("type") or "task",
        "status": row.get("status") or "new",
        "reason": row.get("reason") or "",
        "snooze_until": row.get("snooze_until") or "",
        "text": f.get("task") or row.get("text") or "",
        "assignee": f.get("assignee") or row.get("who") or "",
        "deadline": f.get("deadline") or row.get("due") or "",
        "state": f.get("state") or "",
        "comment": f.get("comment") or "",
        "link": row.get("link") or {},
        "source": {k: src.get(k) for k in ("kind", "note_kind", "id", "title", "label", "date") if src.get(k) is not None},
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


def marks_to_text(html_text: str) -> str:
    """Для ИИ: значок → «[выполнено: Вова, 20.10]», чтобы по документу было видно, что сделано."""

    def repl(m: re.Match[str]) -> str:
        a = _mark_attrs(m.group(0))
        st = {"done": "выполнено", "linked": "связано с задачей", "snoozed": "отложено"}.get(a.get("status", ""), "отмечено")
        bits = [x for x in (a.get("by"), _short_date(a.get("at", ""))) if x]
        url = f" {a['url']}" if a.get("url") else ""
        return f" [{st}{': ' + ', '.join(bits) if bits else ''}{url}]"

    return MARK_RE.sub(repl, str(html_text or ""))


def mark_html(row: dict[str, Any]) -> str:
    st = {"completed": "done", "linked": "linked", "converted": "done"}.get(str(row.get("status") or ""), "")
    if not st:
        return ""
    attrs = {
        "": st,
        "item": mark_id(row["item_key"]),
        "by": str(row.get("by_name") or ""),
        "at": str(row.get("updated_at") or ""),
    }
    url = str((row.get("link") or {}).get("url") or "")
    if url:
        attrs["url"] = url
    task_id = (row.get("link") or {}).get("task_id")
    if task_id:
        attrs["task"] = str(task_id)
    a = " ".join(f'data-leo-mark{"-" + k if k else ""}="{_h.escape(v, quote=True)}"' for k, v in attrs.items())
    label = "✓" if st == "done" else "Задача"
    return f'<span class="note-focus-mark" {a} contenteditable="false">{label}</span>'


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


def place_mark(html_text: str, row: dict[str, Any]) -> tuple[str, bool]:
    """Снять старый значок пункта и поставить новый в конец самой похожей строки."""
    mid = mark_id(row["item_key"])
    src = re.sub(
        r'<span[^>]*class="note-focus-mark"[^>]*data-leo-mark-item="%s"[^>]*>.*?</span>' % mid, "", str(html_text or ""), flags=re.S
    )
    mark = mark_html(row)
    if not mark:
        return src, src != html_text
    p = public(row)
    want = " ".join(x for x in (p["assignee"], row.get("text") or p["text"]) if x)
    parser = _Blocks(src)
    try:
        parser.feed(src)
        parser.close()
    except Exception:
        return src, src != html_text
    best, best_score = None, 0.0
    for b in parser.blocks:
        s = _score(want, b["text"])
        if s > best_score:
            best, best_score = b, s
    if not best or best_score < 0.5:
        return src, src != html_text
    at = int(best["at"])
    return src[:at].rstrip() + " " + mark + src[at:], True


def sync_source(owner_user_id: Any, row: dict[str, Any]) -> None:
    """Отметить пункт в исходнике: значок в тексте + задача в meta саммари (выполнена, ссылка, правки)."""
    src = row.get("source") or {}
    if src.get("kind") != "note" or not src.get("id"):
        return  # пункты из чатов: исходник — переписка в Telegram, её не трогаем
    from assistant.stores import notes as notes_store

    note = notes_store.get_note(owner_user_id, int(src["id"]))
    if not note or str(note.get("owner_user_id") or note.get("user_id") or owner_user_id) != str(owner_user_id):
        return
    body = str(note.get("body") or "")
    new_body, changed = place_mark(body, row)
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    meta_changed = _sync_meta_task(meta, row)
    if changed or meta_changed:
        notes_store.update_note(
            owner_user_id,
            int(src["id"]),
            body=new_body if changed else None,
            meta=meta if meta_changed else None,
        )


def _sync_meta_task(meta: dict[str, Any], row: dict[str, Any]) -> bool:
    tasks = meta.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return False
    p = public(row)
    want = " ".join(x for x in (row.get("who"), row.get("text")) if x)
    best_i, best_s = -1, 0.0
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        s = _score(want, f"{t.get('assignee') or ''} {t.get('task') or ''}")
        if s > best_s:
            best_i, best_s = i, s
    if best_i < 0 or best_s < 0.5:
        return False
    t = dict(tasks[best_i])
    before = json.dumps(t, ensure_ascii=False, sort_keys=True)
    f = row.get("fields") or {}
    if f.get("assignee"):
        t["assignee"] = f["assignee"]
    if f.get("task"):
        t["task"] = f["task"]
    if f.get("deadline"):
        t["deadline"] = f["deadline"]
    if f.get("comment"):
        t["comment"] = f["comment"]
    st = row.get("status")
    t["completed"] = st in ("completed", "converted")
    t["focus_status"] = st
    if st in ("completed", "converted", "linked"):
        t["completed_by"] = row.get("by_name") or ""
        t["completed_at"] = row.get("updated_at") or ""
    else:
        t.pop("completed_by", None)
        t.pop("completed_at", None)
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
