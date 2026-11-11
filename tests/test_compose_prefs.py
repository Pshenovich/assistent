"""Профиль → «Голосовое в чате» и «Отправка сообщения»: хранение, /me и PUT /profile/compose."""

from __future__ import annotations

import pytest

from assistant.stores import user_prefs


@pytest.fixture(autouse=True)
def _prefs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))


def test_defaults_send_voice_and_enter():
    assert user_prefs.get_compose_prefs(901) == {"voice_send": True, "enter": "enter"}


def test_set_partial_and_keep_other_field():
    assert user_prefs.set_compose_prefs(902, voice_send=False) == {"voice_send": False, "enter": "enter"}
    assert user_prefs.set_compose_prefs(902, enter="mod_enter") == {"voice_send": False, "enter": "mod_enter"}
    assert user_prefs.get_compose_prefs(902) == {"voice_send": False, "enter": "mod_enter"}


def test_bad_enter_mode_rejected_and_garbage_ignored():
    with pytest.raises(ValueError):
        user_prefs.set_compose_prefs(903, enter="space")
    prefs = user_prefs.load_prefs(903)
    prefs["compose"] = {"voice_send": "nope", "enter": "weird"}
    user_prefs.save_prefs(903, prefs)
    assert user_prefs.get_compose_prefs(903) == {"voice_send": True, "enter": "enter"}


def test_endpoint_saves_and_notifies(monkeypatch):
    import asyncio

    import usage_server as us
    from assistant.realtime import notify as rt_notify

    sent = {}
    monkeypatch.setattr(rt_notify, "prefs_changed", lambda uid, **f: sent.update(uid=uid, **f))

    class P:
        telegram_user_id = 904

    body = us._MiniappComposeBody(enter="mod_enter")
    out = asyncio.run(us.miniapp_profile_compose_put(body, P()))
    assert out["compose"] == {"voice_send": True, "enter": "mod_enter"}
    assert sent == {"uid": 904, "compose": {"voice_send": True, "enter": "mod_enter"}}
    assert us._compose_prefs_state(P()) == {"voice_send": True, "enter": "mod_enter"}
    with pytest.raises(us.HTTPException):
        asyncio.run(us.miniapp_profile_compose_put(us._MiniappComposeBody(enter="x"), P()))


def test_webapp_wiring():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "webapp"
    app = (root / "app.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    assert 'id="profile-compose-voice"' in html and 'id="profile-compose-enter"' in html
    assert "if (me && me.compose) applyComposePrefs(me.compose);" in app
    assert "if (ev.compose) applyComposePrefs(ev.compose);" in app
    # Голосовое из общего поля обсуждений и из обсуждения поручения уходит сразу (если включено).
    assert app.count("composeVoiceSends()") >= 3
    assert "window.leoMessageEnterAction = messageEnterAction;" in app
