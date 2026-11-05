"""Знакомство в Telegram-боте: выбор сценария, разбор дня с превью, пересланное → саммари."""

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import assistant.bot.onboarding as onboarding
import assistant.bot.onboarding_day as day
import assistant.stores.user_prefs as up

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=TZ)  # четверг


class _PrefsDir(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("USER_PREFS_DIR")
        os.environ["USER_PREFS_DIR"] = self._td.name

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("USER_PREFS_DIR", None)
        else:
            os.environ["USER_PREFS_DIR"] = self._prev
        self._td.cleanup()


class BuildPlanTests(unittest.TestCase):
    def test_meeting_task_reminder(self) -> None:
        plan = day.build_plan(
            [
                {"kind": "meeting", "title": "Созвон с Аней", "date": "2026-11-06", "time": "11:00"},
                {"kind": "task", "title": "Отправить отчёт", "date": "2026-11-07"},
                {"kind": "reminder", "title": "Позвонить в банк", "date": "", "time": "19:00"},
            ],
            now=NOW,
            tz=TZ,
        )
        kinds = [(p["kind"], p["start"][:16]) for p in plan]
        self.assertIn(("meeting", "2026-11-06T11:00"), kinds)
        self.assertIn(("task", "2026-11-07T00:00"), kinds)
        self.assertIn(("reminder", "2026-11-05T19:00"), kinds)
        meeting = next(p for p in plan if p["kind"] == "meeting")
        self.assertTrue(meeting["end"].startswith("2026-11-06T12:00"))
        task = next(p for p in plan if p["kind"] == "task")
        self.assertTrue(task["all_day"])

    def test_meeting_without_time_becomes_task(self) -> None:
        plan = day.build_plan([{"kind": "meeting", "title": "Встреча с Петей", "date": "2026-11-06"}], now=NOW, tz=TZ)
        self.assertEqual(plan[0]["kind"], "task")

    def test_drops_bad_dates_kinds_duplicates_and_caps(self) -> None:
        raw = [
            {"kind": "question", "title": "Что такое Лео?"},
            {"kind": "task", "title": ""},
            {"kind": "task", "title": "Прошлое", "date": "2026-10-01"},
            {"kind": "task", "title": "Дубль", "date": "2026-11-06"},
            {"kind": "task", "title": "дубль", "date": "2026-11-06"},
        ] + [{"kind": "task", "title": f"Дело {i}"} for i in range(30)]
        plan = day.build_plan(raw, now=NOW, tz=TZ)
        titles = [p["title"] for p in plan]
        self.assertNotIn("Что такое Лео?", titles)
        self.assertEqual(sum(1 for t in titles if t.lower() == "дубль"), 1)
        # Дата из прошлого не принимается — дело ставится на сегодня, а не в октябрь.
        past = next(p for p in plan if p["title"] == "Прошлое")
        self.assertTrue(past["start"].startswith("2026-11-05"))
        self.assertLessEqual(len(plan), day.MAX_ITEMS)

    def test_time_already_passed_without_day_goes_to_tomorrow(self) -> None:
        plan = day.build_plan([{"kind": "meeting", "title": "Синк", "time": "09:00"}], now=NOW, tz=TZ)
        self.assertTrue(plan[0]["start"].startswith("2026-11-06T09:00"))

    def test_plan_html_and_counts(self) -> None:
        plan = day.build_plan(
            [
                {"kind": "meeting", "title": "Созвон <b>", "date": "2026-11-05", "time": "15:00"},
                {"kind": "task", "title": "Отчёт", "date": "2026-11-06"},
                {"kind": "task", "title": "Слайды", "date": "2026-11-06"},
            ],
            now=NOW,
            tz=TZ,
        )
        body = day.plan_html(plan, today=NOW.date())
        self.assertIn("<b>Сегодня</b>", body)
        self.assertIn("<b>Завтра</b>", body)
        self.assertIn("Созвон &lt;b&gt;", body)
        self.assertEqual(day.counts_label(plan), "1 встреча, 2 задачи")


class CommitPlanTests(unittest.TestCase):
    def test_creates_each_kind(self) -> None:
        plan = day.build_plan(
            [
                {"kind": "meeting", "title": "Созвон", "date": "2026-11-06", "time": "11:00"},
                {"kind": "task", "title": "Отчёт", "date": "2026-11-06"},
                {"kind": "reminder", "title": "Банк", "time": "19:00"},
            ],
            now=NOW,
            tz=TZ,
        )
        with patch("assistant.services.calendar.create_event") as ev, patch(
            "assistant.stores.calendar_tasks.create_task"
        ) as tk, patch("assistant.stores.reminders_store.add_reminder", return_value="r1") as rm:
            done, failed = day.commit_plan(5, 5, plan, tz=TZ)
        self.assertEqual(len(done), 3)
        self.assertEqual(failed, [])
        ev.assert_called_once()
        tk.assert_called_once()
        rm.assert_called_once()
        self.assertEqual(next(d for d in done if d["kind"] == "reminder")["reminder_id"], "r1")

    def test_failure_is_reported(self) -> None:
        plan = day.build_plan([{"kind": "task", "title": "Отчёт"}], now=NOW, tz=TZ)
        with patch("assistant.stores.calendar_tasks.create_task", side_effect=RuntimeError("db")):
            done, failed = day.commit_plan(5, 5, plan, tz=TZ)
        self.assertEqual(done, [])
        self.assertEqual(len(failed), 1)


class PrefsTests(_PrefsDir):
    def test_pick_marks_completed_and_ages(self) -> None:
        up.set_onboarding_pick(1, "talk")
        self.assertTrue(up.onboarding_completed(1))
        self.assertEqual(up.onboarding_pick(1, max_age_sec=60), "talk")
        prefs = up.load_prefs(1)
        prefs["onboarding_pick_at"] = 0
        up.save_prefs(1, prefs)
        self.assertEqual(up.onboarding_pick(1, max_age_sec=60), "")

    def test_followup_claimed_once(self) -> None:
        self.assertTrue(up.claim_onboarding_followup(2))
        self.assertFalse(up.claim_onboarding_followup(2))

    def test_wants_summary_only_for_talk_before_first_value(self) -> None:
        up.set_onboarding_pick(3, "talk")
        self.assertTrue(onboarding.wants_summary(3))
        up.claim_onboarding_followup(3)
        self.assertFalse(onboarding.wants_summary(3))
        up.set_onboarding_pick(4, "day")
        self.assertFalse(onboarding.wants_summary(4))


def _callback(data: str, uid: int = 7):
    query = MagicMock()
    query.data = data
    query.message.chat_id = uid
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=uid, username="u")
    context = MagicMock()
    context.user_data = {}
    context.bot.send_message = AsyncMock()
    context.bot.username = "leo_test_bot"
    return update, context, query


