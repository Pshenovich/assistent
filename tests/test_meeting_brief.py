import json
from datetime import datetime, timedelta, timezone

import pytest

from assistant.services import meeting_brief as mb


@pytest.fixture(autouse=True)
def _no_topics(monkeypatch):
    """Бриф без базы тем: тема — только сама встреча, чатов нет."""
    from assistant.services import meeting_topics

    monkeypatch.setattr(meeting_topics, "resolve", lambda uid, title, **k: {
        "topic": None, "titles": {title}, "meetings": [], "chats": [], "candidates": {"titles": [], "chats": []}})


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
    series = [{"date": "2026-10-15", "title": "Синк по пилоту", "note_id": 5, "topic": "пилот",
               "decisions": ["решили пилот"], "open_questions": [], "tasks": []}]
    monkeypatch.setattr(mb, "_series", lambda uid, meetings: series)
    monkeypatch.setattr(mb, "related_items", lambda *a, **k: [])
    facts = mb.collect_facts(1, _ev(start))
    assert calls["args"]["person"] == "sasha@x.ru"
    assert calls["args"]["end_date"] == "2026-10-22"
    p = facts["people"][0]
    # только «когда виделись»: без материалов и поручений с чужих встреч
    assert p["meetings"] == [{"title": "Синк по пилоту", "start": "15 окт"},
                             {"title": "Старое", "start": "1 окт"}, {"title": "Ещё", "start": "2 сен"}]
    assert "open_tasks_given" not in p
    assert facts["series"][0]["decisions"] == ["решили пилот"]
    assert "бюджет" in facts["meeting"]["description"] and "<b>" not in facts["meeting"]["description"]

    monkeypatch.setattr(llm, "_chat", lambda *a, **k: "```html\n<b>👥 Кто</b>\n<ul><li>Саша — 15 окт</li></ul><script>x</script> 5 < 6\n```")
    text = mb.build_brief_message(1, _ev(start))
    assert text.startswith("📋 <b>Бриф к встрече «Синк по пилоту»</b>")
    assert "• Саша — 15 окт" in text
    assert "<script>" not in text and "&lt;script&gt;" in text and "5 &lt; 6" in text


def test_no_history_no_brief(monkeypatch):
    from assistant.services import gpt_calendar_tools as gct

    monkeypatch.setattr(gct, "tool_person_history", lambda ctx, args: json.dumps({"error": "нет"}))
    monkeypatch.setattr(mb, "related_items", lambda *a, **k: [])
    assert mb.build_brief_message(1, _ev(datetime.now(timezone.utc))) is None


def test_people_only_is_not_a_brief(monkeypatch):
    """Одни «кто и когда виделись» + ссылки созвона — не бриф."""
    from assistant.services import gpt_calendar_tools as gct

    monkeypatch.setattr(gct, "tool_person_history", lambda ctx, args: json.dumps(
        {"person": "Саша", "meetings": [{"title": "Демо", "start": "2026-09-25 12:00"}]}, ensure_ascii=False))
    monkeypatch.setattr(mb, "related_items", lambda *a, **k: [])
    ev = _ev(datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc))
    ev["raw"]["description"] = (
        "Zoom: https://us04web.zoom.us/j/78438719306?pwd=abc\nИдентификатор конференции: 784 3871 9306\n"
        "Код доступа: 1\nТелемост: https://telemost.yandex.ru/j/74677864151616")
    facts = mb.collect_facts(1, ev)
    assert facts["people"] and facts["meeting"]["description"] == ""
    assert facts["join_url"].startswith("https://us04web.zoom.us/j/")
    assert not mb.has_substance(facts)
    assert mb.build_brief_message(1, ev) is None


def test_today_meetings_not_in_people(monkeypatch):
    from assistant.services import gpt_calendar_tools as gct

    monkeypatch.setattr(gct, "_tz", lambda uid: timezone.utc)
    monkeypatch.setattr(gct, "tool_person_history", lambda ctx, args: json.dumps({"person": "Саша", "meetings": [
        {"title": "Синк по пилоту", "start": "2026-10-22 12:00"}, {"title": "Позже", "start": "2026-10-22 15:00"},
        {"title": "Утро", "start": "2026-10-22 09:00"}]}, ensure_ascii=False))
    monkeypatch.setattr(mb, "related_items", lambda *a, **k: [])
    facts = mb.collect_facts(1, _ev(datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)))
    assert facts["people"][0]["meetings"] == [{"title": "Утро", "start": "2026-10-22 09:00"}]


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
