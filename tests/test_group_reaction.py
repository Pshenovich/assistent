import asyncio
from unittest.mock import AsyncMock, MagicMock

from assistant.bot import group_reaction as gr


def test_react_group_message_seen_calls_api(monkeypatch):
    monkeypatch.setenv("TELEGRAM_GROUP_SEEN_REACTION_ENABLED", "1")
    bot = MagicMock()
    bot.set_message_reaction = AsyncMock()
    context = MagicMock()
    context.bot = bot
    msg = MagicMock()
    msg.message_id = 42
    chat = MagicMock()
    chat.type = "supergroup"
    chat.id = -1001
    update = MagicMock()
    update.message = msg
    update.effective_chat = chat

    asyncio.run(gr.react_group_message_seen(update, context))

    bot.set_message_reaction.assert_awaited_once_with(
        chat_id=-1001,
        message_id=42,
        reaction="👀",
    )


def test_react_skips_private_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_GROUP_SEEN_REACTION_ENABLED", "1")
    bot = MagicMock()
    bot.set_message_reaction = AsyncMock()
    context = MagicMock()
    context.bot = bot
    msg = MagicMock()
    msg.message_id = 1
    chat = MagicMock()
    chat.type = "private"
    update = MagicMock()
    update.message = msg
    update.effective_chat = chat

    asyncio.run(gr.react_group_message_seen(update, context))

    bot.set_message_reaction.assert_not_awaited()
