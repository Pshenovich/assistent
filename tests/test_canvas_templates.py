import copy
import os
import re
import tempfile
import unittest

from assistant.lib import canvas_charts as charts
from assistant.lib import canvas_templates as tpl
from assistant.lib import web_canvas as wc
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store
from tests.canvas_sample_deck import SAMPLE_ACTION, SAMPLE_ACTION_2, SAMPLE_ACTION_3


def _deck(*slides):
    return tpl.build_deck({"title": "T", "slides": list(slides)})


class CatalogTests(unittest.TestCase):
    def test_every_variant_renders_from_samples(self) -> None:
        seen = set()
        for action in (SAMPLE_ACTION, SAMPLE_ACTION_2, SAMPLE_ACTION_3):
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
        self.assertEqual(len(deck["slides"][0]["slots"]["goals"]), 5)

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


class EvidenceLayoutTests(unittest.TestCase):
    def _html(self, facts):
        deck = {"title": "T", "slides": [{"id": "s1", "role": "insight", "variant": "insight.evidence", "slots": {
            "claim": "Вывод", "evidence": [{"fact": f, "text": "пояснение"} for f in facts]}}]}
        return tpl.render_deck_html(deck, {})

    def test_short_numbers_stay_inline(self):
        self.assertIn("grid-template-columns:150px 1fr", self._html(["+18%", "×2,5"]))

    def test_word_facts_go_above_text(self):
        html = self._html(["+18%", "Документация"])
        self.assertNotIn("grid-template-columns:150px 1fr", html)
        self.assertIn("flex-direction:column", html)


class InsertSlideTests(unittest.TestCase):
    def _html(self):
        deck = tpl.build_deck(SAMPLE_ACTION)
        deck["slides"] = deck["slides"][:5]
        return tpl.render_deck_html(deck, {})

    def test_inserts_after_given_slide_without_touching_others(self):
        from assistant.lib import canvas_revise

        html = self._html()
        deck = tpl.extract_deck(html)
        first = deck["slides"][0]["id"]
        answer = (
            '```leo-canvas\n{"action":"update","slides":[{"id":"%s","slots":{"title":"взлом"}}],'
            '"insert":[{"after":"zzz","role":"insight","variant":"insight.evidence","slots":{"claim":"Новый вывод",'
            '"evidence":[{"fact":"+18%%","text":"рост"},{"fact":"x2","text":"быстрее"}]}}]}\n```' % first
        )
        new_html, new_ids = canvas_revise.insert_slides(html, first, "вывод про рост", ask=lambda s, u: answer)
        after = tpl.extract_deck(new_html)
        self.assertEqual(len(new_ids), 1)
        self.assertEqual(after["slides"][1]["id"], new_ids[0])
        self.assertEqual(after["slides"][1]["slots"]["claim"], "Новый вывод")
        self.assertEqual(after["slides"][0], deck["slides"][0])  # соседний слайд не тронут

    def test_full_deck_refuses(self):
        from assistant.lib import canvas_revise

        deck = tpl.build_deck(SAMPLE_ACTION)
        while len(deck["slides"]) < tpl.MAX_DECK_SLIDES:
            deck["slides"].append(dict(copy.deepcopy(deck["slides"][-1]), id=tpl._next_id(deck)))
        html = tpl.render_deck_html(deck, {})
        with self.assertRaises(ValueError):
            canvas_revise.insert_slides(html, deck["slides"][0]["id"], "x", ask=lambda s, u: "")


class MonoStyleTests(unittest.TestCase):
    def test_every_variant_has_mono_renderer_and_renders(self):
        from assistant.lib import canvas_style_mono

        self.assertEqual(set(canvas_style_mono.RENDERERS), set(tpl.VARIANTS))
        deck = tpl.build_deck(SAMPLE_ACTION)
        html = tpl.render_deck_html(deck, {}, theme="mono")
        self.assertIn('data-leo-theme="mono"', html)
        self.assertNotIn("Не удалось отрисовать", html)
        self.assertIn('class="m-slide', html)

    def test_mono_keeps_edit_attributes(self):
        deck = _deck({"role": "cover", "variant": "cover.goals", "slots": {"title": "A", "goals": ["x", "y"]}})
        html = tpl.render_deck_html(deck, {}, theme="mono")
        self.assertIn('data-group="goals" data-min="1" data-max="5"', html)
        self.assertIn('data-slot="goals.1"', html)

    def test_unknown_theme_falls_back_to_analytic(self):
        html = tpl.render_deck_html(tpl.build_deck(SAMPLE_ACTION), {}, theme="nope")
        self.assertIn('data-leo-theme="analytic"', html)


