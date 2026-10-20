"""Ссылка из уведомления «добавил(а) вас в заметку» должна открывать заметку у приглашённого.

Баг: на телефоне ?note=ID обрабатывался только внутри loadNotes(), а при старте
вкладка «Заметки» не грузится — ссылка открывала главный экран.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.lib import telegram_notify
from assistant.services import transcription_notes as tnotes
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:NOTE-INVITE-LINK"
APP_JS = Path(__file__).resolve().parents[1] / "webapp" / "app.js"
_ids = itertools.count(930_000, 10)


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
    sent: list[tuple[int, str]] = []
    monkeypatch.setattr(telegram_notify, "send_message", lambda uid, text, **_: sent.append((int(uid), text)))
    return {"client": TestClient(usage_server.app, raise_server_exceptions=False), "sent": sent}


def test_invitee_opens_call_note_from_notification_link(env):
    client, sent = env["client"], env["sent"]
    owner, invitee = next(_ids), next(_ids) + 1
    note = notes_store.create_note(
        owner, 'Звонок "Документ о релизам на платформу"', "расшифровка", role=tnotes.TRANSCRIPTION_ROLE
    )
    nid = int(note["id"])

    resp = client.post(
        f"/api/miniapp/notes/local/{nid}/members",
        headers=_headers(owner),
        json={"telegram_user_id": invitee},
    )
    assert resp.status_code == 200

    [(to, text)] = [m for m in sent if m[0] == invitee]
    assert "добавил(а) вас в заметку" in text
    href = re.search(r'href="([^"]+)"', text).group(1).replace("&amp;", "&")
    url = urlparse(href)
    assert url.path == "/webapp/"
    assert parse_qs(url.query)["note"] == [str(nid)]

    got = client.get(f"/api/miniapp/notes/local/{nid}", headers=_headers(invitee))
    assert got.status_code == 200
    assert int(got.json()["item"]["id"]) == nid


def _boot_tail() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    end = src.index("var bootAppStarted = false;")
    start = src.rindex('setTab("actual", { noAnim: true });', 0, end)
    return src[start:end]


def test_boot_opens_pending_note_link_without_notes_tab():
    """Как ?task= и ?event=, ?note= должен открываться прямо при старте приложения."""
    tail = _boot_tail()
    assert "openPendingTaskFromLocation();" in tail
    assert "openPendingSharedNote(" in tail
