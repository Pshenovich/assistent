import copy
import os
import tempfile
import unittest

from assistant.lib import canvas_charts as charts
from assistant.lib import canvas_templates as tpl
from assistant.lib import web_canvas as wc
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store
from tests.canvas_sample_deck import SAMPLE_ACTION, SAMPLE_ACTION_2


def _deck(*slides):
    return tpl.build_deck({"title": "T", "slides": list(slides)})


class CatalogTests(unittest.TestCase):
    def test_every_variant_renders_from_samples(self) -> None:
        seen = set()
        for action in (SAMPLE_ACTION, SAMPLE_ACTION_2):
            deck = tpl.build_deck(action)
            html = tpl.render_deck_html(deck)
            self.assertNotIn("Не удалось отрисовать", html)
            seen |= {s["variant"] for s in deck["slides"]}
        # Every non-chart variant + the main chart types are exercised.
        missing = {v for v in tpl.VARIANTS if not v.startswith("chart.")} - seen
        self.assertEqual(missing, {"closing.ask", "appendix.notes"} & missing)

    def test_explicit_variant_kept_when_data_fits(self) -> None:
        deck = _deck({"role": "cover", "variant": "cover.minimal", "slots": {"title": "A", "goals": ["x", "y"]}})
        self.assertEqual(deck["slides"][0]["variant"], "cover.minimal")

    def test_variant_picked_from_data_shape(self) -> None:
        deck = _deck(
            {"role": "cover", "slots": {"title": "A", "hero_stat": {"value": "61%", "label": "x"}}},
            {"role": "insight", "slots": {"quote": "Цитата", "who": "Клиент"}},
            {"role": "metrics", "slots": {"kpis": [{"label": "a", "value": "1"}]}},
        )
        self.assertEqual([s["variant"] for s in deck["slides"]], ["cover.split-stat", "insight.quote", "metrics.hero"])

    def test_unknown_variant_and_role_fall_back(self) -> None:
        deck = _deck({"role": "weird", "variant": "nope", "slots": {"title": "A", "items": [{"title": "x", "text": "y"}]}})
        self.assertEqual(deck["slides"][0]["role"], "content")
        self.assertIn(deck["slides"][0]["variant"], tpl.VARIANTS)

    def test_groups_are_clipped_to_max(self) -> None:
        deck = _deck({"role": "cover", "slots": {"title": "A", "goals": [str(i) for i in range(9)]}})
        self.assertEqual(len(deck["slides"][0]["slots"]["goals"]), 4)

    def test_card_grid_adapts_to_count(self) -> None:
        for n, cols in ((1, 1), (2, 2), (3, 3), (5, 3), (6, 3)):
            items = [{"title": f"t{i}", "text": "short"} for i in range(n)]
            html = tpl.render_deck_html(_deck({"role": "content", "variant": "content.cards", "slots": {"title": "A", "items": items}}))
            self.assertIn(f"grid cols-{cols}", html, n)
        long_items = [{"title": "t", "text": "x" * 120} for _ in range(4)]
        html = tpl.render_deck_html(_deck({"role": "content", "variant": "content.cards", "slots": {"title": "A", "items": long_items}}))
        self.assertIn("grid cols-2", html)

    def test_slot_text_is_escaped_and_cannot_close_script(self) -> None:
        evil = '</script><script>alert(1)</script><img src=x onerror=alert(1)>'
        deck = _deck({"role": "insight", "slots": {"claim": evil}})
        html = tpl.render_deck_html(deck)
        self.assertNotIn("<img src=x", html)
        self.assertEqual(html.count("</script>"), 2)  # deck JSON + present script only
        again = tpl.extract_deck(html)
        self.assertEqual(again["slides"][0]["slots"]["claim"], evil)


