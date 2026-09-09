from __future__ import annotations

from starlette.requests import Request

from usage_server import _is_local_dashboard_request, _require_token
from fastapi import HTTPException


def _request(*, client_host: str, headers: list[tuple[str, str]] | None = None, query: str = "") -> Request:
    encoded = [(k.lower().encode(), v.encode()) for k, v in (headers or [])]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/dashboard/",
        "raw_path": b"/dashboard/",
        "query_string": query.encode(),
        "headers": encoded,
        "client": (client_host, 12345),
        "server": ("127.0.0.1", 8080),
    }
    return Request(scope)


def test_loopback_without_proxy_is_local(monkeypatch):
    monkeypatch.delenv("USAGE_DASHBOARD_FORCE_AUTH", raising=False)
    assert _is_local_dashboard_request(_request(client_host="127.0.0.1")) is True


def test_nginx_public_client_requires_token(monkeypatch):
    monkeypatch.delenv("USAGE_DASHBOARD_FORCE_AUTH", raising=False)
    req = _request(
        client_host="127.0.0.1",
        headers=[("x-real-ip", "8.8.8.8"), ("x-forwarded-for", "8.8.8.8")],
    )
    assert _is_local_dashboard_request(req) is False


def test_force_auth_disables_loopback_bypass(monkeypatch):
    monkeypatch.setenv("USAGE_DASHBOARD_FORCE_AUTH", "1")
    assert _is_local_dashboard_request(_request(client_host="127.0.0.1")) is False


def test_require_token_accepts_query(monkeypatch):
    monkeypatch.setenv("USAGE_DASHBOARD_FORCE_AUTH", "1")
    monkeypatch.setenv("USAGE_DASHBOARD_TOKEN", "dash-secret")
    req = _request(client_host="127.0.0.1", query="access_token=dash-secret")
    _require_token(req)


def test_require_token_rejects_wrong_query(monkeypatch):
    monkeypatch.setenv("USAGE_DASHBOARD_FORCE_AUTH", "1")
    monkeypatch.setenv("USAGE_DASHBOARD_TOKEN", "dash-secret")
    req = _request(client_host="127.0.0.1", query="access_token=nope")
    try:
        _require_token(req)
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("expected 401")


def test_dashboard_home_is_users(monkeypatch, tmp_path):
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "usage.sqlite"))
    monkeypatch.setenv("USAGE_DASHBOARD_TOKEN", "dash-secret")
    monkeypatch.setenv("USAGE_DASHBOARD_FORCE_AUTH", "1")
    from fastapi.testclient import TestClient
    import usage_server

    client = TestClient(usage_server.app)
    denied = client.get("/dashboard/")
    assert denied.status_code == 401
    ok = client.get("/dashboard/", params={"access_token": "dash-secret"})
    assert ok.status_code == 200
    assert "Расход OpenRouter по пользователям" in ok.text
    assert "Кэш prompt" in ok.text
    days = client.get("/dashboard/days", params={"access_token": "dash-secret"})
    assert days.status_code == 200
    assert "по дням" in days.text.lower()
