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
