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


if __name__ == "__main__":
    unittest.main()
