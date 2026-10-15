"""Бейджи карточки списка считаются по всему телу заметки (до усечения превью)."""

from __future__ import annotations

import unittest

import usage_server


class NoteListBadgesTests(unittest.TestCase):
    def test_checklist_editor_markup(self) -> None:
        body = '<p>x</p><ul class="note-task-list"><li checked=""><p>a</p></li><li><p>b</p><ul><li>sub</li></ul></li></ul>'
        b = usage_server.note_list_badges(body)
        self.assertEqual((b["checklist_done"], b["checklist_total"], b["has_table"]), (1, 2, False))

    def test_checklist_tiptap_markup_and_table(self) -> None:
        body = '<ul data-type="taskList"><li data-checked="true" data-type="taskItem">a</li><li data-checked="false" data-type="taskItem">b</li></ul><table><tr><td>1</td></tr></table>'
        b = usage_server.note_list_badges(body)
        self.assertEqual((b["checklist_done"], b["checklist_total"], b["has_table"]), (1, 2, True))

    def test_long_body_counts_beyond_preview(self) -> None:
        body = "<p>" + "x" * 5000 + '</p><ul class="note-task-list"><li checked>a</li></ul>'
        item = usage_server.minify_note_for_list({"body": body, "description": body})
        self.assertEqual(item["checklist_total"], 1)
        self.assertTrue(item.get("body_preview_only"))

    def test_plain(self) -> None:
        self.assertEqual(usage_server.note_list_badges("<p>hi</p>")["checklist_total"], 0)


if __name__ == "__main__":
    unittest.main()
