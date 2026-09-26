import os
import tempfile
import unittest

from assistant.board.note_paei import load_note_excerpt_for_agents
from assistant.nlu.ask_context import pack_note_sheets
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store


class NoteSheetsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_list_includes_primary(self) -> None:
        note = notes_store.create_note(1, "Сделка", "<p>план</p>")
        listed = sheets.list_sheets(note)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], "main")
        self.assertTrue(listed[0]["is_primary"])
        self.assertEqual(listed[0]["title"], "Основная")
        self.assertIn("план", listed[0]["body"])

    def test_create_update_delete(self) -> None:
        note = notes_store.create_note(2, "Сделка", "тело")
        extra = sheets.create_sheet(note, "Черновик")
        self.assertEqual(extra["title"], "Черновик")
        self.assertFalse(extra["is_primary"])
        self.assertEqual(extra["revision"], 1)
        listed = sheets.list_sheets(note)
        self.assertEqual(len(listed), 2)
        updated = sheets.update_sheet(
            note["id"], extra["id"], body="<p>черновик</p>", expected_revision=1
        )
        self.assertEqual(updated["revision"], 2)
        self.assertIn("черновик", updated["body"])
        self.assertTrue(sheets.delete_sheet(note["id"], extra["id"]))
        self.assertEqual(len(sheets.list_sheets(note)), 1)

    def test_conflict(self) -> None:
        note = notes_store.create_note(3, "A", "b")
        extra = sheets.create_sheet(note, "Лист 2")
        sheets.update_sheet(note["id"], extra["id"], body="x", expected_revision=1)
        with self.assertRaises(sheets.SheetConflictError):
            sheets.update_sheet(note["id"], extra["id"], body="y", expected_revision=1)

    def test_limit(self) -> None:
        note = notes_store.create_note(4, "A", "b")
        for i in range(sheets.MAX_EXTRA_SHEETS):
            sheets.create_sheet(note, f"L{i}")
        with self.assertRaises(sheets.SheetError):
            sheets.create_sheet(note, "overflow")

    def test_knowledge_rejected(self) -> None:
        kb = notes_store.create_note(5, "БЗ", "док", role=notes_store.KNOWLEDGE_ROLE)
        with self.assertRaises(sheets.SheetError):
            sheets.create_sheet(kb, "Черновик")
        self.assertEqual(len(sheets.list_sheets(kb)), 1)

    def test_delete_note_cascades(self) -> None:
        note = notes_store.create_note(6, "A", "b")
        extra = sheets.create_sheet(note, "Черновик")
        self.assertTrue(notes_store.delete_note(6, note["id"]))
        self.assertIsNone(sheets.get_extra_sheet(note["id"], extra["id"]))

    def test_duplicate_copies_sheets(self) -> None:
        note = notes_store.create_note(7, "A", "основная")
        sheets.create_sheet(note, "Черновик")
        extra = sheets.list_extra_sheets(note["id"])[0]
        sheets.update_sheet(note["id"], extra["id"], body="сырьё")
        dup = notes_store.duplicate_note(7, note["id"])
        assert dup is not None
        copied = sheets.list_extra_sheets(dup["id"])
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0]["title"], "Черновик")
        self.assertEqual(copied[0]["body"], "сырьё")
        self.assertNotEqual(copied[0]["id"], extra["id"])

    def test_pack_note_sheets_focus_and_omit(self) -> None:
        rows = [
            {"id": "main", "title": "Основная", "body": "чистовой план " * 40},
            {"id": "2", "title": "Черновик", "body": "черновые мысли " * 40},
            {"id": "3", "title": "Материалы", "body": "свалка " * 40},
        ]
        packed = pack_note_sheets(
            rows, selected_ids=["main", "2"], focus_id="2", budget=400
        )
        self.assertIn("[Черновик]", packed)
        self.assertIn("[Основная]", packed)
        self.assertNotIn("[Материалы]", packed)
        empty = pack_note_sheets(rows, selected_ids=[], budget=400)
        self.assertEqual(empty, "")

    def test_excerpt_uses_selected_sheets(self) -> None:
        note = notes_store.create_note(8, "Сделка", "чистовая")
        extra = sheets.create_sheet(note, "Черновик")
        sheets.update_sheet(note["id"], extra["id"], body="секретный черновик")
        title, text = load_note_excerpt_for_agents(
            8, "local", note["id"], sheet_ids=["main"], budget=2000
        )
        self.assertEqual(title, "Сделка")
        self.assertIn("чистовая", text)
        self.assertNotIn("секретный", text)
        _, both = load_note_excerpt_for_agents(
            8, "local", note["id"], sheet_ids=["main", str(extra["id"])], budget=2000
        )
        self.assertIn("чистовая", both)
        self.assertIn("секретный", both)
        _, none = load_note_excerpt_for_agents(
            8, "local", note["id"], sheet_ids=[], budget=2000
        )
        self.assertEqual(none, "(пустая заметка)")

    def test_compose_share_body_selects_sheets(self) -> None:
        note = notes_store.create_note(9, "Сделка", "<p>основная</p>")
        extra = sheets.create_sheet(note, "Финальный")
        sheets.update_sheet(note["id"], extra["id"], body="<p>чистовик</p>")
        note = notes_store.get_note(9, note["id"])
        assert note is not None
        empty, selected = sheets.compose_share_body(note, [])
        self.assertIn("основная", empty)
        self.assertNotIn("чистовик", empty)
        self.assertEqual([str(r["id"]) for r in selected], ["main"])
        only_extra, rows = sheets.compose_share_body(note, [str(extra["id"])])
        self.assertNotIn("основная", only_extra)
        self.assertIn("Финальный", only_extra)
        self.assertIn("чистовик", only_extra)
        self.assertEqual([str(r["id"]) for r in rows], [str(extra["id"])])
        both, _ = sheets.compose_share_body(note, ["main", str(extra["id"])])
        self.assertIn("основная", both)
        self.assertIn("чистовик", both)


if __name__ == "__main__":
    unittest.main()
