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
    monkeypatch.setenv("VAULT_HOST", "vault.test")
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


def test_kb_mcp_write_token(env, monkeypatch, tmp_path):
    monkeypatch.setenv("NOTE_VERSIONS_DB_PATH", str(tmp_path / "versions.sqlite"))
    c, owner, guest = env["c"], env["owner"], env["guest"]
    base = "/api/miniapp/orgs"
    oid = c.post(base, headers=_headers(owner), json={"name": "MCP-запись"}).json()["org"]["id"]
    token = c.post(f"{base}/{oid}/invites", headers=_headers(owner)).json()["invite"]["token"]
    c.post(f"{base}/invite/{token}/accept", headers=_headers(guest))
    sec = c.post(f"{base}/{oid}/kb/sections", headers=_headers(owner), json={"title": "Проект"}).json()["created"]["id"]
    c.put(f"{base}/{oid}/kb/nodes/{sec}/access", headers=_headers(owner), json={"principal": "all", "role": "editor"})
    closed = c.post(f"{base}/{oid}/kb/sections", headers=_headers(owner), json={"title": "Закрытое"}).json()["created"]["id"]
    c.put(f"{base}/{oid}/kb/nodes/{closed}/access", headers=_headers(owner), json={"principal": "all", "role": "reader"})
    locked = c.post(f"{base}/{oid}/kb/docs", headers=_headers(owner), json={"section_id": closed, "title": "Только читать"}).json()["created"]["note_id"]

    ro = c.post("/api/miniapp/ai-tokens", headers=_headers(guest), json={"name": "ro"}).json()["created"]
    rw = c.post("/api/miniapp/ai-tokens", headers=_headers(guest), json={"name": "rw", "write": True}).json()["created"]
    assert ro["scope"] == "kb:read" and rw["scope"] == "kb:write"

    def call(tok, name, args):
        r = c.post("/mcp/kb", headers={"Authorization": f"Bearer {tok}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return r.json()["result"]

    def tools(tok):
        r = c.post("/mcp/kb", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return {t["name"] for t in r.json()["result"]["tools"]}

    assert "create_doc" not in tools(ro["token"]) and "create_doc" in tools(rw["token"])
    assert call(ro["token"], "create_doc", {"section_id": sec, "title": "x", "markdown": "y"}).get("isError")

    sub = json.loads(call(rw["token"], "create_section", {"title": "Архитектура", "parent_id": sec})["content"][0]["text"])
    made = call(rw["token"], "create_doc", {"section_id": sub["section_id"], "title": "Обзор проекта",
                                            "markdown": "# Обзор\n\n- сервис A\n- сервис B"})
    assert not made.get("isError"), made
    doc_id = json.loads(made["content"][0]["text"])["id"]
    up = call(rw["token"], "update_doc", {"id": doc_id, "markdown": "## Деплой\n\nЧерез скрипт.", "mode": "append"})
    assert not up.get("isError"), up
    text = call(rw["token"], "read", {"id": doc_id})["content"][0]["text"]
    assert "сервис A" in text and "Деплой" in text
    from assistant.stores import note_versions

    cur = note_versions.current_states(doc_id)
    assert any(str(v.get("source") or "") == "ai" for v in cur.values()), cur  # в истории — правка ИИ
    # владелец видит документ в пространстве, автор — человек с токеном
    nodes = c.get(f"{base}/{oid}/kb", headers=_headers(owner)).json()["nodes"]
    doc = next(n for n in nodes if n.get("note_id") == doc_id)
    assert doc["owner_user_id"] == str(guest)
    # куда права только на чтение — не пишет; верхний уровень — только руководство
    assert call(rw["token"], "update_doc", {"id": locked, "markdown": "взлом"}).get("isError")
    assert call(rw["token"], "create_doc", {"section_id": closed, "title": "x", "markdown": "y"}).get("isError")
    assert call(rw["token"], "create_section", {"title": "Своё", "org_id": oid}).get("isError")


def _vh(uid):
    """Запрос к API сейфов — только на их адресе и с их сессией (vault_session)."""
    from assistant.lib import vault_host

    return {"Host": "vault.test", "Cookie": f"{vault_host.SESSION_COOKIE}={vault_host.issue_session(int(uid), {'id': int(uid)})}"}


def test_vault_host_handoff_and_isolation(env, monkeypatch):
    """Основной сайт к сейфам не пускает; вход на адрес сейфов — одноразовым кодом; там только сейфы."""
    c, owner = env["c"], env["owner"]
    monkeypatch.setenv("VAULTS_ENABLED", "1")
    assert c.get("/api/miniapp/vaults", headers=_headers(owner)).status_code == 404
    assert c.get("/api/miniapp/vaults", headers={"Host": "vault.test"}).status_code == 401
    url = c.post("/api/miniapp/vault-handoff", headers=_headers(owner)).json()["url"]
    code = url.split("#c=", 1)[1]
    assert c.post("/api/miniapp/vault-session", headers=_headers(owner), json={"code": code}).status_code == 404
    r = c.post("/api/miniapp/vault-session", headers={"Host": "vault.test"}, json={"code": code})
    assert r.status_code == 200 and "vault_session=" in r.headers.get("set-cookie", "")
    assert "samesite=strict" in r.headers["set-cookie"].lower()
    assert c.post("/api/miniapp/vault-session", headers={"Host": "vault.test"}, json={"code": code}).status_code == 400
    token = r.headers["set-cookie"].split("vault_session=", 1)[1].split(";", 1)[0]
    ok = c.get("/api/miniapp/vaults", headers={"Host": "vault.test", "Cookie": f"vault_session={token}"})
    assert ok.status_code == 200 and ok.json()["enabled"] is True
    # сессия основного сайта на адресе сейфов не подходит
    c.cookies.clear()
    assert c.get("/api/miniapp/vaults", headers={**_headers(owner), "Host": "vault.test"}).status_code == 401
    # на адресе сейфов — только сейфы, со строгим CSP
    assert c.get("/api/miniapp/me", headers={"Host": "vault.test"}).status_code == 404
    assert c.get("/webapp/app.js", headers={"Host": "vault.test"}).status_code == 404
    page = c.get("/", headers={"Host": "vault.test"})
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp and "trusted-types vault-html" in csp


def test_vaults_api_flag_and_flow(env, monkeypatch):
    """Сейфы: выключены флагом — пусто и 404; включены — ключи, сейф, запись, конверт коллеге."""
    c, owner, guest = env["c"], env["owner"], env["guest"]
    monkeypatch.delenv("VAULTS_ENABLED", raising=False)
    r = c.get("/api/miniapp/vaults", headers=_vh(owner))
    assert r.json() == {"ok": True, "enabled": False, "keys": None, "vaults": []}
    assert c.get("/api/miniapp/vaults/1/items", headers=_vh(owner)).status_code == 404

    monkeypatch.setenv("VAULTS_ENABLED", "1")
    org = c.post("/api/miniapp/orgs", headers=_headers(owner), json={"name": "Сейфы", "domain": env["domain"]}).json()["org"]
    c.post(f"/api/miniapp/orgs/{org['id']}/join", headers=_headers(guest))
    c.post(f"/api/miniapp/orgs/{org['id']}/approve", headers=_headers(owner), json={"user_id": guest})
    unl = [{"kind": "recovery", "salt": "c2FsdA", "wrapped_umk": '{"v":1,"iv":"aXY","ct":"Y3Q"}'}]
    for uid in (owner, guest):
        r = c.post("/api/miniapp/vaults/keys", headers=_vh(uid),
                   json={"public_key": f'{{"kty":"EC","x":"{uid}"}}', "enc_private": '{"v":1,"iv":"aXY","ct":"cHI"}', "unlockers": unl})
        assert r.status_code == 200, r.text
    r = c.post("/api/miniapp/vaults", headers=_vh(owner), json={"org_id": org["id"], "name": "Серверы", "envelope": '{"v":1}'})
    assert r.status_code == 200, r.text
    vid = r.json()["created"]["id"]
    # «всем» — нельзя (400, не 403)
    assert c.put(f"/api/miniapp/vaults/{vid}/access", headers=_vh(owner), json={"principal": "all", "role": "viewer"}).status_code == 400
    r = c.put(f"/api/miniapp/vaults/{vid}/access", headers=_vh(owner), json={"principal": f"u:{guest}", "role": "viewer"})
    assert [p["user_id"] for p in r.json()["vault"]["pending"]] == [str(guest)]
    r = c.post(f"/api/miniapp/vaults/{vid}/envelopes", headers=_vh(owner), json={"envelopes": [{"user_id": str(guest), "envelope": '{"v":1,"g":1}'}]})
    assert r.json()["put"] == 1
    r = c.post(f"/api/miniapp/vaults/{vid}/items", headers=_vh(owner), json={"blob": '{"v":1,"iv":"aXY","ct":"c2VjcmV0"}'})
    iid = r.json()["id"]
    got = c.get(f"/api/miniapp/vaults/{vid}/items", headers=_vh(guest)).json()["items"]
    assert [i["id"] for i in got] == [iid]
    # смотрящий не правит
    assert c.post(f"/api/miniapp/vaults/{vid}/items", headers=_vh(guest), json={"blob": '{"v":1}'}).status_code == 400
    assert c.post(f"/api/miniapp/vaults/{vid}/items/{iid}/log", headers=_vh(guest), json={"action": "copy"}).status_code == 200
    mine = c.get("/api/miniapp/vaults", headers=_vh(guest)).json()
    assert mine["vaults"][0]["envelope"] == '{"v":1,"g":1}'


def test_vaults_api_request_and_rotate(env, monkeypatch):
    """Владелец организации — владелец сейфа сотрудника; участник просит доступ, владелец открывает;
    смена ключа через API."""
    import usage_server

    c, owner, guest = env["c"], env["owner"], env["guest"]
    monkeypatch.setenv("VAULTS_ENABLED", "1")
    monkeypatch.setattr(usage_server, "_vault_notify_now", lambda: None)
    org = c.post("/api/miniapp/orgs", headers=_headers(owner), json={"name": "Запросы", "domain": env["domain"]}).json()["org"]
    c.post(f"/api/miniapp/orgs/{org['id']}/join", headers=_headers(guest))
    c.post(f"/api/miniapp/orgs/{org['id']}/approve", headers=_headers(owner), json={"user_id": guest})
    unl = [{"kind": "recovery", "salt": "c2FsdA", "wrapped_umk": '{"v":1,"iv":"aXY","ct":"Y3Q"}'}]
    for uid in (owner, guest):
        c.post("/api/miniapp/vaults/keys", headers=_vh(uid),
               json={"public_key": f'{{"kty":"EC","x":"{uid}"}}', "enc_private": '{"v":1}', "unlockers": unl})
    # без конверта владельцу организации — нельзя
    assert c.post("/api/miniapp/vaults", headers=_vh(guest),
                  json={"org_id": org["id"], "name": "Бэкапы", "envelope": '{"v":1}'}).status_code == 400
    owners = c.get("/api/miniapp/vaults", headers=_vh(guest)).json()["org_owner_keys"][str(org["id"])]
    assert [o["user_id"] for o in owners] == [str(owner)]
    vid = c.post("/api/miniapp/vaults", headers=_vh(guest), json={
        "org_id": org["id"], "name": "Бэкапы", "envelope": '{"v":1,"me":1}',
        "owner_envelopes": [{"user_id": str(owner), "envelope": '{"v":1,"own":1}'}],
    }).json()["created"]["id"]
    mine = [v for v in c.get("/api/miniapp/vaults", headers=_vh(owner)).json()["vaults"] if v["id"] == vid][0]
    assert mine["role"] == "owner" and mine["envelope"] == '{"v":1,"own":1}'
    # владелец организации забирает себе сейф целиком: сотрудника убирает из доступа — а запрос: сотрудник просит снова
    r = c.put(f"/api/miniapp/vaults/{vid}/access", headers=_vh(owner), json={"principal": f"u:{guest}", "role": ""})
    assert r.status_code == 200, r.text
    foreign = [v for v in c.get("/api/miniapp/vaults", headers=_vh(guest)).json()["vaults"] if v["id"] == vid][0]
    assert foreign["foreign"] and foreign["envelope"] is None
    assert c.get(f"/api/miniapp/vaults/{vid}/items", headers=_vh(guest)).status_code == 404
    assert c.post(f"/api/miniapp/vaults/{vid}/request", headers=_vh(guest)).status_code == 200
    mine = c.get("/api/miniapp/vaults", headers=_vh(owner)).json()["vaults"]
    assert [r["user_id"] for r in [v for v in mine if v["id"] == vid][0]["requests"]] == [str(guest)]
    r = c.post(f"/api/miniapp/vaults/{vid}/request/decide", headers=_vh(owner), json={"user_id": str(guest), "role": "editor"})
    assert r.status_code == 200, r.text
    assert [p["user_id"] for p in r.json()["vault"]["pending"]] == [str(guest)]
    # смена ключа (после ухода сотрудника флаг стоит): записи и конверты заново
    v = r.json()["vault"]
    assert v["rotate"]
    r = c.post(f"/api/miniapp/vaults/{vid}/rotate", headers=_vh(owner), json={
        "key_version": v["key_version"], "items": [],
        "envelopes": [{"user_id": str(owner), "envelope": '{"v":1,"own":2}'}, {"user_id": str(guest), "envelope": '{"v":1,"g":2}'}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["vault"]["key_version"] == v["key_version"] + 1
    assert c.post(f"/api/miniapp/vaults/{vid}/items", headers=_vh(guest),
                  json={"blob": '{"v":1}', "key_version": v["key_version"]}).status_code == 400
