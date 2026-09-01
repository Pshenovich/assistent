"""Тесты Bitrix24 REST поиска."""

import unittest
from unittest.mock import patch

from assistant.integrations import bitrix24_rest as rest_mod


class TestBitrix24RestFilter(unittest.TestCase):
    def test_stage_filter_keeps_zero_in_same_group(self) -> None:
        tasks = [
            {"id": "22859", "title": "[Бустра LLM] A", "stageId": "1053", "groupId": "153"},
            {"id": "22427", "title": "[Бустра LLM] B", "stageId": "0", "groupId": "153"},
            {"id": "99999", "title": "[Бустра LLM] C", "stageId": "721", "groupId": "153"},
        ]

        def fake_post(method: str, payload: dict) -> dict:
            if method == "tasks.task.list":
                return {"result": {"tasks": tasks}}
            return {}

        with patch.object(rest_mod, "webhook_base", return_value="https://example.test/rest/1/x"):
            with patch.object(rest_mod, "_post", side_effect=fake_post):
                out = rest_mod.search_tasks_by_title(
                    "Бустра LLM",
                    group_id=153,
                    stage_ids=[1053],
                    limit=10,
                )
        ids = {x["taskId"] for x in out}
        self.assertEqual(ids, {22859, 22427})

    def test_resolve_stage_ids(self) -> None:
        stages = {
            "1053": {"ID": "1053", "TITLE": "Оценка, клиентские задачи"},
            "1089": {"ID": "1089", "TITLE": "Оценка, продуктовые задачи"},
        }
        with patch.object(rest_mod, "get_group_stages", return_value=stages):
            ids = rest_mod.resolve_stage_ids(
                153,
                status_hint="оценка",
                group_hint="клиентские задачи",
            )
        self.assertEqual(ids, [1053])
