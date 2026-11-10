import os
import tempfile
import unittest

from assistant.stores import note_members
from assistant.stores import notes as notes_store
from assistant.stores import share_links


class NoteMembersTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        note_members._PRESENCE.clear()
        note_members._PHOTO_CACHE.clear()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_add_member_sees_and_edits_note(self) -> None:
        note = notes_store.create_note(1, "Общая", "текст")
        member = note_members.add_member(1, note["id"], 2)
        self.assertEqual(member["user_id"], "2")
        self.assertFalse(member["is_owner"])
        listed = notes_store.list_notes(2)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], note["id"])
        self.assertFalse(listed[0]["is_owner"])
        self.assertEqual(len(listed[0]["members"]), 2)
        updated = notes_store.update_note(
            "1", note["id"], body="новый", actor_user_id=2
        )
        assert updated is not None
        self.assertEqual(updated["body"], "новый")
        self.assertGreaterEqual(updated["revision"], 2)
        again = notes_store.get_accessible_note(2, note["id"])
        assert again is not None
        self.assertEqual(again["body"], "новый")
        self.assertEqual(again["owner_user_id"], "1")

    def test_member_leave_does_not_delete(self) -> None:
        note = notes_store.create_note(5, "X", "y")
        note_members.add_member(5, note["id"], 6)
        self.assertTrue(note_members.remove_member(5, note["id"], 6))
        self.assertIsNone(notes_store.get_accessible_note(6, note["id"]))
        self.assertIsNotNone(notes_store.get_note(5, note["id"]))

    def test_owner_delete_removes_membership(self) -> None:
        note = notes_store.create_note(7, "Z", "q")
        note_members.add_member(7, note["id"], 8)
        self.assertTrue(notes_store.delete_note(7, note["id"]))
        self.assertEqual(note_members.list_note_ids_for_member(8), [])

    def test_cannot_add_owner_or_knowledge(self) -> None:
        note = notes_store.create_note(1, "A", "b")
        with self.assertRaises(ValueError):
            note_members.add_member(1, note["id"], 1)
        kb = notes_store.create_note(1, "KB", "k", role=notes_store.KNOWLEDGE_ROLE)
        with self.assertRaises(ValueError):
            note_members.add_member(1, kb["id"], 2)
        member = note_members.add_member(1, kb["id"], 2, allow_knowledge=True)
        self.assertEqual(member["user_id"], "2")
        listed = notes_store.list_knowledge_notes(2)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], kb["id"])

    def test_conflict_on_stale_revision(self) -> None:
        note = notes_store.create_note(3, "T", "one")
        notes_store.update_note(3, note["id"], body="two")
        with self.assertRaises(notes_store.NoteConflictError):
            notes_store.update_note(
                3, note["id"], body="three", expected_revision=1
            )

    def test_edit_request_allow_adds_member(self) -> None:
        note = notes_store.create_note(10, "Док", "body")
        req = note_members.create_edit_request(
            owner_user_id=10,
            note_id=note["id"],
            requester_user_id=11,
            requester_username="anna",
            requester_name="Анна",
        )
        self.assertTrue(req["new"])
        again = note_members.create_edit_request(
            owner_user_id=10,
            note_id=note["id"],
            requester_user_id=11,
            requester_username="anna",
        )
        self.assertFalse(again["new"])
        self.assertEqual(req["token"], again["token"])
        resolved = note_members.resolve_edit_request(req["token"], allow=True)
        assert resolved is not None
        self.assertEqual(resolved["status"], "allowed")
        self.assertIsNotNone(notes_store.get_accessible_note(11, note["id"]))

    def test_edit_request_deny(self) -> None:
        note = notes_store.create_note(12, "N", "b")
        req = note_members.create_edit_request(
            owner_user_id=12,
            note_id=note["id"],
            requester_user_id=13,
        )
        note_members.resolve_edit_request(req["token"], allow=False)
        self.assertIsNone(notes_store.get_accessible_note(13, note["id"]))
        self.assertIsNone(
            note_members.resolve_edit_request(req["token"], allow=True)
        )

    def test_copy_from_share_is_independent(self) -> None:
        src = notes_store.create_note(20, "Шаблон", "<p>hi</p>")
        link = share_links.create_or_get_share(20, "local", src["id"], access="view")
        resolved = share_links.resolve_share(link["token"])
        assert resolved is not None
        copy = notes_store.create_note(21, src["title"], src["body"])
        notes_store.update_note(20, src["id"], body="<p>changed</p>")
        self.assertEqual(copy["body"], "<p>hi</p>")
        self.assertNotEqual(copy["id"], src["id"])

    def test_presence_and_color(self) -> None:
        note = notes_store.create_note(1, "Live", "x")
        note_members.heartbeat(1, note["id"], 1, cursor=3, display_name="А")
        note_members.heartbeat(1, note["id"], 2, cursor=8, display_name="Б")
        peers = note_members.list_peers(1, note["id"], exclude_user_id=1)
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["user_id"], "2")
        self.assertEqual(peers[0]["cursor"], 8)
        self.assertTrue(peers[0]["color"].startswith("#"))
        note_members.leave_presence(1, note["id"], 2)
        self.assertEqual(note_members.list_peers(1, note["id"], exclude_user_id=1), [])

    def test_resolve_contact_via_registry(self) -> None:
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmpdir.name, "reg.json")
        from assistant.stores import telegram_registry

        telegram_registry.register_user(
            telegram_user_id=777, telegram_username="artyawn"
        )
        uid, _ = note_members.resolve_contact_telegram_id(
            owner_user_id=1, telegram_username="@Artyawn"
        )
        self.assertEqual(uid, 777)

    def test_resolve_missing_user_mentions_username(self) -> None:
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(
            self._tmpdir.name, "reg-empty.json"
        )
        with self.assertRaises(ValueError) as ctx:
            note_members.resolve_contact_telegram_id(
                owner_user_id=1, telegram_username="missinguser"
            )
        self.assertIn("@missinguser", str(ctx.exception))

    def test_resolve_email_account_without_bot(self) -> None:
        # Вошёл по почте, бота не открывал: находим по почте и по нику из приглашения в организацию.
        from assistant.stores import accounts, orgs_store

        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmpdir.name, "reg-empty2.json")
        acc = accounts.create_email_account("garya@obuchat.me")
        uid = int(acc["user_id"])
        by_mail, _ = note_members.resolve_contact_telegram_id(owner_user_id=1, email="Garya@obuchat.me")
        self.assertEqual(by_mail, uid)
        with self.assertRaises(ValueError):
            note_members.resolve_contact_telegram_id(owner_user_id=1, telegram_username="garya_dzhidzhaev")
        with orgs_store._LOCK:  # type: ignore[attr-defined]
            orgs_store._insert_member(1, str(uid), key="garya@obuchat.me", tg_hint="garya_dzhidzhaev")  # type: ignore[attr-defined]
            orgs_store._conn().commit()  # type: ignore[attr-defined]
        by_nick, _ = note_members.resolve_contact_telegram_id(owner_user_id=1, telegram_username="@garya_dzhidzhaev")
        self.assertEqual(by_nick, uid)
        self.assertEqual((note_members.get_profile(uid) or {}).get("username"), "garya_dzhidzhaev")

    def test_registry_tg_id_maps_to_email_account(self) -> None:
        from assistant.stores import accounts, telegram_registry

        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmpdir.name, "reg3.json")
        telegram_registry.register_user(telegram_user_id=888, telegram_username="linked_user")
        acc = accounts.create_email_account("linked@obuchat.me", tg_user_id=888)
        uid, _ = note_members.resolve_contact_telegram_id(owner_user_id=1, telegram_username="linked_user")
        self.assertEqual(uid, int(acc["user_id"]))

    def test_contact_tg_id_maps_to_email_account(self) -> None:
        # Вошёл по почте, привязал Telegram; владелец добавляет его из контактов (там Telegram id).
        from assistant.stores import accounts

        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmpdir.name, "reg4.json")
        acc = accounts.create_email_account("art@obuchat.me", tg_user_id=4242)
        uid, _ = note_members.resolve_contact_telegram_id(owner_user_id=1, telegram_user_id=4242)
        self.assertEqual(uid, int(acc["user_id"]))

    def test_member_by_linked_tg_id_sees_note(self) -> None:
        # Доступ уже выдан на Telegram id, а человек входит аккаунтом по почте с этим Telegram.
        from assistant.stores import accounts

        acc = accounts.create_email_account("art2@obuchat.me", tg_user_id=4343)
        uid = int(acc["user_id"])
        note = notes_store.create_note(1, "Общая", "текст")
        note_members.add_member(1, note["id"], 4343)
        self.assertEqual([n["id"] for n in notes_store.list_notes(uid)], [note["id"]])
        self.assertIsNotNone(notes_store.get_accessible_note(uid, note["id"]))
        self.assertTrue(note_members.remove_member(1, note["id"], uid))
        self.assertEqual(notes_store.list_notes(uid), [])
        # Чужой аккаунт по почте без этого Telegram документ не видит.
        other = accounts.create_email_account("other@obuchat.me")
        note2 = notes_store.create_note(1, "Ещё", "x")
        note_members.add_member(1, note2["id"], 4343)
        self.assertEqual(notes_store.list_notes(int(other["user_id"])), [])


if __name__ == "__main__":
    unittest.main()
