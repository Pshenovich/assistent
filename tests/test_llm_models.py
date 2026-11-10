"""Модели ИИ из админки и уровни усилия: список, выбор пользователя, потолок организации, запрос в OpenRouter."""

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
from assistant.board.llm import reasoning_request_fields
from assistant.bot import access_gate
from assistant.integrations import openrouter_client as orc
from assistant.lib.telegram_login_auth import MINIAPP_SESSION_COOKIE, issue_browser_session
from assistant.stores import credits_store, llm_models, notes, orgs_store

BOT_TOKEN = "123456:LLM-MODELS"
_ids = itertools.count(980_000, 10)


def _reset_registry() -> None:
    with notes._LOCK:
        conn = llm_models._conn()
        conn.execute("DELETE FROM llm_models")
        conn.execute("DELETE FROM llm_models_meta")
        conn.commit()
    llm_models.reset_cache()


@pytest.fixture(autouse=True)
def _registry():
    _reset_registry()
    yield
    _reset_registry()


def _h(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setenv("CREDITS_DB_PATH", str(tmp_path / "credits.sqlite"))
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(orc, "list_openrouter_models", lambda **_: {"models": [], "default": "openai/gpt-4o-mini"})
    owner, member = next(_ids), next(_ids) + 1
    org = orgs_store.create_org(owner, f"Орг {owner}")
    with orgs_store._LOCK:
        orgs_store._insert_member(org["id"], str(member), key="m", role="member")
        orgs_store._conn().commit()
    orgs_store.set_current_org(member, org["id"])
    return {"c": TestClient(usage_server.app), "owner": owner, "member": member, "org": org["id"]}


def test_seed_keeps_previous_list_and_levels():
    rows = llm_models.list_models()
    ids = [r["id"] for r in rows]
    assert ids[0] == "openai/gpt-5.6-sol" and "anthropic/claude-fable-5.1" in ids
    by = {r["id"]: r for r in rows}
    assert by["openai/gpt-4o"]["efforts"] == []
    assert by["openai/o3"]["efforts"] == ["low", "medium", "high"] and by["openai/o3"]["default_effort"] == "medium"
    # удалили всё — заново не засевается
    for r in rows:
        llm_models.remove(r["id"])
    assert llm_models.list_models() == []
    assert orc.build_gpt_picker_models(set())["models"][0]["id"] == "leo/auto"


def test_admin_list_drives_pickers_and_labels():
    llm_models.save("x-ai/grok-5", label="Grok 5", enabled=True, efforts=["high", "low", "bogus"], default_effort="high")
    llm_models.save("openai/gpt-4o", label="GPT-4o", enabled=False, efforts=[])
    m = llm_models.get("x-ai/grok-5")
    assert m["efforts"] == ["low", "high"] and m["default_effort"] == "high"
    ids = [r["id"] for r in orc.build_gpt_picker_models(set())["models"]]
    assert "x-ai/grok-5" in ids and "openai/gpt-4o" not in ids
    assert [r["id"] for r in orc.tg_leo_models()][-1] == "x-ai/grok-5"
    assert orc.gpt_display_label("x-ai/grok-5") == "Grok 5"
    assert orc.is_paie_reasoning_model("x-ai/grok-5")
    paie = {r["id"] for r in orc.build_paie_picker_models(set(), default="google/gemini-2.5-flash")["models"]}
    assert "x-ai/grok-5" in paie
    before = [r["id"] for r in llm_models.list_models()].index("x-ai/grok-5")
    llm_models.move("x-ai/grok-5", -1)
    assert [r["id"] for r in llm_models.list_models()].index("x-ai/grok-5") == before - 1


def test_clamp_and_cap():
    allowed = ["low", "medium", "high", "xhigh"]
    assert llm_models.clamp("high", allowed) == "high"
    assert llm_models.clamp("max", allowed) == "xhigh"  # ближайший ниже
    assert llm_models.clamp("minimal", allowed) == "low"  # ниже нет — самый слабый
    assert llm_models.clamp("xhigh", allowed, cap="medium") == "medium"
    assert llm_models.clamp("high", ["high", "max"], cap="low") == "high"  # всё выше потолка — самый слабый


def test_reasoning_fields_follow_user_choice_and_org_cap(api):
    member, oid = api["member"], api["org"]
    assert reasoning_request_fields("openai/gpt-4.1", 1200) == {}
    with orc.usage_user(member):
        base = reasoning_request_fields("openai/o3", 1200)
    assert base["reasoning"] == {"effort": "medium", "exclude": True} and base["max_tokens"] >= 8000
    assert llm_models.set_user_effort(member, "openai/o3", "high") == "high"
    with orc.usage_user(member):
        high = reasoning_request_fields("openai/o3", 1200)
    assert high["reasoning"]["effort"] == "high" and high["max_tokens"] >= 16000
    credits_store.set_member_settings(oid, member, max_effort="low")
    with orc.usage_user(member):
        capped = reasoning_request_fields("openai/o3", 1200)
    assert capped["reasoning"]["effort"] == "low"
    assert [e for e in llm_models.efforts_for_user("openai/o3", member)] == ["low"]
    with pytest.raises(llm_models.ModelError):
        llm_models.set_user_effort(member, "openai/o3", "high")
    with pytest.raises(llm_models.ModelError):
        llm_models.set_user_effort(member, "openai/gpt-4o", "low")  # у модели уровней нет


def test_picker_and_effort_api(api):
    c, member, owner, oid = api["c"], api["member"], api["owner"], api["org"]
    r = c.get("/api/miniapp/gpt/models", headers=_h(member))
    assert r.status_code == 200, r.text
    rows = {m["id"]: m for m in r.json()["models"]}
    assert [e["id"] for e in rows["openai/o3"]["efforts"]] == ["low", "medium", "high"]
    assert rows["openai/o3"]["effort"] == "medium" and rows["openai/gpt-4o"]["efforts"] == []
    r = c.post("/api/miniapp/llm/effort", headers=_h(member), json={"model": "openai/o3", "effort": "high"})
    assert r.status_code == 200 and r.json()["effort"] == "high"
    r = c.get("/api/miniapp/profile/telegram-leo", headers=_h(member))
    assert {m["id"]: m for m in r.json()["models"]}["openai/o3"]["effort"] == "high"
    r = c.post("/api/miniapp/llm/effort", headers=_h(member), json={"model": "openai/o3", "effort": "max"})
    assert r.status_code == 400
    # руководитель ставит потолок — участник видит только разрешённые уровни
    url = f"/api/miniapp/orgs/{oid}/members/{member}/ai"
    r = c.patch(url, headers=_h(owner), json={"max_effort": "medium"})
    assert r.status_code == 200, r.text
    cred = r.json()["credits"]
    assert cred["members"][str(member)]["max_effort"] == "medium"
    assert [e["id"] for e in cred["efforts"]] == ["low", "medium", "high"]
    r = c.get("/api/miniapp/paie/models", headers=_h(member))
    o3 = {m["id"]: m for m in r.json()["models"]}["openai/o3"]
    assert [e["id"] for e in o3["efforts"]] == ["low", "medium"] and o3["effort"] == "medium"
    assert c.patch(url, headers=_h(owner), json={"max_effort": "huge"}).status_code == 400
    r = c.patch(url, headers=_h(owner), json={"any_effort": True})
    assert r.json()["credits"]["members"][str(member)]["max_effort"] is None


def test_removed_model_falls_back_for_saved_agent(api):
    from assistant.stores import user_agents

    member = api["member"]
    user_agents.patch_agent(member, "gpt", {"model": "openai/gpt-4.1"})
    assert user_agents.get_agent(member, "gpt")["model"] == "openai/gpt-4.1"
    llm_models.remove("openai/gpt-4.1")
    assert user_agents.get_agent(member, "gpt")["model"] != "openai/gpt-4.1"


@pytest.fixture
def admin(monkeypatch, tmp_path):
    op = next(_ids)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ADMIN")
    monkeypatch.setenv("USAGE_DASHBOARD_TOKEN", "dash-secret")
    monkeypatch.setenv("USAGE_DASHBOARD_FORCE_AUTH", "1")
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "usage.sqlite"))
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", str(op))
    catalog = [
        orc.catalog_entry({"id": "anthropic/claude-sonnet-5.5", "name": "Anthropic: Claude Sonnet 5.5",
                           "pricing": {"prompt": "0.000002", "completion": "0.00001"}, "context_length": 1000000,
                           "reasoning": {"supported_efforts": ["max", "xhigh", "high", "medium", "low"],
                                         "default_effort": "high", "mandatory": False}}),
        orc.catalog_entry({"id": "mistralai/mistral-large", "name": "Mistral Large",
                           "pricing": {"prompt": "0.000002", "completion": "0.000006"}}),
    ]
    monkeypatch.setattr(orc, "openrouter_catalog", lambda **_: catalog)
    c = TestClient(usage_server.app, base_url="https://testserver")
    token, _ = issue_browser_session(op, {"id": op, "first_name": "Op"})
    c.cookies.set(MINIAPP_SESSION_COOKIE, token)
    return c


