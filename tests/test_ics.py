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
