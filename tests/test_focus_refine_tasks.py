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


def test_refine_risk_card_returns_registry_items(client, monkeypatch):
    from datetime import datetime, timezone

    from assistant.services import focus_items as fi

    c, _ = client
    monkeypatch.setattr(fi, "_owner_names", lambda uid: ["Вова"])
    monkeypatch.setattr(fi, "sync_source", lambda *a, **k: None)
    fi._CONN = None
    fi.ingest(UID, [{"key": "sumtasks:9", "type": "summary_tasks", "source": "транскрипция",
                     "note": {"id": 9, "kind": "transcription", "title": "Встреча 9"},
                     "items": [{"id": "sumtask:9:a", "text": "Спикер 2 — Обновить сайт — 2026-10-23"}]}],
              datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc))
    card = {"key": "risk:no_owner:x", "type": "risk", "title": "Срок близко, а ответственного нет: 1 задача",
            "commitment_keys": ["sumtask:9:a", "sumtask:missing"]}
    monkeypatch.setattr(usage_server, "_focus_card_for", lambda uid, key: card if key == card["key"] else None)
    r = c.post("/api/miniapp/proactive/refine", json={"key": card["key"]}, headers=_headers(UID))
    assert r.status_code == 200, r.text
    act = r.json()["action"]
    assert act["kind"] == "focus_item"
    assert [f["key"] for f in act.get("focus") or []] == ["sumtask:9:a"]
    fi._CONN = None


def test_items_for_stack_summary_card(client, monkeypatch):
    """«Разобрать»: пункты карточки — сразу из реестра, без карточки в чате."""
    from assistant.services import focus_items as fi

    c, _ = client
    monkeypatch.setattr(fi, "_owner_names", lambda uid: ["Вова"])
    monkeypatch.setattr(fi, "sync_source", lambda *a, **k: None)
    fi._CONN = None
    card = {"key": "sumtasks:7", "type": "summary_tasks", "title": "2 задачи со встречи", "source": "транскрипция",
            "note": {"id": 7, "kind": "transcription", "title": "Встреча 7"},
            "items": [{"id": "sumtask:7:a", "text": "Спикер 2 — Обновить сайт — 2026-10-23"},
                      {"id": "sumtask:7:b", "text": "Вова — Отправить смету"}]}
    monkeypatch.setattr(usage_server, "_focus_card_for", lambda uid, key: card if key == card["key"] else None)
    recorded = []
    monkeypatch.setattr(gct, "record_action", lambda *a, **k: recorded.append(a) or "x")
    r = c.post("/api/miniapp/proactive/items", json={"key": card["key"]}, headers=_headers(UID))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["key"] for i in items] == ["sumtask:7:a", "sumtask:7:b"]
    assert all(i["status"] == "new" for i in items) and items[0]["text"]
    assert not recorded  # в отличие от «Уточнить», в чат ничего не пишем
    # пункт разобран в стопке — тот же реестр, что у карточек чата
    r = c.post("/api/miniapp/proactive/item", json={"key": "sumtask:7:b", "op": "dismiss", "reason": "duplicate"}, headers=_headers(UID))
    assert r.status_code == 200 and r.json()["item"]["reason"] == "duplicate"
    items = c.post("/api/miniapp/proactive/items", json={"key": card["key"]}, headers=_headers(UID)).json()["items"]
    assert [i["status"] for i in items] == ["new", "dismissed"]
    fi._CONN = None


def test_items_for_stack_unknown_card_404(client):
    c, _ = client
    r = c.post("/api/miniapp/proactive/items", json={"key": "nope"}, headers=_headers(UID))
    assert r.status_code == 404
