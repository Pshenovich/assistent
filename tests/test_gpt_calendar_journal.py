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