def test_catalog_entry_prices_and_efforts():
    e = orc.catalog_entry({"id": "a/b", "name": "A: B", "pricing": {"prompt": "0.000004", "completion": "0.00002"},
                           "reasoning": {"supported_efforts": None, "mandatory": True}})
    assert e["name"] == "B" and e["input_usd_m"] == 4.0 and e["output_usd_m"] == 20.0
    assert e["efforts"] == [] and e["mandatory"]
    assert orc.catalog_entry({"id": "a/c", "supported_parameters": ["max_tokens"]})["efforts"] is None


def test_admin_page_add_save_remove(admin):
    c = admin
    page = c.get("/dashboard/models", params={"q": "sonnet"})
    assert page.status_code == 200 and "Claude Sonnet 5.5" in page.text and "Добавить" in page.text
    r = c.post("/dashboard/models/add", data={"id": "anthropic/claude-sonnet-5.5"}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    m = llm_models.get("anthropic/claude-sonnet-5.5")
    assert m["label"] == "Claude Sonnet 5.5" and m["efforts"] == ["low", "medium", "high"] and m["default_effort"] == "high"
    r = c.post("/dashboard/models/add", data={"id": "unknown/model"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = c.post("/dashboard/models/save", data={"id": "anthropic/claude-sonnet-5.5", "label": "Sonnet", "enabled": "1",
                                              "efforts": ["medium", "xhigh"], "default_effort": "xhigh"},
               follow_redirects=False)
    assert r.status_code == 303
    m = llm_models.get("anthropic/claude-sonnet-5.5")
    assert m["label"] == "Sonnet" and m["efforts"] == ["medium", "xhigh"] and m["default_effort"] == "xhigh"
    c.post("/dashboard/models/save", data={"id": "anthropic/claude-sonnet-5.5", "label": "Sonnet"})
    assert llm_models.get("anthropic/claude-sonnet-5.5")["enabled"] is False
    assert c.post("/dashboard/models/remove", data={"id": "anthropic/claude-sonnet-5.5"}).status_code == 200
    assert llm_models.get("anthropic/claude-sonnet-5.5") is None
    hist = c.get("/dashboard/audit").text
    assert "Добавил модель" in hist and "Убрал модель" in hist
    # по токену — только просмотр
    ro = TestClient(usage_server.app, base_url="https://testserver")
    assert ro.get("/dashboard/models", params={"access_token": "dash-secret"}).status_code == 200
    assert ro.post("/dashboard/models/remove?access_token=dash-secret", data={"id": "openai/o3"}).status_code in (401, 403)
