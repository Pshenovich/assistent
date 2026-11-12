"""Серый черновик результата при сдаче задачи: POST /calendar/tasks/{id}/result-draft."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.services import task_result_draft as trd
from tests.test_canvas_endpoints import BOT_TOKEN, _headers

OWNER = 11
ASSIGNEE = 22
STRANGER = 44
TZ = ZoneInfo("Europe/Moscow")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "calendar_tasks.sqlite"))
    from assistant.stores import calendar_tasks

    calendar_tasks._CONN = None  # type: ignore[attr-defined]
    monkeypatch.setattr(calendar_tasks, "_meeting_task_changed", lambda *_: None)
    trd._CACHE.clear()
    yield calendar_tasks
    calendar_tasks._CONN = None  # type: ignore[attr-defined]
    trd._CACHE.clear()


@pytest.fixture()
def llm_calls(monkeypatch):
    from assistant.nlu import llm

    calls: list[str] = []

    def fake_chat(system, user, **kw):
        calls.append(user)
        assert kw.get("operation") == "task_result_draft"
        return "- Собрал отчёт за октябрь и отправил клиенту\n"

    monkeypatch.setattr(llm, "_chat", fake_chat)
    return calls


@pytest.fixture()
def client(store, monkeypatch):
    from assistant.lib import telegram_notify
    from assistant.services import bundle_access

    monkeypatch.setattr(telegram_notify, "send_message", lambda *a, **k: True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_sync_task_google", lambda *a, **k: None)
    monkeypatch.setattr(bundle_access, "bundles_for_item", lambda *a, **k: [])
    return TestClient(usage_server.app, raise_server_exceptions=False)


def _task(store, owner=OWNER, assignee=ASSIGNEE):
    return store.create_task(
        owner, title="Отчёт", start_at=datetime(2026, 11, 20, 15, 0, tzinfo=TZ),
        assignee_user_id=assignee, assignee_name="Ульяна", tz=TZ,
    )


def _draft(client, uid, task_id):
    return client.post(f"/api/miniapp/calendar/tasks/{task_id}/result-draft", headers=_headers(uid))


def test_no_material_gives_empty_draft_without_llm(client, store, llm_calls):
    t = _task(store)
    r = _draft(client, ASSIGNEE, t["id"])
    assert r.status_code == 200, r.text
    assert r.json()["draft"] == ""
    assert llm_calls == []


def test_only_executor_gets_draft(client, store, llm_calls):
    t = _task(store)
    assert _draft(client, STRANGER, t["id"]).status_code == 400
    assert _draft(client, OWNER, t["id"]).status_code == 400  # постановщик поручения — не исполнитель
    own = store.create_task(OWNER, title="Своя", start_at=datetime(2026, 11, 20, 15, 0, tzinfo=TZ), tz=TZ)
    assert _draft(client, OWNER, own["id"]).status_code == 200  # своя задача — сам себе исполнитель
    assert llm_calls == []


def test_chat_gives_draft_cached_until_new_event(client, store, llm_calls):
    t = _task(store)
    store.post_message(ASSIGNEE, t["id"], "Отчёт за октябрь собран, отправил клиенту", actor_name="Ульяна")
    r = _draft(client, ASSIGNEE, t["id"])
    assert r.status_code == 200, r.text
    assert r.json()["draft"] == "Собрал отчёт за октябрь и отправил клиенту"
    assert "отправил клиенту" in llm_calls[0]
    assert _draft(client, ASSIGNEE, t["id"]).json()["cached"] is True
    assert len(llm_calls) == 1
    store.post_message(OWNER, t["id"], "Спасибо, а сверку приложишь?", actor_name="Вова")
    assert _draft(client, ASSIGNEE, t["id"]).json()["cached"] is False
    assert len(llm_calls) == 2


def test_material_helpers():
    events = [
        {"kind": "created", "text": "", "meta": {}},
        {"kind": "message", "actor_name": "Ульяна", "text": "Готово",
         "meta": {"files": [{"id": 5, "name": "отчёт.pdf"}], "refs": [{"kind": "transcription", "id": "7", "title": "Созвон"}]}},
    ]
    assert trd.event_file_ids(events) == [5]
    assert trd.event_note_ids(events) == ["journal:7"]
    assert "📎 отчёт.pdf" in trd.chat_text(events)
    assert trd.bundle_note_ids([{"items": [{"item_kind": "note", "item_id": "3"}, {"item_kind": "task", "item_id": "9"}]}]) == ["3"]
    assert trd.build_material(task={"title": "x"}, chat="", files_text="", docs_text="") == ""
    assert trd.clean_draft("«Первое»\n\n- второе\nтретье\nчетвёртое") == "Первое\nвторое\nтретье"


def test_dashboard_label_and_usage_user(client, store, monkeypatch):
    from assistant.integrations import openrouter_client
    from assistant.lib import usage_store
    from assistant.nlu import llm

    assert usage_store.operation_label_ru("task_result_draft") == "Описание результата задачи"
    seen: list = []
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: seen.append(openrouter_client.get_openrouter_usage_telegram_user()[0]) or "Готово")
    t = _task(store)
    store.post_message(ASSIGNEE, t["id"], "Сделал", actor_name="Ульяна")
    assert _draft(client, ASSIGNEE, t["id"]).json()["draft"] == "Готово"
    assert seen == [str(ASSIGNEE)]
