"""Версия бандла редактора в app.js должна совпадать с assetVersion внутри note-rich-editor.js.

Иначе loadNoteEditorScripts считает, что полный редактор не загрузился, и включает
«Упрощённый редактор» (так было после поднятия NOTE_EDITOR_ASSET_V без пересборки бандла).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class EditorAssetVersionTests(unittest.TestCase):
    def test_app_matches_bundle(self) -> None:
        app = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
        bundle = (ROOT / "webapp" / "note-rich-editor.js").read_text(encoding="utf-8")
        want = re.search(r'NOTE_EDITOR_ASSET_V = "([^"]+)"', app).group(1)
        got = re.search(r'assetVersion:"([^"]+)"', bundle).group(1)
        self.assertEqual(want, got, "NOTE_EDITOR_ASSET_V в app.js ≠ assetVersion в note-rich-editor.js — пересоберите бандл или верните версию")


if __name__ == "__main__":
    unittest.main()
