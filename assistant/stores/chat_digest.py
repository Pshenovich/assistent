"""Чаты Leo, лог сообщений и ежедневные дайджесты."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ITEM_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):(-?\d+)$")

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None

_MAX_TEXT_LEN = 4000


def _db_path() -> Path:
    raw = (os.getenv("CHAT_DIGEST_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    return ROOT / "data" / "chat_digest.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(str(path), check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS leo_chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            chat_type TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_message_at TEXT
        )
        """
    )
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS leo_chat_members (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS leo_chat_messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            user_id INTEGER,
            username TEXT,
            display_name TEXT,
            text TEXT NOT NULL DEFAULT '',
            ts_utc TEXT NOT NULL,
            date_utc TEXT NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_leo_msg_chat_date "
        "ON leo_chat_messages(chat_id, date_utc, ts_utc)"
    )
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS leo_chat_daily_reports (
            report_date TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            generated_at TEXT NOT NULL,
            model TEXT,
            is_demo INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (report_date, chat_id)
        )
        """
    )
    _CONN.commit()
    return _CONN


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_item_id(report_date: str, chat_id: int) -> str:
    return f"{(report_date or '').strip()}:{int(chat_id)}"


def parse_item_id(item_id: str | int) -> tuple[str, int] | None:
    raw = str(item_id or "").strip()
    match = _ITEM_ID_RE.fullmatch(raw)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def upsert_chat(
    chat_id: int,
    *,
    title: str | None = None,
    chat_type: str | None = None,
    touch_message: bool = False,
) -> None:
    cid = int(chat_id)
    now = _now_iso()
    title_s = (title or "").strip()
    type_s = (chat_type or "").strip()
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT chat_id, title, chat_type FROM leo_chats WHERE chat_id = ?",
            (cid,),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO leo_chats (chat_id, title, chat_type, first_seen_at, last_message_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cid, title_s, type_s, now, now if touch_message else None),
            )
        else:
            new_title = title_s or str(row["title"] or "")
            new_type = type_s or str(row["chat_type"] or "")
            if touch_message:
                conn.execute(
                    """
                    UPDATE leo_chats
                    SET title = ?, chat_type = ?, last_message_at = ?
                    WHERE chat_id = ?
                    """,
                    (new_title, new_type, now, cid),
                )
            elif title_s or type_s:
                conn.execute(
                    """
                    UPDATE leo_chats SET title = ?, chat_type = ? WHERE chat_id = ?
                    """,
                    (new_title, new_type, cid),
                )
        conn.commit()


def upsert_member(
    chat_id: int,
    user_id: int,
    *,
    username: str | None = None,
) -> None:
    cid = int(chat_id)
    uid = int(user_id)
    now = _now_iso()
    uname = (username or "").strip().lstrip("@") or None
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT chat_id FROM leo_chat_members WHERE chat_id = ? AND user_id = ?",
            (cid, uid),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO leo_chat_members
                    (chat_id, user_id, username, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cid, uid, uname, now, now),
            )
        else:
            conn.execute(
                """
                UPDATE leo_chat_members
                SET username = COALESCE(?, username), last_seen_at = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (uname, now, cid, uid),
            )
        conn.commit()


