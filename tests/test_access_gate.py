import os

import assistant.lib.telegram_access_allowlist as access


def test_approver_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", "106278723")
    monkeypatch.delenv("MINIAPP_DEV_TELEGRAM_USER_ID", raising=False)
    assert 106278723 in access.approver_user_ids()


def test_add_and_allow_user(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", str(tmp_path / "allowed.json"))
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "req.json"))
    access.add_approved_user(username="testuser", user_id=42)
    assert access.is_extra_allowed("testuser", 42)
    assert not access.is_pending(42)


def test_deny_user(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "req.json"))
    access.deny_user(99)
    assert access.is_denied(99)
