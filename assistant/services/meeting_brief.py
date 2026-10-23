"""B3: бриф перед встречей — история с участниками, прошлая встреча серии, открытые задачи.

Собирает факты из уже существующих источников (person_history, материалы встреч,
предыдущая встреча с тем же названием) и сжимает их моделью в короткий Telegram HTML.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timedelta
from typing import Any, Optional

_MAX_PEOPLE = 6


def briefs_enabled_for(user_id: int) -> bool:
    raw = (os.getenv("MEETING_BRIEFS_ENABLED", "0") or "0").strip().lower()
    if raw in {"0", "false", "no", "off", ""}:
        return False
    allow = [x.strip() for x in (os.getenv("MEETING_BRIEFS_USERS", "") or "").split(",") if x.strip()]
    return not allow or str(int(user_id)) in allow


def brief_minutes_before() -> int:
    try:
        return max(5, int(os.getenv("MEETING_BRIEF_MINUTES", "30") or "30"))
    except ValueError:
        return 30


def _attendees(raw: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for a in raw.get("attendees") or []:
        if not isinstance(a, dict) or a.get("resource") or a.get("self"):
            continue
        email = str(a.get("email") or "").strip().lower()
        name = str(a.get("displayName") or "").strip()
        if email or name:
            out.append({"email": email, "name": name})
    return out


def _start_dt(ev: dict[str, Any]) -> Optional[datetime]:
    st = ev.get("start")
    if isinstance(st, datetime):
        return st
    try:
        return datetime.fromisoformat(str(ev.get("start_iso") or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _trim_history(h: dict[str, Any]) -> dict[str, Any]:
    """Только «когда виделись»: последние 3 встречи (название и дата).

    Материалы и поручения из чужих встреч сюда не берём — иначе в бриф груминга
    попадает то, что обсуждали с участником один на один. Повестка и обязательства
    берутся только из прошлой встречи этой серии (series).
    """
    meetings = [
        {k: m.get(k) for k in ("title", "start") if m.get(k)}
        for m in (h.get("meetings") or [])[:3]
    ]
    out: dict[str, Any] = {"person": h.get("person")}
    meetings = [m for m in meetings if m]
    if meetings:
        out["meetings"] = meetings
    return out


def collect_facts(user_id: int, ev: dict[str, Any]) -> dict[str, Any]:
    from assistant.lib.meeting_context import _previous_brief
    from assistant.services.gpt_calendar_tools import GptCalendarContext, tool_person_history

    raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
    title = str(ev.get("summary") or raw.get("summary") or "").strip()
    start = _start_dt(ev)
    ctx = GptCalendarContext(uid=int(user_id))
    people: list[dict[str, Any]] = []
    attendees = _attendees(raw)
    for a in attendees[:_MAX_PEOPLE]:
        try:
            res = json.loads(tool_person_history(ctx, {
                "person": a["email"] or a["name"],
                "start_date": ((start or datetime.now()) - timedelta(days=90)).date().isoformat(),
                "end_date": ((start or datetime.now()) - timedelta(minutes=1)).date().isoformat(),
            }))
        except Exception as e:
            print(f"[meeting_brief] history uid={user_id} who={a['email'] or a['name']} err={e!r}")
            continue
        if res.get("error"):
            continue
        h = _trim_history(res)
        if len(h) > 1:
            people.append(h)
    series = _previous_brief(int(user_id), title, start, None)
    description = ""
    try:
        from assistant.lib.telegram_html import html_to_plain

        description = html_to_plain(str(raw.get("description") or ""))[:1500]
    except Exception:
        description = str(raw.get("description") or "")[:1500]
    return {
        "meeting": {
            "title": title,
            "start": start.isoformat() if start else "",
            "description": description,
            "attendees": [a["name"] or a["email"] for a in attendees],
        },
        "people": people,
        "series": series,
    }


def has_substance(facts: dict[str, Any]) -> bool:
    return bool(facts.get("people") or facts.get("series"))


def compose_brief(facts: dict[str, Any]) -> str:
    from assistant.nlu import llm
    from assistant.nlu.prompts import MEETING_BRIEF_SYSTEM

    payload = dict(facts)
    payload["now"] = datetime.now().isoformat(timespec="minutes")
    text = llm._chat(
        MEETING_BRIEF_SYSTEM,
        json.dumps(payload, ensure_ascii=False, default=str),
        operation="meeting_brief",
        model=llm._model_chat_digest(),
        timeout=120,
        temperature=0.2,
        max_tokens=1500,
    )
    return sanitize_brief_html((text or "").strip())


_ALLOWED_TAGS = ("b", "i", "a")


def sanitize_brief_html(text: str) -> str:
    """Telegram HTML строгий: оставляем b/i/a, остальное экранируем."""
    import re

    t = re.sub(r"^```(?:html)?\s*|\s*```$", "", text.strip())
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</?(?:ul|ol|p|div|h\d)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "• ", t, flags=re.I).replace("</li>", "\n")
    parts = re.split(r"(</?(?:b|i|a)(?:\s+href=\"[^\"]*\")?>)", t, flags=re.I)
    out = []
    for p in parts:
        if re.fullmatch(r"</?(?:b|i|a)(?:\s+href=\"[^\"]*\")?>", p or "", flags=re.I):
            out.append(p)
        else:
            out.append(html.escape(html.unescape(p), quote=False))
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def build_brief_message(user_id: int, ev: dict[str, Any]) -> Optional[str]:
    """Готовый текст брифа или None, если по встрече нечего сказать."""
    facts = collect_facts(int(user_id), ev)
    if not has_substance(facts):
        return None
    body = compose_brief(facts)
    if not body:
        return None
    title = html.escape(str(facts["meeting"]["title"] or "Встреча"))
    return f"📋 <b>Бриф к встрече «{title}»</b>\n\n{body}"


def events_due_for_brief(user_id: int, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    from assistant.lib.calendar_event_utils import calendar_event_is_task
    from assistant.services import meeting_reminders as mr
    from assistant.services.calendar import _tz_for
    from assistant.stores import meeting_reminders_sent as sent_store

    tz = _tz_for(user_id)
    now = now or datetime.now(tz)
    mins = brief_minutes_before()
    half = mr.reminder_window_sec() / 2.0
    due = []
    for ev in mr.fetch_upcoming_timed_events(user_id, horizon_min=mins + 12):
        cal_id = str(ev.get("calendar_id") or "")
        ev_id = str(ev.get("event_id") or "")
        start_iso = str(ev.get("start_iso") or "")
        raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
        start = ev.get("start")
        if not ev_id or not cal_id or not isinstance(start, datetime) or calendar_event_is_task(raw):
            continue
        if not _attendees(raw):
            continue  # бриф нужен только встречам с другими людьми
        if sent_store.was_sent(user_id, cal_id, brief_sent_id(ev_id), start_iso):
            continue
        if abs((start - now).total_seconds() - mins * 60) <= half:
            due.append(ev)
    return due


def brief_sent_id(event_id: str) -> str:
    # отдельный ключ в meeting_reminders_sent, чтобы бриф и напоминание не мешали друг другу
    return f"brief:{event_id}"
