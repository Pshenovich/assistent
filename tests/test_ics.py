from __future__ import annotations

from assistant.lib import ics

EV = {
    "ical_uid": "orbita-abc@orbita", "sequence": 2, "title": "Синк; по проекту, срочно",
    "start_utc": "2026-11-02T08:00:00+00:00", "end_utc": "2026-11-02T08:45:00+00:00",
    "description": "Повестка:\nпервое", "location": "Переговорка 1", "organizer_email": "vova@acme.io",
    "attendees": [{"email": "guest@partner.ru", "name": "Гость, Партнёр", "response": "needsAction"}],
}


def test_build_request():
    text = ics.build(EV, organizer_name="Вова", rsvp_url="https://x/rsvp/t")
    assert text.endswith("\r\n") and "METHOD:REQUEST" in text
    assert "DTSTART:20261102T080000Z" in text and "SEQUENCE:2" in text
    assert "SUMMARY:Синк\; по проекту\\, срочно" in text
    assert "ATTENDEE;CN=\"Гость, Партнёр\";ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:guest@partner.ru" in ics._unfold(text)
    assert all(len(ln.encode()) <= 75 for ln in text.split("\r\n"))
    assert "STATUS:CONFIRMED" in text


def test_build_cancel():
    text = ics.build(EV, method="CANCEL")
    assert "METHOD:CANCEL" in text and "STATUS:CANCELLED" in text


def test_parse_reply_gmail_style():
    reply = ("BEGIN:VCALENDAR\r\nMETHOD:REPLY\r\nBEGIN:VEVENT\r\nUID:orbita-abc@orbita\r\n"
             "ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=Guest\r\n"
             " Person:mailto:Guest@Partner.ru\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics.parse_reply(reply) == {"uid": "orbita-abc@orbita", "email": "guest@partner.ru", "response": "accepted"}
    assert ics.parse_reply(ics.build(EV)) is None


def test_build_series_with_skip_and_move():
    ev = {**EV, "recurrence": "RRULE:FREQ=WEEKLY;COUNT=4", "exdates": ["20261109T080000Z"],
          "overrides": {"20261116T080000Z": {"start_utc": "2026-11-16T11:00:00+00:00", "end_utc": "2026-11-16T11:45:00+00:00"}}}
    lines = ics._unfold(ics.build(ev))
    assert "RRULE:FREQ=WEEKLY;COUNT=4" in lines and "EXDATE:20261109T080000Z" in lines
    assert lines.count("BEGIN:VEVENT") == 2
    assert "RECURRENCE-ID:20261116T080000Z" in lines and "DTSTART:20261116T110000Z" in lines
