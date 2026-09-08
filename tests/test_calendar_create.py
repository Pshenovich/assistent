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


def test_create_event_sends_attendee_updates():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 9, 3, 10, 0, tzinfo=tz)
    end = datetime(2026, 9, 3, 11, 0, tzinfo=tz)
    svc = MagicMock()
    insert_req = MagicMock()
    insert_req.execute.return_value = {
        "id": "event-2",
        "htmlLink": "https://calendar.google.com/event",
        "summary": "Синк",
    }
    svc.events().insert.return_value = insert_req

    with patch.object(cal, "_service", return_value=svc), patch.object(
        cal, "_tz_name_for", return_value="Europe/Moscow"
    ), patch.object(cal, "_parse_dt", side_effect=[start, end]), patch.object(
        cal, "normalize_event_title", return_value="Синк"
    ), patch.object(
        cal, "_resolve_attendees", return_value=[{"email": "anna@x.com"}]
    ), patch.object(
        cal.cal_sources, "resolve_calendar_id", return_value="primary"
    ), patch.object(
        cal.cal_sources, "assert_calendar_writable"
    ):
        result = cal.create_event(
            42,
            {
                "title": "Синк",
                "start": "2026-09-03T10:00",
                "end": "2026-09-03T11:00",
                "attendees": ["anna@x.com"],
            },
        )

    kwargs = svc.events().insert.call_args.kwargs
    assert kwargs["sendUpdates"] == "all"
    assert kwargs["body"]["attendees"] == [{"email": "anna@x.com"}]
    assert result["attendee_emails"] == ["anna@x.com"]


def test_update_event_writes_description_and_new_attendees():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 9, 3, 10, 0, tzinfo=tz)
    end = datetime(2026, 9, 3, 11, 0, tzinfo=tz)
    svc = MagicMock()
    get_req = MagicMock()
    get_req.execute.return_value = {
        "attendees": [{"email": "old@x.com"}],
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    patch_req = MagicMock()
    patch_req.execute.return_value = {
        "htmlLink": "https://calendar.google.com/event",
        "summary": "Синк",
        "end": {"dateTime": end.isoformat()},
    }
    svc.events().get.return_value = get_req
    svc.events().patch.return_value = patch_req

    with patch.object(cal, "_service", return_value=svc), patch.object(
        cal, "_tz_name_for", return_value="Europe/Moscow"
    ), patch.object(cal, "_parse_dt", side_effect=[start, end]), patch.object(
        cal, "_resolve_attendees", return_value=[{"email": "new@x.com"}]
    ), patch.object(
        cal.cal_sources, "assert_calendar_writable"
    ), patch.object(
        cal, "_event_start_local", return_value=start
    ), patch.object(
        cal, "_tz_for", return_value=tz
    ):
        result = cal.update_event(
            42,
            "event-2",
            {
                "title": "Синк",
                "start": "2026-09-03T10:00",
                "end": "2026-09-03T11:00",
                "description": "Повестка",
                "attendees": ["new@x.com"],
            },
            calendar_id="primary",
        )

    kwargs = svc.events().patch.call_args.kwargs
    assert kwargs["body"]["description"] == "Повестка"
    assert kwargs["body"]["attendees"] == [{"email": "new@x.com"}]
    assert kwargs["sendUpdates"] == "all"
    assert result["attendee_emails"] == ["new@x.com"]
    assert result["previous_attendee_emails"] == ["old@x.com"]
