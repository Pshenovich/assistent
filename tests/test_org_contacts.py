from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from assistant.stores import contacts_store, note_members, org_contacts, orgs_store
from assistant.stores import notes as notes_store


class OrgContactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["CONTACTS_USER_DIR"] = os.path.join(self._tmp.name, "contacts_user")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmp.name, "reg.json")
        notes_store._CONN = None  # type: ignore[attr-defined]
        contacts_store._CONTACTS_CACHE_BY_PATH.clear()
        self.emails = {1: ["vova@acme.io"], 2: ["anna@acme.io"], 3: []}
        self._p = [
            mock.patch.object(orgs_store, "verified_emails", lambda uid: self.emails.get(int(uid), [])),
            mock.patch.object(orgs_store, "_resolve_key", lambda actor, key: None),
        ]
        for p in self._p:
            p.start()
        note_members.upsert_profile(1, username="vova", first_name="Вова")
        note_members.upsert_profile(2, username="anna_k", first_name="Анна", last_name="Котова")
        note_members.upsert_profile(3, username="petr", first_name="Пётр")

    def tearDown(self) -> None:
        for p in self._p:
            p.stop()
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def _book(self, uid: int) -> dict[int, dict]:
        return {int(c.get("telegram_user_id") or 0): c for c in contacts_store.load_contacts(telegram_user_id=uid)}

    def _join(self, oid: int, uid: int) -> None:
        orgs_store.accept_invite(uid, orgs_store.create_invite(1, oid)["token"])

    def test_members_see_each_other_and_personal_contacts_untouched(self) -> None:
        oid = orgs_store.create_org(1, "Акме", domain="acme.io")["id"]
        # у Анны Пётр уже есть как личный контакт — его не трогаем
        contacts_store.save_contacts([{"name": "Петя из бухгалтерии", "email": "", "telegram_username": "petr",
                                       "telegram_user_id": 3}], telegram_user_id=2)
        self._join(oid, 2)
        self._join(oid, 3)
        org_contacts.sync(oid)
        b1 = self._book(1)
        self.assertEqual(b1[2]["name"], "Анна Котова")
        self.assertEqual(b1[2]["email"], "anna@acme.io")
        self.assertEqual(b1[3]["telegram_username"], "petr")
        self.assertEqual(self._book(2)[3]["name"], "Петя из бухгалтерии")
        self.assertIn(1, self._book(3))
        # повторный вызов ничего не дублирует
        self.assertEqual(org_contacts.sync(oid)["added"], 0)
        self.assertEqual(len(contacts_store.load_contacts(telegram_user_id=1)), 2)

    def test_leaving_removes_only_what_org_added(self) -> None:
        oid = orgs_store.create_org(1, "Акме", domain="acme.io")["id"]
        contacts_store.save_contacts([{"name": "Петя", "email": "", "telegram_username": "petr",
                                       "telegram_user_id": 3}], telegram_user_id=2)
        self._join(oid, 2)
        self._join(oid, 3)
        org_contacts.sync(oid)
        orgs_store.leave(3, oid)
        org_contacts.sync(oid)
        self.assertNotIn(3, self._book(1))  # добавляла организация — убрали
        self.assertIn(3, self._book(2))  # личный контакт Анны — остался
        self.assertNotIn(1, self._book(3))
        self.assertNotIn(2, self._book(3))

    def test_email_rule_domain_then_calendar_never_mailbox(self) -> None:
        from assistant.stores import user_prefs

        prefs_env = mock.patch.dict(os.environ, {"USER_PREFS_DIR": os.path.join(self._tmp.name, "users")})
        prefs_env.start()
        self.addCleanup(prefs_env.stop)
        self.emails[3] = ["petr.home@mail.ru"]  # подключён только почтовый ящик
        self.assertEqual(org_contacts.member_card(3, prefer_domains=["acme.io"])["email"], "")
        user_prefs.save_prefs(3, {"google_account_email": "petr@gmail.com"})
        self.assertEqual(org_contacts.member_card(3, prefer_domains=["acme.io"])["email"], "petr@gmail.com")
        self.emails[3] = ["petr@acme.io"]
        self.assertEqual(org_contacts.member_card(3, prefer_domains=["acme.io"])["email"], "petr@acme.io")

    def test_deleted_contact_not_readded(self) -> None:
        oid = orgs_store.create_org(1, "Акме", domain="acme.io")["id"]
        self._join(oid, 2)
        org_contacts.sync(oid)
        contacts_store.save_contacts([], telegram_user_id=1)  # Вова удалил Анну у себя
        org_contacts.sync(oid)
        self.assertNotIn(2, self._book(1))

    def test_delete_org_cleans_up(self) -> None:
        oid = orgs_store.create_org(1, "Акме", domain="acme.io")["id"]
        self._join(oid, 2)
        org_contacts.sync(oid)
        orgs_store.delete_org(1, oid)
        org_contacts.sync(oid)
        self.assertEqual(self._book(1), {})


if __name__ == "__main__":
    unittest.main()