class StartTests(_PrefsDir, unittest.IsolatedAsyncioTestCase):
    async def test_new_user_gets_three_picks_and_marked_fresh(self) -> None:
        msg = MagicMock()
        msg.chat_id = 42
        bot = MagicMock()
        bot.send_message = AsyncMock()
        msg.get_bot.return_value = bot
        update = MagicMock()
        update.message = msg
        with patch("assistant.onboarding.backfill.has_activity", return_value=False), patch(
            "assistant.onboarding.state.has_state", return_value=False
        ):
            await onboarding.send_start_onboarding(update, user_id=42, webapp_url="https://e.com/app")
        kw = bot.send_message.await_args.kwargs
        self.assertIn("Привет, я Лео", kw["text"])
        datas = [b.callback_data for row in kw["reply_markup"].inline_keyboard for b in row if b.callback_data]
        self.assertEqual(datas, ["ob:pick:day", "ob:pick:talk", "ob:pick:chats"])
        self.assertTrue(up.onboarding_fresh(42))

    async def test_existing_user_not_marked_fresh_returning_text(self) -> None:
        up.mark_onboarding_completed(43)
        msg = MagicMock()
        msg.chat_id = 43
        bot = MagicMock()
        bot.send_message = AsyncMock()
        msg.get_bot.return_value = bot
        update = MagicMock()
        update.message = msg
        await onboarding.send_start_onboarding(update, user_id=43, webapp_url="https://e.com/app")
        self.assertIn("С чего продолжим", bot.send_message.await_args.kwargs["text"])
        self.assertFalse(up.onboarding_fresh(43))

    async def test_pick_day_arms_capture(self) -> None:
        update, context, _ = _callback("ob:pick:day")
        await onboarding.handle_onboarding_callback(update, context)
        self.assertTrue(onboarding.capture_active(context))
        self.assertEqual(up.onboarding_pick(7), "day")
        self.assertIn("Сам ничего не создам", context.bot.send_message.await_args.kwargs["text"])

    async def test_pick_chats_has_startgroup_link(self) -> None:
        update, context, _ = _callback("ob:pick:chats")
        await onboarding.handle_onboarding_callback(update, context)
        kb = context.bot.send_message.await_args.kwargs["reply_markup"]
        self.assertEqual(kb.inline_keyboard[0][0].url, "https://t.me/leo_test_bot?startgroup=orbita")

    async def test_capture_shows_preview_and_creates_nothing(self) -> None:
        context = MagicMock()
        context.user_data = {}
        onboarding.arm_capture(context)
        status = MagicMock()
        status.edit_text = AsyncMock()
        msg = MagicMock()
        msg.reply_text = AsyncMock(return_value=status)
        update = MagicMock()
        update.message = msg
        update.effective_user = MagicMock(id=8)
        update.effective_chat = MagicMock(type="private")
        raw = [{"kind": "meeting", "title": "Каждый день Zoom", "date": "2026-11-06", "time": "10:00"}]
        with patch("assistant.nlu.llm.parse_day_plan", return_value=raw), patch(
            "assistant.services.calendar._tz_for", return_value=TZ
        ), patch("assistant.services.calendar.create_event") as ev:
            handled = await onboarding.try_consume_capture(update, context, "завтра в 10 зум")
        self.assertTrue(handled)
        ev.assert_not_called()
        self.assertIn("Вот что я понял", status.edit_text.await_args.args[0])
        self.assertIn(onboarding._PLAN_KEY, context.user_data)
        self.assertFalse(onboarding.capture_active(context))

    async def test_capture_inactive_passes_through(self) -> None:
        context = MagicMock()
        context.user_data = {}
        update = MagicMock()
        update.effective_chat = MagicMock(type="private")
        self.assertFalse(await onboarding.try_consume_capture(update, context, "привет"))

    async def test_add_all_commits_and_sends_followup_once(self) -> None:
        up.set_onboarding_pick(7, "day")
        update, context, query = _callback("ob:day:add")
        plan = day.build_plan([{"kind": "task", "title": "Отчёт", "date": "2026-11-06"}], now=NOW, tz=TZ)
        context.user_data[onboarding._PLAN_KEY] = plan
        with patch("assistant.bot.onboarding_day.commit_plan", return_value=(plan, [])) as commit, patch(
            "assistant.services.calendar._tz_for", return_value=TZ
        ), patch("assistant.services.calendar.has_google", return_value=False), patch(
            "assistant.bot.onboarding.asyncio.sleep", new_callable=AsyncMock
        ):
            await onboarding.handle_onboarding_callback(update, context)
        commit.assert_called_once()
        self.assertIn("Готово: 1 задача", query.edit_message_text.await_args.args[0])
        self.assertIn("/calendar_auth", context.bot.send_message.await_args.kwargs["text"])
        self.assertTrue(up.load_prefs(7).get("onboarding_followup_at"))

    async def test_add_all_without_plan_is_stale(self) -> None:
        update, context, query = _callback("ob:day:add")
        await onboarding.handle_onboarding_callback(update, context)
        self.assertIn("неактуален", query.edit_message_text.await_args.args[0])

    async def test_group_add_enables_digest_only_for_chats_pick(self) -> None:
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.username = "leo_test_bot"
        with patch("assistant.stores.chat_digest.member_chat_ids", return_value={-100}):
            await onboarding.on_bot_added_to_group(bot, chat_id=-100, chat_title="Команда", actor_id=9)
            self.assertFalse(up.digest_enabled(9))
            bot.send_message.assert_not_awaited()

            up.set_onboarding_pick(9, "chats")
            with patch("assistant.bot.onboarding.asyncio.sleep", new_callable=AsyncMock):
                await onboarding.on_bot_added_to_group(bot, chat_id=-100, chat_title="Команда", actor_id=9)
            self.assertTrue(up.digest_enabled(9))
            self.assertEqual(up.digest_chat_ids(9), [-100])
        targets = [c.kwargs["chat_id"] for c in bot.send_message.await_args_list]
        self.assertEqual(targets, [-100, 9, 9])  # привет в группе, личка, «что дальше»


class ForwardedTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwarded_voice_goes_to_summary_not_route(self) -> None:
        from assistant.skills import transcribe

        msg = MagicMock()
        msg.voice = MagicMock()
        msg.forward_origin = object()
        update = MagicMock()
        update.message = msg
        update.effective_chat = MagicMock(type="private")
        context = MagicMock()
        context.user_data = {}
        with patch.object(transcribe, "should_process_message", return_value=True), patch(
            "assistant.bot.access_gate.ensure_access", new_callable=AsyncMock, return_value=True
        ), patch("assistant.lib.voice_once.claim_voice_once", new_callable=AsyncMock, return_value=True), patch(
            "assistant.skills.summary.run_explicit_summary", new_callable=AsyncMock
        ) as summ, patch.object(transcribe, "_transcribe_and_route", new_callable=AsyncMock) as route:
            await transcribe.handle_voice(update, context)
        summ.assert_awaited_once()
        route.assert_not_awaited()

    async def test_forwarded_voice_in_group_ignored(self) -> None:
        from assistant.skills import transcribe

        msg = MagicMock()
        msg.voice = MagicMock()
        msg.forward_origin = object()
        update = MagicMock()
        update.message = msg
        update.effective_chat = MagicMock(type="supergroup")
        with patch.object(transcribe, "should_process_message", return_value=True), patch(
            "assistant.bot.access_gate.ensure_access", new_callable=AsyncMock, return_value=True
        ), patch("assistant.skills.summary.run_explicit_summary", new_callable=AsyncMock) as summ:
            await transcribe.handle_voice(update, MagicMock())
        summ.assert_not_awaited()


class HelpTextTests(unittest.TestCase):
    def test_help_uses_cyrillic_names_and_documents(self) -> None:
        self.assertIn("Лео", onboarding.HELP_HTML)
        self.assertIn("Документы", onboarding.HELP_HTML)
        self.assertNotIn("Leo", onboarding.HELP_HTML)
        self.assertIn("/calendar_auth", onboarding.HELP_HTML)


if __name__ == "__main__":
    unittest.main()
