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
