"""Smoke-тесты HTTP-слоя usage_server: маршруты подключены, защита на месте.

Фиксируют текущее поведение, а не желаемое: падение здесь означает, что
маршрут стал публичным, проверка подписи отвалилась или статика не отдаётся.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlencode

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import usage_server

BOT_TOKEN = "123456:SMOKE-TEST-TOKEN"

# Маршруты мини-приложения, которые обязаны работать без входа (сам вход/выход).
PUBLIC_MINIAPP_ROUTES = {
    ("GET", "/api/miniapp/auth/config"),
    ("GET", "/api/miniapp/auth/telegram/callback"),
    ("POST", "/api/miniapp/auth/telegram"),
    ("POST", "/api/miniapp/auth/logout"),
}

OAUTH_PROVIDERS = ("google", "todoist", "yandex-disk", "telemost", "zoom")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.delenv("MINIAPP_DEV_BEARER", raising=False)
    return TestClient(usage_server.app, raise_server_exceptions=False, follow_redirects=False)


def _miniapp_routes() -> list[tuple[str, str]]:
    out = []
    for route in usage_server.miniapp_router.routes:
        if isinstance(route, APIRoute):
            out.extend((m, route.path) for m in sorted(route.methods))
    return out


def _signed_init_data(user: dict, *, bot_token: str = BOT_TOKEN, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAE",
        "user": json.dumps(user, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_public_miniapp_allowlist_matches_router():
    routes = set(_miniapp_routes())
    assert PUBLIC_MINIAPP_ROUTES <= routes
    assert len(routes) > 100


def test_every_miniapp_route_rejects_anonymous_requests(client: TestClient):
    leaked = []
    for method, path in _miniapp_routes():
        if (method, path) in PUBLIC_MINIAPP_ROUTES:
            continue
        resp = client.request(method, re.sub(r"\{[^}]+\}", "1", path))
        if resp.status_code != 401:
            leaked.append((method, path, resp.status_code))
    assert leaked == []


def test_public_auth_config_responds(client: TestClient):
    resp = client.get("/api/miniapp/auth/config")
    assert resp.status_code == 200
    assert "telegram_login_enabled" in resp.json()


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "tma garbage"},
        {"Authorization": "tma " + _signed_init_data({"id": 42, "first_name": "T"}, bot_token="999:OTHER")},
        {"X-Telegram-Init-Data": _signed_init_data({"id": 42, "first_name": "T"}, auth_date=1)},
    ],
    ids=["garbage", "foreign-bot-signature", "expired"],
)
def test_invalid_init_data_is_rejected(client: TestClient, headers: dict):
    resp = client.get("/api/miniapp/me", headers=headers)
    assert resp.status_code == 401


def test_valid_init_data_reaches_handler(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from assistant.bot import access_gate

    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_remember_principal_profile", lambda _p: None)
    monkeypatch.setattr(usage_server, "_miniapp_resolve_bot_username", lambda: "smoke_bot")
    init_data = _signed_init_data({"id": 4242, "first_name": "Smoke", "username": "smoke"})

    resp = client.get("/api/miniapp/me", headers={"Authorization": "tma " + init_data})

    assert resp.status_code == 200
    assert resp.json()["telegram_user_id"] == 4242


def test_valid_init_data_without_access_is_forbidden(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from assistant.bot import access_gate

    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (False, "нет доступа"))
    init_data = _signed_init_data({"id": 4243, "first_name": "Smoke"})

    resp = client.get("/api/miniapp/me", headers={"Authorization": "tma " + init_data})

    assert resp.status_code == 403


@pytest.mark.parametrize("path", ["/webhook", "/webhook/deauthorize"])
def test_zoom_webhook_rejects_bad_signature(client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str):
    from assistant.integrations import zoom_webhook

    monkeypatch.setenv("ZOOM_WEBHOOK_SECRET", "smoke-secret")
    handled = []
    monkeypatch.setattr(zoom_webhook, "handle_webhook_payload", lambda p: handled.append(p) or {})

    resp = client.post(
        path,
        content=b'{"event":"meeting.ended","payload":{}}',
        headers={"x-zm-signature": "v0=bad", "x-zm-request-timestamp": str(int(time.time()))},
    )

    assert resp.status_code == 401
    assert handled == []


def test_zoom_webhook_accepts_valid_signature(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from assistant.integrations import zoom_webhook

    monkeypatch.setenv("ZOOM_WEBHOOK_SECRET", "smoke-secret")
    monkeypatch.setattr(zoom_webhook, "handle_webhook_payload", lambda p: {"ok": True})
    body = b'{"event":"meeting.ended","payload":{}}'
    ts = str(int(time.time()))
    digest = hmac.new(b"smoke-secret", f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()

    resp = client.post("/webhook", content=body, headers={"x-zm-signature": f"v0={digest}", "x-zm-request-timestamp": ts})

    assert resp.status_code == 200


def test_vexa_webhook_rejects_wrong_secret(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from assistant.integrations import vexa_webhook

    monkeypatch.setenv("VEXA_WEBHOOK_SECRET", "smoke-secret")
    handled = []
    monkeypatch.setattr(vexa_webhook, "handle_webhook_payload", lambda p: handled.append(p) or {})

    resp = client.post("/webhook/vexa", content=b"{}", headers={"Authorization": "Bearer wrong"})

    assert resp.status_code == 401
    assert handled == []


def test_vexa_webhook_without_configured_secret_is_closed(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from assistant.integrations import vexa_webhook

    monkeypatch.delenv("VEXA_WEBHOOK_SECRET", raising=False)
    handled = []
    monkeypatch.setattr(vexa_webhook, "handle_webhook_payload", lambda p: handled.append(p) or {})

    resp = client.post("/webhook/vexa", content=b'{"event":"meeting.completed"}')

    assert resp.status_code == 401
    assert handled == []


def test_vexa_webhook_accepts_configured_secret(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from assistant.integrations import vexa_webhook

    monkeypatch.setenv("VEXA_WEBHOOK_SECRET", "smoke-secret")
    monkeypatch.setattr(vexa_webhook, "handle_webhook_payload", lambda p: {"ok": True})

    resp = client.post("/webhook/vexa", content=b"{}", headers={"Authorization": "Bearer smoke-secret"})

    assert resp.status_code == 200


@pytest.mark.parametrize("provider", OAUTH_PROVIDERS)
@pytest.mark.parametrize("query", ["", "?code=x&state=bogus", "?error=access_denied&state=bogus"])
def test_oauth_callback_rejects_unknown_state(client: TestClient, provider: str, query: str):
    resp = client.get(f"/oauth/{provider}/callback{query}")
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("text/html")


def test_dashboard_requires_token_behind_proxy(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("USAGE_DASHBOARD_TOKEN", "smoke-dash-token")
    monkeypatch.setenv("USAGE_DASHBOARD_FORCE_AUTH", "1")

    assert client.get("/dashboard/").status_code == 401
    assert client.get("/dashboard/", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401
    assert client.get("/dashboard/", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_dashboard_without_configured_token_is_closed(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("USAGE_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("USAGE_DASHBOARD_FORCE_AUTH", "1")

    assert client.get("/dashboard/").status_code == 503


@pytest.mark.parametrize("token", ["nonexistent-token-xyz", "../../etc/passwd"])
def test_unknown_share_token_is_not_found(client: TestClient, token: str):
    assert client.get(f"/share/{token}").status_code == 404
    assert client.get(f"/api/public/share/{token}").status_code == 404


def test_webapp_shell_is_served_with_matching_build(client: TestClient):
    index = client.get("/webapp/")
    app_js = client.get("/webapp/app.js")
    sw = client.get("/webapp/sw.js")

    assert index.status_code == app_js.status_code == sw.status_code == 200
    build = re.search(r'data-build="([^"]+)"', index.text).group(1)
    assert f'WEBAPP_BUILD = "{build}"' in app_js.text
    assert build in sw.text


@pytest.mark.parametrize("path", ["/", "/webapp/", "/api/miniapp/me", "/oauth/google/callback"])
def test_security_headers_present(client: TestClient, path: str):
    resp = client.get(path)
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options")
