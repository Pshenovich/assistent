"""Правила отправителей почты: block («Не показывать / спам») и allow («Всегда пропускать»).

Шаблон — адрес (ivan@firm.ru) или домен (@firm.ru). Точный адрес сильнее домена; правила сильнее воронки.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

RULES = ("block", "allow")

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None


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
        CREATE TABLE IF NOT EXISTS mail_senders (
            owner_user_id TEXT NOT NULL,
            pattern TEXT NOT NULL,
            rule TEXT NOT NULL,
            created_at TEXT NOT NULL,
            moved_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (owner_user_id, pattern)
        )
        """
    )
    _CONN.commit()
    return _CONN


def normalize(raw: str) -> str:
    """«Ivan@Firm.ru» → ivan@firm.ru; «firm.ru» / «@firm.ru» / «*@firm.ru» → @firm.ru. Пусто — ошибка."""
    p = str(raw or "").strip().lower().strip("<>").lstrip("*")
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", p):
        return p
    d = p.lstrip("@")
    if re.fullmatch(r"[a-z0-9.-]+\.[a-z0-9-]{2,}", d):
        return "@" + d
    raise ValueError("Укажите адрес (ivan@firm.ru) или домен (@firm.ru).")


def set_rule(owner_user_id: Any, pattern: str, rule: str) -> dict[str, Any]:
    if rule not in RULES:
        raise ValueError("Неизвестное правило")
    p = normalize(pattern)
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO mail_senders (owner_user_id, pattern, rule, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_user_id, pattern) DO UPDATE SET rule = excluded.rule, created_at = excluded.created_at
            """,
            (str(owner_user_id), p, rule, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    return {"pattern": p, "rule": rule}


def remove(owner_user_id: Any, pattern: str) -> Optional[dict[str, Any]]:
    p = normalize(pattern)
    with _LOCK:
        conn = _conn()
        r = conn.execute(
            "SELECT * FROM mail_senders WHERE owner_user_id = ? AND pattern = ?", (str(owner_user_id), p)
        ).fetchone()
        conn.execute("DELETE FROM mail_senders WHERE owner_user_id = ? AND pattern = ?", (str(owner_user_id), p))
        conn.commit()
    return _row(r) if r else None


def _row(r: sqlite3.Row) -> dict[str, Any]:
    try:
        moved = json.loads(r["moved_json"] or "[]")
    except ValueError:
        moved = []
    return {"pattern": r["pattern"], "rule": r["rule"], "created_at": r["created_at"], "moved": moved}


def list_rules(owner_user_id: Any) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM mail_senders WHERE owner_user_id = ? ORDER BY created_at DESC", (str(owner_user_id),)
        ).fetchall()
    return [_row(r) for r in rows]


def rules_map(owner_user_id: Any) -> dict[str, str]:
    return {r["pattern"]: r["rule"] for r in list_rules(owner_user_id)}


def match(rules: dict[str, str], addr: str) -> Optional[str]:
    """block / allow / None. Адрес сильнее домена."""
    a = str(addr or "").strip().lower()
    if not a:
        return None
    if a in rules:
        return rules[a]
    if "@" in a:
        return rules.get("@" + a.rsplit("@", 1)[1])
    return None


def add_moved(owner_user_id: Any, pattern: str, items: list[dict[str, Any]]) -> None:
    """Запомнить письма, перенесённые в «Спам» (чтобы вернуть при снятии блока)."""
    p = normalize(pattern)
    with _LOCK:
        conn = _conn()
        r = conn.execute(
            "SELECT moved_json FROM mail_senders WHERE owner_user_id = ? AND pattern = ?", (str(owner_user_id), p)
        ).fetchone()
        if not r:
            return
        moved = json.loads(r["moved_json"] or "[]") + items
        conn.execute(
            "UPDATE mail_senders SET moved_json = ? WHERE owner_user_id = ? AND pattern = ?",
            (json.dumps(moved[-500:], ensure_ascii=False), str(owner_user_id), p),
        )
        conn.commit()
