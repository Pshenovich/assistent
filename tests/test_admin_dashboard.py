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


def test_users_list_twins_email_org_and_merge(env, monkeypatch):
    """Два аккаунта одного человека — рядом, почта вместо «ID 5…», доступ через организацию, склейка."""
    from assistant.stores import accounts, note_members

    c, op = env["c"], env["op"]
    monkeypatch.setattr(orgs_store, "verified_emails", lambda uid: [])
    tg = next(_ids)
    note_members.upsert_profile(tg, username="twin_admin_nick", first_name="Тимур")
    mail = f"twin-admin-{tg}@obuchat.me"
    email_uid = int(accounts.create_email_account(mail)["user_id"])
    note_members.upsert_profile(email_uid, username="twin_admin_nick")
    lonely = int(accounts.create_email_account(f"lonely-{tg}@obuchat.me")["user_id"])
    member = next(_ids)
    note_members.upsert_profile(member, first_name="Олег")
    orgs_store.create_org(member, "Twin Org")
    # доступ к чужому документу, выданный почтовому аккаунту
    with note_members._LOCK:
        conn = note_members._conn()
        conn.execute("INSERT INTO note_members (owner_user_id, note_id, member_user_id, role, added_at) "
                     "VALUES (?, 1, ?, 'edit', '2026-11-10')", (str(op), str(email_uid)))
        conn.commit()

    _login(c, op)
    page = c.get("/dashboard/users").text
    assert "Доступ в бота" in page
    assert f"lonely-{tg}@obuchat.me" in page and f"ID {lonely}" not in page
    assert page.count("тот же человек") == 2 and "Склеить" in page
    i_tg, i_mail = page.index(f"/user/{tg}\""), page.index(f"/user/{email_uid}\"")
    assert abs(i_tg - i_mail) < 2000  # строки пары рядом
    row = page[page.index(f"/user/{member}\""):]
    row = row[:row.index("</tr>")]
    assert "Через организацию" in row and "Открыть доступ" not in row

    r = c.post(f"/dashboard/user/{email_uid}/merge", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/dashboard/merge?a={email_uid}&b={tg}"
    preview = c.get(r.headers["location"]).text
    assert "пробный прогон" in preview and "note_members" in preview
    assert accounts.get(email_uid) is not None  # пробный прогон ничего не меняет
    r = c.post("/dashboard/merge", data={"src": str(email_uid), "dst": str(tg)}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    twins = c.get("/dashboard/twins")
    assert twins.status_code == 200 and "Двойники" in twins.text
    acc = accounts.get_by_email(mail)
    assert acc and int(acc["user_id"]) == tg and accounts.resolve_alias(email_uid) == tg
    with note_members._LOCK:
        got = note_members._conn().execute(
            "SELECT member_user_id FROM note_members WHERE owner_user_id = ? AND note_id = 1", (str(op),)).fetchall()
    assert [str(x[0]) for x in got] == [str(tg)]
    page = c.get("/dashboard/users").text
    assert f"/user/{email_uid}\"" not in page and "тот же человек" not in page
    assert c.get(f"/dashboard/user/{email_uid}", follow_redirects=False).status_code == 303
    assert audit.recent(target=f"user:{tg}")[0]["action"] == "account.merge"
