import unittest

from assistant.lib.telegram_markdown import (
    append_source_footer_markdown,
    html_to_rich_markdown,
    inject_tasks_checklists_markdown,
    prepare_summary_markdown,
)


class TelegramMarkdownTests(unittest.TestCase):
    def test_html_headings_and_list(self) -> None:
        raw = "<h2>📌 Кратко</h2><p>Текст раздела.</p><ul><li>один</li><li checked>два</li></ul>"
        out = html_to_rich_markdown(raw)
        self.assertIn("## 📌 Кратко", out)
        self.assertIn("- один", out)
        self.assertIn("- [x] два", out)

    def test_html_table(self) -> None:
        raw = (
            "<table><tr><th>Ответственный</th><th>Задача</th></tr>"
            "<tr><td>Иван</td><td><a href=\"https://t.me/\">Сделать</a></td></tr></table>"
        )
        out = html_to_rich_markdown(raw)
        self.assertIn("| Ответственный | Задача |", out)
        self.assertIn("[Сделать](https://t.me/)", out)

    def test_prepare_summary_with_tasks(self) -> None:
        html_body = "<h2>📌 Кратко</h2><p>Обсуждали релиз.</p>"
        tasks = [{"assignee": "Иван", "task": "Подготовить демо", "deadline": "пятница", "completed": False}]
        out = prepare_summary_markdown(
            html_body,
            tasks=tasks,
            headline="Встреча",
            ts="2026-06-16T15:30:00",
        )
        self.assertIn("## Встреча", out)
        self.assertIn("*16 июня 2026, 15:30*", out)
        self.assertIn("## 📋 Задачи", out)
        self.assertIn("- [ ] Иван — Подготовить демо", out)

    def test_inject_tasks_replaces_section(self) -> None:
        body = "## 📌 Кратко\n\nТекст\n\n## 📋 Задачи\n\n- старый пункт"
        tasks = [{"task": "Новая задача", "completed": False}]
        out = inject_tasks_checklists_markdown(body, tasks)
        self.assertIn("Новая задача", out)
        self.assertNotIn("старый пункт", out)

    def test_bold_sections_and_inline_bullets(self) -> None:
        raw = (
            "<b>📌 Кратко</b><br><br>Краткий текст.<br><br>"
            "<b>🎯 Что обсуждали</b><br><br>"
            "• пункт один • пункт два • пункт три"
        )
        out = html_to_rich_markdown(raw)
        self.assertIn("## 📌 Кратко", out)
        self.assertIn("## 🎯 Что обсуждали", out)
        self.assertIn("- пункт один", out)
        self.assertIn("- пункт два", out)
        self.assertNotIn("• пункт один •", out)

    def test_tasks_section_replaced_not_duplicated(self) -> None:
        html_body = (
            "<b>📌 Кратко</b><br><br>Текст<br><br>"
            "<b>📋 Задачи</b><br><br><ul><li>старая задача</li></ul>"
        )
        tasks = [
            {"assignee": "Иван", "task": "Новая задача", "completed": False},
        ]
        out = prepare_summary_markdown(html_body, tasks=tasks)
        self.assertEqual(out.count("## 📋 Задачи"), 1)
        self.assertIn("- [ ] Иван — Новая задача", out)
        self.assertNotIn("старая задача", out)

    def test_prepare_journal_qa_markdown(self) -> None:
        from assistant.lib.telegram_markdown import prepare_journal_qa_markdown

        raw = "**📋 Задачи**\n\n- [ ] Иван — настроить API"
        out = prepare_journal_qa_markdown(raw)
        self.assertIn("## 📋 Задачи", out)
        self.assertIn("- [ ] Иван — настроить API", out)

    def test_zoom_footer_rich_block(self) -> None:
        body = "## 📌 Кратко\n\nТекст"
        out = append_source_footer_markdown(
            body,
            source_url="https://zoom.us/j/123",
            telegram_link=None,
        )
        self.assertNotIn("[^zoom]", out)
        self.assertTrue(out.endswith("</footer>"))
        self.assertIn(
            '<footer><a href="https://zoom.us/j/123">🔗 Zoom-встреча</a></footer>',
            out,
        )
        self.assertLess(out.index("Текст"), out.index("<footer>"))
        self.assertNotIn("🔗 Zoom-встреча", out.split("<footer>")[0])

    def test_html_zoom_ref_stripped_from_body(self) -> None:
        raw = (
            "<h2>📌 Кратко</h2><p>Текст</p>"
            '<p><a href="#zoom">🔗 Zoom-встреча</a></p>'
            '<footer>[^zoom]: <a href="https://zoom.us/j/1">Zoom-встреча</a></footer>'
        )
        out = prepare_summary_markdown(
            raw,
            source_url="https://zoom.us/j/1",
        )
        self.assertNotIn("🔗 Zoom-встреча", out.split("<footer>")[0])
        self.assertIn('<footer><a href="https://zoom.us/j/1">🔗 Zoom-встреча</a></footer>', out)

    def test_trailing_zoom_html_link_stripped(self) -> None:
        raw = (
            "<b>🔜 Следующие шаги</b><br><br>• Встреча на следующей неделе<br><br>"
            '🔗 <a href="https://us04web.zoom.us/j/75244037047?pwd=abc">Zoom-встреча</a>'
        )
        out = prepare_summary_markdown(raw, source_url="https://us04web.zoom.us/j/75244037047?pwd=abc")
        body = out.split("<footer>", 1)[0]
        self.assertNotIn("Zoom", body)
        self.assertIn('<footer><a href="https://us04web.zoom.us/j/75244037047?pwd=abc">🔗 Zoom-встреча</a></footer>', out)


if __name__ == "__main__":
    unittest.main()