class RoundTripTests(unittest.TestCase):
    def test_extract_deck_round_trip(self) -> None:
        deck = tpl.build_deck(SAMPLE_ACTION)
        html = tpl.render_deck_html(deck)
        self.assertTrue(tpl.is_template_html(html))
        back = tpl.extract_deck(html)
        self.assertEqual([s["slots"] for s in back["slides"]], [s["slots"] for s in deck["slides"]])

    def test_inline_text_edit_syncs_into_slots(self) -> None:
        html = tpl.render_deck_html(_deck({"role": "content", "variant": "content.cards", "slots": {"title": "Старое", "items": [{"title": "a", "text": "b"}]}}))
        edited = html.replace(">Старое<", ">Новое <b>название</b><")
        # Editor chrome inside a slide must not leak into slots.
        edited = edited.replace('<div class="tpl-in">', '<div class="tpl-in"><div class="leo-edit-chrome"><p data-slot="title">junk</p></div>', 1)
        out = tpl.refinalize(edited)
        deck = tpl.extract_deck(out)
        self.assertEqual(deck["slides"][0]["slots"]["title"], "Новое название")

    def test_media_survives_rerender(self) -> None:
        deck = _deck({"role": "content", "variant": "content.text-media", "slots": {"title": "A", "body": "b", "image_prompt": "teal"}})
        html = tpl.render_deck_html(deck)
        self.assertIn('data-leo-img="pending"', html)
        filled = wc.fill_media_slots_with_data_urls(html, ["data:image/png;base64,AAAA"])
        out = wc.finalize_canvas_html(filled)
        self.assertIn('src="data:image/png;base64,AAAA"', out)
        self.assertNotIn('data-leo-img="pending"', out)

    def test_finalize_delegates_to_templates(self) -> None:
        html = tpl.render_deck_html(_deck({"role": "insight", "slots": {"claim": "X"}}))
        out = wc.finalize_canvas_html(html)
        self.assertIn('id="leo-deck"', out)
        self.assertNotIn('id="leo-canvas-kit"', out)


class OpTests(unittest.TestCase):
    def _cards(self, n):
        return _deck({"role": "content", "variant": "content.cards", "slots": {"title": "A", "items": [{"title": str(i), "text": "t"} for i in range(n)]}})

    def test_add_and_remove_items_within_limits(self) -> None:
        deck = self._cards(5)
        tpl.apply_op(deck, {"op": "add_item", "slide_id": "s1", "group": "items"})
        self.assertEqual(len(deck["slides"][0]["slots"]["items"]), 6)
        with self.assertRaises(tpl.DeckOpError):
            tpl.apply_op(deck, {"op": "add_item", "slide_id": "s1", "group": "items"})
        deck = self._cards(1)
        with self.assertRaises(tpl.DeckOpError):
            tpl.apply_op(deck, {"op": "remove_item", "slide_id": "s1", "group": "items", "index": 0})

    def test_add_item_after_index(self) -> None:
        deck = self._cards(2)
        tpl.apply_op(deck, {"op": "add_item", "slide_id": "s1", "group": "items", "after": 0})
        self.assertEqual(deck["slides"][0]["slots"]["items"][1]["title"], "Новый пункт")

    def test_nested_group(self) -> None:
        deck = tpl.build_deck({"slides": [copy.deepcopy(SAMPLE_ACTION_2["slides"][5])]})
        tpl.apply_op(deck, {"op": "add_item", "slide_id": "s1", "group": "options.1.points"})
        self.assertEqual(len(deck["slides"][0]["slots"]["options"][1]["points"]), 3)

    def test_set_variant_checks_data(self) -> None:
        deck = _deck({"role": "cover", "slots": {"title": "A", "goals": ["x", "y"]}})
        with self.assertRaises(tpl.DeckOpError):
            tpl.apply_op(deck, {"op": "variant", "slide_id": "s1", "variant": "cover.split-stat"})
        tpl.apply_op(deck, {"op": "variant", "slide_id": "s1", "variant": "cover.minimal"})
        self.assertEqual(deck["slides"][0]["variant"], "cover.minimal")
        # Slots of the previous layout are kept, so switching back restores them.
        tpl.apply_op(deck, {"op": "variant", "slide_id": "s1", "variant": "cover.goals"})
        self.assertEqual(deck["slides"][0]["slots"]["goals"], ["x", "y"])

    def test_variant_menu_marks_unavailable(self) -> None:
        deck = _deck({"role": "cover", "slots": {"title": "A", "goals": ["x", "y"]}})
        menu = {m["id"]: m for m in tpl._menu_for(deck["slides"][0])}
        self.assertTrue(menu["cover.goals"]["ok"])
        self.assertFalse(menu["cover.split-stat"]["ok"])
        self.assertEqual(menu["cover.split-stat"]["why"], "нет ключевой цифры")

    def test_chart_variants_follow_data(self) -> None:
        deck = tpl.build_deck({"slides": [copy.deepcopy(SAMPLE_ACTION["slides"][12])]})  # 2-series trend
        menu = {m["id"]: m["ok"] for m in tpl._menu_for(deck["slides"][0])}
        self.assertTrue(menu["chart.line"])
        self.assertFalse(menu["chart.area"])
        self.assertFalse(menu["chart.donut"])

    def test_slide_updates_insert_remove(self) -> None:
        deck = tpl.build_deck(SAMPLE_ACTION_2)
        n = len(deck["slides"])
        touched = tpl.apply_slide_updates(deck, {
            "slides": [{"id": "s6", "slots": {"title": "Новый", "options": [{"name": "A", "points": ["1", "2"]}, {"name": "B", "points": ["3", "4"]}]}}],
            "insert": [{"after": "s1", "role": "insight", "slots": {"claim": "Вставка"}}],
            "remove": ["s2"],
        })
        self.assertIn("s6", touched)
        self.assertEqual(len(deck["slides"]), n)
        self.assertEqual(deck["slides"][1]["slots"]["claim"], "Вставка")
        self.assertNotIn("s2", [s["id"] for s in deck["slides"]])


