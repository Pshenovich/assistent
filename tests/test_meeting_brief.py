import json
from datetime import datetime, timedelta, timezone

from assistant.services import meeting_brief as mb


def _ev(start, attendees=None):
    return {
        "calendar_id": "primary", "event_id": "e1", "summary": "Синк по пилоту",
        "start": start, "start_iso": start.isoformat(),
        "raw": {"summary": "Синк по пилоту", "description": "<p>Обсудить <b>бюджет</b></p>",
                "attendees": attendees if attendees is not None else [
                    {"email": "me@x.ru", "self": True},
                    {"email": "sasha@x.ru", "displayName": "Саша"},
                    {"email": "room@resource.calendar.google.com", "resource": True},
                ]},
    }


def test_enabled_flags(monkeypatch):
    monkeypatch.delenv("MEETING_BRIEFS_ENABLED", raising=False)
    assert not mb.briefs_enabled_for(1)
    monkeypatch.setenv("MEETING_BRIEFS_ENABLED", "1")
    assert mb.briefs_enabled_for(1)
    monkeypatch.setenv("MEETING_BRIEFS_USERS", "7, 8")
    assert mb.briefs_enabled_for(7) and not mb.briefs_enabled_for(1)


def test_attendees_skip_self_and_resources():
    ev = _ev(datetime.now(timezone.utc))
    assert mb._attendees(ev["raw"]) == [{"email": "sasha@x.ru", "name": "Саша"}]


def test_collect_facts_and_message(monkeypatch):
    from assistant.lib import meeting_context
    from assistant.services import gpt_calendar_tools as gct
    from assistant.nlu import llm

    start = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    calls = {}

    def fake_history(ctx, args):
        calls["args"] = args
        return json.dumps({"person": "Саша", "meetings": [
            {"title": "Синк по пилоту", "start": "15 окт", "materials": [{"tasks": [{"task": "Прислать смету"}]}]},
            {"title": "Старое", "start": "1 окт"}, {"title": "Ещё", "start": "2 сен"},
        ], "tasks_given": [{"title": "Договор", "status": "выполнена"}, {"title": "Смета"}]}, ensure_ascii=False)

    monkeypatch.setattr(gct, "tool_person_history", fake_history)
    monkeypatch.setattr(meeting_context, "_previous_brief", lambda uid, title, before, nid: "15 окт: решили пилот")
    facts = mb.collect_facts(1, _ev(start))
    assert calls["args"]["person"] == "sasha@x.ru"
    assert calls["args"]["end_date"] == "2026-10-22"
    p = facts["people"][0]
    assert len(p["meetings"]) == 2  # с материалами + одна без, остальное отрезано
    assert p["open_tasks_given"] == [{"title": "Смета"}]
    assert facts["series"] == "15 окт: решили пилот"
    assert "бюджет" in facts["meeting"]["description"] and "<b>" not in facts["meeting"]["description"]

    monkeypatch.setattr(llm, "_chat", lambda *a, **k: "```html\n<b>👥 Кто</b>\n<ul><li>Саша — 15 окт</li></ul><script>x</script> 5 < 6\n```")
    text = mb.build_brief_message(1, _ev(start))
    assert text.startswith("📋 <b>Бриф к встрече «Синк по пилоту»</b>")
    assert "• Саша — 15 окт" in text
    assert "<script>" not in text and "&lt;script&gt;" in text and "5 &lt; 6" in text


def test_no_history_no_brief(monkeypatch):
    from assistant.lib import meeting_context
    from assistant.services import gpt_calendar_tools as gct

    monkeypatch.setattr(gct, "tool_person_history", lambda ctx, args: json.dumps({"error": "нет"}))
    monkeypatch.setattr(meeting_context, "_previous_brief", lambda *a: "")
    assert mb.build_brief_message(1, _ev(datetime.now(timezone.utc))) is None


def test_events_due_for_brief(monkeypatch):
    from assistant.services import meeting_reminders as mr
    from assistant.services import calendar as cal
    from assistant.stores import meeting_reminders_sent as sent_store

    now = datetime(2026, 10, 22, 11, 30, tzinfo=timezone.utc)
    soon = _ev(now + timedelta(minutes=30))
    later = dict(_ev(now + timedelta(minutes=40)), event_id="e2")
    solo = dict(_ev(now + timedelta(minutes=30), attendees=[]), event_id="e3")
    monkeypatch.setattr(mr, "fetch_upcoming_timed_events", lambda uid, horizon_min: [soon, later, solo])
    monkeypatch.setattr(cal, "_tz_for", lambda uid: timezone.utc)
    sent = set()
    monkeypatch.setattr(sent_store, "was_sent", lambda u, c, e, s: (c, e, s) in sent)
    due = mb.events_due_for_brief(1, now=now)
    assert [e["event_id"] for e in due] == ["e1"]
    sent.add(("primary", "brief:e1", soon["start_iso"]))
    assert mb.events_due_for_brief(1, now=now) == []
