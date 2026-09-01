import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from assistant.bot import meeting_reminder_job as mrj


def test_meeting_reminder_tick_skips_disabled_user():
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    due_event = {
        "calendar_id": "cal1",
        "event_id": "ev1",
        "start_iso": "2026-06-08T12:00:00+03:00",
    }

    with patch.object(mrj, "reminders_enabled", return_value=True), patch.object(
        mrj, "is_user_allowed", return_value=True
    ), patch.object(mrj.user_prefs, "meeting_reminders_enabled", return_value=False), patch.object(
        mrj.mr, "iter_calendar_user_ids", return_value=[123]
    ), patch.object(
        mrj.mr, "collect_due_reminders", return_value=[due_event]
    ), patch.object(
        mrj.mr, "format_reminder_message", return_value=("text", None)
    ), patch.object(mrj.sent_store, "mark_sent", MagicMock()) as mark_sent:
        asyncio.run(mrj.meeting_reminder_tick(context))

    context.bot.send_message.assert_not_called()
    mark_sent.assert_not_called()
