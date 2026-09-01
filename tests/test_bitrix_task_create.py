"""Тесты создания задач Bitrix24."""

import unittest
from unittest.mock import AsyncMock, patch

from assistant.services import bitrix_task_create as create_mod


class TestBitrixTaskCreateParse(unittest.TestCase):
    def test_parse_user_example(self) -> None:
        text = (
            "заведи задачу «[Бустра LLM] Тестирую бота», "
            "описание: сделать один, два, три исполнитель: Артем"
        )
        req = create_mod.parse_create_task_request(text)
        assert req is not None
        self.assertEqual(req.title, "[Бустра LLM] Тестирую бота")
        self.assertEqual(req.description, "сделать один, два, три")
        self.assertEqual(req.assignee_name, "Артем")

    def test_is_create_request(self) -> None:
        self.assertTrue(create_mod.is_create_task_request("заведи задачу «Test»"))
        self.assertFalse(create_mod.is_create_task_request("найди задачи про бустра"))


class TestBitrixTaskCreateResolve(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_responsible_id(self) -> None:
        raw = 'Users successfully found: [{"ID":"367","LAST_NAME":"","NAME":"Артем"}].'
        with patch.object(create_mod, "call_tool", new_callable=AsyncMock, return_value=raw):
            uid, name = await create_mod.resolve_responsible_id("tok", "Артем")
        self.assertEqual(uid, 367)
        self.assertEqual(name, "Артем")

    async def test_create_task_sets_responsible(self) -> None:
        req = create_mod.CreateTaskRequest(
            title="[Бустра LLM] Test",
            description="one two",
            assignee_name="Артем",
            group_id=153,
        )
        users_raw = 'Users successfully found: [{"ID":"367","NAME":"Артем"}].'
        create_raw = 'Task created: {"taskId":99999}.'
        detail_raw = 'Task successfully found: {"taskId":99999,"title":"[Бустра LLM] Test","description":"one two","status":"pending","responsible":{"id":367,"name":"Артем"},"creator":{"id":73,"name":"Me"},"link":"/company/personal/user/73/tasks/task/view/99999/"}.'

        async def fake_call(_token, name, args=None):
            if name == "search_users":
                return users_raw
            if name == "create_task":
                self.assertEqual(args.get("responsibleId"), 367)
                self.assertEqual(args.get("title"), "[Бустра LLM] Test")
                self.assertEqual(args.get("groupId"), 153)
                return create_raw
            if name == "get_task_by_id":
                return detail_raw
            return ""

        with patch.object(create_mod, "call_tool", side_effect=fake_call):
            out = await create_mod.create_task_direct("tok", req)
        self.assertIn("Задача создана", out)
        self.assertIn("Артем", out)
