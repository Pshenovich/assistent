import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import assistant.bot.onboarding as onboarding
import assistant.stores.user_prefs as up


class OnboardingMarkdownTests(unittest.TestCase):
    def test_welcome_card_contains_table_and_details(self) -> None:
        body = onboarding.welcome_card_markdown(bot_username="leo_assistant_bot")
        self.assertIn("## Leo", body)
        self.assertIn("| Что нужно | Пример фразы |", body)
        self.assertIn("<details>", body)
        self.assertIn("/calendar_auth", body)
        self.assertIn("/yandex_disk_auth", body)
        self.assertIn("Мини-app", body)
        self.assertIn("Актуальное", body)
        self.assertIn("Мои контакты", body)
        self.assertIn("корпоративный тариф", body)
        self.assertIn("@leo_assistant_bot", body)
        self.assertNotIn("@mention", body)
        self.assertNotIn("todoist", body.lower())

    def test_bot_mention_from_env(self) -> None:
        prev = os.environ.get("TELEGRAM_BOT_USERNAME")
        os.environ["TELEGRAM_BOT_USERNAME"] = "@MyLeoBot"
        try:
            self.assertEqual(onboarding._bot_mention(), "@MyLeoBot")
        finally:
            if prev is None:
                os.environ.pop("TELEGRAM_BOT_USERNAME", None)
            else:
                os.environ["TELEGRAM_BOT_USERNAME"] = prev

    def test_welcome_slideshow_with_urls(self) -> None:
        urls = [
            "https://example.com/1.jpg",
            "https://example.com/2.jpg",
        ]
        body = onboarding.welcome_slideshow_markdown(urls)
        self.assertIn("<tg-slideshow>", body)
        self.assertIn("https://example.com/1.jpg", body)
        self.assertIn("Встречи", body)

    def test_onboarding_mode_slideshow_without_urls_falls_back_to_card(self) -> None:
        prev_mode = os.environ.get("TELEGRAM_ONBOARDING_MODE")
        prev_urls = os.environ.get("TELEGRAM_ONBOARDING_SLIDE_URLS")
        try:
            os.environ["TELEGRAM_ONBOARDING_MODE"] = "slideshow"
            os.environ.pop("TELEGRAM_ONBOARDING_SLIDE_URLS", None)
            body = onboarding._welcome_body(bot_username="testbot")
            self.assertIn("| Что нужно | Пример фразы |", body)
            self.assertNotIn("<tg-slideshow>", body)
        finally:
            if prev_mode is None:
                os.environ.pop("TELEGRAM_ONBOARDING_MODE", None)
            else:
                os.environ["TELEGRAM_ONBOARDING_MODE"] = prev_mode
            if prev_urls is None:
                os.environ.pop("TELEGRAM_ONBOARDING_SLIDE_URLS", None)
            else:
                os.environ["TELEGRAM_ONBOARDING_SLIDE_URLS"] = prev_urls

    def test_demo_calendar_markdown(self) -> None:
        body = onboarding.demo_calendar_markdown()
        self.assertIn("Пример ответа", body)
        self.assertIn("Синк с командой", body)
        self.assertIn("| 11:30-12:00 |", body)

    def test_demo_summary_markdown(self) -> None:
        body = onboarding.demo_summary_markdown()
        self.assertIn("Пример саммари", body)
        self.assertIn("тариф", body.lower())
        self.assertTrue("- [ ]" in body or "- [x]" in body)

    def test_onboarding_completed_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prev = os.environ.get("USER_PREFS_DIR")
            os.environ["USER_PREFS_DIR"] = td
            try:
                self.assertFalse(up.onboarding_completed(1))
                up.mark_onboarding_completed(1)
                self.assertTrue(up.onboarding_completed(1))
            finally:
                if prev is None:
                    os.environ.pop("USER_PREFS_DIR", None)
                else:
                    os.environ["USER_PREFS_DIR"] = prev

    def test_onboarding_slide_urls_from_env(self) -> None:
        prev = os.environ.get("TELEGRAM_ONBOARDING_SLIDE_URLS")
        os.environ["TELEGRAM_ONBOARDING_SLIDE_URLS"] = "https://a/1.jpg, https://b/2.jpg"
        try:
            self.assertEqual(
                onboarding.onboarding_slide_urls(),
                ["https://a/1.jpg", "https://b/2.jpg"],
            )
        finally:
            if prev is None:
                os.environ.pop("TELEGRAM_ONBOARDING_SLIDE_URLS", None)
            else:
                os.environ["TELEGRAM_ONBOARDING_SLIDE_URLS"] = prev


class OnboardingAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_start_onboarding_short_when_completed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ["USER_PREFS_DIR"] = td
            up.mark_onboarding_completed(42)

            msg = MagicMock()
            msg.reply_text = AsyncMock()
            update = MagicMock()
            update.message = msg

            await onboarding.send_start_onboarding(
                update,
                user_id=42,
                webapp_url="https://example.com/app",
            )
            msg.reply_text.assert_awaited_once()
            self.assertIn("Leo — встречи", msg.reply_text.await_args.args[0])

    async def test_send_start_onboarding_force_when_completed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ["USER_PREFS_DIR"] = td
            up.mark_onboarding_completed(42)

            msg = MagicMock()
            msg.reply_text = AsyncMock()
            update = MagicMock()
            update.message = msg

            with patch(
                "assistant.bot.onboarding.reply_formatted",
                new_callable=AsyncMock,
            ) as mock_reply:
                await onboarding.send_start_onboarding(
                    update,
                    user_id=42,
                    webapp_url="https://example.com/app",
                    force=True,
                )

            msg.reply_text.assert_not_awaited()
            self.assertEqual(mock_reply.await_count, 3)

    async def test_handle_onboarding_callback_examples(self) -> None:
        query = MagicMock()
        query.data = "ob:examples"
        query.message.chat_id = 100
        query.answer = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        update.effective_user = MagicMock(id=7)

        context = MagicMock()
        context.bot.send_message = AsyncMock()

        await onboarding.handle_onboarding_callback(update, context)

        query.answer.assert_awaited_once()
        context.bot.send_message.assert_awaited_once()
        self.assertIn("Создай Zoom", context.bot.send_message.await_args.kwargs["text"])

    async def test_handle_onboarding_callback_done(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ["USER_PREFS_DIR"] = td

            query = MagicMock()
            query.data = "ob:done"
            query.message.chat_id = 100
            query.answer = AsyncMock()

            update = MagicMock()
            update.callback_query = query
            update.effective_user = MagicMock(id=9)

            context = MagicMock()
            context.bot.send_message = AsyncMock()

            await onboarding.handle_onboarding_callback(update, context)

            self.assertTrue(up.onboarding_completed(9))
            context.bot.send_message.assert_awaited_once()
            self.assertIn("Готово", context.bot.send_message.await_args.kwargs["text"])
