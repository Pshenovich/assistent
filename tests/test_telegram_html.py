import unittest

from assistant.lib.telegram_html import (
    deep_unescape,
    escape_html_text,
    format_transcription_html,
    html_tables_to_pre,
    html_to_plain,
    markdown_tables_to_pre,
    needs_rich_message,
    prepare_classic_html,
    sanitize_telegram_html,
    split_telegram_html,
)


class TelegramHtmlTests(unittest.TestCase):
    def test_sanitize_removes_ul_li(self) -> None:
        raw = "<b>📌 Кратко</b><br><br>Текст<ul><li>один</li><li>два</li></ul>"
        out = sanitize_telegram_html(raw)
        self.assertNotIn("<ul>", out.lower())
        self.assertNotIn("<li>", out.lower())
        self.assertIn("• один", out)

    def test_sanitize_details_and_summary(self) -> None:
        raw = (
            "<details><summary>Подробнее</summary>"
            "<b>Описание</b><br>Текст</details>"
        )
        out = sanitize_telegram_html(raw)
        self.assertIn("<details>", out)
        self.assertIn("<summary>Подробнее</summary>", out)
        self.assertIn("Текст", out)

        raw = "<blockquote>цитата</blockquote> и <tg-spoiler>секрет</tg-spoiler>"
        out = sanitize_telegram_html(raw)
        self.assertIn("<blockquote>", out)
        self.assertIn("<tg-spoiler>", out)

    def test_html_to_plain(self) -> None:
        plain = html_to_plain("<b>Заголовок</b><br><br>Текст")
        self.assertIn("Заголовок", plain)
        self.assertIn("Текст", plain)

    def test_split_preserves_chunks(self) -> None:
        body = "<b>A</b><br><br>" + ("x" * 100) + "<br><br><b>B</b>"
        chunks = split_telegram_html(body, max_len=80)
        self.assertGreaterEqual(len(chunks), 1)
        joined = "".join(chunks)
        self.assertIn("A", joined)
        self.assertIn("B", joined)

    def test_markdown_table_to_pre(self) -> None:
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        out = markdown_tables_to_pre(md)
        self.assertIn("<pre>", out)
        self.assertIn("A", out)

    def test_needs_rich_message_for_table_tag(self) -> None:
        self.assertTrue(needs_rich_message("<table><tr><td>x</td></tr></table>"))
        self.assertFalse(needs_rich_message("<b>просто</b>"))

    def test_format_transcription_speakers(self) -> None:
        raw = "Спикер 1:\nПривет\n\nСпикер 2:\nПока"
        out = format_transcription_html(raw)
        self.assertIn("<table>", out)
        self.assertIn("<th>Спикер</th>", out)
        self.assertIn("Спикер 1", out)
        self.assertIn("Привет", out)

    def test_prepare_classic_html_heading(self) -> None:
        out = prepare_classic_html("<h2>Заголовок</h2>Текст")
        self.assertIn("<b>Заголовок</b>", out)

    def test_html_tables_to_pre_preserves_rows(self) -> None:
        raw = (
            "<h2>Встречи</h2><table>"
            "<tr><th>Время</th><th>Встреча</th></tr>"
            "<tr><td>09:30</td><td>Офис</td></tr>"
            "<tr><td>10:00</td><td>Тренировка</td></tr>"
            "</table>"
        )
        out = html_tables_to_pre(raw)
        self.assertIn("<pre>", out)
        self.assertIn("09:30", out)
        self.assertIn("Офис", out)
        self.assertIn("Тренировка", out)
        self.assertNotIn("ВремяВстреча", out)

    def test_prepare_classic_html_table_fallback(self) -> None:
        raw = (
            "<table><tr><th>Время</th><th>Встреча</th></tr>"
            "<tr><td>09:30</td><td>Офис</td></tr></table>"
        )
        out = prepare_classic_html(raw)
        self.assertIn("<pre>", out)
        self.assertIn("09:30", out)
        self.assertNotIn("ВремяВстреча", out)

    def test_sanitize_does_not_double_escape_ampersand(self) -> None:
        raw = "<b>Boostra: Взыск &amp; Obuchat AI</b>"
        out = sanitize_telegram_html(raw)
        self.assertIn("&amp; Obuchat", out)
        self.assertNotIn("&amp;amp;", out)

    def test_escape_html_text_preserves_entities(self) -> None:
        self.assertEqual(escape_html_text("a &amp; b"), "a &amp; b")
        self.assertEqual(escape_html_text("a & b"), "a &amp; b")
        self.assertEqual(escape_html_text("a&nbsp;b"), "a&nbsp;b")

    def test_format_events_day_markdown_table(self) -> None:
        from assistant.lib.telegram_rich import format_events_day_markdown

        events = [
            {
                "summary": "Boostra: Взыск &amp; Obuchat AI",
                "start": "2026-06-19T14:00:00",
                "end": "2026-06-19T15:00:00",
                "link": "https://calendar.google.com/event?eid=abc",
            },
            {
                "summary": "Груминг",
                "start": "2026-06-19T15:00:00",
                "end": "2026-06-19T16:00:00",
                "link": "",
            },
        ]
        md = format_events_day_markdown(events, "Встречи на 2026-06-19")
        self.assertIn("## Встречи на 2026-06-19", md)
        self.assertIn("| Время | Встреча |", md)
        self.assertIn("|:------|:--------|", md)
        self.assertIn("14:00-15:00", md)
        self.assertIn("[Boostra: Взыск & Obuchat AI](https://calendar.google.com/event?eid=abc)", md)
        self.assertIn("| 15:00-16:00 | Груминг |", md)


if __name__ == "__main__":
    unittest.main()
