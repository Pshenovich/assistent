"""Тесты сверки задач записи с Vexa."""

import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

import assistant.stores.meeting_recordings_store as mrs
from assistant.services import meeting_record_reconcile as mrr
from assistant.services.meeting_record_reconcile import (
    dedupe_action,
    is_join_blocked,
    is_waiting_room,
    join_blocked_message,
    reconcile_job,
)


def test_reconcile_promotes_joining_to_recording_when_active(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=1,
            source="zoom_link",
            topic="T",
            meeting_url="https://zoom.us/j/123",
            native_meeting_id="123",
            status="joining",
        )
        mrs.update_job(job_id, vexa_meeting_id=2, status="joining")
        job = mrs.get_job(job_id)
        assert job is not None
        with mock.patch.object(
            mrr.meeting_bot,
            "fetch_vexa_meeting",
            return_value={"status": "active", "updated_at": job["updated_at_utc"]},
        ):
            assert dedupe_action(job) == "subscribe"
            updated = reconcile_job(job)
        assert updated["status"] == "recording"


def test_dedupe_create_new_when_vexa_failed(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=1,
            source="zoom_link",
            topic="T",
            meeting_url="https://zoom.us/j/123",
            native_meeting_id="123",
            status="recording",
        )
        mrs.update_job(job_id, vexa_meeting_id=2, status="recording")
        job = mrs.get_job(job_id)
        assert job is not None
        with mock.patch.object(
            mrr.meeting_bot,
            "fetch_vexa_meeting",
            return_value={"status": "failed", "updated_at": job["updated_at_utc"]},
        ):
            with mock.patch.object(mrr.meeting_bot, "stop_zoom_bot"):
                assert dedupe_action(job) == "create_new"
        failed = mrs.get_job(job_id)
        assert failed is not None
        assert failed["status"] == "failed"


def test_stuck_needs_human_help_message() -> None:
    meeting = {"status": "needs_human_help", "data": {"participants_count": 0}}
    assert is_join_blocked(meeting) is True
    msg = join_blocked_message(topic="T", job={"meta": {"obf_sent": True}})
    assert "нужен zoom meeting sdk" not in msg.lower()
    assert "obf-токен отправлен" in msg.lower()


def test_join_blocked_without_zoom_auth() -> None:
    msg = join_blocked_message(topic="T", job={"meta": {"obf_missing": True}})
    assert "/zoom_auth" in msg
    assert "user:read:token" in msg


def test_waiting_room_when_needs_human_help_unknown_blocking() -> None:
    meeting = {
        "status": "needs_human_help",
        "start_time": None,
        "data": {
            "participants_count": 0,
            "escalation": {"reason": "unknown_blocking_state"},
        },
    }
    assert is_waiting_room(meeting) is False
    assert is_join_blocked(meeting) is True


def test_waiting_room_when_awaiting_admission() -> None:
    meeting = {"status": "awaiting_admission", "data": {"participants_count": 0}}
    assert is_waiting_room(meeting) is True
    assert is_join_blocked(meeting) is False


def test_parse_iso_naive_vexa_timestamp_is_utc_aware() -> None:
    from assistant.services.meeting_record_reconcile import _parse_iso

    dt = _parse_iso("2026-06-15T13:49:46.133195")
    assert dt is not None
    assert dt.tzinfo is not None
    now = datetime.now(timezone.utc)
    assert (now - dt).total_seconds() >= 0


def test_poll_joining_jobs_with_naive_vexa_updated_at(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=1,
            source="zoom_instant",
            topic="T",
            meeting_url="https://zoom.us/j/123",
            native_meeting_id="123",
            status="joining",
        )
        mrs.update_job(job_id, vexa_meeting_id=9, status="joining")
        job = mrs.get_job(job_id)
        assert job is not None
        old_job_updated = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).isoformat()
        mrs.update_job(job_id, status="joining")
        job = mrs.get_job(job_id)
        meeting = {
            "status": "needs_human_help",
            "start_time": None,
            "updated_at": "2026-06-15T13:49:46.133195",
            "data": {
                "participants_count": 0,
                "escalation": {"reason": "unknown_blocking_state"},
            },
        }
        with mock.patch.object(mrr.meeting_bot, "fetch_vexa_meeting", return_value=meeting):
            with mock.patch.object(mrr, "reconcile_job", side_effect=lambda j: j):
                with mock.patch(
                    "assistant.services.meeting_record_reconcile.update_job_status_messages"
                ) as notify:
                    # force updated_at old enough for poll
                    job["updated_at_utc"] = old_job_updated
                    with mock.patch.object(mrs, "list_jobs_by_statuses", return_value=[job]):
                        with mock.patch.object(
                            mrs, "list_recipient_user_ids", return_value=[1]
                        ):
                            sent = mrr.poll_joining_jobs()
        assert sent == 1
        notify.assert_called_once()


