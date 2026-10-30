"""Подзадачи: задача → подзадача → под-подзадача, права по цепочке, одна карточка во входящих."""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.test_canvas_endpoints import _headers
from tests.test_posted_tasks import ASSIGNEE, OWNER, TZ, client, sent, store  # noqa: F401

LEAD = 33  # Артём: отвечает за общую задачу
START = datetime(2026, 10, 20, 15, 0, tzinfo=TZ)


def _task(store, title, assignee=OWNER, parent_id=None, owner=OWNER):
    return store.create_task(
        owner, title=title, start_at=START, assignee_user_id=assignee,
        assignee_name={OWNER: "Вова", ASSIGNEE: "Ульяна", LEAD: "Артём"}.get(assignee, "Кто-то"),
        tz=TZ, parent_id=parent_id,
    )


def test_subtask_tree_and_counts(store):
    top = _task(store, "Запустить лендинг", LEAD)
    a = _task(store, "Тексты", ASSIGNEE, top["id"])
    b = _task(store, "Домен", LEAD, top["id"])
    c = _task(store, "DNS", ASSIGNEE, b["id"])
    assert a["parent_id"] == top["id"]
    tree = store.subtasks_brief(top["id"])
    assert [t["title"] for t in tree] == ["Тексты", "Домен"]
    assert [t["title"] for t in tree[1]["subtasks"]] == ["DNS"]
    assert tree[0]["state"] == "assigned"
    assert [t["id"] for t in store.ancestors(c)] == [b["id"], top["id"]]


def test_depth_limit_and_cycles(store):
    top = _task(store, "A")
    mid = _task(store, "B", parent_id=top["id"])
    low = _task(store, "C", parent_id=mid["id"])
    with pytest.raises(ValueError):
        _task(store, "D", parent_id=low["id"])  # третий уровень
    with pytest.raises(ValueError):
        store.set_parent(OWNER, top["id"], low["id"])  # в свою же подзадачу
    other = _task(store, "E")
    other_sub = _task(store, "E1", parent_id=other["id"])
    with pytest.raises(ValueError):
        store.set_parent(OWNER, mid["id"], other_sub["id"])  # mid тянет за собой C → глубже двух
    row = store.set_parent(OWNER, low["id"], other["id"])
    assert row["parent_id"] == other["id"]
    assert store.set_parent(OWNER, low["id"], None)["parent_id"] is None


def test_lead_sees_and_edits_subtask_of_another(store):
    top = _task(store, "Запустить лендинг", LEAD)
    sub = _task(store, "Тексты", ASSIGNEE, top["id"])
    deep = _task(store, "Проверить тон", ASSIGNEE, sub["id"])
    assert store.get_task_for_user(LEAD, deep["id"])  # через две ступени
    row = store.update_task(LEAD, sub["id"], description="тон как на старом сайте")
    assert row["description"] == "тон как на старом сайте"
    stranger = 99
    assert store.get_task_for_user(stranger, sub["id"]) is None
    with pytest.raises(ValueError):
        _task(store, "Чужое", stranger, top["id"], owner=stranger)  # не может вложить в чужую


def test_delete_lifts_children(store):
    top = _task(store, "A")
    mid = _task(store, "B", parent_id=top["id"])
    low = _task(store, "C", parent_id=mid["id"])
    assert store.delete_task(OWNER, mid["id"])
    assert store._get_task_row(low["id"])["parent_id"] == top["id"]


def test_inbox_bundles_siblings_and_children(store):
    top = _task(store, "Запустить лендинг", LEAD)
    a = _task(store, "Тексты", ASSIGNEE, top["id"])
    b = _task(store, "Скриншоты", ASSIGNEE, top["id"])
    mine = _task(store, "Домен", LEAD, top["id"])
    loose = _task(store, "Отдельное", ASSIGNEE)
    uly = store.list_inbox(ASSIGNEE)["items"]
    assert [t["id"] for t in uly] == [a["id"], loose["id"]]
    assert uly[0]["bundle_ids"] == [b["id"]]
    art = store.list_inbox(LEAD)["items"]
    assert [t["id"] for t in art] == [top["id"]]
    assert art[0]["bundle_ids"] == [mine["id"]]


def test_decline_of_subtask_goes_to_lead(store, sent):
    from assistant.lib import task_notify

    top = _task(store, "Запустить лендинг", LEAD)
    sub = _task(store, "Тексты", ASSIGNEE, top["id"])
    row = store.triage_decline(ASSIGNEE, sub["id"], "Не моё")
    task_notify.notify_task_declined(task=row, assignee_name="Ульяна", note="Не моё")
    assert [chat for chat, _ in sent] == [LEAD]


def test_api_parent_and_bundle_triage(client, store):
    top = _task(store, "Запустить лендинг", LEAD)
    a = _task(store, "Тексты", ASSIGNEE, top["id"])
    b = _task(store, "Скриншоты", ASSIGNEE, top["id"])
    got = client.get(f"/api/miniapp/calendar/tasks/{top['id']}", headers=_headers(OWNER)).json()["task"]
    assert got["subtasks_total"] == 2 and got["subtasks_done"] == 0
    sub = client.get(f"/api/miniapp/calendar/tasks/{a['id']}", headers=_headers(LEAD)).json()["task"]
    assert sub["parents"][0]["title"] == "Запустить лендинг"

    inbox = client.get("/api/miniapp/calendar/tasks/inbox", headers=_headers(ASSIGNEE)).json()["items"]
    assert [i["id"] for i in inbox] == [a["id"]]
    assert [i["title"] for i in inbox[0]["bundle_items"]] == ["Скриншоты"]
    r = client.post(
        f"/api/miniapp/calendar/tasks/{a['id']}/triage",
        json={"action": "accept", "with_ids": [b["id"]]}, headers=_headers(ASSIGNEE),
    )
    assert r.status_code == 200
    assert store._get_task_row(b["id"])["assignee_triage"] == "accepted"

    other = _task(store, "Другое", OWNER)
    r = client.patch(f"/api/miniapp/calendar/tasks/{b['id']}", json={"parent_id": other["id"]}, headers=_headers(OWNER))
    assert r.status_code == 200 and r.json()["task"]["parent_id"] == other["id"]
    r = client.patch(f"/api/miniapp/calendar/tasks/{b['id']}", json={"parent_id": None}, headers=_headers(OWNER))
    assert r.json()["task"]["parent_id"] is None
    r = client.patch(f"/api/miniapp/calendar/tasks/{top['id']}", json={"parent_id": a["id"]}, headers=_headers(OWNER))
    assert r.status_code == 400
