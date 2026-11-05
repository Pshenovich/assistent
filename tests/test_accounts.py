"""Аккаунты по почте: коды подтверждения, синтетические id, привязка Telegram."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from assistant.stores import accounts
from assistant.stores import notes as notes_store
from assistant.stores.accounts import AccountError


class AccountsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"NOTES_DB_PATH": os.path.join(self._tmpdir.name, "n.sqlite")})
        self._env.start()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_code_roundtrip_hash_only_and_single_use(self) -> None:
        issued = accounts.issue_code(" Vova@Obuchat.me ", ip="1.2.3.4")
        self.assertEqual(issued["email"], "vova@obuchat.me")
        self.assertRegex(issued["code"], r"^\d{6}$")
        stored = accounts._conn().execute("SELECT code_hash FROM email_codes").fetchone()[0]
        self.assertNotIn(issued["code"], stored)
        self.assertEqual(accounts.verify_code("VOVA@obuchat.me", issued["code"]), "vova@obuchat.me")
        with self.assertRaises(AccountError):
            accounts.verify_code("vova@obuchat.me", issued["code"])

    def test_wrong_code_attempts_limited(self) -> None:
        issued = accounts.issue_code("a@b.io")
        wrong = "000000" if issued["code"] != "000000" else "111111"
        for _ in range(accounts.CODE_MAX_ATTEMPTS):
            with self.assertRaises(AccountError):
                accounts.verify_code("a@b.io", wrong)
        with self.assertRaises(AccountError) as ctx:
            accounts.verify_code("a@b.io", issued["code"])
        self.assertEqual(ctx.exception.code, 429)

    def test_expired_code(self) -> None:
        issued = accounts.issue_code("a@b.io")
        with mock.patch.object(accounts, "_now", lambda: issued["expires_at"] + 1):
            with self.assertRaises(AccountError):
                accounts.verify_code("a@b.io", issued["code"])

    def test_resend_and_rate_limits(self) -> None:
        accounts.issue_code("a@b.io")
        with self.assertRaises(AccountError) as ctx:
            accounts.issue_code("a@b.io")
        self.assertEqual(ctx.exception.code, 429)
        t = [accounts._now()]
        with mock.patch.object(accounts, "_now", lambda: t[0]):
            for _ in range(accounts.PER_EMAIL_HOUR - 1):
                t[0] += accounts.RESEND_GAP_SEC
                accounts.issue_code("a@b.io")
            t[0] += accounts.RESEND_GAP_SEC
            with self.assertRaises(AccountError):
                accounts.issue_code("a@b.io")
            for i in range(accounts.PER_IP_HOUR):
                accounts.issue_code(f"u{i}@b.io", ip="9.9.9.9")
            with self.assertRaises(AccountError):
                accounts.issue_code("other@b.io", ip="9.9.9.9")

    def test_only_latest_code_works(self) -> None:
        first = accounts.issue_code("a@b.io")
        with mock.patch.object(accounts, "_now", lambda: first["expires_at"] - 500):
            second = accounts.issue_code("a@b.io")
            if first["code"] != second["code"]:
                with self.assertRaises(AccountError):
                    accounts.verify_code("a@b.io", first["code"])
            self.assertEqual(accounts.verify_code("a@b.io", second["code"]), "a@b.io")

    def test_bad_email(self) -> None:
        with self.assertRaises(AccountError):
            accounts.issue_code("not-an-email")

    def test_synthetic_accounts_and_tg_mapping(self) -> None:
        acc = accounts.create_email_account("new@b.io")
        self.assertGreaterEqual(acc["user_id"], accounts.SYNTHETIC_BASE)
        self.assertLess(acc["user_id"], 2 ** 53)
        self.assertEqual(accounts.create_email_account("NEW@b.io")["user_id"], acc["user_id"])
        self.assertIsNone(accounts.tg_chat_for(acc["user_id"]))
        # старый аккаунт: id = Telegram id, без строки в accounts
        self.assertEqual(accounts.account_for_tg(777), 777)
        self.assertEqual(accounts.tg_chat_for(777), 777)
        accounts.link_tg(acc["user_id"], 555)
        self.assertEqual(accounts.account_for_tg(555), acc["user_id"])
        self.assertEqual(accounts.tg_chat_for(acc["user_id"]), 555)
        with self.assertRaises(AccountError):
            accounts.link_tg(accounts.create_email_account("x@b.io")["user_id"], 555)

    def test_attach_email_to_legacy(self) -> None:
        self.assertIsNone(accounts.email_of(777))
        acc = accounts.attach_email(777, "Old@B.io")
        self.assertEqual((acc["user_id"], acc["tg_user_id"], acc["email"]), (777, 777, "old@b.io"))
        self.assertEqual(accounts.email_of(777), "old@b.io")
        self.assertEqual(accounts.account_for_tg(777), 777)
        with self.assertRaises(AccountError) as ctx:
            accounts.attach_email(888, "old@b.io")
        self.assertEqual(ctx.exception.code, 409)
        accounts.attach_email(777, "new@b.io")
        self.assertEqual(accounts.email_of(777), "new@b.io")


if __name__ == "__main__":
    unittest.main()
