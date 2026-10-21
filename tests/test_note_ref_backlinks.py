import os
import tempfile
import unittest

from assistant.stores import note_members
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store


def _chip(ref: str) -> str:
    return (
        f'<span contenteditable="false" data-leo-ref="{ref}" class="note-ref-chip">'
        '<span class="note-ref-chip-icon"></span><span class="note-ref-chip-label">x</span></span>'
    )


class NoteRefBacklinksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_finds_note_and_sheet_mentions(self) -> None:
        a = notes_store.create_note(1, "Планёрка", f"<p>Сделать {_chip('task:12')}</p>")
        b = notes_store.create_note(1, "Другое", "<p>без ссылок</p>")
        sheets.create_sheet(b, "Итоги", f"<p>{_chip('task:12')}</p>")
        notes_store.create_note(1, "Похожая", f"<p>{_chip('task:120')}</p>")
        found = sheets.find_ref_backlinks(1, "task", "12")
        got = {(x["note_id"], x["sheet_id"]) for x in found}
        self.assertIn((a["id"], ""), got)
        self.assertEqual(len(got), 2)
        sheet_hit = [x for x in found if x["sheet_id"]][0]
        self.assertEqual(sheet_hit["note_id"], b["id"])
        self.assertEqual(sheet_hit["sheet_title"], "Итоги")

    def test_respects_access_and_person_keys_with_underscore(self) -> None:
        notes_store.create_note(2, "Чужая", f"<p>{_chip('person:a_b@x.ru')}</p>")
        shared = notes_store.create_note(2, "Общая", f"<p>{_chip('person:a_b@x.ru')}</p>")
        notes_store.create_note(1, "Почти", f"<p>{_chip('person:aXb@x.ru')}</p>")
        self.assertEqual(sheets.find_ref_backlinks(1, "person", "a_b@x.ru"), [])
        note_members.add_member(2, shared["id"], 1)
        found = sheets.find_ref_backlinks(1, "person", "a_b@x.ru")
        self.assertEqual([x["note_id"] for x in found], [shared["id"]])

    def test_origin_note_counts_without_chip(self) -> None:
        origin = notes_store.create_note(1, "Откуда задача", "<p>текст</p>")
        found = sheets.find_ref_backlinks(1, "task", "7", origin_note_ids=[str(origin["id"]), ""])
        self.assertEqual([x["note_id"] for x in found], [origin["id"]])

    def test_rejects_unknown_kind(self) -> None:
        notes_store.create_note(1, "N", f"<p>{_chip('note:1')}</p>")
        self.assertEqual(sheets.find_ref_backlinks(1, "note", "1"), [])


if __name__ == "__main__":
    unittest.main()
