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
        # отдел ОКК, но не команда; админ организации — тоже: видят только название, чтобы запросить доступ
        for uid in (MANAGER, ADMIN):
            self.assertEqual([(v["id"], v["foreign"], v["envelope"]) for v in vaults.list_for(uid)], [(self.vault, True, None)])
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
            vaults.set_access(OWNER, self.vault, f"u:{OUTSIDER}", "viewer")
        # владелец организации остаётся владельцем сейфа и без строки в доступе
        vaults.set_access(OWNER, self.vault, f"u:{OWNER}", "")
        self.assertEqual(vaults.role_in(self.vault, OWNER), "owner")
        # в личном сейфе последнего владельца убрать нельзя
        pv = vaults.create(BACK, None, "Личный", "cA")["id"]
        with self.assertRaises(OrgError):
            vaults.set_access(BACK, pv, f"u:{BACK}", "")

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

    def _backdate_items(self) -> None:
        with orgs_store._LOCK:
            c = vaults._conn()
            c.execute("UPDATE vault_items SET created_at = '2026-01-01T00:00:00+00:00', updated_at = '2026-01-01T00:00:00+00:00'")
            c.commit()

    def test_leaving_team_drops_envelope_and_marks_departure(self) -> None:
        vaults.put_envelopes(OWNER, self.vault, [{"user_id": str(BACK), "envelope": "ZW52LWJhY2s"}])
        iid = vaults.add_item(OWNER, self.vault, "Y3Q")["id"]
        self._backdate_items()
        org_groups.remove_member(ADMIN, self.org, self.backend, BACK)
        self.assertTrue(all(v.get("foreign") for v in vaults.list_for(BACK)))
        v = vaults.get(self.vault, OWNER)
        self.assertEqual(v["departed"], 1)
        self.assertEqual([d["user_id"] for d in v["departures"]], [str(BACK)])
        self.assertTrue(v["rotate"])  # ушедший мог сохранить ключ — его надо сменить
        # пароль сменили — список «смените пароли» пропал сам
        vaults.update_item(OWNER, self.vault, iid, "bmV3")
        self.assertEqual(vaults.get(self.vault, OWNER)["departed"], 0)
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

    # --- смена ключа ---

    def test_rotate_replaces_items_and_envelopes(self) -> None:
        vaults.put_envelopes(OWNER, self.vault, [{"user_id": str(BACK), "envelope": "ZW52LWJhY2s"}])
        iid = vaults.add_item(OWNER, self.vault, "b2xk")["id"]
        org_groups.remove_member(ADMIN, self.org, self.backend, BACK)
        v = vaults.get(self.vault, OWNER)
        self.assertEqual([k["user_id"] for k in v["rotate_keys"]], [str(OWNER)])
        kv = v["key_version"]
        # не все записи — отказ
        with self.assertRaises(OrgError):
            vaults.rotate(OWNER, self.vault, kv, [], [{"user_id": str(OWNER), "envelope": "bmV3"}])
        # без конверта себе — отказ
        with self.assertRaises(OrgError):
            vaults.rotate(OWNER, self.vault, kv, [{"id": iid, "blob": "bmV3"}], [])
        out = vaults.rotate(OWNER, self.vault, kv, [{"id": iid, "blob": "bmV3"}],
                            [{"user_id": str(OWNER), "envelope": "ZW52Mg"}, {"user_id": str(BACK), "envelope": "eA"}])
        self.assertEqual(out["key_version"], kv + 1)
        self.assertFalse(out["rotate"])
        self.assertEqual(out["envelope"], "ZW52Mg")
        self.assertEqual(vaults.items(self.vault, OWNER)[0]["blob"], "bmV3")
        # старой версией ключа писать нельзя
        with self.assertRaises(OrgError):
            vaults.add_item(OWNER, self.vault, "eA", key_version=kv)
        vaults.add_item(OWNER, self.vault, "eA", key_version=kv + 1)

    # --- владелец организации и запросы доступа ---

    def _staff_vault(self) -> int:
        """Сейф сотрудника: владелец организации туда не добавлен — но конверт ему кладётся при создании."""
        enable(BACK2)
        vid = vaults.create(BACK, self.org, "Только бэкенд", "ZW52LWJhY2s",
                            owner_envelopes=[{"user_id": str(OWNER), "envelope": "b3du"}])["id"]
        vaults.set_access(BACK, vid, f"u:{BACK2}", "viewer")
        return vid

    def test_org_vault_needs_owner_envelope(self) -> None:
        with self.assertRaises(OrgError):
            vaults.create(BACK, self.org, "Без владельца", "ZW52")
        org2 = orgs_store.create_org(BACK2, "Вторая")["id"]
        orgs_store.add_member(BACK2, org2, f"u{BACK}@obuchat.me")
        with self.assertRaises(OrgError):  # владелец второй организации ещё не включил сейфы
            vaults.create(BACK, org2, "Рано", "ZW52")
        self.assertEqual(vaults.owner_keys_for(BACK)[str(self.org)][0]["user_id"], str(OWNER))
        self.assertEqual(vaults.owner_keys_for(BACK)[str(org2)], [])

    def test_org_owner_is_owner_of_every_vault(self) -> None:
        vid = self._staff_vault()
        mine = vaults.get(vid, OWNER)
        self.assertEqual(mine["role"], "owner")
        self.assertTrue(mine["org_owner"])
        self.assertEqual(mine["envelope"], "b3du")
        vaults.set_access(OWNER, vid, f"u:{MANAGER}", "viewer")  # может менять доступ
        people = {p["user_id"]: p["org_owner"] for p in vaults.access(vid, OWNER)["people"]}
        self.assertTrue(people[str(OWNER)])
        # админ организации — нет
        self.assertEqual(vaults.role_in(vid, ADMIN), "")
        # сотрудник уволился — сейф остаётся у владельца организации
        orgs_store.remove_member(OWNER, self.org, next(
            m["id"] for m in orgs_store.list_members(self.org) if m["user_id"] == str(BACK)))
        self.assertEqual(vaults.get(vid, OWNER)["role"], "owner")
        self.assertTrue(vaults.get(vid, OWNER)["rotate"])

    def test_member_requests_access_owner_decides(self) -> None:
        vid = self._staff_vault()
        f = next(v for v in vaults.list_for(MANAGER) if v["id"] == vid)
        self.assertTrue(f["foreign"])
        self.assertIsNone(f["envelope"])
        with self.assertRaises(OrgError):
            vaults.items(vid, MANAGER)
        vaults.request_access(MANAGER, vid)
        with self.assertRaises(OrgError):
            vaults.request_access(MANAGER, vid)  # уже ждёт
        self.assertEqual(next(v for v in vaults.list_for(MANAGER) if v["id"] == vid)["request"]["status"], "pending")
        ev = vaults.watch()
        self.assertEqual([(e["kind"], e["to"]) for e in ev], [("access_request", [str(OWNER), str(BACK)])])
        self.assertEqual([r["user_id"] for r in vaults.get(vid, BACK)["requests"]], [str(MANAGER)])
        self.assertEqual(vaults.get(vid, BACK2)["requests"], [])  # смотрящий запросов не видит
        with self.assertRaises(OrgError):
            vaults.decide_request(BACK2, vid, MANAGER, "viewer")
        out = vaults.decide_request(BACK, vid, MANAGER, "viewer")
        self.assertIn(str(MANAGER), [p["user_id"] for p in out["pending"]])  # браузер BACK положит конверт
        self.assertEqual(vaults.role_in(vid, MANAGER), "viewer")
        ev = vaults.watch()
        self.assertEqual([(e["kind"], e["to"], e["role"]) for e in ev], [("access_approved", [str(MANAGER)], "viewer")])

    def test_request_rejected_and_guest_cannot_request(self) -> None:
        vid = self._staff_vault()
        vaults.request_access(MANAGER, vid)
        vaults.decide_request(OWNER, vid, MANAGER, "")
        f = next(v for v in vaults.list_for(MANAGER) if v["id"] == vid)
        self.assertEqual(f["request"]["status"], "rejected")
        self.assertEqual(vaults.role_in(vid, MANAGER), "")
        # гость сейфов организации не видит и запросить не может
        mid = next(m["id"] for m in orgs_store.list_members(self.org) if m["user_id"] == str(GUEST))
        orgs_store.set_guest(OWNER, self.org, mid, True)
        self.assertNotIn(vid, [v["id"] for v in vaults.list_for(GUEST)])
        with self.assertRaises(OrgError):
            vaults.request_access(GUEST, vid)

    def test_departure_notifies_editors_once(self) -> None:
        vaults.put_envelopes(OWNER, self.vault, [{"user_id": str(BACK), "envelope": "ZW52LWJhY2s"}])
        enable(BACK2)
        vaults.put_envelopes(OWNER, self.vault, [{"user_id": str(BACK2), "envelope": "Yg"}])
        vaults.add_item(OWNER, self.vault, "Y3Q")
        self._backdate_items()
        org_groups.remove_member(ADMIN, self.org, self.backend, BACK)
        ev = vaults.watch()
        self.assertEqual([(e["kind"], e["who"], e["to"]) for e in ev], [("departure", str(BACK), [str(OWNER), str(BACK2)])])
        self.assertEqual(vaults.watch(), [])


