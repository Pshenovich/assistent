import os
import tempfile
import unittest

from assistant.stores import notes as notes_store
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
        self.assertTrue(share_links.revoke_share(42, "local", 7))
        third = share_links.create_or_get_share(42, "local", 7)
        self.assertNotEqual(first["token"], third["token"])
        self.assertIsNone(share_links.resolve_share(first["token"]))
        self.assertIsNotNone(share_links.resolve_share(third["token"]))

    def test_resolve_rejects_other_user_guesses(self) -> None:
        link = share_links.create_or_get_share(1, "journal", 99)
        self.assertIsNone(share_links.get_active_share(2, "journal", 99))
        self.assertFalse(share_links.revoke_share(2, "journal", 99))
        self.assertIsNotNone(share_links.resolve_share(link["token"]))

    def test_invalid_kind_and_token(self) -> None:
        with self.assertRaises(ValueError):
            share_links.create_or_get_share(1, "secret", 1)
        self.assertIsNone(share_links.resolve_share(""))
        self.assertIsNone(share_links.resolve_share("short"))
        self.assertIsNone(share_links.resolve_share("../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
