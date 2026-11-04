"""Открытие списка документов не пишет в базу, если хэштеги не менялись (раньше — DELETE+INSERT на каждый)."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.stores import hashtags as hashtags_store
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:NOTES-LIST"
_ids = itertools.count(960_000, 10)


def _headers(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


def test_second_list_open_writes_nothing_and_keeps_hashtags(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    uid = next(_ids)
    for i in range(5):
        notes_store.create_note(uid, f"Док {i}", f"<p>текст #проект{i} и #общий</p>")
    client = TestClient(usage_server.app, raise_server_exceptions=False)

    r = client.get("/api/miniapp/notes", headers=_headers(uid))  # первый раз хэштеги могут записаться
    assert r.status_code == 200, r.text
    before = hashtags_store._conn().total_changes
    r = client.get("/api/miniapp/notes", headers=_headers(uid))
    assert r.status_code == 200, r.text
    assert hashtags_store._conn().total_changes == before
    names = {h["name"] for n in r.json()["local_notes"] for h in n.get("hashtags", [])}
    assert {"проект0", "общий"} <= names


def test_hashtag_added_in_text_is_picked_up_on_list(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    uid = next(_ids)
    note = notes_store.create_note(uid, "Док", "<p>без тегов</p>")
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    client.get("/api/miniapp/notes", headers=_headers(uid))
    notes_store.update_note(uid, int(note["id"]), body="<p>теперь #новый</p>")  # мимо сохранения из редактора
    r = client.get("/api/miniapp/notes", headers=_headers(uid))
    item = next(n for n in r.json()["local_notes"] if int(n["id"]) == int(note["id"]))
    assert [h["name"] for h in item["hashtags"]] == ["новый"]


def test_list_members_bulk_matches_per_note():
    """Участники в списке документов — пакетом, но те же, что и по одному документу."""
    from assistant.stores import note_members

    a, b, c = next(_ids), next(_ids), next(_ids)
    note_members.upsert_profile(a, username="anna_list", first_name="Анна")
    note_members.upsert_profile(b, first_name="Боб")
    n1 = notes_store.create_note(a, "общий", "x")
    notes_store.create_note(a, "свой", "y")
    n3 = notes_store.create_note(b, "чужой общий", "z")
    note_members.add_member(a, int(n1["id"]), b)
    note_members.add_member(a, int(n1["id"]), c)
    note_members.add_member(b, int(n3["id"]), a)
    listed = notes_store.list_notes(a)
    assert {n["title"] for n in listed} == {"общий", "свой", "чужой общий"}
    for n in listed:
        owner = str(n["owner_user_id"])
        assert n["members"] == note_members.list_members_public(owner, int(n["id"])), n["title"]