class DeviceUnlockTests(unittest.TestCase):
    """Браузеры без PRF (Arc): обёртку ключа сейфов отдаём только после проверенного касания."""

    def setUp(self) -> None:
        import base64
        import hashlib
        import json

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["TELEGRAM_REGISTRY_PATH"] = os.path.join(self._tmp.name, "reg.json")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self.b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
        self.key = ec.generate_private_key(ec.SECP256R1())
        spki = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        vaults.setup(OWNER, '{"kty":"EC"}', "cHJpdg", [
            {"kind": "device", "credential_id": "ZGV2", "salt": "c2FsdA", "wrapped_umk": "c2VjcmV0", "label": "Mac",
             "public_key": self.b64(spki), "alg": -7, "rp_id": "getorbita.me"},
            {"kind": "recovery", "salt": "c2FsdDI", "wrapped_umk": "d3JhcDI"},
        ])
        self.dev = next(u for u in vaults.my_keys(OWNER)["unlockers"] if u["kind"] == "device")

        def sign(challenge: str, *, origin: str = "https://getorbita.me", rp: str = "getorbita.me",
                 flags: int = 0x05, count: int = 0, key=None) -> tuple[str, str, str]:
            cdj = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": origin}).encode()
            auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")
            sig = (key or self.key).sign(auth + hashlib.sha256(cdj).digest(), ec.ECDSA(hashes.SHA256()))
            return self.b64(cdj), self.b64(auth), self.b64(sig)

        self.sign = sign

    def tearDown(self) -> None:
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_wrap_hidden_until_touch_verified(self) -> None:
        self.assertEqual(self.dev["wrapped_umk"], "")
        ch = vaults.device_challenge(OWNER, self.dev["id"])["challenge"]
        out = vaults.unlock_device(OWNER, self.dev["id"], *self.sign(ch))
        self.assertEqual(out["wrapped_umk"], "c2VjcmV0")
        # вызов одноразовый
        with self.assertRaises(OrgError):
            vaults.unlock_device(OWNER, self.dev["id"], *self.sign(ch))

    def test_bad_touch_rejected(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec

        cases = [
            {"key": ec.generate_private_key(ec.SECP256R1())},  # чужой ключ
            {"flags": 0x01},  # без проверки пользователя (Touch ID / пароль)
            {"origin": "https://evil.example"},
            {"rp": "evil.example"},
        ]
        for kw in cases:
            ch = vaults.device_challenge(OWNER, self.dev["id"])["challenge"]
            with self.assertRaises(OrgError, msg=str(kw)):
                vaults.unlock_device(OWNER, self.dev["id"], *self.sign(ch, **kw))
        with self.assertRaises(OrgError):  # подпись под другой вызов
            vaults.device_challenge(OWNER, self.dev["id"])
            vaults.unlock_device(OWNER, self.dev["id"], *self.sign("c3RhbGU"))
        with self.assertRaises(OrgError):  # чужой человек
            vaults.device_challenge(BACK, self.dev["id"])

    def test_counter_must_grow_and_device_counts_as_last(self) -> None:
        ch = vaults.device_challenge(OWNER, self.dev["id"])["challenge"]
        vaults.unlock_device(OWNER, self.dev["id"], *self.sign(ch, count=5))
        ch = vaults.device_challenge(OWNER, self.dev["id"])["challenge"]
        with self.assertRaises(OrgError):
            vaults.unlock_device(OWNER, self.dev["id"], *self.sign(ch, count=5))
        with self.assertRaises(OrgError):
            vaults.remove_unlocker(OWNER, self.dev["id"])

    def test_device_needs_valid_public_key(self) -> None:
        with self.assertRaises(OrgError):
            vaults.add_unlocker(OWNER, {"kind": "device", "credential_id": "eA", "salt": "cw", "wrapped_umk": "dw",
                                        "public_key": "bm9wZQ", "alg": -7, "rp_id": "getorbita.me"})


if __name__ == "__main__":
    unittest.main()
