from __future__ import annotations

from usage_server import _serialize_event_attendees


def test_includes_organizer_from_organizer_field() -> None:
    rows = _serialize_event_attendees(
        {
            "organizer": {
                "email": "artem.danilin.1999@gmail.com",
                "displayName": "Artem Danilin",
            },
            "attendees": [
                {
                    "email": "viewer@example.com",
                    "self": True,
                    "responseStatus": "accepted",
                }
            ],
        }
    )
    assert rows == [
        {
            "email": "artem.danilin.1999@gmail.com",
            "name": "Artem Danilin",
            "organizer": True,
        }
    ]


def test_includes_organizer_marked_in_attendees() -> None:
    rows = _serialize_event_attendees(
        {
            "attendees": [
                {
                    "email": "artem.danilin.1999@gmail.com",
                    "displayName": "Artem",
                    "organizer": True,
                },
                {"email": "me@example.com", "self": True},
            ]
        }
    )
    assert [r["email"] for r in rows] == ["artem.danilin.1999@gmail.com"]
    assert rows[0]["organizer"] is True


def test_skips_self_organizer_keeps_guests() -> None:
    rows = _serialize_event_attendees(
        {
            "organizer": {"email": "me@example.com", "self": True},
            "attendees": [
                {"email": "me@example.com", "self": True, "organizer": True},
                {"email": "guest@example.com", "displayName": "Guest"},
            ],
        }
    )
    assert rows == [{"email": "guest@example.com", "name": "Guest"}]


def test_dedupes_organizer_and_fills_name() -> None:
    rows = _serialize_event_attendees(
        {
            "organizer": {"email": "Org@Example.com"},
            "attendees": [
                {
                    "email": "org@example.com",
                    "displayName": "Org Name",
                    "organizer": True,
                },
                {"email": "other@example.com", "displayName": "Other"},
            ],
        }
    )
    assert rows[0]["email"] == "org@example.com"
    assert rows[0]["name"] == "Org Name"
    assert rows[0]["organizer"] is True
    assert rows[1] == {"email": "other@example.com", "name": "Other"}


def test_attaches_contact_telegram_user_id() -> None:
    rows = _serialize_event_attendees(
        {
            "organizer": {"email": "artem.danilin.1999@gmail.com"},
        },
        contacts_by_email={
            "artem.danilin.1999@gmail.com": {
                "email": "artem.danilin.1999@gmail.com",
                "name": "Артём",
                "telegram_user_id": 12345,
            }
        },
    )
    assert rows[0]["telegram_user_id"] == 12345
    assert rows[0]["name"] == "Артём"
