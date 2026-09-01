from assistant.lib.calendar_attendees import infer_event_title_from_text
from assistant.services.calendar import (
    ensure_event_title_from_request,
    needs_time_selection,
)


def test_infer_event_title_meeting_with_name():
    text = "поставить в календарь Встреча с Мишей в понедельник в 18:00"
    assert infer_event_title_from_text(text) == "Встреча с Мишей"


def test_skip_attendees_keeps_time_from_original_request():
    parsed = {
        "start": "2026-07-07T18:00:00",
        "slot_day": "2026-07-07",
        "title": "",
        "attendee_names": ["Миша"],
    }
    request = "поставить в календарь Встреча с Мишей в понедельник в 18:00"
    assert not needs_time_selection(parsed, request)


def test_skip_attendees_empty_text_still_needs_time():
    parsed = {"slot_day": "2026-07-07", "title": ""}
    assert needs_time_selection(parsed, "")


def test_ensure_event_title_from_request():
    parsed = {"title": "встреча", "attendee_names": ["Миша"]}
    request = "поставить в календарь Встреча с Мишей в понедельник в 18:00"
    ensure_event_title_from_request(parsed, request)
    assert parsed["title"] == "Встреча с Мишей"
