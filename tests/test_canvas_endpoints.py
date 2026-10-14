"""Edit-mode endpoints for template decks: layout ops and per-slide comments."""

from __future__ import annotations

import copy
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
from assistant.lib import web_canvas as wc
from assistant.stores import note_sheets
from assistant.stores import notes as notes_store
from tests.canvas_sample_deck import SAMPLE_ACTION

BOT_TOKEN = "123456:CANVAS-EDIT"
_ids = itertools.count(940_000, 10)


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
    note = notes_store.create_note(owner, "Q3", "<p>Активация 61%, отток 18%.</p>")
    sheet = wc.apply_canvas_action(note=note, action=copy.deepcopy(SAMPLE_ACTION), note_sheets_mod=note_sheets)
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    base = f"/api/miniapp/notes/local/{note['id']}/sheets/{sheet['id']}"
    return {"owner": owner, "note": note, "sheet": sheet, "client": client, "base": base}


def _deck(item):
    return tpl.extract_deck(item["body"])


def test_variant_switch_and_item_ops(env):
    c, base, h = env["client"], env["base"], _headers(env["owner"])
    r = c.post(base + "/canvas-op", headers=h, json={"op": "variant", "slide_id": "s1", "variant": "cover.minimal"})
    assert r.status_code == 200, r.text
    assert _deck(r.json()["item"])["slides"][0]["variant"] == "cover.minimal"

    r = c.post(base + "/canvas-op", headers=h, json={"op": "variant", "slide_id": "s1", "variant": "cover.goals"})
    assert r.status_code == 200
    r = c.post(base + "/canvas-op", headers=h, json={"op": "add_item", "slide_id": "s1", "group": "goals"})
    assert r.status_code == 200
    assert len(_deck(r.json()["item"])["slides"][0]["slots"]["goals"]) == 4
    r = c.post(base + "/canvas-op", headers=h, json={"op": "add_item", "slide_id": "s1", "group": "goals"})
    assert r.status_code == 400
    assert "Максимум" in r.json()["detail"]


def test_unavailable_variant_is_rejected(env):
    c, base, h = env["client"], env["base"], _headers(env["owner"])
    r = c.post(base + "/canvas-op", headers=h, json={"op": "variant", "slide_id": "s1", "variant": "cover.split-stat"})
    assert r.status_code == 400
    assert "нет ключевой цифры" in r.json()["detail"]


def test_op_keeps_inline_text_edits(env):
    c, base, h = env["client"], env["base"], _headers(env["owner"])
    title = SAMPLE_ACTION["slides"][0]["slots"]["title"]
    edited = env["sheet"]["body"].replace(f">{title}<", ">Правка в редакторе<", 1)
    r = c.post(base + "/canvas-op", headers=h, json={"op": "remove_item", "slide_id": "s1", "group": "goals", "index": 2, "html": edited})
    assert r.status_code == 200, r.text
    s1 = _deck(r.json()["item"])["slides"][0]
    assert s1["slots"]["title"] == "Правка в редакторе"
    assert len(s1["slots"]["goals"]) == 2


def test_other_user_cannot_edit(env):
    r = env["client"].post(env["base"] + "/canvas-op", headers=_headers(next(_ids)), json={"op": "variant", "slide_id": "s1", "variant": "cover.minimal"})
    assert r.status_code == 404


def test_revise_applies_only_commented_slides(env, monkeypatch):
    calls = []

    def fake_answer(question, context="", **kw):
        calls.append({"question": question, "system": kw.get("system"), "note_text": kw.get("note_text")})
        payload = {
            "action": "update",
            "slides": [
                {"id": "s6", "slots": {"claim": "Интеграции — причина №1", "label": "Главное"}},
                {"id": "s7", "slots": {"quote": "Не трогать"}},  # not commented → ignored
            ],
        }
        return {"answer": "```leo-canvas\n" + json.dumps(payload, ensure_ascii=False) + "\n```"}

    monkeypatch.setattr(miniapp_shims, "gpt_openrouter_answer_with_context", fake_answer)
    c, base, h = env["client"], env["base"], _headers(env["owner"])
    r = c.post(base + "/canvas-revise", headers=h, json={"comments": {"s6": "Сформулируй короче", "s99": "нет такого"}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["results"] == {"s6": "applied", "s99": "failed"}
    slides = _deck(data["item"])["slides"]
    assert slides[5]["slots"]["claim"] == "Интеграции — причина №1"
    assert slides[6]["slots"]["quote"] == SAMPLE_ACTION["slides"][6]["slots"]["quote"]
    assert "Сформулируй короче" in calls[0]["question"]
    assert "61%" in calls[0]["note_text"]


def test_revise_without_comments_is_400(env):
    r = env["client"].post(env["base"] + "/canvas-revise", headers=_headers(env["owner"]), json={"comments": {"s1": "  "}})
    assert r.status_code == 400
