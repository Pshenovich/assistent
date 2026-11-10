"""Человек писал боту (аккаунт = Telegram id), а входит по почте отдельным аккаунтом без Telegram.

Владелец добавляет его из контактов (там Telegram) — документ и чат должны появиться и там,
где он реально работает.
"""

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
from assistant.lib import telegram_notify
from assistant.stores import accounts, note_members, orgs_store
from assistant.stores import chat_threads as chat_threads_store
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:EMAIL-TWIN"
_ids = itertools.count(940_000, 10)
_mails = itertools.count(1)


def _headers(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_remember_principal_profile", lambda _p: None)
    monkeypatch.setattr(telegram_notify, "send_message", lambda *a, **k: None)
    return TestClient(usage_server.app, raise_server_exceptions=False)


def _two_accounts(nick: str) -> tuple[int, int]:
    """Telegram-аккаунт со старым профилем и почтовый аккаунт без Telegram, известный по нику."""
    tg = next(_ids)
    note_members.upsert_profile(tg, username=nick)
    email_uid = int(accounts.create_email_account(f"twin{next(_mails)}@obuchat.me")["user_id"])
    note_members.upsert_profile(email_uid, username=nick)
    return tg, email_uid


def test_note_shared_from_contact_reaches_email_account(client):
    owner = next(_ids)
    tg, email_uid = _two_accounts("twin_note_user")
    nid = int(notes_store.create_note(owner, "Общий", "текст")["id"])

    resp = client.post(
        f"/api/miniapp/notes/local/{nid}/members",
        headers=_headers(owner),
        json={"telegram_user_id": tg, "telegram_username": "twin_note_user"},
    )
    assert resp.status_code == 200, resp.text
    assert nid in [int(n["id"]) for n in notes_store.list_notes(email_uid)]
    assert nid in [int(n["id"]) for n in notes_store.list_notes(tg)]


def test_chat_shared_from_contact_reaches_email_account(client):
    owner = next(_ids)
    tg, email_uid = _two_accounts("twin_chat_user")
    thread = chat_threads_store.create_thread(owner, title="Обсуждение")
    tid = int(thread["id"])

    resp = client.post(
        f"/api/miniapp/chats/{tid}/members",
        headers=_headers(owner),
        json={"telegram_user_id": tg},
    )
    assert resp.status_code == 200, resp.text
    assert chat_threads_store.is_member(owner, tid, email_uid)


def test_twin_found_by_org_nick():
    tg = next(_ids)
    note_members.upsert_profile(tg, username="twin_org_nick")
    email_uid = int(accounts.create_email_account(f"twin{next(_mails)}@obuchat.me")["user_id"])
    with orgs_store._LOCK:  # type: ignore[attr-defined]
        orgs_store._insert_member(1, str(email_uid), key="x@obuchat.me", tg_hint="twin_org_nick")  # type: ignore[attr-defined]
        orgs_store._conn().commit()  # type: ignore[attr-defined]
    assert note_members.email_twin(tg) == email_uid


def test_no_twin_when_not_same_person():
    # Telegram уже привязан к почтовому аккаунту — переводится сам, второго не ищем.
    tg = next(_ids)
    linked = int(accounts.create_email_account(f"twin{next(_mails)}@obuchat.me", tg_user_id=tg)["user_id"])
    note_members.upsert_profile(linked, username="twin_linked")
    assert note_members.email_twin(tg, "twin_linked") is None
    # Почтовый аккаунт с чужим привязанным Telegram — не он.
    tg2 = next(_ids)
    other = int(accounts.create_email_account(f"twin{next(_mails)}@obuchat.me", tg_user_id=next(_ids))["user_id"])
    note_members.upsert_profile(other, username="twin_taken")
    assert note_members.email_twin(tg2, "twin_taken") is None
    # Без ника и без профиля — не угадываем.
    assert note_members.email_twin(next(_ids)) is None
    # Сам почтовый аккаунт — двойника у него нет.
    assert note_members.email_twin(other, "twin_taken") is None
