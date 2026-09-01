"""Тесты Telegram Login Widget и browser session."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import unittest
from unittest import mock

from assistant.lib import telegram_login_auth as tla


class TestTelegramLoginAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.token = "123456:TEST_BOT_TOKEN"
        self.env = mock.patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": self.token},
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    def _widget_payload(self, *, uid: int = 42, age_sec: int = 0) -> dict:
        data = {
            "id": uid,
            "first_name": "Test",
            "username": "tester",
            "auth_date": int(time.time()) - age_sec,
        }
        check_pairs = [f"{k}={data[k]}" for k in sorted(data.keys())]
        data_check_string = "\n".join(check_pairs)
        secret_key = hashlib.sha256(self.token.encode()).digest()
        data["hash"] = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        return data

    def test_verify_login_widget_ok(self) -> None:
        user = tla.verify_login_widget_payload(self._widget_payload(), bot_token=self.token)
        self.assertEqual(user["id"], 42)
        self.assertEqual(user["username"], "tester")

    def test_verify_login_widget_bad_hash(self) -> None:
        data = self._widget_payload()
        data["hash"] = "deadbeef"
        with self.assertRaises(ValueError):
            tla.verify_login_widget_payload(data, bot_token=self.token)

    def test_browser_session_roundtrip(self) -> None:
        user = {"id": 99, "username": "u99", "first_name": "N"}
        token, exp = tla.issue_browser_session(99, user)
        self.assertGreater(exp, int(time.time()))
        parsed = tla.verify_browser_session(token)
        self.assertIsNotNone(parsed)
        uid, u = parsed  # type: ignore[misc]
        self.assertEqual(uid, 99)
        self.assertEqual(u["username"], "u99")

    def test_browser_session_expired(self) -> None:
        with mock.patch.object(tla, "_session_ttl_sec", return_value=-10):
            token, _ = tla.issue_browser_session(1, {"id": 1})
        self.assertIsNone(tla.verify_browser_session(token))


if __name__ == "__main__":
    unittest.main()
