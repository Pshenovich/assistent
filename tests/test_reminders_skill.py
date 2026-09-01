from assistant.skills.reminders import _resolve_reminder_task, reminder_actions_keyboard


def test_resolve_reminder_task_uses_reply_text():
    assert (
        _resolve_reminder_task(
            "напомни завтра в 10",
            {"task": "завтра в 10"},
            "купить молоко",
        )
        == "купить молоко"
    )


def test_resolve_reminder_task_without_reply_uses_parsed():
    assert (
        _resolve_reminder_task(
            "напомни в 18:00 позвонить",
            {"task": "позвонить"},
            "",
        )
        == "позвонить"
    )


def test_resolve_reminder_task_without_reply_falls_back_to_stripped():
    assert _resolve_reminder_task("напомни купить хлеб", None, "") == "купить хлеб"


def test_reminder_keyboard_buttons():
    kb = reminder_actions_keyboard("abc123")
    rows = kb.inline_keyboard
    assert rows[0][0].text == "Выполнено"
    assert rows[0][0].callback_data == "rem:done:abc123"
    assert rows[1][0].callback_data == "rem:snooze:15:abc123"
    assert rows[1][1].callback_data == "rem:snooze:30:abc123"
    assert rows[2][0].callback_data == "rem:snooze:60:abc123"
