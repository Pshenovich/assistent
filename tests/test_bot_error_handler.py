"""Глобальный обработчик ошибок Leo: traceback в лог, ответ только в личке."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from assistant.bot import handlers


def _boom() -> Exception:
    try:
        raise ValueError("boom")
    except ValueError as e:
        return e


def _update(chat_type: str | None):
    msg = SimpleNamespace(reply_text=AsyncMock())
    chat = SimpleNamespace(type=chat_type) if chat_type else None
    return SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_chat=chat, effective_message=msg), msg


def _run(update) -> None:
    asyncio.run(handlers.handle_error(update, SimpleNamespace(error=_boom())))


def test_private_chat_gets_reply_and_traceback_is_logged(capsys):
    update, msg = _update("private")
    _run(update)

    msg.reply_text.assert_awaited_once_with(handlers.HANDLER_ERROR_REPLY)
    out = capsys.readouterr().out
    assert "[bot] handler_error user=42 err=ValueError('boom')" in out
    assert "Traceback (most recent call last)" in out
    assert "_boom" in out


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_group_chats_get_no_reply(chat_type):
    update, msg = _update(chat_type)
    _run(update)
    msg.reply_text.assert_not_called()


def test_error_without_update_is_only_logged(capsys):
    asyncio.run(handlers.handle_error(None, SimpleNamespace(error=_boom())))
    assert "handler_error user=None" in capsys.readouterr().out


def test_failed_reply_does_not_raise(capsys):
    update, msg = _update("private")
    msg.reply_text.side_effect = RuntimeError("bot blocked")
    _run(update)
    assert "reply failed" in capsys.readouterr().out


def test_error_handler_is_registered():
    app = MagicMock()
    handlers.register_handlers(app)
    app.add_error_handler.assert_called_once_with(handlers.handle_error)
