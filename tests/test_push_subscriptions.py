"""Web Push subscriptions store."""

from __future__ import annotations

import tempfile
from pathlib import Path

from assistant.stores import push_subscriptions as ps


def test_push_subscriptions_upsert_list_delete(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv(
            "PUSH_SUBSCRIPTIONS_DB_PATH",
            str(Path(td) / "push.sqlite"),
        )
        ps.reset_connection()
        try:
            row = ps.upsert(
                7,
                endpoint="https://push.example/a",
                p256dh="p256",
                auth="secret",
                user_agent="Mozilla",
            )
            assert row["user_id"] == "7"
            assert row["endpoint"] == "https://push.example/a"
            listed = ps.list_for_user(7)
            assert len(listed) == 1
            assert listed[0]["p256dh"] == "p256"
            again = ps.upsert(
                7,
                endpoint="https://push.example/a",
                p256dh="p256-new",
                auth="secret2",
            )
            assert again["p256dh"] == "p256-new"
            assert len(ps.list_for_user(7)) == 1
            assert ps.delete("https://push.example/a", user_id=7) is True
            assert ps.list_for_user(7) == []
            assert ps.delete("https://push.example/a", user_id=7) is False
        finally:
            ps.reset_connection()


def test_push_subscriptions_delete_other_user_ignored(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv(
            "PUSH_SUBSCRIPTIONS_DB_PATH",
            str(Path(td) / "push.sqlite"),
        )
        ps.reset_connection()
        try:
            ps.upsert(
                7,
                endpoint="https://push.example/b",
                p256dh="p",
                auth="a",
            )
            assert ps.delete("https://push.example/b", user_id=8) is False
            assert len(ps.list_for_user(7)) == 1
        finally:
            ps.reset_connection()
