from unittest.mock import patch

import assistant.bot.access_gate as gate
import assistant.lib.telegram_access_allowlist as access


def test_unknown_user_not_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_GATE_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", "106278723")
    monkeypatch.setenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", str(tmp_path / "allowed.json"))
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "req.json"))
    assert not gate.is_user_allowed(999, "stranger")


def test_miniapp_access_registers_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_GATE_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", "106278723")
    monkeypatch.setenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", str(tmp_path / "allowed.json"))
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "req.json"))
    with patch.object(gate, "_notify_approvers_sync") as notify:
        allowed, msg = gate.miniapp_access_message(
            user_id=555,
            username="new_user",
            first_name="New",
            last_name="User",
        )
    assert not allowed
    assert "заявка" in msg.lower()
    assert access.is_pending(555)
    notify.assert_called_once()
