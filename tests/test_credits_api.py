"""Кредиты через HTTP: настройки руководителя, закрытые модели, блокировка ответа, пополнение."""

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
from assistant.lib import billing_store
from assistant.stores import credits_store, orgs_store

BOT_TOKEN = "123456:CREDITS-API"
_ids = itertools.count(960_000, 10)


def _h(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setenv("CREDITS_DB_PATH", str(tmp_path / "credits.sqlite"))
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    owner, member, outsider = next(_ids), next(_ids) + 1, next(_ids) + 2
    org = orgs_store.create_org(owner, f"Орг {owner}")
    with orgs_store._LOCK:
        orgs_store._insert_member(org["id"], str(member), key="m", role="member")
        orgs_store._conn().commit()
    return {"c": TestClient(usage_server.app), "owner": owner, "member": member, "outsider": outsider, "org": org["id"]}


def test_owner_sets_limit_and_models_member_cannot(env):
    c, oid, member = env["c"], env["org"], env["member"]
    url = f"/api/miniapp/orgs/{oid}/members/{member}/ai"
    r = c.patch(url, headers=_h(member), json={"daily_limit": 9999})
    assert r.status_code == 400
    r = c.patch(url, headers=_h(env["owner"]), json={"daily_limit": 800, "allowed_models": ["openai/gpt-4.1-mini"]})
    assert r.status_code == 200, r.text
    cred = r.json()["credits"]
    assert cred["can_manage"] and cred["members"][str(member)]["daily_limit"] == 800
    assert cred["members"][str(member)]["allowed_models"] == ["openai/gpt-4.1-mini"]
    # участнику организации детали чужих лимитов не отдаются
    r = c.get(f"/api/miniapp/orgs/{oid}", headers=_h(member))
    assert r.status_code == 200 and "members" not in r.json()["credits"]
    # пустой список моделей нельзя
    r = c.patch(url, headers=_h(env["owner"]), json={"allowed_models": []})
    assert r.status_code == 400


def test_locked_models_marked_in_lists(env):
    c, oid, member = env["c"], env["org"], env["member"]
    orgs_store.set_current_org(member, oid)
    credits_store.set_member_settings(oid, member, allowed_models=["openai/gpt-5.5"])
    r = c.get("/api/miniapp/profile/telegram-leo", headers=_h(member))
    locked = {m["id"]: bool(m.get("locked")) for m in r.json()["models"]}
    assert locked["openai/gpt-5.5"] is False and locked["openai/gpt-5.6-sol"] is True
    assert any(m.get("locked_reason") == billing_store.LOCKED_LABEL for m in r.json()["models"])


def test_gate_swaps_locked_model_and_notices_once(env):
    oid, member = env["org"], env["member"]
    orgs_store.set_current_org(member, oid)
    credits_store.set_member_settings(oid, member, daily_limit=10, allowed_models=["openai/gpt-5.5"])
    g = billing_store.chat_gate(member, "anthropic/claude-opus-5.5")
    assert g["model"] == "openai/gpt-5.5" and g["can_answer"] and not g["notice"]
    credits_store.charge(member, 50, org_id=str(oid))
    g = billing_store.chat_gate(member, "openai/gpt-5.5")
    assert g["notice"] == billing_store.MSG_OVER_LIMIT
    assert billing_store.chat_gate(member, "openai/gpt-5.5")["notice"] == ""  # раз в день


def test_personal_empty_blocks_chat_and_topup(env):
    c, uid = env["c"], env["outsider"]
    credits_store.charge(uid, 2500, org_id=None)
    r = c.post("/api/miniapp/gpt/chat", headers=_h(uid), json={"message": "привет"})
    assert r.status_code == 200, r.text
    assert r.json()["credits_blocked"] and r.json()["answer"] == billing_store.MSG_PERSONAL_EMPTY
    r = c.post("/api/miniapp/billing/topup", headers=_h(uid))
    assert r.status_code == 200 and r.json()["balance_credits"] == pytest.approx(2500)
    st = c.get("/api/miniapp/credits/status", headers=_h(uid)).json()
    assert st["can_answer"] and st["mode"] == "personal"


def test_org_topup_only_manager(env):
    c, oid = env["c"], env["org"]
    assert c.post(f"/api/miniapp/orgs/{oid}/credits/topup", headers=_h(env["member"])).status_code == 400
    r = c.post(f"/api/miniapp/orgs/{oid}/credits/topup", headers=_h(env["owner"]))
    assert r.status_code == 200 and r.json()["credits"]["balance"] == pytest.approx(40000)


def test_usage_event_charges_credits(env):
    from assistant.lib import usage_store

    uid = env["outsider"]
    usage_store.insert_usage_event(operation="ask", model="m", generation_id="g", usage={"cost": 1.0}, telegram_user_id=str(uid))
    assert credits_store.balance("user", uid) == pytest.approx(2500 - 230)
