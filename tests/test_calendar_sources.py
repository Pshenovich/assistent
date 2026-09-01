from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from assistant.services import calendar_sources as cs


def _mock_calendar_list(*items):
    svc = MagicMock()
    svc.calendarList().list().execute.return_value = {"items": items}
    return svc


def test_get_active_calendar_ids_excludes_prefs():
    with patch.object(cs, "list_readable_calendars") as lst:
        lst.return_value = [
            {"id": "primary", "summary": "Main"},
            {"id": "bitrix@group.calendar.google.com", "summary": "Bitrix"},
        ]
        with patch.object(cs, "get_calendar_excluded_ids") as ex:
            ex.return_value = {"bitrix@group.calendar.google.com"}
            assert cs.get_active_calendar_ids(1) == ["primary"]


def test_dedupe_by_ical_uid_prefers_primary():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 6, 4, 9, 0, tzinfo=tz)
    end = start + timedelta(hours=1)
    a = {
        "calendar_id": "bitrix@group.calendar.google.com",
        "event_id": "a1",
        "summary": "Синк",
        "ical_uid": "uid-1",
        "start": start,
        "end": end,
        "raw": {},
    }
    b = {
        "calendar_id": "primary",
        "event_id": "b1",
        "summary": "Синк",
        "ical_uid": "uid-1",
        "start": start,
        "end": end,
        "raw": {"hangoutLink": "https://meet.google.com/x"},
    }
    out = cs.dedupe_events([a, b])
    assert len(out) == 1
    assert out[0]["calendar_id"] == "primary"


def test_set_calendar_excluded_filters_unknown_ids():
    with patch.object(cs, "list_readable_calendars") as lst:
        lst.return_value = [
            {"id": "cal-a", "summary": "A"},
            {"id": "cal-b", "summary": "B"},
        ]
        with patch.object(cs, "user_prefs") as prefs:
            cs.set_calendar_excluded(1, ["cal-b", "unknown-id", "cal-a"])
            prefs.set_calendar_excluded_ids.assert_called_once_with(1, ["cal-b", "cal-a"])


def test_list_events_in_window_merges_calendars():
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 6, 4, 8, 0, tzinfo=tz)
    end = now + timedelta(hours=2)
    ev_bitrix = {
        "id": "ev1",
        "summary": "Bitrix call",
        "iCalUID": "uid-b",
        "start": {"dateTime": "2026-06-04T09:00:00+03:00"},
        "end": {"dateTime": "2026-06-04T10:00:00+03:00"},
        "htmlLink": "https://calendar.google.com/ev1",
    }

    svc = MagicMock()

    def _list(**kwargs):
        res = MagicMock()
        if kwargs.get("calendarId") == "bitrix@group.calendar.google.com":
            res.execute.return_value = {"items": [ev_bitrix]}
        else:
            res.execute.return_value = {"items": []}
        return res

    svc.events().list.side_effect = _list

    with patch.object(cs, "_service", return_value=svc):
        with patch.object(cs, "get_active_calendar_ids", return_value=["primary", "bitrix@group.calendar.google.com"]):
            items = cs.list_events_in_window(1, now, end)
    assert len(items) == 1
    assert items[0]["summary"] == "Bitrix call"
    assert items[0]["calendar_id"] == "bitrix@group.calendar.google.com"
