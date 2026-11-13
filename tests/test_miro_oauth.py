"""Подключение Miro (картинки при вставке схем): assistant/integrations/miro_oauth.py."""

import os
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from assistant.integrations import miro_oauth


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class MiroOAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "MIRO_USER_TOKENS_DIR": os.path.join(self.tmp.name, "tok"),
            "MIRO_OAUTH_PENDING_DIR": os.path.join(self.tmp.name, "pend"),
            "MIRO_OAUTH_CLIENT_ID": "cid",
            "MIRO_OAUTH_CLIENT_SECRET": "sec",
            "MIRO_OAUTH_REDIRECT_URI": "https://getorbita.me/oauth/miro/callback",
            "MIRO_API_BASE": "https://proxy.example/key/miro/",
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_authorize_url(self) -> None:
        state = miro_oauth.register_oauth_state(42)
        u = urlparse(miro_oauth.build_authorization_url(state))
        self.assertEqual(f"{u.scheme}://{u.netloc}{u.path}", "https://miro.com/oauth/authorize")
        q = parse_qs(u.query)
        self.assertEqual(q["client_id"], ["cid"])
        self.assertEqual(q["state"], [state])
        self.assertEqual(q["redirect_uri"], ["https://getorbita.me/oauth/miro/callback"])

    def test_exchange_via_proxy_and_one_time_state(self) -> None:
        state = miro_oauth.register_oauth_state(42)
        calls = []

        def post(url, timeout):
            calls.append(url)
            return _Resp(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "team_id": 7})

        with mock.patch.object(miro_oauth.requests, "post", side_effect=post):
            self.assertEqual(miro_oauth.exchange_code_and_save_token("CODE", state), 42)
            # Тот же state второй раз не принимается.
            with self.assertRaises(ValueError):
                miro_oauth.exchange_code_and_save_token("CODE", state)
        u = urlparse(calls[0])
        self.assertEqual(f"{u.scheme}://{u.netloc}{u.path}", "https://proxy.example/key/miro/v1/oauth/token")
        q = parse_qs(u.query)
        self.assertEqual((q["grant_type"], q["code"], q["client_secret"]), (["authorization_code"], ["CODE"], ["sec"]))
        self.assertTrue(miro_oauth.is_connected(42))
        self.assertEqual(miro_oauth.get_access_token(42), "AT")
        self.assertEqual(oct(os.stat(miro_oauth.user_token_path(42)).st_mode & 0o777), "0o600")

    def test_refresh_when_expired(self) -> None:
        state = miro_oauth.register_oauth_state(5)
        with mock.patch.object(miro_oauth.requests, "post", return_value=_Resp(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})):
            miro_oauth.exchange_code_and_save_token("C", state)
        with mock.patch.object(miro_oauth.time, "time", return_value=time.time() + 7200), \
                mock.patch.object(miro_oauth.requests, "post", return_value=_Resp(200, {"access_token": "A2", "expires_in": 3600})) as post:
            self.assertEqual(miro_oauth.get_access_token(5), "A2")
            self.assertIn("grant_type=refresh_token", post.call_args[0][0])
        # Новый refresh_token не пришёл — старый остаётся.
        self.assertIn('"refresh_token": "R1"', miro_oauth.user_token_path(5).read_text())

    def test_non_expiring_token(self) -> None:
        # Бессрочный ключ: Miro присылает expires_in 0 и без refresh_token — ключ действует.
        state = miro_oauth.register_oauth_state(3)
        with mock.patch.object(miro_oauth.requests, "post", return_value=_Resp(200, {"access_token": "NE", "expires_in": 0})):
            miro_oauth.exchange_code_and_save_token("C", state)
        self.assertNotIn("expires_at", miro_oauth.user_token_path(3).read_text())
        self.assertEqual(miro_oauth.get_access_token(3), "NE")
        # Старый файл с уже прошедшим сроком и без refresh_token (так сохранил первый вход на проде) — тоже ключ.
        miro_oauth.user_token_path(3).write_text('{"access_token": "OLD", "expires_at": 1}')
        with mock.patch.object(miro_oauth.requests, "post") as post:
            self.assertEqual(miro_oauth.get_access_token(3), "OLD")
            post.assert_not_called()

    def test_bad_state_and_errors(self) -> None:
        with self.assertRaises(ValueError):
            miro_oauth.exchange_code_and_save_token("C", "../x")
        state = miro_oauth.register_oauth_state(1)
        with mock.patch.object(miro_oauth.requests, "post", return_value=_Resp(403, {"error": "geo"})):
            with self.assertRaises(RuntimeError):
                miro_oauth.exchange_code_and_save_token("C", state)
        self.assertFalse(miro_oauth.is_connected(1))
        self.assertIsNone(miro_oauth.get_access_token(1))

    def test_disconnect_and_not_configured(self) -> None:
        state = miro_oauth.register_oauth_state(9)
        with mock.patch.object(miro_oauth.requests, "post", return_value=_Resp(200, {"access_token": "X"})):
            miro_oauth.exchange_code_and_save_token("C", state)
        self.assertTrue(miro_oauth.remove_user_token(9))
        self.assertFalse(miro_oauth.is_connected(9))
        with mock.patch.dict(os.environ, {"MIRO_OAUTH_CLIENT_SECRET": ""}):
            self.assertFalse(miro_oauth.is_configured())
            with self.assertRaises(RuntimeError):
                miro_oauth.build_authorization_url("s")


if __name__ == "__main__":
    unittest.main()
