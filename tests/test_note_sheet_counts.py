"""Счётчик листов для карточек списка заметок."""

from __future__ import annotations

import os
import tempfile
import unittest

from assistant.stores import note_sheets
from assistant.stores import notes as notes_store


class SheetCountsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_counts(self) -> None:
        a = notes_store.create_note(1, title="A", body="<p>a</p>")
        b = notes_store.create_note(1, title="B", body="<p>b</p>")
        note_sheets.create_sheet(a, title="Лист 2")
        note_sheets.create_sheet(a, title="Лист 3")
        counts = note_sheets.extra_sheet_counts([a["id"], b["id"], "x", None])
        self.assertEqual(counts, {int(a["id"]): 2})
        self.assertEqual(note_sheets.extra_sheet_counts([]), {})

    def test_sheet_edit_bumps_note_updated_at(self) -> None:
        a = notes_store.create_note(1, title="A", body="<p>a</p>")
        sheet = note_sheets.create_sheet(a, title="Лист 2", body="<p>x</p>")
        with notes_store._LOCK:  # type: ignore[attr-defined]
            notes_store._conn().execute("UPDATE local_notes SET updated_at = ? WHERE id = ?", ("2020-01-01T00:00:00+00:00", a["id"]))  # type: ignore[attr-defined]
            notes_store._conn().commit()  # type: ignore[attr-defined]
        before = notes_store.get_note(1, a["id"])
        note_sheets.update_sheet(a["id"], sheet["id"], body="<p>x</p>")  # без изменений
        self.assertEqual(notes_store.get_note(1, a["id"])["updated_at"], "2020-01-01T00:00:00+00:00")
        note_sheets.update_sheet(a["id"], sheet["id"], body="<p>y</p>")
        after = notes_store.get_note(1, a["id"])
        self.assertGreater(after["updated_at"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(after["revision"], before["revision"])


if __name__ == "__main__":
    unittest.main()
