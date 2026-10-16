"""Связь встреч календаря с заметками и транскрипциями («материалы встречи»).

Связь принадлежит тому, кто её сделал (owner_user_id): материалы встречи видит только он.
Встреча хранится снимком (тема, время, участники, ссылка) — заметка показывает плашку
встречи, даже если событие потом удалили из календаря. Ключ встречи — calendar_id:event_id
(для повторяющихся встреч event_id — id конкретного экземпляра).

Участники заметки, добавленные вручную (note_people), живут отдельно — так транскрипцию
можно связать с людьми и без встречи.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    from assistant.stores import notes as notes_store

    path = str(notes_store._db_path())  # та же база, что у заметок: можно JOIN с local_notes
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    notes_store._conn()  # таблица local_notes уже создана
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS meeting_links (
            owner_user_id TEXT NOT NULL,
            note_id INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            calendar_id TEXT NOT NULL DEFAULT 'primary',
            event_id TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            start_iso TEXT NOT NULL DEFAULT '',
            end_iso TEXT NOT NULL DEFAULT '',
            meet_url TEXT NOT NULL DEFAULT '',
            attendees_json TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, note_id)
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_meeting_links_event ON meeting_links(owner_user_id, event_key)"
    )
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS note_people (
            owner_user_id TEXT NOT NULL,
            note_id INTEGER NOT NULL,
            person_key TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, note_id, person_key)
        )
        """
    )
    _CONN.commit()
    return _CONN


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def event_key(calendar_id: Any, event_id: Any) -> str:
    return f"{str(calendar_id or 'primary').strip() or 'primary'}:{str(event_id or '').strip()}"


def _clean_attendees(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        email = str(a.get("email") or "").strip().lower()
        name = str(a.get("name") or a.get("displayName") or a.get("display_name") or "").strip()
        key = email or name.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"email": email, "name": name or email})
    return out[:60]


def _time_str(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("dateTime") or v.get("date") or "").strip()
    return str(v or "").strip()


def meeting_from_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Снимок встречи из события в формате API календаря миниаппа."""
    return {
        "calendar_id": str(ev.get("calendar_id") or ev.get("_calendarId") or "primary"),
        "event_id": str(ev.get("id") or ev.get("event_id") or "").strip(),
        "summary": str(ev.get("summary") or "").strip()[:300],
        "start_iso": _time_str(ev.get("start") if ev.get("start") is not None else ev.get("start_iso")),
        "end_iso": _time_str(ev.get("end") if ev.get("end") is not None else ev.get("end_iso")),
        "meet_url": str(ev.get("meet_url") or "").strip()[:500],
        "attendees": _clean_attendees(ev.get("attendees")),
    }


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    try:
        d["attendees"] = json.loads(d.pop("attendees_json") or "[]")
    except ValueError:
        d["attendees"] = []
    return d


def link(owner_user_id: Any, note_id: Any, meeting: dict[str, Any], *, source: str = "manual") -> dict[str, Any]:
    m = meeting_from_event(meeting)
    if not m.get("event_id"):
        raise ValueError("Нет id встречи")
    key = event_key(m.get("calendar_id"), m.get("event_id"))
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO meeting_links (
                owner_user_id, note_id, event_key, calendar_id, event_id, summary,
                start_iso, end_iso, meet_url, attendees_json, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, note_id) DO UPDATE SET
                event_key = excluded.event_key, calendar_id = excluded.calendar_id,
                event_id = excluded.event_id, summary = excluded.summary,
                start_iso = excluded.start_iso, end_iso = excluded.end_iso,
                meet_url = excluded.meet_url, attendees_json = excluded.attendees_json,
                source = excluded.source
            """,
            (
                str(owner_user_id),
                int(note_id),
                key,
                str(m.get("calendar_id") or "primary"),
                str(m.get("event_id")),
                str(m.get("summary") or ""),
                str(m.get("start_iso") or ""),
                str(m.get("end_iso") or ""),
                str(m.get("meet_url") or ""),
                json.dumps(_clean_attendees(m.get("attendees")), ensure_ascii=False),
                str(source or "manual"),
                _now(),
            ),
        )
        conn.commit()
    out = get_for_note(owner_user_id, note_id)
    assert out is not None
    return out


