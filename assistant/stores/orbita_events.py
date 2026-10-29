"""Встречи Орбиты — календарь без Google.

Встреча хранится здесь, если у организатора не подключён Google Calendar. Её видят в «Актуальном»
организатор и приглашённые пользователи Орбиты; внешним гостям уходит письмо с приглашением (.ics).
Наружу встреча выглядит как событие Google (``as_gcal``), чтобы её показывали те же сериализатор
и интерфейс: id ``orb-<n>``, календарь ``orbita``.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

CALENDAR_ID = "orbita"
ID_PREFIX = "orb-"
RESPONSES = ("needsAction", "accepted", "tentative", "declined")


def _conn():
    conn = notes_store._conn()  # type: ignore[attr-defined]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orbita_event_attendees'").fetchone():
        return conn
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS orbita_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ical_uid TEXT NOT NULL UNIQUE,
            owner_user_id TEXT NOT NULL,
            organizer_email TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            start_utc TEXT NOT NULL,
            end_utc TEXT NOT NULL,
            tz TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'confirmed',
            sequence INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orbita_events_owner ON orbita_events(owner_user_id, start_utc);
        CREATE TABLE IF NOT EXISTS orbita_event_attendees (
            event_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            user_id TEXT,
            name TEXT NOT NULL DEFAULT '',
            response TEXT NOT NULL DEFAULT 'needsAction',
            token TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (event_id, email)
        );
        CREATE INDEX IF NOT EXISTS idx_orbita_att_user ON orbita_event_attendees(user_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orbita_att_token ON orbita_event_attendees(token);
        """
    )
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("нужно время с часовым поясом")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def is_orbita_id(event_id: str) -> bool:
    return str(event_id or "").startswith(ID_PREFIX)


def parse_id(event_id: str) -> int:
    raw = str(event_id or "")
    if not raw.startswith(ID_PREFIX) or not raw[len(ID_PREFIX):].isdigit():
        raise ValueError("Встреча не найдена")
    return int(raw[len(ID_PREFIX):])


def public_id(n: int) -> str:
    return f"{ID_PREFIX}{int(n)}"


# ---------- чтение ----------


def _attendees(event_id: int) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM orbita_event_attendees WHERE event_id = ? ORDER BY rowid", (int(event_id),)
    ).fetchall()
    return [dict(r) for r in rows]


