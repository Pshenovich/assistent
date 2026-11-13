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

    def test_migrate_extra_tags_noop_with_projects(self) -> None:
        # запись может быть в нескольких проектах — лишних ярлыков больше нет
        self.assertFalse(hashtags_store.migrate_extra_tags_to_hashtags(6))

