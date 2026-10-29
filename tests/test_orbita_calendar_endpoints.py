"""Встречи Орбиты через API: создание без Google, лента у организатора и гостя, ответ, правка, отмена."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.services import meeting_invites, orbita_calendar

BOT_TOKEN = "123456:ORBCAL"
_ids = itertools.count(980_000, 10)


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
    emails = {f"g{guest}@acme.io": guest}
    monkeypatch.setattr(meeting_invites, "telegram_id_for_attendee_email", lambda o, em, **kw: emails.get(em))
    monkeypatch.setattr(orbita_calendar, "organizer_email", lambda uid: f"o{uid}@acme.io")
    sent: list[dict] = []

    async def fake_notify(**kw):
        sent.append(kw)
        return 1

    monkeypatch.setattr(meeting_invites, "notify_invitees_for_miniapp", fake_notify)
    return {"c": TestClient(usage_server.app), "owner": owner, "guest": guest, "guest_email": f"g{guest}@acme.io",
            "sent": sent}


def _day(offset_days: int = 3) -> datetime:
    tz = ZoneInfo("Europe/Moscow")
    return (datetime.now(tz) + timedelta(days=offset_days)).replace(hour=11, minute=0, second=0, microsecond=0)


def test_orbita_meeting_flow(env):
    c, owner, guest = env["c"], env["owner"], env["guest"]
    start = _day()
    r = c.post("/api/miniapp/calendar/events", headers=_headers(owner), json={
        "title": "Синк", "start": start.isoformat(), "end": (start + timedelta(minutes=45)).isoformat(),
        "attendees": [env["guest_email"], "outside@partner.ru"]})
    assert r.status_code == 200, r.text
    ev = r.json()["event"]
    assert ev["id"].startswith("orb-") and ev["calendar_id"] == "orbita" and ev["is_organizer"]
    assert env["sent"] and env["sent"][0]["result"]["event_id"] == ev["id"]

    day = start.date().isoformat()
    feed = c.get("/api/miniapp/calendar/today", params={"date": day}, headers=_headers(owner)).json()
    assert feed["connected"] is True and feed["google_connected"] is False
    assert [e["id"] for e in feed["events"] if e.get("orbita")] == [ev["id"]]

    gfeed = c.get("/api/miniapp/calendar/today", params={"date": day}, headers=_headers(guest)).json()
    mine = [e for e in gfeed["events"] if e["id"] == ev["id"]]
    assert mine and mine[0]["needs_rsvp"] and not mine[0]["is_organizer"]

    r = c.post(f"/api/miniapp/calendar/events/{ev['id']}/rsvp", headers=_headers(guest),
               json={"status": "accepted", "calendar_id": "orbita"})
    assert r.status_code == 200, r.text
    assert r.json()["event"]["self_response_status"] == "accepted"

    # гость не может править
    assert c.put(f"/api/miniapp/calendar/events/{ev['id']}", headers=_headers(guest),
                 json={"title": "Взлом", "calendar_id": "orbita"}).status_code == 400
    later = start + timedelta(hours=2)
    r = c.put(f"/api/miniapp/calendar/events/{ev['id']}", headers=_headers(owner),
              json={"start": later.isoformat(), "calendar_id": "orbita"})
    assert r.status_code == 200, r.text
    assert r.json()["event"]["summary"] == "Синк"

    rng = c.get("/api/miniapp/calendar/range", params={"start": day, "end": day}, headers=_headers(owner)).json()
    assert any(e["id"] == ev["id"] for e in rng["events"])

    assert c.delete(f"/api/miniapp/calendar/events/{ev['id']}", headers=_headers(owner)).json()["ok"]
    feed = c.get("/api/miniapp/calendar/today", params={"date": day}, headers=_headers(guest)).json()
    assert not any(e["id"] == ev["id"] for e in feed["events"])
