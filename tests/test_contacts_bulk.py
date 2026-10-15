"""Контакты по нику без почты, добавление списком ников, команда с владельцем."""

from __future__ import annotations

import os
import tempfile
import unittest

from assistant.stores import contacts_store
from assistant.stores import note_members
from assistant.stores import notes as notes_store
from assistant.stores import teams_store


class ContactsBulkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        os.environ["CONTACTS_USER_DIR"] = os.path.join(self._tmpdir.name, "contacts_user")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmpdir.name, "reg.json")
        notes_store._CONN = None  # type: ignore[attr-defined]
        note_members._PRESENCE.clear()
        contacts_store._CONTACTS_CACHE_BY_PATH.clear()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_parse_nicks(self) -> None:
        self.assertEqual(
            contacts_store.parse_telegram_nicks("@ivan, Maria;  https://t.me/petr_k/ @ivan ab"),
            ["ivan", "maria", "petr_k"],
        )

    def test_contact_without_email_roundtrip(self) -> None:
        item, st = contacts_store.create_contact_for_user(
            telegram_user_id=1, telegram_username=None, name="Иван", email="", telegram_username_contact="@ivan"
        )
        self.assertEqual(st, "added")
        self.assertEqual(contacts_store.contact_key(item), "tg:ivan")
        rows = contacts_store.load_contacts(telegram_user_id=1)
        self.assertEqual(len(rows), 1)
        # Позже дописали почту — ключ стал email.
        row = contacts_store.update_contact_for_user(
            telegram_user_id=1, telegram_username=None, old_email="tg:ivan", email="ivan@x.ru"
        )
        self.assertEqual(contacts_store.contact_key(row), "ivan@x.ru")
        self.assertTrue(contacts_store.delete_contact_for_user(telegram_user_id=1, telegram_username=None, email="ivan@x.ru"))

    def test_bulk_add_statuses(self) -> None:
        def resolve(nick):
            return {
                "anna": (2, "Анна Петрова", "anna@x.ru"),
                "boris": (3, "Борис", ""),
            }.get(nick, (None, "", ""))

        res = contacts_store.bulk_add_by_telegram(
            telegram_user_id=1, telegram_username="myself", text="@anna, boris, ghost_user, @myself", resolve=resolve
        )
        by = {r["username"]: r["status"] for r in res}
        self.assertEqual(by, {"anna": "added", "boris": "added_no_email", "ghost_user": "not_in_leo", "myself": "self"})
        rows = {contacts_store.contact_key(c): c for c in contacts_store.load_contacts(telegram_user_id=1)}
        self.assertEqual(rows["anna@x.ru"]["name"], "Анна Петрова")
        self.assertEqual(rows["tg:boris"]["telegram_user_id"], 3)
        self.assertEqual(rows["tg:ghost_user"]["name"], "@ghost_user")
        # Повтор — уже есть; у Бориса появилась почта.
        res2 = contacts_store.bulk_add_by_telegram(
            telegram_user_id=1, telegram_username="myself", text="anna boris",
            resolve=lambda n: (3, "Борис", "boris@x.ru") if n == "boris" else resolve(n),
        )
        self.assertEqual({r["username"]: r["status"] for r in res2}, {"anna": "existing", "boris": "email_added"})

    def test_team_with_no_email_member_and_owner_added(self) -> None:
        note_members.upsert_profile(1, username="owner", first_name="Олег", last_name="")
        items = contacts_store.load_contacts(telegram_user_id=1)
        items.append({"name": "Анна", "email": "anna@test.com", "telegram_user_id": 2})
        items.append({"name": "Борис", "email": "", "telegram_username": "boris", "telegram_user_id": 3})
        contacts_store.save_contacts(items, telegram_user_id=1)

        team = teams_store.create_team(1, "Отдел")
        teams_store.set_contact_teams(1, "anna@test.com", [team["id"]])
        result = teams_store.set_contact_teams(1, "tg:boris", [team["id"]])
        self.assertIn(team["id"], result["team_ids"])

        boris_book = {contacts_store.contact_key(c): c for c in contacts_store.load_contacts(telegram_user_id=3)}
        self.assertIn("anna@test.com", boris_book)
        self.assertIn("tg:owner", boris_book)  # владелец команды тоже в контактах
        self.assertEqual(boris_book["tg:owner"]["name"], "Олег")
        anna_book = {contacts_store.contact_key(c): c for c in contacts_store.load_contacts(telegram_user_id=2)}
        self.assertIn("tg:boris", anna_book)
        detailed = teams_store.list_teams_detailed(1)
        self.assertEqual({m["email"] for m in detailed[0]["members"]}, {"anna@test.com", "tg:boris"})


if __name__ == "__main__":
    unittest.main()
