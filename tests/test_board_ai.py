import json
import os
import tempfile
import unittest

from assistant.lib import board_ai, board_doc
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store

FLOW = {
    "action": "create", "title": "Заявка", "layout": "flow",
    "nodes": [
        {"id": "n1", "text": "Заявка пришла", "shape": "ellipse", "color": "green"},
        {"id": "n2", "text": "Проверить данные"},
        {"id": "n3", "text": "Данные полные?", "shape": "diamond"},
        {"id": "n4", "text": "Запросить недостающее"},
        {"id": "n5", "text": "Передать в работу", "color": "blue"},
    ],
    "edges": [
        {"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"},
        {"from": "n3", "to": "n5", "label": "да"}, {"from": "n3", "to": "n4", "label": "нет"},
        {"from": "n4", "to": "n2", "dashed": True},
    ],
}


def _els(body):
    return board_doc.parse_board(body)["elements"]


def _overlap(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


class BoardAiTests(unittest.TestCase):
    def test_parse_strips_block(self):
        text = 'Готово.\n```leo-board\n{"action":"create","nodes":[{"id":"a","text":"X"}],}\n```\nНарисовал.'
        clean, acts, tried = board_ai.parse_blocks(text)
        self.assertTrue(tried)
        self.assertEqual(len(acts), 1)
        self.assertNotIn("leo-board", clean)
        self.assertIn("Нарисовал", clean)

    def test_flow_layout_layers_and_attached_arrows(self):
        els = _els(board_ai.build_board(FLOW))
        shapes = {e["text"]: e for e in els.values() if e["type"] != "line"}
        lines = [e for e in els.values() if e["type"] == "line"]
        self.assertEqual(len(shapes), 5)
        self.assertEqual(len(lines), 5)
        # Ярусы сверху вниз; блоки не перекрываются.
        self.assertLess(shapes["Заявка пришла"]["y"], shapes["Проверить данные"]["y"])
        self.assertLess(shapes["Проверить данные"]["y"], shapes["Данные полные?"]["y"])
        self.assertLess(shapes["Данные полные?"]["y"], shapes["Передать в работу"]["y"])
        vals = list(shapes.values())
        for i, a in enumerate(vals):
            for b in vals[i + 1:]:
                self.assertFalse(_overlap(a, b), (a["text"], b["text"]))
        self.assertEqual(shapes["Данные полные?"]["type"], "diamond")
        self.assertEqual(shapes["Передать в работу"]["style"]["fill"], "blue")
        ids = {e["id"] for e in shapes.values()}
        for ln in lines:
            self.assertIn(ln["start"]["el"], ids)
            self.assertIn(ln["end"]["el"], ids)
        down = [ln for ln in lines if ln["start"].get("side") == "bottom"]
        self.assertGreaterEqual(len(down), 3)
        self.assertTrue(any(ln.get("text") == "да" for ln in lines))
        back = [ln for ln in lines if ln.get("style", {}).get("dash") == "dashed"]
        self.assertEqual(len(back), 1)  # обратная связь цикла — пунктир «к объекту»

    def test_mindmap_both_sides_and_grid(self):
        mm = {"action": "create", "layout": "mindmap", "nodes": [{"id": "c", "text": "Продукт"}] + [
            {"id": f"b{i}", "text": f"Ветка {i}"} for i in range(4)
        ], "edges": [{"from": "c", "to": f"b{i}"} for i in range(4)]}
        els = _els(board_ai.build_board(mm))
        center = next(e for e in els.values() if e.get("text") == "Продукт")
        cx = center["x"] + center["w"] / 2
        xs = [e["x"] + e["w"] / 2 for e in els.values() if e.get("text", "").startswith("Ветка")]
        self.assertTrue(any(x > cx for x in xs) and any(x < cx for x in xs))
        grid = _els(board_ai.build_board({"action": "create", "nodes": [{"id": str(i), "text": f"Идея {i}"} for i in range(6)]}))
        self.assertTrue(all(e["type"] == "sticky" for e in grid.values()))

    def test_update_edits_adds_and_removes(self):
        body = board_ai.build_board(FLOW)
        els = _els(body)
        by_text = {e.get("text"): e["id"] for e in els.values() if e["type"] != "line"}
        upd = board_ai.update_board(body, {
            "action": "update",
            "nodes": [{"id": by_text["Проверить данные"], "text": "Проверить анкету"}, {"id": "x1", "text": "Уведомить клиента"}],
            "edges": [{"from": by_text["Передать в работу"], "to": "x1"}],
            "remove": [by_text["Запросить недостающее"]],
        })
        after = _els(upd)
        texts = {e.get("text") for e in after.values() if e["type"] != "line"}
        self.assertIn("Проверить анкету", texts)
        self.assertIn("Уведомить клиента", texts)
        self.assertNotIn("Запросить недостающее", texts)
        ids = set(after)
        for ln in (e for e in after.values() if e["type"] == "line"):
            self.assertIn(ln["start"]["el"], ids)
            self.assertIn(ln["end"]["el"], ids)
        # Ручные изломы после перестройки устарели — их нет.
        doc = json.loads(upd)
        for e in doc["elements"].values():
            if e["type"] == "line":
                e["waypoints"] = [[1, 2], [3, 4]]
        upd = json.dumps(doc, ensure_ascii=False)
        relaid = _els(board_ai.update_board(upd, {"action": "update", "relayout": True, "layout": "flow-right"}))
        self.assertFalse([e for e in relaid.values() if e.get("waypoints")])
        boxes = [e for e in relaid.values() if e["type"] != "line"]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertFalse(_overlap(a, b))

    def test_prompt_block_lists_nodes(self):
        block = board_ai.open_board_prompt_block("7", "Схема", board_ai.build_board(FLOW))
        self.assertIn("sheet_id=7", block)
        self.assertIn("Данные полные?", block)
        self.assertIn("→", block)


class BoardAiApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self):
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_create_then_update_open_board(self):
        note = notes_store.create_note(1, "N", "<p>x</p>")
        sh = board_ai.apply_action(note, FLOW, sheets)
        self.assertEqual(sh["kind"], "board")
        self.assertEqual(sh["title"], "Заявка")
        upd = board_ai.apply_action(note, {"action": "update", "nodes": [{"id": "z", "text": "Новый шаг"}]}, sheets, open_sheet_id=str(sh["id"]))
        self.assertEqual(upd["id"], sh["id"])
        self.assertIn("Новый шаг", json.dumps(json.loads(upd["body"]), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()


class BoardIntentTests(unittest.TestCase):
    def test_board_request_is_not_image(self):
        self.assertTrue(board_ai.looks_like_board_request("Нарисуй блок-схему процесса на доске"))
        self.assertTrue(board_ai.looks_like_board_request("сделай mind map по заметке"))
        self.assertFalse(board_ai.looks_like_board_request("нарисуй картинку со схемой"))
        self.assertFalse(board_ai.looks_like_board_request("нарисуй кота"))


class BoardInsertTests(unittest.TestCase):
    def test_new_node_goes_next_to_its_neighbor(self):
        body = board_ai.build_board(FLOW)
        els = _els(body)
        ids = {e.get("text"): e["id"] for e in els.values() if e["type"] != "line"}
        deal = els[ids["Передать в работу"]]
        upd = _els(board_ai.update_board(body, {
            "action": "update", "nodes": [{"id": "x", "text": "Отправить договор"}],
            "edges": [{"from": ids["Передать в работу"], "to": "x"}],
        }))
        new = next(e for e in upd.values() if e.get("text") == "Отправить договор")
        self.assertGreater(new["y"], deal["y"] + deal["h"])  # под соседом
        self.assertLess(abs((new["x"] + new["w"] / 2) - (deal["x"] + deal["w"] / 2)), 400)
        boxes = [e for e in upd.values() if e["type"] != "line"]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertFalse(_overlap(a, b))


class BoardBlockTextTests(unittest.TestCase):
    def test_prompt_lists_blocks(self):
        p = board_ai.block_text_prompt("короче", [{"id": "a1", "text": "Длинный шаг"}, {"id": "", "text": "x"}], "Доска")
        self.assertIn('"id": "a1"', p)
        self.assertIn("короче", p)
        self.assertIn("Доска", p)
        self.assertNotIn('"text": "x"', p)

    def test_parse_keeps_only_known_ids(self):
        ans = 'Вот:\n{"blocks": [{"id": "a1", "text": " Шаг "}, {"id": "zz", "text": "чужой"}, {"id": "a2", "text": ""}]}'
        self.assertEqual(board_ai.parse_block_texts(ans, ["a1", "a2"]), {"a1": "Шаг"})
        self.assertEqual(board_ai.parse_block_texts("нет json", ["a1"]), {})
        self.assertEqual(board_ai.parse_block_texts('{"blocks": "x"}', ["a1"]), {})
