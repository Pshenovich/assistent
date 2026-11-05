"""Первый просмотр календаря не шлёт пачку RSVP по уже существующим приглашениям."""

import asyncio
from types import SimpleNamespace

from assistant.bot import meeting_invite_job as job
from assistant.stores import meeting_invites_notified as notified


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kw):
        self.sent.append(kw)


def _run(monkeypatch, tmp_path, pending):
    monkeypatch.setenv("MEETING_INVITES_NOTIFIED_PATH", str(tmp_path / "n.json"))
    monkeypatch.setattr(job.inv, "invites_enabled", lambda: True)
    monkeypatch.setattr(job, "is_user_allowed", lambda uid, _u: True)
    monkeypatch.setattr(job.inv, "collect_pending_incoming_invites", lambda uid: list(pending))
    monkeypatch.setattr(job.inv, "prepare_incoming_invite", lambda uid, ev: ("t", "tok"))
    monkeypatch.setattr(job.inv, "build_invite_keyboard", lambda tok: None)
    monkeypatch.setattr(job.mr, "iter_calendar_user_ids", lambda: [42])

    async def for_each_user(users, fn, label=""):
        for u in users:
            await fn(u)

    monkeypatch.setattr(job.fanout, "for_each_user", for_each_user)
    bot = _Bot()
    asyncio.run(job.meeting_invite_tick(SimpleNamespace(bot=bot)))
    return bot


def test_first_tick_is_silent_then_only_new(monkeypatch, tmp_path):
    bot = _run(monkeypatch, tmp_path, [{"event_id": "a"}, {"event_id": "b"}])
    assert bot.sent == []
    assert notified.has_baseline(42)
    assert notified.was_notified(42, "a") and notified.was_notified(42, "b")
    bot = _run(monkeypatch, tmp_path, [{"event_id": "a"}, {"event_id": "b"}, {"event_id": "c"}])
    assert len(bot.sent) == 1
    assert notified.was_notified(42, "c")
