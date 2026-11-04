"""Смена типа листа: только у пустого, тело подставляется под новый тип."""

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
from assistant.stores import note_sheets
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:SHEET-KIND"
_ids = itertools.count(950_000, 10)


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
    note = notes_store.create_note(owner, "Документ", "")
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    base = f"/api/miniapp/notes/local/{note['id']}/sheets"
    return {"client": client, "base": base, "h": _headers(owner), "note": note}


def _create(env, kind: str, body: str | None = None) -> dict:
    payload = {"title": "Лист", "kind": kind}
    if body is not None:
        payload["description"] = body
    r = env["client"].post(env["base"], headers=env["h"], json=payload)
    assert r.status_code == 200, r.text
    return r.json()["item"]


def _patch(env, sid, **payload):
    return env["client"].patch(f"{env['base']}/{sid}", headers=env["h"], json=payload)


def test_blank_doc_becomes_board_and_web(env):
    s = _create(env, "doc")
    r = _patch(env, s["id"], kind="board", description='{"v":1,"mode":"design","elements":{}}')
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["kind"] == "board" and json.loads(item["body"])["mode"] == "design"

    r = _patch(env, s["id"], kind="web")
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["kind"] == "web" and "<section" in item["body"]

    r = _patch(env, s["id"], kind="doc")
    assert r.status_code == 200, r.text
    assert r.json()["item"]["kind"] == "doc" and r.json()["item"]["body"] == ""


def test_blank_doc_to_board_without_body_gets_empty_board(env):
    s = _create(env, "doc")
    r = _patch(env, s["id"], kind="board")
    assert r.status_code == 200, r.text
    assert json.loads(r.json()["item"]["body"])["elements"] == {}


def test_filled_sheet_keeps_kind(env):
    s = _create(env, "doc", "<p>Текст</p>")
    r = _patch(env, s["id"], kind="board")
    assert r.status_code == 400
    assert "пустого" in r.json()["detail"]

    b = _create(env, "board", '{"v":1,"elements":{"a":{"type":"rect","x":0,"y":0,"w":10,"h":10}}}')
    assert _patch(env, b["id"], kind="doc").status_code == 400


def test_same_kind_patch_is_not_blocked(env):
    s = _create(env, "doc", "<p>Текст</p>")
    r = _patch(env, s["id"], kind="doc", title="Новое")
    assert r.status_code == 200, r.text


def test_sheet_is_blank():
    assert note_sheets.sheet_is_blank({"kind": "doc", "body": "<p></p>"})
    assert not note_sheets.sheet_is_blank({"kind": "doc", "body": '<p><img src="x"></p>'})
    assert note_sheets.sheet_is_blank({"kind": "board", "body": '{"v":1,"elements":{}}'})
    assert note_sheets.sheet_is_blank({"kind": "web", "body": "<html>…</html>", "revision": 1})
