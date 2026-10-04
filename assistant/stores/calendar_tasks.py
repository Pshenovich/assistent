"""Календарные задачи Leo (не встречи Google Calendar)."""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None

DEFAULT_TITLE = "Задача"
MAX_TITLE_LEN = 200
MAX_DESC_LEN = 4000
DEFAULT_DURATION = timedelta(minutes=30)


def _db_path() -> Path:
    raw = (os.getenv("CALENDAR_TASKS_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    return ROOT / "data" / "calendar_tasks.sqlite"


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
        CREATE TABLE IF NOT EXISTS calendar_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'Задача',
            description TEXT NOT NULL DEFAULT '',
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            assignee_user_id TEXT NOT NULL DEFAULT '',
            assignee_email TEXT NOT NULL DEFAULT '',
            assignee_name TEXT NOT NULL DEFAULT '',
            checklist_json TEXT NOT NULL DEFAULT '[]',
            google_task_id TEXT NOT NULL DEFAULT '',
            google_tasklist_id TEXT NOT NULL DEFAULT '',
            note_id TEXT NOT NULL DEFAULT '',
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_tasks_owner_start "
        "ON calendar_tasks(owner_user_id, start_at)"
    )
    _ensure_google_event_columns(_CONN)
    _CONN.commit()
    return _CONN


def _ensure_google_event_columns(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    if "google_event_id" not in cols:
        conn.execute(
            "ALTER TABLE calendar_tasks ADD COLUMN google_event_id TEXT NOT NULL DEFAULT ''"
        )
    if "google_calendar_id" not in cols:
        conn.execute(
            "ALTER TABLE calendar_tasks ADD COLUMN google_calendar_id TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_tasks_google_event "
        "ON calendar_tasks(owner_user_id, google_event_id)"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: int | str) -> str:
    raw = str(user_id or "").strip()
    try:
        return str(int(raw))
    except (TypeError, ValueError):
        return raw


def _clip(raw: str | None, limit: int) -> str:
    return str(raw or "").replace("\u0000", "").strip()[:limit]


def _normalize_checklist(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        text = _clip(str(it.get("text") or ""), 400)
        if not text:
            continue
        rid = str(it.get("id") or "").strip() or ("c" + uuid.uuid4().hex[:10])
        out.append({"id": rid, "text": text, "done": bool(it.get("done"))})
    return out


def _parse_dt(raw: str | None, tz: ZoneInfo | None = None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or timezone.utc)
    return dt


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "owner_user_id": str(row["owner_user_id"] or ""),
        "title": str(row["title"] or DEFAULT_TITLE),
        "description": str(row["description"] or ""),
        "start_at": str(row["start_at"] or ""),
        "end_at": str(row["end_at"] or ""),
        "assignee_user_id": str(row["assignee_user_id"] or ""),
        "assignee_email": str(row["assignee_email"] or ""),
        "assignee_name": str(row["assignee_name"] or ""),
        "checklist": _normalize_checklist(row["checklist_json"]),
        "google_task_id": str(row["google_task_id"] or ""),
        "google_tasklist_id": str(row["google_tasklist_id"] or ""),
        "google_event_id": str(row["google_event_id"] or "") if "google_event_id" in row.keys() else "",
        "google_calendar_id": str(row["google_calendar_id"] or "")
        if "google_calendar_id" in row.keys()
        else "",
        "note_id": str(row["note_id"] or ""),
        "done": bool(int(row["done"] or 0)),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _default_end(start: datetime) -> datetime:
    return start + DEFAULT_DURATION


def parse_slash_when(
    text: str,
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> dict[str, Any]:
    """«/сегодня 15:00 тема» → start/end/title."""
    from assistant.lib.calendar_datetime_parse import (
        _WEEKDAY_RU,
        _REL_DAY_WORDS,
        calendar_extract_time,
        calendar_parse_explicit_date,
        calendar_relative_day,
    )

    raw = str(text or "").strip()
    if raw.startswith("/"):
        raw = raw[1:].strip()
    if not raw:
        raise ValueError("Укажите день и время")
    now_dt = now or datetime.now(tz)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=tz)
    else:
        now_dt = now_dt.astimezone(tz)
    today = now_dt.date()
    rel = calendar_relative_day(raw, today, role="any")
    explicit = calendar_parse_explicit_date(raw, today) if rel is None else None
    day = rel or explicit
    if day is None:
        raise ValueError("Укажите день: сегодня, завтра, день недели или дату")
    raw_for_time = raw
    if explicit is not None:
        raw_for_time = re.sub(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b", " ", raw_for_time)
        raw_for_time = re.sub(
            r"\b\d{1,2}\s+(?:янв\w*|фев\w*|мар\w*|апр\w*|ма[йя]\w*|июн\w*|июл\w*|авг\w*|сен\w*|окт\w*|ноя\w*|дек\w*)\b",
            " ",
            raw_for_time,
            flags=re.IGNORECASE,
        )
    th = calendar_extract_time(raw_for_time)
    if th:
        start = datetime.combine(day, time(th[0], th[1]), tzinfo=tz)
    elif day == today:
        start = now_dt.replace(second=0, microsecond=0)
    else:
        start = datetime.combine(day, time(9, 0), tzinfo=tz)
    title = _REL_DAY_WORDS.sub(" ", raw)
    for wd in _WEEKDAY_RU:
        title = re.sub(rf"\b{re.escape(wd)}\w*\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b", " ", title)
    title = re.sub(
        r"\b\d{1,2}\s+(?:янв\w*|фев\w*|мар\w*|апр\w*|ма[йя]\w*|июн\w*|июл\w*|авг\w*|сен\w*|окт\w*|ноя\w*|дек\w*)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\b\d{1,2}[:.]\d{2}\b", " ", title)
    title = re.sub(
        r"\b(?:в|к)\s*\d{1,2}(?:[:.]\d{2})?\s*(?:час|ч\.?)?\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\b(?:на|в|с|со|к|послезавтра)\b", " ", title, flags=re.IGNORECASE)
    title = " ".join(title.split()).strip()
    return {
        "title": _clip(title, MAX_TITLE_LEN) or DEFAULT_TITLE,
        "start": start,
        "end": _default_end(start),
    }


def chip_label(task: dict[str, Any], *, tz: ZoneInfo | None = None) -> str:
    start = _parse_dt(task.get("start_at"))
    if start is None:
        return str(task.get("title") or DEFAULT_TITLE)
    if tz is not None:
        start = start.astimezone(tz)
    months = (
        "янв",
        "фев",
        "мар",
        "апр",
        "мая",
        "июн",
        "июл",
        "авг",
        "сен",
        "окт",
        "ноя",
        "дек",
    )
    return f"Задача {start.day} {months[start.month - 1]}, {start.strftime('%H:%M')}"


def task_chip_html(task: dict[str, Any], *, tz: ZoneInfo | None = None) -> str:
    tid = int(task.get("id") or 0)
    label = html.escape(chip_label(task, tz=tz), quote=True)
    return (
        f'<span data-leo-task-id="{tid}" class="note-task-chip" '
        f'contenteditable="false">{label}</span>'
    )


def task_id_from_chip_html(raw: str) -> Optional[int]:
    m = re.search(r'data-leo-task-id=["\'](\d+)["\']', str(raw or ""))
    return int(m.group(1)) if m else None


def as_calendar_event(task: dict[str, Any], *, tz: ZoneInfo | None = None) -> dict[str, Any]:
    start = _parse_dt(task.get("start_at"))
    end = _parse_dt(task.get("end_at")) or (start + DEFAULT_DURATION if start else None)
    if start and tz is not None:
        start = start.astimezone(tz)
    if end and tz is not None:
        end = end.astimezone(tz)
    start_iso = start.isoformat() if start else str(task.get("start_at") or "")
    end_iso = end.isoformat() if end else str(task.get("end_at") or "")
    start_day = start.date().isoformat() if start else start_iso[:10]
    end_day = end.date().isoformat() if end else start_day
    return {
        "id": f"task-{task.get('id')}",
        "task_id": task.get("id"),
        "calendar_id": str(task.get("google_calendar_id") or "") or "leo-tasks",
        "google_event_id": str(task.get("google_event_id") or ""),
        "summary": str(task.get("title") or DEFAULT_TITLE),
        "kind": "Задача",
        "entry_type": "task",
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
        "start_day": start_day,
        "end_day": end_day,
        "day_end_exclusive": False,
        "html_link": "",
        "meet_url": None,
        "description": str(task.get("description") or ""),
        "location": "",
        "attendees": [],
        "is_organizer": True,
        "self_response_status": None,
        "needs_rsvp": False,
        "checklist": list(task.get("checklist") or []),
        "assignee": {
            "user_id": str(task.get("assignee_user_id") or ""),
            "email": str(task.get("assignee_email") or ""),
            "name": str(task.get("assignee_name") or ""),
        },
        "done": bool(task.get("done")),
        "note_id": str(task.get("note_id") or ""),
        "chip_label": chip_label(task, tz=tz),
    }


def create_task(
    owner_user_id: int | str,
    *,
    title: str | None = None,
    description: str = "",
    start_at: str | datetime,
    end_at: str | datetime | None = None,
    assignee_user_id: str | int | None = None,
    assignee_email: str = "",
    assignee_name: str = "",
    checklist: list[dict[str, Any]] | None = None,
    note_id: str = "",
    tz: ZoneInfo | None = None,
) -> dict[str, Any]:
    uid = _uid(owner_user_id)
    start = start_at if isinstance(start_at, datetime) else _parse_dt(start_at, tz)
    if start is None:
        raise ValueError("Укажите начало задачи")
    end = end_at if isinstance(end_at, datetime) else _parse_dt(end_at, tz)
    if end is None or end <= start:
        end = _default_end(start)
    now = _now_iso()
    assignee = _uid(assignee_user_id) if assignee_user_id not in (None, "") else uid
    title_s = _clip(title, MAX_TITLE_LEN) or DEFAULT_TITLE
    with _LOCK:
        cur = _conn().execute(
            """
            INSERT INTO calendar_tasks (
                owner_user_id, title, description, start_at, end_at,
                assignee_user_id, assignee_email, assignee_name,
                checklist_json, note_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                title_s,
                _clip(description, MAX_DESC_LEN),
                _iso(start),
                _iso(end),
                assignee,
                _clip(assignee_email, 200),
                _clip(assignee_name, 120),
                json.dumps(_normalize_checklist(checklist), ensure_ascii=False),
                _clip(note_id, 40),
                now,
                now,
            ),
        )
        _conn().commit()
        tid = int(cur.lastrowid)
    item = get_task(uid, tid)
    assert item is not None
    return item


def get_task(owner_user_id: int | str, task_id: int | str) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM calendar_tasks WHERE id = ? AND owner_user_id = ?",
            (tid, uid),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_owner_user_ids() -> list[int]:
    with _LOCK:
        cur = _conn().execute(
            "SELECT DISTINCT owner_user_id FROM calendar_tasks"
        )
        raw = [str(r[0] or "").strip() for r in cur.fetchall()]
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except ValueError:
            continue
    return out


def list_tasks_in_window(
    owner_user_id: int | str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    uid = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM calendar_tasks
            WHERE owner_user_id = ?
              AND start_at < ?
              AND end_at > ?
            ORDER BY start_at ASC, id ASC
            """,
            (uid, _iso(end), _iso(start)),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
    return rows


def list_tasks(owner_user_id: int | str, *, limit: int = 200) -> list[dict[str, Any]]:
    uid = _uid(owner_user_id)
    lim = max(1, min(int(limit or 200), 500))
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM calendar_tasks
            WHERE owner_user_id = ?
            ORDER BY start_at ASC, id ASC
            LIMIT ?
            """,
            (uid, lim),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def update_task(
    owner_user_id: int | str,
    task_id: int | str,
    *,
    title: str | None = None,
    description: str | None = None,
    start_at: str | datetime | None = None,
    end_at: str | datetime | None = None,
    assignee_user_id: str | int | None = None,
    assignee_email: str | None = None,
    assignee_name: str | None = None,
    checklist: list[dict[str, Any]] | None = None,
    note_id: str | None = None,
    done: bool | None = None,
    google_task_id: str | None = None,
    google_tasklist_id: str | None = None,
    google_event_id: str | None = None,
    google_calendar_id: str | None = None,
    tz: ZoneInfo | None = None,
) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    existing = get_task(uid, task_id)
    if not existing:
        return None
    start = (
        start_at
        if isinstance(start_at, datetime)
        else _parse_dt(start_at, tz)
        if start_at is not None
        else _parse_dt(existing["start_at"])
    )
    end = (
        end_at
        if isinstance(end_at, datetime)
        else _parse_dt(end_at, tz)
        if end_at is not None
        else _parse_dt(existing["end_at"])
    )
    if start is None:
        raise ValueError("Укажите начало задачи")
    if end is None or end <= start:
        end = _default_end(start)
    fields = {
        "title": _clip(title, MAX_TITLE_LEN) or existing["title"]
        if title is not None
        else existing["title"],
        "description": _clip(description, MAX_DESC_LEN)
        if description is not None
        else existing["description"],
        "start_at": _iso(start),
        "end_at": _iso(end),
        "assignee_user_id": _uid(assignee_user_id)
        if assignee_user_id not in (None, "")
        else existing["assignee_user_id"],
        "assignee_email": _clip(assignee_email, 200)
        if assignee_email is not None
        else existing["assignee_email"],
        "assignee_name": _clip(assignee_name, 120)
        if assignee_name is not None
        else existing["assignee_name"],
        "checklist_json": json.dumps(
            _normalize_checklist(checklist if checklist is not None else existing["checklist"]),
            ensure_ascii=False,
        ),
        "note_id": _clip(note_id, 40) if note_id is not None else existing["note_id"],
        "done": 1 if (existing["done"] if done is None else bool(done)) else 0,
        "google_task_id": _clip(google_task_id, 200)
        if google_task_id is not None
        else existing["google_task_id"],
        "google_tasklist_id": _clip(google_tasklist_id, 200)
        if google_tasklist_id is not None
        else existing["google_tasklist_id"],
        "google_event_id": _clip(google_event_id, 200)
        if google_event_id is not None
        else existing.get("google_event_id") or "",
        "google_calendar_id": _clip(google_calendar_id, 200)
        if google_calendar_id is not None
        else existing.get("google_calendar_id") or "",
        "updated_at": _now_iso(),
    }
    with _LOCK:
        _conn().execute(
            """
            UPDATE calendar_tasks SET
                title = ?, description = ?, start_at = ?, end_at = ?,
                assignee_user_id = ?, assignee_email = ?, assignee_name = ?,
                checklist_json = ?, note_id = ?, done = ?,
                google_task_id = ?, google_tasklist_id = ?,
                google_event_id = ?, google_calendar_id = ?, updated_at = ?
            WHERE id = ? AND owner_user_id = ?
            """,
            (
                fields["title"],
                fields["description"],
                fields["start_at"],
                fields["end_at"],
                fields["assignee_user_id"],
                fields["assignee_email"],
                fields["assignee_name"],
                fields["checklist_json"],
                fields["note_id"],
                fields["done"],
                fields["google_task_id"],
                fields["google_tasklist_id"],
                fields["google_event_id"],
                fields["google_calendar_id"],
                fields["updated_at"],
                int(existing["id"]),
                uid,
            ),
        )
        _conn().commit()
    return get_task(uid, existing["id"])


def delete_task(owner_user_id: int | str, task_id: int | str) -> bool:
    uid = _uid(owner_user_id)
    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return False
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM calendar_tasks WHERE id = ? AND owner_user_id = ?",
            (tid, uid),
        )
        _conn().commit()
        return cur.rowcount > 0


def find_by_google_event(
    owner_user_id: int | str, google_event_id: str
) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    gid = str(google_event_id or "").strip()
    if not gid:
        return None
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM calendar_tasks
            WHERE owner_user_id = ? AND google_event_id = ?
            LIMIT 1
            """,
            (uid, gid),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def find_by_google_task(
    owner_user_id: int | str, google_task_id: str
) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    gid = str(google_task_id or "").strip()
    if not gid:
        return None
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM calendar_tasks
            WHERE owner_user_id = ? AND google_task_id = ?
            LIMIT 1
            """,
            (uid, gid),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None
