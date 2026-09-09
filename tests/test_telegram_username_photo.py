from __future__ import annotations

from unittest.mock import Mock, patch

from assistant.lib.telegram_notify import get_username_profile_photo_bytes

JPEG = b"\xff\xd8\xff" + b"\x00" * 900


def test_username_photo_from_userpic() -> None:
    resp = Mock()
    resp.ok = True
    resp.content = JPEG
    with patch("assistant.lib.telegram_notify.requests.get", return_value=resp) as get:
        blob = get_username_profile_photo_bytes("Artyawn")
    assert blob == JPEG
    assert "t.me/i/userpic/320/artyawn.jpg" in get.call_args.args[0]


def test_username_photo_skips_telegram_logo() -> None:
    missing = Mock()
    missing.ok = False
    missing.content = b""
    page = Mock()
    page.ok = True
    page.text = '<meta property="og:image" content="https://telegram.org/img/t_logo_2x.png">'
    with patch(
        "assistant.lib.telegram_notify.requests.get", side_effect=[missing, page]
    ):
        assert get_username_profile_photo_bytes("asmalltalk") is None


def test_username_photo_from_og_image() -> None:
    missing = Mock()
    missing.ok = False
    missing.content = b""
    page = Mock()
    page.ok = True
    page.text = (
        '<meta property="og:image" content="https://cdn4.telesco.pe/file/abc.jpg">'
    )
    img = Mock()
    img.ok = True
    img.content = JPEG
    with patch(
        "assistant.lib.telegram_notify.requests.get",
        side_effect=[missing, page, img],
    ):
        assert get_username_profile_photo_bytes("artyawn") == JPEG