def test_stuck_needs_human_help_retries_after_timeout(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=1,
            source="zoom_link",
            topic="T",
            meeting_url="https://zoom.us/j/123",
            native_meeting_id="123",
            status="recording",
        )
        old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        mrs.update_job(job_id, vexa_meeting_id=2, status="recording")
        job = mrs.get_job(job_id)
        assert job is not None
        with mock.patch.object(
            mrr.meeting_bot,
            "fetch_vexa_meeting",
            return_value={"status": "needs_human_help", "updated_at": old},
        ):
            with mock.patch.object(mrr.meeting_bot, "stop_zoom_bot") as stop:
                assert dedupe_action(job) == "create_new"
                stop.assert_called_once()
        failed = mrs.get_job(job_id)
        assert failed is not None
        assert failed["status"] == "failed"


def test_join_not_blocked_when_in_meeting_with_start_time() -> None:
    meeting = {
        "status": "needs_human_help",
        "start_time": "2026-06-15T14:03:14",
        "data": {"escalation": {"reason": "audio_join_failed: ..."}},
    }
    assert is_join_blocked(meeting) is False


def test_update_job_status_messages_stores_message_ids(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=1,
            source="zoom_link",
            topic="T",
            meeting_url="https://zoom.us/j/123",
            native_meeting_id="123",
            status="joining",
        )
        calls: list[tuple] = []

        def fake_upsert(uid, text, *, message_id=None):
            calls.append((uid, text, message_id))
            return 42 if message_id is None else message_id

        with mock.patch(
            "assistant.lib.telegram_notify.upsert_user_status_message",
            side_effect=fake_upsert,
        ):
            assert mrr.update_job_status_messages(job_id, "first", [1]) == 1
            assert mrr.update_job_status_messages(job_id, "second", [1]) == 1
        job = mrs.get_job(job_id)
        assert job is not None
        assert job["meta"]["status_message_ids"] == {"1": 42}
        assert calls == [(1, "first", None), (1, "second", 42)]


def test_notify_job_failure_join_failure_updates_status_message(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=1,
            source="zoom_link",
            topic="T",
            meeting_url="https://zoom.us/j/123",
            native_meeting_id="123",
            status="joining",
        )
        mrs.update_job(
            job_id,
            status="failed",
            error_message="vexa:meeting.completed:join_failure",
            meta={"status_message_ids": {"1": 99}},
        )
        with mock.patch(
            "assistant.services.meeting_record_reconcile.update_job_status_messages",
            return_value=1,
        ) as notify:
            assert mrr.notify_job_failure(job_id) is True
        notify.assert_called_once()
        text = notify.call_args[0][1]
        assert "не смог войти" in text.lower()
        job = mrs.get_job(job_id)
        assert job is not None
        assert job["meta"].get("failed_notified") is True


def test_meeting_completed_join_failure_notifies(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=42,
            source="zoom_link",
            topic="T",
            meeting_url="https://zoom.us/j/1",
            native_meeting_id="1",
            status="joining",
        )
        mrs.update_job(job_id, vexa_meeting_id=16)
        job = mrs.get_job(job_id)
        payload = {
            "event_type": "meeting.completed",
            "meeting": {
                "id": 16,
                "data": {"completion_reason": "join_failure"},
            },
        }
        from assistant.integrations import vexa_webhook

        with mock.patch.object(vexa_webhook.mrs, "find_job_by_vexa_meeting_id", return_value=job):
            with mock.patch.object(vexa_webhook, "start_recording_pipeline_if_ready", return_value=False):
                with mock.patch.object(vexa_webhook, "notify_job_failure") as notify:
                    vexa_webhook._handle_meeting_completed(payload)
        notify.assert_called_once_with(job_id)


def test_should_notify_recording_failure_skips_when_newer_done(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        old_id = mrs.create_job(
            telegram_user_id=1,
            source="zoom_link",
            topic="Old",
            meeting_url="https://zoom.us/j/111",
            native_meeting_id="111",
            status="joining",
        )
        mrs.create_job(
            telegram_user_id=1,
            source="zoom_link",
            topic="New",
            meeting_url="https://zoom.us/j/222",
            native_meeting_id="222",
            status="done",
        )
        old_job = mrs.get_job(old_id)
        assert old_job is not None
        from assistant.services.meeting_record_reconcile import should_notify_recording_failure

        assert should_notify_recording_failure(old_job) is False
