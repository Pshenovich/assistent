import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from assistant.services import chat_url_transcribe as cut

GDRIVE = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing"


class DetectTests(unittest.TestCase):
    def test_gdrive_link_and_transcribe(self) -> None:
        req = cut.detect_url_transcribe_request(f"{GDRIVE} транскрибируй")
        self.assertEqual(req, cut.UrlTranscribeRequest(url=GDRIVE, with_summary=False))

    def test_transcribe_word_forms(self) -> None:
        for text in (
            f"транскрибировать {GDRIVE}",
            f"Сделай транскрипцию: {GDRIVE}",
            f"расшифруй запись {GDRIVE}.",
        ):
            req = cut.detect_url_transcribe_request(text)
            self.assertIsNotNone(req, text)
            self.assertEqual(req.url, GDRIVE)

    def test_summary_on_file_link(self) -> None:
        req = cut.detect_url_transcribe_request(f"сделай саммари {GDRIVE}")
        self.assertEqual(req, cut.UrlTranscribeRequest(url=GDRIVE, with_summary=True))

    def test_summary_on_web_page_goes_to_model(self) -> None:
        self.assertIsNone(
            cut.detect_url_transcribe_request("сделай саммари статьи https://habr.com/ru/articles/1/")
        )

    def test_summary_on_direct_media_link(self) -> None:
        req = cut.detect_url_transcribe_request("саммари https://example.com/call.mp4")
        self.assertEqual(req.url, "https://example.com/call.mp4")
        self.assertTrue(req.with_summary)

    def test_no_intent_or_no_link(self) -> None:
        self.assertIsNone(cut.detect_url_transcribe_request(f"посмотри {GDRIVE}"))
        self.assertIsNone(cut.detect_url_transcribe_request("транскрибируй вчерашнюю встречу"))
        self.assertIsNone(cut.detect_url_transcribe_request(""))

    def test_prefers_file_link(self) -> None:
        req = cut.detect_url_transcribe_request(
            f"транскрибируй https://example.com/page и {GDRIVE}"
        )
        self.assertEqual(req.url, GDRIVE)

    def test_url_in_text_does_not_trigger_by_itself(self) -> None:
        # «transcribe» только в самой ссылке — не просьба.
        self.assertIsNone(
            cut.detect_url_transcribe_request("глянь https://example.com/transcribe/audio.mp3")
        )

    def test_accepted_text(self) -> None:
        txt = cut.accepted_text(cut.UrlTranscribeRequest(url=GDRIVE, with_summary=True))
        self.assertIn("Скачиваю и транскрибирую и сделаю саммари", txt)
        self.assertIn("«Транскрипции»", txt)


class DoneTextTests(unittest.TestCase):
    @patch("assistant.stores.note_members.note_webapp_url", return_value="https://x/webapp/?b=1&note=7")
    def test_link_to_note(self, _url: MagicMock) -> None:
        txt = cut.done_text({"id": 7, "title": "Встреча [1] (итог)"}, with_summary=False)
        self.assertIn("[Встреча 1 итог](https://x/webapp/?b=1&note=7)", txt)


class RunJobTests(unittest.TestCase):
    def _patches(self):
        return (
            patch("assistant.lib.media_download.download_url_bytes", return_value=(b"vid", "Созвон.mp4")),
            patch(
                "assistant.integrations.transcribe.transcribe_bytes",
                return_value=SimpleNamespace(job_id="j1"),
            ),
            patch(
                "assistant.stores.voice_prefs.process_transcript_result",
                return_value=SimpleNamespace(formatted_text="Привет", plain_text="", job_id="j1"),
            ),
            patch("assistant.lib.usage_store.insert_usage_event"),
            patch(
                "assistant.services.transcription_notes.create_transcript_only",
                return_value={"id": 42, "title": "Созвон"},
            ),
            patch.object(cut, "post_chat_reply"),
            patch("assistant.stores.note_members.note_webapp_url", return_value="https://x/webapp/?note=42"),
        )

    def test_success_creates_note_and_posts_link(self) -> None:
        p = self._patches()
        with p[0] as dl, p[1] as stt, p[2], p[3], p[4] as create, p[5] as post, p[6]:
            cut.run_chat_url_transcription(
                5, "th1", cut.UrlTranscribeRequest(url=GDRIVE, with_summary=False), min_delay_sec=0
            )
        dl.assert_called_once_with(GDRIVE)
        stt.assert_called_once_with(b"vid", "Созвон.mp4")
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["title"], "Созвон")
        self.assertEqual(kwargs["meta"]["source"], "miniapp_url")
        self.assertEqual(kwargs["meta"]["url"], GDRIVE)
        args, _ = post.call_args
        self.assertEqual(args[:2], (5, "th1"))
        self.assertIn("[Созвон](https://x/webapp/?note=42)", args[2])

    def test_summary_started_after_reply(self) -> None:
        p = self._patches()
        with p[0], p[1], p[2], p[3], p[4], p[5] as post, p[6], patch(
            "assistant.services.transcription_notes.begin_make_summary",
            return_value={"id": 42, "title": "Созвон"},
        ) as begin, patch(
            "assistant.services.transcription_notes.generate_summary_for_note"
        ) as gen:
            cut.run_chat_url_transcription(
                5, "th1", cut.UrlTranscribeRequest(url=GDRIVE, with_summary=True), min_delay_sec=0
            )
        begin.assert_called_once_with(5, 42)
        gen.assert_called_once_with(5, 42)
        self.assertIn("Саммари ещё пишется", post.call_args.args[2])

    def test_value_error_is_shown_in_chat(self) -> None:
        err = ValueError("Google Диск не отдал файл: откройте доступ «Все, у кого есть ссылка».")
        with patch("assistant.lib.media_download.download_url_bytes", side_effect=err), patch.object(
            cut, "post_chat_reply"
        ) as post:
            cut.run_chat_url_transcription(
                5, "th1", cut.UrlTranscribeRequest(url=GDRIVE, with_summary=True), min_delay_sec=0
            )
        self.assertEqual(post.call_args.args[2], f"Не получилось: {err}")


if __name__ == "__main__":
    unittest.main()
