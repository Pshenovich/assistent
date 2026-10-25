import json
import os
import tempfile
import unittest

from assistant.lib import board_doc
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store


def _board(*elements: dict) -> str:
    return json.dumps({"v": 1, "elements": {e["id"]: e for e in elements}}, ensure_ascii=False)


A = {"id": "a", "type": "rect", "x": 0, "y": 0, "w": 120, "h": 60, "text": "Заявка"}
B = {"id": "b", "type": "diamond", "x": 300, "y": 0, "w": 120, "h": 80, "text": "Одобрить?"}
C = {"id": "c", "type": "sticky", "x": 0, "y": 300, "w": 160, "h": 160, "text": "Идея <b>"}
L = {
    "id": "l", "type": "line", "x": 0, "y": 0, "points": [[120, 30], [300, 40]],
    "start": {"el": "a", "side": "right"}, "end": {"el": "b", "side": "left"}, "arrowEnd": True,
}


class BoardDocTests(unittest.TestCase):
    def test_plain_text_order_and_links(self) -> None:
        text = board_doc.board_plain_text(_board(C, B, A, L))
        lines = text.split("\n")
        self.assertEqual(lines[:3], ["Заявка", "Одобрить?", "Идея <b>"])
        self.assertIn("Заявка → Одобрить?", text)

    def test_parse_drops_unknown_and_bad_images(self) -> None:
        board = board_doc.parse_board(_board(
            A,
            {"id": "x", "type": "script", "x": 0, "y": 0},
            {"id": "i", "type": "image", "x": 0, "y": 0, "w": 10, "h": 10, "src": "https://evil/x.png"},
            {"id": "j", "type": "image", "x": 0, "y": 0, "w": 10, "h": 10, "src": "data:image/png;base64,AAAA"},
        ))
        self.assertEqual(sorted(board["elements"]), ["a", "j"])

    def test_line_path_whitelist(self) -> None:
        bad = dict(L, d='M0,0"/><script>')
        self.assertNotIn("d", board_doc.parse_board(_board(bad))["elements"]["l"])
        good = dict(L, d="M120,30 C200,30 220,40 300,40")
        self.assertEqual(board_doc.parse_board(_board(good))["elements"]["l"]["d"], good["d"])

    def test_normalize_rejects_garbage(self) -> None:
        with self.assertRaises(board_doc.BoardError):
            board_doc.normalize_board_body("<p>not json</p>")
        with self.assertRaises(board_doc.BoardError):
            board_doc.normalize_board_body("[1,2]")
        self.assertEqual(json.loads(board_doc.normalize_board_body(""))["elements"], {})

    def test_svg_escapes_text(self) -> None:
        svg = board_doc.render_board_svg(_board(A, C, L))
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Идея &lt;b&gt;", svg)
        self.assertIn("marker-end", svg)


class BoardSheetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_create_and_update_board(self) -> None:
        note = notes_store.create_note(1, "Схема", "<p>текст</p>")
        sh = sheets.create_sheet(note, "Холст", kind="board")
        self.assertEqual(sh["kind"], "board")
        self.assertEqual(json.loads(sh["body"])["elements"], {})
        upd = sheets.update_sheet(note["id"], sh["id"], body=_board(A, B, L), expected_revision=1)
        self.assertEqual(set(json.loads(upd["body"])["elements"]), {"a", "b", "l"})
        # Без текста — не «пустое тело затирает»: холст можно очистить.
        empty = sheets.update_sheet(note["id"], sh["id"], body=_board())
        self.assertEqual(json.loads(empty["body"])["elements"], {})
        with self.assertRaises(sheets.SheetError):
            sheets.update_sheet(note["id"], sh["id"], body="<p>html</p>")

    def test_board_not_promoted_to_primary_and_shared_separately(self) -> None:
        note = notes_store.create_note(2, "Схема", "<p>основная</p>")
        board = sheets.create_sheet(note, "Холст", body=_board(A), kind="board")
        note = notes_store.get_note(2, note["id"])
        body, _ = sheets.compose_share_body(note, ["main", str(board["id"])])
        self.assertNotIn("Заявка", body)
        shared = sheets.list_share_boards(note, ["main", str(board["id"])])
        self.assertEqual(len(shared), 1)
        self.assertIn("a", shared[0]["board"]["elements"])
        with self.assertRaises(sheets.SheetError):
            sheets.delete_primary(2, note)


if __name__ == "__main__":
    unittest.main()
