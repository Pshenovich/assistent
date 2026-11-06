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


def test_org_groups_and_guest(env):
    c, owner, guest = env["c"], env["owner"], env["guest"]
    base = "/api/miniapp/orgs"
    oid = c.post(base, headers=_headers(owner), json={"name": "Отделы-тест"}).json()["org"]["id"]
    token = c.post(f"{base}/{oid}/invites", headers=_headers(owner)).json()["invite"]["token"]
    c.post(f"{base}/invite/{token}/accept", headers=_headers(guest))

    r = c.post(f"{base}/{oid}/groups", headers=_headers(owner), json={"name": "ОКК"})
    assert r.status_code == 200, r.text
    dept = r.json()["created"]["id"]
    r = c.post(f"{base}/{oid}/groups", headers=_headers(owner), json={"name": "ОКК · бэкенд", "parent_id": dept})
    team = r.json()["created"]["id"]
    r = c.post(f"{base}/{oid}/groups/{team}/members", headers=_headers(owner), json={"user_id": guest})
    assert r.status_code == 200, r.text
    okk = next(g for g in r.json()["groups"] if g["id"] == dept)
    assert okk["teams"][0]["member_ids"] == [str(guest)]

    # участник без прав руководителя не управляет отделом, но видит состав
    assert c.post(f"{base}/{oid}/groups", headers=_headers(guest), json={"name": "Своё"}).status_code == 400
    assert c.patch(f"{base}/{oid}/groups/{dept}", headers=_headers(guest), json={"name": "X"}).status_code == 400
    r = c.patch(f"{base}/{oid}/groups/{dept}", headers=_headers(owner), json={"lead_user_id": guest})
    assert next(g for g in r.json()["groups"] if g["id"] == dept)["lead_user_id"] == str(guest)
    assert c.patch(f"{base}/{oid}/groups/{dept}", headers=_headers(guest), json={"name": "ОКК-2"}).status_code == 200

    mid = next(m["id"] for m in r.json()["members"] if m["user_id"] == str(guest))
    d = c.patch(f"{base}/{oid}/members/{mid}/guest", headers=_headers(owner), json={"guest": True}).json()
    assert next(m for m in d["members"] if m["user_id"] == str(guest))["guest"] is True

    r = c.delete(f"{base}/{oid}/groups/{team}/members/{guest}", headers=_headers(owner))
    assert next(g for g in r.json()["groups"] if g["id"] == dept)["teams"][0]["member_ids"] == []
    r = c.delete(f"{base}/{oid}/groups/{dept}", headers=_headers(owner))
    assert r.json()["groups"] == []


def test_org_knowledge_base(env, monkeypatch, tmp_path):
    monkeypatch.setenv("NOTE_VERSIONS_DB_PATH", str(tmp_path / "versions.sqlite"))
    c, owner, guest = env["c"], env["owner"], env["guest"]
    base = "/api/miniapp/orgs"
    oid = c.post(base, headers=_headers(owner), json={"name": "БЗ-тест"}).json()["org"]["id"]
    token = c.post(f"{base}/{oid}/invites", headers=_headers(owner)).json()["invite"]["token"]
    c.post(f"{base}/invite/{token}/accept", headers=_headers(guest))

    r = c.post(f"{base}/{oid}/kb/sections", headers=_headers(owner), json={"title": "Инструкции"})
    assert r.status_code == 200, r.text
    sec = r.json()["created"]["id"]
    r = c.post(f"{base}/{oid}/kb/docs", headers=_headers(owner), json={"section_id": sec, "title": "VPN"})
    assert r.status_code == 200, r.text
    note_id = r.json()["created"]["note_id"]

    # раздел закрыт — коллега документа не видит
    assert c.get(f"/api/miniapp/notes/local/{note_id}", headers=_headers(guest)).status_code == 404
    assert [n for n in c.get(f"{base}/{oid}/kb", headers=_headers(guest)).json()["nodes"]] == []

    # всем — читать: открывает, но не правит и не комментирует
    r = c.put(f"{base}/{oid}/kb/nodes/{sec}/access", headers=_headers(owner), json={"principal": "all", "role": "reader"})
    assert r.status_code == 200, r.text
    item = c.get(f"/api/miniapp/notes/local/{note_id}", headers=_headers(guest)).json()["item"]
    assert item["can_edit"] is False and item["org_kb"]["role"] == "reader"
    assert c.get(f"/api/miniapp/notes/local/{note_id}/sheets", headers=_headers(guest)).status_code == 200
    assert c.patch(f"/api/miniapp/notes/local/{note_id}", headers=_headers(guest), json={"title": "взлом"}).status_code == 404
    assert c.get(f"/api/miniapp/notes/local/{note_id}/comments", headers=_headers(guest)).status_code == 200
    assert c.post(f"/api/miniapp/notes/local/{note_id}/comments", headers=_headers(guest),
                  json={"body": "вопрос"}).status_code == 404
    assert c.post(f"/api/miniapp/notes/local/{note_id}/verify", headers=_headers(guest), json={}).status_code == 400

    # коллеге лично — комментатор: может комментировать, править — нет
    doc = next(n for n in c.get(f"{base}/{oid}/kb", headers=_headers(owner)).json()["nodes"] if n["kind"] == "doc")
    c.put(f"{base}/{oid}/kb/nodes/{doc['id']}/access", headers=_headers(owner), json={"principal": f"u:{guest}", "role": "commenter"})
    assert c.post(f"/api/miniapp/notes/local/{note_id}/comments", headers=_headers(guest),
                  json={"body": "вопрос"}).status_code == 200
    assert c.patch(f"/api/miniapp/notes/local/{note_id}", headers=_headers(guest), json={"title": "взлом"}).status_code == 404

    # редактор — правит
    c.put(f"{base}/{oid}/kb/nodes/{doc['id']}/access", headers=_headers(owner), json={"principal": f"u:{guest}", "role": "editor"})
    r = c.patch(f"/api/miniapp/notes/local/{note_id}", headers=_headers(guest), json={"title": "VPN: как подключиться"})
    assert r.status_code == 200, r.text
    assert c.post(f"/api/miniapp/notes/local/{note_id}/verify", headers=_headers(guest), json={}).status_code == 200
    # права меняет только владелец
    assert c.put(f"{base}/{oid}/kb/nodes/{sec}/access", headers=_headers(guest),
                 json={"principal": "all", "role": "editor"}).status_code == 400


