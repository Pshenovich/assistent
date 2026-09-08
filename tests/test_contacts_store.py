"""Тесты contacts_store (без Telegram)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant.stores import contacts_store


class TestContactsStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.uid = 999001

    def _patch_paths(self) -> None:
        base = Path(self.tmp.name) / "contacts_user"
        base.mkdir(parents=True, exist_ok=True)

        def resolve(*, telegram_user_id, telegram_username=None):
            return base / f"{int(telegram_user_id)}.json"

        self.patch_resolve = mock.patch.object(
            contacts_store, "resolve_contacts_path", side_effect=resolve
        )
        self.patch_resolve.start()
        self.addCleanup(self.patch_resolve.stop)
        contacts_store._CONTACTS_CACHE_BY_PATH.clear()

    def test_create_update_delete(self) -> None:
        self._patch_paths()
        item, st = contacts_store.create_contact_for_user(
            telegram_user_id=self.uid,
            telegram_username=None,
            name="Иван",
            email="ivan@test.com",
            telegram_username_contact="ivan_tg",
        )
        self.assertEqual(st, "added")
        assert item is not None
        self.assertEqual(item["email"], "ivan@test.com")

        row = contacts_store.update_contact_for_user(
            telegram_user_id=self.uid,
            telegram_username=None,
            old_email="ivan@test.com",
            name="Иван Петров",
        )
        assert row is not None
        self.assertEqual(row["name"], "Иван Петров")

        ok = contacts_store.delete_contact_for_user(
            telegram_user_id=self.uid,
            telegram_username=None,
            email="ivan@test.com",
        )
        self.assertTrue(ok)
        items = contacts_store.load_contacts(telegram_user_id=self.uid)
        self.assertEqual(items, [])

    def test_pshenovich_migrates_root_contacts_json(self) -> None:
        root = Path(self.tmp.name)
        root.mkdir(parents=True, exist_ok=True)
        legacy = root / "contacts.json"
        legacy.write_text(
            json.dumps(
                [
                    {
                        "name": "Артём",
                        "email": "artem@test.com",
                        "aliases": [],
                        "telegram_username": "artyawn",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        uid = contacts_store.LEGACY_CONTACTS_OWNER_TELEGRAM_ID
        user_dir = root / "contacts_user"
        user_dir.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(contacts_store, "ROOT", root):
            with mock.patch.object(
                contacts_store, "contacts_user_primary_dir", return_value=user_dir
            ):
                contacts_store._CONTACTS_CACHE_BY_PATH.clear()
                items = contacts_store.load_contacts(
                    telegram_user_id=uid,
                    telegram_username="pshenovich",
                )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["email"], "artem@test.com")
        migrated = user_dir / f"{uid}.json"
        self.assertTrue(migrated.is_file())

    def test_migrates_when_primary_empty_array(self) -> None:
        root = Path(self.tmp.name)
        user_dir = root / "contacts_user"
        user_dir.mkdir(parents=True, exist_ok=True)
        legacy_dir = root / "assistant" / "stores" / "contacts_user"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_dir.joinpath(f"{self.uid}.json").write_text(
            json.dumps(
                [{"name": "Мария", "email": "maria@test.com", "aliases": []}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        user_dir.joinpath(f"{self.uid}.json").write_text("[]\n", encoding="utf-8")

        with mock.patch.object(contacts_store, "ROOT", root):
            with mock.patch.object(
                contacts_store, "contacts_user_primary_dir", return_value=user_dir
            ):
                with mock.patch.object(
                    contacts_store,
                    "_legacy_contacts_user_dir",
                    return_value=legacy_dir,
                ):
                    contacts_store._CONTACTS_CACHE_BY_PATH.clear()
                    items = contacts_store.load_contacts(telegram_user_id=self.uid)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["email"], "maria@test.com")

    def test_migrates_when_primary_has_invalid_rows(self) -> None:
        root = Path(self.tmp.name)
        uid = contacts_store.LEGACY_CONTACTS_OWNER_TELEGRAM_ID
        user_dir = root / "contacts_user"
        user_dir.mkdir(parents=True, exist_ok=True)
        user_dir.joinpath(f"{uid}.json").write_text(
            json.dumps([{"name": "Без почты", "aliases": []}]),
            encoding="utf-8",
        )
        (root / "contacts.json").write_text(
            json.dumps(
                [{"name": "Гаря", "email": "garya@test.com", "aliases": []}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with mock.patch.object(contacts_store, "ROOT", root):
            with mock.patch.object(
                contacts_store, "contacts_user_primary_dir", return_value=user_dir
            ):
                contacts_store._CONTACTS_CACHE_BY_PATH.clear()
                items = contacts_store.load_contacts(
                    telegram_user_id=uid, telegram_username="pshenovich"
                )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["email"], "garya@test.com")

    def test_reads_dict_wrapped_contacts(self) -> None:
        root = Path(self.tmp.name)
        user_dir = root / "contacts_user"
        user_dir.mkdir(parents=True, exist_ok=True)
        user_dir.joinpath(f"{self.uid}.json").write_text(
            json.dumps(
                {
                    "contacts": [
                        {"name": "Ира", "email": "ira@test.com", "aliases": []}
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with mock.patch.object(
            contacts_store, "resolve_contacts_path",
            side_effect=lambda **kw: user_dir / f"{int(kw['telegram_user_id'])}.json",
        ):
            contacts_store._CONTACTS_CACHE_BY_PATH.clear()
            items = contacts_store.load_contacts(telegram_user_id=self.uid)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["email"], "ira@test.com")

    def test_migrates_from_assistant_backup_dir(self) -> None:
        root = Path(self.tmp.name)
        user_dir = root / "contacts_user"
        user_dir.mkdir(parents=True, exist_ok=True)
        backup = root / "assistant.backup-test"
        backup_contacts = backup / "contacts_user"
        backup_contacts.mkdir(parents=True, exist_ok=True)
        backup_contacts.joinpath(f"{self.uid}.json").write_text(
            json.dumps(
                [{"name": "Олег", "email": "oleg@test.com", "aliases": []}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with mock.patch.object(contacts_store, "ROOT", root):
            with mock.patch.object(
                contacts_store, "contacts_user_primary_dir", return_value=user_dir
            ):
                with mock.patch.object(
                    contacts_store,
                    "_backup_assistant_roots",
                    return_value=[backup],
                ):
                    contacts_store._CONTACTS_CACHE_BY_PATH.clear()
                    restored = contacts_store.restore_all_contacts_from_legacy()
                    self.assertIn(self.uid, restored)
                    items = contacts_store.load_contacts(telegram_user_id=self.uid)
                    self.assertEqual(len(items), 1)
                    self.assertEqual(items[0]["email"], "oleg@test.com")


if __name__ == "__main__":
    unittest.main()
