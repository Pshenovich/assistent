"""Кого звать после правки встречи: только новых участников и только на будущую встречу."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assistant.services import calendar as cal_svc
from assistant.services.meeting_invites import added_invitees


def _future():
    s = datetime.now(timezone.utc) + timedelta(days=1)
    return s, s + timedelta(hours=1)


def test_resave_with_organizer_in_list_invites_nobody():
    # Участница сохранила форму целиком (ради цвета): в списке есть организатор, которого
    # Google не отдаёт среди гостей. Раньше он считался «новым» и получал приглашение.
    st, en = _future()
    ev = {
        "start": st, "end": en,
        "attendee_emails": ["vova@x.ru", "vinse@x.ru", "anya@x.ru"],
        "previous_attendee_emails": ["anya@x.ru"],
        "previous_all_emails": ["vova@x.ru", "vinse@x.ru", "anya@x.ru"],
    }
    assert added_invitees(ev) == []


def test_new_guest_is_invited():
    st, en = _future()
    ev = {
        "start": st, "end": en,
        "attendee_emails": ["vova@x.ru", "new@x.ru"],
        "previous_attendee_emails": [],
        "previous_all_emails": ["vova@x.ru"],
    }
    assert added_invitees(ev) == ["new@x.ru"]


def test_past_meeting_invites_nobody():
    st = datetime.now(timezone.utc) - timedelta(days=3)
    ev = {"start": st, "end": st + timedelta(hours=1), "attendee_emails": ["new@x.ru"], "previous_all_emails": []}
    assert added_invitees(ev) == []


def test_all_attendee_emails_include_organizer_and_self():
    ev = {
        "organizer": {"email": "Vova@x.ru"},
        "attendees": [{"email": "vinse@x.ru", "self": True}, {"email": "anya@x.ru"}, {"email": "vova@x.ru", "organizer": True}],
    }
    assert cal_svc._all_attendee_emails(ev) == ["vova@x.ru", "vinse@x.ru", "anya@x.ru"]
