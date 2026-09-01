from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

from assistant.lib.message_context import telegram_message_link


def _msg(*, chat_id: int, chat_type: str, message_id: int, username: str | None = None):
    msg = MagicMock()
    msg.message_id = message_id
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.chat.username = username
    return msg


class TelegramMessageLinkTests(unittest.TestCase):
    def test_private_chat_uses_bot_username_not_user_id(self) -> None:
        msg = _msg(chat_id=123456789, chat_type="private", message_id=42)
        link = telegram_message_link(msg, bot_username="my_leo_bot", bot_id=999)
        self.assertEqual(link, "https://t.me/my_leo_bot/42")
        self.assertNotIn("123456789", link or "")

    def test_private_chat_fallback_bot_id(self) -> None:
        msg = _msg(chat_id=123456789, chat_type="private", message_id=7)
        link = telegram_message_link(msg, bot_id=555)
        self.assertEqual(link, "tg://openmessage?chat_id=555&message_id=7")

    def test_supergroup_without_username(self) -> None:
        msg = _msg(chat_id=-1001234567890, chat_type="supergroup", message_id=15)
        link = telegram_message_link(msg)
        self.assertEqual(link, "https://t.me/c/1234567890/15")

    def test_public_group_username(self) -> None:
        msg = _msg(
            chat_id=-1001,
            chat_type="supergroup",
            message_id=3,
            username="team_chat",
        )
        link = telegram_message_link(msg)
        self.assertEqual(link, "https://t.me/team_chat/3")

    def test_env_bot_username(self) -> None:
        os.environ["TELEGRAM_BOT_USERNAME"] = "env_bot"
        try:
            msg = _msg(chat_id=1, chat_type="private", message_id=1)
            link = telegram_message_link(msg)
            self.assertEqual(link, "https://t.me/env_bot/1")
        finally:
            os.environ.pop("TELEGRAM_BOT_USERNAME", None)


if __name__ == "__main__":
    unittest.main()
