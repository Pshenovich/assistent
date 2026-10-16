"""Защита: обрезанное превью из списка не затирает полный текст заметки."""

from __future__ import annotations

import unittest

import usage_server


class PreviewOverwriteGuardTests(unittest.TestCase):
    def test_blocks_truncated_preview(self) -> None:
        full = "<p>" + "Длинный текст заметки про KPI и планы. " * 60 + "</p><p>Хвост, который нельзя терять.</p>"
        preview, truncated = usage_server.note_body_list_preview(full)
        self.assertTrue(truncated)
        # Редактор закрывает обрезанные теги — имитируем.
        edited = preview + "</p>"
        self.assertTrue(usage_server.looks_like_list_preview_overwrite(edited, full))

    def test_blocks_preview_cut_inside_tag_without_ellipsis(self) -> None:
        full = "<ol>" + "".join("<li><p>Пункт номер %d про интеграции календарей</p></li>" % i for i in range(80)) + "</ol>"
        cut = full[:560]
        cut = cut[: cut.rfind("</p>") + 4] + "</li></ol>"  # редактор выкинул оборванный тег вместе с «…»
        self.assertTrue(usage_server.looks_like_list_preview_overwrite(cut, full))

    def test_allows_normal_edits(self) -> None:
        full = "<p>" + "Текст. " * 200 + "</p>"
        self.assertFalse(usage_server.looks_like_list_preview_overwrite(full + "<p>Новая строка</p>", full))
        self.assertFalse(usage_server.looks_like_list_preview_overwrite("<p>Совсем другой текст…</p>", full))
        self.assertFalse(usage_server.looks_like_list_preview_overwrite("<p>Короткая…</p>", "<p>Короткая заметка</p>"))
        self.assertFalse(usage_server.looks_like_list_preview_overwrite(None, full))


if __name__ == "__main__":
    unittest.main()
