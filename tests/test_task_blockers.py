"""«Заблокировано»: блокеры задачи, автоснятие, карточка в Фокусе у человека-блокера, Битрикс24."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from tests.test_canvas_endpoints import BOT_TOKEN, _headers

OWNER = 11
ASSIGNEE = 22
BLOCKER = 33
STRANGER = 44
TZ = ZoneInfo("Europe/Moscow")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "calendar_tasks.sqlite"))
    from assistant.stores import calendar_tasks

    calendar_tasks._CONN = None  # type: ignore[attr-defined]
    monkeypatch.setattr(calendar_tasks, "_meeting_task_changed", lambda *_: None)
    yield calendar_tasks
    calendar_tasks._CONN = None  # type: ignore[attr-defined]


@pytest.fixture()
def client(store, monkeypatch):
    from assistant.lib import telegram_notify

    monkeypatch.setattr(telegram_notify, "send_message", lambda *a, **k: True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_sync_task_google", lambda *a, **k: None)
    return TestClient(usage_server.app, raise_server_exceptions=False)


def _task(store, owner=OWNER, assignee=ASSIGNEE, title="Отчёт", name="Ульяна"):
    return store.create_task(
        owner, title=title, start_at=datetime(2026, 11, 20, 15, 0, tzinfo=TZ),
        assignee_user_id=assignee, assignee_name=name, tz=TZ,
    )


def _block(client, uid, task_id, blockers, note=""):
    return client.post(f"/api/miniapp/calendar/tasks/{task_id}/block",
                       json={"blockers": blockers, "note": note}, headers=_headers(uid))


def test_assignee_and_owner_can_block_stranger_cannot(client, store):
    t = _task(store)
    r = _block(client, ASSIGNEE, t["id"], [{"kind": "text", "text": "Ждём доступ к серверу"}])
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["event"]["assignee_state"]["status"] == "blocked"
    assert task["blockers"][0]["kind"] == "text"
    assert "доступ" in task["event"]["assignee_state"]["status_note"]

    r = _block(client, OWNER, t["id"], [{"kind": "external", "name": "Пётр", "company": "Клиент"}])
    assert r.status_code == 200
    assert len(r.json()["task"]["blockers"]) == 2

    r = _block(client, STRANGER, t["id"], [{"kind": "text", "text": "x"}])
    assert r.status_code == 400


def test_block_needs_blockers_and_status_route_refuses_blocked(client, store):
    t = _task(store)
    assert _block(client, ASSIGNEE, t["id"], []).status_code == 400
    r = client.post(f"/api/miniapp/calendar/tasks/{t['id']}/status",
                    json={"status": "blocked"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 400
    assert "чем заблокировано" in r.json()["detail"]


def test_unblock_returns_to_in_progress(client, store):
    from assistant.stores import task_blockers

    t = _task(store)
    _block(client, ASSIGNEE, t["id"], [{"kind": "text", "text": "a"}, {"kind": "text", "text": "b"}])
    task = store.get_task_for_user(OWNER, t["id"])
    first = task_blockers.active_for_task(t["id"])[0]
    r = client.post(f"/api/miniapp/calendar/tasks/{t['id']}/unblock", json={"blocker_id": first["id"]},
                    headers=_headers(OWNER))
    assert r.status_code == 200
    assert r.json()["task"]["event"]["assignee_state"]["status"] == "blocked"
    assert len(r.json()["task"]["blockers"]) == 1
    r = client.post(f"/api/miniapp/calendar/tasks/{t['id']}/unblock", json={}, headers=_headers(ASSIGNEE))
    assert r.json()["task"]["event"]["assignee_state"]["status"] == "in_progress"
    assert r.json()["task"]["blockers"] == []
    assert task  # задача на месте


def test_blocking_task_done_unblocks(client, store):
    from assistant.stores import task_blockers

    blocked = _task(store)
    blocker_task = _task(store, owner=OWNER, assignee=BLOCKER, title="Доступы", name="Иван")
    r = _block(client, ASSIGNEE, blocked["id"], [{"kind": "task", "task_id": blocker_task["id"]}])
    # исполнителю задача-блокер недоступна — выбирать можно только из своих
    assert r.status_code == 400
    r = _block(client, OWNER, blocked["id"], [{"kind": "task", "task_id": blocker_task["id"]}])
    assert r.status_code == 200, r.text

    # у задачи-блокера — метка «Блокирует»; исполнителю-блокеру чужая задача не видна по названию
    held = client.get(f"/api/miniapp/calendar/tasks/{blocker_task['id']}", headers=_headers(BLOCKER)).json()
    item = held.get("task") or held.get("item") or held
    assert item["blocking"][0]["accessible"] is False
    assert "Отчёт" not in item["blocking"][0]["title"]
    own = client.get(f"/api/miniapp/calendar/tasks/{blocker_task['id']}", headers=_headers(OWNER)).json()
    own_item = own.get("task") or own.get("item") or own
    assert own_item["blocking"][0]["title"] == "Отчёт"

    # сдаёт исполнитель задачи-блокера
    r = client.patch(f"/api/miniapp/calendar/tasks/{blocker_task['id']}", json={"done": True},
                     headers=_headers(BLOCKER))
    assert r.status_code == 200, r.text
    row = store.get_task_for_user(ASSIGNEE, blocked["id"])
    assert row["assignee_status"] == "in_progress"
    assert task_blockers.active_for_task(blocked["id"]) == []
    kinds = [e["kind"] for e in store.list_events(row)]
    assert "blocker_resolved" in kinds
    # «Можно продолжать» у исполнителя
    from assistant.services import proactive

    cards = proactive.rule_task_unblocked(ASSIGNEE, proactive._now())
    assert cards and cards[0]["type"] == "task_unblocked"


def test_user_blocker_sees_focus_card_and_can_resolve(client, store, monkeypatch):
    from assistant.services import proactive

    t = _task(store)
    r = _block(client, ASSIGNEE, t["id"], [{"kind": "user", "user_id": str(BLOCKER), "name": "Иван"}], note="Нужен макет")
    assert r.status_code == 200
    cards = proactive.rule_task_blocking(BLOCKER, proactive._now())
    assert cards and cards[0]["type"] == "task_blocking"
    it = cards[0]["blocks"][0]
    assert it["title"] == "Отчёт" and it["note"] == "Нужен макет"
    assert it["accessible"] is False
    # внешний человек и текст Фокус никому не создают
    t2 = _task(store, title="Другое")
    _block(client, ASSIGNEE, t2["id"], [{"kind": "external", "name": "Пётр"}, {"kind": "text", "text": "погода"}])
    assert len(proactive.rule_task_blocking(BLOCKER, proactive._now())[0]["blocks"]) == 1

    r = client.post(f"/api/miniapp/proactive/blocking/{it['blocker_id']}", json={"how": "not_me"},
                    headers=_headers(BLOCKER))
    assert r.status_code == 200, r.text
    assert proactive.rule_task_blocking(BLOCKER, proactive._now()) == []
    row = store.get_task_for_user(ASSIGNEE, t["id"])
    assert row["assignee_status"] == "in_progress"
    assert any("не блокирую" in (e.get("text") or "") for e in store.list_events(row))
    # чужой блокер снять нельзя
    r = client.post(f"/api/miniapp/proactive/blocking/{it['blocker_id']}", json={"how": "done"},
                    headers=_headers(STRANGER))
    assert r.status_code == 400


def test_message_does_not_drop_blocked(client, store):
    t = _task(store)
    _block(client, ASSIGNEE, t["id"], [{"kind": "text", "text": "ждём"}])
    store.post_message(ASSIGNEE, t["id"], "Есть новости?", actor_name="Ульяна")
    assert store.get_task_for_user(OWNER, t["id"])["assignee_status"] == "blocked"


def test_in_progress_clears_blockers(client, store):
    from assistant.stores import task_blockers

    t = _task(store)
    _block(client, ASSIGNEE, t["id"], [{"kind": "user", "user_id": str(BLOCKER), "name": "Иван"}])
    r = client.post(f"/api/miniapp/calendar/tasks/{t['id']}/status", json={"status": "in_progress"},
                    headers=_headers(ASSIGNEE))
    assert r.status_code == 200
    assert task_blockers.active_for_task(t["id"]) == []


def test_cancelled_task_releases_its_blockers(client, store):
    from assistant.stores import task_blockers

    t = _task(store)
    _block(client, OWNER, t["id"], [{"kind": "user", "user_id": str(BLOCKER), "name": "Иван"}])
    store.cancel_task(OWNER, t["id"])
    assert task_blockers.active_for_user(BLOCKER) == []


def test_subtask_and_meeting_state():
    from assistant.services import meeting_tasks
    from assistant.stores import calendar_tasks as ct

    row = {"id": 1, "owner_user_id": "1", "assignee_user_id": "2", "assignee_status": "blocked",
           "assignee_status_note": "задача «Доступы»", "assignee_triage": "accepted", "done": False}
    assert ct.subtask_state(row) == "blocked"
    st = meeting_tasks.status_of({"link": {"task_id": 1}}, row)
    assert st["code"] == "blocked" and st["label"].startswith("Заблокировано")


def test_parse_bitrix_ref():
    from assistant.stores.task_blockers import parse_bitrix_ref

    assert parse_bitrix_ref("123") == ("123", "")
    assert parse_bitrix_ref("#45") == ("45", "")
    url = "https://x.bitrix24.ru/company/personal/user/7/tasks/task/view/987/"
    assert parse_bitrix_ref(url) == ("987", url)
    assert parse_bitrix_ref("какой-то текст") == ("", "")


def test_bitrix_blocker_and_tracker_task(client, store, monkeypatch):
    from assistant.trackers import bitrix24

    t = _task(store)
    r = _block(client, ASSIGNEE, t["id"], [{"kind": "bitrix", "ref": "https://x.bitrix24.ru/workgroups/group/5/tasks/task/view/77/",
                                            "title": "Договор"}])
    assert r.status_code == 200, r.text
    b = r.json()["task"]["blockers"][0]
    assert b["external_id"] == "77" and b["url"].startswith("https://") and b["title"] == "Договор"
    assert _block(client, ASSIGNEE, t["id"], [{"kind": "bitrix", "ref": "не ссылка"}]).status_code == 400

    calls = []

    def fake_call(base, method, params=None):
        calls.append(method)
        if method == "tasks.task.get":
            return {"task": {"id": "77", "title": "Договор", "status": "3", "stageId": "9", "groupId": "5"}}
        if method == "task.stages.get":
            return {"9": {"ID": "9", "TITLE": "На согласовании", "SORT": 1}}
        if method == "tasks.task.list":
            return {"tasks": [{"id": "77", "title": "Договор", "status": "3", "groupId": "5"}]}
        raise AssertionError(method)

    monkeypatch.setattr(bitrix24, "call", fake_call)
    monkeypatch.setattr(bitrix24.Bitrix24Tracker, "_creds",
                        lambda self, uid: {"secret": "https://x.bitrix24.ru/rest/7/abc/", "portal": "https://x.bitrix24.ru",
                                           "account_id": "7", "kind": "webhook"})
    bitrix24._drop_stage_cache(ASSIGNEE)
    r = client.get("/api/miniapp/trackers/bitrix24/tasks/77", headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["stage"] == "На согласовании" and task["status"] == "Выполняется"
    assert task["url"] == "https://x.bitrix24.ru/workgroups/group/5/tasks/task/view/77/"
    r = client.get("/api/miniapp/trackers/bitrix24/tasks", params={"q": "Дого"}, headers=_headers(ASSIGNEE))
    assert r.status_code == 200 and r.json()["tasks"][0]["external_id"] == "77"


def test_comment_survives_adding_and_removing_blockers(client, store):
    from assistant.stores import task_blockers

    t = _task(store)
    _block(client, ASSIGNEE, t["id"], [{"kind": "text", "text": "a"}], note="Ждём демо")
    _block(client, OWNER, t["id"], [{"kind": "text", "text": "b"}])
    assert store.get_task_for_user(OWNER, t["id"])["assignee_status_note"] == "Ждём демо"
    first = task_blockers.active_for_task(t["id"])[0]
    client.post(f"/api/miniapp/calendar/tasks/{t['id']}/unblock", json={"blocker_id": first["id"]}, headers=_headers(OWNER))
    assert store.get_task_for_user(OWNER, t["id"])["assignee_status_note"] == "Ждём демо"
    # без комментария — строка обновляется по оставшимся блокерам
    t2 = _task(store, title="Другая")
    _block(client, ASSIGNEE, t2["id"], [{"kind": "text", "text": "a"}, {"kind": "text", "text": "b"}])
    b0 = task_blockers.active_for_task(t2["id"])[0]
    client.post(f"/api/miniapp/calendar/tasks/{t2['id']}/unblock", json={"blocker_id": b0["id"]}, headers=_headers(OWNER))
    assert store.get_task_for_user(OWNER, t2["id"])["assignee_status_note"] == "b"
