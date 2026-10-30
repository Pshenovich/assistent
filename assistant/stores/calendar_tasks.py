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

from assistant.lib import sqlite_conn

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None

DEFAULT_TITLE = "Задача"
MAX_TITLE_LEN = 200
MAX_DESC_LEN = 4000
MAX_RESULT_LEN = 4000
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
    _CONN = sqlite_conn.connect(str(path))
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
    _ensure_recurrence_column(_CONN)
    _ensure_result_column(_CONN)
    _ensure_assignee_state_columns(_CONN)
    _ensure_outcome_columns(_CONN)
    _ensure_parent_column(_CONN)
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            at TEXT NOT NULL,
            actor_user_id TEXT NOT NULL DEFAULT '',
            actor_name TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT ''
        )
        """
    )
    _CONN.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, id)")
    if "meta_json" not in {str(r[1]) for r in _CONN.execute("PRAGMA table_info(task_events)")}:
        _CONN.execute("ALTER TABLE task_events ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'")
    _CONN.commit()
    return _CONN


def _ensure_recurrence_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    if "recurrence" not in cols:
        conn.execute(
            "ALTER TABLE calendar_tasks ADD COLUMN recurrence TEXT NOT NULL DEFAULT ''"
        )


def _ensure_result_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    if "result" not in cols:
        conn.execute(
            "ALTER TABLE calendar_tasks ADD COLUMN result TEXT NOT NULL DEFAULT ''"
        )


# Обратная связь исполнителя по поручению: видел, взял в работу / есть вопрос; когда автор напомнил.
ASSIGNEE_STATUSES = ("", "in_progress", "question")
ASSIGNEE_STATE_COLUMNS = (
    "assignee_seen_at",
    "assignee_status",
    "assignee_status_note",
    "assignee_status_at",
    "last_nudged_at",
    "assignee_triage",
    "assignee_snoozed_until",
)
MAX_STATUS_NOTE_LEN = 500
# Разбор входящих у исполнителя: новое поручение не попадает в его календарь, пока он его не взял.
# '' — до разбора (старые задачи, задачи себе) — ведёт себя как раньше.
TRIAGE_STATES = ("", "inbox", "snoozed", "accepted", "declined")
TRIAGE_HIDDEN = ("inbox", "snoozed", "declined")
SNOOZE_HOUR = 7


def _ensure_assignee_state_columns(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    for col in ASSIGNEE_STATE_COLUMNS:
        if col not in cols:
            conn.execute(f"ALTER TABLE calendar_tasks ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")


# Итог поручения у постановщика: когда сдали, когда приняли, принято или отменено.
OUTCOME_COLUMNS = ("done_at", "accepted_at", "outcome")
OUTCOMES = ("", "accepted", "cancelled")


def _ensure_outcome_columns(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    for col in OUTCOME_COLUMNS:
        if col not in cols:
            conn.execute(f"ALTER TABLE calendar_tasks ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")


# Подзадачи: задача → подзадача → под-подзадача. Глубже не вкладываем.
MAX_SUBTASK_DEPTH = 2


def _ensure_parent_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(calendar_tasks)")}
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE calendar_tasks ADD COLUMN parent_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calendar_tasks_parent ON calendar_tasks(parent_id)")


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
        "result": str(row["result"] or "") if "result" in keys else "",
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
        "recurrence": str(row["recurrence"] or "") if "recurrence" in keys else "",
        "parent_id": int(row["parent_id"]) if "parent_id" in keys and row["parent_id"] else None,
        **{c: str(row[c] or "") if c in keys else "" for c in ASSIGNEE_STATE_COLUMNS + OUTCOME_COLUMNS},
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
    if task_is_delegated(task) and str(task.get("outcome") or "") == "cancelled":
        return False  # постановщик отменил поручение — у исполнителя его больше нет
    if task_is_delegated(task) and viewer == assignee and str(task.get("assignee_triage") or "") in TRIAGE_HIDDEN:
        return False  # ещё во входящих (или «не моё») — в календарь не ставим
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
        "result": str(task.get("result") or ""),
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
        "assignee_state": assignee_state(task),
        "done_at": str(task.get("done_at") or ""),
        "accepted_at": str(task.get("accepted_at") or ""),
        "outcome": str(task.get("outcome") or ""),
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
    result: str = "",
    start_at: str | datetime,
    end_at: str | datetime | None = None,
    assignee_user_id: str | int | None = None,
    assignee_email: str = "",
    assignee_name: str = "",
    checklist: list[dict[str, Any]] | None = None,
    note_id: str = "",
    all_day: bool = False,
    tz: ZoneInfo | None = None,
    recurrence: str = "",
    parent_id: int | None = None,
) -> dict[str, Any]:
    uid = _uid(owner_user_id)
    if parent_id is not None:
        check_parent(uid, None, parent_id)
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
    rrule = str(recurrence or "").strip()
    if rrule and not rrule.upper().startswith("RRULE:"):
        rrule = "RRULE:" + rrule
    with _LOCK:
        cur = _conn().execute(
            """
            INSERT INTO calendar_tasks (
                owner_user_id, title, description, result, start_at, end_at,
                assignee_user_id, assignee_email, assignee_name,
                checklist_json, note_id, all_day, recurrence, assignee_triage,
                parent_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                title_s,
                _clip(description, MAX_DESC_LEN),
                _clip(result, MAX_RESULT_LEN),
                _iso(start),
                _iso(end),
                assignee,
                _clip(assignee_email, 200),
                _clip(assignee_name, 120),
                json.dumps(_normalize_checklist(checklist), ensure_ascii=False),
                _clip(note_id, 40),
                1 if all_day else 0,
                _clip(rrule, 500),
                "inbox" if assignee and assignee != uid else "",
                int(parent_id) if parent_id is not None else None,
                now,
                now,
            ),
        )
        _conn().commit()
        tid = int(cur.lastrowid)
    item = get_task(uid, tid)
    assert item is not None
    if parent_id is not None:
        _parent_changed(int(parent_id), actor_user_id=uid)
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
    """Постановщик и исполнитель задачи — и те же роли у общих задач выше по цепочке:
    Артём отвечает за «Запуск лендинга», значит, видит и правит подзадачу Ульяны."""
    if not task:
        return False
    uid = _uid(user_id)
    return any(
        uid in (str(t.get("owner_user_id") or ""), str(t.get("assignee_user_id") or ""))
        for t in [task, *ancestors(task)]
    )


