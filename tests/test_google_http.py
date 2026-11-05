"""Google API из РФ: висящее подключение — короткий таймаут и повтор, но POST (создание) не дублируем."""

import socket

import httplib2
import pytest

from assistant.lib import google_http


def _flaky(monkeypatch, fails: int):
    calls = []

    def fake(self, uri, method="GET", *a, **k):
        calls.append(method)
        if len(calls) <= fails:
            raise socket.timeout("timed out")
        return ("resp", b"{}")

    monkeypatch.setattr(httplib2.Http, "request", fake)
    monkeypatch.setattr(google_http.time, "sleep", lambda s: None)
    return calls


def test_get_retried(monkeypatch):
    calls = _flaky(monkeypatch, 2)
    assert google_http.RetryingHttp().request("https://www.googleapis.com/calendar/v3/x") == ("resp", b"{}")
    assert len(calls) == 3


def test_post_not_retried(monkeypatch):
    calls = _flaky(monkeypatch, 1)
    with pytest.raises(socket.timeout):
        google_http.RetryingHttp().request("https://www.googleapis.com/calendar/v3/x", "POST")
    assert len(calls) == 1


def test_token_refresh_retried(monkeypatch):
    calls = _flaky(monkeypatch, 1)
    google_http.RetryingHttp().request("https://oauth2.googleapis.com/token", "POST")
    assert len(calls) == 2
