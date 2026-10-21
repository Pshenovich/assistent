"""Контекст встречи для саммари: дата, событие календаря, владелец, известные люди.

Всё best-effort: любая ошибка календаря/контактов просто оставляет поле пустым.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

_GLOSSARY_MAX = 60


@dataclass
class MeetingContext:
    meeting_at: str = ""  # ISO, локальное время владельца
    timezone: str = ""
    title: str = ""
    attendees: list[str] = field(default_factory=list)
    owner: str = ""
    owner_aliases: list[str] = field(default_factory=list)
    glossary: list[str] = field(default_factory=list)
    previous: str = ""
    # для нормализации исполнителей (не уходит в LLM целиком)
    people: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.meeting_at:
            out["meeting_at"] = self.meeting_at
            try:
                wd = datetime.fromisoformat(self.meeting_at).strftime("%A")
                out["weekday"] = wd
            except ValueError:
                pass
        if self.timezone:
            out["timezone"] = self.timezone
        if self.title:
            out["title"] = self.title
        if self.attendees:
            out["attendees"] = self.attendees
        if self.owner:
            out["owner"] = self.owner
        if self.glossary:
            out["glossary"] = self.glossary[:_GLOSSARY_MAX]
        if self.previous:
            out["previous"] = self.previous
        return out

    def meeting_date(self) -> Optional[datetime]:
        if not self.meeting_at:
            return None
        try:
            return datetime.fromisoformat(self.meeting_at)
        except ValueError:
            return None


def _parse_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _tz_name(user_id: int) -> str:
    try:
        from assistant.lib.user_timezone import resolve_user_tz_name

        return resolve_user_tz_name(int(user_id))
    except Exception:
        from assistant.config import CALENDAR_TZ

        return CALENDAR_TZ


def _owner(user_id: int) -> tuple[str, list[str]]:
    try:
        from assistant.stores import note_members

        prof = note_members.get_profile(int(user_id)) or {}
    except Exception:
        prof = {}
    fn = str(prof.get("first_name") or "").strip()
    ln = str(prof.get("last_name") or "").strip()
    name = " ".join(p for p in (fn, ln) if p).strip()
    aliases = [a for a in (fn, str(prof.get("username") or "").strip()) if a and a != name]
    return name, aliases


def _contacts(user_id: int) -> list[dict[str, Any]]:
    try:
        from assistant.stores import contacts_store

        return contacts_store.load_contacts(telegram_user_id=int(user_id))
    except Exception:
        return []


def _previous_brief(user_id: int, title: str, before: Optional[datetime], note_id: Optional[int]) -> str:
    """Прошлая встреча с тем же названием события: тема и незакрытые задачи."""
    t = (title or "").strip()
    if not t:
        return ""
    try:
        from assistant.stores import meeting_links, notes as notes_store

        conn = meeting_links._conn()
        rows = conn.execute(
            "SELECT note_id, start_iso FROM meeting_links WHERE owner_user_id = ? AND summary = ? "
            "ORDER BY start_iso DESC LIMIT 5",
            (str(int(user_id)), t),
        ).fetchall()
    except Exception:
        return ""
    for r in rows:
        nid = int(r["note_id"])
        if note_id is not None and nid == int(note_id):
            continue
        st = _parse_dt(r["start_iso"])
        if before and st and st >= before:
            continue
        try:
            note = notes_store.get_note(str(int(user_id)), nid)
        except Exception:
            note = None
        if not note:
            continue
        meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
        bits: list[str] = []
        topic = str(meta.get("main_topic") or "").strip()
        when = st.date().isoformat() if st else ""
        bits.append(f"{when} {topic}".strip())
        open_tasks = [
            t for t in meta.get("tasks") or [] if isinstance(t, dict) and not t.get("completed")
        ]
        if open_tasks:
            lines = []
            for x in open_tasks[:10]:
                who = str(x.get("assignee") or "").strip()
                what = str(x.get("task") or "").strip()
                lines.append(f"{who}: {what}" if who else what)
            bits.append("Задачи с прошлой встречи: " + "; ".join(lines))
        decisions = [str(d.get("text") if isinstance(d, dict) else d).strip() for d in meta.get("decisions") or []]
        decisions = [d for d in decisions if d]
        if decisions:
            bits.append("Решения: " + "; ".join(decisions[:6]))
        return "\n".join(b for b in bits if b)[:2000]
    return ""


def build_meeting_context(
    user_id: int,
    *,
    topic: str = "",
    start_at: Any = None,
    meeting_url: str = "",
    native_meeting_id: str = "",
    event: Optional[dict[str, Any]] = None,
    note_id: Optional[int] = None,
) -> MeetingContext:
    uid = int(user_id)
    tz_name = _tz_name(uid)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
        tz_name = "UTC"

    ev = event
    at = _parse_dt(start_at)
    if ev is None and (meeting_url or native_meeting_id):
        try:
            from assistant.services import meeting_autolink

            ev = meeting_autolink.find_event_for_recording(
                uid, meeting_url=meeting_url, native_meeting_id=native_meeting_id, start_at=at
            )
        except Exception as e:
            print(f"[meeting_context] event lookup failed uid={uid} err={e!r}")
            ev = None
    title = (topic or "").strip()
    attendees: list[str] = []
    if ev:
        title = str(ev.get("summary") or "").strip() or title
        st = ev.get("start") if ev.get("start") is not None else ev.get("start_iso")
        if isinstance(st, dict):
            st = st.get("dateTime") or st.get("date")
        at = _parse_dt(st) or at
        for a in ev.get("attendees") or []:
            if isinstance(a, dict):
                n = str(a.get("name") or a.get("email") or "").strip()
                if n and n not in attendees:
                    attendees.append(n)
    if at is None:
        at = datetime.now(timezone.utc)

    owner, owner_aliases = _owner(uid)
    contacts = _contacts(uid)
    people: list[dict[str, Any]] = []
    glossary: list[str] = []
    for c in contacts:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        aliases = [str(x).strip() for x in c.get("aliases") or [] if str(x).strip()]
        people.append({"name": name, "email": str(c.get("email") or ""), "aliases": aliases,
                       "key": _contact_key(c)})
    attendee_low = {a.lower() for a in attendees}
    # сначала приглашённые, потом остальные контакты
    for p in sorted(people, key=lambda p: (p["name"].lower() not in attendee_low and p["email"].lower() not in attendee_low)):
        label = p["name"] + (f" ({', '.join(p['aliases'][:3])})" if p["aliases"] else "")
        glossary.append(label)
    if owner:
        people.append({"name": owner, "email": "", "aliases": owner_aliases, "key": "", "owner": True})

    return MeetingContext(
        meeting_at=at.astimezone(tz).replace(microsecond=0).isoformat(),
        timezone=tz_name,
        title=title,
        attendees=attendees,
        owner=owner,
        owner_aliases=owner_aliases,
        glossary=glossary,
        previous=_previous_brief(uid, title, at, note_id),
        people=people,
    )


def _contact_key(c: dict[str, Any]) -> str:
    try:
        from assistant.stores.contacts_store import contact_key

        return contact_key(c)
    except Exception:
        return str(c.get("email") or "")


def context_for_note(user_id: int, note: dict[str, Any]) -> MeetingContext:
    """Контекст для пересборки саммари существующей заметки (по привязанной встрече)."""
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    ev = None
    try:
        from assistant.stores import meeting_links

        link = meeting_links.get_for_note(int(user_id), int(note["id"]))
        if link:
            ev = {"summary": link.get("summary"), "start_iso": link.get("start_iso"),
                  "attendees": link.get("attendees") or []}
    except Exception:
        ev = None
    return build_meeting_context(
        int(user_id),
        topic=str(meta.get("meeting_topic") or note.get("title") or ""),
        start_at=None if ev else note.get("created_at"),
        event=ev,
        note_id=int(note["id"]) if note.get("id") is not None else None,
    )
