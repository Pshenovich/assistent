"""Зеркало внешних календарей (пока Google) в Орбите: история событий для нагрузки и вопросов «на что ушло время».

Источник правды остаётся внешний календарь: правки из Орбиты уходят туда как раньше, а сюда события
попадают при любом чтении окна (``calendar_sources.list_events_in_window``) и фоновой сверкой
(``bot/calendar_mirror_job``). Зеркало одностороннее: внешний → Орбита.

* Прочитали окно календаря целиком (без поиска, календарь ответил) — что в окне было, а теперь нет,
  помечаем ``cancelled`` (удалили или отменили в Google). Строки не удаляем — прошлое остаётся.
* Храним событие целиком для работы в Орбите: время, название, описание, место, ссылку на событие
  и на видеовстречу, участников с ответами, организатора, «занят/свободен», тип
  (встреча / задача / нет на месте / фокус). Руководителю в показателях — только числа.
* ``synced_from`` — с какого дня у человека есть история (раньше — не знаем, а не «было пусто»).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from assistant.lib import sqlite_conn

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[Path] = None

KINDS = ("meeting", "task", "ooo", "focus", "other")


def enabled() -> bool:
    return os.getenv("CALENDAR_MIRROR_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def db_path() -> Path:
    from assistant.config import ROOT

    raw = os.getenv("CALENDAR_MIRROR_DB_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (ROOT / p).resolve()
    return ROOT / "data" / "calendar_mirror.sqlite"


def _conn() -> sqlite3.Connection:
    """Под _LOCK."""
    global _CONN, _CONN_PATH
    path = db_path()
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    if _CONN is not None:
        _CONN.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_conn.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mirror_events (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            calendar_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            series_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            start_utc TEXT NOT NULL,
            end_utc TEXT NOT NULL,
            all_day INTEGER NOT NULL DEFAULT 0,
            kind TEXT NOT NULL DEFAULT 'meeting',
            status TEXT NOT NULL DEFAULT 'confirmed',
            my_response TEXT NOT NULL DEFAULT '',
            organizer_self INTEGER NOT NULL DEFAULT 0,
            others INTEGER NOT NULL DEFAULT 0,
            other_emails TEXT NOT NULL DEFAULT '',
            transparent INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, source, calendar_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_mirror_user_time ON mirror_events(user_id, start_utc);
        CREATE TABLE IF NOT EXISTS mirror_sync (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            synced_from TEXT NOT NULL,
            last_sync TEXT NOT NULL,
            PRIMARY KEY (user_id, source)
        );
        """
    )
    have = {r[1] for r in conn.execute("PRAGMA table_info(mirror_events)").fetchall()}
    for col in _CONTENT_COLS:
        if col not in have:
            conn.execute(f"ALTER TABLE mirror_events ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    conn.commit()
    _CONN, _CONN_PATH = conn, path
    return conn


# Содержимое события (колонки добавлены после первой версии таблицы — отсюда ALTER).
_CONTENT_COLS = ("description", "location", "html_link", "meeting_url", "organizer_email", "attendees_json", "g_updated")
MAX_DESCRIPTION = 20000


def _utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _now() -> str:
    return _utc(datetime.now(timezone.utc))


def _kind(raw: dict[str, Any]) -> str:
    from assistant.lib.calendar_event_utils import calendar_event_has_task_marker

    et = str(raw.get("eventType") or "default").strip().lower()
    if et in ("outofoffice", "out_of_office"):
        return "ooo"
    if et in ("focustime", "focus_time"):
        return "focus"
    if et in ("workinglocation", "birthday", "fromgmail"):
        return "other"
    if calendar_event_has_task_marker(raw):
        return "task"
    return "meeting"


def row_from_gcal(raw: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    """Поля зеркала из события в форме Google (подходит и для встреч Орбиты — ``as_gcal``)."""
    me = ""
    others: list[str] = []
    for a in raw.get("attendees") or []:
        if not isinstance(a, dict):
            continue
        if a.get("self"):
            me = str(a.get("responseStatus") or "")
            continue
        if a.get("resource"):
            continue  # переговорка — не человек
        others.append(str(a.get("email") or "").strip().lower())
    org = raw.get("organizer") if isinstance(raw.get("organizer"), dict) else {}
    organizer_self = bool(org.get("self")) or not (raw.get("attendees") or [])
    st = raw.get("start") if isinstance(raw.get("start"), dict) else {}
    people = [
        {k: a[k] for k in ("email", "displayName", "responseStatus", "organizer", "self", "optional", "resource") if a.get(k)}
        for a in raw.get("attendees") or [] if isinstance(a, dict)
    ]
    try:
        from assistant.lib.meeting_links import extract_online_meeting_url

        meeting_url = extract_online_meeting_url(raw) or ""
    except Exception:
        meeting_url = str(raw.get("hangoutLink") or "")
    return {
        "description": str(raw.get("description") or "")[:MAX_DESCRIPTION],
        "location": str(raw.get("location") or "")[:500],
        "html_link": str(raw.get("htmlLink") or "")[:500],
        "meeting_url": str(meeting_url or "")[:500],
        "organizer_email": str(org.get("email") or "").strip().lower()[:200],
        "attendees_json": json.dumps(people, ensure_ascii=False)[:20000] if people else "",
        "g_updated": str(raw.get("updated") or ""),
        "series_id": str(raw.get("recurringEventId") or ""),
        "title": str(raw.get("summary") or "").strip()[:200],
        "start_utc": _utc(start),
        "end_utc": _utc(end),
        "all_day": 0 if st.get("dateTime") else 1,
        "kind": _kind(raw),
        "my_response": me or ("accepted" if organizer_self else ""),
        "organizer_self": 1 if organizer_self else 0,
        "others": len(others),
        "other_emails": ",".join(sorted({e for e in others if e}))[:2000],
        "transparent": 1 if str(raw.get("transparency") or "") == "transparent" else 0,
    }


def record_window(
    user_id: int | str,
    events: Iterable[dict[str, Any]],
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_ids: Iterable[str],
    source: str = "google",
) -> int:
    """Записать прочитанное окно. ``events`` — нормализованные события (``calendar_id``, ``event_id``,
    ``start``, ``end``, ``raw``); ``calendar_ids`` — календари, которые ответили (по ним сверяем удаления).
    Возвращает число записанных событий. Не бросает."""
    if not enabled():
        return 0
    uid = str(user_id)
    cals = {str(c) for c in calendar_ids if c}
    if not cals:
        return 0
    now = _now()
    seen: dict[str, set[str]] = {c: set() for c in cals}
    rows = []
    for e in events:
        cal = str(e.get("calendar_id") or "")
        eid = str(e.get("event_id") or "")
        raw = e.get("raw") if isinstance(e.get("raw"), dict) else None
        if cal not in cals or not eid or raw is None or not isinstance(e.get("start"), datetime):
            continue
        seen[cal].add(eid)
        r = row_from_gcal(raw, e["start"], e["end"])
        rows.append((uid, source, cal, eid, r["series_id"], r["title"], r["start_utc"], r["end_utc"], r["all_day"],
                     r["kind"], r["my_response"], r["organizer_self"], r["others"], r["other_emails"], r["transparent"],
                     now, now, *(r[c] for c in _CONTENT_COLS)))
    lo, hi = _utc(time_min), _utc(time_max)
    try:
        with _LOCK:
            conn = _conn()
            conn.executemany(
                """
                INSERT INTO mirror_events (user_id, source, calendar_id, event_id, series_id, title, start_utc, end_utc,
                    all_day, kind, status, my_response, organizer_self, others, other_emails, transparent, first_seen, updated_at,
                    description, location, html_link, meeting_url, organizer_email, attendees_json, g_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, source, calendar_id, event_id) DO UPDATE SET
                    series_id = excluded.series_id, title = excluded.title, start_utc = excluded.start_utc,
                    end_utc = excluded.end_utc, all_day = excluded.all_day, kind = excluded.kind, status = 'confirmed',
                    my_response = excluded.my_response, organizer_self = excluded.organizer_self,
                    others = excluded.others, other_emails = excluded.other_emails,
                    transparent = excluded.transparent, updated_at = excluded.updated_at,
                    description = excluded.description, location = excluded.location, html_link = excluded.html_link,
                    meeting_url = excluded.meeting_url, organizer_email = excluded.organizer_email,
                    attendees_json = excluded.attendees_json, g_updated = excluded.g_updated
                """,
                rows,
            )
            # Было в окне, а теперь календарь его не вернул — удалили или отменили.
            for cal, ids in seen.items():
                stale = conn.execute(
                    "SELECT event_id FROM mirror_events WHERE user_id = ? AND source = ? AND calendar_id = ?"
                    " AND status = 'confirmed' AND start_utc < ? AND end_utc > ?",
                    (uid, source, cal, hi, lo),
                ).fetchall()
                gone = [r[0] for r in stale if r[0] not in ids]
                for i in range(0, len(gone), 500):
                    part = gone[i:i + 500]
                    conn.execute(
                        f"UPDATE mirror_events SET status = 'cancelled', updated_at = ? WHERE user_id = ? AND source = ?"
                        f" AND calendar_id = ? AND event_id IN ({','.join('?' * len(part))})",
                        (now, uid, source, cal, *part),
                    )
            cur = conn.execute("SELECT synced_from FROM mirror_sync WHERE user_id = ? AND source = ?", (uid, source)).fetchone()
            frm = min(lo, str(cur[0])) if cur else lo
            conn.execute(
                "INSERT INTO mirror_sync (user_id, source, synced_from, last_sync) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(user_id, source) DO UPDATE SET synced_from = excluded.synced_from, last_sync = excluded.last_sync",
                (uid, source, frm, now),
            )
            conn.commit()
    except Exception as e:
        print(f"[calendar_mirror] record failed uid={uid} err={e!r}")
        return 0
    return len(rows)


def synced_from(user_id: int | str, source: str = "google") -> Optional[datetime]:
    with _LOCK:
        r = _conn().execute(
            "SELECT synced_from FROM mirror_sync WHERE user_id = ? AND source = ?", (str(user_id), source)
        ).fetchone()
    return datetime.fromisoformat(r[0]) if r else None


def events(user_id: int | str, start: datetime, end: datetime, *, source: str = "google") -> list[dict[str, Any]]:
    """Действующие события человека, пересекающие [start, end). Время — datetime в UTC."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM mirror_events WHERE user_id = ? AND source = ? AND status = 'confirmed'"
            " AND start_utc < ? AND end_utc > ? ORDER BY start_utc",
            (str(user_id), source, _utc(end), _utc(start)),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["start"] = datetime.fromisoformat(d["start_utc"])
        d["end"] = datetime.fromisoformat(d["end_utc"])
        try:
            d["attendees"] = json.loads(d.get("attendees_json") or "[]")
        except ValueError:
            d["attendees"] = []
        out.append(d)
    return out
