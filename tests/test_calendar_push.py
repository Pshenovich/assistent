"""Календарь без опроса: подписка, уведомление от Google, копия событий, откат на живой запрос."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from assistant.services import calendar_push as cp
from assistant.services import calendar_sources as cs

TZ = timezone(timedelta(hours=3))


def _raw(ev_id, start, minutes=30, summary="Встреча"):
    end = start + timedelta(minutes=minutes)
    return {"id": ev_id, "summary": summary, "iCalUID": ev_id + "@g", "status": "confirmed",
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}


class FakeSvc:
    def __init__(self):
        self.watched, self.stopped = [], []

    def events(self):
        svc = self

        class E:
            def watch(self, calendarId, body):
                svc.watched.append((calendarId, body))

                class R:
                    def execute(self_inner):
                        return {"resourceId": "res-" + body["id"][:6], "expiration": str(int((datetime.now().timestamp() + 7 * 86400) * 1000))}
                return R()
        return E()

    def channels(self):
        svc = self

        class C:
            def stop(self, body):
                svc.stopped.append(body["id"])

                class R:
                    def execute(self_inner):
                        return {}
                return R()
        return C()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GCAL_PUSH_DB_PATH", str(tmp_path / "gcal.sqlite"))
    monkeypatch.setenv("GCAL_PUSH_ENABLED", "1")
    monkeypatch.setenv("WEBAPP_PUBLIC_URL", "https://example.test")
    svc = FakeSvc()
    calls = []
    now = datetime.now(TZ)
    events = [_raw("e1", now + timedelta(minutes=14)), _raw("e2", now + timedelta(days=3))]

    def fake_list(uid, tmin, tmax, **kw):
        calls.append((tmin, tmax))
        out = []
        for r in events:
            n = cs._normalize_event(r, calendar_id="primary", user_id=uid)
            if n and n["start"] < tmax and n["end"] > tmin:
                out.append(n)
        return out

    monkeypatch.setattr(cs, "_service", lambda uid: svc)
    monkeypatch.setattr(cs, "get_active_calendar_ids", lambda uid: ["primary"])
    monkeypatch.setattr(cs, "list_events_in_window", fake_list)
    return {"svc": svc, "calls": calls, "events": events, "now": now}


def test_no_channel_means_live(env):
    assert cp.cached_events(7, env["now"], env["now"] + timedelta(minutes=30)) is None


def test_disabled_means_live(env, monkeypatch):
    cp.ensure_channels(7)
    monkeypatch.setenv("GCAL_PUSH_ENABLED", "0")
    assert cp.cached_events(7, env["now"], env["now"] + timedelta(minutes=30)) is None


def test_http_base_url_does_not_subscribe(env, monkeypatch):
    monkeypatch.setenv("WEBAPP_PUBLIC_URL", "http://127.0.0.1:8080")
    assert cp.ensure_channels(7)["failed"] == -1 and not env["svc"].watched


def test_cache_served_until_notification(env):
    res = cp.ensure_channels(7)
    assert res["created"] == 1
    cal, body = env["svc"].watched[0]
    assert cal == "primary" and body["address"] == "https://example.test/webhook/google-calendar"
    now = env["now"]
    soon = cp.cached_events(7, now, now + timedelta(minutes=27))
    assert [e["event_id"] for e in soon] == ["e1"]
    n_calls = len(env["calls"])
    # повторы — из копии, без запросов к Google
    for _ in range(5):
        assert [e["event_id"] for e in cp.cached_events(7, now, now + timedelta(days=14))] == ["e1", "e2"]
    assert len(env["calls"]) == n_calls
    # Google сообщил об изменении → копия перечитывается один раз
    env["events"].append(_raw("e3", now + timedelta(minutes=20), summary="Новая"))
    headers = {"X-Goog-Channel-ID": body["id"], "X-Goog-Channel-Token": body["token"], "X-Goog-Resource-State": "exists"}
    assert cp.handle_notification(headers) == 200
    ids = [e["event_id"] for e in cp.cached_events(7, now, now + timedelta(minutes=27))]
    assert ids == ["e1", "e3"] and len(env["calls"]) == n_calls + 1
    cp.cached_events(7, now, now + timedelta(minutes=27))
    assert len(env["calls"]) == n_calls + 1


def test_notification_auth_and_sync(env):
    cp.ensure_channels(7)
    body = env["svc"].watched[0][1]
    assert cp.handle_notification({"X-Goog-Channel-ID": body["id"], "X-Goog-Channel-Token": "bad"}) == 403
    assert cp.handle_notification({"X-Goog-Channel-ID": "unknown", "X-Goog-Channel-Token": cp.channel_token("unknown")}) == 200
    before = cp._dirty_seq(cp._conn(), "7")
    cp.handle_notification({"X-Goog-Channel-ID": body["id"], "X-Goog-Channel-Token": body["token"], "X-Goog-Resource-State": "sync"})
    assert cp._dirty_seq(cp._conn(), "7") == before  # sync — не изменение


def test_renewal_stops_old_channel(env, monkeypatch):
    cp.ensure_channels(7)
    assert cp.ensure_channels(7)["kept"] == 1  # свежая подписка не трогается
    monkeypatch.setattr(cp, "RENEW_BEFORE_SEC", 30 * 86400)  # «истекает скоро»
    res = cp.ensure_channels(7)
    assert res["created"] == 1 and res["stopped"] == 1 and len(env["svc"].stopped) == 1


def test_webhook_route(env):
    import usage_server

    cp.ensure_channels(7)
    body = env["svc"].watched[0][1]
    c = TestClient(usage_server.app)
    r = c.post("/webhook/google-calendar", headers={"X-Goog-Channel-ID": body["id"], "X-Goog-Channel-Token": body["token"], "X-Goog-Resource-State": "exists"})
    assert r.status_code == 200
    assert c.post("/webhook/google-calendar", headers={"X-Goog-Channel-ID": body["id"], "X-Goog-Channel-Token": "x"}).status_code == 403


def test_reminder_fetch_uses_cache(env, monkeypatch):
    from assistant.services import meeting_reminders as mr

    monkeypatch.setattr("assistant.services.calendar._tz_for", lambda uid: TZ)
    cp.ensure_channels(7)
    first = mr.fetch_upcoming_timed_events(7, horizon_min=27)
    n = len(env["calls"])
    again = mr.fetch_upcoming_timed_events(7, horizon_min=27)
    assert [e["event_id"] for e in first] == [e["event_id"] for e in again] == ["e1"]
    assert len(env["calls"]) == n


def test_holiday_calendar_without_push_counts_as_covered(env, monkeypatch):
    monkeypatch.setattr(cs, "get_active_calendar_ids", lambda uid: ["primary", "ru.russian#holiday@group.v.calendar.google.com"])
    svc = env["svc"]
    real_events = svc.events

    def events():
        e = real_events()
        orig = e.watch

        def watch(calendarId, body):
            if "holiday" in calendarId:
                raise RuntimeError("HttpError 400 pushNotSupportedForRequestedResource")
            return orig(calendarId=calendarId, body=body)
        e.watch = watch
        return e

    svc.events = events
    res = cp.ensure_channels(7)
    assert res["created"] == 1 and res["kept"] == 1 and res["failed"] == 0
    assert cp.cached_events(7, env["now"], env["now"] + timedelta(minutes=27)) is not None


def test_change_notifies_open_screens(env, monkeypatch):
    from assistant.realtime import notify

    sent = []
    monkeypatch.setattr(notify, "publish", lambda topic, ev: sent.append((topic, ev)))
    cp.ensure_channels(7)
    body = env["svc"].watched[0][1]
    h = {"X-Goog-Channel-ID": body["id"], "X-Goog-Channel-Token": body["token"]}
    cp.handle_notification({**h, "X-Goog-Resource-State": "sync"})
    assert sent == []  # проверка подписки — экран не дёргаем
    cp.handle_notification({**h, "X-Goog-Resource-State": "exists"})
    assert sent and sent[-1][1] == {"type": "calendar.changed"} and sent[-1][0].endswith("7")
