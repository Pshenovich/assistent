"""Тесты Zoom webhook (без сети)."""

import hashlib
import hmac
import json
import unittest
from unittest import mock

from assistant.integrations import zoom_webhook


class TestZoomWebhook(unittest.TestCase):
    def test_url_validation_token(self) -> None:
        secret = "test-secret"
        plain = "abc123"
        enc = hmac.new(secret.encode(), plain.encode(), hashlib.sha256).hexdigest()
        with mock.patch.object(zoom_webhook, "webhook_secret", return_value=secret):
            out = zoom_webhook.handle_webhook_payload(
                {"event": "endpoint.url_validation", "payload": {"plainToken": plain}}
            )
        self.assertEqual(out["plainToken"], plain)
        self.assertEqual(out["encryptedToken"], enc)

    def test_invalid_signature(self) -> None:
        body = b'{"event":"recording.completed","payload":{}}'
        with mock.patch.object(zoom_webhook, "webhook_secret", return_value="sec"):
            status, out = zoom_webhook.handle_webhook_request(
                body, signature="v0=bad", timestamp="123"
            )
        self.assertEqual(status, 401)

    def test_recording_completed_starts_processor(self) -> None:
        secret = "sec"
        payload = {
            "event": "recording.completed",
            "payload": {
                "account_id": "acc",
                "download_token": "tok",
                "object": {
                    "host_id": "host1",
                    "topic": "Test",
                    "id": "mid",
                    "recording_files": [
                        {
                            "id": "f1",
                            "file_type": "M4A",
                            "download_url": "https://example.com/a.m4a",
                            "status": "completed",
                        }
                    ],
                },
            },
        }
        body = json.dumps(payload).encode()
        ts = "1700000000"
        sig_body = f"v0:{ts}:{body.decode()}"
        sig = "v0=" + hmac.new(secret.encode(), sig_body.encode(), hashlib.sha256).hexdigest()
        with mock.patch.object(zoom_webhook, "webhook_secret", return_value=secret):
            with mock.patch.object(zoom_webhook.zoom_oauth, "find_telegram_user_by_zoom_host", return_value=42):
                with mock.patch.object(
                    zoom_webhook.zoom_recording_processor,
                    "process_recording_async",
                ) as proc:
                    status, out = zoom_webhook.handle_webhook_request(
                        body, signature=sig, timestamp=ts
                    )
        self.assertEqual(status, 200)
        self.assertTrue(out.get("ok"))
        proc.assert_called_once()
        self.assertEqual(proc.call_args.kwargs["telegram_user_id"], 42)

    def test_meeting_ended_sets_pending_and_notifies(self) -> None:
        secret = "sec"
        payload = {
            "event": "meeting.ended",
            "payload": {
                "account_id": "acc",
                "object": {
                    "host_id": "host1",
                    "topic": "Standup",
                    "id": "999",
                },
            },
        }
        body = json.dumps(payload).encode()
        ts = "1700000000"
        sig_body = f"v0:{ts}:{body.decode()}"
        sig = "v0=" + hmac.new(secret.encode(), sig_body.encode(), hashlib.sha256).hexdigest()
        with mock.patch.object(zoom_webhook, "webhook_secret", return_value=secret):
            with mock.patch.object(
                zoom_webhook.zoom_oauth, "find_telegram_user_by_zoom_host", return_value=7
            ):
                with mock.patch.object(zoom_webhook.zoom_local_pending, "set_pending") as sp:
                    with mock.patch.object(
                        zoom_webhook.zoom_local_notify,
                        "notify_local_recording_expected",
                    ) as notify:
                        status, out = zoom_webhook.handle_webhook_request(
                            body, signature=sig, timestamp=ts
                        )
        self.assertEqual(status, 200)
        self.assertTrue(out.get("ok"))
        sp.assert_called_once()
        self.assertEqual(sp.call_args.args[0], 7)
        self.assertEqual(sp.call_args.kwargs["topic"], "Standup")
        notify.assert_called_once_with(7, topic="Standup", meeting_id="999")


    def test_app_deauthorized_purges_user(self) -> None:
        secret = "sec"
        payload = {
            "event": "app_deauthorized",
            "payload": {
                "account_id": "acc",
                "user_id": "zoom-host-1",
                "client_id": "cid-test",
                "deauthorization_time": "2019-06-17T13:52:28.632Z",
            },
        }
        body = json.dumps(payload).encode()
        ts = "1700000000"
        sig_body = f"v0:{ts}:{body.decode()}"
        sig = "v0=" + hmac.new(secret.encode(), sig_body.encode(), hashlib.sha256).hexdigest()
        with mock.patch.object(zoom_webhook, "webhook_secret", return_value=secret):
            with mock.patch.object(zoom_webhook.zoom_oauth, "client_id", return_value="cid-test"):
                with mock.patch.object(
                    zoom_webhook.zoom_oauth,
                    "deauthorize_by_zoom_user_id",
                    return_value=True,
                ) as purge:
                    status, out = zoom_webhook.handle_webhook_request(
                        body, signature=sig, timestamp=ts
                    )
        self.assertEqual(status, 200)
        self.assertTrue(out.get("ok"))
        purge.assert_called_once_with("zoom-host-1")


if __name__ == "__main__":
    unittest.main()