def insert_message(
    *,
    chat_id: int,
    message_id: int,
    user_id: int | None,
    username: str | None,
    display_name: str | None,
    text: str,
    ts_utc: datetime | None = None,
) -> bool:
    """Возвращает True, если сообщение новое (вставлено)."""
    cid = int(chat_id)
    mid = int(message_id)
    when = ts_utc or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc).replace(microsecond=0)
    body = (text or "").strip()
    if len(body) > _MAX_TEXT_LEN:
        body = body[:_MAX_TEXT_LEN]
    if not body:
        return False
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO leo_chat_messages (
                    chat_id, message_id, user_id, username, display_name,
                    text, ts_utc, date_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid,
                    mid,
                    int(user_id) if user_id is not None else None,
                    (username or "").strip().lstrip("@") or None,
                    (display_name or "").strip() or None,
                    body,
                    when.isoformat(),
                    when.date().isoformat(),
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def list_chats_for_user(user_id: int) -> list[dict[str, Any]]:
    uid = int(user_id)
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            """
            SELECT c.chat_id, c.title, c.chat_type, c.last_message_at, m.last_seen_at
            FROM leo_chat_members m
            JOIN leo_chats c ON c.chat_id = m.chat_id
            WHERE m.user_id = ?
            ORDER BY COALESCE(c.last_message_at, m.last_seen_at) DESC, c.title ASC
            """,
            (uid,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "chat_id": int(r["chat_id"]),
                "title": str(r["title"] or "") or f"Чат {r['chat_id']}",
                "chat_type": str(r["chat_type"] or ""),
                "last_message_at": r["last_message_at"],
            }
        )
    return out


def ensure_demo_chats(user_id: int, chat_ids: list[int]) -> None:
    uid = int(user_id)
    for cid in chat_ids:
        upsert_chat(int(cid), title=f"Чат {cid}", chat_type="supergroup")
        upsert_member(int(cid), uid)


def latest_report_date(chat_id: int) -> str | None:
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            """
            SELECT report_date FROM leo_chat_daily_reports
            WHERE chat_id = ?
            ORDER BY report_date DESC
            LIMIT 1
            """,
            (int(chat_id),),
        ).fetchone()
    if row is None:
        return None
    day = str(row["report_date"] or "").strip()
    return day or None


def earliest_message_date(chat_id: int) -> str | None:
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            """
            SELECT date_utc FROM leo_chat_messages
            WHERE chat_id = ?
            ORDER BY date_utc ASC
            LIMIT 1
            """,
            (int(chat_id),),
        ).fetchone()
    if row is None:
        return None
    day = str(row["date_utc"] or "").strip()
    return day or None


def message_dates_between(
    chat_id: int,
    date_from: str,
    date_to: str,
) -> list[str]:
    """Уникальные date_utc с сообщениями в [date_from, date_to], по возрастанию."""
    d0 = (date_from or "").strip()
    d1 = (date_to or "").strip()
    if not d0 or not d1 or d0 > d1:
        return []
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            """
            SELECT DISTINCT date_utc FROM leo_chat_messages
            WHERE chat_id = ? AND date_utc >= ? AND date_utc <= ?
            ORDER BY date_utc ASC
            """,
            (int(chat_id), d0, d1),
        ).fetchall()
    return [str(r["date_utc"]) for r in rows if r["date_utc"]]


def messages_for_chat_date(chat_id: int, date_utc: str) -> list[dict[str, Any]]:
    cid = int(chat_id)
    day = (date_utc or "").strip()
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            """
            SELECT message_id, user_id, username, display_name, text, ts_utc
            FROM leo_chat_messages
            WHERE chat_id = ? AND date_utc = ?
            ORDER BY ts_utc ASC, message_id ASC
            """,
            (cid, day),
        ).fetchall()
    return [
        {
            "message_id": int(r["message_id"]),
            "user_id": int(r["user_id"]) if r["user_id"] is not None else None,
            "username": r["username"],
            "display_name": r["display_name"],
            "text": str(r["text"] or ""),
            "ts_utc": r["ts_utc"],
        }
        for r in rows
    ]


def get_chat(chat_id: int) -> dict[str, Any] | None:
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT chat_id, title, chat_type, last_message_at FROM leo_chats WHERE chat_id = ?",
            (int(chat_id),),
        ).fetchone()
    if row is None:
        return None
    return {
        "chat_id": int(row["chat_id"]),
        "title": str(row["title"] or "") or f"Чат {row['chat_id']}",
        "chat_type": str(row["chat_type"] or ""),
        "last_message_at": row["last_message_at"],
    }


