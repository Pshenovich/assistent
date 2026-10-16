"""Автопривязка записи встречи (бот Leo) к событию календаря.

Запись знает ссылку на созвон, id встречи Zoom/Телемоста и время начала. Ищем в календаре
пользователя событие вокруг этого времени с той же ссылкой (или тем же id встречи) и
связываем с ним готовую транскрипцию. Ошибки календаря не мешают сохранению записи.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_URL_CLEAN_RE = re.compile(r"[?#].*$")


def _norm_url(u: str) -> str:
    return _URL_CLEAN_RE.sub("", str(u or "").strip().lower()).rstrip("/")


def _event_snapshot(norm: dict[str, Any]) -> dict[str, Any]:
    from assistant.lib.meeting_links import extract_online_meeting_url

    raw = dict(norm.get("raw") or {})
    attendees = []
    for a in raw.get("attendees") or []:
        if isinstance(a, dict) and not a.get("resource"):
            attendees.append({"email": str(a.get("email") or ""), "name": str(a.get("displayName") or a.get("email") or "")})
    return {
        "id": str(norm.get("event_id") or raw.get("id") or ""),
        "calendar_id": str(norm.get("calendar_id") or raw.get("_calendarId") or "primary"),
        "summary": str(raw.get("summary") or ""),
        "start": raw.get("start") or {},
        "end": raw.get("end") or {},
        "meet_url": extract_online_meeting_url(raw) or "",
        "attendees": attendees,
    }


def _matches(raw: dict[str, Any], *, url: str, native_id: str) -> bool:
    from assistant.lib.meeting_links import extract_online_meeting_urls

    if native_id:
        try:
            from assistant.lib.zoom_gcal import read_zoom_meeting_id

            if read_zoom_meeting_id(raw) == native_id:
                return True
        except Exception:
            pass
    want = _norm_url(url)
    urls = [_norm_url(u) for u in extract_online_meeting_urls(raw)]
    if want and want in urls:
        return True
    if native_id and len(native_id) >= 6:
        digits = re.sub(r"\D", "", native_id)
        for u in urls:
            if native_id.lower() in u or (digits and len(digits) >= 9 and digits in re.sub(r"\D", "", u)):
                return True
    return False


def find_event_for_recording(
    user_id: int,
    *,
    meeting_url: str = "",
    native_meeting_id: str = "",
    start_at: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    from assistant.integrations import google_calendar_oauth
    from assistant.services import calendar_sources

    if not (meeting_url or native_meeting_id):
        return None
    if not google_calendar_oauth.user_token_path(int(user_id)).is_file():
        return None
    at = start_at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    events = calendar_sources.list_events_in_window(int(user_id), at - timedelta(hours=12), at + timedelta(hours=12))
    best: Optional[tuple[float, dict[str, Any]]] = None
    for norm in events:
        raw = norm.get("raw") or {}
        if not isinstance(raw, dict) or not _matches(raw, url=meeting_url, native_id=native_meeting_id):
            continue
        st = norm.get("start")
        dist = abs((st - at).total_seconds()) if isinstance(st, datetime) else 1e9
        if best is None or dist < best[0]:
            best = (dist, norm)
    return _event_snapshot(best[1]) if best else None


def link_recording_note(
    user_id: int,
    note_id: int,
    *,
    meeting_url: str = "",
    native_meeting_id: str = "",
    start_at: Any = None,
) -> Optional[dict[str, Any]]:
    """Найти встречу записи и привязать к ней заметку-транскрипцию. Возвращает связь или None."""
    from assistant.stores import meeting_links

    try:
        at = start_at
        if isinstance(at, str) and at.strip():
            at = datetime.fromisoformat(at.strip().replace("Z", "+00:00"))
        if not isinstance(at, datetime):
            at = None
        ev = find_event_for_recording(
            int(user_id), meeting_url=meeting_url, native_meeting_id=str(native_meeting_id or ""), start_at=at
        )
        if not ev or not ev.get("id"):
            return None
        return meeting_links.link(int(user_id), int(note_id), ev, source="bot")
    except Exception as e:
        print(f"[meeting_autolink] failed uid={user_id} note={note_id} err={e!r}")
        return None
