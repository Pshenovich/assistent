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


class BoardBlocksTests(unittest.TestCase):
    """Таблица (разметка таблицы заметки), оргкарточка и Гант."""

    TABLE = {
        "id": "t", "type": "table", "x": 0, "y": 0, "w": 300, "h": 100,
        "html": '<table class="note-editor-table" onclick="x"><colgroup><col style="width: 120px; background: url(x)"></colgroup>'
                '<tbody><tr><th colspan="1" style="text-align: center"><p>Этап<script>alert(1)</script></p></th><th><p>Кто</p></th></tr>'
                '<tr><td data-highlight="1"><p>Бриф</p></td><td><p><a href="javascript:alert(1)">Аня</a> <a href="https://x.ru">сайт</a></p></td></tr>'
                "</tbody></table><img src=x onerror=alert(1)>",
    }
    CARD = {"id": "c", "type": "card", "x": 0, "y": 300, "w": 220, "h": 230, "title": "CEO", "name": "Иван", "text": "Стратегия",
            "photo": "https://evil/x.png", "style": {"fill": "purple"}}
    GANTT = {"id": "g", "type": "gantt", "x": 0, "y": 700, "w": 900, "h": 200, "start": "2026-11-03", "unit": "day", "span": 14,
             "tasks": [{"id": "t1", "name": "Дизайн", "start": "2026-11-07", "end": "2026-11-05", "owner": "Аня", "color": "green"},
                       {"id": "t2", "name": "плохая дата", "start": "завтра"}]}

    def test_table_html_whitelisted(self) -> None:
        html = board_doc.parse_board(_board(self.TABLE))["elements"]["t"]["html"]
        self.assertTrue(html.startswith('<table class="note-editor-table">'))
        for bad in ("onclick", "script", "alert", "img", "javascript", "url("):
            self.assertNotIn(bad, html)
        self.assertIn('style="width: 120px"', html)
        self.assertIn('data-highlight="1"', html)
        self.assertIn('href="https://x.ru"', html)
        self.assertEqual(board_doc.table_rows(html), [["Этап", "Кто"], ["Бриф", "Аня сайт"]])

    def test_table_without_markup_gets_empty_grid(self) -> None:
        el = board_doc._clean_element("t", {"type": "table", "html": "<b>нет таблицы</b>"})
        self.assertEqual(board_doc.table_rows(el["html"]), [["", "", ""]] * 3)

    def test_card_and_gantt_cleaned(self) -> None:
        els = board_doc.parse_board(_board(self.CARD, self.GANTT))["elements"]
        self.assertNotIn("photo", els["c"])
        self.assertEqual((els["c"]["title"], els["c"]["name"], els["c"]["text"]), ("CEO", "Иван", "Стратегия"))
        g = els["g"]
        self.assertEqual(len(g["tasks"]), 1)
        self.assertEqual((g["tasks"][0]["start"], g["tasks"][0]["end"]), ("2026-11-05", "2026-11-07"))
        self.assertEqual(board_doc._clean_element("g", dict(self.GANTT, unit="month"))["unit"], "day")

    def test_blocks_text_describe_and_svg(self) -> None:
        body = _board(self.TABLE, self.CARD, self.GANTT)
        text = board_doc.board_plain_text(body)
        self.assertIn("Этап | Кто", text)
        self.assertIn("CEO — Иван: Стратегия", text)
        self.assertIn("Дизайн (Аня): 05.11–07.11", text)
        desc = board_doc.board_describe(body)
        self.assertIn("[таблица]", desc)
        self.assertIn("[диаграмма Ганта]", desc)
        svg = board_doc.render_board_svg(body)
        self.assertIn("Ответственный", svg)
        self.assertIn("Этап", svg)
        self.assertNotIn("<script", svg)

    def test_table_from_rows(self) -> None:
        html = board_doc.table_html_from_rows([["A", "B <i>"], ["1"]])
        self.assertEqual(board_doc.table_rows(html), [["A", "B <i>"], ["1", ""]])
        self.assertIn("<th", html)
        self.assertEqual(board_doc.clean_table_html(html), html)