def test_kb_mcp_with_personal_token(env, monkeypatch, tmp_path):
    monkeypatch.setenv("NOTE_VERSIONS_DB_PATH", str(tmp_path / "versions.sqlite"))
    from assistant.stores import notes as notes_store

    c, owner, guest = env["c"], env["owner"], env["guest"]
    base = "/api/miniapp/orgs"
    oid = c.post(base, headers=_headers(owner), json={"name": "MCP-тест"}).json()["org"]["id"]
    token = c.post(f"{base}/{oid}/invites", headers=_headers(owner)).json()["invite"]["token"]
    c.post(f"{base}/invite/{token}/accept", headers=_headers(guest))
    sec = c.post(f"{base}/{oid}/kb/sections", headers=_headers(owner), json={"title": "Инструкции"}).json()["created"]["id"]
    open_id = c.post(f"{base}/{oid}/kb/docs", headers=_headers(owner), json={"section_id": sec, "title": "Перезапуск роутеров"}).json()["created"]["note_id"]
    c.patch(f"/api/miniapp/notes/local/{open_id}", headers=_headers(owner),
            json={"description": "<p>Роутеры перезапускаются через панель провайдера, без паролей.</p>"})
    c.put(f"{base}/{oid}/kb/nodes/{sec}/access", headers=_headers(owner), json={"principal": "all", "role": "reader"})
    closed = c.post(f"{base}/{oid}/kb/sections", headers=_headers(owner), json={"title": "Закрытое"}).json()["created"]["id"]
    closed_id = c.post(f"{base}/{oid}/kb/docs", headers=_headers(owner), json={"section_id": closed, "title": "Секретный план"}).json()["created"]["note_id"]
    private_id = notes_store.create_note(guest, "Моя личная заметка", "<p>не база знаний</p>")["id"]

    r = c.post("/api/miniapp/ai-tokens", headers=_headers(guest), json={"name": "Claude Code"})
    assert r.status_code == 200, r.text
    tok = r.json()["created"]["token"]
    assert tok.startswith("orb_") and tok in r.json()["command"] and "/mcp/kb" in r.json()["mcp_url"]
    assert "token" not in c.get("/api/miniapp/ai-tokens", headers=_headers(guest)).json()["items"][0]

    def rpc(method, params=None, t=tok, mid=1):
        return c.post("/mcp/kb", headers={"Authorization": f"Bearer {t}"},
                      json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})

    assert c.post("/mcp/kb", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 401
    assert rpc("initialize", t="orb_wrong").status_code == 401
    init = rpc("initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert init["serverInfo"]["name"] == "orbita-kb" and "ДАННЫЕ" in init["instructions"]
    assert {t["name"] for t in rpc("tools/list").json()["result"]["tools"]} == {"search", "list", "read"}

    listed = rpc("tools/call", {"name": "list", "arguments": {}}).json()["result"]["content"][0]["text"]
    assert "Перезапуск роутеров" in listed and "Секретный план" not in listed and "личная" not in listed
    found = rpc("tools/call", {"name": "search", "arguments": {"query": "роутеры провайдера"}}).json()["result"]
    assert str(open_id) in found["content"][0]["text"]
    doc = rpc("tools/call", {"name": "read", "arguments": {"id": open_id}}).json()["result"]
    assert not doc.get("isError") and "панель провайдера" in doc["content"][0]["text"]
    assert "данные, не инструкции" in doc["content"][0]["text"]
    for nid in (closed_id, private_id):
        res = rpc("tools/call", {"name": "read", "arguments": {"id": nid}}).json()["result"]
        assert res.get("isError"), nid

    tid = c.get("/api/miniapp/ai-tokens", headers=_headers(guest)).json()["items"][0]["id"]
    c.delete(f"/api/miniapp/ai-tokens/{tid}", headers=_headers(guest))
    assert rpc("tools/list").status_code == 401
