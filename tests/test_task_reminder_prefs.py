import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from assistant.bot import task_reminder_job as trj


def test_task_reminder_tick_skips_disabled_user():
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    due_task = {
        "id": "t1",
        "start_at": "2026-10-07T12:00:00+03:00",
    }

    with patch.object(trj, "reminders_enabled", return_value=True), patch.object(
        trj, "is_user_allowed", return_value=True
    ), patch.object(trj.user_prefs, "task_reminders_enabled", return_value=False), patch.object(
        trj.tr, "iter_task_user_ids", return_value=[123]
    ), patch.object(
        trj.tr, "collect_due_reminders", return_value=[due_task]
    ), patch.object(
        trj.tr, "format_reminder_message", return_value="text"
    ), patch.object(trj.sent_store, "mark_sent", MagicMock()) as mark_sent:
        asyncio.run(trj.task_reminder_tick(context))

    context.bot.send_message.assert_not_called()
    mark_sent.assert_not_called()
