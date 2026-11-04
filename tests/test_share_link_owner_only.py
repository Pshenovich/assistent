"""Публичными ссылками на документ управляет владелец: участник копирует готовую, но не создаёт и не отключает."""

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
from assistant.stores import note_members
from assistant.stores import notes as notes_store
from assistant.stores import share_links

BOT_TOKEN = "123456:SHARE-OWNER"
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
    owner, member = next(_ids), next(_ids)
    note = notes_store.create_note(owner, "Общий", "<p>текст</p>")
    note_members.add_member(owner, int(note["id"]), member)
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    url = f"/api/miniapp/notes/local/{note['id']}/share"
    return {"owner": owner, "member": member, "note": note, "client": client, "url": url}


def test_member_cannot_create_new_link(env):
    r = env["client"].post(env["url"], headers=_headers(env["member"]), json={"access": "comment"})
    assert r.status_code == 403
    assert share_links.list_active_shares(str(env["owner"]), "local", str(env["note"]["id"])) == []


def test_member_copies_existing_link_without_changing_access(env):
    link = share_links.create_or_get_share(str(env["owner"]), "local", str(env["note"]["id"]), "view", None)
    r = env["client"].post(env["url"], headers=_headers(env["member"]), json={"access": "comment"})
    assert r.status_code == 200, r.text
    assert link["token"] in json.dumps(r.json())
    assert share_links.resolve_share(link["token"])["access"] == "view"


def test_member_cannot_revoke(env):
    link = share_links.create_or_get_share(str(env["owner"]), "local", str(env["note"]["id"]), "view", None)
    r = env["client"].delete(env["url"], headers=_headers(env["member"]))
    assert r.status_code == 403
    assert share_links.resolve_share(link["token"]) is not None


def test_owner_creates_and_revokes(env):
    r = env["client"].post(env["url"], headers=_headers(env["owner"]), json={"access": "view"})
    assert r.status_code == 200, r.text
    r = env["client"].delete(env["url"], headers=_headers(env["owner"]))
    assert r.status_code == 200 and r.json()["revoked"] is True
