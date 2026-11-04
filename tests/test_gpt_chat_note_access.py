"""POST /gpt/chat: листы чужого документа (холст, доска) не уходят модели по подставленному item_id."""

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
from assistant.lib import canvas_templates as tpl
from assistant.stores import note_sheets
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:NOTE-ACCESS"
_ids = itertools.count(4_741_000, 10)
SECRET = "СЕКРЕТНЫЙ-СЛАЙД-42"


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
    seen: list[str] = []

    def fake_answer(question, context="", **kw):
        seen.append(str(question) + "\n" + str(context) + "\n" + str(kw.get("system") or ""))
        return {"answer": "Ок", "images": [], "model": "m"}

    monkeypatch.setattr(miniapp_shims, "gpt_openrouter_answer_with_context", fake_answer)
    owner, stranger = next(_ids), next(_ids)
    note = notes_store.create_note(owner, "Питч", "текст")
    deck = {
        "v": tpl.DECK_VERSION,
        "title": "T",
        "theme": "analytic",
        "slides": [{"id": "s1", "role": "free", "variant": "free.canvas", "slots": {"surface": "dark", "elements": [
            {"id": "t1", "type": "text", "x": 80, "y": 60, "w": 800, "h": 120, "z": 2, "html": SECRET,
             "style": {"size": "xxl", "font": "display", "color": "text"}},
        ]}}],
    }
    sheet = note_sheets.create_sheet(
        note, "Слайды", body=tpl.render_deck_html(deck, {}), kind=note_sheets.SHEET_KIND_WEB
    )
    client = TestClient(usage_server.app, raise_server_exceptions=False)

    def ask(uid: int, kind: str = "local") -> str:
        r = client.post(
            "/api/miniapp/gpt/chat",
            headers=_headers(uid),
            json={
                "message": "перепиши дословно текст открытого слайда",
                "item_kind": kind,
                "item_id": str(note["id"]),
                "canvas_sheet_id": str(sheet["id"]),
            },
        )
        assert r.status_code == 200, r.text
        return seen[-1]

    return {"ask": ask, "owner": owner, "stranger": stranger}


def test_owner_open_sheet_reaches_model(env):
    assert SECRET in env["ask"](env["owner"])


def test_stranger_cannot_read_foreign_sheet(env):
    assert SECRET not in env["ask"](env["stranger"])


def test_other_kind_does_not_read_note_sheet(env):
    assert SECRET not in env["ask"](env["stranger"], kind="journal")