def upsert_report(
    *,
    report_date: str,
    chat_id: int,
    summary: dict[str, Any],
    message_count: int,
    model: str | None = None,
    is_demo: bool = False,
) -> None:
    day = (report_date or "").strip()
    cid = int(chat_id)
    now = _now_iso()
    payload = json.dumps(summary, ensure_ascii=False)
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO leo_chat_daily_reports (
                report_date, chat_id, summary_json, message_count,
                generated_at, model, is_demo
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date, chat_id) DO UPDATE SET
                summary_json = excluded.summary_json,
                message_count = excluded.message_count,
                generated_at = excluded.generated_at,
                model = excluded.model,
                is_demo = excluded.is_demo
            """,
            (
                day,
                cid,
                payload,
                int(message_count),
                now,
                (model or "").strip() or None,
                1 if is_demo else 0,
            ),
        )
        conn.commit()


def update_report_summary(
    report_date: str,
    chat_id: int,
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    day = (report_date or "").strip()
    cid = int(chat_id)
    payload = json.dumps(summary, ensure_ascii=False)
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            """
            UPDATE leo_chat_daily_reports
            SET summary_json = ?
            WHERE report_date = ? AND chat_id = ?
            """,
            (payload, day, cid),
        )
        conn.commit()
        if cur.rowcount <= 0:
            return None
    return get_report(day, cid)


def list_reports_for_chats(
    chat_ids: list[int],
    *,
    limit: int = 60,
) -> list[dict[str, Any]]:
    ids = [int(x) for x in chat_ids if x]
    if not ids:
        return []
    lim = max(1, min(int(limit), 200))
    placeholders = ",".join("?" for _ in ids)
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            f"""
            SELECT r.report_date, r.chat_id, r.summary_json, r.message_count,
                   r.generated_at, r.model, r.is_demo, c.title, c.chat_type
            FROM leo_chat_daily_reports r
            LEFT JOIN leo_chats c ON c.chat_id = r.chat_id
            WHERE r.chat_id IN ({placeholders})
            ORDER BY r.report_date DESC, c.title ASC
            LIMIT ?
            """,
            (*ids, lim),
        ).fetchall()
    return [_report_row(r) for r in rows]


def get_report(report_date: str, chat_id: int) -> dict[str, Any] | None:
    day = (report_date or "").strip()
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            """
            SELECT r.report_date, r.chat_id, r.summary_json, r.message_count,
                   r.generated_at, r.model, r.is_demo, c.title, c.chat_type
            FROM leo_chat_daily_reports r
            LEFT JOIN leo_chats c ON c.chat_id = r.chat_id
            WHERE r.report_date = ? AND r.chat_id = ?
            """,
            (day, int(chat_id)),
        ).fetchone()
    if row is None:
        return None
    return _report_row(row)


def _report_row(row: sqlite3.Row) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    raw = row["summary_json"]
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                summary = parsed
        except json.JSONDecodeError:
            summary = {"brief": str(raw)}
    title = str(row["title"] or "") or f"Чат {row['chat_id']}"
    day = str(row["report_date"])
    cid = int(row["chat_id"])
    return {
        "report_date": day,
        "chat_id": cid,
        "item_id": make_item_id(day, cid),
        "title": title,
        "chat_type": str(row["chat_type"] or ""),
        "summary": summary,
        "message_count": int(row["message_count"] or 0),
        "generated_at": row["generated_at"],
        "model": row["model"],
        "is_demo": bool(row["is_demo"]),
        "preview": _preview_from_summary(summary),
    }


def _preview_from_summary(summary: dict[str, Any]) -> str:
    brief = str(summary.get("brief") or "").strip()
    if brief:
        return brief[:180]
    decisions = summary.get("decisions") or []
    if isinstance(decisions, list) and decisions:
        first = decisions[0]
        if isinstance(first, dict):
            return str(first.get("text") or first.get("decision") or "")[:180]
        return str(first)[:180]
    steps = summary.get("next_steps") or []
    if isinstance(steps, list) and steps:
        first = steps[0]
        if isinstance(first, dict):
            return str(first.get("task") or "")[:180]
        return str(first)[:180]
    return ""
