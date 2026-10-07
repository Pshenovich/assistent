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
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_tasks_assignee_start "
        "ON calendar_tasks(assignee_user_id, start_at)"
    )
    _ensure_google_event_columns(_CONN)
    _ensure_all_day_column(_CONN)
    _ensure_schedule_log_column(_CONN)
    _ensure_owner_archived_column(_CONN)
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


def _ensure_all_day_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    if "all_day" not in cols:
        conn.execute(
            "ALTER TABLE calendar_tasks ADD COLUMN all_day INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_schedule_log_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    if "schedule_log_json" not in cols:
        conn.execute(
            "ALTER TABLE calendar_tasks ADD COLUMN schedule_log_json TEXT NOT NULL DEFAULT '[]'"
        )


def _ensure_owner_archived_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    if "owner_archived" not in cols:
        conn.execute(
            "ALTER TABLE calendar_tasks ADD COLUMN owner_archived INTEGER NOT NULL DEFAULT 0"
        )


def _normalize_schedule_log(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            items = []
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "at": str(it.get("at") or ""),
                "by_user_id": str(it.get("by_user_id") or ""),
                "by_name": str(it.get("by_name") or ""),
                "from_start": str(it.get("from_start") or ""),
                "to_start": str(it.get("to_start") or ""),
                "from_end": str(it.get("from_end") or ""),
                "to_end": str(it.get("to_end") or ""),
                "from_all_day": bool(it.get("from_all_day")),
                "to_all_day": bool(it.get("to_all_day")),
            }
        )
    return out[-50:]


def format_schedule_log_lines(
    task: dict[str, Any], *, tz: ZoneInfo | None = None
) -> list[str]:
    """Человекочитаемые строки истории смены даты/времени."""
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

    def _fmt(iso: str, *, all_day: bool) -> str:
        dt = _parse_dt(iso)
        if dt is None:
            return "—"
        if tz is not None:
            dt = dt.astimezone(tz)
        day = f"{dt.day} {months[dt.month - 1]}"
        if all_day:
            return day
        return f"{day}, {dt.strftime('%H:%M')}"

    lines: list[str] = []
    for it in _normalize_schedule_log(task.get("schedule_log") or []):
        left = _fmt(it.get("from_start") or "", all_day=bool(it.get("from_all_day")))
        right = _fmt(it.get("to_start") or "", all_day=bool(it.get("to_all_day")))
        who = str(it.get("by_name") or "").strip() or "Участник"
        at = _parse_dt(it.get("at") or "")
        when = ""
        if at is not None:
            if tz is not None:
                at = at.astimezone(tz)
            when = f" · {at.day} {months[at.month - 1]} {at.strftime('%H:%M')}"
        lines.append(f"{left} → {right} · {who}{when}")
    return lines


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
    keys = row.keys()
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
        "google_event_id": str(row["google_event_id"] or "") if "google_event_id" in keys else "",
        "google_calendar_id": str(row["google_calendar_id"] or "")
        if "google_calendar_id" in keys
        else "",
        "all_day": bool(int(row["all_day"] or 0)) if "all_day" in keys else False,
        "schedule_log": _normalize_schedule_log(
            row["schedule_log_json"] if "schedule_log_json" in keys else "[]"
        ),
        "note_id": str(row["note_id"] or ""),
        "done": bool(int(row["done"] or 0)),
        "owner_archived": bool(int(row["owner_archived"] or 0))
        if "owner_archived" in keys
        else False,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def task_is_delegated(task: dict[str, Any] | None) -> bool:
    if not task:
        return False
    owner = str(task.get("owner_user_id") or "").strip()
    assignee = str(task.get("assignee_user_id") or "").strip()
    return bool(owner and assignee and owner != assignee)


def task_visible_in_calendar(task: dict[str, Any] | None, viewer_id: int | str) -> bool:
    """Делегированные задачи автора не показываем в его календаре — только у исполнителя.

    Выполненные (done) остаются видимыми (зачёркнутыми в UI). owner_archived
    влияет только на список «Поставленные» у автора, не на календарь исполнителя.
    """
    if not task:
        return False
    viewer = _uid(viewer_id)
    owner = str(task.get("owner_user_id") or "")
    assignee = str(task.get("assignee_user_id") or "")
    if not viewer:
        return False
    if task_is_delegated(task) and viewer == owner:
        return False
    return viewer == owner or viewer == assignee


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
    if task.get("all_day"):
        local_tz = tz or start.tzinfo or timezone.utc
        start = _coerce_aware(start, local_tz)
    elif tz is not None:
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
    day = f"{start.day} {months[start.month - 1]}"
    if task.get("all_day"):
        return day
    return f"{day}, {start.strftime('%H:%M')}"


