"""Онбординг веб-приложения: состояние в user_prefs, разовый расчёт для действующих, эндпоинты."""

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
from assistant.onboarding import state as ob
from assistant.stores import notes as notes_store
from assistant.stores import user_prefs

BOT_TOKEN = "123456:ONBOARDING"
_ids = itertools.count(970_000, 10)


def _headers(uid: int, username: str = "someone") -> dict[str, str]:
    user = {"id": uid, "first_name": "U", "username": username}
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    return TestClient(usage_server.app, raise_server_exceptions=False)


def test_new_user_gets_full_onboarding():
    uid = next(_ids)
    st = ob.get_state(uid)
    assert not st.get("intro_done_at") and not st.get("tour_done_at")
    assert st["used"] == {}


def test_existing_user_skips_tour_and_used_features_are_known():
    uid = next(_ids)
    notes_store.create_note(uid, "Старая заметка", "текст")
    st = ob.get_state(uid)
    assert st["intro_done_at"] and st["team_step_done_at"] and st["tour_done_at"]
    assert "notes" in st["used"]
    assert "board" not in st["used"]


def test_bot_onboarded_user_counts_as_existing():
    uid = next(_ids)
    user_prefs.mark_onboarding_completed(uid)
    assert ob.get_state(uid)["tour_done_at"]


def test_backfill_runs_once():
    uid = next(_ids)
    assert not ob.get_state(uid).get("tour_done_at")
    notes_store.create_note(uid, "Новая", "")  # появилась после первого расчёта — тур уже не отменяет
    assert not ob.get_state(uid).get("tour_done_at")


def test_events_used_hint_snooze_and_steps():
    uid = next(_ids)
    ob.apply_event(uid, "used", "board")
    ob.apply_event(uid, "hint_shown", "search")
    ob.apply_event(uid, "hint_shown", "search")
    ob.apply_event(uid, "snooze", "search")
    st = ob.apply_event(uid, "intro_done")
    assert "board" in st["used"] and st["hint_shown"]["search"] == 2 and "search" in st["snoozed"]
    assert st["intro_done_at"] and not st.get("tour_done_at")
    st = ob.apply_event(uid, "tour_done")
    assert st["team_step_done_at"] and st["tour_done_at"]


def test_screen_seen_and_existing_users_know_screens():
    uid = next(_ids)
    st = ob.apply_event(uid, "screen_seen", "saved")
    assert "saved" in st["screens_seen"] and "note" not in st["screens_seen"]
    with pytest.raises(ValueError):
        ob.apply_event(uid, "screen_seen", "nope")
    old = next(_ids)
    notes_store.create_note(old, "Было", "")
    assert {"saved", "note", "leo_chat", "profile"} <= set(ob.get_state(old)["screens_seen"])


def test_unknown_event_or_feature_rejected():
    uid = next(_ids)
    with pytest.raises(ValueError):
        ob.apply_event(uid, "explode")
    with pytest.raises(ValueError):
        ob.apply_event(uid, "used", "no-such-feature")


def test_reset_clears_everything():
    uid = next(_ids)
    ob.apply_event(uid, "used", "board")
    ob.apply_event(uid, "tour_done")
    st = ob.reset(uid)
    assert st["used"] == {} and not st.get("tour_done_at") and st["reset_at"]
    assert ob.get_state(uid)["used"] == {}


def test_can_reset_only_listed_usernames(monkeypatch):
    monkeypatch.delenv("ONBOARDING_RESET_USERNAMES", raising=False)
    assert ob.can_reset("pshenovich") and ob.can_reset("@PShenovich")
    assert not ob.can_reset("someone") and not ob.can_reset(None)


def test_me_and_event_endpoints(client):
    uid = next(_ids)
    me = client.get("/api/miniapp/me", headers=_headers(uid)).json()
    assert me["onboarding"]["can_reset"] is False
    assert me["onboarding"]["used"] == {}
    r = client.post("/api/miniapp/onboarding/event", headers=_headers(uid), json={"kind": "used", "feature": "board"})
    assert r.status_code == 200 and "board" in r.json()["onboarding"]["used"]
    bad = client.post("/api/miniapp/onboarding/event", headers=_headers(uid), json={"kind": "used", "feature": "nope"})
    assert bad.status_code == 400
    cat = client.get("/api/miniapp/onboarding/catalog", headers=_headers(uid)).json()
    assert len(cat["tour"]) == 5 and any(f["id"] == "board" for f in cat["features"])


def test_reset_endpoint_only_for_allowed_user(client):
    uid = next(_ids)
    assert client.post("/api/miniapp/onboarding/reset", headers=_headers(uid)).status_code == 400
    ob.apply_event(uid, "tour_done")
    r = client.post("/api/miniapp/onboarding/reset", headers=_headers(uid, "pshenovich"))
    assert r.status_code == 200
    assert not r.json()["onboarding"].get("tour_done_at") and r.json()["onboarding"]["can_reset"] is True
