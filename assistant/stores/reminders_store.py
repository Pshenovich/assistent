"""Хранение напоминаний бота. Общий модуль для bot.py и usage_server (мини-приложение).

Напоминания лежат в SQLite (data/reminders.sqlite), каждое — отдельной строкой.
Раньше это был один reminders.json, который бот и веб переписывали целиком и
могли затереть правки друг друга. Теперь каждая правка — своя транзакция
BEGIN IMMEDIATE, поэтому два процесса не теряют изменения.

При первом открытии старый reminders.json один раз переносится в базу (флаг в
meta), сам файл не трогается. Откат на старый код:
scripts/reminders_export_json.py выгружает базу обратно в JSON.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from assistant.lib import sqlite_conn

_lock = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: Path | None = None

_IMPORT_FLAG = "imported_from_json"


def parse_when_iso(
    when_iso: Any,
    *,
    tz: ZoneInfo | None = None,
) -> datetime | None:
    raw = str(when_iso or "").strip()
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        from assistant.config import CALENDAR_TZ

        when = when.replace(tzinfo=tz or ZoneInfo(CALENDAR_TZ))
    return when
def store_path() -> Path:
    """Старый reminders.json: источник однократного переноса в базу."""
    from assistant.config import ROOT

    raw = os.getenv("REMINDERS_STORE_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        return p
    return ROOT / "reminders.json"


def db_path() -> Path:
    from assistant.config import ROOT

    raw = os.getenv("REMINDERS_DB_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (ROOT / p).resolve()
    legacy = store_path()
    # JSON не на штатном месте (тесты, временный каталог) — база рядом с ним.
    # Штатный ./reminders.json → data/: корень сайта деплой перезаписывает,
    # а data/ не трогает и бэкапит.
    if legacy.resolve() != (ROOT / "reminders.json").resolve():
        return legacy.with_suffix(".sqlite")
    return ROOT / "data" / "reminders.sqlite"


def _uid(item: dict[str, Any]) -> int:
    try:
        return int(item.get("user_id") or 0)
    except (TypeError, ValueError):
        return 0


def _rid(item: dict[str, Any]) -> str:
    return str(item.get("id") or "").strip()


def _load_legacy_json(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if raw.strip() else []
    return data if isinstance(data, list) else []


def _insert_rows(conn: sqlite3.Connection, items: list[Any], *, start_seq: int) -> int:
    """Вставляет записи по порядку. Запись без id получает служебный ключ, сама не меняется."""
    n = 0
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        seq = start_seq + i
        key = _rid(it) or f"_noid_{seq}"
        cur = conn.execute(
            "INSERT OR IGNORE INTO reminders(id, user_id, seq, data) VALUES (?, ?, ?, ?)",
            (key, _uid(it), seq, json.dumps(it, ensure_ascii=False)),
        )
        if cur.rowcount:
            n += 1
        else:
            print(f"[reminders_store] duplicate id skipped id={key!r}")
    return n


def _import_legacy_once(conn: sqlite3.Connection) -> None:
    done = conn.execute("SELECT value FROM meta WHERE key = ?", (_IMPORT_FLAG,)).fetchone()
    if done:
        return
    src = store_path()
    try:
        items = _load_legacy_json(src)
    except Exception as e:
        # Флаг не ставим: перенос повторится при следующем открытии, хранилище работает.
        print(f"[reminders_store] legacy json unreadable path={src} err={e!r}")
        return
    n = _insert_rows(conn, items, start_seq=1)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        (_IMPORT_FLAG, json.dumps({"from": str(src), "in_json": len(items), "imported": n, "ts": time.time()})),
    )
    print(f"[reminders_store] imported from {src}: json={len(items)} imported={n}")


def _conn() -> sqlite3.Connection:
    """Соединение с базой текущего пути; вызывать под _lock."""
    global _CONN, _CONN_PATH
    path = db_path()
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    if _CONN is not None:
        _CONN.close()
        _CONN = None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_conn.connect(path)
    # Транзакции ведём сами (BEGIN IMMEDIATE), без неявных BEGIN модуля sqlite3.
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL DEFAULT 0,
                seq INTEGER NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        _import_legacy_once(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        conn.close()
        raise
    _CONN, _CONN_PATH = conn, path
    return conn


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    """Транзакция записи: другой процесс ждёт её конца, а не затирает её."""
    with _lock:
        conn = _conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def _rows(sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _lock:
        rows = _conn().execute(sql, args).fetchall()
    return [json.loads(r[0]) for r in rows]


def load_reminders() -> list[dict[str, Any]]:
    try:
        return _rows("SELECT data FROM reminders ORDER BY seq")
    except Exception as e:
        print(f"[reminders_store] load_failed err={e!r}")
        return []


def save_reminders(items: list[dict[str, Any]]) -> None:
    """Заменяет весь список (только для локальных фикстур)."""
    with _tx() as conn:
        conn.execute("DELETE FROM reminders")
        _insert_rows(conn, list(items), start_seq=1)


def _update(reminder_id: str, fn: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
    """Читает запись, правит её fn и сохраняет — в одной транзакции."""
    rid = (reminder_id or "").strip()
    if not rid:
        return None
    with _tx() as conn:
        row = conn.execute("SELECT data FROM reminders WHERE id = ?", (rid,)).fetchone()
        if row is None:
            return None
        item = json.loads(row[0])
        fn(item)
        conn.execute(
            "UPDATE reminders SET data = ?, user_id = ? WHERE id = ?",
            (json.dumps(item, ensure_ascii=False), _uid(item), rid),
        )
    return item


def get_reminder_by_id(reminder_id: str) -> dict[str, Any] | None:
    rid = (reminder_id or "").strip()
    if not rid:
        return None
    rows = _rows("SELECT data FROM reminders WHERE id = ?", (rid,))
    return rows[0] if rows else None


def find_reminder_by_message(chat_id: int, message_id: int) -> dict[str, Any] | None:
    for it in load_reminders():
        if not isinstance(it, dict):
            continue
        if int(it.get("chat_id") or 0) != int(chat_id):
            continue
        if int(it.get("reminder_message_id") or 0) == int(message_id):
            return it
    return None


def remove_reminder(reminder_id: str) -> None:
    rid = (reminder_id or "").strip()
    if not rid:
        return
    with _tx() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (rid,))


def set_reminder_message_id(reminder_id: str, message_id: int) -> None:
    def fn(it: dict[str, Any]) -> None:
        it["reminder_message_id"] = int(message_id)

    _update(reminder_id, fn)


def update_reminder_when(reminder_id: str, new_when_iso: str) -> dict[str, Any] | None:
    def fn(it: dict[str, Any]) -> None:
        it["when_iso"] = new_when_iso

    return _update(reminder_id, fn)


def _normalize_checklist(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        rid = str(it.get("id") or "").strip()
        if not rid:
            import uuid

            rid = "c" + uuid.uuid4().hex[:10]
        out.append(
            {
                "id": rid,
                "text": text,
                "done": bool(it.get("done")),
            }
        )
    return out


def patch_reminder(
    reminder_id: str,
    *,
    task: str | None = None,
    when_iso: str | None = None,
    done: bool | None = None,
    checklist: list[dict[str, Any]] | None = None,
    recurrence: str | None = None,
) -> dict[str, Any] | None:
    """Частичное обновление записи. Возвращает обновлённый dict или None.

    recurrence: None — не трогать, "" — убрать повтор, иначе RRULE.
    """

    def fn(it: dict[str, Any]) -> None:
        if task is not None:
            it["task"] = str(task).strip()
        if when_iso is not None:
            it["when_iso"] = str(when_iso).strip()
        if done is not None:
            if done:
                it["done"] = True
            else:
                it.pop("done", None)
        if checklist is not None:
            it["checklist"] = _normalize_checklist(checklist)
        if recurrence is not None:
            rule = str(recurrence).strip()
            if rule:
                it["recurrence"] = rule if rule.upper().startswith("RRULE:") else "RRULE:" + rule
            else:
                it.pop("recurrence", None)

    return _update(reminder_id, fn)


def add_reminder(
    *,
    user_id: int,
    chat_id: int,
    task: str,
    when_iso: str,
    checklist: list[dict[str, Any]] | None = None,
    recurrence: str = "",
) -> str:
    import uuid

    rid = uuid.uuid4().hex[:12]
    row = {
        "id": rid,
        "user_id": int(user_id),
        "chat_id": int(chat_id),
        "task": (task or "").strip(),
        "when_iso": when_iso,
        "done": False,
    }
    rrule = str(recurrence or "").strip()
    if rrule:
        if not rrule.upper().startswith("RRULE:"):
            rrule = "RRULE:" + rrule
        row["recurrence"] = rrule
    normalized_checklist = _normalize_checklist(checklist)
    if normalized_checklist:
        row["checklist"] = normalized_checklist
    with _tx() as conn:
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM reminders").fetchone()[0]
        conn.execute(
            "INSERT INTO reminders(id, user_id, seq, data) VALUES (?, ?, ?, ?)",
            (rid, int(user_id), seq, json.dumps(row, ensure_ascii=False)),
        )
    return rid


def clear_reminder_message_id(reminder_id: str) -> None:
    def fn(it: dict[str, Any]) -> None:
        it.pop("reminder_message_id", None)

    _update(reminder_id, fn)


def reminders_for_user(telegram_user_id: int) -> list[dict[str, Any]]:
    """Все напоминания пользователя (включая выполненные)."""
    return _rows(
        "SELECT data FROM reminders WHERE user_id = ? ORDER BY seq", (int(telegram_user_id),)
    )
