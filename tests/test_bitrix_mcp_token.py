"""Тесты хранения токенов Bitrix24 MCP."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant.integrations import bitrix_mcp_token as token_mod


class TestBitrixMcpToken(unittest.TestCase):
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

    def test_save_get_remove(self) -> None:
        tid = 4242
        self.assertFalse(token_mod.is_connected(tid))
        token_mod.save_user_token(tid, "secret-token")
        self.assertTrue(token_mod.is_connected(tid))
        self.assertEqual(token_mod.get_user_token(tid), "secret-token")
        path = token_mod.user_token_path(tid)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["token"], "secret-token")
        self.assertTrue(token_mod.remove_user_token(tid))
        self.assertFalse(token_mod.is_connected(tid))
        self.assertIsNone(token_mod.get_user_token(tid))

    def test_empty_token_rejected(self) -> None:
        with self.assertRaises(ValueError):
            token_mod.save_user_token(1, "   ")

    def test_strip_bearer_and_expiry(self) -> None:
        import time

        payload = {"exp": int(time.time()) - 60}
        body = json.dumps(payload, separators=(",", ":")).encode()
        import base64

        b64 = base64.urlsafe_b64encode(body).decode().rstrip("=")
        jwt = f"header.{b64}.sig"
        token_mod.save_user_token(7, f"Bearer {jwt}")
        self.assertEqual(token_mod.get_user_token(7), jwt)
        self.assertTrue(token_mod.is_token_expired(jwt))
