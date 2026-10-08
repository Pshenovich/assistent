import os
import tempfile
import unittest

from assistant.stores import comment_files
from assistant.stores import notes as notes_store
from assistant.stores import share_comments


class CommentFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_save_link_and_preview(self) -> None:
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
            b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        img = comment_files.save_bytes(
            owner_user_id=3,
            item_kind="local",
            item_id=9,
            author_user_id=3,
            filename="shot.png",
            mime="image/png",
            data=png,
        )
        self.assertEqual(img["kind"], "image")
        self.assertIsNone(img["comment_id"])
        txt = comment_files.save_bytes(
            owner_user_id=3,
            item_kind="local",
            item_id=9,
            author_user_id=3,
            filename="brief.txt",
            mime="text/plain",
            data="hello from file".encode("utf-8"),
        )
        comment = share_comments.add_comment(
            3,
            "local",
            9,
            author_user_id=3,
            author_name="Анна",
            body="📎",
        )
        linked = comment_files.link_to_comment(
            [img["id"], txt["id"]],
            comment_id=int(comment["id"]),
            owner_user_id=3,
            item_kind="local",
            item_id=9,
            author_user_id=3,
        )
        self.assertEqual(len(linked), 2)
        listed = comment_files.list_for_comment(int(comment["id"]))
        self.assertEqual([row["filename"] for row in listed], ["shot.png", "brief.txt"])
        self.assertEqual(comment_files.extract_text_preview(txt), "hello from file")
        self.assertTrue(comment_files.is_pdf({"filename": "a.pdf", "mime": "application/pdf"}))
        self.assertFalse(comment_files.is_pdf({"filename": "a.txt", "mime": "text/plain"}))
        share_comments.delete_comment(int(comment["id"]), requester_user_id=3)
        self.assertEqual(comment_files.list_for_comment(int(comment["id"])), [])
        self.assertFalse(comment_files.disk_path(int(img["id"])).exists())

    def test_parse_generated_file_blocks(self) -> None:
        raw = 'Сначала текст\n\n:::file name="notes.txt"\nline one\nline two\n:::\n\nхвост'
        cleaned, files = comment_files.parse_generated_file_blocks(raw)
        self.assertEqual(files, [("notes.txt", "line one\nline two")])
        self.assertIn("Сначала текст", cleaned)
        self.assertIn("хвост", cleaned)
        self.assertNotIn(":::file", cleaned)

    def test_parse_file_block_single_quotes_and_fence(self) -> None:
        raw = "текст\n\n:::file name='a.csv'\ncol,val\n1,2\n:::\n"
        cleaned, files = comment_files.parse_generated_file_blocks(raw)
        self.assertEqual(files, [("a.csv", "col,val\n1,2")])
        self.assertEqual(cleaned, "текст")
        fenced = "до\n```file:notes.md\n# hi\n```\nпосле"
        cleaned2, files2 = comment_files.parse_generated_file_blocks(fenced)
        self.assertEqual(files2, [("notes.md", "# hi")])
        self.assertIn("до", cleaned2)
        self.assertIn("после", cleaned2)

    def test_parse_image_prompts_and_intent(self) -> None:
        raw = 'Вот картинка\n\n:::image prompt="red balloon"\n:::\n'
        cleaned, prompts = comment_files.parse_generated_image_prompts(raw)
        self.assertEqual(prompts, ["red balloon"])
        self.assertEqual(cleaned, "Вот картинка")
        body = ":::image\nкот в шляпе\n:::"
        _, prompts2 = comment_files.parse_generated_image_prompts(body)
        self.assertEqual(prompts2, ["кот в шляпе"])
        self.assertTrue(comment_files.wants_generated_image("нарисуй кота"))
        self.assertTrue(comment_files.wants_generated_image("пришли фото офиса"))
        self.assertTrue(comment_files.wants_generated_image("апскейл ×3 и DPI ×3"))
        self.assertTrue(comment_files.wants_generated_image("увеличь это фото в 3 раза"))
        self.assertTrue(comment_files.wants_generated_image("увеличить dpi этого png"))
        self.assertFalse(comment_files.wants_generated_image("что на фото в заметке?"))
        # False positive from KPI note (@vinse_u): «увеличивается» ≠ request for image.
        self.assertFalse(
            comment_files.wants_generated_image(
                "ценность анализа увеличивается, если есть точные скрипты звонков"
            )
        )
        self.assertFalse(
            comment_files.wants_image_delivery(
                "ценность анализа увеличивается, если есть точные скрипты звонков"
            )
        )
        self.assertFalse(
            comment_files.wants_generated_image("нужно обсудить масштаб проекта на октябрь")
        )
        self.assertTrue(comment_files.wants_resend_attachment("еще раз отправь файл"))
        self.assertTrue(comment_files.claims_attachment_ready("Готово — файл во вложении."))
        self.assertTrue(
            comment_files.looks_like_attachment_refusal(
                "Похоже, вложения всё ещё не проходят. Могу выслать PNG в виде base64"
            )
        )
        self.assertTrue(comment_files.is_bare_confirm("да"))
        self.assertEqual(
            comment_files.suggested_image_filename("сохрани как IMG_5158_3x.png"),
            "IMG_5158_3x.png",
        )
        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
            "IQAAAABJRU5ErkJggg=="
        )
        cleaned_b64, embedded = comment_files.extract_embedded_images(
            "вот\n" + png_b64 + "\nхвост"
        )
        self.assertEqual(len(embedded), 1)
        self.assertEqual(embedded[0]["mime"], "image/png")
        self.assertNotIn("iVBORw0KGgo", cleaned_b64)
        decoded = comment_files.decode_generated_file_bytes("shot.png", png_b64)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded[1], "image/png")