def unlink(owner_user_id: Any, note_id: Any) -> bool:
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM meeting_links WHERE owner_user_id = ? AND note_id = ?",
            (str(owner_user_id), int(note_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def get_for_note(owner_user_id: Any, note_id: Any) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute(
            "SELECT * FROM meeting_links WHERE owner_user_id = ? AND note_id = ?",
            (str(owner_user_id), int(note_id)),
        ).fetchone()
    return _row(r) if r else None


def for_notes(owner_user_id: Any, note_ids: list[Any]) -> dict[int, dict[str, Any]]:
    ids = [int(x) for x in note_ids if str(x).lstrip("-").isdigit()]
    if not ids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    with _LOCK:
        conn = _conn()
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            rows = conn.execute(
                "SELECT * FROM meeting_links WHERE owner_user_id = ? AND note_id IN (%s)" % ",".join("?" * len(part)),
                [str(owner_user_id), *part],
            ).fetchall()
            for r in rows:
                out[int(r["note_id"])] = _row(r)
    return out


def notes_for_event(owner_user_id: Any, key: str) -> list[dict[str, Any]]:
    """Материалы встречи: живые заметки/транскрипции, привязанные этим пользователем."""
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT l.note_id, l.source, l.created_at AS linked_at
            FROM meeting_links l JOIN local_notes n ON n.id = l.note_id
            WHERE l.owner_user_id = ? AND l.event_key = ?
            ORDER BY l.created_at
            """,
            (str(owner_user_id), str(key)),
        ).fetchall()
    return [dict(r) for r in rows]


def kinds_for_events(owner_user_id: Any, keys: list[str]) -> dict[str, dict[str, int]]:
    """Значки для карточек встреч: {event_key: {"transcription": n, "note": n}}."""
    keys = [str(k) for k in keys if k][:400]
    if not keys:
        return {}
    out: dict[str, dict[str, int]] = {}
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT l.event_key, n.role, n.primary_sheet_title
            FROM meeting_links l JOIN local_notes n ON n.id = l.note_id
            WHERE l.owner_user_id = ? AND l.event_key IN (%s)
            """ % ",".join("?" * len(keys)),
            [str(owner_user_id), *keys],
        ).fetchall()
    for r in rows:
        kind = "transcription" if str(r["role"] or "") == "transcription" else "note"
        slot = out.setdefault(str(r["event_key"]), {"transcription": 0, "note": 0})
        slot[kind] += 1
    return out


def _person_key(email: str, name: str, contact_key: str = "") -> str:
    return (contact_key or email or name).strip().lower()


def add_person(owner_user_id: Any, note_id: Any, *, name: str = "", email: str = "", contact_key: str = "") -> list[dict[str, Any]]:
    name = str(name or "").strip()[:120]
    email = str(email or "").strip().lower()[:200]
    key = _person_key(email, name, str(contact_key or ""))
    if not key:
        raise ValueError("Укажите участника")
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO note_people (owner_user_id, note_id, person_key, name, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, note_id, person_key) DO UPDATE SET name = excluded.name, email = excluded.email
            """,
            (str(owner_user_id), int(note_id), key, name or email, email, _now()),
        )
        conn.commit()
    return people_for_note(owner_user_id, note_id)


def remove_person(owner_user_id: Any, note_id: Any, person_key: str) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "DELETE FROM note_people WHERE owner_user_id = ? AND note_id = ? AND person_key = ?",
            (str(owner_user_id), int(note_id), str(person_key or "").strip().lower()),
        )
        conn.commit()
    return people_for_note(owner_user_id, note_id)


def people_for_note(owner_user_id: Any, note_id: Any) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT person_key, name, email FROM note_people WHERE owner_user_id = ? AND note_id = ? ORDER BY created_at, rowid",
            (str(owner_user_id), int(note_id)),
        ).fetchall()
    return [dict(r) for r in rows]


def forget_note(note_id: Any) -> None:
    """Заметка удалена навсегда — убрать её связи (у всех)."""
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM meeting_links WHERE note_id = ?", (int(note_id),))
        conn.execute("DELETE FROM note_people WHERE note_id = ?", (int(note_id),))
        conn.commit()


def materials_for_events(owner_user_id: Any, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Материалы по встречам: {event_key: [{note_id, kind, title}]} — для карточки контакта."""
    keys = [str(k) for k in keys if k][:600]
    if not keys:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    with _LOCK:
        conn = _conn()
        for i in range(0, len(keys), 300):
            part = keys[i : i + 300]
            rows = conn.execute(
                """
                SELECT l.event_key, l.note_id, n.title, n.role
                FROM meeting_links l JOIN local_notes n ON n.id = l.note_id
                WHERE l.owner_user_id = ? AND l.event_key IN (%s)
                ORDER BY l.created_at
                """ % ",".join("?" * len(part)),
                [str(owner_user_id), *part],
            ).fetchall()
            for r in rows:
                out.setdefault(str(r["event_key"]), []).append(
                    {
                        "note_id": int(r["note_id"]),
                        "title": str(r["title"] or ""),
                        "kind": "transcription" if str(r["role"] or "") == "transcription" else "note",
                    }
                )
    return out


def notes_for_person(owner_user_id: Any, person_keys: list[str]) -> list[dict[str, Any]]:
    """Заметки/транскрипции, куда человек добавлен вручную (+ встреча, если привязана)."""
    keys = sorted({str(k).strip().lower() for k in person_keys if str(k or "").strip()})
    if not keys:
        return []
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT DISTINCT p.note_id, n.title, n.role, n.created_at, n.updated_at,
                   l.event_id, l.calendar_id, l.summary, l.start_iso, l.end_iso
            FROM note_people p
            JOIN local_notes n ON n.id = p.note_id
            LEFT JOIN meeting_links l ON l.owner_user_id = p.owner_user_id AND l.note_id = p.note_id
            WHERE p.owner_user_id = ? AND (p.person_key IN (%s) OR lower(p.email) IN (%s))
            ORDER BY COALESCE(NULLIF(l.start_iso, ''), n.created_at) DESC
            LIMIT 100
            """ % (",".join("?" * len(keys)), ",".join("?" * len(keys))),
            [str(owner_user_id), *keys, *keys],
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kind"] = "transcription" if str(d.pop("role") or "") == "transcription" else "note"
        out.append(d)
    return out
