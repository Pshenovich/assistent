"""Тесты отключения Zoom OAuth."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant.integrations import zoom_oauth


class TestZoomOAuthDisconnect(unittest.TestCase):
    def test_disconnect_user_revokes_and_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            with mock.patch.object(zoom_oauth, "_tokens_dir", return_value=tdir):
                uid = 1001
                path = zoom_oauth.user_token_path(uid)
                path.write_text(
                    json.dumps({"access_token": "at-1", "refresh_token": "rt-1"}),
                    encoding="utf-8",
                )
                zoom_oauth.register_zoom_host("zuid-1", uid)
                with mock.patch.object(zoom_oauth, "revoke_access_token", return_value=True) as rev:
                    ok = zoom_oauth.disconnect_user(uid)
                self.assertTrue(ok)
                rev.assert_called_once_with("at-1")
                self.assertFalse(path.exists())
                self.assertIsNone(zoom_oauth.find_telegram_user_by_zoom_host("zuid-1"))

    def test_deauthorize_by_zoom_user_id_without_revoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            with mock.patch.object(zoom_oauth, "_tokens_dir", return_value=tdir):
                uid = 42
                path = zoom_oauth.user_token_path(uid)
                path.write_text(json.dumps({"access_token": "at"}), encoding="utf-8")
                zoom_oauth.register_zoom_host("host-42", uid)
                with mock.patch.object(zoom_oauth, "revoke_access_token") as rev:
                    ok = zoom_oauth.deauthorize_by_zoom_user_id("host-42")
                self.assertTrue(ok)
                rev.assert_not_called()
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
