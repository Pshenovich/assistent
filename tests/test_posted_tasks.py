"""«Поручено другим»: видел / в работе / вопрос, «Напомнить», «Принять всё», вечерняя сводка в Фокусе."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from tests.test_canvas_endpoints import BOT_TOKEN, _headers

OWNER = 11
ASSIGNEE = 22
TZ = ZoneInfo("Europe/Moscow")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "calendar_tasks.sqlite"))
    from assistant.stores import calendar_tasks

    calendar_tasks._CONN = None  # type: ignore[attr-defined]
    yield calendar_tasks
    calendar_tasks._CONN = None  # type: ignore[attr-defined]


@pytest.fixture()
def sent(monkeypatch):
    from assistant.lib import telegram_notify

    out: list[tuple[int, str]] = []

    def _send(chat_id, text, **_):
        out.append((int(chat_id), text))
        return True

    monkeypatch.setattr(telegram_notify, "send_message", _send)
    return out


@pytest.fixture()
def client(store, sent, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_sync_task_google", lambda *a, **k: None)
    return TestClient(usage_server.app, raise_server_exceptions=False)


def _delegated(store, **kw):
    return store.create_task(
        OWNER,
        title=kw.pop("title", "Отчёт"),
        start_at=kw.pop("start_at", datetime(2026, 10, 20, 15, 0, tzinfo=TZ)),
        assignee_user_id=ASSIGNEE,
        assignee_name="Ульяна",
        tz=TZ,
        **kw,
    )


def test_seen_and_status_only_by_assignee(store):
    t = _delegated(store)
    assert store.mark_seen(OWNER, t["id"]) is None  # автор — не «видел»
    seen = store.mark_seen(ASSIGNEE, t["id"])
    first = seen["assignee_seen_at"]
    assert first
    assert store.mark_seen(ASSIGNEE, t["id"])["assignee_seen_at"] == first  # не перезаписываем

    with pytest.raises(ValueError):
        store.set_assignee_status(ASSIGNEE, t["id"], "question", "")
    with pytest.raises(ValueError):
        store.set_assignee_status(ASSIGNEE, t["id"], "whatever")
    assert store.set_assignee_status(OWNER, t["id"], "in_progress") is None

    row = store.set_assignee_status(ASSIGNEE, t["id"], "question", "Какой период?")
    st = store.assignee_state(row)
    assert st["status"] == "question" and st["status_note"] == "Какой период?"
    ev = store.as_calendar_event(row, tz=TZ, viewer_id=OWNER)
    assert ev["assignee_state"]["status"] == "question"

    row = store.set_assignee_status(ASSIGNEE, t["id"], "in_progress", "игнор")
    assert store.assignee_state(row)["status_note"] == ""


def test_reassign_resets_state(store):
    t = _delegated(store)
    store.set_assignee_status(ASSIGNEE, t["id"], "in_progress")
    store.mark_nudged(t["id"])
    row = store.update_task(OWNER, t["id"], assignee_user_id=33, assignee_name="Артём", tz=TZ)
    assert store.assignee_state(row) == {"seen_at": "", "status": "", "status_note": "", "status_at": "", "nudged_at": ""}


def test_archive_done_posted(store):
    a = _delegated(store, title="A")
    b = _delegated(store, title="B")
    mine = store.create_task(OWNER, title="Себе", start_at=datetime(2026, 10, 20, 9, 0, tzinfo=TZ), tz=TZ)
    store.update_task(OWNER, a["id"], done=True, tz=TZ)
    store.update_task(OWNER, mine["id"], done=True, tz=TZ)
    assert store.archive_done_posted(OWNER) == 1
    current = {t["id"] for t in store.list_posted_tasks(OWNER, include_done=True, archived=False)}
    assert current == {b["id"]}
    assert store.get_task(OWNER, mine["id"])["owner_archived"] is False


def test_nudge_endpoint_sends_and_cools_down(client, store, sent):
    t = _delegated(store)
    r = client.post(f"/api/miniapp/calendar/tasks/{t['id']}/nudge", headers=_headers(ASSIGNEE))
    assert r.status_code == 403
    r = client.post(f"/api/miniapp/calendar/tasks/{t['id']}/nudge", headers=_headers(OWNER))
    assert r.status_code == 200, r.text
    assert r.json()["task"]["event"]["assignee_state"]["nudged_at"]
    assert sent and sent[0][0] == ASSIGNEE and "напоминает о задаче" in sent[0][1]
    r = client.post(f"/api/miniapp/calendar/tasks/{t['id']}/nudge", headers=_headers(OWNER))
    assert r.status_code == 429 and "Уже напомнили" in r.json()["detail"]
    assert len(sent) == 1


def test_status_question_notifies_owner_once(client, store, sent):
    t = _delegated(store)
    url = f"/api/miniapp/calendar/tasks/{t['id']}/status"
    r = client.post(url, json={"status": "in_progress"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    assert sent == []  # «в работе» — без пуша
    r = client.post(url, json={"status": "question", "note": "Какой период?"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 200
    assert len(sent) == 1 and sent[0][0] == OWNER and "Какой период?" in sent[0][1]
    client.post(url, json={"status": "question", "note": "Какой период?"}, headers=_headers(ASSIGNEE))
    assert len(sent) == 1  # тот же вопрос второй раз — без пуша
    r = client.post(url, json={"status": "in_progress"}, headers=_headers(OWNER))
    assert r.status_code == 403


def test_seen_and_accept_done_endpoints(client, store):
    t = _delegated(store)
    r = client.post(f"/api/miniapp/calendar/tasks/{t['id']}/seen", headers=_headers(ASSIGNEE))
    assert r.status_code == 200 and r.json()["assignee_state"]["seen_at"]
    r = client.get("/api/miniapp/calendar/tasks/posted", headers=_headers(OWNER))
    item = r.json()["items"][0]
    assert item["event"]["assignee_state"]["seen_at"] and item["tracker_links"] == []
    store.update_task(OWNER, t["id"], done=True, tz=TZ)
    r = client.post("/api/miniapp/calendar/tasks/posted/accept-done", headers=_headers(OWNER))
    assert r.json() == {"ok": True, "archived": 1}


def test_posted_overdue_card_evening_only():
    from assistant.services import proactive

    late = {"id": 5, "title": "Договор", "owner_user_id": str(OWNER), "assignee_user_id": str(ASSIGNEE),
            "assignee_name": "Артём", "start_at": "2026-10-20T11:00:00+00:00", "end_at": "2026-10-20T11:30:00+00:00"}
    done = {**late, "id": 6, "done": True}
    morning = datetime(2026, 10, 23, 9, 0, tzinfo=TZ).astimezone(timezone.utc)
    assert proactive.rule_posted_overdue([late], morning, TZ) == []
    evening = morning + timedelta(hours=10)
    card = proactive.rule_posted_overdue([late, done], evening, TZ)[0]
    assert card["type"] == "posted_overdue" and card["title"] == "1 поручение просрочено"
    assert card["actions"][0]["act"] == "nudge_tasks" and card["actions"][0]["payload"] == {"task_ids": [5]}
    assert proactive._posted_by(late, str(OWNER)) and not proactive._posted_by(late, str(ASSIGNEE))


def test_assignee_open_or_edit_marks_seen(client, store):
    t = _delegated(store)
    r = client.get(f"/api/miniapp/calendar/tasks/{t['id']}", headers=_headers(OWNER))
    assert r.status_code == 200 and r.json()["task"]["event"]["assignee_state"]["seen_at"] == ""  # автор — не «просмотрено»
    r = client.get(f"/api/miniapp/calendar/tasks/{t['id']}", headers=_headers(ASSIGNEE))
    assert r.status_code == 200 and r.json()["task"]["event"]["assignee_state"]["seen_at"]

    t2 = _delegated(store, title="Второе")
    r = client.patch(f"/api/miniapp/calendar/tasks/{t2['id']}", json={"checklist": [{"text": "a", "done": True}]},
                     headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    assert store.assignee_state(store.get_task(OWNER, t2["id"]))["seen_at"]


def test_discussion_question_and_answer(client, store, sent):
    t = _delegated(store)
    url = f"/api/miniapp/calendar/tasks/{t['id']}/messages"
    r = client.post(url, json={"text": "Квартал или 9 месяцев?"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    assert r.json()["task"]["event"]["assignee_state"]["status"] == "question"  # «ждёт» постановщика
    assert sent[-1][0] == OWNER and "Квартал или 9 месяцев?" in sent[-1][1]
    r = client.post(url, json={"text": "Квартал"}, headers=_headers(OWNER))
    st = r.json()["task"]["event"]["assignee_state"]
    assert st["status"] == "in_progress" and st["status_note"] == ""
    assert sent[-1][0] == ASSIGNEE
    assert client.post(url, json={"text": "  "}, headers=_headers(OWNER)).status_code == 400
    assert client.post(url, json={"text": "чужой"}, headers=_headers(99)).status_code == 403
    ev = client.get(f"/api/miniapp/calendar/tasks/{t['id']}/events", headers=_headers(OWNER)).json()["events"]
    assert [e["kind"] for e in ev] == ["created", "seen", "message", "message"]
    assert [e["text"] for e in ev if e["kind"] == "message"] == ["Квартал или 9 месяцев?", "Квартал"]


def test_accept_return_cancel(client, store, sent):
    t = _delegated(store)
    base = f"/api/miniapp/calendar/tasks/{t['id']}"
    assert client.post(base + "/accept", headers=_headers(ASSIGNEE)).status_code == 403
    r = client.patch(base, json={"done": True, "result": "Таблица готова"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 200 and r.json()["task"]["event"]["done_at"]
    r = client.post(base + "/return", json={"text": "Добавь сравнение с прошлым годом"}, headers=_headers(OWNER))
    ev = r.json()["task"]["event"]
    assert ev["done"] is False and ev["done_at"] == "" and ev["assignee_state"]["status"] == "in_progress"
    assert sent[-1][0] == ASSIGNEE and "прошлым годом" in sent[-1][1]
    r = client.post(base + "/accept", headers=_headers(OWNER))
    ev = r.json()["task"]["event"]
    assert ev["outcome"] == "accepted" and ev["owner_archived"] and ev["done"] and ev["accepted_at"]
    kinds = [e["kind"] for e in r.json()["events"]]
    assert kinds[-3:] == ["done", "returned", "accepted"]
    archived = client.get("/api/miniapp/calendar/tasks/posted?archived=1", headers=_headers(OWNER)).json()["items"]
    assert [i["id"] for i in archived] == [t["id"]]

    t2 = _delegated(store, title="Ненужное")
    r = client.post(f"/api/miniapp/calendar/tasks/{t2['id']}/cancel", headers=_headers(OWNER))
    assert r.json()["task"]["event"]["outcome"] == "cancelled"
    assert "отменил" in sent[-1][1]
    assert store.task_visible_in_calendar(store.get_task(OWNER, t2["id"]), ASSIGNEE) is False


def test_legacy_task_events_from_fields(store):
    t = _delegated(store)
    store.set_assignee_status(ASSIGNEE, t["id"], "question", "Какой период?")
    with store._LOCK:  # как у задачи, поставленной до ленты: событий нет, есть только поля
        store._conn().execute("DELETE FROM task_events")
        store._conn().commit()
    ev = store.list_events(store.get_task(OWNER, t["id"]))
    assert [e["kind"] for e in ev] == ["created", "seen", "message"]
    assert ev[2]["text"] == "Какой период?"


def test_photo_allowed_between_task_parties(store):
    _delegated(store)
    assert store.users_share_task(OWNER, ASSIGNEE) and store.users_share_task(ASSIGNEE, OWNER)
    assert not store.users_share_task(OWNER, 99)


def test_reply_keeps_legacy_question(store):
    t = _delegated(store)
    store.set_assignee_status(ASSIGNEE, t["id"], "question", "Какой период?")
    with store._LOCK:  # вопрос из времён до ленты — только в статусе
        store._conn().execute("DELETE FROM task_events")
        store._conn().commit()
    store.post_message(OWNER, t["id"], "Квартал", actor_name="Вова")
    msgs = [(e["actor_user_id"], e["text"]) for e in store.list_events(store.get_task(OWNER, t["id"])) if e["kind"] == "message"]
    assert msgs == [(str(ASSIGNEE), "Какой период?"), (str(OWNER), "Квартал")]
