"""После простоя бота повторяющееся напоминание приходит один раз и переносится в будущее, без пачки."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from assistant.bot import reminder_job
from assistant.config import CALENDAR_TZ
from assistant.skills import reminders as reminders_skill
from assistant.stores import reminders_store


def test_recurring_reminder_after_downtime_sent_once(tmp_path, monkeypatch):
    path = tmp_path / "reminders.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setenv("REMINDERS_STORE_PATH", str(path))
    tz = ZoneInfo(CALENDAR_TZ)
    now = datetime.now(tz)
    three_days_ago = (now - timedelta(days=3)).replace(microsecond=0)
    rid = reminders_store.add_reminder(user_id=1, chat_id=1, task="зарядка", when_iso=three_days_ago.isoformat())
    reminders_store.patch_reminder(rid, recurrence="FREQ=DAILY")

    sent: list[str] = []

    async def send_message(chat_id, text, reply_markup=None):
        sent.append(text)
        return SimpleNamespace(message_id=len(sent))

    context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message), job_queue=None)
    monkeypatch.setattr(reminders_skill, "schedule_reminder_job", lambda *a, **k: None)

    async def ticks():
        for _ in range(4):  # четыре минуты опроса после старта
            await reminder_job.deliver_missed_reminders(context)

    asyncio.run(ticks())
    assert len(sent) == 1
    rec = reminders_store.get_reminder_by_id(rid)
    assert reminders_store.parse_when_iso(rec["when_iso"], tz=tz) > now


def test_blocked_chat_reminder_closed_not_retried(tmp_path, monkeypatch):
    from telegram.error import Forbidden

    path = tmp_path / "reminders.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setenv("REMINDERS_STORE_PATH", str(path))
    tz = ZoneInfo(CALENDAR_TZ)
    past = (datetime.now(tz) - timedelta(hours=1)).replace(microsecond=0)
    rid = reminders_store.add_reminder(user_id=1, chat_id=1, task="t", when_iso=past.isoformat())
    calls: list[int] = []

    async def send_message(chat_id, text, reply_markup=None):
        calls.append(chat_id)
        raise Forbidden("bot was blocked by the user")

    context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message), job_queue=None)

    async def ticks():
        for _ in range(3):
            await reminder_job.deliver_missed_reminders(context)

    asyncio.run(ticks())
    assert calls == [1]
    assert reminders_store.get_reminder_by_id(rid)["done"]
