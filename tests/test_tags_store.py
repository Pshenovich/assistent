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

    def test_take_extra_tag_links_is_noop(self) -> None:
        self.assertEqual(tags_store.take_extra_tag_links(9), [])

    def test_legacy_tags_move_to_personal_projects(self) -> None:
        from assistant.stores import bundles, projects

        n = notes_store.create_note(9, "N", "Body")
        conn = bundles._conn()  # type: ignore[attr-defined]
        ts = "2026-01-01T00:00:00+00:00"
        conn.execute("CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, "
                     "name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(user_id, name))")
        conn.execute("CREATE TABLE IF NOT EXISTS tag_links (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, "
                     "tag_id INTEGER NOT NULL, item_kind TEXT NOT NULL, item_id TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO tags(id, user_id, name, created_at, updated_at) VALUES (77, '9', 'Старый', ?, ?)", (ts, ts))
        conn.execute("INSERT INTO tag_links(user_id, tag_id, item_kind, item_id, created_at) VALUES ('9', 77, 'local', ?, ?)",
                     (str(n["id"]), ts))
        conn.execute("INSERT INTO tag_links(user_id, tag_id, item_kind, item_id, created_at) VALUES ('9', 77, 'journal', '5', ?)",
                     (ts,))
        conn.commit()
        conn = tags_store._conn()  # type: ignore[attr-defined]  # переезд — при первом открытии
        self.assertEqual(tags_store.migrate_legacy(conn), 0)  # повтор ничего не дублирует
        self.assertEqual([t["name"] for t in tags_store.list_tags(9)], ["Старый"])
        self.assertEqual(tags_store.list_tags(10), [])
        pid = tags_store.list_tags(9)[0]["id"]
        p = projects.get(pid)
        assert p is not None
        self.assertEqual(projects.role_of(9, p), "owner")
        self.assertEqual({(i["item_kind"], i["item_id"]) for i in p["items"]}, {("note", str(n["id"])), ("journal", "5")})
        self.assertEqual([t["id"] for t in tags_store.get_item_tags(9, "local", n["id"])], [pid])
        self.assertEqual(tags_store.project_ids_for_legacy_tags([77, 78]), [pid])

    def test_shared_project_shows_in_member_list(self) -> None:
        from assistant.stores import projects

        t = tags_store.create_tag(20, "Общий")
        projects.set_member(20, t["id"], "u:21", "viewer")
        listed = tags_store.list_tags(21)
        self.assertEqual([x["name"] for x in listed], ["Общий"])
        self.assertTrue(listed[0]["shared"])
        self.assertFalse(tags_store.delete_tag(21, t["id"]))  # не владелец
        with self.assertRaises(ValueError):
            tags_store.update_tag(21, t["id"], name="Моё")  # только смотрит


if __name__ == "__main__":
    unittest.main()
