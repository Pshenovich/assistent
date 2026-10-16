"""История версий: что и когда сохраняется, восстановление листов и заметок."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta

from assistant.stores import note_sheets
from assistant.stores import note_versions
from assistant.stores import notes as notes_store


class NoteVersionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        os.environ.pop("NOTE_VERSIONS_DB_PATH", None)
        notes_store._CONN = None  # type: ignore[attr-defined]
        note_versions._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]
        note_versions._CONN = None  # type: ignore[attr-defined]

    def test_edit_keeps_previous_state_throttled(self) -> None:
        n = notes_store.create_note(1, title="A", body="<p>" + "раз " * 100 + "</p>")
        notes_store.update_note(1, n["id"], body="<p>" + "раз " * 101 + "</p>")
        notes_store.update_note(1, n["id"], body="<p>" + "раз " * 102 + "</p>")  # в пределах 10 минут — без новой версии
        vs = note_versions.list_versions(n["id"])
        self.assertEqual(len(vs), 1)
        v = note_versions.get_version(n["id"], vs[0]["id"])
        self.assertEqual(v["body"].count("раз"), 100)

    def test_big_change_always_saved(self) -> None:
        long = "<p>" + "текст " * 400 + "</p>"
        n = notes_store.create_note(1, title="A", body=long)
        notes_store.update_note(1, n["id"], body=long + "<p>x</p>")
        notes_store.update_note(1, n["id"], body="<p>текст текст…</p>")  # обрезка — версия всё равно есть
        vs = note_versions.list_versions(n["id"])
        self.assertEqual(len(vs), 2)
        self.assertGreater(vs[0]["size"], 2000)

    def test_throttle_expires(self) -> None:
        n = notes_store.create_note(1, title="A", body="<p>один</p>")
        notes_store.update_note(1, n["id"], body="<p>два</p>")
        old = note_versions.THROTTLE
        note_versions.THROTTLE = timedelta(seconds=-1)
        try:
            notes_store.update_note(1, n["id"], body="<p>три</p>")
        finally:
            note_versions.THROTTLE = old
        bodies = [note_versions.get_version(n["id"], v["id"])["body"] for v in note_versions.list_versions(n["id"])]
        self.assertEqual(bodies, ["<p>два</p>", "<p>один</p>"])

    def test_sheet_delete_and_note_delete_are_versioned(self) -> None:
        n = notes_store.create_note(1, title="A", body="<p>основа</p>")
        sh = note_sheets.create_sheet(n, title="Лист 2", body="<p>важный лист</p>")
        note_sheets.delete_sheet(n["id"], sh["id"])
        vs = note_versions.list_versions(n["id"])
        self.assertEqual(vs[0]["reason"], "sheet_deleted")
        self.assertEqual(vs[0]["sheet_title"], "Лист 2")
        notes_store.delete_note(1, n["id"])
        reasons = {v["reason"] for v in note_versions.list_versions(n["id"])}
        self.assertIn("note_deleted", reasons)


if __name__ == "__main__":
    unittest.main()
