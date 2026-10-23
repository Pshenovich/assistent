import unittest
from unittest.mock import MagicMock, patch

from assistant.lib import media_download


class MediaDownloadTests(unittest.TestCase):
    def test_is_yandex_disk_public_url(self) -> None:
        self.assertTrue(
            media_download.is_yandex_disk_public_url(
                "https://disk.yandex.ru/i/xXDSjiRh2cNqcA"
            )
        )
        self.assertTrue(
            media_download.is_yandex_disk_public_url("https://yadi.sk/d/abc123")
        )
        self.assertFalse(
            media_download.is_yandex_disk_public_url("https://example.com/file.mp4")
        )

    @patch("assistant.lib.media_download.requests.get")
    def test_resolve_yandex_disk_download_url(self, mock_get: MagicMock) -> None:
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "href": "https://downloader.disk.yandex.ru/disk/x?filename=video.mp4&content_type=video/mp4"
        }
        mock_get.return_value = api_resp

        href, fname = media_download.resolve_yandex_disk_download_url(
            "https://disk.yandex.ru/i/xXDSjiRh2cNqcA"
        )
        self.assertIn("downloader.disk.yandex.ru", href)
        self.assertEqual(fname, "video.mp4")
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], media_download._YANDEX_DISK_API)
        self.assertEqual(
            kwargs["params"]["public_key"],
            "https://disk.yandex.ru/i/xXDSjiRh2cNqcA",
        )

    @patch("assistant.lib.media_download.requests.get")
    def test_download_url_bytes_resolves_yandex_before_stream(self, mock_get: MagicMock) -> None:
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "href": "https://downloader.disk.yandex.ru/disk/x?filename=meet.mp4"
        }

        file_resp = MagicMock()
        file_resp.status_code = 200
        file_resp.headers = {
            "Content-Type": "video/mp4",
            "Content-Disposition": 'attachment; filename="meet.mp4"',
        }
        file_resp.raise_for_status = MagicMock()
        file_resp.iter_content.return_value = [b"abc", b"def"]
        file_resp.__enter__ = MagicMock(return_value=file_resp)
        file_resp.__exit__ = MagicMock(return_value=False)

        mock_get.side_effect = [api_resp, file_resp]

        data, fname = media_download.download_url_bytes(
            "https://disk.yandex.ru/i/xXDSjiRh2cNqcA"
        )
        self.assertEqual(data, b"abcdef")
        self.assertEqual(fname, "meet.mp4")
        self.assertEqual(mock_get.call_count, 2)

    def test_google_drive_links_resolve_to_direct_download(self):
        fid = "190tb2UxgzqOtswgyg_lWbQHL7TUb_xbp"
        direct = (
            "https://drive.usercontent.google.com/download"
            f"?id={fid}&export=download&confirm=t"
        )
        for url in (
            f"https://drive.google.com/file/d/{fid}/view",
            f"https://drive.google.com/file/d/{fid}/view?usp=sharing",
            f"https://drive.google.com/open?id={fid}",
            f"https://drive.google.com/uc?id={fid}&export=download",
        ):
            self.assertEqual(media_download.resolve_download_url(url), (direct, None), url)
        self.assertIsNone(media_download.google_drive_file_id("https://drive.google.com/drive/folders/abc"))

    @patch("assistant.lib.media_download.requests.get")
    def test_google_drive_private_file_explains_access(self, mock_get):
        resp = MagicMock()
        resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        resp.raise_for_status = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_get.return_value = resp
        with self.assertRaises(ValueError) as cm:
            media_download.download_url_bytes(
                "https://drive.google.com/file/d/190tb2UxgzqOtswgyg_lWbQHL7TUb_xbp/view"
            )
        self.assertIn("Все, у кого есть ссылка", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
