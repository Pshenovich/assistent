"""Тесты Bitrix24 MCP client (без сети)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from assistant.integrations import bitrix_mcp_client as client_mod


class TestBitrixMcpClient(unittest.TestCase):
    def test_normalize_auth_token(self) -> None:
        jwt = "abc.def.ghi"
        self.assertEqual(client_mod.normalize_auth_token(jwt), "Bearer abc.def.ghi")
        self.assertEqual(
            client_mod.normalize_auth_token("Bearer xyz"),
            "Bearer xyz",
        )

    def test_serialize_tool_result_text(self) -> None:
        result = SimpleNamespace(
            content=[SimpleNamespace(text='{"ok": true}')],
            isError=False,
        )
        text = client_mod._serialize_tool_result(result)
        self.assertIn("ok", text)

    def test_serialize_tool_result_error(self) -> None:
        result = SimpleNamespace(
            content=[SimpleNamespace(text="invalid token")],
            isError=True,
        )
        text = client_mod._serialize_tool_result(result)
        self.assertTrue(text.startswith("ERROR:"))

    @patch.object(client_mod, "mcp_session")
    def test_mcp_url_trailing_slash(self, _session_mock) -> None:
        with patch.dict("os.environ", {"BITRIX_MCP_URL": "https://example.com/mcp"}, clear=False):
            self.assertEqual(client_mod.mcp_url(), "https://example.com/mcp/")

    def test_raise_mcp_error_unwraps_task_group(self) -> None:
        try:
            ExceptionGroup
        except NameError:
            from exceptiongroup import ExceptionGroup

        inner = httpx.HTTPStatusError(
            "401",
            request=httpx.Request("POST", "https://mcp.bitrix24.tech/mcp/"),
            response=httpx.Response(401, text="Unauthorized"),
        )
        group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
        with self.assertRaises(RuntimeError) as ctx:
            client_mod._raise_mcp_error(group)
        self.assertIn("HTTP 401", str(ctx.exception))

    def test_raise_mcp_error_streaming_response_body(self) -> None:
        request = httpx.Request("POST", "https://mcp.bitrix24.tech/mcp/")
        response = httpx.Response(500, request=request, content=httpx.ByteStream(b"server error"))
        err = httpx.HTTPStatusError("500", request=request, response=response)
        with self.assertRaises(RuntimeError) as ctx:
            client_mod._raise_mcp_error(err)
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertNotIn("streaming response content", str(ctx.exception))

    def test_raise_mcp_error_401_message(self) -> None:
        request = httpx.Request("POST", "https://mcp.bitrix24.tech/mcp/")
        response = httpx.Response(401, request=request, text="Unauthorized")
        err = httpx.HTTPStatusError("401", request=request, response=response)
        with self.assertRaises(RuntimeError) as ctx:
            client_mod._raise_mcp_error(err)
        self.assertIn("истёк", str(ctx.exception))
        self.assertIn("MCP-подключения", str(ctx.exception))
