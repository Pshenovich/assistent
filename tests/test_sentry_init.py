"""Sentry: без DSN — тишина; перехваченная ошибка бота уходит в Sentry, только когда он включён."""

from __future__ import annotations

import sys
import types

from assistant.lib import sentry_init


def test_no_dsn_is_off(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(sentry_init, "_DONE", False)
    assert sentry_init.init_sentry("web") is False
    sentry_init.capture_exception(RuntimeError("x"))  # не падает


def test_capture_only_when_enabled(monkeypatch):
    sent = []
    fake = types.SimpleNamespace(capture_exception=sent.append)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    err = RuntimeError("boom")
    monkeypatch.setattr(sentry_init, "_DONE", False)
    sentry_init.capture_exception(err)
    assert sent == []
    monkeypatch.setattr(sentry_init, "_DONE", True)
    sentry_init.capture_exception(err)
    sentry_init.capture_exception(None)
    assert sent == [err]
