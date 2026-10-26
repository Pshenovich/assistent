"""Свободный слайд (free.canvas): проверка элементов, отрисовка во всех стилях, операции, правка текста с разметкой."""

from __future__ import annotations

import copy
import unittest

from assistant.lib import canvas_free as cf
from assistant.lib import canvas_templates as tpl


def _deck(*slides, theme="analytic"):
    return {"v": tpl.DECK_VERSION, "title": "T", "theme": theme,
            "slides": [dict(s, id=s.get("id") or f"s{i + 1}") for i, s in enumerate(slides)]}


ELEMENTS = [
    {"id": "t1", "type": "text", "x": 80, "y": 60, "w": 800, "h": 120, "z": 2,
     "html": "Рост <b>выручки</b><script>alert(1)</script>", "style": {"size": "xxl", "font": "display", "color": "text"}},
    {"id": "sh1", "type": "shape", "shape": "round", "x": 60, "y": 300, "w": 400, "h": 300, "z": 1, "style": {"fill": "grad", "radius": 24}},
    {"id": "ic1", "type": "icon", "icon": "brain", "x": 100, "y": 320, "w": 48, "h": 48, "z": 3},
    {"id": "im1", "type": "image", "x": 700, "y": 300, "w": 480, "h": 320, "z": 2, "mkey": "media"},
    {"id": "ob1", "type": "object", "visual": "orb", "x": 500, "y": 300, "w": 180, "h": 180, "z": 2},
    {"id": "ch1", "type": "chart", "ctype": "bar-v", "x": 80, "y": 620, "w": 600, "h": 90, "z": 2,
     "chart": {"x": ["A", "B"], "series": [{"values": [1, 2]}]}},
    {"type": "nope", "x": 1},
]


