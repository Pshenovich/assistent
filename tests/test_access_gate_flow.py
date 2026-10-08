"""Путь заявки на доступ: сообщение боту → заявка → кнопка администратора."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import assistant.bot.access_gate as gate
import assistant.lib.telegram_access_allowlist as access

APPROVER = 106278723
STRANGER = 555


@pytest.fixture(autouse=True)
def _gate_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_GATE_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", str(APPROVER))
    monkeypatch.setenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", str(tmp_path / "allowed.json"))
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "req.json"))
    monkeypatch.setenv("TELEGRAM_REGISTRY_PATH", str(tmp_path / "reg.json"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "leo-token")
    monkeypatch.delenv("TG_DONATELLO_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOARD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_API_BASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)
    access.invalidate_allowlist_cache()


def _user(uid: int, *, is_bot: bool = False, username: str = "new_user"):
    return SimpleNamespace(id=uid, is_bot=is_bot, username=username, first_name="New", last_name="User")


def _message_update(uid: int, **kw):
    msg = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_user=_user(uid, **kw), effective_message=msg), msg


def _context():
    return SimpleNamespace(bot=SimpleNamespace(token="leo-token", send_message=AsyncMock()))


def _callback_update(approver_id: int, data: str):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(callback_query=q, effective_user=_user(approver_id)), q


def _ensure(update, ctx) -> bool:
    with patch.object(gate, "_notify_approvers_sync") as notify:
        ok = asyncio.run(gate.ensure_access(update, ctx))
    return ok, notify


def test_bot_accounts_are_never_allowed():
    update, msg = _message_update(APPROVER, is_bot=True)
    ok, notify = _ensure(update, _context())
    assert ok is False
    msg.reply_text.assert_not_called()
    notify.assert_not_called()


def test_approver_passes_without_request():
    update, msg = _message_update(APPROVER)
    ok, notify = _ensure(update, _context())
    assert ok is True
    msg.reply_text.assert_not_called()
    notify.assert_not_called()


def test_new_user_creates_one_request_and_notifies_approvers():
    update, msg = _message_update(STRANGER)
    ok, notify = _ensure(update, _context())

    assert ok is False
    assert access.is_pending(STRANGER)
    assert "отправлена администратору" in msg.reply_text.await_args.args[0]
    notify.assert_called_once()
    assert notify.call_args.kwargs["user_id"] == STRANGER

    update2, msg2 = _message_update(STRANGER)
    ok2, notify2 = _ensure(update2, _context())
    assert ok2 is False
    assert "уже отправлена" in msg2.reply_text.await_args.args[0]
    notify2.assert_not_called()


def test_denied_user_gets_refusal_without_new_request():
    access.deny_user(STRANGER)
    update, msg = _message_update(STRANGER)
    ok, notify = _ensure(update, _context())
    assert ok is False
    assert "не предоставлен" in msg.reply_text.await_args.args[0]
    assert not access.is_pending(STRANGER)
    notify.assert_not_called()


def test_registry_failure_does_not_block_access_check():
    from assistant.stores import telegram_registry

    update, _ = _message_update(APPROVER)
    with patch.object(telegram_registry, "register_user", side_effect=OSError("disk full")):
        ok, _ = _ensure(update, _context())
    assert ok is True


def test_gate_open_when_no_approvers_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ACCESS_APPROVER_IDS", raising=False)
    monkeypatch.delenv("MINIAPP_DEV_TELEGRAM_USER_ID", raising=False)
    assert gate.is_user_allowed(STRANGER, "anyone") is True


def _pending_token() -> str:
    return access.register_pending_request(
        user_id=STRANGER, username="new_user", first_name="New", last_name="User"
    )


def test_non_approver_cannot_approve():
    token = _pending_token()
    update, q = _callback_update(STRANGER, f"acc:ok:{token}")
    ctx = _context()

    asyncio.run(gate.handle_access_callback(update, ctx))

    assert q.answer.await_args.args[0] == "Недостаточно прав."
    assert not gate.is_user_allowed(STRANGER, "new_user")
    assert access.is_pending(STRANGER)
    ctx.bot.send_message.assert_not_called()


def test_approver_approves_request():
    token = _pending_token()
    update, q = _callback_update(APPROVER, f"acc:ok:{token}")
    ctx = _context()

    asyncio.run(gate.handle_access_callback(update, ctx))

    assert gate.is_user_allowed(STRANGER, "new_user")
    assert "Доступ разрешён" in q.edit_message_text.await_args.args[0]
    assert ctx.bot.send_message.await_args.args[0] == STRANGER


def test_approver_denies_request():
    token = _pending_token()
    update, q = _callback_update(APPROVER, f"acc:no:{token}")
    ctx = _context()

    asyncio.run(gate.handle_access_callback(update, ctx))

    assert access.is_denied(STRANGER)
    assert not access.is_pending(STRANGER)
    assert not gate.is_user_allowed(STRANGER, "new_user")
    assert "Доступ отклонён" in q.edit_message_text.await_args.args[0]


def test_action_token_is_single_use():
    token = _pending_token()
    asyncio.run(gate.handle_access_callback(_callback_update(APPROVER, f"acc:no:{token}")[0], _context()))

    update, q = _callback_update(APPROVER, f"acc:ok:{token}")
    asyncio.run(gate.handle_access_callback(update, _context()))

    assert "устарела" in q.edit_message_text.await_args.args[0]
    assert not gate.is_user_allowed(STRANGER, "new_user")


@pytest.mark.parametrize("data", ["acc:ok:forged-token", "acc:ok", "garbage"])
def test_forged_or_malformed_callback_changes_nothing(data: str):
    _pending_token()
    update, _ = _callback_update(APPROVER, data)

    asyncio.run(gate.handle_access_callback(update, _context()))

    assert access.is_pending(STRANGER)
    assert not gate.is_user_allowed(STRANGER, "new_user")


def test_user_notification_failure_does_not_undo_approval():
    token = _pending_token()
    update, _ = _callback_update(APPROVER, f"acc:ok:{token}")
    ctx = _context()
    ctx.bot.send_message.side_effect = RuntimeError("blocked by user")

    asyncio.run(gate.handle_access_callback(update, ctx))

    assert gate.is_user_allowed(STRANGER, "new_user")


def test_notify_approvers_sends_buttons_to_each_approver(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", "1,2")
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw["json"]))
        if kw["json"]["chat_id"] == 1:
            raise OSError("network down")

    with patch.object(gate.requests, "post", side_effect=fake_post):
        gate._notify_approvers_sync(
            token="tok", user_id=STRANGER, username="new_user", first_name="New", last_name="User"
        )

    assert [c[1]["chat_id"] for c in calls] == [1, 2]
    assert all(url == "https://api.telegram.org/botleo-token/sendMessage" for url, _ in calls)
    buttons = calls[1][1]["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["acc:ok:tok", "acc:no:tok"]


def test_notify_approvers_skips_without_bot_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with patch.object(gate.requests, "post") as post:
        gate._notify_approvers_sync(token="t", user_id=1, username=None, first_name="", last_name="")
    post.assert_not_called()


def test_miniapp_denied_and_pending_messages():
    access.deny_user(STRANGER)
    allowed, msg = gate.miniapp_access_message(user_id=STRANGER, username="u")
    assert (allowed, "не предоставлен" in msg) == (False, True)

    other = STRANGER + 1
    access.register_pending_request(user_id=other, username="v", first_name="", last_name="")
    with patch.object(gate, "_notify_approvers_sync") as notify:
        allowed, msg = gate.miniapp_access_message(user_id=other, username="v")
    assert (allowed, "уже отправлена" in msg) == (False, True)
    notify.assert_not_called()
