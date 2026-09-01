import unittest
from datetime import datetime, timezone
from unittest import mock

from assistant.integrations import yandex_disk_api


class YandexDiskApiTests(unittest.TestCase):
    def test_sanitize_folder_name(self) -> None:
        dt = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
        name = yandex_disk_api.sanitize_folder_name('Встреча "Q1"', date=dt)
        self.assertEqual(name, "2026-06-19 — Встреча Q1")

    def test_upload_bytes_puts_to_href(self) -> None:
        upload_url_resp = mock.Mock()
        upload_url_resp.ok = True
        upload_url_resp.json.return_value = {"href": "https://upload.example/put"}
        put_resp = mock.Mock()
        put_resp.ok = True

        with mock.patch(
            "assistant.integrations.yandex_disk_api.requests.get",
            return_value=upload_url_resp,
        ) as get_mock, mock.patch(
            "assistant.integrations.yandex_disk_api.requests.put",
            return_value=put_resp,
        ) as put_mock:
            path = yandex_disk_api.upload_bytes(
                "token",
                "disk:/Leo/Записи встреч/test/meeting.wav",
                b"abc",
                content_type="audio/wav",
            )
        self.assertEqual(path, "disk:/Leo/Записи встреч/test/meeting.wav")
        get_mock.assert_called_once()
        put_mock.assert_called_once_with(
            "https://upload.example/put",
            data=b"abc",
            headers={"Content-Type": "audio/wav"},
            timeout=yandex_disk_api._UPLOAD_TIMEOUT_SEC,
        )

    def test_disk_path_segments(self) -> None:
        segs = yandex_disk_api._disk_path_segments(
            "disk:/Leo/Записи встреч/2026-06-19 — Встреча Zoom"
        )
        self.assertEqual(
            segs,
            [
                "disk:/Leo",
                "disk:/Leo/Записи встреч",
                "disk:/Leo/Записи встреч/2026-06-19 — Встреча Zoom",
            ],
        )

    def test_ensure_folder_hierarchy(self) -> None:
        with mock.patch.object(yandex_disk_api, "ensure_folder") as ensure_mock:
            yandex_disk_api.ensure_folder_hierarchy(
                "token", "disk:/Leo/Записи встреч/meeting-1"
            )
        self.assertEqual(ensure_mock.call_count, 3)
        ensure_mock.assert_any_call("token", "disk:/Leo")
        ensure_mock.assert_any_call("token", "disk:/Leo/Записи встреч")
        ensure_mock.assert_any_call("token", "disk:/Leo/Записи встреч/meeting-1")

    def test_upload_meeting_artifacts_creates_folder_and_files(self) -> None:
        with mock.patch.object(
            yandex_disk_api, "ensure_folder_hierarchy"
        ) as ensure_mock, mock.patch.object(
            yandex_disk_api,
            "publish_folder_public_url",
            return_value="https://disk.yandex.ru/d/abc123",
        ), mock.patch.object(
            yandex_disk_api, "upload_bytes", side_effect=lambda _t, p, _d, **_: p
        ) as upload_mock, mock.patch.object(
            yandex_disk_api, "sanitize_folder_name", return_value="2026-06-19 — Demo"
        ):
            paths = yandex_disk_api.upload_meeting_artifacts(
                "token",
                media_bytes=b"media",
                media_filename="meeting.wav",
                topic="Demo",
                transcript_pdf=(b"tr", "transkript-demo.pdf"),
                summary_pdf=(b"sm", "samari-demo.pdf"),
            )
        ensure_mock.assert_called_once()
        self.assertEqual(upload_mock.call_count, 3)
        self.assertEqual(paths.get("folder_public_url"), "https://disk.yandex.ru/d/abc123")

    def test_publish_folder_public_url(self) -> None:
        pub_resp = mock.Mock()
        pub_resp.status_code = 200
        pub_resp.json.return_value = {
            "href": "https://cloud-api.yandex.net/v1/disk/resources?path=disk%3A%2Ffoo",
            "method": "GET",
        }
        meta_resp = mock.Mock()
        meta_resp.ok = True
        meta_resp.json.return_value = {
            "public_url": "https://disk.yandex.ru/d/DVUJdOWKg9TMWw"
        }
        with mock.patch(
            "assistant.integrations.yandex_disk_api.requests.put",
            return_value=pub_resp,
        ), mock.patch(
            "assistant.integrations.yandex_disk_api.requests.get",
            return_value=meta_resp,
        ) as get_mock, mock.patch(
            "assistant.integrations.yandex_disk_api.time.sleep"
        ):
            url = yandex_disk_api.publish_folder_public_url(
                "token", "disk:/Leo/Записи встреч/meeting"
            )
        self.assertEqual(url, "https://disk.yandex.ru/d/DVUJdOWKg9TMWw")
        get_mock.assert_called_once()
        self.assertIn("fields", get_mock.call_args.kwargs.get("params", {}))

    def test_publish_folder_public_url_from_public_key(self) -> None:
        pub_resp = mock.Mock()
        pub_resp.status_code = 200
        pub_resp.json.return_value = {}
        meta_resp = mock.Mock()
        meta_resp.ok = True
        meta_resp.json.return_value = {"public_key": "DVUJdOWKg9TMWw"}
        with mock.patch(
            "assistant.integrations.yandex_disk_api.requests.put",
            return_value=pub_resp,
        ), mock.patch(
            "assistant.integrations.yandex_disk_api.requests.get",
            return_value=meta_resp,
        ), mock.patch("assistant.integrations.yandex_disk_api.time.sleep"):
            url = yandex_disk_api.publish_folder_public_url("token", "disk:/foo")
        self.assertEqual(url, "https://disk.yandex.ru/d/DVUJdOWKg9TMWw")


if __name__ == "__main__":
    unittest.main()
