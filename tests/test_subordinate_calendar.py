"""«Календарь подчинённого»: кто кого видит, закрытые события — «Занят», только просмотр."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from assistant.services import subordinate_calendar as subcal
from assistant.stores import calendar_mirror as cm
from assistant.stores import notes as notes_store
from assistant.stores import org_groups, orgs_store

OWNER, ADMIN, LEAD, BACK, FRONT, MANAGER, GUEST = 1, 2, 3, 4, 5, 6, 7
TZ = ZoneInfo("Europe/Moscow")
MON = datetime(2026, 11, 9, tzinfo=TZ)


def gev(eid, hour, mins=60, **raw):
    st = MON.replace(hour=hour)
    body = {"id": eid, "summary": eid, "description": f"про {eid}", "location": "Офис",
            "start": {"dateTime": st.isoformat()}, "end": {"dateTime": (st + timedelta(minutes=mins)).isoformat()},
            "attendees": [{"email": "me@x.ru", "self": True, "responseStatus": "accepted"}, {"email": "a@x.ru"}]}
    body.update(raw)
    return {"calendar_id": "primary", "event_id": eid, "start": st, "end": st + timedelta(minutes=mins), "raw": body}


class SubordinateCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmp.name, "reg.json")
        os.environ["CALENDAR_MIRROR_DB_PATH"] = os.path.join(self._tmp.name, "mirror.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        cm._CONN = None  # type: ignore[attr-defined]
        self._patches = [
            mock.patch.object(orgs_store, "verified_emails", lambda uid: [f"u{int(uid)}@obuchat.me"]),
            mock.patch.object(orgs_store, "_username", lambda uid: ""),
            mock.patch.object(orgs_store, "_resolve_key", lambda actor, key: int(key.split("@")[0][1:])),
        ]
        for p in self._patches:
            p.start()
        self.org = orgs_store.create_org(OWNER, "Обучат", domain="obuchat.me")["id"]
        for uid in (ADMIN, LEAD, BACK, FRONT, MANAGER, GUEST):
            orgs_store.add_member(OWNER, self.org, f"u{uid}@obuchat.me", role="admin" if uid == ADMIN else "member")
        guest_row = next(m for m in orgs_store.list_members(self.org) if m["user_id"] == str(GUEST))
        orgs_store.set_guest(ADMIN, self.org, guest_row["id"], True)
        okk = org_groups.create(ADMIN, self.org, "ОКК")["id"]
        org_groups.set_lead(OWNER, self.org, okk, LEAD)
        back = org_groups.create(LEAD, self.org, "ОКК · бэкенд", parent_id=okk)["id"]
        org_groups.add_member(LEAD, self.org, back, BACK)
        org_groups.add_member(LEAD, self.org, okk, MANAGER)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]
        cm._CONN = None  # type: ignore[attr-defined]

    def test_who_sees_whom(self) -> None:
        self.assertEqual(set(subcal.visible_ids(LEAD)), {str(BACK), str(MANAGER)})
        self.assertTrue(subcal.can_view(LEAD, BACK))
        self.assertFalse(subcal.can_view(LEAD, FRONT))  # не в его отделе
        self.assertFalse(subcal.can_view(LEAD, LEAD))  # себя — обычным календарём
        admin_sees = set(subcal.visible_ids(ADMIN))
        self.assertIn(str(FRONT), admin_sees)
        self.assertNotIn(str(GUEST), admin_sees)
        self.assertEqual(subcal.visible_ids(BACK), {})  # обычный сотрудник — без подчинённых

    def test_mirror_keeps_visibility_and_private_is_busy(self) -> None:
        cm.record_window(BACK, [gev("open", 10), gev("secret", 12, visibility="private")],
                         time_min=MON, time_max=MON + timedelta(days=1), calendar_ids=["primary"])
        rows = {r["event_id"]: r for r in subcal.mirror_rows(BACK, MON, MON + timedelta(days=1))}
        self.assertEqual(rows["secret"]["visibility"], "private")
        busy = subcal.gcal_from_mirror(rows["secret"], TZ)
        self.assertEqual(busy["summary"], "Занят")
        for k in ("description", "location", "attendees", "htmlLink"):
            self.assertNotIn(k, busy)
        open_ev = subcal.gcal_from_mirror(rows["open"], TZ)
        self.assertEqual(open_ev["summary"], "open")
        self.assertEqual(open_ev["description"], "про open")
        self.assertFalse(any(a.get("self") for a in open_ev["attendees"]))

    def test_declined_is_hidden(self) -> None:
        declined = gev("no", 15)
        declined["raw"]["attendees"][0]["responseStatus"] = "declined"
        cm.record_window(BACK, [declined], time_min=MON, time_max=MON + timedelta(days=1), calendar_ids=["primary"])
        self.assertEqual(subcal.mirror_rows(BACK, MON, MON + timedelta(days=1)), [])

    def test_range_payload_read_only_and_access(self) -> None:
        import usage_server

        cm.record_window(BACK, [gev("open", 10), gev("secret", 12, visibility="private")],
                         time_min=MON, time_max=MON + timedelta(days=1), calendar_ids=["primary"])
        with mock.patch("assistant.services.calendar._tz_for", lambda uid: TZ), \
                mock.patch.object(subcal, "task_events", lambda *a, **k: []), \
                mock.patch("assistant.services.orbita_calendar.events_for", lambda *a, **k: []):
            with self.assertRaises(HTTPException) as err:
                usage_server._subordinate_calendar_payload(BACK, str(FRONT), "2026-11-09", "2026-11-09")
            self.assertEqual(err.exception.status_code, 400)
            data = usage_server._subordinate_calendar_payload(LEAD, str(BACK), "2026-11-09", "2026-11-09")
        self.assertTrue(data["read_only"])
        evs = {e["id"]: e for e in data["events"]}
        self.assertTrue(all(e["read_only"] for e in evs.values()))
        self.assertEqual(evs["secret"]["summary"], "Занят")
        self.assertTrue(evs["secret"]["private_busy"])
        self.assertEqual(evs["open"]["summary"], "open")
        self.assertTrue(data["google_connected"])


if __name__ == "__main__":
    unittest.main()