class ThemeChoiceTests(unittest.TestCase):
    def test_theme_from_action_and_op(self):
        deck = tpl.build_deck(dict(SAMPLE_ACTION, theme="mono"))
        self.assertEqual(deck["theme"], "mono")
        self.assertEqual(tpl.build_deck(dict(SAMPLE_ACTION, theme="bento"))["theme"], "analytic")
        before = copy.deepcopy(deck["slides"])
        tpl.apply_op(deck, {"op": "theme", "theme": "analytic"})
        self.assertEqual(deck["theme"], "analytic")
        self.assertEqual(deck["slides"], before)  # содержимое не меняется
        with self.assertRaises(tpl.DeckOpError):
            tpl.apply_op(deck, {"op": "theme", "theme": "nope"})

    def test_theme_survives_round_trip(self):
        html = tpl.render_deck_html(tpl.build_deck(dict(SAMPLE_ACTION, theme="mono")))
        self.assertEqual(tpl.extract_deck(html)["theme"], "mono")
        self.assertIn('data-leo-theme="mono"', tpl.refinalize(html))

    def test_chat_default_and_restyle(self):
        from assistant.lib import web_canvas

        calls = {}

        class Sheets:
            def create_sheet(self, note, title, body, kind):
                calls["create"] = body
                return {"id": 1, "body": body}

            def get_extra_sheet(self, note_id, sid):
                return {"id": sid, "kind": "web", "body": calls["create"]}

            def update_sheet(self, note_id, sid, title=None, body=None, kind=None):
                calls["update"] = body
                return {"id": sid, "body": body}

        note = {"id": 5}
        web_canvas.apply_canvas_action(note=note, action=dict(SAMPLE_ACTION), note_sheets_mod=Sheets(), default_deck_theme="mono")
        self.assertIn('data-leo-theme="mono"', calls["create"])
        web_canvas.apply_canvas_action(note=note, action={"action": "update", "sheet_id": "1", "theme": "analytic"}, note_sheets_mod=Sheets())
        self.assertIn('data-leo-theme="analytic"', calls["update"])


class GalleryTests(unittest.TestCase):
    def test_images_keep_their_media_when_neighbour_removed(self):
        deck = tpl.build_deck(SAMPLE_ACTION_3)
        html = tpl.render_deck_html(deck)
        deck = tpl.extract_deck(html)
        s2 = deck["slides"][1]
        keys = [it["key"] for it in s2["slots"]["images"]]
        self.assertEqual(len(set(keys)), 3)
        media = {f"{s2['id']}-m-{k}": f"data:image/png;base64,{k}" for k in keys}
        tpl.apply_op(deck, {"op": "remove_item", "slide_id": s2["id"], "group": "images", "index": 0})
        html2 = tpl.render_deck_html(deck, media)
        self.assertNotIn(keys[0], html2.split('id="leo-deck"')[0])  # удалённая картинка исчезла
        self.assertIn(f"base64,{keys[1]}", html2)
        self.assertIn(f"base64,{keys[2]}", html2)

    def test_pending_prompts_and_empty_caption(self):
        html = tpl.render_deck_html(tpl.build_deck(SAMPLE_ACTION_3), theme="mono")
        self.assertEqual(html.count('data-leo-img="pending"'), 4)
        self.assertIn("m-gsolo", html)


class CustomLayoutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self):
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def _custom_slots(self):
        return {"title": "Пилот", "surface": "dark", "rows": [
            {"blocks": [{"type": "number", "span": 5, "value": "60", "label": "клиентов"},
                        {"type": "card", "span": 7, "title": "Итог", "text": "Раскатываем", "tone": "dark"}]},
            {"blocks": [{"type": "image", "span": 12, "caption": "Фото", "image_prompt": "office"}]}]}

    def test_custom_grid_renders_in_both_styles_with_edit_attrs(self):
        deck = {"v": tpl.DECK_VERSION, "title": "T", "slides": [{"id": "s1", "role": "custom", "variant": "custom.grid", "slots": self._custom_slots()}]}
        for th in ("analytic", "mono"):
            html = tpl.render_deck_html(deck, {}, theme=th)
            self.assertNotIn("Не удалось отрисовать", html)
            self.assertIn('data-slot="rows.0.blocks.1.title"', html)
            self.assertIn('data-group="rows.0.blocks"', html)
            self.assertIn('data-leo-img="pending"', html)
        self.assertIn("m-black", tpl.render_deck_html(deck, {}, theme="mono"))

    def test_saved_layout_keeps_structure_and_insert_fills_it(self):
        from assistant.lib import canvas_revise
        from assistant.stores import canvas_layouts

        saved = canvas_layouts.create_layout(77, "Цифра и карточка", self._custom_slots())
        st = saved["structure"]
        self.assertEqual([[b["type"] for b in r["blocks"]] for r in st["rows"]], [["number", "card"], ["image"]])
        self.assertEqual(st["rows"][0]["blocks"][1]["tone"], "dark")
        self.assertEqual([x["id"] for x in canvas_layouts.list_layouts(77)], [saved["id"]])
        self.assertEqual(canvas_layouts.list_layouts(78), [])

        deck = tpl.build_deck(SAMPLE_ACTION)
        deck["slides"] = deck["slides"][:3]
        html = tpl.render_deck_html(deck)
        # Модель «перепутала» типы и доли — раскладка всё равно будет как сохранена.
        answer = ('```leo-canvas\n{"action":"update","insert":[{"after":"s1","role":"content","slots":{"title":"Новый",'
                  '"rows":[{"blocks":[{"type":"text","span":2,"value":"412","label":"клиентов"},{"type":"text","span":3,"title":"Вывод","text":"Да"}]}]}}]}\n```')
        new_html, ids = canvas_revise.insert_slides(html, "s1", "про пилот", ask=lambda a, b: answer, structure=st)
        slide = [x for x in tpl.extract_deck(new_html)["slides"] if x["id"] == ids[0]][0]
        self.assertEqual(slide["variant"], "custom.grid")
        rows = slide["slots"]["rows"]
        self.assertEqual([[b["type"] for b in r["blocks"]] for r in rows], [["number", "card"], ["image"]])
        self.assertEqual(rows[0]["blocks"][0]["value"], "412")
        self.assertEqual(slide["slots"]["surface"], "dark")
        self.assertTrue(canvas_layouts.delete_layout(77, saved["id"]))

    def test_cards_3d_visual_can_be_replaced_with_image(self):
        slots = {"title": "Три шага", "items": [{"title": "A", "text": "a"}, {"title": "B", "text": "b"}]}
        deck = {"v": tpl.DECK_VERSION, "title": "T", "slides": [{"id": "s1", "role": "content", "variant": "content.cards", "slots": slots}]}
        html = tpl.render_deck_html(deck, {})
        stored = tpl.extract_deck(html)["slides"][0]["slots"]["items"]
        mid = "s1-v-" + stored[0]["key"]
        self.assertIn(f'data-media="{mid}"', html)
        self.assertIn('data-a3="', html)
        self.assertNotIn('class="media-img"', html)
        # Картинка, вставленная в редакторе, переживает перерисовку и привязана к своей карточке.
        edited = html.replace(f'data-media="{mid}" data-leo-id="{mid}"', f'data-media="{mid}" data-leo-id="{mid}"', 1)
        edited = re.sub(r'(data-media="%s"[^>]*>)<div class="a3-[a-z]+">[^<]*(?:<i></i>)*</div>' % re.escape(mid),
                        r'\1<img class="media-img" alt="" src="data:image/png;base64,AAAA"/>', edited, count=1)
        media = tpl.extract_media(edited)
        self.assertEqual(list(media), [mid])
        again = tpl.render_deck_html(tpl.extract_deck(edited), media)
        self.assertIn('class="a3 a3-photo"', again)
        self.assertEqual(again.count('class="media-img"'), 1)

    def test_custom_insert_plans_layout_first_then_fills_it(self):
        from assistant.lib import canvas_revise

        deck = tpl.build_deck(SAMPLE_ACTION)
        deck["slides"] = deck["slides"][:3]
        html = tpl.render_deck_html(deck)
        plan = ('{"title":true,"rows":[{"blocks":[{"type":"number","span":3},{"type":"card","span":3},'
                '{"type":"card","span":3},{"type":"card","span":3}]},{"blocks":[{"type":"text","span":12}]}]}')
        # Модель на шаге 2 снова сложила всё в столбик и вернула флаг вместо заголовка — раскладка из шага 1 побеждает.
        fill = ('```leo-canvas\n{"action":"update","insert":[{"after":"s1","slots":{"title":true,"rows":['
                '{"blocks":[{"type":"number","span":6,"value":"4,2 млн","label":"бюджет"}]},'
                '{"blocks":[{"type":"card","title":"A"},{"type":"card","title":"B"},{"type":"card","title":"C"}]},'
                '{"blocks":[{"type":"text","text":"Вывод"}]}]}}]}\n```')
        calls = []

        def ask(system, user):
            calls.append(system)
            return plan if len(calls) == 1 else fill

        new_html, ids = canvas_revise.insert_slides(html, "s1", "слева цифра, справа три карточки, внизу вывод", ask=ask, layout="custom")
        self.assertEqual(len(calls), 2)
        slide = [x for x in tpl.extract_deck(new_html)["slides"] if x["id"] == ids[0]][0]
        rows = slide["slots"]["rows"]
        self.assertEqual([[b["type"] for b in r["blocks"]] for r in rows], [["number", "card", "card", "card"], ["text"]])
        self.assertEqual(rows[0]["blocks"][0]["value"], "4,2 млн")
        self.assertNotIn("title", slide["slots"])

    def test_layout_auto_name_and_pending_media_only_for_given_slides(self):
        from assistant.stores import canvas_layouts

        st = canvas_layouts.structure_of(self._custom_slots())
        self.assertEqual(canvas_layouts.auto_name(st), "Цифра + Карточка / Фото")
        self.assertEqual(canvas_layouts.create_layout(77, "", self._custom_slots())["name"], "Цифра + Карточка / Фото")
        deck = {"v": tpl.DECK_VERSION, "title": "T", "slides": [
            {"id": "s1", "role": "custom", "variant": "custom.grid", "slots": self._custom_slots()},
            {"id": "s2", "role": "custom", "variant": "custom.grid", "slots": self._custom_slots()}]}
        html = tpl.render_deck_html(deck, {})
        got = tpl.pending_media(html, ["s2"])
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0][0].startswith("s2-m-"))
        self.assertEqual(got[0][1], "office")

    def test_oversized_image_is_shrunk_not_dropped(self):
        import base64
        import io

        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow не установлен")
        from assistant.lib import web_canvas

        im = Image.frombytes("RGB", (1024, 1024), os.urandom(1024 * 1024 * 3))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        self.assertGreater(len(b64), web_canvas.MAX_IMAGE_B64)
        urls = web_canvas.image_dicts_to_data_urls([{"mime": "image/png", "b64": b64}])
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("data:image/jpeg;base64,"))


