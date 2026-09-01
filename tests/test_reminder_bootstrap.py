from datetime import datetime
from zoneinfo import ZoneInfo

from assistant.stores import reminders_store


def test_parse_when_iso_moscow_offset():
    tz = ZoneInfo("Europe/Moscow")
    when = reminders_store.parse_when_iso("2026-06-08T12:00:00+03:00", tz=tz)
    assert when is not None
    assert when.hour == 12
    assert when.utcoffset().total_seconds() == 3 * 3600


def test_parse_when_iso_zulu():
    tz = ZoneInfo("Europe/Moscow")
    when = reminders_store.parse_when_iso("2026-05-13T20:33:00.000Z", tz=tz)
    assert when is not None
    assert when.tzinfo is not None


def test_parse_when_iso_naive_uses_calendar_tz():
    when = reminders_store.parse_when_iso("2026-06-08T12:00:00")
    assert when is not None
    assert when.tzinfo is not None
