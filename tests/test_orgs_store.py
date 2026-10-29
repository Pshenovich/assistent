from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from assistant.stores import notes as notes_store
from assistant.stores import orgs_store
from assistant.stores.orgs_store import OrgError


class OrgsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmpdir.name, "reg.json")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self.emails: dict[int, list[str]] = {
            1: ["vova@obuchat.me"],
            2: ["anna@obuchat.me"],
            3: ["guest@gmail.com"],
        }
        self.usernames: dict[int, str] = {}
        self._patches = [
            mock.patch.object(orgs_store, "verified_emails", lambda uid: self.emails.get(int(uid), [])),
            mock.patch.object(orgs_store, "_username", lambda uid: self.usernames.get(int(uid), "")),
            mock.patch.object(orgs_store, "_resolve_key", lambda actor, key: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def _org(self) -> dict:
        return orgs_store.create_org(1, "Обучат", domain="obuchat.me")

    def test_create_sets_owner_slug_and_domain(self) -> None:
        org = self._org()
        self.assertEqual(org["slug"], "obuchat")
        self.assertEqual(org["domains"], ["obuchat.me"])
        self.assertEqual(orgs_store.role_of(org["id"], 1), "owner")
        self.assertEqual(orgs_store.current_org(1)["id"], org["id"])

    def test_public_or_foreign_domain_rejected(self) -> None:
        with self.assertRaises(OrgError):
            orgs_store.create_org(3, "Я", domain="gmail.com")
        with self.assertRaises(OrgError):
            orgs_store.create_org(1, "Чужая", domain="example.com")

    def test_domain_belongs_to_one_org(self) -> None:
        self._org()
        with self.assertRaises(OrgError) as ctx:
            orgs_store.create_org(2, "Ещё", domain="obuchat.me")
        self.assertEqual(ctx.exception.code, 409)

    def test_slug_check_and_rename(self) -> None:
        org = self._org()
        self.assertTrue(orgs_store.check_slug("obuchat"))
        self.assertTrue(orgs_store.check_slug("Ab"))
        self.assertEqual(orgs_store.check_slug("obuchat", exclude_org_id=org["id"]), "")
        updated = orgs_store.update_org(1, org["id"], {"name": "Обучат.ми", "slug": "obuchat-me"})
        self.assertEqual(updated["slug"], "obuchat-me")
        orgs_store.create_org(2, "obuchat me")
        self.assertEqual(orgs_store.get_org_by_slug("obuchat-me-2")["name"], "obuchat me")

    def test_join_by_domain_request_then_approve(self) -> None:
        org = self._org()
        self.assertEqual([s["id"] for s in orgs_store.suggestions(2)], [org["id"]])
        self.assertEqual(orgs_store.suggestions(3), [])
        self.assertEqual(orgs_store.join_by_domain(2, org["id"]), "pending")
        self.assertTrue(orgs_store.suggestions(2)[0]["pending"])
        self.assertEqual(orgs_store.role_of(org["id"], 2), "")
        orgs_store.approve(1, org["id"], 2)
        self.assertEqual(orgs_store.role_of(org["id"], 2), "member")
        self.assertEqual(orgs_store.suggestions(2), [])
        with self.assertRaises(OrgError):
            orgs_store.join_by_domain(3, org["id"])

    def test_auto_join_mode(self) -> None:
        org = self._org()
        orgs_store.update_org(1, org["id"], {"join_mode": "auto"})
        self.assertEqual(orgs_store.join_by_domain(2, org["id"]), "active")

    def test_invite_link(self) -> None:
        org = self._org()
        inv = orgs_store.create_invite(1, org["id"])
        self.assertEqual(orgs_store.resolve_invite(inv["token"])["org"]["id"], org["id"])
        orgs_store.accept_invite(3, inv["token"])
        self.assertEqual(orgs_store.role_of(org["id"], 3), "member")
        with self.assertRaises(OrgError):
            orgs_store.create_invite(3, org["id"])
        orgs_store.revoke_invite(1, org["id"], inv["token"])
        self.assertIsNone(orgs_store.resolve_invite(inv["token"]))

    def test_add_member_pending_until_first_login(self) -> None:
        org = self._org()
        res = orgs_store.add_member(1, org["id"], "Guest@Gmail.com")
        self.assertEqual(res["status"], "invited")
        res2 = orgs_store.add_member(1, org["id"], "@petya_dev")
        self.assertEqual(res2["key"], "tg:petya_dev")
        with self.assertRaises(OrgError):
            orgs_store.add_member(1, org["id"], "guest@gmail.com")
        self.assertEqual(orgs_store.claim_pending(3), [org["id"]])
        self.assertEqual(orgs_store.role_of(org["id"], 3), "member")
        self.usernames[4] = "petya_dev"
        orgs_store.claim_pending(4)
        self.assertEqual(sorted(orgs_store.member_ids(org["id"])), [1, 3, 4])

    def test_add_existing_user_is_active(self) -> None:
        org = self._org()
        with mock.patch.object(orgs_store, "_resolve_key", lambda actor, key: 2):
            res = orgs_store.add_member(1, org["id"], "anna@obuchat.me")
        self.assertEqual(res["status"], "active")
        self.assertEqual(orgs_store.role_of(org["id"], 2), "member")

    def test_roles_and_permissions(self) -> None:
        org = self._org()
        oid = org["id"]
        orgs_store.accept_invite(2, orgs_store.create_invite(1, oid)["token"])
        orgs_store.accept_invite(3, orgs_store.create_invite(1, oid)["token"])
        self.assertFalse(orgs_store.can(2, oid, "invite"))
        orgs_store.set_role(1, oid, 2, "admin")
        self.assertTrue(orgs_store.can(2, oid, "invite"))
        self.assertTrue(orgs_store.can(2, oid, "edit_digest"))
        self.assertFalse(orgs_store.can(2, oid, "edit_org"))
        with self.assertRaises(OrgError):
            orgs_store.set_role(2, oid, 3, "admin")
        with self.assertRaises(OrgError):
            orgs_store.add_member(2, oid, "x@obuchat.me", role="admin")
        members = {m["user_id"]: m for m in orgs_store.list_members(oid)}
        with self.assertRaises(OrgError):
            orgs_store.remove_member(2, oid, members["1"]["id"])
        orgs_store.remove_member(2, oid, members["3"]["id"])
        self.assertEqual(orgs_store.role_of(oid, 3), "")

    def test_owner_transfer_and_leave(self) -> None:
        org = self._org()
        oid = org["id"]
        orgs_store.accept_invite(2, orgs_store.create_invite(1, oid)["token"])
        with self.assertRaises(OrgError):
            orgs_store.leave(1, oid)
        with self.assertRaises(OrgError):
            orgs_store.set_role(1, oid, 1, "member")
        orgs_store.set_role(1, oid, 2, "owner")
        self.assertEqual(orgs_store.role_of(oid, 2), "owner")
        self.assertEqual(orgs_store.role_of(oid, 1), "admin")
        orgs_store.leave(1, oid)
        self.assertEqual(orgs_store.orgs_for_user(1), [])
        self.assertEqual(orgs_store.suggestions(1), [])

    def test_settings_need_admin(self) -> None:
        org = self._org()
        orgs_store.accept_invite(3, orgs_store.create_invite(1, org["id"])["token"])
        with self.assertRaises(OrgError):
            orgs_store.set_settings(3, org["id"], "market", {"competitors": ["Gong"]})
        orgs_store.set_settings(1, org["id"], "market", {"competitors": ["Gong"]})
        self.assertEqual(orgs_store.get_settings(org["id"])["market"]["competitors"], ["Gong"])

    def test_billing_seats_and_member_plans(self) -> None:
        org = self._org()
        oid = org["id"]
        orgs_store.accept_invite(2, orgs_store.create_invite(1, oid)["token"])
        orgs_store.set_role(1, oid, 2, "admin")
        with self.assertRaises(OrgError):
            orgs_store.set_billing(2, oid, plan="pro", seats=5)
        members = {m["user_id"]: m for m in orgs_store.list_members(oid)}
        with self.assertRaises(OrgError):
            orgs_store.set_member_plan(1, oid, members["2"]["id"], "pro")  # мест 0
        org = orgs_store.set_billing(1, oid, plan="pro", seats=1)
        self.assertEqual((org["plan"], org["seats"], org["seats_used"]), ("pro", 1, 0))
        orgs_store.set_member_plan(1, oid, members["2"]["id"], "pro")
        with self.assertRaises(OrgError):
            orgs_store.set_member_plan(1, oid, members["1"]["id"], "pro")  # мест больше нет
        with self.assertRaises(OrgError):
            orgs_store.set_billing(1, oid, seats=0)  # место занято
        with self.assertRaises(OrgError):
            orgs_store.set_member_plan(1, oid, members["1"]["id"], "gold")
        self.assertEqual(orgs_store.get_org(oid)["seats_used"], 1)
        orgs_store.set_member_plan(1, oid, members["2"]["id"], "free")
        self.assertEqual(orgs_store.get_org(oid)["seats_used"], 0)

    def test_admin_set_owner(self) -> None:
        org = self._org()
        oid = org["id"]
        with mock.patch.dict(os.environ, {"TELEGRAM_ACCESS_APPROVER_IDS": "99"}):
            self.assertTrue(orgs_store.is_operator(99))
            self.assertFalse(orgs_store.is_operator(1))
        orgs_store.admin_set_owner(oid, 2, by="99")  # не был участником — добавлен владельцем
        self.assertEqual(orgs_store.role_of(oid, 2), "owner")
        self.assertEqual(orgs_store.role_of(oid, 1), "admin")
        listed = orgs_store.admin_list_orgs()
        self.assertEqual([o["user_id"] for o in listed[0]["owners"]], ["2"])
        self.assertEqual(orgs_store.org_names_by_user()["1"], ["Обучат"])
        self.assertEqual(orgs_store.resolve_user(1, "42"), 42)

    def test_delete_org(self) -> None:
        org = self._org()
        orgs_store.delete_org(1, org["id"])
        self.assertIsNone(orgs_store.get_org(org["id"]))
        self.assertEqual(orgs_store.orgs_for_user(1), [])


if __name__ == "__main__":
    unittest.main()
