import json
import time
import unittest
from unittest import mock

from assistant.integrations import zoom_oauth


class ZoomOAuthExchangeTests(unittest.TestCase):
    def test_register_oauth_state_stores_client_id(self) -> None:
        with mock.patch.object(zoom_oauth, "client_id", return_value="cid-test"):
            with mock.patch.object(zoom_oauth, "_pending_dir") as pending_dir:
                tmp = zoom_oauth.HERE / "zoom_oauth_pending"
                pending_dir.return_value = tmp
                tmp.mkdir(parents=True, exist_ok=True)
                state = zoom_oauth.register_oauth_state(42)
                data = json.loads((tmp / f"{state}.json").read_text(encoding="utf-8"))
                self.assertEqual(data["oauth_client_id"], "cid-test")
                (tmp / f"{state}.json").unlink(missing_ok=True)

    def test_invalid_grant_treated_as_success_if_token_just_saved(self) -> None:
        with mock.patch.object(zoom_oauth, "client_id", return_value="cid-test"):
            with mock.patch.object(zoom_oauth, "redirect_uri", return_value="https://example/cb"):
                with mock.patch.object(zoom_oauth, "_basic_auth_header", return_value="Basic x"):
                    with mock.patch.object(zoom_oauth, "_pending_dir") as pending_dir:
                        with mock.patch.object(zoom_oauth, "_tokens_dir") as tokens_dir:
                            pdir = zoom_oauth.HERE / "zoom_oauth_pending"
                            tdir = zoom_oauth.HERE / "zoom_user_tokens"
                            pending_dir.return_value = pdir
                            tokens_dir.return_value = tdir
                            pdir.mkdir(parents=True, exist_ok=True)
                            tdir.mkdir(parents=True, exist_ok=True)
                            state = "state-test-1"
                            pending = pdir / f"{state}.json"
                            pending.write_text(
                                json.dumps(
                                    {
                                        "telegram_user_id": 7,
                                        "expires_at": time.time() + 600,
                                        "oauth_client_id": "cid-test",
                                    }
                                ),
                                encoding="utf-8",
                            )
                            token_path = tdir / "7.json"
                            token_path.write_text(
                                json.dumps(
                                    {
                                        "access_token": "at",
                                        "oauth_client_id": "cid-test",
                                        "saved_at": time.time(),
                                    }
                                ),
                                encoding="utf-8",
                            )
                            resp = mock.Mock()
                            resp.ok = False
                            resp.status_code = 400
                            resp.json.return_value = {
                                "error": "invalid_grant",
                                "reason": "Invalid authorization code",
                            }
                            with mock.patch(
                                "assistant.integrations.zoom_oauth.requests.post",
                                return_value=resp,
                            ):
                                uid = zoom_oauth.exchange_code_and_save_token(
                                    "code-1", state
                                )
                            self.assertEqual(uid, 7)
                            pending.unlink(missing_ok=True)
                            token_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
