"""Owner-only invite/share ACL for shared notes."""

from __future__ import annotations

import os
import tempfile
import unittest

from assistant.stores import note_members
from assistant.stores import notes as notes_store


class CollabAclTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        note_members._PRESENCE.clear()
        note_members._PHOTO_CACHE.clear()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_member_resolves_owner_but_is_not_owner(self) -> None:
        from usage_server import _resolve_item_owner

        note = notes_store.create_note(1, "Общая", "текст")
        note_members.add_member(1, note["id"], 2)
        owner = _resolve_item_owner("2", "local", str(note["id"]))
        self.assertEqual(owner, "1")
        self.assertNotEqual("2", owner)

    def test_owner_resolves_self(self) -> None:
        from usage_server import _resolve_item_owner

        note = notes_store.create_note(5, "Моя", "x")
        owner = _resolve_item_owner("5", "local", str(note["id"]))
        self.assertEqual(owner, "5")


if __name__ == "__main__":
    unittest.main()
