"""SQLite-хранилище задач записи Zoom-встреч через meeting bot."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.RLock()

_STATUS_SCHEDULED = "scheduled"
_STATUS_JOINING = "joining"
_STATUS_RECORDING = "recording"
_STATUS_PROCESSING = "processing"
_STATUS_DONE = "done"
_STATUS_FAILED = "failed"
_STATUS_MERGED = "merged"

_ACTIVE_STATUSES = (
    _STATUS_SCHEDULED,
    _STATUS_JOINING,
    _STATUS_RECORDING,
    _STATUS_PROCESSING,
)


def _project_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _db_path() -> Path:
    raw = os.getenv("MEETING_RECORDINGS_DB_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_project_dir() / p).resolve()
        else:
            p = p.resolve()
        return p
    return (_project_dir() / "data" / "meeting_recordings.sqlite").resolve()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meeting_recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                meeting_url TEXT NOT NULL,
                native_meeting_id TEXT NOT NULL,
                passcode TEXT,
                start_at_utc TEXT,
                status TEXT NOT NULL,
                vexa_meeting_id INTEGER,
                vexa_recording_id INTEGER,
                journal_event_id INTEGER,
                error_message TEXT,
                meta_json TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mr_user ON meeting_recordings(telegram_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mr_status ON meeting_recordings(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mr_native ON meeting_recordings(native_meeting_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meeting_recording_subscribers (
                job_id INTEGER NOT NULL,
                telegram_user_id INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL,
                PRIMARY KEY (job_id, telegram_user_id)
            )
            """
        )
        conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    raw = row["meta_json"]
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                meta = parsed
        except Exception:
            meta = {}
    return {
        "id": int(row["id"]),
        "telegram_user_id": int(row["telegram_user_id"]),
        "source": str(row["source"] or ""),
        "topic": str(row["topic"] or ""),
        "meeting_url": str(row["meeting_url"] or ""),
        "native_meeting_id": str(row["native_meeting_id"] or ""),
        "passcode": row["passcode"],
        "start_at_utc": row["start_at_utc"],
        "status": str(row["status"] or ""),
        "vexa_meeting_id": row["vexa_meeting_id"],
        "vexa_recording_id": row["vexa_recording_id"],
        "journal_event_id": row["journal_event_id"],
        "error_message": row["error_message"],
        "meta": meta,
        "created_at_utc": row["created_at_utc"],
        "updated_at_utc": row["updated_at_utc"],
    }


