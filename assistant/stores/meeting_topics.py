"""Темы встреч: какие названия событий и Telegram-чаты считаются одной темой («БГПТ»).

Бриф к встрече берёт контекст по всей теме: итоги прошлых встреч с похожими названиями
и переписку в связанных чатах. Совпадение нормализованного названия («БГПТ» = «бгпт»)
считается той же серией без вопроса; совпадение по слову («Штурм по БГПТ», чат
«Бустра ГПТ») — только после «Связать» в Фокусе.

Тема общая: её видит автор и все, кто состоит в связанном чате. Тема — только правило
(«эти названия и этот чат — одно»), данные каждый берёт свои: свои заметки встреч, чат —
если сам в нём. Правки:
- пункт темы (meeting_topic_members.status): on / off — «у всех»;
- личное выключение пункта (meeting_topic_optouts) — «у меня»;
- личное «не то» для кандидата (meeting_topic_rejects) — больше не предлагать мне;
- история (meeting_topic_history) — кто что связал и убрал.

Члены темы: kind='title' (value — norm_title), kind='chat' (value — chat_id).
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None

# Служебные слова в названиях встреч: по ним темы не связываем.
STOP = {
    "встреча", "встречи", "встречу", "синк", "созвон", "звонок", "демо", "обсуждение", "обсудить", "штурм",
    "планерка", "планирование", "статус", "ретро", "ретроспектива", "груминг", "дейли", "daily",
    "weekly", "sync", "meeting", "call", "demo", "review", "ревью", "презентация", "интервью", "собеседование",
    "задачи", "задач", "ближайших", "ближайшие", "по", "про", "для", "and", "the", "with", "и", "с", "на",
    "в", "о", "об", "от", "к", "релиз", "релизов", "команда", "команды", "общий", "общая", "еженедельная",
    "еженедельный", "итоги", "итог", "план", "проект", "проекта", "новый", "новая", "запуск", "кофе", "обед",
    "чат", "chat", "chats", "группа", "gmail", "com", "mail", "yandex",
}

_CYR_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}


def _lat(word: str) -> str:
    return "".join(_CYR_LAT.get(ch, ch) for ch in word)


def _words(text: str) -> list[str]:
    """Слова названия: нижний регистр, ё→е, «Б-ГПТ» → «бгпт»."""
    t = (text or "").lower().replace("ё", "е")
    return [w.replace("-", "") for w in re.findall(r"[a-zа-я0-9]+(?:-[a-zа-я0-9]+)*", t)]


def norm_title(text: str) -> str:
    """Название целиком для «той же серии»: «БГПТ», «бгпт», «BGPT» → «bgpt»."""
    return " ".join(_lat(w) for w in _words(text) if w)


def keys(text: str) -> set[str]:
    """Отличительные слова названия (в латинице) + сокращения вида «Бустра ГПТ» → «bgpt»."""
    words = _words(text)
    out: set[str] = set()
    for i, w in enumerate(words):
        if w not in STOP and len(w) >= 3 and not w.isdigit():
            out.add(_lat(w))
        # «Бустра ГПТ» — так же пишут «БГПТ»: первая буква слова + короткое слово-аббревиатура.
        if i + 1 < len(words):
            nxt = words[i + 1]
            if w not in STOP and 2 <= len(nxt) <= 4 and nxt not in STOP and not w.isdigit():
                out.add(_lat(w[0] + nxt))
    return out


def strong_keys(text: str) -> set[str]:
    """Сильные слова: аббревиатуры («БГПТ», «CRM») и сокращения из пары слов («Бустра ГПТ» → bgpt)."""
    out: set[str] = set()
    raw = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", text or "")
    for w in raw:
        letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", w)
        if len(letters) >= 2 and letters.isupper():
            out.add(_lat(w.lower().replace("ё", "е").replace("-", "")))
    words = _words(text)
    for i in range(len(words) - 1):
        w, nxt = words[i], words[i + 1]
        if w not in STOP and 2 <= len(nxt) <= 4 and nxt not in STOP and not w.isdigit():
            out.add(_lat(w[0] + nxt))
    return out


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    from assistant.stores import notes as notes_store

    path = str(notes_store._db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.executescript(
        """
        CREATE TABLE IF NOT EXISTS meeting_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_topic_members (
            topic_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'on',
            by_user_id TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (topic_id, kind, value)
        );
        CREATE INDEX IF NOT EXISTS idx_mtm_value ON meeting_topic_members(kind, value);
        CREATE TABLE IF NOT EXISTS meeting_topic_optouts (
            user_id TEXT NOT NULL,
            topic_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, topic_id, kind, value)
        );
        CREATE TABLE IF NOT EXISTS meeting_topic_rejects (
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, kind, value)
        );
        CREATE TABLE IF NOT EXISTS meeting_topic_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            by_name TEXT NOT NULL DEFAULT '',
            op TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'all',
            kind TEXT NOT NULL DEFAULT '',
            value TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mth_topic ON meeting_topic_history(topic_id, id);
        """
    )
    _CONN.commit()
    return _CONN


def member_value(kind: str, value: Any) -> str:
    return norm_title(str(value)) if kind == "title" else str(int(value))


def _items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i in items:
        if i.get("kind") not in ("title", "chat") or i.get("value") in (None, ""):
            continue
        out.append({"kind": i["kind"], "value": member_value(i["kind"], i["value"]), "label": str(i.get("label") or "")})
    return out


def _user_chat_ids(user_id: int) -> set[str]:
    try:
        from assistant.stores import chat_digest

        return {str(int(c["chat_id"])) for c in chat_digest.list_chats_for_user(int(user_id))}
    except Exception:
        return set()


def visible_topic_ids(user_id: int, *, chat_ids: Optional[set[str]] = None) -> list[int]:
    """Темы, которые видит пользователь: свои и те, где связан чат, в котором он состоит."""
    uid = str(int(user_id))
    chats = _user_chat_ids(user_id) if chat_ids is None else chat_ids
    with _LOCK:
        conn = _conn()
        ids = {int(r[0]) for r in conn.execute("SELECT id FROM meeting_topics WHERE owner_user_id = ?", (uid,))}
        if chats:
            ph = ",".join("?" for _ in chats)
            ids |= {int(r[0]) for r in conn.execute(
                f"SELECT DISTINCT topic_id FROM meeting_topic_members WHERE kind = 'chat' AND status = 'on' "
                f"AND value IN ({ph})", tuple(chats))}
    return sorted(ids)


def get_topic(topic_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute("SELECT * FROM meeting_topics WHERE id = ?", (int(topic_id),)).fetchone()
    return dict(r) if r else None


def topic_view(user_id: int, topic_id: int, *, chat_ids: Optional[set[str]] = None) -> dict[str, Any]:
    """Тема глазами пользователя: пункты со статусом «у всех» и «у меня»; чужие чаты — без доступа."""
    uid = str(int(user_id))
    chats = _user_chat_ids(user_id) if chat_ids is None else chat_ids
    with _LOCK:
        conn = _conn()
        t = conn.execute("SELECT * FROM meeting_topics WHERE id = ?", (int(topic_id),)).fetchone()
        rows = conn.execute(
            "SELECT kind, value, label, status, by_user_id, updated_at FROM meeting_topic_members WHERE topic_id = ? "
            "ORDER BY kind DESC, updated_at", (int(topic_id),)).fetchall()
        offs = {(r["kind"], r["value"]) for r in conn.execute(
            "SELECT kind, value FROM meeting_topic_optouts WHERE user_id = ? AND topic_id = ?", (uid, int(topic_id)))}
    items = []
    for r in rows:
        it = dict(r)
        it["mine_off"] = (r["kind"], r["value"]) in offs
        it["access"] = r["kind"] != "chat" or r["value"] in chats
        it["active"] = r["status"] == "on" and not it["mine_off"] and it["access"]
        items.append(it)
    return {
        "id": int(topic_id),
        "name": (t["name"] if t else "") or "",
        "owner_user_id": (t["owner_user_id"] if t else "") or "",
        "is_owner": bool(t) and str(t["owner_user_id"]) == uid,
        "created_at": t["created_at"] if t else "",
        "items": items,
    }


def topics_for_user(user_id: int) -> list[dict[str, Any]]:
    chats = _user_chat_ids(user_id)
    return [topic_view(user_id, tid, chat_ids=chats) for tid in visible_topic_ids(user_id, chat_ids=chats)]


def topic_for_title(user_id: int, title: str) -> Optional[dict[str, Any]]:
    """Тема, в которой это название встречи активно у пользователя."""
    n = norm_title(title)
    if not n:
        return None
    for t in topics_for_user(user_id):
        if any(i["kind"] == "title" and i["value"] == n and i["active"] for i in t["items"]):
            return t
    return None


def decided(user_id: int) -> set[tuple[str, str]]:
    """Что уже есть в видимых темах (в любом статусе) или отклонено лично — повторно не предлагаем."""
    out: set[tuple[str, str]] = set()
    for t in topics_for_user(user_id):
        out |= {(i["kind"], i["value"]) for i in t["items"]}
    with _LOCK:
        out |= {(r["kind"], r["value"]) for r in _conn().execute(
            "SELECT kind, value FROM meeting_topic_rejects WHERE user_id = ?", (str(int(user_id)),))}
    return out


def _log(conn, topic_id: int, user_id: int, op: str, scope: str, it: dict[str, Any], by_name: str = "") -> None:
    conn.execute(
        "INSERT INTO meeting_topic_history(topic_id, user_id, by_name, op, scope, kind, value, label, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(topic_id), str(int(user_id)), by_name or "", op, scope, it.get("kind", ""), it.get("value", ""),
         it.get("label", ""), _now()),
    )


def history(topic_id: int, *, limit: int = 30) -> list[dict[str, Any]]:
    with _LOCK:
        rs = _conn().execute(
            "SELECT user_id, by_name, op, scope, kind, value, label, created_at FROM meeting_topic_history WHERE topic_id = ? "
            "ORDER BY id DESC LIMIT ?", (int(topic_id), int(limit))).fetchall()
    return [dict(r) for r in rs]


def confirm(user_id: int, items: Iterable[dict[str, Any]], name: str = "", topic_id: Optional[int] = None,
            by_name: str = "") -> dict[str, Any]:
    """«Связать»: пункты включаются у всех. Если кто-то из них уже в видимой теме — добавляем к ней.

    Возвращает {topic_id, created, prev: [{kind, value, status|None}]} — для «Вернуть».
    """
    uid = str(int(user_id))
    items = _items(items)
    with _LOCK:
        conn = _conn()
        tid = int(topic_id) if topic_id else None
        if not tid:
            visible = visible_topic_ids(int(user_id))
            if visible and items:
                ph = ",".join("?" for _ in visible)
                for i in items:
                    r = conn.execute(
                        f"SELECT topic_id FROM meeting_topic_members WHERE kind = ? AND value = ? AND status = 'on' "
                        f"AND topic_id IN ({ph})", (i["kind"], i["value"], *visible)).fetchone()
                    if r:
                        tid = int(r[0])
                        break
        created = False
        if not tid:
            tid = int(conn.execute(
                "INSERT INTO meeting_topics(owner_user_id, name, created_at) VALUES (?, ?, ?)", (uid, name, _now())
            ).lastrowid)
            created = True
        prev = []
        for i in items:
            r = conn.execute("SELECT status FROM meeting_topic_members WHERE topic_id = ? AND kind = ? AND value = ?",
                             (tid, i["kind"], i["value"])).fetchone()
            prev.append({"kind": i["kind"], "value": i["value"], "status": r["status"] if r else None})
            conn.execute(
                "INSERT INTO meeting_topic_members(topic_id, kind, value, label, status, by_user_id, updated_at) "
                "VALUES (?, ?, ?, ?, 'on', ?, ?) ON CONFLICT(topic_id, kind, value) DO UPDATE SET "
                "status = 'on', label = CASE WHEN excluded.label != '' THEN excluded.label ELSE label END, "
                "by_user_id = excluded.by_user_id, updated_at = excluded.updated_at",
                (tid, i["kind"], i["value"], i["label"], uid, _now()),
            )
            conn.execute("DELETE FROM meeting_topic_optouts WHERE user_id = ? AND topic_id = ? AND kind = ? AND value = ?",
                         (uid, tid, i["kind"], i["value"]))
            conn.execute("DELETE FROM meeting_topic_rejects WHERE user_id = ? AND kind = ? AND value = ?",
                         (uid, i["kind"], i["value"]))
            _log(conn, tid, int(user_id), "link", "all", i, by_name)
        conn.commit()
    return {"topic_id": tid, "created": created, "prev": prev}


def reject(user_id: int, items: Iterable[dict[str, Any]]) -> None:
    """«Не то» по кандидатам: больше не предлагать этому пользователю."""
    uid = str(int(user_id))
    with _LOCK:
        conn = _conn()
        for i in _items(items):
            conn.execute(
                "INSERT INTO meeting_topic_rejects(user_id, kind, value, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, kind, value) DO NOTHING", (uid, i["kind"], i["value"], _now()))
        conn.commit()


def undo(user_id: int, token: dict[str, Any], rejected: Iterable[dict[str, Any]] = (), by_name: str = "") -> None:
    """«Вернуть» после карточки: откатить связь (и личные «не то» этой же карточки)."""
    uid = str(int(user_id))
    with _LOCK:
        conn = _conn()
        tid = int(token.get("topic_id") or 0)
        if tid:
            for p in token.get("prev") or []:
                if p.get("status") is None:
                    conn.execute("DELETE FROM meeting_topic_members WHERE topic_id = ? AND kind = ? AND value = ?",
                                 (tid, p["kind"], p["value"]))
                else:
                    conn.execute("UPDATE meeting_topic_members SET status = ? WHERE topic_id = ? AND kind = ? AND value = ?",
                                 (p["status"], tid, p["kind"], p["value"]))
                _log(conn, tid, int(user_id), "undo", "all", p, by_name)
            left = conn.execute("SELECT COUNT(*) FROM meeting_topic_members WHERE topic_id = ?", (tid,)).fetchone()[0]
            if token.get("created") and not left:
                conn.execute("DELETE FROM meeting_topics WHERE id = ?", (tid,))
                conn.execute("DELETE FROM meeting_topic_history WHERE topic_id = ?", (tid,))
        for i in _items(rejected):
            conn.execute("DELETE FROM meeting_topic_rejects WHERE user_id = ? AND kind = ? AND value = ?",
                         (uid, i["kind"], i["value"]))
        conn.commit()


def set_item(user_id: int, topic_id: int, kind: str, value: Any, *, on: bool, scope: str, by_name: str = "") -> None:
    """Переключатель на экране темы: scope='me' — только у себя, 'all' — у всех."""
    uid = str(int(user_id))
    v = member_value(kind, value)
    with _LOCK:
        conn = _conn()
        r = conn.execute("SELECT label FROM meeting_topic_members WHERE topic_id = ? AND kind = ? AND value = ?",
                         (int(topic_id), kind, v)).fetchone()
        if not r:
            raise KeyError("item")
        it = {"kind": kind, "value": v, "label": r["label"]}
        if scope == "all":
            conn.execute("UPDATE meeting_topic_members SET status = ?, by_user_id = ?, updated_at = ? "
                         "WHERE topic_id = ? AND kind = ? AND value = ?",
                         ("on" if on else "off", uid, _now(), int(topic_id), kind, v))
            if on:
                conn.execute("DELETE FROM meeting_topic_optouts WHERE user_id = ? AND topic_id = ? AND kind = ? AND value = ?",
                             (uid, int(topic_id), kind, v))
        elif on:
            conn.execute("DELETE FROM meeting_topic_optouts WHERE user_id = ? AND topic_id = ? AND kind = ? AND value = ?",
                         (uid, int(topic_id), kind, v))
        else:
            conn.execute("INSERT INTO meeting_topic_optouts(user_id, topic_id, kind, value, created_at) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT DO NOTHING", (uid, int(topic_id), kind, v, _now()))
        _log(conn, int(topic_id), int(user_id), "on" if on else "off", scope, it, by_name)
        conn.commit()


def rename(topic_id: int, name: str) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute("UPDATE meeting_topics SET name = ? WHERE id = ?", (str(name or "").strip()[:80], int(topic_id)))
        conn.commit()