class ChartTests(unittest.TestCase):
    def test_pick_by_intent_and_shape(self) -> None:
        self.assertEqual(charts.pick_chart_type({"intent": "trend", "x": list("abcd"), "series": [{"values": [1, 2, 3, 4]}, {"values": [2, 3, 4, 5]}]}), "line")
        self.assertEqual(charts.pick_chart_type({"intent": "compare", "x": ["Очень длинная категория", "b"], "series": [{"values": [1, 2]}]}), "bar-h")
        self.assertEqual(charts.pick_chart_type({"intent": "share", "x": list("abcdefg"), "series": [{"values": [1] * 7}]}), "bar-h")
        self.assertIsNone(charts.pick_chart_type({"intent": "trend", "x": ["a"], "series": [{"values": [1, 2]}]}))

    def test_bad_chart_data_does_not_break_render(self) -> None:
        deck = _deck({"role": "chart", "slots": {"title": "A", "chart": {"intent": "trend", "x": "oops"}}})
        html = tpl.render_deck_html(deck)
        self.assertIn("Данных для графика недостаточно", html)


class ApplyActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_create_and_update_template_deck(self) -> None:
        note = notes_store.create_note(70, "Q3", "body")
        sheet = wc.apply_canvas_action(note=note, action=copy.deepcopy(SAMPLE_ACTION), note_sheets_mod=sheets)
        assert sheet is not None
        self.assertTrue(tpl.is_template_html(sheet["body"]))
        sid = str(sheet["id"])
        updated = wc.apply_canvas_action(
            note=note,
            action={"action": "update", "sheet_id": sid, "slides": [{"id": "s6", "slots": {"claim": "Обновлённый вывод"}}]},
            note_sheets_mod=sheets,
        )
        assert updated is not None
        deck = tpl.extract_deck(updated["body"])
        self.assertEqual(deck["slides"][5]["slots"]["claim"], "Обновлённый вывод")
        self.assertEqual(deck["slides"][0]["slots"]["title"], SAMPLE_ACTION["slides"][0]["slots"]["title"])

    def test_selection_patch_on_template_maps_to_slot(self) -> None:
        note = notes_store.create_note(71, "Q3", "body")
        sheet = wc.apply_canvas_action(note=note, action=copy.deepcopy(SAMPLE_ACTION), note_sheets_mod=sheets)
        sid = str(sheet["id"])
        updated = wc.apply_canvas_action(
            note=note,
            action={"action": "update", "sheet_id": sid, "patches": [{"leo_id": "s1-title", "outer_html": "<h1>Короче</h1>"}]},
            note_sheets_mod=sheets,
            selection_sheet_ids=[sid],
        )
        assert updated is not None
        self.assertEqual(tpl.extract_deck(updated["body"])["slides"][0]["slots"]["title"], "Короче")

    def test_update_with_nothing_to_apply_returns_none(self) -> None:
        note = notes_store.create_note(72, "Q3", "body")
        sheet = wc.apply_canvas_action(note=note, action=copy.deepcopy(SAMPLE_ACTION), note_sheets_mod=sheets)
        out = wc.apply_canvas_action(
            note=note,
            action={"action": "update", "sheet_id": str(sheet["id"]), "patches": [{"leo_id": "nope", "outer_html": "x"}]},
            note_sheets_mod=sheets,
        )
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()


