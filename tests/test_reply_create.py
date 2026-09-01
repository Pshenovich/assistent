from assistant.lib.message_context import add_reply_author_as_attendee
from assistant.services.calendar import needs_time_selection


def test_skip_self_as_reply_attendee():
    parsed: dict = {"attendee_names": []}
    author = {
        "telegram_user_id": 100,
        "first_name": "Bob",
        "last_name": "",
        "username": "bob",
    }
    add_reply_author_as_attendee(
        parsed, author, owner_user_id=100, owner_username="bob"
    )
    assert parsed.get("attendee_names") in (None, [])
    assert "_reply_author" not in parsed


def test_reply_time_skips_slot_picker():
    parsed = {"start": "2026-06-04T09:00:00", "slot_day": "2026-06-04"}
    assert not needs_time_selection(
        parsed,
        "поставь встречу",
        reply_context="давайте завтра в 17 встретимся и обсудим",
    )


def test_no_time_still_needs_picker():
    parsed = {"slot_day": "2026-06-05"}
    assert needs_time_selection(parsed, "встреча в пятницу", reply_context="")
