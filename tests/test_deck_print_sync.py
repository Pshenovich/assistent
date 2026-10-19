"""Блок печати презентации (PDF) одинаковый в share.js и web-canvas.js.

Его держат копией в двух файлах: страница по ссылке не грузит web-canvas.js. Если поправить
только один, PDF из редактора и по ссылке начнут расходиться (так уже было со смещёнными слайдами).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _block(name: str) -> str:
    text = (ROOT / "webapp" / name).read_text(encoding="utf-8")
    m = re.search(r"// leo-deck-print:start.*?// leo-deck-print:end", text, re.S)
    assert m, f"нет блока leo-deck-print в {name}"
    return m.group(0)


class DeckPrintSyncTests(unittest.TestCase):
    def test_share_and_editor_blocks_match(self) -> None:
        self.assertEqual(_block("share.js"), _block("web-canvas.js"))

    def test_print_stage_without_transform(self) -> None:
        block = _block("share.js")
        # Сцена со scale/translate при печати смещалась и обрезалась (Chromium).
        self.assertIn(".tpl-stage{position:relative!important;left:0!important;top:0!important;transform:none!important}", block)
        self.assertIn("leo-print-baked", block)


if __name__ == "__main__":
    unittest.main()