class TableFactsTests(unittest.TestCase):
    def test_totals_by_row_column_and_grand_skip_total_column(self):
        from assistant.lib import table_facts

        html = ("<p>Бюджет</p><table><tr><th>Статья</th><th>Q1</th><th>Q2</th><th>Итого</th></tr>"
                "<tr><td>Маркетинг</td><td>120 000</td><td>95 500</td><td>999</td></tr>"
                "<tr><td>Зарплаты</td><td>1 200 000</td><td>1 250 000</td><td></td></tr>"
                "<tr><td>Итого</td><td>1</td><td>1</td><td></td></tr></table>")
        out = table_facts.facts_from_html(html)
        self.assertIn("Маркетинг: сумма 215 500", out)
        self.assertIn("Зарплаты: сумма 2 450 000", out)
        self.assertIn("Q1: сумма 1 320 000", out)
        self.assertIn("Всего по таблице: 2 665 500", out)
        self.assertEqual(table_facts.facts_from_html("<p>без таблиц</p>"), "")
        self.assertEqual(table_facts.parse_number("−3,1"), -3.1)
        self.assertIsNone(table_facts.parse_number("Q1"))

    def test_big_numbers_get_digit_groups(self):
        self.assertEqual(tpl.group_digits("4231530"), "4\u00a0231\u00a0530")
        self.assertEqual(tpl.group_digits("358750 ₽"), "358\u00a0750 ₽")
        self.assertEqual(tpl.group_digits("2024"), "2024")
        self.assertEqual(tpl.group_digits("12 000"), "12 000")


