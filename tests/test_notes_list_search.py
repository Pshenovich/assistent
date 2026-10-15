"""Поиск по всему тексту для списка заметок и постраничная подгрузка."""

from __future__ import annotations

import os
import tempfile
import unittest

from assistant.stores import notes as notes_store


class NotesListSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_substring_beyond_preview_with_snippet(self) -> None:
        long = "<p>" + "вступление " * 200 + "</p><p>Бюджет маркетинга согласуем в пятницу.</p>"
        notes_store.create_note(1, title="Планы", body=long)
        notes_store.create_note(1, title="Другое", body="<p>ничего</p>")
        found = notes_store.search_notes_for_list(1, "маркетинг")
        self.assertEqual([n["title"] for n in found], ["Планы"])
        self.assertIn("маркетинга", found[0]["search_snippet"])
        self.assertTrue(found[0]["search_snippet"].startswith("…"))

    def test_title_ranks_first_and_partial_word(self) -> None:
        notes_store.create_note(1, title="Ретро", body="<p>обсудили бюджет</p>")
        notes_store.create_note(1, title="Бюджет на Q4", body="<p>цифры</p>")
        found = notes_store.search_notes_for_list(1, "бюдж")
        self.assertEqual([n["title"] for n in found], ["Бюджет на Q4", "Ретро"])

    def test_short_query_empty(self) -> None:
        notes_store.create_note(1, title="A", body="<p>a</p>")
        self.assertEqual(notes_store.search_notes_for_list(1, "a"), [])


if __name__ == "__main__":
    unittest.main()
