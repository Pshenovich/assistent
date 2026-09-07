import os
import tempfile
import unittest

from assistant.stores import notes as notes_store
from assistant.stores import share_comments
from assistant.stores import share_links


class ShareLinksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_create_is_idempotent_until_revoke(self) -> None:
        first = share_links.create_or_get_share(42, "local", 7)
        second = share_links.create_or_get_share(42, "local", 7)
        self.assertEqual(first["token"], second["token"])
        self.assertEqual(first["access"], "view")
        self.assertTrue(share_links.revoke_share(42, "local", 7))
        third = share_links.create_or_get_share(42, "local", 7)
        self.assertNotEqual(first["token"], third["token"])
        self.assertIsNone(share_links.resolve_share(first["token"]))
        self.assertIsNotNone(share_links.resolve_share(third["token"]))

    def test_default_access_is_view(self) -> None:
        link = share_links.create_or_get_share(1, "journal", 99)
        self.assertEqual(link["access"], "view")
        again = share_links.get_active_share(1, "journal", 99)
        assert again is not None
        self.assertEqual(again["access"], "view")
        resolved = share_links.resolve_share(link["token"])
        assert resolved is not None
        self.assertEqual(resolved["access"], "view")

    def test_create_or_get_updates_access_without_new_token(self) -> None:
        first = share_links.create_or_get_share(5, "local", 3, access="view")
        second = share_links.create_or_get_share(5, "local", 3, access="comment")
        self.assertEqual(first["token"], second["token"])
        self.assertEqual(second["access"], "comment")
        resolved = share_links.resolve_share(first["token"])
        assert resolved is not None
        self.assertEqual(resolved["access"], "comment")
        back = share_links.update_share_access(5, "local", 3, "view")
        assert back is not None
        self.assertEqual(back["token"], first["token"])
        self.assertEqual(back["access"], "view")

    def test_other_user_cannot_revoke_or_change_access(self) -> None:
        link = share_links.create_or_get_share(1, "journal", 99, access="comment")
        self.assertIsNone(share_links.get_active_share(2, "journal", 99))
        self.assertFalse(share_links.revoke_share(2, "journal", 99))
        self.assertIsNone(share_links.update_share_access(2, "journal", 99, "view"))
        resolved = share_links.resolve_share(link["token"])
        assert resolved is not None
        self.assertEqual(resolved["access"], "comment")
        self.assertIsNotNone(share_links.resolve_share(link["token"]))

    def test_invalid_kind_and_token(self) -> None:
        with self.assertRaises(ValueError):
            share_links.create_or_get_share(1, "secret", 1)
        self.assertIsNone(share_links.resolve_share(""))
        self.assertIsNone(share_links.resolve_share("short"))
        self.assertIsNone(share_links.resolve_share("../etc/passwd"))

    def test_comments_allowed_only_for_comment_access(self) -> None:
        view = share_links.create_or_get_share(8, "local", 1, access="view")
        comment = share_links.create_or_get_share(8, "local", 2, access="comment")
        self.assertFalse(share_comments.comments_allowed_for_link(view))
        self.assertTrue(share_comments.comments_allowed_for_link(comment))
        share_links.create_or_get_share(8, "local", 2, access="view")
        switched = share_links.get_active_share(8, "local", 2)
        self.assertFalse(share_comments.comments_allowed_for_link(switched))
        self.assertTrue(share_comments.comments_visible_for_link(view))
        self.assertTrue(share_comments.comments_visible_for_link(comment))


class ShareCommentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_add_list_and_delete(self) -> None:
        row = share_comments.add_comment(
            10,
            "local",
            4,
            author_user_id=20,
            author_name="Анна",
            author_username="anna",
            body="Привет",
        )
        self.assertEqual(row["body"], "Привет")
        self.assertEqual(row["author_name"], "Анна")
        self.assertEqual(row["quote"], "")
        listed = share_comments.list_comments(10, "local", 4)
        self.assertEqual(len(listed), 1)
        self.assertFalse(share_comments.delete_comment(row["id"], requester_user_id=99))
        self.assertTrue(share_comments.delete_comment(row["id"], requester_user_id=20))
        self.assertEqual(share_comments.list_comments(10, "local", 4), [])

    def test_owner_can_delete_and_thread_survives_access_change(self) -> None:
        share_links.create_or_get_share(10, "local", 4, access="comment")
        first = share_comments.add_comment(
            10,
            "local",
            4,
            author_user_id=21,
            author_name="Борис",
            body="Раз",
        )
        share_links.create_or_get_share(10, "local", 4, access="view")
        share_links.create_or_get_share(10, "local", 4, access="comment")
        listed = share_comments.list_comments(10, "local", 4)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], first["id"])
        self.assertTrue(share_comments.delete_comment(first["id"], requester_user_id=10))
        self.assertEqual(share_comments.list_comments(10, "local", 4), [])

    def test_rejects_empty_and_too_long(self) -> None:
        with self.assertRaises(ValueError):
            share_comments.add_comment(
                1, "local", 1, author_user_id=2, author_name="A", body="   "
            )
        with self.assertRaises(ValueError):
            share_comments.add_comment(
                1,
                "local",
                1,
                author_user_id=2,
                author_name="A",
                body="x" * (share_comments.MAX_BODY_LEN + 1),
            )

    def test_delete_all_for_item(self) -> None:
        share_comments.add_comment(
            3, "journal", 9, author_user_id=4, author_name="C", body="один"
        )
        share_comments.add_comment(
            3, "journal", 9, author_user_id=5, author_name="D", body="два"
        )
        self.assertEqual(share_comments.delete_all_for_item(3, "journal", 9), 2)
        self.assertEqual(share_comments.list_comments(3, "journal", 9), [])

    def test_comment_keeps_text_anchor(self) -> None:
        row = share_comments.add_comment(
            1,
            "local",
            2,
            author_user_id=3,
            author_name="Катя",
            body="Уточнить",
            quote="режим работы",
            prefix="Адрес и ",
            suffix=" филиала",
        )
        self.assertEqual(row["quote"], "режим работы")
        self.assertEqual(row["prefix"], "Адрес и ")
        listed = share_comments.list_comments(1, "local", 2)
        self.assertEqual(listed[0]["suffix"], " филиала")

    def test_parent_id_and_paie_thread(self) -> None:
        chair = share_comments.add_comment(
            8,
            "local",
            3,
            author_user_id=0,
            author_name="CHAIR",
            author_username="PAIE",
            body="Решение: пилот",
        )
        self.assertIsNone(chair["parent_id"])
        self.assertTrue(share_comments.is_paie_comment(chair))
        reply = share_comments.add_comment(
            8,
            "local",
            3,
            author_user_id=8,
            author_name="Анна",
            body="А если продлить до месяца?",
            parent_id=chair["id"],
        )
        self.assertEqual(reply["parent_id"], chair["id"])
        side = share_comments.add_comment(
            8,
            "local",
            3,
            author_user_id=9,
            author_name="Борис",
            body="Про выделенный абзац",
            quote="пилот",
        )
        listed = share_comments.list_comments(8, "local", 3)
        thread = share_comments.paie_thread(listed)
        self.assertEqual([c["id"] for c in thread], [chair["id"], reply["id"]])
        self.assertEqual(share_comments.paie_thread_root_id(listed), chair["id"])
        self.assertNotIn(side["id"], [c["id"] for c in thread])
        with self.assertRaises(ValueError):
            share_comments.add_comment(
                8,
                "local",
                3,
                author_user_id=8,
                author_name="Анна",
                body="битый parent",
                parent_id=999999,
            )


if __name__ == "__main__":
    unittest.main()
