import os
import tempfile
import unittest

from assistant.stores import hashtags as hashtags_store
from assistant.stores import notes as notes_store
from assistant.stores import tags as tags_store


class HashtagsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_extract_cyrillic_and_latin(self) -> None:
        names = hashtags_store.extract_hashtag_names(
            "Тема #Работа и #work, повтор #работа, не заголовок:\n# Heading"
        )
        self.assertEqual(names, ["Работа", "work"])

    def test_sync_from_body(self) -> None:
        n = notes_store.create_note(4, "T", "Текст #идея и #пайплайн")
        tags = hashtags_store.sync_item_hashtags(4, "local", n["id"], n["title"], n["body"])
        self.assertEqual([t["name"] for t in tags], ["идея", "пайплайн"])
        listed = hashtags_store.list_hashtags(4)
        self.assertEqual(len(listed), 2)
        hashtags_store.sync_item_hashtags(4, "local", n["id"], "без тегов")
        self.assertEqual(hashtags_store.get_item_hashtags(4, "local", n["id"]), [])

    def test_migrate_extra_tags(self) -> None:
        n = notes_store.create_note(6, "N", "<p>Hello</p>")
        t1 = tags_store.create_tag(6, "Inbox")
        t2 = tags_store.create_tag(6, "urgent")
        with tags_store._LOCK:  # type: ignore[attr-defined]
            conn = tags_store._conn()  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO tag_links (user_id, tag_id, item_kind, item_id, created_at)
                VALUES (?, ?, 'local', ?, '2026-01-01T00:00:00+00:00'),
                       (?, ?, 'local', ?, '2026-01-02T00:00:00+00:00')
                """,
                ("6", t1["id"], str(n["id"]), "6", t2["id"], str(n["id"])),
            )
            conn.commit()
        hashtags_store.migrate_extra_tags_to_hashtags(6)
        projects = tags_store.get_item_tags(6, "local", n["id"])
        self.assertEqual([p["name"] for p in projects], ["Inbox"])
        hashes = hashtags_store.get_item_hashtags(6, "local", n["id"])
        self.assertEqual([h["name"] for h in hashes], ["urgent"])
        fresh = notes_store.get_note(6, n["id"])
        assert fresh is not None
        self.assertIn("#urgent", fresh["body"])
