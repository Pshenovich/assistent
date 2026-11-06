from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from assistant.stores import notes as notes_store
from assistant.stores import org_groups, org_kb, orgs_store
from assistant.stores.orgs_store import OrgError

OWNER, LEAD, BACK, FRONT, MANAGER, GUEST, OUTSIDER = 1, 3, 4, 5, 6, 7, 99


class OrgKbTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmpdir.name, "reg.json")
        os.environ["NOTE_VERSIONS_DB_PATH"] = os.path.join(self._tmpdir.name, "versions.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._patches = [
            mock.patch.object(orgs_store, "verified_emails", lambda uid: [f"u{int(uid)}@obuchat.me"]),
            mock.patch.object(orgs_store, "_username", lambda uid: ""),
            mock.patch.object(orgs_store, "_resolve_key", lambda actor, key: int(key.split("@")[0][1:])),
        ]
        for p in self._patches:
            p.start()
        self.org = orgs_store.create_org(OWNER, "Обучат", domain="obuchat.me")["id"]
        for uid in (LEAD, BACK, FRONT, MANAGER, GUEST):
            orgs_store.add_member(OWNER, self.org, f"u{uid}@obuchat.me")
        guest_row = next(m for m in orgs_store.list_members(self.org) if m["user_id"] == str(GUEST))
        orgs_store.set_guest(OWNER, self.org, guest_row["id"], True)
        self.okk = org_groups.create(OWNER, self.org, "ОКК")["id"]
        self.backend = org_groups.create(OWNER, self.org, "ОКК · бэкенд", parent_id=self.okk)["id"]
        org_groups.add_member(OWNER, self.org, self.backend, BACK)
        org_groups.add_member(OWNER, self.org, self.okk, MANAGER)

        # раздел «Инструкции ОКК»: отделу — читать, бэкенду — править
        self.sec = org_kb.create_section(OWNER, self.org, "Инструкции ОКК")["id"]
        org_kb.set_access(OWNER, self.org, self.sec, f"g:{self.okk}", "reader")
        org_kb.set_access(OWNER, self.org, self.sec, f"g:{self.backend}", "editor")
        self.note = notes_store.create_note(BACK, "Как перезапустить n8n", "<p>шаги</p>")["id"]
        self.doc = org_kb.add_doc(BACK, self.org, self.sec, self.note)["id"]

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_roles_inherit_from_section(self) -> None:
        self.assertEqual(org_kb.role_for_note(BACK, self.note), "owner")  # автор
        self.assertEqual(org_kb.role_for_note(MANAGER, self.note), "reader")
        self.assertEqual(org_kb.role_for_note(OWNER, self.note), "owner")  # владелец организации
        self.assertEqual(org_kb.role_for_note(FRONT, self.note), "")
        self.assertEqual(org_kb.role_for_note(OUTSIDER, self.note), "")

    def test_reader_can_read_but_not_edit(self) -> None:
        self.assertIsNone(notes_store.get_accessible_note(MANAGER, self.note))
        n = notes_store.get_readable_note(MANAGER, self.note)
        self.assertIsNotNone(n)
        self.assertFalse(n["can_edit"])
        self.assertIsNone(notes_store.get_readable_note(FRONT, self.note))

    def test_editor_via_team_can_edit(self) -> None:
        other = notes_store.create_note(OWNER, "Регламент", "")["id"]
        org_kb.add_doc(OWNER, self.org, self.sec, other)
        self.assertIsNotNone(notes_store.get_accessible_note(BACK, other))
        self.assertIsNone(notes_store.get_accessible_note(MANAGER, other))

    def test_new_top_section_closed_and_all_excludes_guest(self) -> None:
        sec = org_kb.create_section(OWNER, self.org, "Общее")["id"]
        n = notes_store.create_note(OWNER, "Как подключиться к VPN", "")["id"]
        org_kb.add_doc(OWNER, self.org, sec, n)
        self.assertEqual(org_kb.role_for_note(BACK, n), "")
        org_kb.set_access(OWNER, self.org, sec, org_groups.ALL, "reader")
        self.assertEqual(org_kb.role_for_note(BACK, n), "reader")
        self.assertEqual(org_kb.role_for_note(GUEST, n), "")

    def test_narrow_doc_copies_then_removes(self) -> None:
        org_kb.set_inherit(BACK, self.org, self.doc, False)
        self.assertEqual(org_kb.role_for_note(MANAGER, self.note), "reader")  # скопировалось
        org_kb.set_access(BACK, self.org, self.doc, f"g:{self.okk}", None)
        self.assertEqual(org_kb.role_for_note(MANAGER, self.note), "")
        org_kb.set_inherit(BACK, self.org, self.doc, True)
        self.assertEqual(org_kb.role_for_note(MANAGER, self.note), "reader")

    def test_add_person_to_doc_only_shows_path(self) -> None:
        org_kb.set_access(BACK, self.org, self.doc, f"u:{FRONT}", "commenter")
        n = notes_store.get_readable_note(FRONT, self.note)
        self.assertTrue(n["can_comment"])
        items = {i["id"]: i for i in org_kb.tree(self.org, FRONT)}
        self.assertTrue(items[self.sec]["restricted"])
        self.assertEqual(items[self.doc]["my_role"], "commenter")
        sibling = notes_store.create_note(OWNER, "Соседний", "")["id"]
        sib = org_kb.add_doc(OWNER, self.org, self.sec, sibling)["id"]
        self.assertNotIn(sib, {i["id"] for i in org_kb.tree(self.org, FRONT)})

    def test_permissions_on_changes(self) -> None:
        with self.assertRaises(OrgError):
            org_kb.create_section(BACK, self.org, "Своё")  # верхний уровень — только руководство
        sub = org_kb.create_section(BACK, self.org, "Сервер", parent_id=self.sec)["id"]  # редактор раздела
        with self.assertRaises(OrgError):
            org_kb.create_section(MANAGER, self.org, "X", parent_id=self.sec)
        with self.assertRaises(OrgError):
            org_kb.set_access(MANAGER, self.org, self.sec, org_groups.ALL, "reader")
        with self.assertRaises(OrgError):
            org_kb.add_doc(MANAGER, self.org, self.sec, notes_store.create_note(MANAGER, "m", "")["id"])
        with self.assertRaises(OrgError):
            org_kb.add_doc(BACK, self.org, sub, notes_store.create_note(OWNER, "чужой", "")["id"])
        with self.assertRaises(OrgError):
            org_kb.remove(OWNER, self.org, self.sec)  # не пустой
        with self.assertRaises(OrgError):
            org_kb.move(OWNER, self.org, self.sec, sub)  # внутрь себя
        with self.assertRaises(OrgError):
            org_kb.add_doc(BACK, self.org, sub, self.note)  # уже в базе

    def test_principal_validation(self) -> None:
        with self.assertRaises(OrgError):
            org_kb.set_access(OWNER, self.org, self.sec, f"u:{OUTSIDER}", "reader")
        with self.assertRaises(OrgError):
            org_kb.set_access(OWNER, self.org, self.sec, "g:9999", "reader")
        with self.assertRaises(OrgError):
            org_kb.set_access(OWNER, self.org, self.sec, "all", "god")

    def test_leaving_org_drops_access(self) -> None:
        row = next(m for m in orgs_store.list_members(self.org) if m["user_id"] == str(MANAGER))
        orgs_store.remove_member(OWNER, self.org, row["id"])
        self.assertIsNone(notes_store.get_readable_note(MANAGER, self.note))

    def test_stale_and_verify(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        with orgs_store._LOCK:
            c = org_kb._conn()
            c.execute("UPDATE org_kb_nodes SET verified_at = ? WHERE id = ?", (old, self.doc))
            c.commit()
        self.assertTrue(next(i for i in org_kb.tree(self.org, BACK) if i["id"] == self.doc)["stale"])
        self.assertEqual([s["note_id"] for s in org_kb.stale_for_owner(BACK)], [self.note])
        with self.assertRaises(OrgError):
            org_kb.verify(MANAGER, self.note)
        org_kb.verify(BACK, self.note)
        self.assertEqual(org_kb.stale_for_owner(BACK), [])
        org_kb.verify(BACK, self.note, no_expiry=True)

    def test_access_view(self) -> None:
        a = org_kb.access(self.org, BACK, self.doc)
        self.assertTrue(a["inherit"])
        self.assertIn({"principal": f"g:{self.okk}", "role": "reader"}, a["inherited"])
        with self.assertRaises(OrgError):
            org_kb.access(self.org, MANAGER, self.doc)


if __name__ == "__main__":
    unittest.main()


class OrgKbVerifyOnEditTests(OrgKbTests):
    def _verified(self) -> str:
        return org_kb.node_for_note(self.note)["verified_at"]

    def test_resave_without_text_change_is_not_a_check(self) -> None:
        old = "2026-01-01T00:00:00+00:00"
        with orgs_store._LOCK:
            c = org_kb._conn()
            c.execute("UPDATE org_kb_nodes SET verified_at = ? WHERE id = ?", (old, self.doc))
            c.commit()
        # редактор пересохранил ту же мысль другой разметкой
        notes_store.update_note(BACK, self.note, body="<p>шаги</p>\n", actor_user_id=BACK)
        self.assertEqual(self._verified(), old)
        notes_store.update_note(BACK, self.note, body="<p>шаги: 1, 2, 3</p>", actor_user_id=BACK)
        self.assertNotEqual(self._verified(), old)


class OrgKbEditRequestTests(OrgKbTests):
    def test_reader_requests_and_author_allows(self) -> None:
        from assistant.stores import note_members

        req = note_members.create_edit_request(owner_user_id=BACK, note_id=self.note, requester_user_id=MANAGER)
        self.assertTrue(req["new"])
        again = note_members.create_edit_request(owner_user_id=BACK, note_id=self.note, requester_user_id=MANAGER)
        self.assertFalse(again["new"])  # повторный запрос не плодит уведомления
        note_members.resolve_edit_request(req["token"], allow=True)
        self.assertEqual(org_kb.role_for_note(MANAGER, self.note), "editor")
        self.assertIsNotNone(notes_store.get_accessible_note(MANAGER, self.note))
        self.assertFalse(note_members.is_member(str(BACK), self.note, str(MANAGER)))  # не участник заметки
        with self.assertRaises(ValueError):
            note_members.create_edit_request(owner_user_id=BACK, note_id=self.note, requester_user_id=MANAGER)
