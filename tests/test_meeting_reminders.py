from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from assistant.lib.meeting_links import (
    extract_online_meeting_url,
    extract_online_meeting_urls,
    online_link_label,
)
from assistant.services.meeting_reminders import format_reminder_message


def test_extract_zoom_from_description():
    ev = {
        "description": "Обсудим план\n\nZoom: https://zoom.us/j/123456789",
    }
    url = extract_online_meeting_url(ev)
    assert url and "zoom.us" in url


def test_extract_hangout_link():
    ev = {"hangoutLink": "https://meet.google.com/abc-defg-hij"}
    assert "meet.google.com" in (extract_online_meeting_url(ev) or "")


def test_format_reminder_with_zoom():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 6, 4, 17, 0, tzinfo=tz)
    end = start + timedelta(hours=1)
    ev = {
        "summary": "Синк",
        "html_link": "https://calendar.google.com/event?eid=1",
        "start": start,
        "end": end,
        "raw": {"description": "https://zoom.us/j/999"},
    }
    text, kb = format_reminder_message(ev, user_id=1, minutes_before=15)
    assert "Через 15 минут" in text
    assert "Синк" in text
    assert "17:00" in text
    assert "zoom.us" in text
    assert kb is not None
    assert kb.inline_keyboard[0][0].text == "Подключиться"


def test_online_link_label():
    assert online_link_label("https://zoom.us/j/x") == "Zoom"


def test_extract_multiple_online_urls():
    ev = {
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
        "description": "Дубль\nhttps://zoom.us/j/111\nhttps://teams.microsoft.com/l/meetup/xyz",
    }
    urls = extract_online_meeting_urls(ev)
    assert len(urls) == 3
    assert any("meet.google.com" in u for u in urls)
    assert any("zoom.us" in u for u in urls)
    assert any("teams.microsoft.com" in u for u in urls)


def test_format_reminder_multiple_meet_buttons():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 6, 4, 17, 0, tzinfo=tz)
    end = start + timedelta(hours=1)
    ev = {
        "summary": "Синк",
        "html_link": "https://calendar.google.com/event?eid=1",
        "start": start,
        "end": end,
        "raw": {
            "description": "https://zoom.us/j/999\nhttps://meet.google.com/abc-defg-hij",
        },
    }
    text, kb = format_reminder_message(ev, user_id=1, minutes_before=15)
    assert "Ссылки на встречу" in text
    assert "zoom.us" in text
    assert "meet.google.com" in text
    assert kb is not None
    assert len(kb.inline_keyboard) == 2
    assert kb.inline_keyboard[0][0].text == "Zoom"
    assert kb.inline_keyboard[1][0].text == "Google Meet"
