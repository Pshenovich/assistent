from __future__ import annotations

import os
import tempfile
import unittest

from assistant.stores import contacts_store
from assistant.stores import note_members
from assistant.stores import notes as notes_store
from assistant.stores import teams_store


class TeamsStoreTests(unittest.TestCase):
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

    def _add_contact(
        self, owner: int, *, name: str, email: str, telegram_user_id: int | None = None
    ) -> None:
        row = {"name": name, "email": email}
        if telegram_user_id:
            row["telegram_user_id"] = telegram_user_id
        items = contacts_store.load_contacts(telegram_user_id=owner)
        items.append(row)
        contacts_store.save_contacts(items, telegram_user_id=owner)

    def test_create_rename_delete_team(self) -> None:
        team = teams_store.create_team(1, "Маркетинг")
        self.assertEqual(team["name"], "Маркетинг")
        self.assertEqual(team["member_count"], 0)
        listed = teams_store.list_teams(1)
        self.assertEqual(len(listed), 1)
        renamed = teams_store.rename_team(1, team["id"], "Рост")
        assert renamed is not None
        self.assertEqual(renamed["name"], "Рост")
        self.assertTrue(teams_store.delete_team(1, team["id"]))
        self.assertEqual(teams_store.list_teams(1), [])

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            teams_store.create_team(1, "   ")

    def test_contact_teams_and_knowledge_share(self) -> None:
        self._add_contact(1, name="Анна", email="anna@test.com", telegram_user_id=2)
        team = teams_store.create_team(1, "Продукт")
        result = teams_store.set_contact_teams(1, "anna@test.com", [team["id"]])
        self.assertEqual(result["team_ids"], [team["id"]])
        kb = notes_store.create_note(
            1, "Регламент", "текст", role=notes_store.KNOWLEDGE_ROLE
        )
        shared = teams_store.set_note_teams(1, kb["id"], [team["id"]])
        self.assertEqual(len(shared["teams"]), 1)
        self.assertEqual(len(shared["added_members"]), 1)
        self.assertEqual(shared["added_members"][0]["user_id"], "2")
        member_kb = notes_store.list_knowledge_notes(2)
        self.assertEqual(len(member_kb), 1)
        self.assertEqual(member_kb[0]["id"], kb["id"])
        self.assertFalse(member_kb[0]["is_owner"])
        self.assertEqual(len(notes_store.list_notes(2)), 0)
        own = notes_store.list_knowledge_notes(1)
        self.assertTrue(own[0]["is_owner"])

    def test_unshare_and_remove_member_revokes(self) -> None:
        self._add_contact(1, name="Боб", email="bob@test.com", telegram_user_id=3)
        team = teams_store.create_team(1, "Юристы")
        teams_store.set_contact_teams(1, "bob@test.com", [team["id"]])
        kb = notes_store.create_note(
            1, "Договор", "ok", role=notes_store.KNOWLEDGE_ROLE
        )
        teams_store.set_note_teams(1, kb["id"], [team["id"]])
        self.assertEqual(len(notes_store.list_knowledge_notes(3)), 1)
        teams_store.set_note_teams(1, kb["id"], [])
        self.assertEqual(notes_store.list_knowledge_notes(3), [])
        teams_store.set_note_teams(1, kb["id"], [team["id"]])
        self.assertEqual(len(notes_store.list_knowledge_notes(3)), 1)
        teams_store.set_contact_teams(1, "bob@test.com", [])
        self.assertEqual(notes_store.list_knowledge_notes(3), [])

    def test_two_teams_keep_access_until_both_gone(self) -> None:
        self._add_contact(1, name="Катя", email="katya@test.com", telegram_user_id=4)
        a = teams_store.create_team(1, "A")
        b = teams_store.create_team(1, "B")
        teams_store.set_contact_teams(1, "katya@test.com", [a["id"], b["id"]])
        kb = notes_store.create_note(
            1, "Wiki", "body", role=notes_store.KNOWLEDGE_ROLE
        )
        teams_store.set_note_teams(1, kb["id"], [a["id"], b["id"]])
        self.assertEqual(len(notes_store.list_knowledge_notes(4)), 1)
        teams_store.set_note_teams(1, kb["id"], [a["id"]])
        self.assertEqual(len(notes_store.list_knowledge_notes(4)), 1)
        teams_store.delete_team(1, a["id"])
        self.assertEqual(notes_store.list_knowledge_notes(4), [])

    def test_contact_without_telegram_stays_pending(self) -> None:
        self._add_contact(1, name="Гость", email="guest@test.com")
        team = teams_store.create_team(1, "Гости")
        teams_store.set_contact_teams(1, "guest@test.com", [team["id"]])
        kb = notes_store.create_note(
            1, "Секрет", "x", role=notes_store.KNOWLEDGE_ROLE
        )
        added = teams_store.set_note_teams(1, kb["id"], [team["id"]])
        self.assertEqual(added["added_members"], [])
        self.assertEqual(note_members.list_member_rows(1, kb["id"]), [])

    def test_cannot_share_regular_note(self) -> None:
        team = teams_store.create_team(1, "X")
        note = notes_store.create_note(1, "Обычная", "t")
        with self.assertRaises(ValueError):
            teams_store.set_note_teams(1, note["id"], [team["id"]])

    def test_adding_contact_later_grants_existing_shares(self) -> None:
        team = teams_store.create_team(1, "Позже")
        kb = notes_store.create_note(
            1, "Файл", "y", role=notes_store.KNOWLEDGE_ROLE
        )
        teams_store.set_note_teams(1, kb["id"], [team["id"]])
        self._add_contact(1, name="Даша", email="dasha@test.com", telegram_user_id=5)
        result = teams_store.set_contact_teams(1, "dasha@test.com", [team["id"]])
        self.assertEqual(len(result["added_members"]), 1)
        self.assertEqual(notes_store.list_knowledge_notes(5)[0]["id"], kb["id"])

    def test_knowledge_version_includes_shared(self) -> None:
        self._add_contact(1, name="Егор", email="egor@test.com", telegram_user_id=6)
        team = teams_store.create_team(1, "Версия")
        teams_store.set_contact_teams(1, "egor@test.com", [team["id"]])
        v0 = notes_store.knowledge_version(6)
        kb = notes_store.create_note(
            1, "Версия БЗ", "abc", role=notes_store.KNOWLEDGE_ROLE
        )
        teams_store.set_note_teams(1, kb["id"], [team["id"]])
        v1 = notes_store.knowledge_version(6)
        self.assertNotEqual(v0, v1)

    def test_ensure_knowledge_note_ignores_shared(self) -> None:
        self._add_contact(1, name="Ира", email="ira@test.com", telegram_user_id=7)
        team = teams_store.create_team(1, "Свои")
        teams_store.set_contact_teams(1, "ira@test.com", [team["id"]])
        kb = notes_store.create_note(
            1, "Чужой", "z", role=notes_store.KNOWLEDGE_ROLE
        )
        teams_store.set_note_teams(1, kb["id"], [team["id"]])
        own = notes_store.ensure_knowledge_note(7)
        self.assertNotEqual(own["id"], kb["id"])
        self.assertEqual(own["owner_user_id"], "7")
        listed = notes_store.list_knowledge_notes(7)
        self.assertEqual(len(listed), 2)


if __name__ == "__main__":
    unittest.main()
