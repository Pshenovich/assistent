"""Медленные запросы API пишутся в журнал с путём, временем и занятостью пула потоков."""

from fastapi.testclient import TestClient

import usage_server


def test_slow_api_request_logged(monkeypatch, capsys):
    monkeypatch.setenv("SLOW_REQUEST_LOG_SEC", "0")
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    client.get("/api/miniapp/reminders")
    out = capsys.readouterr().out
    assert "[slow] GET /api/miniapp/reminders 401" in out and "threads=" in out


def test_fast_or_non_api_not_logged(monkeypatch, capsys):
    monkeypatch.setenv("SLOW_REQUEST_LOG_SEC", "0")
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    client.get("/webapp/")
    monkeypatch.setenv("SLOW_REQUEST_LOG_SEC", "30")
    client.get("/api/miniapp/reminders")
    assert "[slow]" not in capsys.readouterr().out
