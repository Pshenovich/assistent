"""Тесты хранилища meeting recordings."""

import tempfile
from datetime import datetime, timezone

import assistant.stores.meeting_recordings_store as mrs


def test_create_and_list_job(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=42,
            source="zoom_link",
            topic="Standup",
            meeting_url="https://zoom.us/j/123",
            native_meeting_id="123",
            passcode="pwd",
            start_at_utc=datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc),
        )
        job = mrs.get_job(job_id)
        assert job is not None
        assert job["telegram_user_id"] == 42
        assert job["status"] == "scheduled"
        items = mrs.list_jobs_for_user(42)
        assert len(items) == 1
        assert items[0]["topic"] == "Standup"


def test_list_due_scheduled(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        mrs.create_job(
            telegram_user_id=1,
            source="zoom_create",
            topic="Future",
            meeting_url="https://zoom.us/j/1",
            native_meeting_id="1",
            start_at_utc=datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        mrs.create_job(
            telegram_user_id=1,
            source="zoom_create",
            topic="Due",
            meeting_url="https://zoom.us/j/2",
            native_meeting_id="2",
            start_at_utc=datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        due = mrs.list_due_scheduled(
            before_utc=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        assert len(due) == 1
        assert due[0]["topic"] == "Due"


def test_create_or_join_dedupes_active_meeting(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_a, joined_a = mrs.create_or_join_job(
            telegram_user_id=100,
            source="zoom_link",
            topic="Sync",
            meeting_url="https://zoom.us/j/999",
            native_meeting_id="999",
        )
        assert joined_a is False
        mrs.update_job(job_a, status="recording")

        job_b, joined_b = mrs.create_or_join_job(
            telegram_user_id=200,
            source="zoom_link",
            topic="Sync",
            meeting_url="https://zoom.us/j/999",
            native_meeting_id="999",
        )
        assert joined_b is True
        assert job_b == job_a
        assert mrs.list_recipient_user_ids(job_a) == [100, 200]


def test_add_subscriber_idempotent(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("MEETING_RECORDINGS_DB_PATH", f"{td}/mr.sqlite")
        job_id = mrs.create_job(
            telegram_user_id=1,
            source="zoom_link",
            topic="T",
            meeting_url="https://zoom.us/j/1",
            native_meeting_id="1",
        )
        assert mrs.add_subscriber(job_id, 2) is True
        assert mrs.add_subscriber(job_id, 2) is False
        assert mrs.list_recipient_user_ids(job_id) == [1, 2]

