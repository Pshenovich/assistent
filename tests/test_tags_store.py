import os
import tempfile
import unittest

from assistant.stores import notes as notes_store
from assistant.stores import tags as tags_store


class TagsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_tag_crud(self) -> None:
        t = tags_store.create_tag(42, "Работа")
        self.assertEqual(t["name"], "Работа")
        listed = tags_store.list_tags(42)
        self.assertEqual(len(listed), 1)
        updated = tags_store.update_tag(42, t["id"], name="Личное")
        assert updated is not None
        self.assertEqual(updated["name"], "Личное")
        self.assertTrue(tags_store.delete_tag(42, t["id"]))
        self.assertEqual(tags_store.list_tags(42), [])

    def test_tag_name_unique_case_insensitive(self) -> None:
        tags_store.create_tag(1, "Важное")
        with self.assertRaises(ValueError):
            tags_store.create_tag(1, "важное")

    def test_assign_and_batch_load(self) -> None:
        n = notes_store.create_note(5, "Note", "Body")
        t1 = tags_store.create_tag(5, "A")
        t2 = tags_store.create_tag(5, "B")
        assigned = tags_store.set_item_tags(5, "local", n["id"], [t1["id"], t2["id"]])
        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["id"], t1["id"])
        tags_store.set_item_tags(5, "local", n["id"], [t2["id"]])
        single = tags_store.get_item_tags(5, "local", n["id"])
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0]["id"], t2["id"])
        batch = tags_store.tags_by_items(5, [("local", str(n["id"])), ("journal", "99")])
        self.assertEqual(len(batch[("local", str(n["id"]))]), 1)
        self.assertEqual(batch[("journal", "99")], [])

    def test_cascade_delete_tag(self) -> None:
        n = notes_store.create_note(3, "T", "B")
        t = tags_store.create_tag(3, "X")
        tags_store.set_item_tags(3, "local", n["id"], [t["id"]])
        tags_store.delete_tag(3, t["id"])
        self.assertEqual(tags_store.get_item_tags(3, "local", n["id"]), [])

    def test_user_isolation(self) -> None:
        t = tags_store.create_tag(10, "Mine")
        self.assertEqual(tags_store.list_tags(11), [])
        self.assertIsNone(tags_store.update_tag(11, t["id"], name="Other"))
        self.assertFalse(tags_store.delete_tag(11, t["id"]))

    def test_filter_and_mode(self) -> None:
        t1 = tags_store.create_tag(8, "one")
        t2 = tags_store.create_tag(8, "two")
        tags_store.set_item_tags(8, "journal", "1", [t1["id"]])
        tags_store.set_item_tags(8, "journal", "2", [t2["id"]])
        one_hits = tags_store.filter_items_by_tags(
            8, "journal", ["1", "2"], [t1["id"]], mode="and"
        )
        self.assertEqual(one_hits, ["1"])
        or_hits = tags_store.filter_items_by_tags(
            8, "journal", ["1", "2"], [t1["id"], t2["id"]], mode="or"
        )
        self.assertEqual(set(or_hits), {"1", "2"})

    def test_take_extra_tag_links(self) -> None:
        n = notes_store.create_note(9, "N", "Body")
        t1 = tags_store.create_tag(9, "Keep")
        t2 = tags_store.create_tag(9, "Extra")
        with tags_store._LOCK:  # type: ignore[attr-defined]
            conn = tags_store._conn()  # type: ignore[attr-defined]
            ts = "2026-01-01T00:00:00+00:00"
            conn.execute(
                """
                INSERT INTO tag_links (user_id, tag_id, item_kind, item_id, created_at)
                VALUES (?, ?, 'local', ?, ?), (?, ?, 'local', ?, ?)
                """,
                (
                    "9",
                    t1["id"],
                    str(n["id"]),
                    ts,
                    "9",
                    t2["id"],
                    str(n["id"]),
                    "2026-01-02T00:00:00+00:00",
                ),
            )
            conn.commit()
        extras = tags_store.take_extra_tag_links(9)
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0]["name"], "Extra")
        left = tags_store.get_item_tags(9, "local", n["id"])
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["name"], "Keep")


if __name__ == "__main__":
    unittest.main()
