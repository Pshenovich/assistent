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
    bot_msgs: list = []
    monkeypatch.setattr(access_gate, "notify_user_sync", lambda uid, text, **kw: bot_msgs.append((uid, text)) or True)
    return {"c": TestClient(usage_server.app), "owner": owner, "guest": guest, "guest_email": f"g{guest}@acme.io",
            "sent": sent, "bot": bot_msgs}


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

    assert any("перенесена" in t for _, t in env["bot"])  # гостю из Орбиты — о переносе
    r = c.delete(f"/api/miniapp/calendar/events/{ev['id']}", headers=_headers(owner)).json()
    assert r["ok"] and r["invite_mail"]["reason"] == "no_mail"  # внешнему гостю — некому отправить
    assert any("отменена" in t for _, t in env["bot"])
    feed = c.get("/api/miniapp/calendar/today", params={"date": day}, headers=_headers(guest)).json()
    assert not any(e["id"] == ev["id"] for e in feed["events"])


def test_public_rsvp_page(env):
    from assistant.stores import orbita_events

    c, owner = env["c"], env["owner"]
    start = _day(4)
    ev = c.post("/api/miniapp/calendar/events", headers=_headers(owner), json={
        "title": "Демо для клиента", "start": start.isoformat(), "attendees": ["client@partner.ru"]}).json()["event"]
    rec = orbita_events.get(orbita_events.parse_id(ev["id"]))
    token = rec["attendees"][0]["token"]
    page = c.get(f"/rsvp/{token}")
    assert page.status_code == 200 and "Демо для клиента" in page.text and "client@partner.ru" in page.text
    r = c.post(f"/rsvp/{token}", data={"r": "accepted"}, follow_redirects=False)
    assert r.status_code == 303
    assert orbita_events.get(rec["id"])["attendees"][0]["response"] == "accepted"
    assert any("ответил" in t for uid, t in env["bot"] if uid == owner)
    assert "Ответ сохранён" in c.get(f"/rsvp/{token}?done=1").text
    ics_resp = c.get(f"/rsvp/{token}/invite.ics")
    assert ics_resp.status_code == 200 and "BEGIN:VCALENDAR" in ics_resp.text
    assert c.get("/rsvp/nope").status_code == 404
    assert c.post(f"/rsvp/{token}", data={"r": "hack"}).status_code == 400
    # организатор видит ответ гостя в своей встрече
    owner_ev = c.get("/api/miniapp/calendar/today", params={"date": start.date().isoformat()}, headers=_headers(owner)).json()
    att = next(e for e in owner_ev["events"] if e["id"] == ev["id"])["attendees"]
    assert any(a.get("email") == "client@partner.ru" and a.get("response_status", a.get("responseStatus")) == "accepted" for a in att), att


def test_video_link_for_orbita_meeting(env, monkeypatch):
    from assistant.lib import zoom_gcal

    c, owner = env["c"], env["owner"]
    start = _day(5)
    ev = c.post("/api/miniapp/calendar/events", headers=_headers(owner), json={
        "title": "Звонок с подрядчиком", "start": start.isoformat()}).json()["event"]
    monkeypatch.setattr(zoom_gcal, "_zoom_access_token", lambda uid: "tok")
    monkeypatch.setattr(zoom_gcal, "user_has_zoom", lambda uid: True)
    monkeypatch.setattr(zoom_gcal.zoom_api, "create_scheduled_meeting",
                        lambda token, **kw: {"id": 77, "join_url": "https://zoom.us/j/77"})
    monkeypatch.setattr(zoom_gcal.zrm, "register_meeting", lambda *a, **kw: None)
    monkeypatch.setattr(zoom_gcal.meeting_sched, "maybe_schedule_for_zoom_create", lambda *a, **kw: None)
    monkeypatch.setattr(zoom_gcal.meeting_sched, "service_available", lambda: False)
    r = c.post(f"/api/miniapp/calendar/events/{ev['id']}/zoom-link", headers=_headers(owner))
    assert r.status_code == 200, r.text
    assert r.json()["join_url"] == "https://zoom.us/j/77"
    again = c.post(f"/api/miniapp/calendar/events/{ev['id']}/zoom-link", headers=_headers(owner)).json()
    assert again["already"] is True
    feed = c.get("/api/miniapp/calendar/today", params={"date": start.date().isoformat()}, headers=_headers(owner)).json()
    assert next(e for e in feed["events"] if e["id"] == ev["id"])["meet_url"] == "https://zoom.us/j/77"


def test_recurring_orbita_meeting_via_form(env):
    c, owner, guest = env["c"], env["owner"], env["guest"]
    start = _day(2)
    r = c.post("/api/miniapp/calendar/events", headers=_headers(owner), json={
        "title": "Еженедельный синк", "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat(),
        "attendees": [env["guest_email"]], "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=3"]})
    assert r.status_code == 200, r.text
    series = r.json()["event"]["id"]
    rng = c.get("/api/miniapp/calendar/range", params={
        "start": start.date().isoformat(), "end": (start + timedelta(days=15)).date().isoformat()},
        headers=_headers(guest)).json()
    inst = [e for e in rng["events"] if e.get("recurringEventId") == series]
    assert len(inst) == 3 and inst[0]["id"].startswith(series + "_")
    rec = c.get(f"/api/miniapp/calendar/events/{inst[1]['id']}/recurrence", headers=_headers(owner)).json()
    assert rec == {"recurrence": ["RRULE:FREQ=WEEKLY;COUNT=3"], "series_id": series}
    r = c.post(f"/api/miniapp/calendar/events/{inst[1]['id']}/rsvp", headers=_headers(guest),
               json={"status": "accepted", "calendar_id": "orbita"})
    assert r.status_code == 200 and r.json()["event"]["id"] == inst[1]["id"]
    assert c.delete(f"/api/miniapp/calendar/events/{inst[1]['id']}?calendar_id=orbita", headers=_headers(owner)).json()["ok"]
    assert any("отменена (остальные" in t for _, t in env["bot"])
    rng = c.get("/api/miniapp/calendar/range", params={
        "start": start.date().isoformat(), "end": (start + timedelta(days=15)).date().isoformat()},
        headers=_headers(owner)).json()
    assert len([e for e in rng["events"] if e.get("recurringEventId") == series]) == 2