def task_chip_html(task: dict[str, Any], *, tz: ZoneInfo | None = None) -> str:
    tid = int(task.get("id") or 0)
    label = html.escape(chip_label(task, tz=tz), quote=True)
    return (
        f'<span data-leo-task-id="{tid}" class="note-task-chip" '
        f'contenteditable="false">'
        f'<span class="note-task-chip-icon" aria-hidden="true"></span>'
        f'<span class="note-task-chip-label">{label}</span></span>'
    )


def task_id_from_chip_html(raw: str) -> Optional[int]:
    m = re.search(r'data-leo-task-id=["\'](\d+)["\']', str(raw or ""))
    return int(m.group(1)) if m else None


def as_calendar_event(
    task: dict[str, Any],
    *,
    tz: ZoneInfo | None = None,
    viewer_id: int | str | None = None,
) -> dict[str, Any]:
    start = _parse_dt(task.get("start_at"))
    end = _parse_dt(task.get("end_at")) or (start + DEFAULT_DURATION if start else None)
    all_day = bool(task.get("all_day"))
    # All-day: гражданская дата в tz календаря (не «вчера» из UTC midnight).
    if all_day and start is not None:
        local_tz = tz or start.tzinfo or timezone.utc
        start = _coerce_aware(start, local_tz)
        end = start + timedelta(days=1)
    else:
        if start and tz is not None:
            start = start.astimezone(tz)
        if end and tz is not None:
            end = end.astimezone(tz)
    start_iso = start.isoformat() if start else str(task.get("start_at") or "")
    end_iso = end.isoformat() if end else str(task.get("end_at") or "")
    start_day = start.date().isoformat() if start else start_iso[:10]
    end_day = end.date().isoformat() if end else start_day
    if all_day and start is not None:
        # Exclusive end.date like Google all-day events.
        end_exclusive = (start.date() + timedelta(days=1)).isoformat()
        start_payload: dict[str, Any] = {"date": start_day}
        end_payload: dict[str, Any] = {"date": end_exclusive}
        day_end_exclusive = True
        end_day = end_exclusive
    else:
        start_payload = {"dateTime": start_iso}
        end_payload = {"dateTime": end_iso}
        day_end_exclusive = False
    owner = str(task.get("owner_user_id") or "")
    viewer = _uid(viewer_id) if viewer_id not in (None, "") else ""
    is_owner = (not viewer) or viewer == owner
    return {
        "id": f"task-{task.get('id')}",
        "task_id": task.get("id"),
        "calendar_id": str(task.get("google_calendar_id") or "") or "leo-tasks",
        "google_event_id": str(task.get("google_event_id") or ""),
        "summary": str(task.get("title") or DEFAULT_TITLE),
        "kind": "Задача",
        "entry_type": "task",
        "all_day": all_day,
        "start": start_payload,
        "end": end_payload,
        "start_day": start_day,
        "end_day": end_day,
        "day_end_exclusive": day_end_exclusive,
        "html_link": "",
        "meet_url": None,
        "description": str(task.get("description") or ""),
        "location": "",
        "attendees": [],
        "is_organizer": is_owner,
        "self_response_status": None,
        "needs_rsvp": False,
        "checklist": list(task.get("checklist") or []),
        "assignee": {
            "user_id": str(task.get("assignee_user_id") or ""),
            "email": str(task.get("assignee_email") or ""),
            "name": str(task.get("assignee_name") or ""),
        },
        "owner_user_id": owner,
        "is_owner": is_owner,
        "done": bool(task.get("done")),
        "owner_archived": bool(task.get("owner_archived")),
        "note_id": str(task.get("note_id") or ""),
        "chip_label": chip_label(task, tz=tz),
        "schedule_log": _normalize_schedule_log(task.get("schedule_log") or []),
        "schedule_log_lines": format_schedule_log_lines(task, tz=tz),
    }