class FreeSlideTests(unittest.TestCase):
    def test_clean_elements_and_sanitizer(self):
        els = cf.clean_elements(ELEMENTS)
        self.assertEqual([e["type"] for e in els], ["text", "shape", "icon", "image", "object", "chart"])
        self.assertEqual(els[0]["html"], "Рост <b>выручки</b>")
        self.assertEqual(cf.sanitize_html('<div>a</div><div><span class="acc" onclick="x">b</span></div><p style="x">c</p>'),
                         'a<br><span class="acc">b</span><br>c')
        self.assertEqual(cf.sanitize_html("<strong>x</strong><em>y</em>"), "<b>x</b><i>y</i>")
        self.assertEqual(len(cf.clean_elements([{"type": "text", "html": "x"}] * 500)), cf.MAX_ELEMENTS)
        big = cf.clean_element({"type": "text", "x": 99999, "w": -5, "style": {"size": "huge", "color": "red"}}, set())
        self.assertEqual((big["x"], big["w"], big["style"]["size"], big["style"]["color"]), (1920.0, 4.0, "m", "text"))
        self.assertIsNone(cf.clean_element({"type": "icon", "icon": "no_such"}, set()))

    def test_renders_in_every_theme(self):
        for theme in ("analytic", "mono", "lime"):
            deck = _deck({"role": "free", "variant": "free.canvas", "slots": {"surface": "dark", "elements": ELEMENTS}}, theme=theme)
            html = tpl.render_deck_html(deck, {})
            self.assertNotIn("Не удалось отрисовать", html)
            self.assertIn('class="fr-slide fr-dark"', html)
            self.assertIn('data-slot="free.t1.html"', html)
            self.assertIn("left:80.0px;top:60.0px;width:800.0px;height:120.0px", html)
            self.assertIn('data-icon="brain"', html)
            self.assertIn('data-media="s1-media"', html)
            self.assertIn('data-a3="orb"', html)
            self.assertIn('class="chart', html)
            self.assertNotIn("<script>alert", html)

    def test_detach_update_reattach(self):
        slots = {"title": "Три шага", "goals": ["a", "b"]}
        deck = _deck({"role": "cover", "variant": "cover.goals", "slots": copy.deepcopy(slots)})
        tpl.apply_op(deck, {"op": "detach", "slide_id": "s1", "elements": ELEMENTS[:2], "surface": "dark"})
        s = deck["slides"][0]
        self.assertEqual((s["role"], s["variant"], s["slots"]["surface"]), ("free", "free.canvas", "dark"))
        self.assertEqual(s["slots"]["origin"]["slots"], slots)
        with self.assertRaises(tpl.DeckOpError):
            tpl.apply_op(deck, {"op": "detach", "slide_id": "s1", "elements": ELEMENTS})
        tpl.apply_op(deck, {"op": "free_update", "slide_id": "s1", "elements": ELEMENTS[:1], "surface": "accent"})
        self.assertEqual(len(s["slots"]["elements"]), 1)
        self.assertEqual(s["slots"]["surface"], "accent")
        self.assertIn("origin", s["slots"])
        # Сохранение и перерисовка не теряют исходный макет.
        again = tpl.extract_deck(tpl.render_deck_html(deck, {}))
        self.assertEqual(again["slides"][0]["slots"]["origin"]["variant"], "cover.goals")
        tpl.apply_op(again, {"op": "reattach", "slide_id": "s1"})
        self.assertEqual((again["slides"][0]["variant"], again["slides"][0]["slots"]), ("cover.goals", slots))
        with self.assertRaises(tpl.DeckOpError):
            tpl.apply_op(again, {"op": "reattach", "slide_id": "s1"})

    def test_add_blank_slide(self):
        deck = _deck({"role": "cover", "variant": "cover.minimal", "slots": {"title": "A"}}, {"role": "cover", "variant": "cover.minimal", "slots": {"title": "B"}})
        tpl.apply_op(deck, {"op": "add_free_slide", "slide_id": "s1", "surface": "light"})
        self.assertEqual([s["variant"] for s in deck["slides"]][:2], ["cover.minimal", "free.canvas"])
        self.assertEqual(deck["slides"][1]["slots"]["elements"][0]["html"], "Заголовок")
        deck["slides"] = [copy.deepcopy(deck["slides"][0]) for _ in range(tpl.MAX_DECK_SLIDES)]
        with self.assertRaises(tpl.DeckOpError):
            tpl.apply_op(deck, {"op": "add_free_slide", "slide_id": "s1"})

    def test_inline_rich_text_edit_survives(self):
        deck = _deck({"role": "free", "variant": "free.canvas", "slots": {"elements": ELEMENTS[:1]}})
        html = tpl.render_deck_html(deck, {})
        edited = html.replace("Рост <b>выручки</b>", 'Рост <b>выручки</b> и <span class="acc">маржи</span><div>вторая строка</div>', 1)
        d = tpl.extract_deck(tpl.refinalize(edited))
        self.assertEqual(d["slides"][0]["slots"]["elements"][0]["html"],
                         'Рост <b>выручки</b> и <span class="acc">маржи</span><br>вторая строка')

    def test_theme_switch_keeps_positions_and_media(self):
        deck = _deck({"role": "free", "variant": "free.canvas", "slots": {"elements": ELEMENTS}})
        media = {"s1-media": "data:image/png;base64,AAAA", "s1-media#own": "1"}
        html = tpl.render_deck_html(deck, media)
        d = tpl.extract_deck(html)
        tpl.apply_op(d, {"op": "theme", "theme": "lime"})
        lime = tpl.render_deck_html(d, tpl.extract_media(html))
        self.assertIn("left:700.0px;top:300.0px", lime)
        self.assertIn("data:image/png;base64,AAAA", lime)
        self.assertEqual(tpl.extract_deck(lime)["slides"][0]["slots"]["elements"][0]["x"], 80.0)

    def test_model_update_of_free_slide_keeps_origin(self):
        deck = _deck({"role": "cover", "variant": "cover.goals", "slots": {"title": "A", "goals": ["x"]}})
        tpl.apply_op(deck, {"op": "detach", "slide_id": "s1", "elements": ELEMENTS[:1]})
        new_els = copy.deepcopy(deck["slides"][0]["slots"]["elements"])
        new_els[0]["html"] = "Новый текст"
        tpl.apply_slide_updates(deck, {"slides": [{"id": "s1", "slots": {"elements": new_els}}]})
        s = deck["slides"][0]
        self.assertEqual((s["variant"], s["slots"]["elements"][0]["html"]), ("free.canvas", "Новый текст"))
        self.assertIn("origin", s["slots"])


if __name__ == "__main__":
    unittest.main()
