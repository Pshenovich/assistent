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


def test_leo_approval_does_not_grant_donatello(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", str(tmp_path / "leo.json"))
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "leo_req.json"))
    monkeypatch.setenv(
        "TELEGRAM_ACCESS_DONATELLO_ALLOWLIST_PATH", str(tmp_path / "don.json")
    )
    monkeypatch.setenv(
        "TELEGRAM_ACCESS_DONATELLO_REQUESTS_PATH", str(tmp_path / "don_req.json")
    )
    access.invalidate_allowlist_cache()
    access.add_approved_user(username="alice", user_id=42, scope="leo")
    assert access.is_extra_allowed("alice", 42, scope="leo")
    assert not access.is_extra_allowed("alice", 42, scope="donatello")


def test_donatello_approval_does_not_grant_leo(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", str(tmp_path / "leo.json"))
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "leo_req.json"))
    monkeypatch.setenv(
        "TELEGRAM_ACCESS_DONATELLO_ALLOWLIST_PATH", str(tmp_path / "don.json")
    )
    monkeypatch.setenv(
        "TELEGRAM_ACCESS_DONATELLO_REQUESTS_PATH", str(tmp_path / "don_req.json")
    )
    access.invalidate_allowlist_cache()
    access.add_approved_user(username="bob", user_id=77, scope="donatello")
    assert access.is_extra_allowed("bob", 77, scope="donatello")
    assert not access.is_extra_allowed("bob", 77, scope="leo")