def _coerce_aware(dt: datetime, tz: ZoneInfo | timezone) -> datetime:
    """Naive datetime = wall time в tz (не системный UTC-сервер!)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _all_day_bounds(
    start: datetime, *, tz: ZoneInfo | timezone | None
) -> tuple[datetime, datetime]:
    local_tz: ZoneInfo | timezone = tz or start.tzinfo or timezone.utc
    aware = _coerce_aware(start, local_tz)
    day = aware.date()
    day_start = datetime.combine(day, time.min, tzinfo=local_tz)
    return day_start, day_start + timedelta(days=1)


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
    all_day: bool = False,
    tz: ZoneInfo | None = None,
) -> dict[str, Any]:
    uid = _uid(owner_user_id)
    start = start_at if isinstance(start_at, datetime) else _parse_dt(start_at, tz)
    if start is None:
        raise ValueError("Укажите начало задачи")
    if all_day:
        start, end = _all_day_bounds(start, tz=tz)
    else:
        if start.tzinfo is None and tz is not None:
            start = start.replace(tzinfo=tz)
        end = end_at if isinstance(end_at, datetime) else _parse_dt(end_at, tz)
        if end is not None and end.tzinfo is None and tz is not None:
            end = end.replace(tzinfo=tz)
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
                checklist_json, note_id, all_day, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if all_day else 0,
                now,
                now,
            ),
        )
        _conn().commit()
        tid = int(cur.lastrowid)
    item = get_task(uid, tid)
    assert item is not None
    return item


def _parse_task_id(task_id: int | str) -> int | None:
    try:
        return int(task_id)
    except (TypeError, ValueError):
        return None


def _get_task_row(task_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM calendar_tasks WHERE id = ?",
            (int(task_id),),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def user_can_access_task(user_id: int | str, task: dict[str, Any] | None) -> bool:
    if not task:
        return False
    uid = _uid(user_id)
    return uid == str(task.get("owner_user_id") or "") or uid == str(
        task.get("assignee_user_id") or ""
    )


def get_task(owner_user_id: int | str, task_id: int | str) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    tid = _parse_task_id(task_id)
    if tid is None:
        return None
    item = _get_task_row(tid)
    if not item or str(item.get("owner_user_id") or "") != uid:
        return None
    return item


def get_task_for_user(user_id: int | str, task_id: int | str) -> Optional[dict[str, Any]]:
    tid = _parse_task_id(task_id)
    if tid is None:
        return None
    item = _get_task_row(tid)
    if not user_can_access_task(user_id, item):
        return None
    return item


def _int_user_ids(raw: list[str]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            n = int(str(item or "").strip())
        except ValueError:
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def list_owner_user_ids() -> list[int]:
    with _LOCK:
        cur = _conn().execute(
            "SELECT DISTINCT owner_user_id FROM calendar_tasks"
        )
        raw = [str(r[0] or "").strip() for r in cur.fetchall()]
    return _int_user_ids(raw)


def list_involved_user_ids() -> list[int]:
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT DISTINCT owner_user_id FROM calendar_tasks
            UNION
            SELECT DISTINCT assignee_user_id FROM calendar_tasks
            WHERE assignee_user_id != ''
            """
        )
        raw = [str(r[0] or "").strip() for r in cur.fetchall()]
    return _int_user_ids(raw)


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
            WHERE (owner_user_id = ? OR assignee_user_id = ?)
              AND start_at < ?
              AND end_at > ?
            ORDER BY start_at ASC, id ASC
            """,
            (uid, uid, _iso(end), _iso(start)),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
    return rows


def list_tasks_for_note(
    owner_user_id: int | str, note_id: str | int
) -> list[dict[str, Any]]:
    uid = _uid(owner_user_id)
    nid = _clip(note_id, 40)
    if not nid:
        return []
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM calendar_tasks
            WHERE owner_user_id = ? AND note_id = ?
            ORDER BY start_at ASC, id ASC
            """,
            (uid, nid),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def list_posted_tasks(
    owner_user_id: int | str,
    *,
    include_done: bool = False,
    archived: bool | None = False,
) -> list[dict[str, Any]]:
    """Задачи, которые владелец поставил другим (не себе).

    archived=False — текущие (не в архиве постановщика);
    archived=True — только архив;
    archived=None — все (и текущие, и архив).
    """
    uid = _uid(owner_user_id)
    archived_flag = None if archived is None else (1 if archived else 0)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM calendar_tasks
            WHERE owner_user_id = ?
              AND assignee_user_id != ''
              AND assignee_user_id != owner_user_id
              AND (? = 1 OR done = 0)
              AND (? IS NULL OR IFNULL(owner_archived, 0) = ?)
            ORDER BY start_at ASC, id ASC
            """,
            (uid, 1 if include_done else 0, archived_flag, archived_flag),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def count_active_posted_tasks(owner_user_id: int | str) -> int:
    """Активные (не done) неархивные делегированные задачи — для счётчика «Поставлено»."""
    uid = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT COUNT(*) AS n FROM calendar_tasks
            WHERE owner_user_id = ?
              AND assignee_user_id != ''
              AND assignee_user_id != owner_user_id
              AND done = 0
              AND IFNULL(owner_archived, 0) = 0
            """,
            (uid,),
        )
        row = cur.fetchone()
        return int(row["n"] if row else 0)


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
    owner_archived: bool | None = None,
    all_day: bool | None = None,
    google_task_id: str | None = None,
    google_tasklist_id: str | None = None,
    google_event_id: str | None = None,
    google_calendar_id: str | None = None,
    schedule_log_entry: dict[str, Any] | None = None,
    schedule_actor: dict[str, Any] | None = None,
    tz: ZoneInfo | None = None,
) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    existing = get_task_for_user(uid, task_id)
    if not existing:
        return None
    # Пустая строка = поле не передавали (частый кейс JSON "" с фронта).
    if isinstance(start_at, str) and not str(start_at).strip():
        start_at = None
    if isinstance(end_at, str) and not str(end_at).strip():
        end_at = None
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
    is_all_day = bool(existing.get("all_day")) if all_day is None else bool(all_day)
    # Явный timed-патч (all_day=false) снимает «весь день».
    if all_day is False:
        is_all_day = False
    if is_all_day:
        start, end = _all_day_bounds(start, tz=tz)
    else:
        if start.tzinfo is None and tz is not None:
            start = start.replace(tzinfo=tz)
        if end is not None and end.tzinfo is None and tz is not None:
            end = end.replace(tzinfo=tz)
        if end is None or end <= start:
            end = _default_end(start)
    new_start_iso = _iso(start)
    new_end_iso = _iso(end)
    log = _normalize_schedule_log(existing.get("schedule_log") or [])
    schedule_changed = (
        new_start_iso != str(existing.get("start_at") or "")
        or new_end_iso != str(existing.get("end_at") or "")
        or bool(is_all_day) != bool(existing.get("all_day"))
    )
    entry = schedule_log_entry if isinstance(schedule_log_entry, dict) else None
    if schedule_changed and entry is None and isinstance(schedule_actor, dict):
        entry = {
            "at": _now_iso(),
            "by_user_id": str(schedule_actor.get("by_user_id") or ""),
            "by_name": str(schedule_actor.get("by_name") or ""),
            "from_start": str(existing.get("start_at") or ""),
            "to_start": new_start_iso,
            "from_end": str(existing.get("end_at") or ""),
            "to_end": new_end_iso,
            "from_all_day": bool(existing.get("all_day")),
            "to_all_day": bool(is_all_day),
        }
    if entry:
        log = _normalize_schedule_log(log + [entry])
    fields = {
        "title": _clip(title, MAX_TITLE_LEN) or existing["title"]
        if title is not None
        else existing["title"],
        "description": _clip(description, MAX_DESC_LEN)
        if description is not None
        else existing["description"],
        "start_at": new_start_iso,
        "end_at": new_end_iso,
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
        "owner_archived": 1
        if (
            existing.get("owner_archived")
            if owner_archived is None
            else bool(owner_archived)
        )
        else 0,
        "all_day": 1 if is_all_day else 0,
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
        "schedule_log_json": json.dumps(log, ensure_ascii=False),
        "updated_at": _now_iso(),
    }
    with _LOCK:
        _conn().execute(
            """
            UPDATE calendar_tasks SET
                title = ?, description = ?, start_at = ?, end_at = ?,
                assignee_user_id = ?, assignee_email = ?, assignee_name = ?,
                checklist_json = ?, note_id = ?, done = ?, owner_archived = ?,
                all_day = ?,
                google_task_id = ?, google_tasklist_id = ?,
                google_event_id = ?, google_calendar_id = ?,
                schedule_log_json = ?, updated_at = ?
            WHERE id = ?
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
                fields["owner_archived"],
                fields["all_day"],
                fields["google_task_id"],
                fields["google_tasklist_id"],
                fields["google_event_id"],
                fields["google_calendar_id"],
                fields["schedule_log_json"],
                fields["updated_at"],
                int(existing["id"]),
            ),
        )
        _conn().commit()
    return _get_task_row(int(existing["id"]))


def delete_task(owner_user_id: int | str, task_id: int | str) -> bool:
    existing = get_task_for_user(owner_user_id, task_id)
    if not existing:
        return False
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM calendar_tasks WHERE id = ?",
            (int(existing["id"]),),
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
