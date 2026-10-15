"""Календарь для ИИ в обсуждениях: инструменты и цикл function calling."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from assistant.nlu import llm
from assistant.services import gpt_calendar_tools as g

TZ = ZoneInfo("Europe/Moscow")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    import assistant.services.calendar as cal

    monkeypatch.setattr(cal, "_tz_for", lambda uid: TZ)
    monkeypatch.setattr(g, "has_google_calendar", lambda uid: True)
    monkeypatch.setattr(g, "_contacts", lambda ctx: [
        {"name": "Андрей", "email": "andrey@x.ru", "telegram_username": "andrey"},
    ])
    store = {}

    def rec(owner_uid, kind, status, *, title, payload=None, before=None, result=None):
        aid = f"a{len(store) + 1}"
        store[aid] = {"id": aid, "owner_uid": owner_uid, "kind": kind, "status": status, "title": title,
                      "payload": payload or {}, "before": before or {}, "result": result or {}}
        return aid

    monkeypatch.setattr(g, "record_action", rec)
    monkeypatch.setattr(g, "get_action", lambda aid: store.get(aid))
    yield store


def _ev(title, start, mins, emails=()):
    st = datetime.fromisoformat(start).replace(tzinfo=TZ)
    return {
        "event_id": title, "calendar_id": "primary", "summary": title,
        "start": st, "end": st + timedelta(minutes=mins),
        "raw": {"attendees": [{"email": e} for e in emails]},
    }


def test_time_stats_counts_people_titles(monkeypatch):
    evs = [
        _ev("Планёрка", "2026-10-12T10:00", 60, ["andrey@x.ru"]),
        _ev("Планёрка", "2026-10-13T10:00", 60, ["andrey@x.ru"]),
        _ev("1:1", "2026-10-14T12:00", 30, ["boss@x.ru"]),
    ]
    monkeypatch.setattr(g, "_events_in_window", lambda ctx, a, b, q="": evs)
    ctx = g.GptCalendarContext(uid=1)
    out = json.loads(g.tool_calendar_time_stats(ctx, {"start_date": "2026-10-12", "end_date": "2026-10-16"}))
    assert out["meetings"] == 3
    assert out["hours_in_meetings"] == 2.5
    assert out["top_people_hours"][0] == ["Андрей", 2.0]
    assert out["top_titles_hours"][0] == ["Планёрка", 2.0]


def test_create_event_conflict_returns_alternatives(monkeypatch):
    import assistant.services.calendar as cal

    monkeypatch.setattr(cal, "unresolved_attendee_names", lambda *a, **k: [])
    alt = datetime(2026, 10, 16, 16, 0, tzinfo=TZ)
    monkeypatch.setattr(cal, "validate_meeting_slot", lambda *a, **k: {"ok": False, "owner_conflict": True, "attendee_busy": [], "alt_starts": [alt]})
    called = []
    monkeypatch.setattr(cal, "create_event", lambda *a, **k: called.append(1))
    ctx = g.GptCalendarContext(uid=1)
    out = json.loads(g.tool_calendar_create_event(ctx, {"title": "Синк", "start": "2026-10-16T15:00", "attendees": ["Андрей"]}))
    assert out["conflict"] is True and out["alternatives"] == ["2026-10-16 16:00"]
    assert not called and not ctx.actions


def test_create_event_records_undoable_action(monkeypatch, _env):
    import assistant.services.calendar as cal

    monkeypatch.setattr(cal, "unresolved_attendee_names", lambda *a, **k: [])
    monkeypatch.setattr(cal, "validate_meeting_slot", lambda *a, **k: {"ok": True})
    st = datetime(2026, 10, 16, 15, 0, tzinfo=TZ)
    monkeypatch.setattr(cal, "create_event", lambda uid, parsed, **k: {
        "event_id": "e1", "calendar_id": "primary", "summary": parsed["title"], "start": st, "end": st + timedelta(minutes=30), "html_link": "L"})
    monkeypatch.setattr(g, "_attach_online", lambda ctx, created, online: "")
    ctx = g.GptCalendarContext(uid=1)
    out = json.loads(g.tool_calendar_create_event(ctx, {"title": "Синк", "start": "2026-10-16T15:00", "attendees": ["Андрей"]}))
    assert out["created"] is True
    a = _env[ctx.actions[0]]
    assert a["kind"] == "event_create" and a["status"] == "done"
    assert "15:00–15:30" in a["result"]["when"]
    deleted = []
    monkeypatch.setattr(cal, "delete_event", lambda uid, eid, calendar_id=None: deleted.append(eid))
    g.undo_action(a)
    assert deleted == ["e1"]


def test_missing_contact_is_reported_not_created(monkeypatch):
    import assistant.services.calendar as cal

    monkeypatch.setattr(cal, "unresolved_attendee_names", lambda *a, **k: ["Петя"])
    ctx = g.GptCalendarContext(uid=1)
    out = json.loads(g.tool_calendar_create_event(ctx, {"title": "Синк", "start": "2026-10-16T15:00", "attendees": ["Петя"]}))
    assert "Петя" in out["error"]


def test_propose_delete_only_proposes(monkeypatch, _env):
    import assistant.services.calendar as cal

    monkeypatch.setattr(cal, "get_event", lambda *a, **k: {"summary": "Синк", "start": {"dateTime": "2026-10-16T15:00:00+03:00"}, "end": {"dateTime": "2026-10-16T15:30:00+03:00"}})
    removed = []
    monkeypatch.setattr(cal, "delete_event", lambda *a, **k: removed.append(1))
    ctx = g.GptCalendarContext(uid=1)
    out = json.loads(g.tool_propose_delete(ctx, {"kind": "event", "id": "e1"}))
    assert out["proposed"] is True and not removed
    a = _env[ctx.actions[0]]
    assert a["status"] == "proposed" and a["kind"] == "event_delete"
    g.apply_delete(a)
    assert removed == [1]


def test_runner_catches_errors():
    ctx = g.GptCalendarContext(uid=1)
    run = g.make_runner(ctx)
    assert "error" in json.loads(run("calendar_events", {"start_date": "плохо"}))
    assert "error" in json.loads(run("nope", {}))


def test_tool_loop_runs_tools_then_answers(monkeypatch):
    replies = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "calendar_events", "arguments": json.dumps({"start_date": "2026-10-16", "end_date": "2026-10-16"})}}]}}]},
        {"choices": [{"message": {"content": "Завтра одна встреча."}}]},
    ]
    sent = []

    def fake(payload, operation, timeout):
        sent.append(payload)
        return replies[len(sent) - 1]

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake)
    calls = []
    data = llm._run_tool_loop({"model": "m", "messages": [{"role": "user", "content": "что завтра?"}]},
                              [{"type": "function"}], lambda n, a: calls.append((n, a)) or '{"items": []}',
                              operation="ask", timeout=10)
    assert calls == [("calendar_events", {"start_date": "2026-10-16", "end_date": "2026-10-16"})]
    assert data["choices"][0]["message"]["content"] == "Завтра одна встреча."
    assert sent[1]["messages"][-1]["role"] == "tool"


def test_tool_loop_falls_back_without_tools(monkeypatch):
    def fake(payload, operation, timeout):
        if "tools" in payload:
            raise RuntimeError("tools not supported")
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake)
    data = llm._run_tool_loop({"model": "m", "messages": []}, [{"type": "function"}], lambda n, a: "", operation="ask", timeout=10)
    assert data["choices"][0]["message"]["content"] == "ok"
