import json

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.services import gpt_calendar_tools as gct
from assistant.services import leo_agent
from tests.test_canvas_endpoints import BOT_TOKEN, _headers

UID = 4242


@pytest.fixture
def client(monkeypatch, tmp_path):
    from assistant.stores import calendar_tasks
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    t1 = calendar_tasks.create_task(UID, title="Отправить смету", start_at="2026-10-20T10:00:00+00:00")
    t2 = calendar_tasks.create_task(UID, title="Позвонить клиенту", start_at="2026-10-21T10:00:00+00:00")
    card = {"key": "overdue:x", "type": "overdue", "title": "2 задачи просрочены",
            "tasks": [{"id": int(t1["id"]), "text": "Отправить смету"}, {"id": int(t2["id"]), "text": "Позвонить клиенту"}]}
    monkeypatch.setattr(usage_server, "_focus_card_for", lambda uid, key: card if key == "overdue:x" else None)
    yield TestClient(usage_server.app, raise_server_exceptions=False), (t1, t2)
    notes_store._CONN = None


def test_refine_overdue_returns_task_cards(client):
    c, (t1, t2) = client
    r = c.post("/api/miniapp/proactive/refine", json={"key": "overdue:x"}, headers=_headers(UID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "tasks" and [a["title"] for a in body["actions"]] == ["Отправить смету", "Позвонить клиенту"]
    ids = [a["id"] for a in body["actions"]]
    # история для модели видит id задач из карточек
    hist = leo_agent.cal_marker_for_history("Просрочено.\n\n[[leo-cal:" + ",".join(ids) + "]]")
    assert f"«Отправить смету» (task id {t1['id']})" in hist and f"(task id {t2['id']})" in hist
    assert "Уже выполнено" not in hist


def test_refine_unknown_card_404(client):
    c, _ = client
    r = c.post("/api/miniapp/proactive/refine", json={"key": "nope"}, headers=_headers(UID))
    assert r.status_code == 404
