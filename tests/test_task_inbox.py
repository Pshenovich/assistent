"""Входящие поручения: не в календаре до «Беру», разбор карточками, одно сообщение в день."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.test_canvas_endpoints import _headers
from tests.test_posted_tasks import ASSIGNEE, OWNER, TZ, _delegated, client, sent, store  # noqa: F401

WIN = (datetime(2026, 10, 20, 0, 0, tzinfo=TZ), datetime(2026, 10, 21, 0, 0, tzinfo=TZ))


def _ids(rows):
    return [t["id"] for t in rows]


def test_new_delegated_task_waits_in_inbox(store):
    t = _delegated(store)
    mine = store.create_task(OWNER, title="Себе", start_at=WIN[0] + timedelta(hours=9), tz=TZ)
    assert t["assignee_triage"] == "inbox"
    assert mine["assignee_triage"] == ""
    assert store.list_tasks_in_window(ASSIGNEE, *WIN) == []
    assert not store.task_visible_in_calendar(t, ASSIGNEE)
    assert _ids(store.list_inbox(ASSIGNEE)["items"]) == [t["id"]]
    assert store.list_inbox_user_ids() == [ASSIGNEE]

    row = store.triage_accept(ASSIGNEE, t["id"])
    assert row["assignee_triage"] == "accepted"
    assert _ids(store.list_tasks_in_window(ASSIGNEE, *WIN)) == [t["id"]]
    assert store.list_inbox(ASSIGNEE)["items"] == []
    assert "taken" in [e["kind"] for e in store.list_events(row)]


def test_accept_with_new_date(store):
    t = _delegated(store)
    row = store.triage_accept(ASSIGNEE, t["id"], start_at="2026-10-22T10:00:00", all_day=True, tz=TZ, actor_name="Ульяна")
    assert row["all_day"] is True
    assert row["start_at"].startswith("2026-10-21T21:00")  # 22 окт по Москве
    assert row["schedule_log"][-1]["by_name"] == "Ульяна"


def test_snooze_until_tomorrow_morning(store):
    t = _delegated(store)
    now = datetime(2026, 10, 20, 15, 0, tzinfo=TZ)
    row = store.triage_snooze(ASSIGNEE, t["id"], tz=TZ, now=now)
    assert row["assignee_triage"] == "snoozed"
    assert row["assignee_snoozed_until"] == "2026-10-21T04:00:00+00:00"
    inbox = store.list_inbox(ASSIGNEE, now=now)
    assert inbox["items"] == [] and inbox["snoozed"] == 1
    later = store.list_inbox(ASSIGNEE, now=datetime(2026, 10, 21, 7, 1, tzinfo=TZ))
    assert _ids(later["items"]) == [t["id"]]
    assert store.list_tasks_in_window(ASSIGNEE, *WIN) == []


def test_decline_and_reassign(store):
    t = _delegated(store)
    row = store.triage_decline(ASSIGNEE, t["id"], "Это к маркетингу")
    assert row["assignee_triage"] == "declined"
    assert store.assignee_state(row)["status_note"] == "Это к маркетингу"
    assert store.list_inbox(ASSIGNEE)["items"] == []
    assert store.triage_decline(OWNER, t["id"]) is None  # постановщик не разбирает
    moved = store.update_task(OWNER, t["id"], assignee_user_id=33, assignee_name="Артём", tz=TZ)
    assert moved["assignee_triage"] == "inbox"
    assert _ids(store.list_inbox(33)["items"]) == [t["id"]]


def test_working_outside_inbox_counts_as_taken(store):
    a = _delegated(store, title="A")
    b = _delegated(store, title="B")
    store.set_assignee_status(ASSIGNEE, a["id"], "in_progress")
    store.update_task(ASSIGNEE, b["id"], done=True, tz=TZ)
    assert store.list_inbox(ASSIGNEE)["items"] == []
    assert store.get_task_for_user(ASSIGNEE, a["id"])["assignee_triage"] == "accepted"


def test_no_instant_push_on_assign(store, sent):
    from assistant.lib.task_notify import notify_task_assignee

    t = _delegated(store)
    notify_task_assignee(assignee_user_id=ASSIGNEE, task=t, assigner_name="Вова")
    assert sent == []


def test_digest_line(store, monkeypatch):
    from assistant.lib import task_notify

    monkeypatch.setattr(task_notify, "owner_display_name", lambda uid: "Вова Пшенович")
    assert task_notify.inbox_digest_line(ASSIGNEE) == ""
    for i in range(3):
        _delegated(store, title=f"T{i}")
    line = task_notify.inbox_digest_line(ASSIGNEE)
    assert "Вам поставили 3 задачи (Вова — 3)" in line
    assert "inbox=1" in line


def test_focus_card(store, monkeypatch):
    from assistant.lib import task_notify
    from assistant.services import proactive

    monkeypatch.setattr(task_notify, "owner_display_name", lambda uid: "Вова")
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    assert proactive.rule_task_inbox(ASSIGNEE, now) == []
    a = _delegated(store, title="A")
    _delegated(store, title="B")
    store.triage_snooze(ASSIGNEE, a["id"], tz=TZ, now=now)
    card = proactive.rule_task_inbox(ASSIGNEE, now)[0]
    assert card["type"] == "task_inbox" and card["count"] == 1 and card["snoozed"] == 1
    assert card["title"] == "Вам поставили 1 задачу"
    assert card["source"] == "Вова — 1 · ещё 1 отложено"


def test_triage_endpoints(client, store, sent):
    t = _delegated(store)
    r = client.get("/api/miniapp/calendar/tasks/inbox", headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    assert _ids(r.json()["items"]) == [t["id"]]
    assert r.json()["items"][0]["owner_name"]

    url = f"/api/miniapp/calendar/tasks/{t['id']}/triage"
    assert client.post(url, json={"action": "accept"}, headers=_headers(OWNER)).status_code == 403
    assert client.post(url, json={"action": "nope"}, headers=_headers(ASSIGNEE)).status_code == 400

    r = client.post(url, json={"action": "done", "result": "Таблица готова"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    row = store.get_task_for_user(ASSIGNEE, t["id"])
    assert row["done"] and row["result"] == "Таблица готова" and row["assignee_triage"] == "accepted"
    assert any(uid == OWNER and "Таблица готова" in text for uid, text in sent)

    r = client.post(url, json={"action": "undo", "state": "inbox"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    row = store.get_task_for_user(ASSIGNEE, t["id"])
    assert not row["done"] and row["assignee_triage"] == "inbox"

    sent.clear()
    r = client.post(url, json={"action": "decline", "note": "не ко мне"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    assert [uid for uid, _ in sent] == [OWNER]
    assert "не ко мне" in sent[0][1]


def test_inbox_source_only_when_accessible(client, store, tmp_path, monkeypatch):
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    meeting = notes_store.create_note(ASSIGNEE, "Планёрка продукта", role="transcription")
    private = notes_store.create_note(OWNER, "Личные заметки Вовы")
    seen = _delegated(store, title="Из встречи", note_id=str(meeting["id"]))
    hidden = _delegated(store, title="Из закрытой", note_id=str(private["id"]))
    r = client.get("/api/miniapp/calendar/tasks/inbox", headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    by_id = {t["id"]: t for t in r.json()["items"]}
    assert by_id[seen["id"]]["source"] == {"id": meeting["id"], "title": "Планёрка продукта", "kind": "transcription"}
    assert "source" not in by_id[hidden["id"]]
    notes_store._CONN = None