def get(event_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute("SELECT * FROM orbita_events WHERE id = ?", (int(event_id),)).fetchone()
        if not row:
            return None
        ev = dict(row)
        ev["attendees"] = _attendees(int(row["id"]))
    return ev


def get_by_token(token: str) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    """Ссылка «Иду / Не иду» из письма → (встреча, гость)."""
    with _LOCK:
        row = _conn().execute("SELECT * FROM orbita_event_attendees WHERE token = ?", (str(token or ""),)).fetchone()
    if not row:
        return None
    ev = get(int(row["event_id"]))
    return (ev, dict(row)) if ev else None


def get_by_ical_uid(uid: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute("SELECT id FROM orbita_events WHERE ical_uid = ?", (str(uid or ""),)).fetchone()
    return get(int(row["id"])) if row else None


def owner_has_guests(owner_user_id: int | str) -> bool:
    """Есть ли у организатора встречи с внешними гостями — тогда ищем их ответы в его почте."""
    with _LOCK:
        row = _conn().execute(
            "SELECT 1 FROM orbita_events e JOIN orbita_event_attendees a ON a.event_id = e.id "
            "WHERE e.owner_user_id = ? AND a.user_id IS NULL LIMIT 1",
            (str(int(owner_user_id)),),
        ).fetchone()
    return bool(row)


def can_view(ev: dict[str, Any], user_id: int | str) -> bool:
    uid = str(int(user_id))
    return ev["owner_user_id"] == uid or any(a.get("user_id") == uid for a in ev["attendees"])


def list_for_user(user_id: int | str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Встречи в окне, где пользователь организатор или приглашён (отменённые — нет)."""
    uid = str(int(user_id))
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT DISTINCT e.id FROM orbita_events e
            LEFT JOIN orbita_event_attendees a ON a.event_id = e.id
            WHERE e.status = 'confirmed' AND e.start_utc < ? AND e.end_utc > ?
              AND (e.owner_user_id = ? OR a.user_id = ?)
            ORDER BY e.start_utc
            """,
            (_utc(end), _utc(start), uid, uid),
        ).fetchall()
    out = []
    for r in rows:
        ev = get(int(r["id"]))
        if ev:
            out.append(ev)
    return out


def busy_for_user(user_id: int | str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Занятые интервалы по встречам Орбиты (без отказавшихся)."""
    uid = str(int(user_id))
    out = []
    for ev in list_for_user(user_id, start, end):
        me = next((a for a in ev["attendees"] if a.get("user_id") == uid), None)
        if me and me.get("response") == "declined":
            continue
        out.append((datetime.fromisoformat(ev["start_utc"]), datetime.fromisoformat(ev["end_utc"])))
    return out


# ---------- запись ----------


def _write_attendees(conn, event_id: int, attendees: list[dict[str, Any]], *, keep: dict[str, dict]) -> None:
    now = _now()
    conn.execute("DELETE FROM orbita_event_attendees WHERE event_id = ?", (int(event_id),))
    for a in attendees:
        em = str(a.get("email") or "").strip().lower()
        if not em or "@" not in em:
            continue
        old = keep.get(em) or {}
        conn.execute(
            "INSERT OR IGNORE INTO orbita_event_attendees (event_id, email, user_id, name, response, token, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(event_id), em, (str(a["user_id"]) if a.get("user_id") else old.get("user_id")),
             str(a.get("name") or old.get("name") or ""), old.get("response") or "needsAction",
             old.get("token") or secrets.token_urlsafe(16), old.get("updated_at") or now),
        )


def create(owner_user_id: int, *, title: str, start: datetime, end: datetime, tz: str = "",
           description: str = "", location: str = "", organizer_email: str = "",
           attendees: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """attendees: [{email, user_id?, name?}] — организатора туда не класть."""
    if end <= start:
        raise ValueError("Конец встречи раньше начала")
    now = _now()
    uid_ical = f"orbita-{secrets.token_hex(10)}@orbita"
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO orbita_events (ical_uid, owner_user_id, organizer_email, title, description, location, "
            "start_utc, end_utc, tz, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid_ical, str(int(owner_user_id)), organizer_email.strip().lower(), title.strip() or "Встреча",
             description, location.strip(), _utc(start), _utc(end), tz, now, now),
        )
        eid = int(cur.lastrowid)
        _write_attendees(conn, eid, attendees or [], keep={})
        conn.commit()
    return get(eid) or {}


def update(event_id: int, *, title: str | None = None, start: datetime | None = None, end: datetime | None = None,
           description: str | None = None, location: str | None = None,
           attendees: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Правка встречи. Меняется время/место — ответы гостей сбрасываются (их надо спросить заново)."""
    ev = get(event_id)
    if not ev:
        raise ValueError("Встреча не найдена")
    sets: dict[str, Any] = {}
    if title is not None:
        sets["title"] = title.strip() or ev["title"]
    if description is not None:
        sets["description"] = description
    if location is not None:
        sets["location"] = location.strip()
    new_start = _utc(start) if start else ev["start_utc"]
    new_end = _utc(end) if end else ev["end_utc"]
    if new_end <= new_start:
        raise ValueError("Конец встречи раньше начала")
    moved = new_start != ev["start_utc"] or new_end != ev["end_utc"] or (
        location is not None and location.strip() != ev["location"])
    sets["start_utc"], sets["end_utc"] = new_start, new_end
    sets["sequence"] = int(ev["sequence"]) + 1
    sets["updated_at"] = _now()
    with _LOCK:
        conn = _conn()
        conn.execute("UPDATE orbita_events SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?",
                     (*sets.values(), int(event_id)))
        keep = {a["email"]: a for a in ev["attendees"]}
        if moved:
            keep = {k: {**v, "response": "needsAction"} for k, v in keep.items()}
            conn.execute("UPDATE orbita_event_attendees SET response = 'needsAction' WHERE event_id = ?", (int(event_id),))
        if attendees is not None:
            _write_attendees(conn, int(event_id), attendees, keep=keep)
        conn.commit()
    return get(event_id) or {}


def cancel(event_id: int) -> dict[str, Any]:
    ev = get(event_id)
    if not ev:
        raise ValueError("Встреча не найдена")
    with _LOCK:
        conn = _conn()
        conn.execute("UPDATE orbita_events SET status = 'cancelled', sequence = sequence + 1, updated_at = ? WHERE id = ?",
                     (_now(), int(event_id)))
        conn.commit()
    return get(event_id) or {}


def set_response(event_id: int, *, email: str | None = None, user_id: int | str | None = None,
                 response: str) -> bool:
    if response not in RESPONSES:
        raise ValueError("Неизвестный ответ")
    with _LOCK:
        conn = _conn()
        if user_id is not None:
            cur = conn.execute("UPDATE orbita_event_attendees SET response = ?, updated_at = ? WHERE event_id = ? AND user_id = ?",
                               (response, _now(), int(event_id), str(int(user_id))))
        else:
            cur = conn.execute("UPDATE orbita_event_attendees SET response = ?, updated_at = ? WHERE event_id = ? AND email = ?",
                               (response, _now(), int(event_id), str(email or "").strip().lower()))
        conn.commit()
        return cur.rowcount > 0


# ---------- как событие Google ----------


def as_gcal(ev: dict[str, Any], viewer_id: int | str) -> dict[str, Any]:
    """Встреча в форме события Google глазами viewer: self у его строки, organizer — у владельца."""
    viewer = str(int(viewer_id))
    is_owner = ev["owner_user_id"] == viewer
    tz = ev.get("tz") or "UTC"
    atts = []
    if ev.get("organizer_email"):
        atts.append({"email": ev["organizer_email"], "organizer": True, "responseStatus": "accepted",
                     **({"self": True} if is_owner else {})})
    for a in ev["attendees"]:
        row = {"email": a["email"], "responseStatus": a["response"]}
        if a.get("name"):
            row["displayName"] = a["name"]
        if a.get("user_id") == viewer and not is_owner:
            row["self"] = True
        atts.append(row)
    out = {
        "id": public_id(ev["id"]),
        "iCalUID": ev["ical_uid"],
        "_calendarId": CALENDAR_ID,
        "summary": ev["title"],
        "description": ev.get("description") or "",
        "location": ev.get("location") or "",
        "start": {"dateTime": ev["start_utc"], "timeZone": tz},
        "end": {"dateTime": ev["end_utc"], "timeZone": tz},
        "status": ev["status"],
        "attendees": atts,
        "organizer": {"email": ev.get("organizer_email") or "", **({"self": True} if is_owner else {})},
        "_orbita": True,
    }
    return out
