"""Задача Орбиты ← её дубль в Битрикс24: стадия, срок, «выполнено», исполнитель не я → «Поручил я»."""

from __future__ import annotations

from datetime import datetime

import pytest

from assistant.services import tracker_sync
from assistant.stores import notes as notes_store
from assistant.stores import trackers as tstore
from assistant.trackers import bitrix24 as b24
from tests.test_posted_tasks import TZ, store  # noqa: F401

UID = 11
HOOK = "https://acme.bitrix24.ru/rest/7/abc123xyz/"


class FakeB24:
    def __init__(self):
        self.task = {"id": "900", "title": "Дубль", "status": "3", "stageId": "0", "groupId": "0",
                     "responsibleId": "7", "responsible": {"id": "7", "name": "Вова П"},
                     "deadline": "2026-11-20T19:00:00+03:00"}

    def __call__(self, base, method, params=None):
        if method == "user.current":
            return {"ID": "7", "NAME": "Вова", "LAST_NAME": "П"}
        if method == "tasks.task.get":
            return {"task": dict(self.task)}
        if method in ("task.stages.get", "sonet_group.user.groups"):
            return {} if method == "task.stages.get" else []
        raise AssertionError(method)


@pytest.fixture()
def env(store, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    tstore._CONN = None
    b24._STAGES_CACHE.clear()
    fake = FakeB24()
    monkeypatch.setattr(b24, "call", fake)
    monkeypatch.setattr("assistant.services.calendar._tz_for", lambda uid: TZ)
    monkeypatch.setattr("assistant.services.task_google_sync.sync_task_google", lambda *a, **k: None)
    b24.Bitrix24Tracker().connect_webhook(UID, HOOK)
    task = store.create_task(UID, title="Сделать лендинг", start_at=datetime(2026, 11, 15, 11, 0, tzinfo=TZ), tz=TZ)
    link = tstore.add_task(UID, "bitrix24", external_id="900", url="u", title="Дубль", calendar_task_id=str(task["id"]))
    yield store, fake, task, link
    notes_store._CONN = None
    tstore._CONN = None


def _sync(link):
    return tracker_sync.sync_link(UID, tstore.get_task_link(link["id"]))


def test_deadline_and_status_pulled(env):
    store, fake, task, link = env
    res = _sync(link)
    assert res["changed"]
    row = store.get_task_for_user(UID, task["id"])
    start = datetime.fromisoformat(row["start_at"].replace("Z", "+00:00")).astimezone(TZ)
    assert (start.year, start.month, start.day, start.hour) == (2026, 11, 20, 11)  # дата из Битрикса, время прежнее
    assert not row["done"] and not row.get("assignee_external")
    saved = tstore.get_task_link(link["id"])
    assert saved["status"] == "Выполняется" and saved["deadline"] == "2026-11-20" and saved["synced_at"]
    assert not _sync(link)["changed"]  # повторно — ничего не меняется


def test_closed_marks_done_and_reopen_undoes(env):
    store, fake, task, link = env
    fake.task["status"] = "5"
    _sync(link)
    assert store.get_task_for_user(UID, task["id"])["done"]
    fake.task["status"] = "3"
    _sync(link)
    assert not store.get_task_for_user(UID, task["id"])["done"]


def test_other_responsible_goes_to_posted(env):
    store, fake, task, link = env
    fake.task.update(responsibleId="42", responsible={"id": "42", "name": "Иван Петров"})
    _sync(link)
    row = store.get_task_for_user(UID, task["id"])
    assert row["assignee_external"] and row["assignee_name"] == "Иван Петров"
    assert store.is_external_delegation(row)  # место — «Поручил я»
    links = tracker_sync.sync_for_task(UID, task["id"], max_age_sec=0)
    assert links[0]["responsible_other"] and links[0]["responsible_name"] == "Иван Петров"
    # вернули на меня — снова своя задача
    fake.task.update(responsibleId="7", responsible={"id": "7", "name": "Вова П"})
    _sync(link)
    row = store.get_task_for_user(UID, task["id"])
    assert not row["assignee_external"]


def test_task_delegated_to_orbita_user_untouched(env):
    store, fake, task, link = env
    store.update_task(UID, task["id"], assignee_user_id="22", assignee_name="Ульяна", tz=TZ)
    fake.task.update(status="5", responsibleId="42", responsible={"id": "42", "name": "Иван Петров"})
    assert not _sync(link)["changed"]
    row = store.get_task_for_user(UID, task["id"])
    assert not row["done"] and str(row["assignee_user_id"]) == "22"


def test_due_links_only_with_credentials(env):
    store, fake, task, link = env
    assert [x["id"] for x in tstore.links_to_sync(older_than="9999")] == [link["id"]]
    assert tracker_sync.sync_due() >= 1
    assert tstore.links_to_sync(older_than="2000-01-01") == []