class BoardExactColorsTests(unittest.TestCase):
    """Точные цвета (#rrggbb) и скругление — вставка из Miro / FigJam как в оригинале."""

    def test_hex_and_palette_kept_garbage_dropped(self) -> None:
        el = board_doc._clean_element("a", dict(A, style={
            "fill": "#305BAB", "stroke": "ink", "textColor": 'red" onload="x', "radius": 55, "strokeWidth": 0,
        }))
        self.assertEqual(el["style"], {"fill": "#305bab", "stroke": "ink", "strokeWidth": 0, "radius": 55})

    def test_svg_uses_hex_radius_and_no_border(self) -> None:
        body = _board(dict(A, style={"fill": "#305bab", "strokeWidth": 0, "radius": 30}),
                      dict(C, style={"fill": "#fff3a3"}),
                      {"id": "t", "type": "text", "x": 0, "y": 500, "w": 100, "h": 20, "text": "x", "style": {"fill": "#fe9f4d"}})
        svg = board_doc.render_board_svg(body)
        self.assertIn('rx="30"', svg)
        self.assertIn('fill="#305bab" stroke="none"', svg)
        self.assertIn('fill="#fff3a3"', svg)
        self.assertIn('fill="#fe9f4d"', svg)


class BoardVectorTests(unittest.TestCase):
    """Вектор (иконка / компонент из FigJam): только контуры и цвета, без произвольного SVG."""

    V = {"id": "v", "type": "vector", "x": 0, "y": 0, "w": 24, "h": 24, "vb": [24, 24], "paths": [
        {"d": "M0 0L24 24Z", "fill": "#1B2559", "rule": "evenodd"},
        {"d": "M1 1C2 2 3 3 4 4", "stroke": "ink", "sw": 2, "cap": "round", "join": "evil"},
        {"d": 'M0 0"/><script>alert(1)</script>', "fill": "#000000"},
        {"d": "M0 0L1 1", "fill": "url(#x)"},
    ]}

    def test_paths_cleaned(self) -> None:
        el = board_doc._clean_element("v", self.V)
        self.assertEqual(el["vb"], [24, 24])
        self.assertEqual(el["paths"], [
            {"d": "M0 0L24 24Z", "fill": "#1b2559", "rule": "evenodd"},
            {"d": "M1 1C2 2 3 3 4 4", "stroke": "ink", "sw": 2, "cap": "round"},
        ])
        self.assertIsNone(board_doc._clean_element("v", dict(self.V, paths=[])))
        self.assertIsNone(board_doc._clean_element("v", dict(self.V, vb="24")))

    def test_svg(self) -> None:
        svg = board_doc.render_board_svg(_board(self.V))
        self.assertIn('viewBox="0 0 24 24"', svg)
        self.assertIn('<path d="M0 0L24 24Z" fill="#1b2559" fill-rule="evenodd"/>', svg)
        self.assertNotIn("script", svg)


