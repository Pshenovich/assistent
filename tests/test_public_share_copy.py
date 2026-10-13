"""«Добавить к себе» по публичной ссылке копирует только открытые ссылкой листы."""

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
from assistant.stores import share_links

BOT_TOKEN = "123456:SHARE-COPY"
_ids = itertools.count(920_000, 10)


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
    owner, guest = next(_ids), next(_ids)
    note = notes_store.create_note(owner, "Сделка", "<p>секретная основная</p>")
    plan = note_sheets.create_sheet(note, "План", body="<p>открытый план</p>")
    hidden = note_sheets.create_sheet(note, "Цены", body="<p>закрытые цены</p>")
    deck = note_sheets.create_sheet(
        note, "Питч", body="<html><body>слайды</body></html>", kind=note_sheets.SHEET_KIND_WEB
    )
    client = TestClient(usage_server.app, raise_server_exceptions=False)

    def share(sheet_ids):
        link = share_links.create_or_get_share(str(owner), "local", str(note["id"]), "view", sheet_ids)
        return link["token"]

    def copy(token, uid=guest):
        return client.post(f"/api/public/share/{token}/copy", headers=_headers(uid))

    return {
        "owner": owner,
        "guest": guest,
        "note": note,
        "plan": plan,
        "hidden": hidden,
        "deck": deck,
        "share": share,
        "copy": copy,
    }


def _copied_sheets(resp) -> list[dict]:
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["copied"] is True
    item = data["item"]
    return note_sheets.list_sheets(notes_store.get_note_by_id(int(item["id"])))


def test_copy_restricted_link_excludes_unshared_sheets(env):
    token = env["share"]([str(env["plan"]["id"]), str(env["deck"]["id"])])
    sheets = _copied_sheets(env["copy"](token))

    dump = json.dumps(sheets, ensure_ascii=False)
    assert "секретная основная" not in dump
    assert "закрытые цены" not in dump
    assert [s["title"] for s in sheets] == ["План", "Питч"]
    assert "открытый план" in sheets[0]["body"]
    assert sheets[0]["is_primary"] is True
    assert sheets[1]["kind"] == note_sheets.SHEET_KIND_WEB


def test_copy_default_link_copies_only_primary(env):
    sheets = _copied_sheets(env["copy"](env["share"](None)))
    assert len(sheets) == 1
    assert "секретная основная" in sheets[0]["body"]
    assert sheets[0]["title"] == note_sheets.PRIMARY_SHEET_TITLE


def test_copy_canvas_only_link_keeps_primary_empty(env):
    sheets = _copied_sheets(env["copy"](env["share"]([str(env["deck"]["id"])])))
    assert sheets[0]["body"] == ""
    assert [s["title"] for s in sheets[1:]] == ["Питч"]


def test_copy_primary_and_extra_in_source_order(env):
    token = env["share"]([str(env["plan"]["id"]), "main"])
    sheets = _copied_sheets(env["copy"](token))
    assert [s["title"] for s in sheets] == ["Основная", "План"]
    assert "закрытые цены" not in json.dumps(sheets, ensure_ascii=False)


def test_owner_copy_returns_original(env):
    resp = env["copy"](env["share"]([str(env["plan"]["id"])]), uid=env["owner"])
    assert resp.status_code == 200
    assert resp.json()["copied"] is False
