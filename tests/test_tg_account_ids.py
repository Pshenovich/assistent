"""Перевод Telegram id ↔ id аккаунта на уровне Bot API и привязка Telegram по почте в боте."""

from __future__ import annotations

import asyncio
import itertools
import json
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop
from telegram.request import RequestData
from telegram.request._requestparameter import RequestParameter

from assistant.bot import email_link
from assistant.lib import tg_account_ids
from assistant.services import transactional_mail
from assistant.stores import accounts, orgs_store

_ids = itertools.count(970_000, 10)
_n = itertools.count(1)


def _email() -> str:
    return f"tg{next(_n)}-{id(object())}@acme.io"


@pytest.fixture(autouse=True)
def _fresh_cache():
    tg_account_ids._IN.clear()
    tg_account_ids._OUT.clear()


def test_outgoing_maps_synthetic_and_keeps_regular():
    acc = accounts.create_email_account(_email())
    uid, tg = acc["user_id"], next(_ids)
    assert tg_account_ids.outgoing_params({"chat_id": 12345, "text": "x"}) == {"chat_id": 12345, "text": "x"}
    assert tg_account_ids.outgoing_params({"chat_id": -100123}) == {"chat_id": -100123}
    assert tg_account_ids.outgoing_params({"chat_id": uid}) is None  # Telegram не привязан
    accounts.link_tg(uid, tg)
    tg_account_ids.forget(uid)
    assert tg_account_ids.outgoing_params({"chat_id": str(uid), "text": "x"}) == {"chat_id": tg, "text": "x"}


def test_incoming_rewrites_from_of_linked_users_only():
    acc = accounts.create_email_account(_email())
    tg, other = next(_ids), next(_ids)
    accounts.link_tg(acc["user_id"], tg)
    body = json.dumps({"ok": True, "result": [
        {"update_id": 1, "message": {"message_id": 1, "chat": {"id": tg, "type": "private"},
                                     "from": {"id": tg, "is_bot": False, "first_name": "A"}, "text": "hi"}},
        {"update_id": 2, "message": {"message_id": 2, "chat": {"id": other, "type": "private"},
                                     "from": {"id": other, "is_bot": False, "first_name": "B"}, "text": "hi"}},
    ]}).encode()
    out = json.loads(tg_account_ids.incoming_updates(body))["result"]
    assert out[0]["message"]["from"]["id"] == acc["user_id"]
    assert out[0]["message"]["chat"]["id"] == tg  # ответы — в тот же чат
    assert out[1]["message"]["from"]["id"] == other
    # ничего не поменялось — тело то же самое (без лишней пересборки)
    plain = json.dumps({"ok": True, "result": [{"update_id": 3}]}).encode()
    assert tg_account_ids.incoming_updates(plain) is plain


def test_request_layer_maps_chat_id(monkeypatch):
    acc = accounts.create_email_account(_email())
    tg = next(_ids)
    accounts.link_tg(acc["user_id"], tg)
    seen = {}

    async def fake_super(self, url, method, request_data=None, **kw):
        seen["params"] = request_data.parameters if request_data else None
        return 200, b'{"ok":true,"result":true}'

    monkeypatch.setattr(tg_account_ids.HTTPXRequest, "do_request", fake_super)
    req = tg_account_ids.AccountMappingRequest()
    data = RequestData([RequestParameter.from_input("chat_id", acc["user_id"]), RequestParameter.from_input("text", "hi")])
    status, _ = asyncio.run(req.do_request("https://x/bot1/sendMessage", "POST", data))
    assert status == 200 and seen["params"]["chat_id"] == tg and seen["params"]["text"] == "hi"
    lonely = accounts.create_email_account(_email())
    data = RequestData([RequestParameter.from_input("chat_id", lonely["user_id"])])
    status, body = asyncio.run(req.do_request("https://x/bot1/sendMessage", "POST", data))
    assert status == 400 and b"chat not found" in body


def test_requests_post_wrapper(monkeypatch):
    lonely = accounts.create_email_account(_email())
    calls = []
    monkeypatch.setattr("requests.post", lambda url, **kw: calls.append(kw) or SimpleNamespace(ok=True))
    r = tg_account_ids.post("https://x/sendMessage", json={"chat_id": lonely["user_id"], "text": "x"}, timeout=1)
    assert r.ok is False and calls == []
    tg_account_ids.post("https://x/sendMessage", json={"chat_id": 42, "text": "x"}, timeout=1)
    assert calls[-1]["json"]["chat_id"] == 42


def test_recognize_by_org_nick(monkeypatch):
    monkeypatch.setenv("EMAIL_REQUIRED", "1")
    owner, newbie, stranger = next(_ids), next(_ids), next(_ids)
    accounts.attach_email(owner, _email())
    org = orgs_store.create_org(owner, f"Org {owner}")
    orgs_store.add_member(owner, org["id"], _email(), tg=f"nick{newbie}")
    ok, joined = email_link.recognize(newbie, f"@nick{newbie}")
    assert ok and joined == [org["name"]]
    assert orgs_store.role_of(org["id"], newbie) == "member"
    assert email_link.recognize(stranger, "nobody_here") == (False, [])
    assert email_link.recognize(owner, None)[0] is True
    monkeypatch.setenv("EMAIL_REQUIRED", "0")
    assert email_link.recognize(stranger, None)[0] is True


class _Msg:
    def __init__(self, text):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


def _update(tg: int, text: str):
    msg = _Msg(text)
    upd = SimpleNamespace(
        effective_chat=SimpleNamespace(type="private", id=tg),
        effective_message=msg,
        effective_user=SimpleNamespace(id=tg, username=None),
    )
    return upd, msg