class BoardDesignModeTests(unittest.TestCase):
    """Лист «Макет» — доска в режиме design: экраны (frame) и их содержимое."""

    def test_mode_and_frames_kept(self) -> None:
        body = json.dumps({"v": 1, "mode": "design", "elements": {
            "f": {"id": "f", "type": "frame", "x": 0, "y": 0, "w": 393, "h": 852, "name": "Главная", "device": "iphone",
                  "style": {"fill": "#ffffff", "radius": 0}},
            "t": {"id": "t", "type": "text", "x": 10, "y": 10, "w": 100, "h": 20, "text": "Привет", "frame": "f"},
            "l": dict(L, frame="f"),
        }})
        board = board_doc.parse_board(body)
        self.assertEqual(board["mode"], "design")
        f = board["elements"]["f"]
        self.assertEqual((f["name"], f["device"], f["style"]["fill"]), ("Главная", "iphone", "#ffffff"))
        self.assertEqual(board["elements"]["t"]["frame"], "f")
        self.assertNotIn("frame", board["elements"]["l"])  # стрелки (переходы) — вне экранов
        free = board_doc.parse_board(json.dumps({"v": 1, "elements": {"f": {"id": "f", "type": "frame", "x": 0, "y": 0, "w": 393, "h": 852},
            "s": {"id": "s", "type": "line", "x": 0, "y": 0, "points": [[10, 10], [90, 10]], "frame": "f"}}}))
        self.assertEqual(free["elements"]["s"]["frame"], "f")  # свободная стрелка — часть экрана
        self.assertNotIn("mode", board_doc.parse_board(_board(A)))
        self.assertNotIn("mode", board_doc.parse_board(json.dumps({"v": 1, "mode": "evil", "elements": {}})))
        self.assertEqual(json.loads(board_doc.empty_board("design"))["mode"], "design")
        self.assertIn("Главная", board_doc.board_plain_text(body))
        svg = board_doc.render_board_svg(body)
        self.assertIn(">Главная</text>", svg)


class BoardLookTests(unittest.TestCase):
    """Макет: непрозрачность и тень («x y размытие #rrggbbaa»)."""

    def test_opacity_and_shadow(self) -> None:
        st = board_doc._clean_element("a", dict(A, style={"opacity": 0.5, "shadow": "0 4 12 #00000040"}))["style"]
        self.assertEqual(st, {"opacity": 0.5, "shadow": "0 4 12 #00000040"})
        st = board_doc._clean_element("a", dict(A, style={"opacity": 7, "shadow": "0 4 12 red;}"}))["style"]
        self.assertEqual(st, {"opacity": 1})


