"""Вход по почте: код → сессия, обязательная почта, привязка Telegram к аккаунту по почте."""

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
from assistant.services import transactional_mail
from assistant.stores import accounts, orgs_store

BOT_TOKEN = "123456:EMAIL"
_ids = itertools.count(960_000, 10)
_mail = itertools.count(1)


def _tma(uid: int, username: str = "") -> dict[str, str]:
    user = {"id": uid, "first_name": "U"}
    if username:
        user["username"] = username
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("EMAIL_REQUIRED", "1")
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.delenv("TX_MAIL_HOST", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(transactional_mail, "send_login_code", lambda to, code, ttl_min=10: sent.append((to, code)))
    monkeypatch.setattr(transactional_mail, "send_org_invite", lambda *a, **k: None)
    return {"c": TestClient(usage_server.app, raise_server_exceptions=False), "sent": sent}


def _email() -> str:
    return f"user{next(_mail)}-{int(time.time() * 1000)}@acme.io"


def _code(env, email: str, ip: str = "") -> str:
    headers = {"X-Real-IP": ip or f"10.0.{next(_mail) % 250}.{next(_mail) % 250}"}
    r = env["c"].post("/api/miniapp/auth/email/start", json={"email": email}, headers=headers)
    assert r.status_code == 200, r.text
    assert env["sent"][-1][0] == email.lower()
    return env["sent"][-1][1]


def test_email_login_creates_account_and_session(env):
    c = env["c"]
    email = _email()
    code = _code(env, email)
    r = c.post("/api/miniapp/auth/email/verify", json={"email": email, "code": "000000" if code != "000000" else "111111"})
    assert r.status_code == 400
    r = c.post("/api/miniapp/auth/email/verify", json={"email": email.upper(), "code": code})
    assert r.status_code == 200, r.text
    uid = r.json()["user"]["id"]
    assert accounts.is_synthetic(uid)
    token = r.json()["session_token"]
    me = c.get("/api/miniapp/me", headers={"Authorization": "session " + token}).json()
    assert me["id"] == uid and me["email"] == email.lower() and me["need_email"] is False
    assert accounts.get_by_email(email)["user_id"] == uid


def test_legacy_telegram_user_must_attach_email(env):
    c = env["c"]
    tg = next(_ids)
    me = c.get("/api/miniapp/me", headers=_tma(tg)).json()
    assert me["need_email"] is True and me["email"] is None
    r = c.get("/api/miniapp/orgs", headers=_tma(tg))
    assert r.status_code == 403 and r.json()["detail"]["need_email"] is True
    email = _email()
    code = _code(env, email)
    r = c.post("/api/miniapp/auth/email/attach", headers=_tma(tg), json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == tg and r.json()["switched"] is False
    assert c.get("/api/miniapp/orgs", headers=_tma(tg)).status_code == 200
    assert c.get("/api/miniapp/me", headers=_tma(tg)).json()["need_email"] is False


def test_telegram_links_to_existing_email_account(env):
    """Завёл аккаунт по почте в вебе, потом открыл миниапп из Telegram — тот же аккаунт."""
    c = env["c"]
    email = _email()
    r = c.post("/api/miniapp/auth/email/verify", json={"email": email, "code": _code(env, email)})
    uid = r.json()["user"]["id"]
    tg = next(_ids)
    real_now = accounts._now
    accounts._now = lambda: real_now() + accounts.RESEND_GAP_SEC + 1  # type: ignore[assignment]
    try:
        code = _code(env, email)
        r = c.post("/api/miniapp/auth/email/attach", headers=_tma(tg), json={"email": email, "code": code})
    finally:
        accounts._now = real_now  # type: ignore[assignment]
    assert r.status_code == 200, r.text
    assert r.json()["switched"] is True and r.json()["user"]["id"] == uid
    me = c.get("/api/miniapp/me", headers=_tma(tg)).json()
    assert me["id"] == uid and me["telegram_user_id"] == uid and me["need_email"] is False


def test_email_taken_by_other_legacy_account(env):
    c = env["c"]
    a, b = next(_ids), next(_ids)
    email = _email()
    accounts.attach_email(a, email)
    code = _code(env, email)
    r = c.post("/api/miniapp/auth/email/attach", headers=_tma(b), json={"email": email, "code": code})
    assert r.status_code == 409


def test_email_login_joins_invited_org(env, monkeypatch):
    c = env["c"]
    owner = next(_ids)
    accounts.attach_email(owner, f"owner{owner}@acme.io")
    org = orgs_store.create_org(owner, f"Org {owner}")
    email = _email()
    orgs_store.add_member(owner, org["id"], email, tg="")
    r = c.post("/api/miniapp/auth/email/verify", json={"email": email, "code": _code(env, email)})
    assert r.status_code == 200, r.text
    uid = r.json()["user"]["id"]
    assert orgs_store.role_of(org["id"], uid) == "member"
    # участник организации проходит шлюз доступа без заявки
    monkeypatch.setattr(access_gate.access, "gate_enabled", lambda: True)
    assert access_gate.is_user_allowed(uid, None, scope="leo")


def test_member_add_requires_email(env):
    c = env["c"]
    owner = next(_ids)
    accounts.attach_email(owner, f"owner{owner}@acme.io")
    oid = c.post("/api/miniapp/orgs", headers=_tma(owner), json={"name": f"Org {owner}"}).json()["org"]["id"]
    r = c.post(f"/api/miniapp/orgs/{oid}/members", headers=_tma(owner), json={"key": "@someone_tg"})
    assert r.status_code == 400
    r = c.post(f"/api/miniapp/orgs/{oid}/members", headers=_tma(owner),
               json={"key": "Someone@Acme.io", "tg": "@someone_tg"})
    assert r.status_code == 200, r.text
    assert r.json()["added"]["status"] == "invited"
    members = r.json()["members"]
    row = next(m for m in members if m["email"] == "someone@acme.io")
    assert row["tg"] == "someone_tg"


def test_profile_change_email_and_tg_link(env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "orbita_test_bot")
    monkeypatch.setattr(usage_server, "_miniapp_resolve_bot_username", lambda: "orbita_test_bot")
    c = env["c"]
    email = _email()
    r = c.post("/api/miniapp/auth/email/verify", json={"email": email, "code": _code(env, email)})
    h = {"Authorization": "session " + r.json()["session_token"]}
    me = c.get("/api/miniapp/me", headers=h).json()
    assert me["tg_changeable"] is True and me["tg_linked"] is False
    r = c.post("/api/miniapp/auth/tg/link", headers=h)
    assert r.status_code == 200 and "?start=link_" in r.json()["url"]
    new = _email()
    r = c.post("/api/miniapp/auth/email/attach", headers=h, json={"email": new, "code": _code(env, new)})
    assert r.status_code == 200 and r.json()["user"]["email"] == new
    assert c.get("/api/miniapp/me", headers=h).json()["email"] == new
    # старый аккаунт из Telegram: сменить Telegram нельзя
    tg = next(_ids)
    accounts.attach_email(tg, _email())
    assert c.get("/api/miniapp/me", headers=_tma(tg)).json()["tg_changeable"] is False
    assert c.post("/api/miniapp/auth/tg/link", headers=_tma(tg)).status_code == 400
