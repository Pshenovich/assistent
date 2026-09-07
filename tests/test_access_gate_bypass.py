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


def test_leo_allowlist_does_not_open_donatello(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ACCESS_GATE_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", "106278723")
    monkeypatch.setenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", str(tmp_path / "leo.json"))
    monkeypatch.setenv("TELEGRAM_ACCESS_REQUESTS_PATH", str(tmp_path / "leo_req.json"))
    monkeypatch.setenv(
        "TELEGRAM_ACCESS_DONATELLO_ALLOWLIST_PATH", str(tmp_path / "don.json")
    )
    monkeypatch.setenv(
        "TELEGRAM_ACCESS_DONATELLO_REQUESTS_PATH", str(tmp_path / "don_req.json")
    )
    access.invalidate_allowlist_cache()
    access.add_approved_user(username="stranger", user_id=999, scope="leo")
    assert gate.is_user_allowed(999, "stranger", scope="leo")
    assert not gate.is_user_allowed(999, "stranger", scope="donatello")


def test_scope_from_donatello_bot_token(monkeypatch):
    monkeypatch.setenv("TG_DONATELLO_BOT_TOKEN", "donatello-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "leo-token")

    class _Bot:
        token = "donatello-token"

    class _Ctx:
        bot = _Bot()

    assert gate.access_scope_from_context(_Ctx()) == "donatello"

    class _LeoCtx:
        bot = type("B", (), {"token": "leo-token"})()

    assert gate.access_scope_from_context(_LeoCtx()) == "leo"
