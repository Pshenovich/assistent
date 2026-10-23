"""Повтор встречи из окна встречи: очистка правила, правка серии, своя ссылка."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from assistant.lib import recurrence as recur
from assistant.lib.meeting_links import extract_online_meeting_url, extract_online_meeting_urls
from assistant.services import calendar as cal


def test_sanitize_accepts_flexible_rules():
    assert recur.sanitize_client_recurrence(["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=10"]) == [
        "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=10"
    ]
    assert recur.sanitize_client_recurrence(["freq=monthly;byday=-1fr;until=20270123T205959Z"]) == [
        "RRULE:FREQ=MONTHLY;BYDAY=-1FR;UNTIL=20270123T205959Z"
    ]
    assert recur.sanitize_client_recurrence([]) == []


@pytest.mark.parametrize(
    "bad",
    [
        "RRULE:FREQ=SECONDLY",
        "RRULE:FREQ=DAILY;INTERVAL=500",
        "RRULE:FREQ=DAILY;COUNT=0",
        "RRULE:FREQ=DAILY;UNTIL=tomorrow",
        "EXDATE:20261023T000000Z",
        "RRULE:FREQ=DAILY;X-EVIL=1",
    ],
)
def test_sanitize_rejects_bad_rules(bad):
    with pytest.raises(ValueError):
        recur.sanitize_client_recurrence([bad])


def test_replace_rrule_keeps_exdates():
    lines = ["RRULE:FREQ=WEEKLY;BYDAY=FR", "EXDATE;TZID=Europe/Moscow:20261030T100000"]
    assert recur.replace_rrule(lines, ["RRULE:FREQ=DAILY"]) == [
        "RRULE:FREQ=DAILY",
        "EXDATE;TZID=Europe/Moscow:20261030T100000",
    ]


def test_end_series_at_replaces_count_with_until():
    start = datetime(2026, 10, 23, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    out = recur.end_series_at(["RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=20"], start)
    assert out == ["RRULE:FREQ=WEEKLY;BYDAY=FR;UNTIL=20261023T090000Z"]


def _svc_with(ev, master=None):
    svc = MagicMock()
    got = {"ev": ev, "master": master}

    def _get(calendarId, eventId):
        req = MagicMock()
        req.execute.return_value = got["master"] if master and eventId == master["id"] else got["ev"]
        return req

    svc.events().get.side_effect = _get
    svc.events().patch.return_value.execute.return_value = ev
    return svc


def test_update_single_event_sets_recurrence():
    ev = {"id": "e1", "start": {"dateTime": "2026-10-23T12:00:00+03:00"}, "end": {"dateTime": "2026-10-23T13:00:00+03:00"}}
    svc = _svc_with(ev)
    with patch.object(cal, "_service", return_value=svc), patch.object(
        cal.cal_sources, "assert_calendar_writable"
    ), patch.object(cal, "_resolve_attendees", return_value=[]), patch.object(
        cal, "_tz_for", return_value=ZoneInfo("Europe/Moscow")
    ):
        cal.update_event(1, "e1", {"recurrence": ["RRULE:FREQ=DAILY;COUNT=5"]})
    body = svc.events().patch.call_args.kwargs["body"]
    assert svc.events().patch.call_args.kwargs["eventId"] == "e1"
    assert body["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=5"]


def test_update_series_instance_patches_master_rule():
    inst = {
        "id": "m1_20261023T090000Z",
        "recurringEventId": "m1",
        "start": {"dateTime": "2026-10-23T12:00:00+03:00"},
        "end": {"dateTime": "2026-10-23T13:00:00+03:00"},
    }
    master = {"id": "m1", "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=FR", "EXDATE:20261030T090000Z"]}
    svc = _svc_with(inst, master)
    with patch.object(cal, "_service", return_value=svc), patch.object(
        cal.cal_sources, "assert_calendar_writable"
    ), patch.object(cal, "_resolve_attendees", return_value=[]), patch.object(
        cal, "_tz_for", return_value=ZoneInfo("Europe/Moscow")
    ):
        cal.update_event(1, inst["id"], {"recurrence": ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=FR"]})
    calls = svc.events().patch.call_args_list
    assert [c.kwargs["eventId"] for c in calls] == ["m1"]
    assert calls[0].kwargs["body"] == {
        "recurrence": ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", "EXDATE:20261030T090000Z"]
    }


def test_update_series_instance_no_repeat_ends_series_here():
    inst = {
        "id": "m1_20261023T090000Z",
        "recurringEventId": "m1",
        "start": {"dateTime": "2026-10-23T12:00:00+03:00"},
    }
    master = {"id": "m1", "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=FR"]}
    svc = _svc_with(inst, master)
    with patch.object(cal, "_service", return_value=svc), patch.object(
        cal.cal_sources, "assert_calendar_writable"
    ), patch.object(cal, "_resolve_attendees", return_value=[]), patch.object(
        cal, "_tz_for", return_value=ZoneInfo("Europe/Moscow")
    ):
        cal.update_event(1, inst["id"], {"title": "Синк", "recurrence": []})
    calls = svc.events().patch.call_args_list
    assert [c.kwargs["eventId"] for c in calls] == [inst["id"], "m1"]
    assert calls[1].kwargs["body"] == {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=FR;UNTIL=20261023T090000Z"]}


def test_custom_link_in_location_is_online_link():
    ev = {"location": "https://my.mts-link.ru/j/123/456"}
    assert extract_online_meeting_url(ev) == "https://my.mts-link.ru/j/123/456"
    ev = {"location": "https://corp.example.com/room/42"}
    assert extract_online_meeting_urls(ev) == ["https://corp.example.com/room/42"]
    # Обычный адрес со ссылкой на карту внутри — не звонок.
    assert extract_online_meeting_urls({"location": "Офис, https://maps.example.com/x"}) == []