class ImageStyleTests(unittest.TestCase):
    def test_styled_prompt_adds_deck_art_direction_once(self):
        from assistant.lib import image_style

        p = image_style.styled_prompt("инженер подключает CRM", "mono")
        self.assertTrue(p.startswith("инженер подключает CRM. Art direction:"))
        self.assertIn("black-and-white", p)
        self.assertIn("No text", p)
        self.assertNotIn("#", p)  # коды цветов модели печатают на картинке
        self.assertEqual(image_style.styled_prompt(p, "analytic"), p)
        self.assertIn("dark-green", image_style.styled_prompt("office", None))
        self.assertEqual(image_style.styled_prompt("  ", "mono"), "")
        self.assertEqual(image_style.deck_theme_of_html('<html data-leo-theme="mono" data-leo-deck="1">'), "mono")

    def test_schema_asks_for_concrete_image_subjects(self):
        self.assertIn("не «abstract shapes»", tpl.schema_prompt())


class RestyleImagesTests(unittest.TestCase):
    def _deck(self):
        return {"v": tpl.DECK_VERSION, "title": "T", "slides": [
            {"id": "s1", "role": "content", "variant": "content.gallery", "slots": {"title": "Фото", "images": [
                {"caption": "a", "image_prompt": "team at a whiteboard", "key": "k1"},
                {"caption": "b", "image_prompt": "laptop with charts", "key": "k2"},
                {"caption": "c", "image_prompt": "", "key": "k3"}]}},
            {"id": "s2", "role": "content", "variant": "content.cards", "slots": {"title": "К", "items": [
                {"title": "A", "text": "a", "key": "v1"}, {"title": "B", "text": "b", "key": "v2"}]}}]}

    def test_prompt_and_own_flags_survive_rerender_and_own_is_skipped(self):
        img = "data:image/png;base64,AAAA"
        media = {"s1-m-k1": img, "s1-m-k2": img, "s1-m-k2#own": "1", "s2-v-v1": img, "s2-v-v1#prompt": "rocket icon"}
        for th in ("analytic", "mono"):
            html = tpl.render_deck_html(self._deck(), media, theme=th)
            back = tpl.extract_media(html)
            self.assertEqual(back.get("s1-m-k1#prompt"), "team at a whiteboard")
            self.assertEqual(back.get("s1-m-k2#own"), "1")
            self.assertNotIn("s1-m-k2#prompt", back)
            again = tpl.render_deck_html(tpl.extract_deck(html), back, theme=th)
            self.assertEqual(tpl.extract_media(again), back)
            targets = dict(tpl.restyle_targets(html))
            self.assertEqual(targets.get("s1-m-k1"), "team at a whiteboard")
            self.assertNotIn("s1-m-k2", targets)  # своя картинка
            self.assertNotIn("s1-m-k3", targets)  # пустой слот без описания
            if th == "analytic":
                self.assertEqual(targets.get("s2-v-v1"), "rocket icon")  # 3D-визуал, заменённый генерацией
                self.assertNotIn("s2-v-v2", targets)  # 3D-фигура без картинки
        self.assertEqual(set(tpl.media_images(media)), {"s1-m-k1", "s1-m-k2", "s2-v-v1"})

    def test_legacy_uploaded_image_is_recognised_by_file_digest(self):
        import base64
        import io

        from PIL import Image

        from assistant.stores import comment_files

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["NOTES_DB_PATH"] = os.path.join(tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self.addCleanup(lambda: setattr(notes_store, "_CONN", None))
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 200, 120)).save(buf, "PNG")
        data = buf.getvalue()
        comment_files.save_bytes(owner_user_id=5, item_kind="local", item_id="7", author_user_id=5,
                                 filename="photo.png", mime="image/png", data=data)
        digests = comment_files.image_digests_for_item(owner_user_id=5, item_kind="local", item_id="7")
        url = "data:image/png;base64," + base64.b64encode(data).decode()
        self.assertIn(tpl.data_url_digest(url), digests)
        self.assertNotIn(tpl.data_url_digest("data:image/png;base64,AAAA"), digests)
        self.assertIsNone(tpl.data_url_digest("/files/1.png"))
