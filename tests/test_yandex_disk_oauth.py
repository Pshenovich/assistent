import json
import time
import unittest
from unittest import mock

from assistant.integrations import yandex_disk_oauth


class YandexDiskOAuthTests(unittest.TestCase):
    def test_build_authorization_url_contains_scope(self) -> None:
        with mock.patch.object(yandex_disk_oauth, "client_id", return_value="cid"):
            with mock.patch.object(
                yandex_disk_oauth, "redirect_uri", return_value="https://example/cb"
            ):
                with mock.patch.object(yandex_disk_oauth, "oauth_scope", return_value="cloud_api:disk.write"):
                    with mock.patch.object(yandex_disk_oauth, "_pending_dir") as pending_dir:
                        pdir = yandex_disk_oauth.HERE / "yandex_disk_oauth_pending"
                        pending_dir.return_value = pdir
                        pdir.mkdir(parents=True, exist_ok=True)
                        state = yandex_disk_oauth.register_oauth_state(11)
                        url = yandex_disk_oauth.build_authorization_url(state)
                        self.assertIn("oauth.yandex.ru/authorize", url)
                        self.assertIn("cloud_api%3Adisk.write", url)
                        (pdir / f"{state}.json").unlink(missing_ok=True)

    def test_exchange_code_and_save_token(self) -> None:
        with mock.patch.object(yandex_disk_oauth, "client_id", return_value="cid"):
            with mock.patch.object(yandex_disk_oauth, "client_secret", return_value="sec"):
                with mock.patch.object(
                    yandex_disk_oauth, "redirect_uri", return_value="https://example/cb"
                ):
                    with mock.patch.object(yandex_disk_oauth, "_pending_dir") as pending_dir:
                        with mock.patch.object(yandex_disk_oauth, "_tokens_dir") as tokens_dir:
                            pdir = yandex_disk_oauth.HERE / "yandex_disk_oauth_pending"
                            tdir = yandex_disk_oauth.HERE / "yandex_disk_user_tokens"
                            pending_dir.return_value = pdir
                            tokens_dir.return_value = tdir
                            pdir.mkdir(parents=True, exist_ok=True)
                            tdir.mkdir(parents=True, exist_ok=True)
                            state = "state-yd-1"
                            pending = pdir / f"{state}.json"
                            pending.write_text(
                                json.dumps(
                                    {"telegram_user_id": 5, "expires_at": time.time() + 600}
                                ),
                                encoding="utf-8",
                            )
                            token_resp = mock.Mock()
                            token_resp.ok = True
                            token_resp.raise_for_status = mock.Mock()
                            token_resp.json.return_value = {
                                "access_token": "at-1",
                                "refresh_token": "rt-1",
                                "expires_in": 3600,
                                "token_type": "bearer",
                            }
                            profile_resp = mock.Mock()
                            profile_resp.ok = True
                            profile_resp.raise_for_status = mock.Mock()
                            profile_resp.json.return_value = {
                                "login": "user",
                                "default_email": "user@yandex.ru",
                                "display_name": "User",
                            }

                            def _fake_get(url, **kwargs):
                                self.assertIn("login.yandex.ru", url)
                                return profile_resp

                            with mock.patch(
                                "assistant.integrations.yandex_disk_oauth.requests.post",
                                return_value=token_resp,
                            ), mock.patch(
                                "assistant.integrations.yandex_disk_oauth.requests.get",
                                side_effect=_fake_get,
                            ):
                                uid = yandex_disk_oauth.exchange_code_and_save_token(
                                    "code-1", state
                                )
                            self.assertEqual(uid, 5)
                            store = json.loads((tdir / "5.json").read_text(encoding="utf-8"))
                            self.assertEqual(store["access_token"], "at-1")
                            self.assertEqual(store["yandex_login"], "user")
                            self.assertFalse(pending.exists())
                            (tdir / "5.json").unlink(missing_ok=True)

    def test_disconnect_user(self) -> None:
        with mock.patch.object(yandex_disk_oauth, "_tokens_dir") as tokens_dir:
            tdir = yandex_disk_oauth.HERE / "yandex_disk_user_tokens"
            tokens_dir.return_value = tdir
            tdir.mkdir(parents=True, exist_ok=True)
            path = tdir / "9.json"
            path.write_text("{}", encoding="utf-8")
            self.assertTrue(yandex_disk_oauth.disconnect_user(9))
            self.assertFalse(path.exists())


    def test_uses_verification_code_flow(self) -> None:
        with mock.patch.object(
            yandex_disk_oauth,
            "redirect_uri",
            return_value="https://oauth.yandex.ru/verification_code",
        ):
            self.assertTrue(yandex_disk_oauth.uses_verification_code_flow())

    def test_exchange_verification_code(self) -> None:
        with mock.patch.object(yandex_disk_oauth, "client_id", return_value="cid"):
            with mock.patch.object(yandex_disk_oauth, "client_secret", return_value="sec"):
                with mock.patch.object(
                    yandex_disk_oauth,
                    "redirect_uri",
                    return_value="https://oauth.yandex.ru/verification_code",
                ):
                    with mock.patch.object(yandex_disk_oauth, "_pending_dir") as pending_dir:
                        with mock.patch.object(yandex_disk_oauth, "_tokens_dir") as tokens_dir:
                            pdir = yandex_disk_oauth.HERE / "yandex_disk_oauth_pending"
                            tdir = yandex_disk_oauth.HERE / "yandex_disk_user_tokens"
                            pending_dir.return_value = pdir
                            tokens_dir.return_value = tdir
                            pdir.mkdir(parents=True, exist_ok=True)
                            tdir.mkdir(parents=True, exist_ok=True)
                            user_pending = pdir / "user_5.json"
                            user_pending.write_text(
                                json.dumps(
                                    {
                                        "telegram_user_id": 5,
                                        "expires_at": time.time() + 600,
                                        "state": "st-1",
                                    }
                                ),
                                encoding="utf-8",
                            )
                            token_resp = mock.Mock()
                            token_resp.ok = True
                            token_resp.raise_for_status = mock.Mock()
                            token_resp.json.return_value = {
                                "access_token": "at-1",
                                "refresh_token": "rt-1",
                                "expires_in": 3600,
                            }
                            profile_resp = mock.Mock()
                            profile_resp.ok = True
                            profile_resp.raise_for_status = mock.Mock()
                            profile_resp.json.return_value = {"login": "user"}

                            with mock.patch(
                                "assistant.integrations.yandex_disk_oauth.requests.post",
                                return_value=token_resp,
                            ) as post_mock, mock.patch(
                                "assistant.integrations.yandex_disk_oauth.requests.get",
                                return_value=profile_resp,
                            ):
                                uid = yandex_disk_oauth.exchange_verification_code("code-xyz", 5)
                            self.assertEqual(uid, 5)
                            post_data = post_mock.call_args.kwargs.get("data") or post_mock.call_args[1].get("data")
                            self.assertEqual(
                                post_data.get("redirect_uri"),
                                "https://oauth.yandex.ru/verification_code",
                            )
                            self.assertFalse(user_pending.exists())
                            (tdir / "5.json").unlink(missing_ok=True)

    def test_needs_scope_refresh_when_only_write(self) -> None:
        with mock.patch.object(yandex_disk_oauth, "_tokens_dir") as tokens_dir:
            tdir = yandex_disk_oauth.HERE / "yandex_disk_user_tokens"
            tokens_dir.return_value = tdir
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "7.json").write_text(
                json.dumps({"access_token": "x", "scope": "cloud_api:disk.write"}),
                encoding="utf-8",
            )
            self.assertTrue(yandex_disk_oauth.needs_scope_refresh(7))
            (tdir / "7.json").write_text(
                json.dumps(
                    {
                        "access_token": "x",
                        "scope": "cloud_api:disk.read cloud_api:disk.write",
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(yandex_disk_oauth.needs_scope_refresh(7))
            (tdir / "7.json").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
