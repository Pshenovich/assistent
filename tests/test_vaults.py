from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from assistant.stores import notes as notes_store
from assistant.stores import org_groups, orgs_store, vaults
from assistant.stores.orgs_store import OrgError

OWNER, ADMIN, BACK, BACK2, MANAGER, GUEST, OUTSIDER = 1, 2, 3, 4, 5, 6, 99


def unlockers(cred: str = "Y3JlZA") -> list[dict[str, str]]:
    return [
        {"kind": "passkey", "credential_id": cred, "salt": "c2FsdA", "wrapped_umk": "d3JhcA", "label": "MacBook"},
        {"kind": "recovery", "salt": "c2FsdDI", "wrapped_umk": "d3JhcDI"},
    ]


def enable(uid: int) -> None:
    vaults.setup(uid, f'{{"kty":"EC","x":"{uid}"}}', "cHJpdg", unlockers())


class VaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmp.name, "reg.json")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._patches = [
            mock.patch.object(orgs_store, "verified_emails", lambda uid: [f"u{int(uid)}@obuchat.me"]),
            mock.patch.object(orgs_store, "_username", lambda uid: ""),
            mock.patch.object(orgs_store, "_resolve_key", lambda actor, key: int(key.split("@")[0][1:])),
        ]
        for p in self._patches:
            p.start()
        self.org = orgs_store.create_org(OWNER, "Обучат", domain="obuchat.me")["id"]
        for uid in (ADMIN, BACK, BACK2, MANAGER, GUEST):
            orgs_store.add_member(OWNER, self.org, f"u{uid}@obuchat.me", role="admin" if uid == ADMIN else "member")
        self.okk = org_groups.create(ADMIN, self.org, "ОКК")["id"]
        self.backend = org_groups.create(ADMIN, self.org, "ОКК · бэкенд", parent_id=self.okk)["id"]
        for uid in (BACK, BACK2):
            org_groups.add_member(ADMIN, self.org, self.backend, uid)
        org_groups.add_member(ADMIN, self.org, self.okk, MANAGER)
        for uid in (OWNER, BACK, MANAGER):
            enable(uid)
        self.vault = vaults.create(OWNER, self.org, "ОКК · бэкенд", "ZW52LW93bmVy",
                                   access=[{"principal": f"g:{self.backend}", "role": "editor"}])["id"]

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    # --- ключи ---

    def test_setup_needs_recovery_code_and_is_once(self) -> None:
        with self.assertRaises(OrgError):
            vaults.setup(BACK2, "a2V5", "cHJpdg", unlockers()[:1])
        with self.assertRaises(OrgError):
            enable(OWNER)
        keys = vaults.my_keys(OWNER)
        self.assertTrue(keys["enabled"])
        self.assertEqual({u["kind"] for u in keys["unlockers"]}, {"passkey", "recovery"})

    def test_last_passkey_cannot_be_removed_and_recovery_is_replaced(self) -> None:
        pk = next(u for u in vaults.my_keys(OWNER)["unlockers"] if u["kind"] == "passkey")
        with self.assertRaises(OrgError):
            vaults.remove_unlocker(OWNER, pk["id"])
        vaults.add_unlocker(OWNER, {"kind": "passkey", "credential_id": "aXBob25l", "salt": "cw", "wrapped_umk": "dw"})
        vaults.remove_unlocker(OWNER, pk["id"])
        vaults.add_unlocker(OWNER, {"kind": "recovery", "salt": "bmV3", "wrapped_umk": "bmV3"})
        rec = [u for u in vaults.my_keys(OWNER)["unlockers"] if u["kind"] == "recovery"]
        self.assertEqual([r["salt"] for r in rec], ["bmV3"])

    def test_garbage_blobs_rejected(self) -> None:
        with self.assertRaises(OrgError):
            vaults.add_item(OWNER, self.vault, "<script>")
        with self.assertRaises(OrgError):
            vaults.add_item(OWNER, self.vault, "x" * (vaults.BLOB_MAX + 1))

    # --- доступ ---

    def test_team_members_see_vault_others_do_not(self) -> None:
        self.assertEqual(vaults.role_in(self.vault, BACK), "editor")
        self.assertEqual([v["id"] for v in vaults.list_for(BACK)], [self.vault])
        self.assertEqual(vaults.list_for(MANAGER), [])  # отдел ОКК, но не команда
        self.assertEqual(vaults.list_for(ADMIN), [])  # админ организации сейф автоматически не видит
        with self.assertRaises(OrgError):
            vaults.items(self.vault, ADMIN)
        with self.assertRaises(OrgError):
            vaults.items(self.vault, OUTSIDER)

    def test_cannot_open_to_everyone(self) -> None:
        with self.assertRaises(OrgError):
            vaults.set_access(OWNER, self.vault, org_groups.ALL, "viewer")
        with self.assertRaises(OrgError):
            vaults.create(OWNER, self.org, "Все", "ZQ", access=[{"principal": "all", "role": "viewer"}])

    def test_only_owner_changes_access_and_last_owner_stays(self) -> None:
        with self.assertRaises(OrgError):
            vaults.set_access(BACK, self.vault, f"u:{MANAGER}", "viewer")
        with self.assertRaises(OrgError):
            vaults.set_access(OWNER, self.vault, f"u:{OWNER}", "")
        with self.assertRaises(OrgError):
            vaults.set_access(OWNER, self.vault, f"u:{OUTSIDER}", "viewer")

    def test_viewer_cannot_write(self) -> None:
        vaults.set_access(OWNER, self.vault, f"u:{MANAGER}", "viewer")
        with self.assertRaises(OrgError):
            vaults.add_item(MANAGER, self.vault, "Y3Q")
        iid = vaults.add_item(BACK, self.vault, "Y3Q")["id"]
        self.assertEqual([i["id"] for i in vaults.items(self.vault, MANAGER)], [iid])

    # --- конверты ---

    def test_pending_envelopes_flow(self) -> None:
        mine = vaults.get(self.vault, OWNER)
        self.assertEqual(mine["envelope"], "ZW52LW93bmVy")
        self.assertEqual([p["user_id"] for p in mine["pending"]], [str(BACK)])  # BACK2 ещё не включил сейфы
        self.assertEqual(mine["waiting_setup"], 1)
        # у BACK конверта нет — он не может раздавать и не видит pending
        back = vaults.get(self.vault, BACK)
        self.assertIsNone(back["envelope"])
        self.assertEqual(back["pending"], [])
        with self.assertRaises(OrgError):
            vaults.put_envelopes(BACK, self.vault, [{"user_id": str(BACK), "envelope": "Zm9v"}])
        # владелец кладёт конверт BACK; конверт постороннему игнорируется
        r = vaults.put_envelopes(OWNER, self.vault, [
            {"user_id": str(BACK), "envelope": "ZW52LWJhY2s"}, {"user_id": str(OUTSIDER), "envelope": "eA"},
            {"user_id": str(MANAGER), "envelope": "eA"},
        ])
        self.assertEqual(r["put"], 1)
        self.assertEqual(vaults.get(self.vault, BACK)["envelope"], "ZW52LWJhY2s")
        # BACK2 включил сейфы — теперь его конверт может положить BACK
        enable(BACK2)
        self.assertEqual([p["user_id"] for p in vaults.get(self.vault, BACK)["pending"]], [str(BACK2)])
        vaults.put_envelopes(BACK, self.vault, [{"user_id": str(BACK2), "envelope": "ZW52LTI"}])
        states = {p["user_id"]: p["state"] for p in vaults.access(self.vault, OWNER)["people"]}
        self.assertEqual(states, {str(OWNER): "ok", str(BACK): "ok", str(BACK2): "ok"})

    def test_existing_envelope_is_not_overwritten(self) -> None:
        vaults.put_envelopes(OWNER, self.vault, [{"user_id": str(BACK), "envelope": "Z29vZA"}])
        enable(BACK2)
        vaults.put_envelopes(OWNER, self.vault, [{"user_id": str(BACK2), "envelope": "Yg"}])
        # BACK2 пытается подменить конверт BACK — тот уже не «ждёт»
        self.assertEqual(vaults.put_envelopes(BACK2, self.vault, [{"user_id": str(BACK), "envelope": "ZXZpbA"}])["put"], 0)
        self.assertEqual(vaults.get(self.vault, BACK)["envelope"], "Z29vZA")

    def test_leaving_team_drops_envelope_and_marks_departure(self) -> None:
        vaults.put_envelopes(OWNER, self.vault, [{"user_id": str(BACK), "envelope": "ZW52LWJhY2s"}])
        org_groups.remove_member(ADMIN, self.org, self.backend, BACK)
        self.assertEqual(vaults.list_for(BACK), [])
        v = vaults.get(self.vault, OWNER)
        self.assertEqual(v["departed"], 1)
        with orgs_store._LOCK:
            n = vaults._conn().execute(
                "SELECT COUNT(*) FROM vault_envelopes WHERE vault_id = ? AND user_id = ?", (self.vault, str(BACK))
            ).fetchone()[0]
        self.assertEqual(n, 0)

    def test_reset_drops_old_envelopes(self) -> None:
        vaults.put_envelopes(OWNER, self.vault, [{"user_id": str(BACK), "envelope": "ZW52LWJhY2s"}])
        vaults.setup(BACK, "bmV3a2V5", "cHJpdg", unlockers(), reset=True)
        back = vaults.get(self.vault, BACK)
        self.assertIsNone(back["envelope"])
        self.assertIn(str(BACK), [p["user_id"] for p in vaults.get(self.vault, OWNER)["pending"]])

    # --- личный сейф, журнал, удаление ---

    def test_personal_vault_is_private_and_single(self) -> None:
        pv = vaults.create(BACK, None, "Личный", "cA")["id"]
        self.assertTrue(vaults.get(pv, BACK)["personal"])
        self.assertNotIn(pv, [v["id"] for v in vaults.list_for(OWNER)])
        with self.assertRaises(OrgError):
            vaults.items(pv, OWNER)
        with self.assertRaises(OrgError):
            vaults.set_access(BACK, pv, f"u:{OWNER}", "viewer")
        with self.assertRaises(OrgError):
            vaults.create(BACK, None, "Второй", "cA")

    def test_log_records_fact_only(self) -> None:
        iid = vaults.add_item(OWNER, self.vault, "Y3Q")["id"]
        vaults.log(OWNER, self.vault, iid, "copy")
        with self.assertRaises(OrgError):
            vaults.log(OWNER, self.vault, iid, "steal")
        acts = [e["action"] for e in vaults.item_log(OWNER, self.vault, iid)]
        self.assertIn("copy", acts)
        self.assertIn("create", acts)

    def test_delete_org_removes_vaults(self) -> None:
        vaults.add_item(OWNER, self.vault, "Y3Q")
        orgs_store.delete_org(OWNER, self.org)
        with orgs_store._LOCK:
            c = vaults._conn()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM vaults").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM vault_items").fetchone()[0], 0)

    def test_list_shows_access_line(self) -> None:
        self.assertEqual(vaults.get(self.vault, OWNER)["access"], "ОКК · бэкенд, 1 чел.")


if __name__ == "__main__":
    unittest.main()
