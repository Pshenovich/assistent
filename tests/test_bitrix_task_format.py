"""Тесты форматирования задач Bitrix24."""

import json
import unittest

from assistant.integrations.bitrix_mcp_portal import fix_bitrix_links, task_url
from assistant.lib.bitrix_task_format import (
    format_task_dropdown_markdown,
    format_task_from_tool_result,
    format_task_list_markdown,
    format_task_markdown,
)


class TestBitrixTaskFormat(unittest.TestCase):
    def test_task_url_from_relative_link(self) -> None:
        link = task_url(
            portal_host="https://obuchat.bitrix24.ru",
            task_id=22749,
            link="/company/personal/user/73/tasks/task/view/22749/",
        )
        self.assertEqual(
            link,
            "https://obuchat.bitrix24.ru/company/personal/user/73/tasks/task/view/22749/",
        )

    def test_fix_example_com_links(self) -> None:
        text = "[Задача](https://example.com/company/personal/user/73/tasks/task/view/22749/)"
        fixed = fix_bitrix_links(text, portal_host="https://obuchat.bitrix24.ru")
        self.assertIn("obuchat.bitrix24.ru", fixed)
        self.assertNotIn("example.com", fixed)

    def test_parse_task_payload_with_suffix(self) -> None:
        raw = 'Task successfully found: {"taskId":22859,"title":"Test"}.'
        from assistant.lib.bitrix_task_format import _parse_task_payload

        data = _parse_task_payload(raw)
        assert data is not None
        self.assertEqual(data.get("taskId"), 22859)

    def test_format_task_dropdown_html(self) -> None:
        task = {
            "taskId": 22749,
            "title": "[Бустра LLM]Перенос BoostraGPT -> Корdекса",
            "description": "Перенести GPT на Кордекса",
            "status": "В работе",
            "stageTitle": "Оценка, клиентские задачи",
            "link": "/company/personal/user/73/tasks/task/view/22749/",
            "creator": {"id": 73, "name": "Жанна"},
            "responsible": {"id": 10, "name": "Антон"},
        }
        text = format_task_dropdown_markdown(
            task,
            portal_host="https://obuchat.bitrix24.ru",
            index=1,
        )
        self.assertIn('<a href="https://obuchat.bitrix24.ru/', text)
        self.assertIn("<details>", text)
        self.assertIn("<summary>Подробнее</summary>", text)
        self.assertIn("<b>Описание</b>", text)
        self.assertIn("Перенести GPT на Кордекса", text)
        self.assertIn("<b>Статус:</b>", text)
        self.assertIn("<b>Исполнитель:</b> Антон", text)
        self.assertNotIn("**>Описание", text)

    def test_format_task_from_tool_result(self) -> None:
        payload = {
            "taskId": 22749,
            "title": "[Бустра LLM]Перенос BoostraGPT -> Корdекса",
            "description": "Перенести GPT на Кордекса",
            "deadline": "2025-09-01",
            "status": "В работе",
            "priority": "Средний",
            "link": "/company/personal/user/73/tasks/task/view/22749/",
            "creator": {"id": 73, "name": "Жанна"},
            "responsible": {"id": 10, "name": "Антон"},
        }
        raw = json.dumps(payload, ensure_ascii=False)
        token = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJvYnVjaGF0LmJpdHJpeDI0LnJ1In0.x"
        text = format_task_from_tool_result(raw, token=token)
        assert text is not None
        self.assertIn("Перенос BoostraGPT", text)
        self.assertIn("obuchat.bitrix24.ru", text)
        self.assertIn("<details>", text)
        self.assertIn("<summary>Подробнее</summary>", text)

    def test_format_task_list_markdown(self) -> None:
        tasks = [
            {
                "taskId": 1,
                "title": "Задача A",
                "description": "Описание A",
                "status": "Новая",
                "responsible": {"name": "Иван"},
            },
            {
                "taskId": 2,
                "title": "Задача B",
                "description": "",
                "status": "В работе",
                "responsible": {"name": "Мария"},
            },
        ]
        text = format_task_list_markdown(
            tasks,
            portal_host="https://obuchat.bitrix24.ru",
            heading="Нашёл 2 задачи",
        )
        self.assertIn("<h2>Нашёл 2 задачи</h2>", text)
        self.assertEqual(text.count("<details>"), 2)
        self.assertIn("Задача A", text)
        self.assertIn("Задача B", text)

    def test_format_task_markdown_alias(self) -> None:
        task = {
            "taskId": 10,
            "title": "Тест",
            "description": "Текст",
            "status": "Новая",
            "responsible": {"name": "Пётр"},
        }
        self.assertEqual(
            format_task_markdown(task, portal_host="https://obuchat.bitrix24.ru"),
            format_task_dropdown_markdown(task, portal_host="https://obuchat.bitrix24.ru"),
        )
