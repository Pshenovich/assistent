"""Бот присылает публичную ссылку на слоты по коротким запросам."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from assistant.skills import calendar as calendar_skill


class CalendarBookingLinkSkillTests(unittest.IsolatedAsyncioTestCase):
    def _make_update(self, text: str, user_id: int = 42):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.message.reply_to_message = None
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_user.username = "tester"
        return update

    @patch("assistant.skills.calendar.nlu_llm.parse_calendar")
    @patch("assistant.services.booking_links.attach_source_message")
    @patch("assistant.services.booking_links.create_booking_link")
    async def test_skiny_slots_replies_with_url(
        self, mock_create, mock_attach, mock_parse
    ) -> None:
        mock_parse.return_value = {"intent": "free_slots", "attendees": []}
        mock_create.return_value = {
            "url": "https://assistent.networ.ru/book/abc",
            "token": "abc",
        }
        update = self._make_update("скинь слоты")
        update.message.chat = MagicMock()
        update.message.chat.id = 42
        sent = MagicMock()
        sent.message_id = 77
        update.message.reply_text = AsyncMock(return_value=sent)
        context = MagicMock()
        context.bot.username = "LeoBot"
        await calendar_skill.handle(update, context, "скинь слоты", kind="free")
        mock_create.assert_called_once()
        mock_attach.assert_called_once_with("abc", 42, 77)
        update.message.reply_text.assert_awaited()
        body = update.message.reply_text.await_args.args[0]
        self.assertIn("https://assistent.networ.ru/book/abc", body)

    @patch("assistant.skills.calendar.nlu_llm.parse_calendar")
    @patch("assistant.services.calendar.list_events_day")
    @patch("assistant.services.booking_links.create_booking_link")
    async def test_meeting_list_does_not_mint_link(
        self, mock_create, mock_list, mock_parse
    ) -> None:
        mock_parse.return_value = {
            "intent": "free_slots",
            "free_slots_date": "2026-10-09",
            "attendees": [],
        }
        mock_list.return_value = []
        update = self._make_update("какие встречи завтра")
        context = MagicMock()
        context.bot.username = "LeoBot"
        await calendar_skill.handle(
            update, context, "какие встречи завтра", kind="free"
        )
        mock_create.assert_not_called()
        mock_list.assert_called()
