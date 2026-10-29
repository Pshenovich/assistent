"""API организаций: создать, пригласить ссылкой, роли, права."""

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
from assistant.stores import orgs_store

BOT_TOKEN = "123456:ORGS"
_ids = itertools.count(950_000, 10)


def _headers(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    owner, guest = next(_ids), next(_ids)
    emails = {owner: [f"o{owner}@acme{owner}.io"], guest: [f"g{guest}@acme{owner}.io"]}
    monkeypatch.setattr(orgs_store, "verified_emails", lambda uid: emails.get(int(uid), []))
    return {"c": TestClient(usage_server.app, raise_server_exceptions=False), "owner": owner, "guest": guest,
            "domain": f"acme{owner}.io"}


def test_org_flow(env):
    c, owner, guest = env["c"], env["owner"], env["guest"]
    base = "/api/miniapp/orgs"
    r = c.get(base, headers=_headers(owner))
    assert r.status_code == 200, r.text
    assert r.json()["orgs"] == [] and r.json()["my_domains"] == [env["domain"]]

    r = c.post(base, headers=_headers(owner), json={"name": "Акме", "domain": env["domain"]})
    assert r.status_code == 200, r.text
    org = r.json()["org"]
    assert org["my_role"] == "owner" and r.json()["can"]["set_role"]
    oid = org["id"]

    # коллега с тем же доменом видит предложение и подаёт заявку
    sug = c.get(base, headers=_headers(guest)).json()["suggestions"]
    assert [s["id"] for s in sug] == [oid]
    r = c.post(f"{base}/{oid}/join", headers=_headers(guest))
    assert r.json()["status"] == "pending"
    assert c.get(f"{base}/{oid}", headers=_headers(guest)).status_code == 404
    r = c.post(f"{base}/{oid}/approve", headers=_headers(owner), json={"user_id": guest})
    assert r.status_code == 200, r.text
    assert c.get(f"{base}/{oid}", headers=_headers(guest)).json()["org"]["my_role"] == "member"

    # участник не может приглашать и менять роли
    assert c.post(f"{base}/{oid}/invites", headers=_headers(guest)).status_code == 400
    assert c.patch(f"{base}/{oid}", headers=_headers(guest), json={"name": "X"}).status_code == 400

    # ссылка-приглашение
    r = c.post(f"{base}/{oid}/invites", headers=_headers(owner))
    token = r.json()["invite"]["token"]
    assert "join=" + token in r.json()["invite"]["urls"]["web"]
    third = next(_ids)
    prev = c.get(f"{base}/invite/{token}", headers=_headers(third)).json()
    assert prev["org"]["name"] == "Акме" and not prev["already_member"]
    assert c.post(f"{base}/invite/{token}/accept", headers=_headers(third)).status_code == 200

    # роль и адрес
    r = c.patch(f"{base}/{oid}/members/{guest}", headers=_headers(owner), json={"role": "admin"})
    assert r.status_code == 200
    assert c.get(f"{base}/slug-check", params={"slug": org["slug"]}, headers=_headers(owner)).json()["ok"] is False
    r = c.patch(f"{base}/{oid}", headers=_headers(owner), json={"slug": f"acme-{owner}"})
    assert r.json()["org"]["slug"] == f"acme-{owner}"
    assert c.patch(f"{base}/{oid}", headers=_headers(owner), json={"slug": "Б"}).status_code == 400


def test_org_billing(env):
    c, owner, guest = env["c"], env["owner"], env["guest"]
    base = "/api/miniapp/orgs"
    oid = c.post(base, headers=_headers(owner), json={"name": "Бета"}).json()["org"]["id"]
    token = c.post(f"{base}/{oid}/invites", headers=_headers(owner)).json()["invite"]["token"]
    c.post(f"{base}/invite/{token}/accept", headers=_headers(guest))
    d = c.patch(f"{base}/{oid}/billing", headers=_headers(owner), json={"plan": "business", "seats": 3}).json()
    assert d["org"]["plan"] == "business" and d["org"]["seats"] == 3
    assert [p["id"] for p in d["plans"]] == ["free", "pro", "business"]
    mid = next(m["id"] for m in d["members"] if m["user_id"] == str(guest))
    d = c.patch(f"{base}/{oid}/members/{mid}/plan", headers=_headers(owner), json={"plan": "business"}).json()
    assert d["org"]["seats_used"] == 1
    assert c.patch(f"{base}/{oid}/billing", headers=_headers(guest), json={"seats": 9}).status_code == 400



def test_market_settings(env):
    c, owner, guest = env["c"], env["owner"], env["guest"]
    base = "/api/miniapp/orgs"
    oid = c.post(base, headers=_headers(owner), json={"name": "Рынок-тест"}).json()["org"]["id"]
    token = c.post(f"{base}/{oid}/invites", headers=_headers(owner)).json()["invite"]["token"]
    c.post(f"{base}/invite/{token}/accept", headers=_headers(guest))
    r = c.get(f"{base}/{oid}/market", headers=_headers(owner)).json()
    assert r["can_edit"] and r["settings"]["time"] == "07:20"
    r = c.patch(f"{base}/{oid}/market", headers=_headers(owner), json={
        "about": "Речевая аналитика продаж", "competitors": "Gong, Clari, gong", "sources": ["https://example.com/rss"],
        "time": "08:30"})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["competitors"] == ["Gong", "Clari"]
    assert c.patch(f"{base}/{oid}/market", headers=_headers(owner), json={"time": "25:00"}).status_code == 400
    assert c.patch(f"{base}/{oid}/market", headers=_headers(owner), json={"sources": "не ссылка"}).status_code == 400
    # участник не меняет настройки организации, но включает себе Telegram
    assert c.patch(f"{base}/{oid}/market", headers=_headers(guest), json={"topics": "x"}).status_code == 400
    g = c.patch(f"{base}/{oid}/market", headers=_headers(guest), json={"telegram": True}).json()
    assert g["telegram"] is True and g["can_edit"] is False and g["settings"]["about"] == "Речевая аналитика продаж"
    assert c.get(f"{base}/{oid}/market", headers=_headers(next(_ids))).status_code == 404
