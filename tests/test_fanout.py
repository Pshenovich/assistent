"""Обход пользователей пачкой: все обработаны, параллельно не больше лимита, ошибка одного не мешает."""

from __future__ import annotations

import asyncio

from assistant.lib import fanout


def test_for_each_user_limit_and_errors():
    seen, active, peak = [], [0], [0]

    async def fn(uid):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        if uid == 3:
            raise RuntimeError("boom")
        seen.append(uid)

    asyncio.run(fanout.for_each_user(range(10), fn, label="t", limit=4))
    assert sorted(seen) == [0, 1, 2, 4, 5, 6, 7, 8, 9]
    assert peak[0] == 4


def test_meeting_reminder_tick_processes_all_users(monkeypatch):
    from types import SimpleNamespace

    from assistant.bot import meeting_reminder_job as job

    sent = []

    class Bot:
        async def send_message(self, chat_id, **kw):
            sent.append(chat_id)

    monkeypatch.setattr(job, "reminders_enabled", lambda: True)
    monkeypatch.setattr(job, "is_user_allowed", lambda uid, _: True)
    monkeypatch.setattr(job.user_prefs, "meeting_reminders_enabled", lambda uid: uid != 2)
    monkeypatch.setattr(job.mr, "iter_calendar_user_ids", lambda: [1, 2, 3])
    monkeypatch.setattr(job.mr, "collect_due_reminders",
                        lambda uid: [{"calendar_id": "c", "event_id": f"e{uid}", "start_iso": "s"}])
    monkeypatch.setattr(job.mr, "format_reminder_message", lambda ev, **kw: ("t", None))
    monkeypatch.setattr(job.mr, "web_push_payload_for_event", lambda *a, **kw: {})
    monkeypatch.setattr(job.sent_store, "mark_sent", lambda *a: None)
    import assistant.services.discussion_notify as dn
    monkeypatch.setattr(dn, "send_web_push", lambda *a: 0)
    asyncio.run(job.meeting_reminder_tick(SimpleNamespace(bot=Bot())))
    assert sorted(sent) == [1, 3]
