"""Presence helpers for collaborative discussion notifications."""

from __future__ import annotations

from assistant.stores import discussion_presence as dp


def test_discussion_presence_online_offline():
    dp.heartbeat(1, "local", "42", 100, display_name="Ann")
    dp.heartbeat(1, "local", "42", 200, display_name="Bob")
    present = dp.present_user_ids(1, "local", "42")
    assert present == {"100", "200"}
    dp.leave(1, "local", "42", 100)
    assert dp.present_user_ids(1, "local", "42") == {"200"}
    dp.leave(1, "local", "42", 200)
    assert dp.present_user_ids(1, "local", "42") == set()
