import tempfile
from pathlib import Path

import assistant.stores.user_prefs as up


def test_timezone_roundtrip(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        up.set_timezone(42, "Asia/Almaty")
        assert up.get_timezone_name(42) == "Asia/Almaty"
        assert str(up.get_user_tz(42)) == "Asia/Almaty"


def test_meeting_reminders_enabled_default_on(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        assert up.meeting_reminders_enabled(7) is True


def test_meeting_reminders_enabled_can_be_disabled(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        up.set_meeting_reminders_enabled(7, False)
        assert up.meeting_reminders_enabled(7) is False
        up.set_meeting_reminders_enabled(7, True)
        assert up.meeting_reminders_enabled(7) is True


def test_task_reminders_enabled_default_on(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        assert up.task_reminders_enabled(7) is True


def test_task_reminders_enabled_can_be_disabled(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        up.set_task_reminders_enabled(7, False)
        assert up.task_reminders_enabled(7) is False
        up.set_task_reminders_enabled(7, True)
        assert up.task_reminders_enabled(7) is True


def test_user_prefs_zoom_auto_record_default_off(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        assert up.zoom_auto_record_enabled(9) is False
        up.set_zoom_auto_record_enabled(9, True)
        assert up.zoom_auto_record_enabled(9) is True


def test_user_prefs_telemost_auto_record_default_off(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        assert up.telemost_auto_record_enabled(9) is False
        up.set_telemost_auto_record_enabled(9, True)
        assert up.telemost_auto_record_enabled(9) is True


def test_onboarding_completed_default_off(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        assert up.onboarding_completed(3) is False
        up.mark_onboarding_completed(3)
        assert up.onboarding_completed(3) is True


def test_calendar_entry_colors_roundtrip(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        assert up.get_calendar_entry_colors(5) == {}
        colors = up.set_calendar_entry_color(5, "event:primary:abc", "#039BE5")
        assert colors["event:primary:abc"] == "#039be5"
        assert (
            up.calendar_entry_color_key({"id": "abc", "calendar_id": "primary"})
            == "event:primary:abc"
        )
        assert up.calendar_entry_color_key({"id": "task-9", "task_id": 9}) == "task:9"
        colors = up.set_calendar_entry_color(5, "event:primary:abc", "")
        assert "event:primary:abc" not in colors


def test_calendar_entry_color_rejects_bad_hex(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("USER_PREFS_DIR", td)
        try:
            up.set_calendar_entry_color(1, "event:primary:x", "blue")
            assert False, "expected ValueError"
        except ValueError:
            pass
