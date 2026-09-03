from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from assistant.services import calendar as cal


def test_create_event_uses_selected_calendar_id():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 9, 3, 10, 0, tzinfo=tz)
    end = datetime(2026, 9, 3, 11, 0, tzinfo=tz)
    svc = MagicMock()
    insert_req = MagicMock()
    insert_req.execute.return_value = {
        "id": "event-1",
        "htmlLink": "https://calendar.google.com/event",
        "summary": "Планерка",
    }
    svc.events().insert.return_value = insert_req

    with patch.object(cal, "_service", return_value=svc), patch.object(
        cal, "_tz_name_for", return_value="Europe/Moscow"
    ), patch.object(cal, "_parse_dt", side_effect=[start, end]), patch.object(
        cal, "normalize_event_title", return_value="Планерка"
    ), patch.object(
        cal, "_resolve_attendees", return_value=[]
    ), patch.object(
        cal.cal_sources, "resolve_calendar_id", return_value="team@example.com"
    ) as resolve, patch.object(
        cal.cal_sources, "assert_calendar_writable"
    ) as assert_writable:
        result = cal.create_event(
            42,
            {"title": "Планерка", "start": "2026-09-03T10:00", "end": "2026-09-03T11:00"},
            calendar_id="team@example.com",
        )

    resolve.assert_called_once_with(42, "team@example.com")
    assert_writable.assert_called_once_with(42, "team@example.com")
    svc.events().insert.assert_called_once()
    kwargs = svc.events().insert.call_args.kwargs
    assert kwargs["calendarId"] == "team@example.com"
    assert result["calendar_id"] == "team@example.com"