class ReviseTests(unittest.TestCase):
    def test_legacy_html_deck_gets_section_patch(self) -> None:
        html = wc.finalize_canvas_html(
            '<main class="deck"><section class="slide" data-leo-id="a"><h1>Старое</h1></section>'
            '<section class="slide" data-leo-id="b"><p>Не трогать</p></section></main>',
            theme="editorial",
        )
        from assistant.lib import canvas_revise

        def ask(system, user):
            self.assertIn("Старое", user)
            self.assertNotIn("Не трогать", user)
            return (
                '```leo-canvas\n{"action":"update","patches":[{"leo_id":"a","outer_html":'
                '"<section class=\\"slide\\" data-leo-id=\\"a\\"><h1>Новое</h1></section>"},'
                '{"leo_id":"b","outer_html":"<section data-leo-id=\\"b\\">хак</section>"}]}\n```'
            )

        new_html, results = canvas_revise.revise(html, {"a": "переименуй"}, ask=ask)
        self.assertEqual(results, {"a": "applied"})
        self.assertIn("Новое", new_html)
        self.assertIn("Не трогать", new_html)

    def test_model_garbage_changes_nothing(self) -> None:
        from assistant.lib import canvas_revise

        html = tpl.render_deck_html(tpl.build_deck(SAMPLE_ACTION))
        new_html, results = canvas_revise.revise(html, {"s1": "сделай лучше"}, ask=lambda s, u: "не знаю")
        self.assertIsNone(new_html)
        self.assertEqual(results, {"s1": "failed"})


class PartialSlotsTests(unittest.TestCase):
    def test_model_sending_only_changed_fields_keeps_the_rest(self) -> None:
        deck = tpl.build_deck(SAMPLE_ACTION)
        s4 = next(s for s in deck["slides"] if s["id"] == "s4")
        stats = copy.deepcopy(s4["slots"]["stats"])
        tpl.apply_slide_updates(deck, {"slides": [{"id": "s4", "slots": {"title": "Новый заголовок"}}]})
        s4 = next(s for s in deck["slides"] if s["id"] == "s4")
        self.assertEqual(s4["slots"]["title"], "Новый заголовок")
        self.assertEqual(s4["slots"]["stats"], stats)
        self.assertEqual(s4["variant"], "context.stats")

    def test_timeline_period_takes_up_to_five_items(self) -> None:
        deck = tpl.build_deck({"slides": [copy.deepcopy(SAMPLE_ACTION_2["slides"][11])]})
        for _ in range(4):
            tpl.apply_op(deck, {"op": "add_item", "slide_id": "s1", "group": "periods.0.items"})
        self.assertEqual(len(deck["slides"][0]["slots"]["periods"][0]["items"]), 5)
        with self.assertRaises(tpl.DeckOpError):
            tpl.apply_op(deck, {"op": "add_item", "slide_id": "s1", "group": "periods.0.items"})
