"""Тесты форматирования HTML базы знаний."""

from __future__ import annotations

import re
import unittest

from assistant.integrations import knowledge_bitrix24
from assistant.lib.kb_html_format import (
    html_to_kb_markdown,
    normalize_kb_answer_markdown,
    repair_mashed_kb_text,
)


class KbHtmlFormatTests(unittest.TestCase):
    def test_two_col_table_to_sections(self) -> None:
        raw = """
        <table>
          <tr>
            <td>Корпоративный диск</td>
            <td><a href="https://disk.example/abc">ссылка</a></td>
          </tr>
          <tr>
            <td>Тестовый кабинет умный анализ</td>
            <td>testcabinet<br>#^:5WH73uMuUg;M<br>https://platform.obuchat.me/login<br>
            после логина вот этот url: https://analysis.obuchat.me/</td>
          </tr>
        </table>
        """
        text = html_to_kb_markdown(raw)
        self.assertIn("**Корпоративный диск:**", text)
        self.assertIn("https://disk.example/abc", text)
        self.assertIn("**Тестовый кабинет умный анализ:**", text)
        self.assertIn("testcabinet", text)
        self.assertIn("platform.obuchat.me/login", text)
        self.assertIn("analysis.obuchat.me", text)

    def test_blocks_to_text_uses_table_format(self) -> None:
        text = knowledge_bitrix24._blocks_to_text(
            [
                {
                    "content": (
                        "<h2>Доступы</h2><table>"
                        "<tr><td>Корпоративный диск</td><td>ссылка</td></tr>"
                        "<tr><td>ОКК кабинет клиентов по металлу</td>"
                        "<td>https://platform.obuchat.me/login<br>MetalsnabOKK</td></tr>"
                        "</table>"
                    )
                }
            ]
        )
        self.assertIn("**Корпоративный диск:**", text)
        self.assertIn("**ОКК кабинет клиентов по металлу:**", text)
        self.assertIn("platform.obuchat.me/login", text)

    def test_repair_mashed_text(self) -> None:
        mashed = (
            "Корпоративный дискссылкаТестовый кабинет умный анализtestcabinet"
            "#^:5WH73uMuUg;Mhttps://platform.obuchat.me/login"
        )
        fixed = repair_mashed_kb_text(mashed)
        self.assertIn("Корпоративный диск", fixed)
        self.assertIn("testcabinet", fixed)
        self.assertIn("platform.obuchat.me/login", fixed)

    def test_normalize_dedupes_title(self) -> None:
        raw = "## Доступы\n\nДоступы\n\n**Корпоративный диск:** ссылка"
        out = normalize_kb_answer_markdown(raw)
        self.assertEqual(out.count("## Доступы"), 1)

    def test_convert_broken_pipe_table_lines(self) -> None:
        raw = (
            "| Корпоративный диск | https://disk.example | | |\n"
            "| Тестовый кабинет | testcabinet | | |\n"
            "| --- | --- | --- | --- |\n"
            "после логина: https://analysis.obuchat.me/"
        )
        out = normalize_kb_answer_markdown(raw)
        self.assertNotIn("| --- |", out)
        self.assertIn("**Корпоративный диск:**", out)
        self.assertIn("**Тестовый кабинет:**", out)
        self.assertIsNone(re.search(r"^\|", out, re.MULTILINE))

    def test_multi_col_table_to_sections(self) -> None:
        raw = (
            "<table><tr><td>A</td><td>B</td><td>C</td></tr>"
            "<tr><td>Звонки</td><td>текст</td><td>@bot</td></tr></table>"
        )
        text = html_to_kb_markdown(raw)
        self.assertIn("**A:**", text)
        self.assertIn("**Звонки:**", text)
        self.assertNotIn("| A |", text)


if __name__ == "__main__":
    unittest.main()
