import os
import tempfile
import unittest

from assistant.lib import web_canvas as wc
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store


class WebCanvasLibTests(unittest.TestCase):
    def test_parse_leo_canvas_block(self) -> None:
        text = (
            "Готово.\n\n```leo-canvas\n"
            '{"action":"create","title":"Pitch","theme":"editorial","html":"<html><body><h1>Hi</h1></body></html>"}'
            "\n```\n"
        )
        clean, actions = wc.parse_leo_canvas_blocks(text)
        self.assertEqual(clean, "Готово.")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "create")
        self.assertEqual(actions[0]["theme"], "editorial")

    def test_parse_bare_json_without_fence(self) -> None:
        text = (
            "Готово. Собрал презентацию по заметке и контексту обсуждения.\n"
            '{"action":"create","title":"Антипаттерны коммуникаций","theme":"editorial",'
            '"html":"<!DOCTYPE html><html><body><h1>Hi</h1></body></html>"}'
        )
        clean, actions = wc.parse_leo_canvas_blocks(text)
        self.assertIn("Готово", clean)
        self.assertNotIn('"action"', clean)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["title"], "Антипаттерны коммуникаций")

    def test_parse_json_fence(self) -> None:
        text = (
            "Ок\n```json\n"
            '{"action":"update","sheet_id":"12","theme":"poster","patches":[]}'
            "\n```"
        )
        clean, actions = wc.parse_leo_canvas_blocks(text)
        self.assertEqual(clean, "Ок")
        self.assertEqual(actions[0]["action"], "update")

    def test_parse_truncated_bare_json_strips_dump(self) -> None:
        text = (
            "Готово. Собрал презентацию.\n"
            '{"action":"create","title":"X","theme":"edito'
        )
        clean, actions = wc.parse_leo_canvas_blocks(text)
        self.assertEqual(clean, "Готово. Собрал презентацию.")
        self.assertEqual(actions, [])
        reconciled = wc.reconcile_canvas_chat_answer(
            clean, actions=actions, applied=[], raw_answer=text
        )
        self.assertEqual(reconciled, wc.CANVAS_FAIL_MSG)

    def test_reconcile_keeps_success_when_applied(self) -> None:
        out = wc.reconcile_canvas_chat_answer(
            "Готово. Собрал презентацию.",
            actions=[{"action": "create"}],
            applied=[{"sheet_id": "1"}],
            raw_answer="…",
        )
        self.assertIn("Готово", out)

    def test_salvage_truncated_html_payload(self) -> None:
        html = "<!DOCTYPE html><html><body>" + ("<section class='slide'><h1>Hi</h1></section>" * 3)
        raw = '{"action":"create","title":"X","theme":"editorial","html":"' + html
        # unclosed string + object — salvage should close it
        data = wc._salvage_truncated_canvas_json(raw)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["action"], "create")
        self.assertIn("<section", data["html"])

    def test_finalize_injects_kit_and_theme(self) -> None:
        html = wc.finalize_canvas_html(
            "<section class='slide'><h1 class='display'>Hello</h1></section>",
            theme="product-dark",
        )
        self.assertIn('data-leo-theme="product-dark"', html)
        self.assertIn('id="leo-canvas-kit"', html)
        self.assertIn("data-leo-id=", html)
        self.assertNotIn("<script", html.lower().replace("leo-canvas-kit", ""))

    def test_strip_script_and_purple(self) -> None:
        raw = (
            "<html><body>"
            "<script>alert(1)</script>"
            "<div style='background:linear-gradient(#7c3aed,#6366f1)'>x</div>"
            "</body></html>"
        )
        out = wc.finalize_canvas_html(raw, theme="brutal")
        self.assertNotIn("<script>", out.lower())
        self.assertNotIn("#7c3aed", out.lower())

    def test_prompt_is_slot_templates(self) -> None:
        prompt = wc.canvas_system_addon()
        self.assertIn("SLOT TEMPLATES", prompt)
        self.assertIn("Never write HTML", prompt)
        self.assertIn("content.cards", prompt)
        self.assertIn("intent: compare|rank|trend", prompt)
        self.assertIn("Never invent numbers", prompt)

    def test_kit_has_layout_primitives(self) -> None:
        wc._KIT_CSS_CACHE = None
        css = wc.load_kit_css()
        for token in (
            'data-leo-theme="bento"',
            ".tile-ink",
            ".bento-2x2",
            ".vbars",
            ".donut",
            ".pill",
            "--ice",
            ".abstract-orb",
        ):
            self.assertIn(token, css)

    def test_default_theme_is_bento(self) -> None:
        self.assertEqual(wc.DEFAULT_THEME, "bento")
        self.assertEqual(wc.normalize_theme("pitch"), "bento")
        html = wc.finalize_canvas_html("<section class='slide'><h1 class='title'>Hi</h1></section>")
        self.assertIn('data-leo-theme="bento"', html)

    def test_finalize_keeps_svg_and_bars(self) -> None:
        raw = (
            '<section class="slide">'
            '<div class="bars" data-leo-id="bars">'
            '<div class="bar"><span class="bar-label">A</span>'
            '<span class="bar-track"><span class="bar-fill" style="width:50%"></span></span>'
            '<span class="bar-value">50%</span></div></div>'
            '<svg class="chart-svg" viewBox="0 0 10 10" data-leo-id="spark">'
            '<polyline points="0,10 10,0"/></svg>'
            "</section>"
        )
        out = wc.finalize_canvas_html(raw, theme="editorial")
        self.assertIn('class="bars"', out)
        self.assertIn("<svg", out.lower())
        self.assertIn("bar-fill", out)
        self.assertNotIn("<script>", out.lower().replace("leo-canvas-kit", ""))

    def test_apply_patch_by_leo_id(self) -> None:
        base = (
            '<html data-leo-theme="editorial"><body>'
            '<h1 class="display" data-leo-id="hero-title">Old</h1>'
            "</body></html>"
        )
        out = wc.finalize_canvas_html(
            base,
            theme="editorial",
            patches=[
                {
                    "leo_id": "hero-title",
                    "outer_html": '<h1 class="display" data-leo-id="hero-title">New</h1>',
                }
            ],
        )
        self.assertIn(">New</h1>", out)
        self.assertNotIn(">Old</h1>", out)


class WebSheetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_create_web_kind(self) -> None:
        note = notes_store.create_note(42, "Deal", "<p>body</p>")
        html = wc.finalize_canvas_html(
            '<section class="slide hero"><h1 class="display">Pitch</h1></section>',
            theme="poster",
        )
        extra = sheets.create_sheet(note, "Презентация", body=html, kind="web")
        self.assertEqual(extra["kind"], "web")
        self.assertIn("leo-canvas-kit", extra["body"])
        listed = sheets.list_sheets(note)
        self.assertEqual(listed[0]["kind"], "doc")
        self.assertEqual(listed[1]["kind"], "web")

    def test_copy_preserves_kind(self) -> None:
        note = notes_store.create_note(43, "A", "b")
        sheets.create_sheet(note, "Web", body="<p>x</p>", kind="web")
        dup = notes_store.duplicate_note(43, note["id"])
        assert dup is not None
        copied = sheets.list_extra_sheets(dup["id"])
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0]["kind"], "web")

    def test_apply_canvas_action_create(self) -> None:
        note = notes_store.create_note(44, "A", "b")
        sheet = wc.apply_canvas_action(
            note=note,
            action={
                "action": "create",
                "title": "Deck",
                "theme": "soft-light",
                "html": '<section class="slide"><h1 class="title">Go</h1></section>',
            },
            note_sheets_mod=sheets,
        )
        assert sheet is not None
        self.assertEqual(sheet["kind"], "web")
        self.assertEqual(sheet["title"], "Deck")
        self.assertIn("data-leo-theme=\"soft-light\"", sheet["body"])

    def test_selection_create_coerced_to_update(self) -> None:
        note = notes_store.create_note(45, "A", "b")
        created = sheets.create_sheet(
            note,
            "Deck",
            body=wc.finalize_canvas_html(
                '<h1 class="display" data-leo-id="hero-title">Old</h1>',
                theme="editorial",
            ),
            kind="web",
        )
        sid = str(created["id"])
        before = len(sheets.list_extra_sheets(note["id"]))
        updated = wc.apply_canvas_action(
            note=note,
            action={
                "action": "create",
                "title": "Should not spawn",
                "theme": "editorial",
                "html": (
                    '<section class="slide"><h1 class="display" data-leo-id="hero-title">'
                    "Brand new deck</h1><p class=\"body\" data-leo-id=\"other\">gone</p>"
                    "</section>"
                ),
                "patches": [
                    {
                        "leo_id": "hero-title",
                        "outer_html": (
                            '<h1 class="display" data-leo-id="hero-title">Fixed</h1>'
                        ),
                    }
                ],
            },
            note_sheets_mod=sheets,
            selection_sheet_ids=[sid],
        )
        assert updated is not None
        self.assertEqual(str(updated["id"]), sid)
        self.assertIn(">Fixed</h1>", updated["body"])
        self.assertNotIn("Brand new deck", updated["body"])
        self.assertEqual(len(sheets.list_extra_sheets(note["id"])), before)

    def test_update_without_sheet_id_does_not_create(self) -> None:
        note = notes_store.create_note(46, "A", "b")
        before = len(sheets.list_extra_sheets(note["id"]))
        out = wc.apply_canvas_action(
            note=note,
            action={"action": "update", "patches": []},
            note_sheets_mod=sheets,
        )
        self.assertIsNone(out)
        self.assertEqual(len(sheets.list_extra_sheets(note["id"])), before)

    def test_normalize_forces_update_for_selections(self) -> None:
        act = wc.normalize_canvas_action_for_apply(
            {"action": "create", "html": "<p>x</p>", "patches": []},
            selection_sheet_ids=["99"],
        )
        self.assertEqual(act["action"], "update")
        self.assertEqual(act["sheet_id"], "99")
        self.assertNotIn("html", act)

    def test_selection_update_without_patches_is_rejected(self) -> None:
        note = notes_store.create_note(47, "A", "b")
        created = sheets.create_sheet(
            note,
            "Deck",
            body=wc.finalize_canvas_html(
                '<h1 class="display" data-leo-id="hero-title">Keep</h1>',
                theme="bento",
            ),
            kind="web",
        )
        sid = str(created["id"])
        before = created["body"]
        out = wc.apply_canvas_action(
            note=note,
            action={
                "action": "update",
                "sheet_id": sid,
                "html": "<h1>Nuked whole deck</h1>",
            },
            note_sheets_mod=sheets,
            selection_sheet_ids=[sid],
        )
        self.assertIsNone(out)
        again = sheets.get_extra_sheet(note["id"], int(sid))
        assert again is not None
        self.assertEqual(again["body"], before)

    def test_selections_prompt_requires_update(self) -> None:
        block = wc.selections_prompt_block(
            [{"sheet_id": "7", "leo_id": "hero-title", "snippet": "<h1>Hi</h1>"}]
        )
        self.assertIn("action=update", block)
        self.assertIn("sheet_id=7", block)
        self.assertIn("Do NOT include an html field", block)
        self.assertIn("SURGICAL PATCH MODE", block)

    def test_extract_and_fill_media_slots(self) -> None:
        html = (
            '<div class="abstract abstract-orb" data-leo-id="hero-art" '
            'data-leo-img="pending" data-leo-img-prompt="soft blue orb">x</div>'
            '<div class="abstract abstract-wave" data-leo-id="other">y</div>'
        )
        prompts = wc.extract_canvas_img_prompts(html)
        self.assertEqual(prompts, ["soft blue orb"])
        urls = wc.image_dicts_to_data_urls(
            [{"mime": "image/png", "b64": "AAAA"}]
        )
        self.assertEqual(urls, ["data:image/png;base64,AAAA"])
        filled = wc.fill_media_slots_with_data_urls(html, urls)
        self.assertIn('src="data:image/png;base64,AAAA"', filled)
        self.assertIn('data-leo-id="hero-art"', filled)
        self.assertNotIn("data-leo-img-prompt", filled)
        self.assertNotIn('data-leo-img="pending"', filled)
        # leftover abstract gets second url
        filled2 = wc.fill_media_slots_with_data_urls(
            html,
            [
                "data:image/png;base64,AAAA",
                "data:image/png;base64,BBBB",
            ],
        )
        self.assertIn("BBBB", filled2)
        self.assertIn('data-leo-id="other"', filled2)

    def test_prompt_mentions_image_prompt_slot(self) -> None:
        addon = wc.canvas_system_addon()
        self.assertIn("image_prompt", addon)


if __name__ == "__main__":
    unittest.main()