def ancestors(task: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Общие задачи над этой — от ближней к верхней."""
    out: list[dict[str, Any]] = []
    seen = {int(task["id"])} if task and task.get("id") else set()
    pid = (task or {}).get("parent_id")
    while pid and int(pid) not in seen and len(out) <= MAX_SUBTASK_DEPTH:
        row = _get_task_row(int(pid))
        if not row:
            break
        out.append(row)
        seen.add(int(pid))
        pid = row.get("parent_id")
    return out


def list_subtasks(task_id: int | str) -> list[dict[str, Any]]:
    """Прямые подзадачи — в порядке добавления."""
    tid = _parse_task_id(task_id)
    if tid is None:
        return []
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM calendar_tasks WHERE parent_id = ? ORDER BY id ASC", (tid,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def subtask_tree(task_id: int | str, *, depth: int = MAX_SUBTASK_DEPTH) -> list[dict[str, Any]]:
    out = []
    for row in list_subtasks(task_id):
        row["subtasks"] = subtask_tree(row["id"], depth=depth - 1) if depth > 1 else []
        out.append(row)
    return out


def subtask_state(t: dict[str, Any]) -> str:
    """Короткий статус подзадачи для списка в общей задаче."""
    if t.get("done"):
        return "done"
    if task_is_delegated(t):
        triage = str(t.get("assignee_triage") or "")
        if triage == "declined":
            return "declined"
        if str(t.get("assignee_status") or "") in ("in_progress", "question"):
            return str(t["assignee_status"])
        if triage in ("inbox", "snoozed"):
            return "assigned"
    elif str(t.get("assignee_status") or "") == "in_progress":
        return "in_progress"
    return "open"


def subtasks_brief(task_id: int | str, *, depth: int = MAX_SUBTASK_DEPTH) -> list[dict[str, Any]]:
    """Дерево подзадач для окна задачи: только то, что нужно строке списка."""
    keys = ("id", "title", "description", "result", "start_at", "all_day", "done",
            "assignee_user_id", "assignee_name", "owner_user_id", "note_id")
    out = []
    for row in list_subtasks(task_id):
        item = {k: row.get(k) for k in keys}
        item["state"] = subtask_state(row)
        item["subtasks"] = subtasks_brief(row["id"], depth=depth - 1) if depth > 1 else []
        out.append(item)
    return out


def _subtree_height(task_id: int, *, guard: int = MAX_SUBTASK_DEPTH + 1) -> int:
    if guard <= 0:
        return MAX_SUBTASK_DEPTH + 1  # петля в данных — считаем «слишком глубоко»
    kids = list_subtasks(task_id)
    return 1 + max(_subtree_height(int(k["id"]), guard=guard - 1) for k in kids) if kids else 0


def check_parent(user_id: int | str, task_id: int | str | None, parent_id: int | str) -> dict[str, Any]:
    """Можно ли сделать task_id (None — новую задачу) подзадачей parent_id. Иначе ValueError."""
    pid = _parse_task_id(parent_id)
    parent = _get_task_row(pid) if pid is not None else None
    if not parent or not user_can_access_task(user_id, parent):
        raise ValueError("Общая задача не найдена")
    return _check_tree(task_id, parent)


def _check_tree(task_id: int | str | None, parent: dict[str, Any]) -> dict[str, Any]:
    tid = _parse_task_id(task_id) if task_id is not None else None
    chain = [parent, *ancestors(parent)]
    if tid is not None and any(int(t["id"]) == tid for t in chain):
        raise ValueError("Задачу нельзя вложить в её же подзадачу")
    height = _subtree_height(tid) if tid is not None else 0
    if len(chain) + height > MAX_SUBTASK_DEPTH:
        raise ValueError("Глубже двух уровней подзадачи не вкладываются")
    return parent


def set_parent(
    user_id: int | str, task_id: int | str, parent_id: int | str | None
) -> Optional[dict[str, Any]]:
    """Вложить задачу в другую (parent_id) или сделать самостоятельной (None)."""
    existing = get_task_for_user(user_id, task_id)
    if not existing:
        return None
    new_pid = None
    if parent_id not in (None, "", 0):
        new_pid = int(check_parent(user_id, existing["id"], parent_id)["id"])
    old_pid = existing.get("parent_id")
    if new_pid == old_pid:
        return existing
    with _LOCK:
        _conn().execute(
            "UPDATE calendar_tasks SET parent_id = ?, updated_at = ? WHERE id = ?",
            (new_pid, _now_iso(), int(existing["id"])),
        )
        _conn().commit()
    for pid in {old_pid, new_pid} - {None}:
        _parent_changed(int(pid), actor_user_id=user_id)
    if str(existing.get("note_id") or "").isdigit():
        _meeting_task_changed(int(existing["note_id"]))
    return _get_task_row(int(existing["id"]))


def link_parent(task_id: int | str, parent_id: int | str | None, *, actor_user_id: int | str = "") -> bool:
    """Связь «общая задача → подзадача» от таблицы задач встречи: решают участники встречи,
    поэтому без проверки прав на сами задачи — только форма дерева. Нельзя — False."""
    row = _get_task_row(int(task_id))
    if not row:
        return False
    new_pid = None
    if parent_id not in (None, "", 0):
        parent = _get_task_row(int(parent_id))
        if not parent:
            return False
        try:
            _check_tree(row["id"], parent)
        except ValueError:
            return False
        new_pid = int(parent["id"])
    old_pid = row.get("parent_id")
    if new_pid == old_pid:
        return True
    with _LOCK:
        _conn().execute(
            "UPDATE calendar_tasks SET parent_id = ?, updated_at = ? WHERE id = ?", (new_pid, _now_iso(), int(row["id"]))
        )
        _conn().commit()
    for pid in {old_pid, new_pid} - {None}:
        _parent_changed(int(pid), actor_user_id=actor_user_id)
    return True


def _parent_changed(parent_id: int, *, actor_user_id: int | str = "") -> None:
    """Состав или статус подзадач поменялся — общей задаче и тем, кто над ней, обновить счётчик."""
    try:
        from assistant.realtime import notify as realtime_notify

        row = _get_task_row(parent_id)
        for t in [row, *ancestors(row)] if row else []:
            realtime_notify.task_changed(
                int(t["id"]), [t["owner_user_id"], t["assignee_user_id"]], actor_user_id=actor_user_id
            )
    except Exception as e:
        print(f"[calendar_tasks] parent notify failed parent={parent_id} err={e!r}")


def get_task(owner_user_id: int | str, task_id: int | str) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    tid = _parse_task_id(task_id)
    if tid is None:
        return None
    item = _get_task_row(tid)
    if not item or str(item.get("owner_user_id") or "") != uid:
        return None
    return item


def list_recent_owner_tasks(owner_user_id: int | str, since_iso: str, limit: int = 20) -> list[dict[str, Any]]:
    """Задачи владельца, созданные после since_iso (новые — первыми)."""
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM calendar_tasks WHERE owner_user_id = ? AND created_at >= ? ORDER BY id DESC LIMIT ?",
            (_uid(owner_user_id), str(since_iso), int(limit)),
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


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


def _expand_recurring_task(
    task: dict[str, Any], window_start: datetime, window_end: datetime
) -> list[dict[str, Any]]:
    """Развернуть серию задачи на окна; одна запись в БД — удаление снимает всю серию."""
    from assistant.lib.recurrence import next_after

    rrule = str(task.get("recurrence") or "").strip()
    if not rrule or task.get("done"):
        return [task]
    base_start = _parse_dt(task.get("start_at"))
    base_end = _parse_dt(task.get("end_at"))
    if base_start is None:
        return [task]
    dur = (base_end - base_start) if base_end and base_end > base_start else timedelta(minutes=30)
    out: list[dict[str, Any]] = []
    # Первое вхождение ≥ window_start (или сама база, если в окне).
    cursor = base_start
    if cursor < window_start:
        nxt = next_after(base_start, rrule, after=window_start - timedelta(microseconds=1))
        if nxt is None:
            return []
        cursor = nxt
    guard = 0
    while cursor < window_end and guard < 400:
        guard += 1
        occ_end = cursor + dur
        if occ_end > window_start and cursor < window_end:
            row = dict(task)
            row["start_at"] = _iso(cursor)
            row["end_at"] = _iso(occ_end)
            row["occurrence_start"] = cursor.isoformat()
            out.append(row)
        nxt = next_after(base_start, rrule, after=cursor)
        if nxt is None or nxt <= cursor:
            break
        cursor = nxt
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
            WHERE (owner_user_id = ? OR assignee_user_id = ?)
              AND NOT (
                assignee_user_id = ? AND owner_user_id != assignee_user_id
                AND IFNULL(assignee_triage, '') IN ('inbox', 'snoozed', 'declined')
              )
              AND (
                (IFNULL(recurrence, '') = '' AND start_at < ? AND end_at > ?)
                OR (IFNULL(recurrence, '') != '' AND start_at < ? AND done = 0)
              )
            ORDER BY start_at ASC, id ASC
            """,
            (uid, uid, uid, _iso(end), _iso(start), _iso(end)),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("recurrence") or "").strip():
            out.extend(_expand_recurring_task(row, start, end))
        else:
            out.append(row)
    out.sort(key=lambda t: (str(t.get("start_at") or ""), int(t.get("id") or 0)))
    return out


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