def create_job(
    *,
    telegram_user_id: int,
    source: str,
    topic: str,
    meeting_url: str,
    native_meeting_id: str,
    passcode: str | None = None,
    start_at_utc: datetime | None = None,
    status: str = _STATUS_SCHEDULED,
    meta: dict[str, Any] | None = None,
) -> int:
    init_db()
    now = _now_iso()
    start_s = start_at_utc.astimezone(timezone.utc).isoformat() if start_at_utc else None
    with _lock:
        with _connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO meeting_recordings (
                    telegram_user_id, source, topic, meeting_url, native_meeting_id,
                    passcode, start_at_utc, status, meta_json, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(telegram_user_id),
                    (source or "zoom_link").strip(),
                    (topic or "Встреча Zoom").strip(),
                    (meeting_url or "").strip(),
                    (native_meeting_id or "").strip(),
                    (passcode or None),
                    start_s,
                    (status or _STATUS_SCHEDULED).strip(),
                    json.dumps(meta or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    init_db()
    allowed = {
        "topic",
        "status",
        "vexa_meeting_id",
        "vexa_recording_id",
        "journal_event_id",
        "error_message",
        "meta",
        "start_at_utc",
    }
    parts: list[str] = []
    values: list[Any] = []
    for key, val in fields.items():
        if key not in allowed:
            continue
        if key == "meta":
            parts.append("meta_json = ?")
            values.append(json.dumps(val if isinstance(val, dict) else {}, ensure_ascii=False))
        elif key == "start_at_utc" and isinstance(val, datetime):
            parts.append("start_at_utc = ?")
            values.append(val.astimezone(timezone.utc).isoformat())
        else:
            parts.append(f"{key} = ?")
            values.append(val)
    if not parts:
        return
    parts.append("updated_at_utc = ?")
    values.append(_now_iso())
    values.append(int(job_id))
    with _lock:
        with _connect() as conn:
            conn.execute(
                f"UPDATE meeting_recordings SET {', '.join(parts)} WHERE id = ?",
                values,
            )
            conn.commit()


def get_job(job_id: int) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM meeting_recordings WHERE id = ?",
            (int(job_id),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_active_job_by_native_meeting_id(
    native_meeting_id: str,
    *,
    exclude_job_id: int | None = None,
) -> dict[str, Any] | None:
    mid = (native_meeting_id or "").strip()
    if not mid:
        return None
    init_db()
    placeholders = ",".join("?" for _ in _ACTIVE_STATUSES)
    params: list[Any] = [mid, *_ACTIVE_STATUSES]
    exclude_sql = ""
    if exclude_job_id is not None:
        exclude_sql = " AND id != ?"
        params.append(int(exclude_job_id))
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM meeting_recordings
            WHERE native_meeting_id = ?
              AND status IN ({placeholders})
              {exclude_sql}
            ORDER BY id ASC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_job_by_native_meeting_id(native_meeting_id: str) -> dict[str, Any] | None:
    mid = (native_meeting_id or "").strip()
    if not mid:
        return None
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM meeting_recordings
            WHERE native_meeting_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (mid,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_job_by_vexa_meeting_id(vexa_meeting_id: int) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM meeting_recordings
            WHERE vexa_meeting_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(vexa_meeting_id),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs_for_user(telegram_user_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM meeting_recordings
            WHERE telegram_user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(telegram_user_id), max(1, min(limit, 200))),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_subscriber(job_id: int, telegram_user_id: int) -> bool:
    """Добавить подписчика на общую запись. False, если уже был."""
    init_db()
    jid = int(job_id)
    uid = int(telegram_user_id)
    job = get_job(jid)
    if not job:
        return False
    if int(job.get("telegram_user_id") or 0) == uid:
        return False
    now = _now_iso()
    with _lock:
        with _connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO meeting_recording_subscribers
                (job_id, telegram_user_id, created_at_utc)
                VALUES (?, ?, ?)
                """,
                (jid, uid, now),
            )
            conn.commit()
            return int(cur.rowcount or 0) > 0


def list_recipient_user_ids(job_id: int) -> list[int]:
    """Владелец job + все подписчики (уникально, в порядке добавления)."""
    job = get_job(job_id)
    if not job:
        return []
    out: list[int] = []
    seen: set[int] = set()
    owner = int(job.get("telegram_user_id") or 0)
    if owner > 0:
        seen.add(owner)
        out.append(owner)
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT telegram_user_id FROM meeting_recording_subscribers
            WHERE job_id = ?
            ORDER BY created_at_utc ASC, telegram_user_id ASC
            """,
            (int(job_id),),
        ).fetchall()
    for row in rows:
        uid = int(row["telegram_user_id"])
        if uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def create_or_join_job(
    *,
    telegram_user_id: int,
    source: str,
    topic: str,
    meeting_url: str,
    native_meeting_id: str,
    passcode: str | None = None,
    start_at_utc: datetime | None = None,
) -> tuple[int, bool]:
    """Создать job или подписать пользователя на активную запись той же встречи.

    Returns (job_id, joined_existing).
    """
    init_db()
    uid = int(telegram_user_id)
    mid = (native_meeting_id or "").strip()
    with _lock:
        existing = find_active_job_by_native_meeting_id(mid)
        if existing:
            from assistant.services import meeting_record_reconcile as reconcile

            if reconcile.dedupe_action(existing) == "subscribe":
                eid = int(existing["id"])
                if int(existing.get("telegram_user_id") or 0) != uid:
                    add_subscriber(eid, uid)
                return eid, True
        job_id = create_job(
            telegram_user_id=uid,
            source=source,
            topic=topic,
            meeting_url=meeting_url,
            native_meeting_id=mid,
            passcode=passcode,
            start_at_utc=start_at_utc,
            status=_STATUS_SCHEDULED,
        )
        return job_id, False


def list_jobs_by_statuses(statuses: tuple[str, ...], *, limit: int = 50) -> list[dict[str, Any]]:
    if not statuses:
        return []
    init_db()
    placeholders = ",".join("?" for _ in statuses)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM meeting_recordings
            WHERE status IN ({placeholders})
            ORDER BY updated_at_utc ASC
            LIMIT ?
            """,
            (*statuses, max(1, min(limit, 200))),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_due_scheduled(*, before_utc: datetime) -> list[dict[str, Any]]:
    init_db()
    cutoff = before_utc.astimezone(timezone.utc).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM meeting_recordings
            WHERE status = ?
              AND (start_at_utc IS NULL OR start_at_utc <= ?)
            ORDER BY COALESCE(start_at_utc, created_at_utc) ASC
            LIMIT 100
            """,
            (_STATUS_SCHEDULED, cutoff),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
