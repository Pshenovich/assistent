"""Права на локальные заметки через HTTP: автор, участник, посторонний."""

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

BOT_TOKEN = "123456:NOTE-PERMS"
_ids = itertools.count(910_000, 10)


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
    monkeypatch.setattr(usage_server, "_remember_principal_profile", lambda _p: None)
    notified = []
    monkeypatch.setattr(usage_server, "_notify_member_added", lambda **kw: notified.append(kw))
    owner, member, stranger, invitee = (next(_ids) + i for i in range(4))
    note = notes_store.create_note(owner, "Secret", "owner text")
    note_members.add_member(owner, note["id"], member)
    share = share_links.create_or_get_share(str(owner), "local", str(note["id"]), None, None)
    client = TestClient(usage_server.app, raise_server_exceptions=False)

    def call(uid, method, suffix="", **kw):
        return client.request(method, f"/api/miniapp/notes/local/{note['id']}{suffix}", headers=_headers(uid), **kw)

    return {
        "owner": owner,
        "member": member,
        "stranger": stranger,
        "invitee": invitee,
        "note_id": note["id"],
        "share_token": share["token"],
        "call": call,
        "notified": notified,
    }


def _body(env) -> str:
    return notes_store.get_note_by_id(env["note_id"])["body"]


def _members(env) -> set[str]:
    return {r["member_user_id"] for r in note_members.list_member_rows(env["owner"], env["note_id"])}


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("GET", "/sheets", None),
        ("GET", "/members", None),
        ("GET", "/comments", None),
        ("PATCH", "", {"description": "hacked"}),
        ("POST", "/members", {"telegram_user_id": 1}),
        ("DELETE", "/members/{member}", None),
        ("POST", "/share", {}),
        ("PUT", "/pin", {"pinned": True}),
        ("POST", "/duplicate", {}),
        ("DELETE", "", None),
    ],
)
def test_stranger_gets_not_found_and_changes_nothing(env, method, suffix, body):
    resp = env["call"](env["stranger"], method, suffix.format(member=env["member"]), json=body)

    assert resp.status_code == 404
    assert _body(env) == "owner text"
    assert _members(env) == {str(env["member"])}
    assert env["notified"] == []


def test_stranger_does_not_see_existing_share_token(env):
    resp = env["call"](env["stranger"], "GET", "/share")
    assert resp.status_code == 200
    assert resp.json()["shared"] is False
    assert env["share_token"] not in resp.text


def test_member_reads_and_edits(env):
    call, member = env["call"], env["member"]

    assert call(member, "GET").json()["item"]["body"] == "owner text"
    assert call(member, "GET", "/sheets").status_code == 200
    assert call(member, "GET", "/share").json()["token"] == env["share_token"]
    assert call(member, "PATCH", json={"description": "by member"}).status_code == 200
    assert _body(env) == "by member"


def test_member_can_invite_but_not_remove_others(env):
    call = env["call"]

    assert call(env["member"], "POST", "/members", json={"telegram_user_id": env["invitee"]}).status_code == 200
    assert _members(env) == {str(env["member"]), str(env["invitee"])}
    assert env["notified"][0]["member_user_id"] == env["invitee"]

    resp = call(env["member"], "DELETE", f"/members/{env['invitee']}")
    assert resp.status_code == 403
    assert str(env["invitee"]) in _members(env)


def test_member_can_leave(env):
    assert env["call"](env["member"], "DELETE", f"/members/{env['member']}").status_code == 200
    assert _members(env) == set()
    assert env["call"](env["member"], "GET").status_code == 404
    assert notes_store.get_note_by_id(env["note_id"]) is not None


def test_owner_cannot_be_added_as_member(env):
    resp = env["call"](env["member"], "POST", "/members", json={"telegram_user_id": env["owner"]})
    assert resp.status_code == 400


def test_owner_removes_member_and_deletes_note(env):
    call, owner = env["call"], env["owner"]

    assert call(owner, "DELETE", f"/members/{env['member']}").status_code == 200
    assert call(env["member"], "GET").status_code == 404

    assert call(owner, "DELETE").status_code == 200
    assert notes_store.get_note_by_id(env["note_id"]) is None
