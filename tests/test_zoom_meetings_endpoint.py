"""«Профиль → Zoom → Ближайшие встречи»: список из GET /users/me/meetings (scope meeting:read:list_meetings)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.integrations import zoom_api
from assistant.lib import zoom_gcal

BOT_TOKEN = "123456:ZOOMLIST"


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
    return TestClient(usage_server.app)


def test_lists_upcoming_sorted_without_instant(client, monkeypatch):
    monkeypatch.setattr(zoom_gcal, "user_has_zoom", lambda uid: True)
    monkeypatch.setattr(zoom_gcal, "_zoom_access_token", lambda uid: "tok")
    monkeypatch.setattr(zoom_api, "list_upcoming_meetings", lambda tok, **kw: [
        {"id": 2, "topic": "Позже", "start_time": "2026-11-12T10:00:00Z", "duration": 30, "join_url": "https://zoom.us/j/2"},
        {"id": 3, "topic": "Мгновенная", "join_url": "https://zoom.us/j/3"},
        {"id": 1, "topic": "Раньше", "start_time": "2026-11-10T09:00:00Z", "duration": 60, "join_url": "https://zoom.us/j/1"},
    ])
    r = client.get("/api/miniapp/zoom/meetings", headers=_headers(4242))
    assert r.status_code == 200
    items = r.json()["items"]
    assert [m["topic"] for m in items] == ["Раньше", "Позже"]
    assert items[0]["join_url"] == "https://zoom.us/j/1" and items[0]["duration"] == 60


def test_not_connected_and_zoom_error(client, monkeypatch):
    monkeypatch.setattr(zoom_gcal, "user_has_zoom", lambda uid: False)
    assert client.get("/api/miniapp/zoom/meetings", headers=_headers(4243)).json() == {"connected": False, "items": []}

    monkeypatch.setattr(zoom_gcal, "user_has_zoom", lambda uid: True)
    monkeypatch.setattr(zoom_gcal, "_zoom_access_token", lambda uid: "tok")

    def boom(tok, **kw):
        raise RuntimeError("Zoom 400")

    monkeypatch.setattr(zoom_api, "list_upcoming_meetings", boom)
    body = client.get("/api/miniapp/zoom/meetings", headers=_headers(4243)).json()
    assert body["items"] == [] and body["error"]
