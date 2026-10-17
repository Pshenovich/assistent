"""RRULE для встреч / задач / напоминаний."""

from datetime import datetime
from zoneinfo import ZoneInfo

from assistant.lib import recurrence as recur

TZ = ZoneInfo("Europe/Moscow")


def test_build_weekdays_until():
    rule = recur.build_rrule_from_args(
        {
            "recurrence": {
                "freq": "weekly",
                "byday": "weekdays",
                "until": "2026-11-30",
            }
        }
    )
    assert rule is not None
    assert "FREQ=WEEKLY" in rule
    assert "BYDAY=MO,TU,WE,TH,FR" in rule
    assert "UNTIL=20261130" in rule
    assert "по будням" in recur.human_recurrence(rule)
    assert "30.11.2026" in recur.human_recurrence(rule)


def test_next_after_weekdays():
    start = datetime(2026, 10, 17, 10, 0, tzinfo=TZ)  # Saturday
    rule = "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;UNTIL=20261130T235959Z"
    nxt = recur.next_after(start, rule, after=start)
    assert nxt is not None
    assert nxt.weekday() == 0  # Monday
    assert nxt.day == 19


def test_create_event_passes_recurrence(monkeypatch):
    from assistant.services import calendar as cal
    from assistant.services import gpt_calendar_tools as g

    monkeypatch.setattr(g, "has_google_calendar", lambda uid: True)
    monkeypatch.setattr(cal, "unresolved_attendee_names", lambda *a, **k: [])
    monkeypatch.setattr(cal, "validate_meeting_slot", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(g, "_attach_online", lambda *a, **k: "")
    captured = {}

    def create(uid, parsed, **k):
        captured["parsed"] = parsed
        st = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
        return {
            "event_id": "series1",
            "calendar_id": "primary",
            "summary": parsed["title"],
            "start": st,
            "end": st.replace(hour=10, minute=30),
            "html_link": "https://calendar.google.com/event",
            "recurrence": list(parsed.get("recurrence") or []),
        }

    monkeypatch.setattr(cal, "create_event", create)
    store = {}

    def rec(owner_uid, kind, status, *, title, payload=None, before=None, result=None):
        aid = "a1"
        store[aid] = {"result": result or {}}
        return aid

    monkeypatch.setattr(g, "record_action", rec)
    ctx = g.GptCalendarContext(uid=1)
    out = g.tool_calendar_create_event(
        ctx,
        {
            "title": "Стендап",
            "start": "2026-10-19T10:00",
            "recurrence": {"freq": "weekly", "byday": "weekdays", "until": "2026-11-30"},
        },
    )
    import json

    data = json.loads(out)
    assert data["created"] is True
    assert data["recurrence"]
    assert captured["parsed"]["recurrence"]
    assert captured["parsed"]["recurrence"][0].startswith("RRULE:")
    assert "BYDAY=MO,TU,WE,TH,FR" in captured["parsed"]["recurrence"][0]


def test_webapp_event_url():
    from assistant.lib.webapp_public import webapp_event_url

    url = webapp_event_url("abc123", calendar_id="primary", day="2026-10-19")
    assert "event=abc123" in url
    assert "cal=primary" in url
    assert "day=2026-10-19" in url
    assert "calendar.google.com" not in url


def test_format_create_reply_uses_webapp(monkeypatch):
    from assistant.services import calendar as cal

    monkeypatch.setenv("WEBAPP_PUBLIC_URL", "https://assistent.networ.ru")
    text = cal.format_create_reply_html(
        {
            "event_id": "e99",
            "calendar_id": "primary",
            "summary": "Синк",
            "start": datetime(2026, 10, 19, 10, 0, tzinfo=TZ),
            "end": datetime(2026, 10, 19, 10, 30, tzinfo=TZ),
            "html_link": "https://calendar.google.com/event?eid=1",
        },
        1,
    )
    assert "assistent.networ.ru/webapp/" in text
    assert "event=e99" in text
    assert "calendar.google.com" not in text


def test_reminder_stores_recurrence(tmp_path, monkeypatch):
    from assistant.stores import reminders_store as rs

    monkeypatch.setenv("REMINDERS_STORE_PATH", str(tmp_path / "reminders.json"))
    rid = rs.add_reminder(
        user_id=1,
        chat_id=1,
        task="Витамины",
        when_iso="2026-10-19T09:00:00+03:00",
        recurrence="RRULE:FREQ=DAILY;COUNT=5",
    )
    row = rs.get_reminder_by_id(rid)
    assert row is not None
    assert row["recurrence"].startswith("RRULE:FREQ=DAILY")
