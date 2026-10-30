"""POST /gpt/chat в заметке: каталог презентаций и инструкция доски — только по просьбе или при открытом листе."""

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
from assistant.lib import web_canvas
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:CANVAS-ADDON"
_ids = itertools.count(970_000, 10)
CANVAS_MARK = "SLOT TEMPLATES"
BOARD_MARK = "leo-board"


def _headers(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("CREDITS_ENFORCE", "0")
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    systems: list[str] = []

    def fake_answer(question, context="", **kw):
        systems.append(str(kw.get("system") or ""))
        return {"answer": "Ок", "images": [], "model": "m"}

    monkeypatch.setattr(miniapp_shims, "gpt_openrouter_answer_with_context", fake_answer)
    uid = next(_ids)
    note = notes_store.create_note(uid, "План", "текст заметки")
    client = TestClient(usage_server.app, raise_server_exceptions=False)

    def ask(message: str, **extra) -> str:
        r = client.post(
            "/api/miniapp/gpt/chat",
            headers=_headers(uid),
            json={"message": message, "item_kind": "local", "item_id": str(note["id"]), **extra},
        )
        assert r.status_code == 200, r.text
        return systems[-1]

    return ask


def test_plain_question_in_note_has_no_canvas_or_board_addon(env):
    system = env("что здесь главное?")
    assert CANVAS_MARK not in system
    assert BOARD_MARK not in system


def test_presentation_request_gets_canvas_addon(env):
    assert CANVAS_MARK in env("сделай презентацию по заметке")


def test_open_canvas_sheet_gets_canvas_addon(env):
    assert CANVAS_MARK in env("поменяй третий пункт", canvas_sheet_id="5")


def test_board_request_gets_board_addon(env):
    system = env("нарисуй схему процесса на доске")
    assert BOARD_MARK in system
    assert CANVAS_MARK not in system


def test_looks_like_canvas_request():
    assert web_canvas.looks_like_canvas_request("Сделай слайды для инвесторов")
    assert web_canvas.looks_like_canvas_request("нужен питч-дек")
    assert not web_canvas.looks_like_canvas_request("какая погода в Москве?")


def test_auto_model_picks_cheap_for_simple_and_strong_in_note(monkeypatch):
    """«Авто» в /gpt/chat: простой вопрос в чате — дешёвая модель с подсказкой, вопрос в заметке — сильная."""
    from assistant.nlu import auto_model
    from assistant.stores import chat_threads

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("CREDITS_ENFORCE", "0")
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL_AUTO_CHEAP", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL_AUTO_STRONG", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    seen: list[tuple[str, str]] = []

    def fake_answer(question, context="", **kw):
        seen.append((kw.get("model"), str(kw.get("system") or "")))
        return {"answer": "Ок", "images": [], "model": kw.get("model")}

    monkeypatch.setattr(miniapp_shims, "gpt_openrouter_answer_with_context", fake_answer)
    uid = next(_ids)
    thread = chat_threads.create_thread(uid, title="Ассистент")
    note = notes_store.create_note(uid, "План", "текст")
    client = TestClient(usage_server.app, raise_server_exceptions=False)

    def post(kind: str, iid: str) -> dict:
        r = client.post(
            "/api/miniapp/gpt/chat",
            headers=_headers(uid),
            json={"message": "что такое NPS?", "model": auto_model.AUTO_MODEL_ID, "item_kind": kind, "item_id": iid},
        )
        assert r.status_code == 200, r.text
        return r.json()

    out = post("chat", str(thread["id"]))
    assert seen[-1][0] == auto_model.cheap_model()
    assert auto_model.CHEAP_HINT in seen[-1][1]
    assert out["model_label"] == "Авто · Claude Haiku 5.5"
    post("local", str(note["id"]))
    assert seen[-1][0] == auto_model.strong_model()
    assert auto_model.CHEAP_HINT not in seen[-1][1]
