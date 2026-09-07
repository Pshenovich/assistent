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

    def test_knowledge_notes_are_many_and_hidden_from_notes(self) -> None:
        a = notes_store.create_note(
            9, "Продукты", "описание", role=notes_store.KNOWLEDGE_ROLE
        )
        b = notes_store.create_note(
            9, "Процессы", "регламенты", role=notes_store.KNOWLEDGE_ROLE
        )
        listed_kb = notes_store.list_knowledge_notes(9)
        self.assertEqual(len(listed_kb), 2)
        self.assertEqual({n["title"] for n in listed_kb}, {"Продукты", "Процессы"})
        notes_store.create_note(9, "Обычная", "текст")
        listed = notes_store.list_notes(9)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["title"], "Обычная")
        self.assertTrue(notes_store.delete_note(9, a["id"]))
        self.assertEqual(len(notes_store.list_knowledge_notes(9)), 1)
        self.assertEqual(notes_store.list_knowledge_notes(9)[0]["id"], b["id"])

    def test_ensure_knowledge_note_creates_first_only(self) -> None:
        kb = notes_store.ensure_knowledge_note(9)
        self.assertEqual(kb["title"], "База знаний")
        self.assertTrue(kb["is_knowledge"])
        again = notes_store.ensure_knowledge_note(9)
        self.assertEqual(kb["id"], again["id"])
        self.assertIsNotNone(notes_store.get_knowledge_note(9))

    def test_search(self) -> None:
        notes_store.create_note(7, "Филиалы в Куркино", "Адрес и режим работы")
        notes_store.create_note(7, "Другое", "Про встречи")
        hits = notes_store.search_notes(7, "филиалы куркино")
        self.assertEqual(len(hits), 1)
        self.assertIn("Куркино", hits[0]["title"])


if __name__ == "__main__":
    unittest.main()
