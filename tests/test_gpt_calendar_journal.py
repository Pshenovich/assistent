"""Журнал действий ИИ с календарём (SQLite)."""

from assistant.services import gpt_calendar_tools as g


def test_record_get_status_roundtrip():
    aid = g.record_action(7, "event_create", "done", title="Синк", payload={"when": "чт"}, result={"event_id": "e1", "when": "чт 15:00"})
    a = g.get_action(aid)
    assert a["owner_uid"] == 7 and a["result"]["event_id"] == "e1"
    pub = g.public_action(a, viewer_uid=7)
    assert pub["mine"] is True and pub["when"] == "чт 15:00"
    assert g.public_action(a, viewer_uid=8)["mine"] is False
    g.set_action_status(aid, "undone", result={"detail": "отменено"})
    assert g.get_action(aid)["status"] == "undone"


def test_contacts_are_really_loaded(tmp_path, monkeypatch):
    """Регресс: load_contacts принимает только именованные аргументы — раньше инструменты видели пустую книгу."""
    from assistant.stores import contacts_store

    monkeypatch.setenv("CONTACTS_USER_DIR", str(tmp_path / "cu"))
    contacts_store._CONTACTS_CACHE_BY_PATH.clear()
    contacts_store.save_contacts(
        [{"name": "Ульяна Смирнова", "email": "u@x.ru"}, {"name": "Артём", "email": "", "telegram_username": "artem"}],
        telegram_user_id=55,
    )
    ctx = g.GptCalendarContext(uid=55)
    assert len(g._contacts(ctx)) == 2
    import json as _j

    found = _j.loads(g.tool_contacts_find(ctx, {"query": "Ульяну"}))
    assert [c["name"] for c in found["contacts"]] == ["Ульяна Смирнова"]
    found2 = _j.loads(g.tool_contacts_find(ctx, {"query": "Артем"}))
    assert [c["name"] for c in found2["contacts"]] == ["Артём"]


def test_offer_slots_apply_creates_event_and_hides_others(monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    import assistant.services.calendar as cal

    tz = ZoneInfo("Europe/Moscow")
    monkeypatch.setattr(cal, "_tz_for", lambda uid: tz)
    monkeypatch.setattr(g, "_resolve_people", lambda ctx, names: (["u@x.ru"], []))
    monkeypatch.setattr(g, "_attach_online", lambda ctx, created, online: "")
    made = {}

    def fake_create(uid, parsed, **k):
        st = datetime.fromisoformat(parsed["start"])
        made.update(parsed)
        return {"event_id": "ev9", "calendar_id": "primary", "summary": parsed["title"], "start": st,
                "end": st + timedelta(minutes=parsed["duration_min"]), "html_link": "", "attendee_emails": parsed["attendees"]}

    monkeypatch.setattr(cal, "create_event", fake_create)
    ctx = g.GptCalendarContext(uid=77)
    import json as _j

    out = _j.loads(g.tool_offer_slots(ctx, {"slots": ["2026-10-19T11:00", "2026-10-19T15:00"], "duration_min": 45,
                                            "attendees": ["Ульяна", "Артём"]}))
    assert out["offered"] == 2 and len(ctx.actions) == 2
    first = g.get_action(ctx.actions[0])
    assert first["status"] == "proposed" and "11:00–11:45" in g.public_action(first)["when"]
    res = g.apply_slot(first)
    assert made["attendees"] == ["u@x.ru"] and made["duration_min"] == 45
    res.pop("_created")
    g.set_action_status(first["id"], "done", result=res)
    g.dismiss_slot_siblings(first)
    assert g.get_action(ctx.actions[1])["status"] == "dismissed"
    pub = g.public_action(g.get_action(first["id"]), viewer_uid=77)
    assert pub["open"]["type"] == "event" and pub["open"]["id"] == "ev9"


def test_show_cards_for_task(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import assistant.services.calendar as cal
    from assistant.stores import calendar_tasks

    tz = ZoneInfo("Europe/Moscow")
    monkeypatch.setattr(cal, "_tz_for", lambda uid: tz)
    t = calendar_tasks.create_task(88, title="Позвонить в банк", description="", start_at=datetime(2026, 10, 16, 10, 0, tzinfo=tz).isoformat(),
                                   end_at=datetime(2026, 10, 16, 10, 30, tzinfo=tz).isoformat(), assignee_user_id=88,
                                   assignee_email="", assignee_name="Я", all_day=False, tz=tz)
    ctx = g.GptCalendarContext(uid=88)
    g.tool_show_calendar_cards(ctx, {"items": [{"type": "task", "id": str(t["id"])}]})
    pub = g.public_action(g.get_action(ctx.actions[0]), viewer_uid=88)
    assert pub["kind"] == "show_task" and pub["open"] == {"type": "task", "id": str(t["id"]), "date": "2026-10-16", "calendar_id": None}
    assert "10:00" in pub["when"]
