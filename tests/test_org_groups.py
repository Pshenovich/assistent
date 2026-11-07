from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from assistant.stores import notes as notes_store
from assistant.stores import org_groups
from assistant.stores import orgs_store
from assistant.stores.orgs_store import OrgError

OWNER, ADMIN, LEAD, BACK, FRONT, MANAGER, GUEST, OUTSIDER = 1, 2, 3, 4, 5, 6, 7, 99


class OrgGroupsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmpdir.name, "reg.json")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._patches = [
            mock.patch.object(orgs_store, "verified_emails", lambda uid: [f"u{int(uid)}@obuchat.me"]),
            mock.patch.object(orgs_store, "_username", lambda uid: ""),
            mock.patch.object(orgs_store, "_resolve_key", lambda actor, key: int(key.split("@")[0][1:])),
        ]
        for p in self._patches:
            p.start()
        self.org = orgs_store.create_org(OWNER, "Обучат", domain="obuchat.me")["id"]
        for uid in (ADMIN, LEAD, BACK, FRONT, MANAGER, GUEST):
            orgs_store.add_member(OWNER, self.org, f"u{uid}@obuchat.me", role="admin" if uid == ADMIN else "member")
        guest_row = next(m for m in orgs_store.list_members(self.org) if m["user_id"] == str(GUEST))
        orgs_store.set_guest(ADMIN, self.org, guest_row["id"], True)

        self.okk = org_groups.create(ADMIN, self.org, "ОКК")["id"]
        org_groups.set_lead(OWNER, self.org, self.okk, LEAD)
        self.backend = org_groups.create(LEAD, self.org, "ОКК · бэкенд", parent_id=self.okk)["id"]
        self.frontend = org_groups.create(LEAD, self.org, "ОКК · фронтенд", parent_id=self.okk)["id"]
        org_groups.add_member(LEAD, self.org, self.backend, BACK)
        org_groups.add_member(LEAD, self.org, self.frontend, FRONT)
        org_groups.add_member(LEAD, self.org, self.okk, MANAGER)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_team_member_gets_department_access_but_not_sibling_team(self) -> None:
        p = org_groups.principals(self.org, BACK)
        self.assertIn(f"g:{self.backend}", p)
        self.assertIn(f"g:{self.okk}", p)
        self.assertNotIn(f"g:{self.frontend}", p)
        self.assertIn(org_groups.ALL, p)

    def test_metrics_visibility(self) -> None:
        lead_sees = set(org_groups.metrics_visible_ids(self.org, LEAD))
        self.assertEqual(lead_sees, {str(LEAD), str(BACK), str(FRONT), str(MANAGER)})
        admin_sees = set(org_groups.metrics_visible_ids(self.org, ADMIN))
        self.assertIn(str(OWNER), admin_sees)
        self.assertNotIn(str(GUEST), admin_sees)
        self.assertEqual(org_groups.metrics_visible_ids(self.org, BACK), [str(BACK)])
        self.assertTrue(org_groups.can_view_metrics(self.org, LEAD, FRONT))
        self.assertFalse(org_groups.can_view_metrics(self.org, BACK, FRONT))
        self.assertEqual(org_groups.metrics_visible_ids(self.org, OUTSIDER), [])

    def test_department_member_does_not_get_team_access(self) -> None:
        p = org_groups.principals(self.org, MANAGER)
        self.assertIn(f"g:{self.okk}", p)
        self.assertNotIn(f"g:{self.backend}", p)

    def test_department_members_include_teams(self) -> None:
        self.assertEqual(
            sorted(org_groups.member_ids(self.org, self.okk)), sorted(str(u) for u in (LEAD, BACK, FRONT, MANAGER))
        )
        self.assertEqual(org_groups.member_ids(self.org, self.backend), [str(BACK)])

    def test_guest_is_not_in_all_and_outsider_has_nothing(self) -> None:
        self.assertNotIn(org_groups.ALL, org_groups.principals(self.org, GUEST))
        self.assertEqual(org_groups.principals(self.org, OUTSIDER), set())

    def test_promoting_guest_clears_flag(self) -> None:
        orgs_store.set_role(OWNER, self.org, GUEST, "admin")
        self.assertIn(org_groups.ALL, org_groups.principals(self.org, GUEST))

    def test_lead_manages_own_department_only(self) -> None:
        other = org_groups.create(ADMIN, self.org, "Чат-боты")["id"]
        with self.assertRaises(OrgError):
            org_groups.add_member(LEAD, self.org, other, BACK)
        with self.assertRaises(OrgError):
            org_groups.create(LEAD, self.org, "Новый отдел")
        with self.assertRaises(OrgError):
            org_groups.delete(LEAD, self.org, self.okk)
        org_groups.delete(LEAD, self.org, self.frontend)
        self.assertNotIn(f"g:{self.frontend}", org_groups.principals(self.org, FRONT))

    def test_plain_member_cannot_manage(self) -> None:
        with self.assertRaises(OrgError):
            org_groups.add_member(BACK, self.org, self.backend, FRONT)
        with self.assertRaises(OrgError):
            org_groups.create(BACK, self.org, "Своя", parent_id=self.okk)

    def test_only_org_members_can_join(self) -> None:
        with self.assertRaises(OrgError):
            org_groups.add_member(ADMIN, self.org, self.okk, OUTSIDER)
        with self.assertRaises(OrgError):
            org_groups.list_groups(self.org, OUTSIDER)

    def test_no_teams_inside_teams_and_unique_names(self) -> None:
        with self.assertRaises(OrgError):
            org_groups.create(ADMIN, self.org, "Глубже", parent_id=self.backend)
        with self.assertRaises(OrgError):
            org_groups.create(ADMIN, self.org, "окк")

    def test_removed_member_leaves_groups_and_lead(self) -> None:
        lead_row = next(m for m in orgs_store.list_members(self.org) if m["user_id"] == str(LEAD))
        orgs_store.remove_member(OWNER, self.org, lead_row["id"])
        tree = org_groups.list_groups(self.org, OWNER)
        okk = next(d for d in tree if d["id"] == self.okk)
        self.assertIsNone(okk["lead_user_id"])
        self.assertNotIn(str(LEAD), okk["member_ids"])

    def test_list_groups_tree(self) -> None:
        tree = org_groups.list_groups(self.org, BACK)
        okk = next(d for d in tree if d["id"] == self.okk)
        self.assertEqual({t["name"] for t in okk["teams"]}, {"ОКК · бэкенд", "ОКК · фронтенд"})
        self.assertFalse(okk["can_manage"])
        self.assertTrue(next(d for d in org_groups.list_groups(self.org, LEAD) if d["id"] == self.okk)["can_manage"])

    def test_delete_org_removes_groups(self) -> None:
        orgs_store.delete_org(OWNER, self.org)
        with orgs_store._LOCK:
            n = orgs_store._conn().execute("SELECT COUNT(*) FROM org_groups").fetchone()[0]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()


class OrgGroupLabelsTests(OrgGroupsTests):
    def test_labels_for_contact_cards(self) -> None:
        by_user, by_email = org_groups.labels_for_viewer(BACK)
        self.assertEqual(by_user[str(BACK)], ["ОКК · бэкенд"])  # название команды уже с отделом — как есть
        self.assertEqual(by_user[str(MANAGER)], ["ОКК"])  # в отделе без команды
        self.assertEqual(by_email["u6@obuchat.me"], str(MANAGER))
        self.assertEqual(org_groups.labels_for_viewer(OUTSIDER), ({}, {}))