def list_involved_tasks(user_id: int | str, *, limit: int = 2000) -> list[dict[str, Any]]:
    """Все задачи, где пользователь постановщик или исполнитель, — свежие первыми (поиск за всё время)."""
    uid = _uid(user_id)
    lim = max(1, min(int(limit or 2000), 5000))
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM calendar_tasks
            WHERE owner_user_id = ? OR assignee_user_id = ?
            ORDER BY start_at DESC, id DESC
            LIMIT ?
            """,
            (uid, uid, lim),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def list_my_tasks(
    user_id: int | str,
    *,
    done: bool = False,
    now: datetime | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Экран «Задачи»: свои (поставил себе) и поставленные мне, которые я взял.

    Входящие (ещё не разобранные), «не моё» и отменённые постановщиком — не здесь.
    done=False — открытые; повторяющаяся задача — ближайшим вхождением с сегодняшнего дня.
    done=True — выполненные, последние сверху.
    """
    uid = _uid(user_id)
    lim = max(1, min(int(limit or 500), 2000))
    with _LOCK:
        rows = _conn().execute(
            f"""
            SELECT * FROM calendar_tasks
            WHERE (
                (owner_user_id = ? AND (assignee_user_id = '' OR assignee_user_id = owner_user_id))
                OR (
                  assignee_user_id = ? AND owner_user_id != assignee_user_id
                  AND IFNULL(outcome, '') != 'cancelled'
                  AND IFNULL(assignee_triage, '') NOT IN ({",".join("?" * len(TRIAGE_HIDDEN))})
                )
              )
              AND done = ?
            ORDER BY {"done_at DESC, id DESC" if done else "start_at ASC, id ASC"}
            LIMIT ?
            """,
            (uid, uid, *TRIAGE_HIDDEN, 1 if done else 0, lim),
        ).fetchall()
    out = [_row_to_dict(r) for r in rows]
    if done:
        return out
    now_dt = now or datetime.now(timezone.utc)
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    for i, t in enumerate(out):
        if str(t.get("recurrence") or "").strip():
            nxt = _expand_recurring_task(t, day_start, day_start + timedelta(days=400))
            if nxt:
                out[i] = nxt[0]
    out.sort(key=lambda t: (str(t.get("start_at") or ""), int(t.get("id") or 0)))
    return out


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
    result: str | None = None,
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
        "result": _clip(result, MAX_RESULT_LEN)
        if result is not None
        else existing.get("result") or "",
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
    reassigned = fields["assignee_user_id"] != existing["assignee_user_id"]
    if fields["done"] and not existing["done"]:
        done_at = _now_iso()
    elif not fields["done"]:
        done_at = ""
    else:
        done_at = str(existing.get("done_at") or "")
    with _LOCK:
        _conn().execute("UPDATE calendar_tasks SET done_at = ? WHERE id = ?", (done_at, int(existing["id"])))
        if reassigned:
            # Новый исполнитель — прежние «видел / в работе / напомнили» не про него.
            _conn().execute(
                "UPDATE calendar_tasks SET "
                + ", ".join(f"{c} = ''" for c in ASSIGNEE_STATE_COLUMNS)
                + " WHERE id = ?",
                (int(existing["id"]),),
            )
            if fields["assignee_user_id"] and fields["assignee_user_id"] != str(existing["owner_user_id"]):
                _conn().execute(
                    "UPDATE calendar_tasks SET assignee_triage = 'inbox' WHERE id = ?", (int(existing["id"]),)
                )
        elif (
            assignee_user_id not in (None, "")
            and existing.get("assignee_triage") == "declined"
            and uid != str(existing["assignee_user_id"])
        ):
            # Исполнитель сказал «не моё», а постановщик снова выбрал его — задача снова у него во входящих.
            _conn().execute(
                "UPDATE calendar_tasks SET assignee_triage = 'inbox', assignee_status_note = '',"
                " assignee_status_at = '' WHERE id = ?",
                (int(existing["id"]),),
            )
        _conn().execute(
            """
            UPDATE calendar_tasks SET
                title = ?, description = ?, result = ?, start_at = ?, end_at = ?,
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
                fields["result"],
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
    if fields["done"] and not existing["done"] and not reassigned:
        mark_triaged(existing)  # сдал из обхода разбора — значит, взял
    if bool(fields["done"]) != bool(existing["done"]):
        try:  # задача из пункта встречи — в саммари «Выполнено» / снова «поставлена»
            from assistant.services import focus_items

            by = str(existing.get("assignee_name") or "") if str(existing.get("assignee_user_id") or "") == uid else ""
            focus_items.task_done_changed(existing["owner_user_id"], int(existing["id"]), bool(fields["done"]), by)
        except Exception as e:
            print(f"[calendar_tasks] focus done sync failed task={existing['id']} err={e!r}")
    if str(existing.get("note_id") or "").isdigit() and (
        bool(fields["done"]) != bool(existing["done"])
        or fields["start_at"] != existing["start_at"]
        or fields["assignee_user_id"] != existing["assignee_user_id"]
    ):
        _meeting_task_changed(int(existing["note_id"]))
    if existing.get("parent_id") and (
        bool(fields["done"]) != bool(existing["done"])
        or fields["title"] != existing["title"]
        or fields["assignee_user_id"] != existing["assignee_user_id"]
    ):
        _parent_changed(int(existing["parent_id"]), actor_user_id=uid)
    if fields["title"] != existing["title"]:
        try:
            from assistant.services import focus_items

            focus_items.task_renamed(existing["owner_user_id"], int(existing["id"]), fields["title"])
        except Exception as e:  # подпись чипа — не повод провалить правку задачи
            print(f"[calendar_tasks] chip rename failed task={existing['id']} err={e!r}")
    return _get_task_row(int(existing["id"]))


def assignee_state(task: dict[str, Any] | None) -> dict[str, str]:
    """Что исполнитель сделал с поручением — для автора в «Поручено другим»."""
    t = task or {}
    status = str(t.get("assignee_status") or "")
    return {
        "seen_at": str(t.get("assignee_seen_at") or ""),
        "status": status if status in ASSIGNEE_STATUSES else "",
        "status_note": str(t.get("assignee_status_note") or ""),
        "status_at": str(t.get("assignee_status_at") or ""),
        "nudged_at": str(t.get("last_nudged_at") or ""),
        "triage": str(t.get("assignee_triage") or ""),
        "snoozed_until": str(t.get("assignee_snoozed_until") or ""),
    }


def _set_columns(task_id: int, values: dict[str, str]) -> Optional[dict[str, Any]]:
    cols = [c for c in values if c in ASSIGNEE_STATE_COLUMNS]
    if not cols:
        return _get_task_row(task_id)
    with _LOCK:
        _conn().execute(
            "UPDATE calendar_tasks SET " + ", ".join(f"{c} = ?" for c in cols) + " WHERE id = ?",
            tuple(values[c] for c in cols) + (int(task_id),),
        )
        _conn().commit()
    row = _get_task_row(task_id)
    if row and str(row.get("note_id") or "").isdigit() and {"assignee_status", "assignee_triage"} & set(cols):
        _meeting_task_changed(int(row["note_id"]))
    return row


def _assignee_task(viewer_id: int | str, task_id: int | str) -> Optional[dict[str, Any]]:
    """Поручение, где viewer — исполнитель (не автор)."""
    task = get_task_for_user(viewer_id, task_id)
    if not task or not task_is_delegated(task):
        return None
    if str(task.get("assignee_user_id") or "") != _uid(viewer_id):
        return None
    return task


# ——— Лента поручения: события и переписка постановщика с исполнителем ———
EVENT_KINDS = (
    "created", "seen", "in_progress", "message", "nudge", "done", "returned", "accepted", "cancelled",
    "taken", "snoozed", "declined", "insisted",
)
MAX_MESSAGE_LEN = 2000


def add_event(
    task_id: int | str,
    kind: str,
    *,
    actor_user_id: int | str = "",
    actor_name: str = "",
    text: str = "",
    at: str = "",
    meta: dict[str, Any] | None = None,
) -> None:
    tid = _parse_task_id(task_id)
    if tid is None or kind not in EVENT_KINDS:
        return
    with _LOCK:
        _conn().execute(
            "INSERT INTO task_events (task_id, at, actor_user_id, actor_name, kind, text, meta_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, at or _now_iso(), str(actor_user_id or ""), _clip(actor_name, 120), kind,
             _clip(text, MAX_MESSAGE_LEN), json.dumps(meta or {}, ensure_ascii=False)),
        )
        _conn().commit()
        who = _conn().execute(
            "SELECT owner_user_id, assignee_user_id FROM calendar_tasks WHERE id = ?", (tid,)
        ).fetchone()
    if who:
        from assistant.realtime import notify as realtime_notify

        realtime_notify.task_changed(tid, [who[0], who[1]], actor_user_id=actor_user_id)


def _meeting_task_changed(note_id: int) -> None:
    """Поручение из встречи взяли / в работе / отказ / сдали — статус в таблице задач встречи."""

    def run() -> None:
        try:
            from assistant.services import meeting_tasks
            from assistant.stores import notes as notes_store

            note = notes_store.get_note_by_id(note_id)
            if note:
                meeting_tasks.refresh_note(str(note.get("user_id") or ""), note_id)
        except Exception as e:
            print(f"[calendar_tasks] meeting refresh failed note={note_id} err={e!r}")

    threading.Thread(target=run, name=f"meeting-tasks-{note_id}", daemon=True).start()


def list_events(task: dict[str, Any]) -> list[dict[str, Any]]:
    """События поручения по времени. Для задач, поставленных до ленты, достраиваем из полей задачи."""
    tid = int(task["id"])
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY at ASC, id ASC", (tid,)
        ).fetchall()
    out = [
        {
            "at": str(r["at"]),
            "kind": str(r["kind"]),
            "actor_user_id": str(r["actor_user_id"] or ""),
            "actor_name": str(r["actor_name"] or ""),
            "text": str(r["text"] or ""),
            "meta": _event_meta(r),
        }
        for r in rows
    ]
    kinds = {e["kind"] for e in out}
    owner = str(task.get("owner_user_id") or "")
    assignee = str(task.get("assignee_user_id") or "")
    legacy: list[dict[str, Any]] = []
    if "created" not in kinds and task.get("created_at"):
        legacy.append({"at": str(task["created_at"]), "kind": "created", "actor_user_id": owner, "actor_name": "", "text": ""})
    if "seen" not in kinds and task.get("assignee_seen_at"):
        legacy.append({"at": str(task["assignee_seen_at"]), "kind": "seen", "actor_user_id": assignee,
                       "actor_name": str(task.get("assignee_name") or ""), "text": ""})
    if "message" not in kinds and task.get("assignee_status") == "question" and task.get("assignee_status_note"):
        legacy.append({"at": str(task.get("assignee_status_at") or task.get("updated_at") or ""), "kind": "message",
                       "actor_user_id": assignee, "actor_name": str(task.get("assignee_name") or ""),
                       "text": str(task["assignee_status_note"])})
    if "nudge" not in kinds and task.get("last_nudged_at"):
        legacy.append({"at": str(task["last_nudged_at"]), "kind": "nudge", "actor_user_id": owner, "actor_name": "", "text": ""})
    if "done" not in kinds and task.get("done") and task.get("done_at"):
        legacy.append({"at": str(task["done_at"]), "kind": "done", "actor_user_id": assignee,
                       "actor_name": str(task.get("assignee_name") or ""), "text": str(task.get("result") or "")})
    if legacy:
        # Достроенные события старше записанных: при равном времени — первыми (сортировка устойчивая).
        out = sorted(legacy + out, key=lambda e: e["at"])
    return out


def discussion_by_task(task_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Переписка поручений одним запросом: сообщения и комментарии возврата (с текстом), по времени."""
    ids = sorted({int(t) for t in task_ids if str(t).isdigit()})
    out: dict[int, list[dict[str, Any]]] = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        with _LOCK:
            rows = _conn().execute(
                f"SELECT task_id, at, actor_user_id, actor_name, kind, text FROM task_events"
                f" WHERE task_id IN ({','.join('?' * len(part))}) AND kind IN ('message', 'returned')"
                f" AND TRIM(COALESCE(text, '')) != '' ORDER BY at ASC, id ASC",
                part,
            ).fetchall()
        for r in rows:
            out.setdefault(int(r["task_id"]), []).append({
                "at": str(r["at"] or ""), "kind": str(r["kind"]), "actor_user_id": str(r["actor_user_id"] or ""),
                "actor_name": str(r["actor_name"] or ""), "text": str(r["text"] or ""),
            })
    return out


def _event_meta(row: sqlite3.Row) -> dict[str, Any]:
    if "meta_json" not in row.keys():
        return {}
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def users_share_task(a: int | str, b: int | str) -> bool:
    """Постановщик и исполнитель одного поручения (для фото в «Поручено другим»)."""
    ua, ub = _uid(a), _uid(b)
    if not ua or not ub:
        return False
    with _LOCK:
        row = _conn().execute(
            "SELECT 1 FROM calendar_tasks WHERE (owner_user_id = ? AND assignee_user_id = ?)"
            " OR (owner_user_id = ? AND assignee_user_id = ?) LIMIT 1",
            (ua, ub, ub, ua),
        ).fetchone()
    return row is not None


def _owner_task(owner_id: int | str, task_id: int | str) -> Optional[dict[str, Any]]:
    task = get_task_for_user(owner_id, task_id)
    if not task or not task_is_delegated(task) or str(task.get("owner_user_id") or "") != _uid(owner_id):
        return None
    return task


def post_message(
    viewer_id: int | str,
    task_id: int | str,
    text: str,
    *,
    actor_name: str = "",
    meta: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    """Сообщение в обсуждении поручения (текст, вложения, ссылки на задачи/заметки/встречи).
    От исполнителя — это вопрос (поручение «ждёт» постановщика), ответ постановщика вопрос снимает."""
    body = _clip(text, MAX_MESSAGE_LEN)
    if not body and not ((meta or {}).get("files") or (meta or {}).get("refs")):
        raise ValueError("Напишите сообщение")
    task = get_task_for_user(viewer_id, task_id)
    if not task or not task_is_delegated(task):
        return None
    viewer = _uid(viewer_id)
    tid = int(task["id"])
    if task.get("assignee_status") == "question" and task.get("assignee_status_note"):
        with _LOCK:
            has_msg = _conn().execute(
                "SELECT 1 FROM task_events WHERE task_id = ? AND kind = 'message' LIMIT 1", (tid,)
            ).fetchone()
        if not has_msg:
            # Вопрос задан до ленты (жил только в статусе) — сохраняем, иначе ответ его сотрёт.
            add_event(tid, "message", actor_user_id=task.get("assignee_user_id") or "",
                      actor_name=str(task.get("assignee_name") or ""), text=str(task["assignee_status_note"]),
                      at=str(task.get("assignee_status_at") or ""))
    add_event(tid, "message", actor_user_id=viewer, actor_name=actor_name, text=body, meta=meta)
    now = _now_iso()
    if viewer == str(task.get("assignee_user_id") or ""):
        return _set_columns(tid, {
            "assignee_status": "question",
            "assignee_status_note": _clip(body or "Вложение", MAX_STATUS_NOTE_LEN),
            "assignee_status_at": now,
            "assignee_seen_at": task.get("assignee_seen_at") or now,
        })
    if task.get("assignee_status") == "question":
        return _set_columns(tid, {"assignee_status": "in_progress", "assignee_status_note": "", "assignee_status_at": now})
    return _get_task_row(tid)


def accept_task(owner_id: int | str, task_id: int | str, *, actor_name: str = "") -> Optional[dict[str, Any]]:
    """«Принять» — поручение закрыто и уходит в «Принятые»."""
    task = _owner_task(owner_id, task_id)
    if not task:
        return None
    now = _now_iso()
    with _LOCK:
        _conn().execute(
            "UPDATE calendar_tasks SET done = 1, owner_archived = 1, outcome = 'accepted', accepted_at = ?,"
            " done_at = CASE WHEN done_at = '' THEN ? ELSE done_at END, updated_at = ? WHERE id = ?",
            (now, now, now, int(task["id"])),
        )
        _conn().commit()
    add_event(task["id"], "accepted", actor_user_id=owner_id, actor_name=actor_name)
    return _get_task_row(int(task["id"]))


def return_task(owner_id: int | str, task_id: int | str, comment: str = "", *, actor_name: str = "") -> Optional[dict[str, Any]]:
    """«Вернуть» — снова в работе (из «Сделано» или из «Принятых»), с комментарием исполнителю."""
    task = _owner_task(owner_id, task_id)
    if not task:
        return None
    now = _now_iso()
    with _LOCK:
        _conn().execute(
            "UPDATE calendar_tasks SET done = 0, owner_archived = 0, outcome = '', accepted_at = '', done_at = '',"
            " assignee_status = 'in_progress', assignee_status_note = '', assignee_status_at = ?, updated_at = ? WHERE id = ?",
            (now, now, int(task["id"])),
        )
        _conn().commit()
    add_event(task["id"], "returned", actor_user_id=owner_id, actor_name=actor_name, text=comment)
    return _get_task_row(int(task["id"]))


def cancel_task(owner_id: int | str, task_id: int | str, *, actor_name: str = "") -> Optional[dict[str, Any]]:
    """«Отменить поручение» — в «Принятые» с пометкой «отменено», у исполнителя пропадает."""
    task = _owner_task(owner_id, task_id)
    if not task:
        return None
    now = _now_iso()
    with _LOCK:
        _conn().execute(
            "UPDATE calendar_tasks SET owner_archived = 1, outcome = 'cancelled', accepted_at = ?, updated_at = ? WHERE id = ?",
            (now, now, int(task["id"])),
        )
        _conn().commit()
    add_event(task["id"], "cancelled", actor_user_id=owner_id, actor_name=actor_name)
    return _get_task_row(int(task["id"]))


def mark_seen(viewer_id: int | str, task_id: int | str) -> Optional[dict[str, Any]]:
    """Исполнитель открыл поручение — запоминаем первый раз (дальше не перезаписываем)."""
    task = _assignee_task(viewer_id, task_id)
    if not task:
        return None
    if task.get("assignee_seen_at"):
        return task
    add_event(task["id"], "seen", actor_user_id=viewer_id, actor_name=str(task.get("assignee_name") or ""))
    return _set_columns(int(task["id"]), {"assignee_seen_at": _now_iso()})


def set_assignee_status(
    viewer_id: int | str, task_id: int | str, status: str, note: str = ""
) -> Optional[dict[str, Any]]:
    """«Взял в работу» / «Есть вопрос» (с текстом) / сброс — только исполнитель.

    Своя задача (исполнитель — сам автор): только «в работе» / сброс, без ленты поручения."""
    status = str(status or "").strip()
    if status not in ASSIGNEE_STATUSES:
        raise ValueError("Неизвестный статус")
    task = _assignee_task(viewer_id, task_id)
    if not task:
        own = get_task_for_user(viewer_id, task_id)
        if not own or task_is_delegated(own) or str(own.get("owner_user_id") or "") != _uid(viewer_id):
            return None
        if status == "question":
            raise ValueError("Вопрос задаётся по поручению")
        return _set_columns(int(own["id"]), {"assignee_status": status, "assignee_status_note": "",
                                             "assignee_status_at": _now_iso() if status else ""})
    note = _clip(note, MAX_STATUS_NOTE_LEN) if status == "question" else ""
    if status == "question" and not note:
        raise ValueError("Напишите вопрос")
    if status == "question":
        add_event(task["id"], "message", actor_user_id=viewer_id, actor_name=str(task.get("assignee_name") or ""), text=note)
    elif status == "in_progress" and task.get("assignee_status") != "in_progress":
        add_event(task["id"], "in_progress", actor_user_id=viewer_id, actor_name=str(task.get("assignee_name") or ""))
    if status == "in_progress":
        mark_triaged(task)
    now = _now_iso()
    return _set_columns(
        int(task["id"]),
        {
            "assignee_status": status,
            "assignee_status_note": note,
            "assignee_status_at": now if status else "",
            "assignee_seen_at": task.get("assignee_seen_at") or now,
        },
    )


def _set_triage(task: dict[str, Any], state: str, **extra: str) -> Optional[dict[str, Any]]:
    now = _now_iso()
    values = {
        "assignee_triage": state,
        "assignee_snoozed_until": extra.pop("snoozed_until", ""),
        "assignee_seen_at": task.get("assignee_seen_at") or now,
        **extra,
    }
    return _set_columns(int(task["id"]), values)


def triage_accept(
    viewer_id: int | str,
    task_id: int | str,
    *,
    actor_name: str = "",
    start_at: str | datetime | None = None,
    end_at: str | datetime | None = None,
    all_day: bool | None = None,
    title: str | None = None,
    description: str | None = None,
    tz: ZoneInfo | None = None,
) -> Optional[dict[str, Any]]:
    """«Беру» (и «Изменить»): поручение встаёт в календарь исполнителя — на предложенный или свой срок."""
    task = _assignee_task(viewer_id, task_id)
    if not task:
        return None
    if any(v is not None for v in (start_at, end_at, all_day, title, description)):
        update_task(
            viewer_id, task_id, start_at=start_at, end_at=end_at, all_day=all_day,
            title=title, description=description, tz=tz,
            schedule_actor={"by_user_id": _uid(viewer_id), "by_name": actor_name},
        )
    if str(task.get("assignee_triage") or "") != "accepted":
        add_event(task["id"], "taken", actor_user_id=viewer_id, actor_name=actor_name)
    return _set_triage(task, "accepted", assignee_status_note="")


def triage_snooze(
    viewer_id: int | str, task_id: int | str, *, tz: ZoneInfo | None = None, now: datetime | None = None
) -> Optional[dict[str, Any]]:
    """«Позже» — убрать из стопки до завтрашнего утра (SNOOZE_HOUR по поясу пользователя)."""
    task = _assignee_task(viewer_id, task_id)
    if not task:
        return None
    zone = tz or timezone.utc
    local_now = (now or datetime.now(timezone.utc)).astimezone(zone)
    until = datetime.combine(local_now.date() + timedelta(days=1), time(SNOOZE_HOUR, 0), tzinfo=zone)
    if str(task.get("assignee_triage") or "") != "snoozed":
        add_event(task["id"], "snoozed", actor_user_id=viewer_id, actor_name=str(task.get("assignee_name") or ""))
    return _set_triage(task, "snoozed", snoozed_until=_iso(until))


def triage_decline(
    viewer_id: int | str, task_id: int | str, note: str = "", *, actor_name: str = ""
) -> Optional[dict[str, Any]]:
    """«Не моё» — поручение возвращается постановщику с комментарием; у исполнителя пропадает."""
    task = _assignee_task(viewer_id, task_id)
    if not task:
        return None
    note = _clip(note, MAX_STATUS_NOTE_LEN)
    add_event(task["id"], "declined", actor_user_id=viewer_id, actor_name=actor_name, text=note)
    return _set_triage(task, "declined", assignee_status_note=note, assignee_status_at=_now_iso())


def triage_restore(viewer_id: int | str, task_id: int | str, state: str, snoozed_until: str = "") -> Optional[dict[str, Any]]:
    """Отмена последнего действия в разборе — вернуть прежнее состояние."""
    if state not in TRIAGE_STATES:
        raise ValueError("Неизвестное состояние")
    task = _assignee_task(viewer_id, task_id)
    if not task:
        return None
    extra = {"assignee_status_note": ""} if task.get("assignee_triage") == "declined" else {}
    return _set_triage(task, state, snoozed_until=snoozed_until, **extra)


def mark_triaged(task: dict[str, Any] | None) -> None:
    """Исполнитель работает с поручением в обход разбора (взял в работу, сдал) — значит, взял."""
    if task and str(task.get("assignee_triage") or "") in ("inbox", "snoozed"):
        _set_columns(int(task["id"]), {"assignee_triage": "accepted", "assignee_snoozed_until": ""})


def insist_task(owner_id: int | str, task_id: int | str, comment: str = "", *, actor_name: str = "") -> Optional[dict[str, Any]]:
    """Исполнитель нажал «Не моё», постановщик настаивает — задача снова у него во входящих."""
    task = _owner_task(owner_id, task_id)
    if not task or str(task.get("assignee_triage") or "") != "declined":
        return None
    add_event(task["id"], "insisted", actor_user_id=owner_id, actor_name=actor_name, text=_clip(comment, MAX_MESSAGE_LEN))
    return _set_columns(int(task["id"]), {
        "assignee_triage": "inbox", "assignee_snoozed_until": "", "assignee_status_note": "", "assignee_status_at": "",
    })


def list_inbox_user_ids() -> list[int]:
    with _LOCK:
        raw = [
            str(r[0] or "").strip()
            for r in _conn().execute(
                "SELECT DISTINCT assignee_user_id FROM calendar_tasks"
                " WHERE assignee_user_id != owner_user_id AND done = 0"
                " AND IFNULL(assignee_triage, '') IN ('inbox', 'snoozed')"
            ).fetchall()
        ]
    return _int_user_ids(raw)


def list_inbox(user_id: int | str, *, now: datetime | None = None) -> dict[str, Any]:
    """Входящие поручения исполнителя: новые + отложенные, у которых наступило утро.

    Отложенные до завтра отдаются только числом (snoozed)."""
    uid = _uid(user_id)
    now_iso = _iso(now or datetime.now(timezone.utc))
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT * FROM calendar_tasks
            WHERE assignee_user_id = ? AND owner_user_id != assignee_user_id
              AND done = 0 AND IFNULL(outcome, '') != 'cancelled'
              AND IFNULL(assignee_triage, '') IN ('inbox', 'snoozed')
            ORDER BY created_at ASC, id ASC
            """,
            (uid,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    snoozed = 0
    for r in rows:
        t = _row_to_dict(r)
        if t["assignee_triage"] == "snoozed" and str(t.get("assignee_snoozed_until") or "") > now_iso:
            snoozed += 1
            continue
        items.append(t)
    # Сначала новые, потом вернувшиеся из «Позже».
    items.sort(key=lambda t: (t["assignee_triage"] == "snoozed", str(t.get("created_at") or ""), t["id"]))
    return {"items": _bundle_inbox(items), "snoozed": snoozed}


def _bundle_inbox(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Подзадачи одной общей задачи — одной карточкой.

    Общая задача тоже во входящих этого человека → её подзадачи внутри неё (bundle_ids).
    Нет → подзадачи-соседки собираются под первой из них."""
    by_id = {int(t["id"]): t for t in items}
    lead_of: dict[int, int] = {}
    for t in items:
        top = next((int(a["id"]) for a in reversed(ancestors(t)) if int(a["id"]) in by_id), None)
        if top is not None:
            lead_of[int(t["id"])] = top
    first_sibling: dict[int, int] = {}
    for t in items:
        tid, pid = int(t["id"]), t.get("parent_id")
        if tid in lead_of or not pid:
            continue
        lead = first_sibling.setdefault(int(pid), tid)
        if lead != tid:
            lead_of[tid] = lead
    out = []
    for t in items:
        if int(t["id"]) in lead_of:
            continue
        t["bundle_ids"] = [int(x["id"]) for x in items if lead_of.get(int(x["id"])) == int(t["id"])]
        out.append(t)
    return out


def mark_nudged(task_id: int | str, *, actor_user_id: int | str = "", actor_name: str = "") -> Optional[dict[str, Any]]:
    tid = _parse_task_id(task_id)
    if tid is None:
        return None
    add_event(tid, "nudge", actor_user_id=actor_user_id, actor_name=actor_name)
    return _set_columns(tid, {"last_nudged_at": _now_iso()})


def archive_done_posted(owner_user_id: int | str) -> int:
    """«Принять всё»: выполненные поручения автора — в его архив."""
    uid = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE calendar_tasks SET owner_archived = 1, outcome = 'accepted', accepted_at = ?, updated_at = ?
            WHERE owner_user_id = ?
              AND assignee_user_id != ''
              AND assignee_user_id != owner_user_id
              AND done = 1
              AND IFNULL(owner_archived, 0) = 0
            """,
            (_now_iso(), _now_iso(), uid),
        )
        _conn().commit()
        return int(cur.rowcount or 0)


def delete_task(owner_user_id: int | str, task_id: int | str) -> bool:
    existing = get_task_for_user(owner_user_id, task_id)
    if not existing:
        return False
    with _LOCK:
        # Подзадачи удалённой задачи встают на её место, а не пропадают.
        _conn().execute(
            "UPDATE calendar_tasks SET parent_id = ? WHERE parent_id = ?",
            (existing.get("parent_id"), int(existing["id"])),
        )
        cur = _conn().execute(
            "DELETE FROM calendar_tasks WHERE id = ?",
            (int(existing["id"]),),
        )
        _conn().commit()
        deleted = cur.rowcount > 0
    if deleted and existing.get("parent_id"):
        _parent_changed(int(existing["parent_id"]), actor_user_id=owner_user_id)
    if deleted and _deleted_by_assignee(existing, owner_user_id):
        # Исполнитель удалил поручение — постановщик иначе узнал бы об этом, только не найдя его в «Поставленных».
        try:
            from assistant.lib.task_notify import notify_task_deleted_by_assignee

            notify_task_deleted_by_assignee(task=existing)
        except Exception as e:
            print(f"[calendar_tasks] notify deleted task={existing.get('id')} err={e!r}")
    return deleted


def _deleted_by_assignee(task: dict[str, Any], actor_id: int | str) -> bool:
    return (
        task_is_delegated(task)
        and _uid(actor_id) == str(task.get("assignee_user_id") or "")
        # Отменённое постановщиком или уже принятое — ему об этом знать незачем.
        and str(task.get("outcome") or "") not in ("cancelled", "accepted")
    )


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
