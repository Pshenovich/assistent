"""Доска: «ИИ» у выделенных блоков — POST /notes/local/{id}/board-ai."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.compat import miniapp_shims
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:BOARD-AI"
_ids = itertools.count(960_000, 10)


def _headers(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    owner = next(_ids)
    note = notes_store.create_note(owner, "Флоу", "")
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    return {"owner": owner, "url": f"/api/miniapp/notes/local/{note['id']}/board-ai", "client": client}


def test_rewrites_only_selected_blocks(env, monkeypatch):
    calls = []

    def fake_answer(question, context="", **kw):
        calls.append(question)
        return {"answer": json.dumps({"blocks": [{"id": "a", "text": "Короче"}, {"id": "x", "text": "чужой"}]}, ensure_ascii=False)}

    monkeypatch.setattr(miniapp_shims, "gpt_openrouter_answer_with_context", fake_answer)
    r = env["client"].post(env["url"], headers=_headers(env["owner"]), json={
        "instruction": "Сделай короче", "blocks": [{"id": "a", "text": "Очень длинный шаг"}], "board_text": "Доска: …",
    })
    assert r.status_code == 200, r.text
    assert r.json()["texts"] == {"a": "Короче"}
    assert "Очень длинный шаг" in calls[0] and "Сделай короче" in calls[0]


def test_empty_request_is_400_and_foreign_note_404(env, monkeypatch):
    monkeypatch.setattr(miniapp_shims, "gpt_openrouter_answer_with_context", lambda *a, **k: {"answer": "{}"})
    c, h = env["client"], _headers(env["owner"])
    assert c.post(env["url"], headers=h, json={"instruction": " ", "blocks": [{"id": "a", "text": "x"}]}).status_code == 400
    r = c.post(env["url"], headers=_headers(next(_ids)), json={"instruction": "x", "blocks": [{"id": "a", "text": "x"}]})
    assert r.status_code == 404
    # Модель ничего не поменяла — 422 с понятным текстом.
    assert c.post(env["url"], headers=h, json={"instruction": "x", "blocks": [{"id": "a", "text": "x"}]}).status_code == 422
