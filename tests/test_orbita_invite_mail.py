"""Письма-приглашения внешним гостям: кому, что и когда уходит."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from assistant.integrations import mail_smtp
from assistant.services import mail_source, meeting_invites, orbita_calendar, orbita_invite_mail
from assistant.stores import notes as notes_store
from assistant.stores import orbita_events as store

ACC = {"account_id": "a1", "email": "vova@acme.io", "send_enabled": True, "auth": "password"}


class InviteMailTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self.sent: list = []
        self.acc = dict(ACC)
        self._p = [
            mock.patch.object(meeting_invites, "telegram_id_for_attendee_email",
                              lambda o, em, **kw: {"anna@acme.io": 2}.get(em)),
            mock.patch.object(orbita_calendar, "organizer_email", lambda uid: "vova@acme.io"),
            mock.patch.object(orbita_invite_mail, "send_account", lambda uid: self.acc),
            mock.patch.object(mail_source, "credentials", lambda uid, acc: {"secret": "x"}),
            mock.patch.object(mail_smtp, "send", lambda acc, msg, **kw: self.sent.append(msg)),
        ]
        for p in self._p:
            p.start()
        self.start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)

    def tearDown(self) -> None:
        for p in self._p:
            p.stop()
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def _ev(self, res):
        return store.get(store.parse_id(res["event_id"]))

    def test_create_mails_only_external_guests(self) -> None:
        res = orbita_calendar.create(1, {"title": "Демо", "start": self.start.isoformat(),
                                         "attendees": ["anna@acme.io", "client@partner.ru"]})
        out = orbita_invite_mail.after_change(1, None, self._ev(res))
        self.assertEqual(out["sent"], ["client@partner.ru"])
        msg = self.sent[0]
        self.assertEqual(msg["To"], "client@partner.ru")
        self.assertIn("Приглашение на встречу: Демо", msg["Subject"])
        cal = next(p for p in msg.walk() if p.get_content_type() == "text/calendar")
        self.assertEqual(cal.get_param("method"), "REQUEST")
        from assistant.lib import ics

        body = "\n".join(ics._unfold(cal.get_content()))
        self.assertIn("ORGANIZER:mailto:vova@acme.io", body)  # без профиля — без «Участник»
        self.assertIn("mailto:client@partner.ru", body)
        self.assertIn("/rsvp/", msg.get_body(("plain",)).get_content())

    def test_update_notifies_on_move_and_cancels_removed(self) -> None:
        res = orbita_calendar.create(1, {"title": "Демо", "start": self.start.isoformat(),
                                         "attendees": ["client@partner.ru", "other@partner.ru"]})
        prev = self._ev(res)
        # правка без сути (только добавили гостя) — письмо только новому
        orbita_calendar.update(1, res["event_id"], {"attendees": ["client@partner.ru", "other@partner.ru", "new@x.ru"]})
        out = orbita_invite_mail.after_change(1, prev, self._ev(res))
        self.assertEqual(out["sent"], ["new@x.ru"])
        prev = self._ev(res)
        orbita_calendar.update(1, res["event_id"], {"start": (self.start + timedelta(hours=1)).isoformat(),
                                                    "attendees": ["client@partner.ru", "new@x.ru"]})
        self.sent.clear()
        out = orbita_invite_mail.after_change(1, prev, self._ev(res))
        self.assertEqual(sorted(out["sent"]), ["client@partner.ru", "new@x.ru"])
        self.assertEqual(out["cancelled"], ["other@partner.ru"])
        self.assertTrue(any("Изменена встреча" in m["Subject"] for m in self.sent))

    def test_cancel_and_no_mail_account(self) -> None:
        res = orbita_calendar.create(1, {"title": "Демо", "start": self.start.isoformat(), "attendees": ["c@p.ru"]})
        prev = self._ev(res)
        orbita_calendar.cancel(1, res["event_id"])
        out = orbita_invite_mail.after_change(1, prev, self._ev(res))
        self.assertEqual(out["sent"], ["c@p.ru"])
        self.assertIn("Отменена встреча", self.sent[-1]["Subject"])
        self.acc = None
        res2 = orbita_calendar.create(1, {"title": "Ещё", "start": self.start.isoformat(), "attendees": ["c@p.ru"]})
        out = orbita_invite_mail.after_change(1, None, self._ev(res2))
        self.assertEqual(out, {"sent": [], "failed": [], "skipped": ["c@p.ru"], "reason": "no_mail"})

    def test_guest_reply_from_mail_client(self) -> None:
        from email.message import EmailMessage

        from assistant.bot import access_gate

        res = orbita_calendar.create(1, {"title": "Демо", "start": self.start.isoformat(), "attendees": ["c@p.ru"]})
        ev = self._ev(res)
        self.assertTrue(store.owner_has_guests(1))
        reply = EmailMessage()
        reply["Subject"] = "Принято: Приглашение на встречу: Демо"
        reply.set_content("Принято")
        reply.add_alternative(
            "BEGIN:VCALENDAR\r\nMETHOD:REPLY\r\nBEGIN:VEVENT\r\nUID:" + ev["ical_uid"] +
            "\r\nATTENDEE;PARTSTAT=DECLINED:mailto:C@P.ru\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
            subtype="calendar", params={"method": "REPLY"})
        stranger = EmailMessage()
        stranger.add_alternative("BEGIN:VCALENDAR\r\nMETHOD:REPLY\r\nBEGIN:VEVENT\r\nUID:" + ev["ical_uid"] +
                                 "\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:evil@x.ru\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
                                 subtype="calendar")
        told: list = []
        with mock.patch.object(access_gate, "notify_user_sync", lambda uid, text, **kw: told.append(text)):
            self.assertEqual(orbita_invite_mail.apply_replies(1, [reply, stranger]), 1)
            self.assertEqual(orbita_invite_mail.apply_replies(2, [reply]), 0)  # чужая встреча — не трогаем
        self.assertEqual(self._ev(res)["attendees"][0]["response"], "declined")
        self.assertTrue(told and "не приду" in told[0])


if __name__ == "__main__":
    unittest.main()
