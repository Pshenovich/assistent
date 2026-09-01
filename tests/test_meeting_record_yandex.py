"""Yandex Disk hook в pipeline записи встреч."""

import threading
import time
from unittest import mock

from assistant.services.meeting_record_pipeline import process_job_recording_async


def _wait_thread(name_prefix: str, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(t.name.startswith(name_prefix) for t in threading.enumerate()):
            return
        time.sleep(0.02)
    raise AssertionError(f"thread {name_prefix} still running")


def test_pipeline_upload_failure_does_not_fail_job(monkeypatch) -> None:
    job = {
        "id": 1,
        "status": "recording",
        "topic": "Demo",
        "meeting_url": "https://zoom.us/j/1",
        "native_meeting_id": "123",
        "telegram_user_id": 42,
    }
    recording = {
        "id": 9,
        "media_files": [{"id": 3, "type": "audio"}],
    }

    monkeypatch.setattr(
        "assistant.services.meeting_record_pipeline.mrs.get_job",
        lambda _jid: dict(job),
    )
    monkeypatch.setattr(
        "assistant.services.meeting_record_pipeline.mrs.list_recipient_user_ids",
        lambda _jid: [42],
    )
    updates: list[dict] = []

    def _update_job(jid, **kwargs):
        updates.append({"id": jid, **kwargs})

    monkeypatch.setattr(
        "assistant.services.meeting_record_pipeline.mrs.update_job",
        _update_job,
    )
    monkeypatch.setattr(
        "assistant.services.meeting_record_pipeline.meeting_bot.pick_audio_media_file",
        lambda _rec: recording["media_files"][0],
    )
    monkeypatch.setattr(
        "assistant.services.meeting_record_pipeline.meeting_bot.download_recording_media",
        lambda *_a, **_k: (b"audio", "meeting.wav"),
    )
    monkeypatch.setattr(
        "assistant.services.meeting_record_pipeline.process_recording_bytes",
        lambda **_k: {
            "summary_text": "ok",
            "transcript": "hi",
            "participant_names": [],
            "meta": {},
            "result": None,
        },
    )
    monkeypatch.setattr(
        "assistant.services.meeting_record_pipeline.persist_meeting_artifacts_for_user",
        lambda **_k: (10, 11),
    )
    monkeypatch.setattr(
        "assistant.services.meeting_record_pipeline.notify_summary_ready",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "assistant.services.meeting_record_reconcile.update_job_status_messages",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "assistant.services.yandex_disk_upload.try_upload_meeting_for_user",
        lambda **_k: {"ok": False, "error": "quota"},
    )

    process_job_recording_async(1, recording)
    _wait_thread("meeting-record-1")

    done = [u for u in updates if u.get("status") == "done"]
    assert done, updates
    failed = [u for u in updates if u.get("status") == "failed"]
    assert not failed