def _run(coro):
    try:
        asyncio.run(coro)
    except ApplicationHandlerStop:
        pass


def test_bot_email_then_code_links_to_invited_org(monkeypatch):
    sent = []
    monkeypatch.setattr(transactional_mail, "send_login_code", lambda to, code, ttl_min=10, login_url=None: sent.append((to, code)))
    started = []

    async def fake_continue(update, context, uid):
        started.append(uid)

    monkeypatch.setattr(email_link, "_continue_start", fake_continue)
    owner, tg = next(_ids), next(_ids)
    accounts.attach_email(owner, _email())
    org = orgs_store.create_org(owner, f"Org {owner}")
    email = _email()
    orgs_store.add_member(owner, org["id"], email)
    ctx = SimpleNamespace(user_data={})

    upd, msg = _update(tg, "привет")
    _run(email_link.prompt(upd, ctx))
    assert ctx.user_data[email_link.STATE_KEY]["step"] == "email" and msg.replies == [email_link.ASK_EMAIL]

    upd, msg = _update(tg, "не почта")
    _run(email_link.handle_message(upd, ctx))
    assert "не похоже на почту" in msg.replies[-1]

    upd, msg = _update(tg, email.upper())
    _run(email_link.handle_message(upd, ctx))
    assert sent[-1][0] == email and ctx.user_data[email_link.STATE_KEY] == {"step": "code", "email": email}

    upd, msg = _update(tg, "12")
    _run(email_link.handle_message(upd, ctx))
    assert "6 цифр" in msg.replies[-1]

    code = sent[-1][1]
    upd, msg = _update(tg, code[:3] + " " + code[3:])
    _run(email_link.handle_message(upd, ctx))
    assert email_link.STATE_KEY not in ctx.user_data
    assert "подтверждена" in msg.replies[-1] and org["name"] in msg.replies[-1]
    assert started == [tg]
    assert accounts.email_of(tg) == email and accounts.account_for_tg(tg) == tg
    assert orgs_store.role_of(org["id"], tg) == "member"


def test_bot_code_links_telegram_to_web_account(monkeypatch):
    """Аккаунт завели по почте в вебе; в боте та же почта — Telegram привязывается к нему."""
    sent = []
    monkeypatch.setattr(transactional_mail, "send_login_code", lambda to, code, ttl_min=10, login_url=None: sent.append((to, code)))
    monkeypatch.setattr(email_link, "_continue_start", lambda *a: asyncio.sleep(0))
    email = _email()
    acc = accounts.create_email_account(email)
    tg = next(_ids)
    ctx = SimpleNamespace(user_data={email_link.STATE_KEY: {"step": "email"}})
    _run(email_link.handle_message(_update(tg, email)[0], ctx))
    _run(email_link.handle_message(_update(tg, sent[-1][1])[0], ctx))
    assert accounts.account_for_tg(tg) == acc["user_id"]
    assert accounts.tg_chat_for(acc["user_id"]) == tg


def test_commands_pass_through_and_cancel():
    ctx = SimpleNamespace(user_data={email_link.STATE_KEY: {"step": "email"}})
    upd, msg = _update(next(_ids), "/start")
    asyncio.run(email_link.handle_message(upd, ctx))  # не перехватывает
    assert msg.replies == [] and email_link.STATE_KEY in ctx.user_data
    upd, msg = _update(next(_ids), "/cancel")
    _run(email_link.handle_message(upd, ctx))
    assert email_link.STATE_KEY not in ctx.user_data


def test_change_telegram_by_profile_link():
    """Профиль → «Сменить Telegram»: ссылка в бота с токеном, Start с нового Telegram."""
    acc = accounts.create_email_account(_email())
    uid, old_tg, new_tg = acc["user_id"], next(_ids), next(_ids)
    accounts.link_tg(uid, old_tg)
    token = accounts.issue_tg_link(uid)
    got, old = accounts.link_tg_by_token(token, new_tg)
    assert got["tg_user_id"] == new_tg and old == old_tg
    assert accounts.account_for_tg(new_tg) == uid
    assert accounts.account_for_tg(old_tg) == old_tg  # старый Telegram больше не этот аккаунт
    with pytest.raises(accounts.AccountError):
        accounts.link_tg_by_token(token, next(_ids))  # одноразовая


def test_telegram_of_legacy_account_not_changeable():
    legacy = next(_ids)
    accounts.attach_email(legacy, _email())
    assert accounts.tg_changeable(legacy) is False
    with pytest.raises(accounts.AccountError):
        accounts.issue_tg_link(legacy)
    # Telegram со своим аккаунтом (почтой) не переносим на чужой аккаунт
    acc = accounts.create_email_account(_email())
    with pytest.raises(accounts.AccountError):
        accounts.link_tg_by_token(accounts.issue_tg_link(acc["user_id"]), legacy)


def test_bot_start_link(monkeypatch):
    acc = accounts.create_email_account(_email())
    tg = next(_ids)
    started = []

    async def fake_continue(update, context, uid):
        started.append(uid)

    monkeypatch.setattr(email_link, "_continue_start", fake_continue)
    upd, msg = _update(tg, "/start")
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace())
    asyncio.run(email_link.link_by_start(upd, ctx, accounts.issue_tg_link(acc["user_id"])))
    assert "привязан" in msg.replies[-1] and started == [acc["user_id"]]
    assert accounts.account_for_tg(tg) == acc["user_id"]
