"""Тесты mint OBF для Vexa meeting bot."""

from unittest import mock

from assistant.integrations import meeting_bot, zoom_oauth
from assistant.lib.zoom_link import ZoomMeetingLink


def test_mint_obf_returns_token(monkeypatch) -> None:
    with mock.patch.object(
        zoom_oauth,
        "get_access_token_for_user",
        return_value="access",
    ), mock.patch("assistant.integrations.zoom_oauth.requests.get") as get:
        get.return_value = mock.Mock(
            ok=True,
            status_code=200,
            json=lambda: {"token": "obf-secret"},
        )
        assert zoom_oauth.mint_obf_token(1, "123456") == "obf-secret"


def test_obf_scope_granted() -> None:
    assert zoom_oauth.obf_scope_granted({"scope": "user:read:user user:read:token"}) is True
    assert zoom_oauth.obf_scope_granted({"scope": "user:read:user meeting:write:meeting"}) is False


def test_create_zoom_bot_passes_obf_when_available(monkeypatch) -> None:
    monkeypatch.setenv("MEETING_BOT_ENABLED", "1")
    monkeypatch.setenv("VEXA_API_KEY", "k")
    monkeypatch.setenv("VEXA_API_BASE", "http://vexa.test")
    link = ZoomMeetingLink(
        url="https://zoom.us/j/123",
        native_meeting_id="123",
        passcode="pwd",
    )
    with mock.patch.object(
        zoom_oauth,
        "mint_obf_token",
        return_value="obf-tok",
    ) as mint, mock.patch("assistant.integrations.meeting_bot.requests.post") as post:
        post.return_value = mock.Mock(ok=True, status_code=201, json=lambda: {"id": 1})
        meeting_bot.create_zoom_bot(link, telegram_user_id=42)
        payload = post.call_args.kwargs["json"]
        assert payload["zoom_obf_token"] == "obf-tok"
        assert "authenticated" not in payload
        mint.assert_called_once()


def test_create_zoom_bot_uses_pre_minted_obf(monkeypatch) -> None:
    monkeypatch.setenv("MEETING_BOT_ENABLED", "1")
    monkeypatch.setenv("VEXA_API_KEY", "k")
    monkeypatch.setenv("VEXA_API_BASE", "http://vexa.test")
    link = ZoomMeetingLink(
        url="https://zoom.us/j/123",
        native_meeting_id="123",
        passcode="pwd",
    )
    with mock.patch.object(
        zoom_oauth,
        "mint_obf_token",
    ) as mint, mock.patch("assistant.integrations.meeting_bot.requests.post") as post:
        post.return_value = mock.Mock(ok=True, status_code=201, json=lambda: {"id": 1})
        meeting_bot.create_zoom_bot(link, telegram_user_id=42, zoom_obf_token="pre-minted")
        payload = post.call_args.kwargs["json"]
        assert payload["zoom_obf_token"] == "pre-minted"
        mint.assert_not_called()

