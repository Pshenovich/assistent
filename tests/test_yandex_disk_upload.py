from unittest import mock

from assistant.services import yandex_disk_upload


def test_try_upload_skips_without_connection() -> None:
    with mock.patch(
        "assistant.services.yandex_disk_upload.yandex_disk_oauth.has_connection",
        return_value=False,
    ):
        out = yandex_disk_upload.try_upload_meeting_for_user(
            user_id=1,
            media_bytes=b"x",
            media_filename="meeting.wav",
            topic="T",
            transcript_event_id=10,
            summary_event_id=11,
        )
    assert out == {"ok": False, "skipped": True}


def test_try_upload_calls_api_when_connected() -> None:
    with mock.patch(
        "assistant.services.yandex_disk_upload.yandex_disk_oauth.has_connection",
        return_value=True,
    ), mock.patch(
        "assistant.services.yandex_disk_upload.upload_meeting_for_user",
        return_value={"folder": "disk:/Leo/Записи встреч/x"},
    ) as upload_mock:
        out = yandex_disk_upload.try_upload_meeting_for_user(
            user_id=2,
            media_bytes=b"x",
            media_filename="meeting.wav",
            topic="T",
            transcript_event_id=10,
            summary_event_id=11,
        )
    upload_mock.assert_called_once()
    assert out["ok"] is True
    assert out["paths"]["folder"].startswith("disk:/Leo")


def test_try_upload_returns_error_without_raising() -> None:
    with mock.patch(
        "assistant.services.yandex_disk_upload.yandex_disk_oauth.has_connection",
        return_value=True,
    ), mock.patch(
        "assistant.services.yandex_disk_upload.upload_meeting_for_user",
        side_effect=RuntimeError("disk full"),
    ):
        out = yandex_disk_upload.try_upload_meeting_for_user(
            user_id=3,
            media_bytes=b"x",
            media_filename="meeting.wav",
            topic="T",
            transcript_event_id=10,
            summary_event_id=11,
        )
    assert out["ok"] is False
    assert "disk full" in out["error"]
