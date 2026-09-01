"""Тесты Vexa webhook."""

import json
import unittest
from unittest import mock

from assistant.integrations import vexa_webhook


class TestVexaWebhook(unittest.TestCase):
    def test_recording_completed_triggers_pipeline(self) -> None:
        payload = {
            "event_type": "recording.completed",
            "recording": {
                "id": 99,
                "meeting_id": 16,
                "media_files": [{"id": 1, "type": "audio", "format": "wav"}],
            },
            "meeting": {"id": 16, "native_meeting_id": "12345"},
        }
        job = {
            "id": 7,
            "status": "recording",
            "telegram_user_id": 42,
            "topic": "T",
            "meeting_url": "https://zoom.us/j/1",
        }
        with mock.patch.object(
            vexa_webhook.mrs, "find_job_by_vexa_meeting_id", return_value=job
        ):
            with mock.patch.object(vexa_webhook.mrs, "update_job") as upd:
                with mock.patch.object(
                    vexa_webhook.pipeline, "process_job_recording_async"
                ) as proc:
                    vexa_webhook._handle_recording_completed(payload)
        upd.assert_called()
        proc.assert_called_once_with(7, payload["recording"])

    def test_recording_completed_nested_payload(self) -> None:
        payload = {
            "event_type": "recording.completed",
            "data": {
                "recording": {
                    "id": 99,
                    "meeting_id": 16,
                    "status": "completed",
                    "media_files": [{"id": 1, "type": "audio", "format": "wav"}],
                },
            },
            "meeting": {"id": 16, "native_meeting_id": "12345"},
        }
        job = {
            "id": 7,
            "status": "failed",
            "telegram_user_id": 42,
            "vexa_meeting_id": 16,
            "topic": "T",
        }
        with mock.patch.object(
            vexa_webhook.mrs, "find_job_by_vexa_meeting_id", return_value=job
        ):
            with mock.patch.object(vexa_webhook.mrs, "update_job") as upd:
                with mock.patch.object(
                    vexa_webhook.pipeline, "process_job_recording_async"
                ) as proc:
                    vexa_webhook._handle_recording_completed(payload)
        upd.assert_called()
        proc.assert_called_once()

    def test_meeting_completed_starts_pipeline(self) -> None:
        job = {
            "id": 3,
            "status": "recording",
            "vexa_meeting_id": 16,
            "telegram_user_id": 1,
            "topic": "T",
        }
        rec = {
            "id": 99,
            "status": "completed",
            "media_files": [{"id": 1, "type": "audio"}],
        }
        with mock.patch.object(
            vexa_webhook.mrs, "find_job_by_vexa_meeting_id", return_value=job
        ):
            with mock.patch.object(
                vexa_webhook.meeting_bot, "list_recordings", return_value=[rec]
            ):
                with mock.patch.object(
                    vexa_webhook.pipeline, "process_job_recording_async"
                ) as proc:
                    vexa_webhook._handle_meeting_completed(
                        {"event_type": "meeting.completed", "meeting": {"id": 16}}
                    )
        proc.assert_called_once_with(3, rec)

        with mock.patch.object(vexa_webhook, "webhook_secret", return_value="sec"):
            status, out = vexa_webhook.handle_webhook_request(
                b"{}", authorization="Bearer wrong"
            )
        self.assertEqual(status, 401)
