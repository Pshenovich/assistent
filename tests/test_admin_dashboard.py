"""Админка /dashboard: вход оператора по сессии Орбиты, только просмотр по токену, действия и журнал."""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.admin import audit
from assistant.lib import telegram_access_allowlist as access
from assistant.lib.telegram_login_auth import MINIAPP_SESSION_COOKIE, issue_browser_session
from assistant.stores import orgs_store

_ids = itertools.count(970_000, 10)


@pytest.fixture
def env(monkeypatch, tmp_path):
    op, user, other = next(_ids), next(_ids), next(_ids)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ADMIN")
    monkeypatch.setenv("USAGE_DASHBOARD_TOKEN", "dash-secret")
    monkeypatch.setenv("USAGE_DASHBOARD_FORCE_AUTH", "1")
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "usage.sqlite"))
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", str(op))
    monkeypatch.setenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", str(tmp_path / "allow.json"))
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "requests.json"))
    access.invalidate_allowlist_cache()
    sent: list[tuple[int, str]] = []
    from assistant.bot import access_gate

    monkeypatch.setattr(access_gate, "notify_user_sync", lambda uid, text, **kw: sent.append((uid, text)) or True)
    c = TestClient(usage_server.app, base_url="https://testserver")
    return {"c": c, "op": op, "user": user, "other": other, "sent": sent}


def _login(c: TestClient, uid: int) -> None:
    token, _ = issue_browser_session(uid, {"id": uid, "first_name": f"U{uid}"})
    c.cookies.set(MINIAPP_SESSION_COOKIE, token)


def test_login_required_and_non_operator(env):
    c = env["c"]
    r = c.get("/dashboard/")
    assert r.status_code == 401 and "Открыть Орбиту" in r.text
    _login(c, env["user"])
    r = c.get("/dashboard/")
    assert r.status_code == 401 and "не оператор" in r.text


def test_token_is_read_only(env):
    c = env["c"]
    r = c.get("/dashboard/users", params={"access_token": "dash-secret"})
    assert r.status_code == 200 and "Только просмотр" in r.text
    r = c.post(f"/dashboard/user/{env['user']}/access?access_token=dash-secret", data={"action": "approve"})
    assert r.status_code in (401, 403)


def test_operator_approves_request(env):
    c, user = env["c"], env["user"]
    access.register_pending_request(user_id=user, username="newbie", first_name="Нина", last_name="", scope="leo")
    _login(c, env["op"])
    page = c.get("/dashboard/users", params={"status": "pending"})
    assert page.status_code == 200 and "Нина" in page.text and "Открыть доступ" in page.text
    r = c.post(f"/dashboard/user/{user}/access", data={"action": "approve", "back": "/users"},
               follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert access.is_extra_allowed("newbie", user, scope="leo")
    assert env["sent"] and env["sent"][0][0] == user
    assert audit.recent(target=f"user:{user}")[0]["action"] == "access.approve"
    card = c.get(f"/dashboard/user/{user}")
    assert card.status_code == 200 and "Доступ открыт" in card.text and "Открыл доступ" in card.text


def test_foreign_origin_rejected(env):
    c = env["c"]
    _login(c, env["op"])
    r = c.post(f"/dashboard/user/{env['user']}/access", data={"action": "deny"},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_operator_manages_org(env, monkeypatch):
    c, op, user, other = env["c"], env["op"], env["user"], env["other"]
    monkeypatch.setattr(orgs_store, "verified_emails", lambda uid: [])
    org = orgs_store.create_org(user, "Дельта")
    oid = org["id"]
    _login(c, op)
    assert "Дельта" in c.get("/dashboard/orgs").text
    r = c.post(f"/dashboard/org/{oid}/owner", data={"key": str(other)}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert orgs_store.role_of(oid, other) == "owner" and orgs_store.role_of(oid, user) == "admin"
    r = c.post(f"/dashboard/org/{oid}/billing", data={"plan": "pro", "seats": "3"}, follow_redirects=False)
    assert orgs_store.get_org(oid)["seats"] == 3
    r = c.post(f"/dashboard/org/{oid}/billing", data={"plan": "gold", "seats": "3"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    page = c.get(f"/dashboard/org/{oid}")
    assert page.status_code == 200 and "Назначил владельца" in page.text


def test_overview_and_spend_render(env):
    c = env["c"]
    _login(c, env["op"])
    for path in ("/dashboard/", "/dashboard/spend?days=7", "/dashboard/audit", "/dashboard/days"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.text[:300])
