"""Доска в тексте документа (div[data-leo-board]): текст доски в экспорте/MCP/контексте и права на чужие доски."""

import json
import os
import tempfile
import unittest

from assistant.lib import board_doc
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store

BOARD = json.dumps(
    {"v": 1, "elements": {"a": {"id": "a", "type": "rect", "x": 0, "y": 0, "w": 120, "h": 60, "text": "Юрист"}}}
)


def _embed(nid, sid, title="Схема"):
    return (
        f'<div data-leo-board="{nid}:{sid}" data-height="320" class="note-board-embed" '
        f'contenteditable="false" data-title="{title}">Доска «{title}»</div>'
    )


class BoardEmbedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_refs_and_expand_same_note(self) -> None:
        note = notes_store.create_note(1, "Договор", "<p>план</p>")
        sh = sheets.create_sheet(note, "Схема", body=BOARD, kind="board")
        html = "<p>до</p>" + _embed(note["id"], sh["id"]) + "<p>после</p>"
        self.assertEqual(board_doc.board_embed_refs(html), [(int(note["id"]), int(sh["id"]))])
        out = board_doc.expand_board_embeds(html, sheets.board_embed_resolver(note["id"]))
        self.assertIn("Доска «Схема»", out)
        self.assertIn("Юрист", out)
        self.assertNotIn("data-leo-board", out)
        self.assertTrue(out.startswith("<p>до</p>") and out.endswith("<p>после</p>"))

    def test_foreign_board_needs_reader(self) -> None:
        other = notes_store.create_note(2, "Чужой", "<p>x</p>")
        sh = sheets.create_sheet(other, "Секрет", body=BOARD, kind="board")
        mine = notes_store.create_note(1, "Мой", _embed(other["id"], sh["id"], "Секрет"))
        # Без зрителя и без доступа — только подпись, содержимое не утекает.
        for resolver in (sheets.board_embed_resolver(mine["id"]), sheets.board_embed_resolver(mine["id"], 1)):
            out = board_doc.expand_board_embeds(mine["body"], resolver)
            self.assertNotIn("Юрист", out)
            self.assertIn("Доска «Секрет»", out)
        # Владелец чужого документа видит.
        out = board_doc.expand_board_embeds(mine["body"], sheets.board_embed_resolver(mine["id"], 2))
        self.assertIn("Юрист", out)

    def test_share_embeds_only_same_note(self) -> None:
        note = notes_store.create_note(1, "Договор", "")
        own = sheets.create_sheet(note, "Схема", body=BOARD, kind="board")
        other = notes_store.create_note(1, "Другой", "")
        far = sheets.create_sheet(other, "Далёкая", body=BOARD, kind="board")
        body = _embed(note["id"], own["id"]) + _embed(other["id"], far["id"])
        got = sheets.share_board_embeds(note, body)
        self.assertEqual(list(got), [f"{note['id']}:{own['id']}"])
        self.assertEqual(got[f"{note['id']}:{own['id']}"]["title"], "Схема")

    def test_doc_sheet_is_not_a_board(self) -> None:
        note = notes_store.create_note(1, "Договор", "")
        doc = sheets.create_sheet(note, "Лист", body="<p>текст</p>")
        out = board_doc.expand_board_embeds(_embed(note["id"], doc["id"], "Лист"), sheets.board_embed_resolver(note["id"]))
        self.assertIn("[Доска «Лист»]", out)


    def test_design_and_deck(self) -> None:
        note = notes_store.create_note(1, "Продукт", "")
        design = json.loads(BOARD)
        design["mode"] = "design"
        mk = sheets.create_sheet(note, "Экраны", body=json.dumps(design), kind="board")
        deck_html = (
            "<!DOCTYPE html><html><head><style>.x{}</style><script>var a=1</script></head><body><div class=\"deck\">"
            '<section class="slide"><h1>Цели</h1><p>Рост</p></section>'
            '<section class="slide"><h1>План</h1></section></div></body></html>'
        )
        dk = sheets.create_sheet(note, "Питч", body=deck_html, kind="web")
        body = (
            f'<div data-leo-board="{note["id"]}:{mk["id"]}" data-kind="design">Макет «Экраны»</div>'
            f'<div data-leo-deck="{note["id"]}:{dk["id"]}">Презентация «Питч»</div>'
        )
        self.assertEqual(len(board_doc.board_embed_refs(body)), 2)
        out = board_doc.expand_board_embeds(body, sheets.board_embed_resolver(note["id"]))
        self.assertIn("Макет «Экраны»", out)
        self.assertIn("Презентация «Питч»", out)
        self.assertIn("Слайд 1: Цели Рост", out)
        self.assertIn("Слайд 2: План", out)
        self.assertNotIn("var a", out)
        # Рамка-презентация на лист-доску (и наоборот) — только подпись.
        wrong = f'<div data-leo-deck="{note["id"]}:{mk["id"]}">Презентация «?»</div>'
        self.assertIn("[Презентация «?»]", board_doc.expand_board_embeds(wrong, sheets.board_embed_resolver(note["id"])))
        got = sheets.share_board_embeds(note, body)
        self.assertIn("board", got[f"{note['id']}:{mk['id']}"])
        self.assertIn("Цели", got[f"{note['id']}:{dk['id']}"]["html"])


if __name__ == "__main__":
    unittest.main()
