"""Страховочная сверка зеркала внешних календарей (stores/calendar_mirror).

Основной путь — подписка Google: правка в календаре → уведомление → этот календарь перечитывается
в зеркало за 1–2 секунды (calendar_push.sync_soon). Плюс любое чтение окна календаря (лента, Лео,
брифы) тоже записывает его. Эта сверка раз в 6 часов ловит то, что подписка пропустила (подписка
ещё не оформлена, календарь без подписок вроде праздников, уведомление не дошло), и при первом
проходе забирает историю за 13 недель, чтобы нагрузку можно было посчитать сразу за «12 недель».
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from telegram.ext import ContextTypes

from assistant.lib import fanout

INTERVAL_SEC = 6 * 3600
BACKFILL_DAYS = 91
RECHECK_DAYS = 14


def sync_user(uid: int, *, now: datetime | None = None) -> int:
    from assistant.services import calendar_sources
    from assistant.stores import calendar_mirror

    now = now or datetime.now(timezone.utc)
    have = calendar_mirror.synced_from(uid)
    back = RECHECK_DAYS if have and have <= now - timedelta(days=BACKFILL_DAYS - 1) else BACKFILL_DAYS
    events = calendar_sources.list_events_in_window(uid, now - timedelta(days=back), now + timedelta(days=RECHECK_DAYS))
    return len(events)


async def calendar_mirror_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.services import meeting_reminders as mr

    async def one(uid) -> None:
        try:
            await asyncio.to_thread(sync_user, int(uid))
        except Exception as e:
            print(f"[calendar_mirror] uid={uid} err={e!r}")

    users = await asyncio.to_thread(mr.iter_calendar_user_ids)
    await fanout.for_each_user(users, one, label="calendar_mirror", limit=3)


def register_calendar_mirror_jobs(app) -> None:
    from assistant.stores import calendar_mirror

    if app.job_queue is None or not calendar_mirror.enabled():
        return
    app.job_queue.run_repeating(calendar_mirror_tick, interval=INTERVAL_SEC, first=600, name="calendar_mirror")
    print(f"[calendar_mirror] sync every {INTERVAL_SEC}s")
