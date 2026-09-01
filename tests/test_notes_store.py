import os
import tempfile
import unittest

from assistant.stores import notes as notes_store


class NotesStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_crud(self) -> None:
        n = notes_store.create_note(42, "Title", "Body")
        self.assertEqual(n["title"], "Title")
        listed = notes_store.list_notes(42)
        self.assertEqual(len(listed), 1)
        updated = notes_store.update_note(42, n["id"], title="New")
        assert updated is not None
        self.assertEqual(updated["title"], "New")
        self.assertTrue(notes_store.delete_note(42, n["id"]))

    def test_search(self) -> None:
        notes_store.create_note(7, "Филиалы в Куркино", "Адрес и режим работы")
        notes_store.create_note(7, "Другое", "Про встречи")
        hits = notes_store.search_notes(7, "филиалы куркино")
        self.assertEqual(len(hits), 1)
        self.assertIn("Куркино", hits[0]["title"])


if __name__ == "__main__":
    unittest.main()
