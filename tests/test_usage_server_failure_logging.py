"""Сбои, которые раньше глушились молча, остаются с прежним ответом, но попадают в лог."""

from unittest.mock import patch

from fastapi.testclient import TestClient

import usage_server


def test_todoist_callback_failure_keeps_response_and_logs(capsys):
    client = TestClient(usage_server.app)
    with patch(
        "assistant.integrations.todoist_oauth.exchange_code_and_save_token",
        side_effect=RuntimeError("token endpoint down"),
    ):
        resp = client.get("/oauth/todoist/callback", params={"code": "c", "state": "s"})
    assert resp.status_code == 500
    assert "token endpoint down" in resp.text
    assert "[oauth] todoist_callback err=RuntimeError('token endpoint down')" in capsys.readouterr().out


def test_calendar_today_load_failure_keeps_payload_and_logs(capsys):
    with (
        patch("assistant.integrations.google_calendar_oauth.user_token_path") as token_path,
        patch("usage_server._miniapp_dev_mode_on", return_value=False),
        patch(
            "assistant.compat.miniapp_shims.calendar_free_slots",
            side_effect=RuntimeError("google 503"),
        ),
        patch("usage_server._attach_leo_tasks", side_effect=lambda payload, *a, **k: payload),
    ):
        token_path.return_value.is_file.return_value = True
        payload = usage_server._calendar_today_payload(9101, "2026-10-07")
    assert payload["connected"] is False
    assert payload["error"] == "google 503"
    assert payload["events"] == []
    assert "[miniapp_calendar] today_load_failed uid=9101 err=RuntimeError('google 503')" in capsys.readouterr().out
