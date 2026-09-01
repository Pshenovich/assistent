"""Тесты LLM agent loop Bitrix24 MCP (без реального MCP/OpenRouter)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

from assistant.integrations import bitrix_mcp_token as token_mod
from assistant.services import bitrix_mcp_agent as agent_mod


class _FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: Optional[dict] = None) -> None:
        self.name = name
        self.description = description
        self.title = name
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


class TestBitrixMcpAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tokens_dir = Path(self.tmp.name) / "tokens"
        self.env_patch = patch.dict(
            os.environ,
            {"BITRIX_MCP_USER_TOKENS_DIR": str(self.tokens_dir)},
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        token_mod.save_user_token(99, "tok")

    @patch.object(agent_mod, "find_task_candidates", new_callable=AsyncMock)
    @patch.object(agent_mod, "is_llm_configured", return_value=True)
    @patch.object(agent_mod, "call_tool_in_session", new_callable=AsyncMock)
    @patch.object(agent_mod, "list_tools_in_session", new_callable=AsyncMock)
    @patch.object(agent_mod, "mcp_session")
    @patch.object(agent_mod, "openrouter_chat_completion")
    def test_agent_tool_loop_then_answer(
        self,
        mock_llm: MagicMock,
        mock_mcp_session: MagicMock,
        mock_list_tools: AsyncMock,
        mock_call_tool: AsyncMock,
        _llm_on: MagicMock,
        mock_find: AsyncMock,
    ) -> None:
        mock_find.return_value = []
        session = object()
        mock_mcp_session.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_mcp_session.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_list_tools.return_value = [_FakeTool("create_task", "Create task")]
        mock_call_tool.return_value = "task_id=1"
        mock_llm.side_effect = [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "create_task",
                                        "arguments": json.dumps({"title": "Test"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Задача создана.",
                        }
                    }
                ]
            },
        ]

        answer = agent_mod.run(99, "создай задачу Test")
        self.assertIn("Задача создана", answer)
        mock_call_tool.assert_awaited_once()

    @patch.object(agent_mod, "is_llm_configured", return_value=True)
    def test_requires_connected_token(self, _llm_on: MagicMock) -> None:
        token_mod.remove_user_token(99)
        with self.assertRaises(RuntimeError):
            agent_mod.run(99, "создай задачу")
