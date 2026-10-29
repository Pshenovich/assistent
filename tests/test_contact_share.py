"""«Поделиться контактом»: кому можно, запись получателю, сообщение от бота."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from assistant.bot import access_gate
from assistant.services import contact_share
from assistant.stores import contacts_store
from assistant.stores import notes as notes_store


class ContactShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["CONTACTS_USER_DIR"] = os.path.join(self._tmp.name, "contacts_user")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmp.name, "reg.json")
        notes_store._CONN = None  # type: ignore[attr-defined]
        contacts_store._CONTACTS_CACHE_BY_PATH.clear()
        contacts_store.save_contacts([
            {"name": "Ольга", "email": "olga@acme.io"},  # делимся ею
            {"name": "Анна", "email": "anna@acme.io", "telegram_user_id": 2},  # в Орбите
            {"name": "Гость", "email": "guest@mail.ru"},  # не в Орбите
        ], telegram_user_id=1)
        self.sent: list[tuple[int, str]] = []
        self._p = mock.patch.object(access_gate, "notify_user_sync", lambda uid, text, **kw: self.sent.append((uid, text)) or True)
        self._p.start()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_targets(self) -> None:
        t = contact_share.targets(1, "olga@acme.io")
        self.assertEqual([(x["name"], x["can_receive"]) for x in t], [("Анна", True), ("Гость", False)])
        with self.assertRaises(ValueError):
            contact_share.targets(1, "nobody@x.io")

    def test_share_adds_and_notifies(self) -> None:
        res = contact_share.share(1, "Вова", "olga@acme.io", ["anna@acme.io", "guest@mail.ru"])
        self.assertEqual([r["status"] for r in res], ["added", "not_in_orbita"])
        book = contacts_store.load_contacts(telegram_user_id=2)
        self.assertEqual([c["email"] for c in book], ["olga@acme.io"])
        self.assertEqual(self.sent[0][0], 2)
        self.assertIn("Вова поделился контактом", self.sent[0][1])
        again = contact_share.share(1, "Вова", "olga@acme.io", ["anna@acme.io"])
        self.assertEqual(again[0]["status"], "existing")
        self.assertEqual(len(contacts_store.load_contacts(telegram_user_id=2)), 1)


if __name__ == "__main__":
    unittest.main()
