"""Групповой фильтр: только @бот или ответ на сообщение бота."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from telegram import MessageEntity, User
from telegram.constants import MessageEntityType

from assistant.bot.group_gate import (
    is_bot_mentioned,
    is_reply_to_bot,
    should_process_in_chat,
    should_process_message,
    user_has_pending_dialog,
)
from assistant.lib import calendar_pending_store as cps


def _msg(
    *,
    text: str | None = None,
    caption: str | None = None,
    entities=None,
    caption_entities=None,
    reply_to=None,
):
    return SimpleNamespace(
        text=text,
        caption=caption,
        entities=entities,
        caption_entities=caption_entities,
        reply_to_message=reply_to,
    )


class GroupGateTests(unittest.TestCase):
    def test_group_ignored_without_mention_or_reply_to_bot(self):
        msg = _msg(text="привет всем")
        self.assertFalse(
            should_process_in_chat(
                msg, chat_type="supergroup", bot_id=1, bot_username="mybot"
            )
        )

    def test_group_accepts_at_mention_in_text(self):
        msg = _msg(text="@mybot напомни завтра")
        self.assertTrue(
            should_process_in_chat(
                msg, chat_type="group", bot_id=1, bot_username="mybot"
            )
        )

    def test_group_accepts_entity_mention(self):
        ent = MessageEntity(type=MessageEntityType.MENTION, offset=0, length=6)
        msg = _msg(text="@mybot hi", entities=[ent])
        self.assertTrue(is_bot_mentioned(msg, bot_id=1, username="mybot"))

    def test_group_accepts_reply_to_bot_only(self):
        bot_user = User(id=99, is_bot=True, first_name="B")
        human = User(id=2, is_bot=False, first_name="H")
        bot_msg = SimpleNamespace(from_user=bot_user)
        human_msg = SimpleNamespace(from_user=human)
        self.assertTrue(is_reply_to_bot(_msg(reply_to=bot_msg), bot_id=99))
        self.assertFalse(is_reply_to_bot(_msg(reply_to=human_msg), bot_id=99))
        self.assertTrue(
            should_process_in_chat(
                _msg(text="да", reply_to=bot_msg),
                chat_type="supergroup",
                bot_id=99,
                bot_username="mybot",
            )
        )
        self.assertFalse(
            should_process_in_chat(
                _msg(text="да", reply_to=human_msg),
                chat_type="supergroup",
                bot_id=99,
                bot_username="mybot",
            )
        )

    def test_private_always_allowed(self):
        self.assertTrue(
            should_process_in_chat(
                _msg(text="любой текст"),
                chat_type="private",
                bot_id=1,
                bot_username="mybot",
            )
        )

    def test_group_caption_mention(self):
        ent = MessageEntity(type=MessageEntityType.MENTION, offset=0, length=6)
        msg = _msg(caption="@mybot файл", caption_entities=[ent])
        self.assertTrue(
            should_process_in_chat(
                msg, chat_type="group", bot_id=1, bot_username="mybot"
            )
        )

    def test_group_pending_contact_reply_without_mention(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cal.json"
            with mock.patch.object(cps, "store_path", return_value=path):
                cps.set_pending(
                    -1001,
                    {
                        "kind": "await_contact_details",
                        "user_id": 42,
                        "step": "name",
                        "parsed": {"intent": "create_event"},
                        "missing_names": ["Артем"],
                    },
                    user_id=42,
                )
                update = SimpleNamespace(
                    message=_msg(text="Артем Данилин artem.danilin.1999@gmail.com"),
                    effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
                    effective_user=SimpleNamespace(id=42),
                )
                context = SimpleNamespace(
                    bot=SimpleNamespace(id=1, username="mybot"),
                    user_data={},
                )
                self.assertTrue(user_has_pending_dialog(update, context))
                self.assertTrue(should_process_message(update, context))


if __name__ == "__main__":
    unittest.main()