class BoardCornersAndNestingTests(unittest.TestCase):
    """Макет: скругление каждого угла отдельно и экран внутри экрана."""

    def test_radii(self) -> None:
        st = board_doc._clean_element("a", dict(A, style={"radii": "0 20 0 4.5"}))["style"]
        self.assertEqual(st, {"radii": "0 20 0 4.5"})
        self.assertNotIn("style", board_doc._clean_element("a", dict(A, style={"radii": "1 2 3"})))
        svg = board_doc.render_board_svg(_board(dict(A, style={"radii": "0 20 0 0"})))
        self.assertIn("A20.0 20.0 0 0 1", svg)

    def test_paint_style_keys(self) -> None:
        body = json.dumps({"v": 1, "elements": {"r": {"id": "r", "type": "rect", "x": 0, "y": 0, "w": 10, "h": 10, "style": {
            "fill": "#ff0000", "fillOpacity": 0.5, "fillOff": True, "strokeOff": "yes", "strokeAlign": "inside", "strokeOpacity": 7}}}})
        st = board_doc.parse_board(body)["elements"]["r"]["style"]
        self.assertEqual((st["fillOpacity"], st["fillOff"], st["strokeAlign"], st["strokeOpacity"]), (0.5, True, "inside", 1))
        self.assertNotIn("strokeOff", st)  # только True
        body = json.dumps({"v": 1, "elements": {"r": {"id": "r", "type": "rect", "x": 0, "y": 0, "w": 10, "h": 10, "style": {
            "fillGrad": "linear 90 #ff0000ff@0 #0000ff00@100", "strokeGrad": "bad", "blur": 4, "bgBlur": -3}}}})
        st = board_doc.parse_board(body)["elements"]["r"]["style"]
        self.assertEqual((st["fillGrad"], st["blur"]), ("linear 90 #ff0000ff@0 #0000ff00@100", 4))
        self.assertNotIn("strokeGrad", st)
        self.assertNotIn("bgBlur", st)
        body = json.dumps({"v": 1, "elements": {"t": {"id": "t", "type": "text", "x": 0, "y": 0, "w": 10, "h": 10, "text": "a", "style": {
            "fontFamily": "Golos Text", "fontWeight": 650, "lineHeight": 24, "letterSpacing": -2, "valign": "bottom", "deco": "x",
            "textCase": "upper"}}}})
        st = board_doc.parse_board(body)["elements"]["t"]["style"]
        self.assertEqual((st["fontFamily"], st["fontWeight"], st["lineHeight"], st["letterSpacing"], st["valign"], st["textCase"]),
                         ("Golos Text", 600, 24, -2, "bottom", "upper"))
        self.assertNotIn("deco", st)
        body = json.dumps({"v": 1, "elements": {"t": {"id": "t", "type": "text", "x": 0, "y": 0, "w": 10, "h": 10, "text": "a",
                                                    "style": {"valign": "middle"}}}})
        self.assertEqual(board_doc.parse_board(body)["elements"]["t"]["style"]["valign"], "middle")
        body = json.dumps({"v": 1, "elements": {
            "t": {"id": "t", "type": "text", "x": 0, "y": 0, "w": 10, "h": 40, "text": "a", "fixH": True},
            "a": {"id": "a", "type": "text", "x": 0, "y": 0, "w": 10, "h": 40, "text": "a", "fixH": True, "autoW": True},
            "r": {"id": "r", "type": "rect", "x": 0, "y": 0, "w": 10, "h": 40, "fixH": True}}})
        els = board_doc.parse_board(body)["elements"]
        self.assertEqual([els[k].get("fixH") for k in ("t", "a", "r")], [True, None, None])

    def test_components_fields(self) -> None:
        els = {
            "m": {"id": "m", "type": "frame", "x": 0, "y": 0, "w": 100, "h": 40, "comp": True, "name": "Кнопка"},
            "t": {"id": "t", "type": "text", "x": 0, "y": 0, "w": 50, "h": 20, "frame": "m", "text": "a", "comp": True},
            "i": {"id": "i", "type": "frame", "x": 200, "y": 0, "w": 100, "h": 40, "inst": "m", "ovr": ["geo", "style.fill", "bad key", 5]},
            "p": {"id": "p", "type": "text", "x": 200, "y": 0, "w": 50, "h": 20, "frame": "i", "msrc": "t", "ovr": ["text", "text"]},
            "q": {"id": "q", "type": "rect", "x": 0, "y": 0, "w": 5, "h": 5, "ovr": ["text"], "msrc": "q"},
        }
        out = board_doc.parse_board(json.dumps({"v": 1, "elements": els}))["elements"]
        self.assertTrue(out["m"]["comp"])
        self.assertNotIn("comp", out["t"])  # мастер — только фрейм
        self.assertEqual((out["i"]["inst"], out["i"]["ovr"]), ("m", ["geo", "style.fill"]))
        self.assertEqual((out["p"]["msrc"], out["p"]["ovr"]), ("t", ["text"]))
        self.assertNotIn("msrc", out["q"])  # ссылка на себя
        self.assertNotIn("ovr", out["q"])  # правки — только у экземпляра и его частей
        lib = {"id": "l", "type": "frame", "x": 0, "y": 0, "w": 1, "h": 1, "inst": "L7:emwc3gac6", "lv": "0a1b2c3d4e5f6789"}
        bad = dict(lib, id="k", lv="<script>")
        out = board_doc.parse_board(json.dumps({"v": 1, "elements": {"l": lib, "k": bad}}))["elements"]
        self.assertEqual((out["l"]["inst"], out["l"]["lv"]), ("L7:emwc3gac6", "0a1b2c3d4e5f6789"))
        self.assertNotIn("lv", out["k"])

    def test_frame_layout(self) -> None:
        good = {"id": "f", "type": "frame", "x": 0, "y": 0, "w": 100, "h": 100, "layout": "v 12 16 16 16 16 center start wh"}
        bad = dict(good, id="g", layout="v 12 16 left")
        els = board_doc.parse_board(json.dumps({"v": 1, "elements": {"f": good, "g": bad}}))["elements"]
        self.assertEqual(els["f"]["layout"], "v 12 16 16 16 16 center start wh")
        self.assertNotIn("layout", els["g"])
        grid = dict(good, id="h", layout="g 8 0 0 0 0 start start h c3")
        kid = {"id": "k", "type": "rect", "x": 0, "y": 0, "w": 10, "h": 10, "frame": "f", "grow": "w", "ratio": True}
        els = board_doc.parse_board(json.dumps({"v": 1, "elements": {"f": good, "h": grid, "k": kid}}))["elements"]
        self.assertEqual(els["h"]["layout"], "g 8 0 0 0 0 start start h c3")
        self.assertEqual((els["k"]["grow"], els["k"]["ratio"]), ("w", True))

    def test_pin_constraints(self) -> None:
        def pin(v: str):
            el = {"id": "k", "type": "rect", "x": 0, "y": 0, "w": 10, "h": 10, "pin": v}
            return board_doc.parse_board(json.dumps({"v": 1, "elements": {"k": el}}))["elements"]["k"].get("pin")
        self.assertEqual(pin("r b"), "r b")
        self.assertEqual(pin("lr tb"), "lr tb")
        self.assertIsNone(pin("l t"))  # по умолчанию — не пишется
        self.assertIsNone(pin("bottom"))

    def test_hidden_and_locked(self) -> None:
        body = json.dumps({"v": 1, "elements": {
            "f": {"id": "f", "type": "frame", "x": 0, "y": 0, "w": 100, "h": 100, "hidden": True, "locked": True},
            "t": {"id": "t", "type": "text", "x": 10, "y": 10, "w": 80, "h": 20, "text": "Секрет", "frame": "f"}}})
        els = board_doc.parse_board(body)["elements"]
        self.assertEqual((els["f"]["hidden"], els["f"]["locked"]), (True, True))
        self.assertNotIn("Секрет", board_doc.render_board_svg(body))  # содержимое скрытого фрейма не рисуется

    def test_nested_frame_and_self_ref(self) -> None:
        outer = {"id": "f1", "type": "frame", "x": 0, "y": 0, "w": 400, "h": 800}
        inner = {"id": "f2", "type": "frame", "x": 10, "y": 10, "w": 100, "h": 100, "frame": "f1"}
        loop = {"id": "f3", "type": "frame", "x": 10, "y": 10, "w": 100, "h": 100, "frame": "f3"}
        els = board_doc.parse_board(_board(outer, inner, loop))["elements"]
        self.assertEqual(els["f2"]["frame"], "f1")
        self.assertNotIn("frame", els["f3"])

    def test_flow_shapes_kept_and_drawn(self) -> None:
        els = [{"id": t, "type": t, "x": i * 220, "y": 0, "w": 200, "h": 120, "text": "Шаг " + t}
               for i, t in enumerate(board_doc.FLOW_SHAPES)]
        body = _board(*els)
        parsed = board_doc.parse_board(body)["elements"]
        self.assertEqual(sorted(e["type"] for e in parsed.values()), sorted(board_doc.FLOW_SHAPES))
        svg = board_doc.render_board_svg(body)
        self.assertEqual(svg.count('stroke-linejoin="round"'), len(board_doc.FLOW_SHAPES))
        self.assertIn("Шаг cylinder", svg)
        self.assertIn("цилиндр", board_doc.board_describe(body))

    def test_text_color_follows_fill(self) -> None:
        def color(style):
            body = _board({"id": "a", "type": "rect", "x": 0, "y": 0, "w": 100, "h": 50, "text": "Т", "style": style})
            return board_doc.render_board_svg(body).split('<text font-size="16" fill="')[1].split('"')[0]
        self.assertEqual(color({"fill": "#7048e8"}), "#ffffff")  # тёмная заливка — белый текст
        self.assertEqual(color({"fill": "yellow"}), "#1f2328")  # светлая — тёмный
        self.assertEqual(color({"fill": "#7048e8", "textColor": "red"}), "#e03131")  # выбран вручную — как есть
        self.assertEqual(board_doc.auto_text_color({}, None), None)
