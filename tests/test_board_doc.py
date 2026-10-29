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
    def test_line_waypoints_cleaned(self) -> None:
        raw = dict(L, waypoints=[[200, 30], ["x", 1], [200.123, "40"], *([[1, 1]] * 50)])
        el = board_doc._clean_element("l", raw)
        wps = el["waypoints"]
        self.assertEqual(len(wps), board_doc.MAX_WAYPOINTS)
        self.assertEqual(wps[0], [200, 30])
        self.assertEqual(wps[2], [200.12, 40])
        self.assertNotIn("waypoints", board_doc._clean_element("l", dict(L, waypoints="bad")))
        self.assertEqual(board_doc._clean_element("l", dict(L, labelT=0.3))["labelT"], 0.3)
        self.assertEqual(board_doc._clean_element("l", dict(L, labelT=7))["labelT"], 1)
        self.assertNotIn("labelT", board_doc._clean_element("l", L))

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

    def test_sticky_author_kept_and_described(self) -> None:
        raw = dict(C, author={"id": 123456789, "name": "Вова П", "x": "мусор"})
        el = board_doc._clean_element("c", raw)
        self.assertEqual(el["author"], {"id": "123456789", "name": "Вова П"})
        self.assertNotIn("author", board_doc._clean_element("a", dict(A, author={"name": "Вова"})))
        self.assertNotIn("author", board_doc._clean_element("c", dict(C, author={"name": " "})))
        self.assertIn("автор Вова П", board_doc.board_describe(_board(raw)))

    def test_svg_text_align(self) -> None:
        right = dict(A, style={"align": "right"})
        self.assertIn('text-anchor="end"', board_doc.render_board_svg(_board(right)))
        self.assertIn('text-anchor="middle"', board_doc.render_board_svg(_board(A)))
        text = {"id": "t", "type": "text", "x": 0, "y": 0, "w": 100, "h": 20, "text": "x"}
        self.assertIn('text-anchor="start"', board_doc.render_board_svg(_board(text)))


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


class BoardDescribeTests(unittest.TestCase):
    def test_describe_shapes_links_and_extras(self):
        pen = {"id": "p", "type": "pen", "x": 0, "y": 0, "points": [[0, 0], [5, 5]]}
        lab = dict(L, text="да", style={"dash": "dashed"})
        text = board_doc.board_describe(_board(A, B, C, lab, pen), "Процесс")
        self.assertIn("Доска «Процесс»", text)
        self.assertIn("[ромб/решение] Одобрить?", text)
        self.assertIn("[стикер] Идея <b>", text)
        self.assertIn("#1 «Заявка» → #2 «Одобрить?» («да», пунктир)", text)
        self.assertIn("рисунков карандашом: 1", text)
        self.assertEqual(board_doc.board_describe(""), "Доска пустая.")
