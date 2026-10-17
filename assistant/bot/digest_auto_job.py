"""Автообновление дайджеста чатов и разбор «важного для вас» (раньше — только по кнопке).

Вчера — дособираем отчёты, которых нет; сегодня — обновляем не чаще раза в 3 часа и только
если в чатах появились новые сообщения после последнего отчёта.
"""

from __future__ import annotations

import asyncio
import os
import time

from telegram.ext import ContextTypes

from assistant.bot.access_gate import is_user_allowed

_LAST_TODAY: dict[int, float] = {}
TODAY_EVERY_SEC = 3 * 3600


def auto_enabled() -> bool:
    return os.getenv("CHAT_DIGEST_AUTO", "1").strip().lower() not in {"0", "false", "no", "off"}


def poll_interval_sec() -> float:
    try:
        return max(300.0, float(os.getenv("CHAT_DIGEST_AUTO_POLL_SEC", "1800") or "1800"))
    except ValueError:
        return 1800.0


def _needs(chat_ids: list[int], day: str) -> bool:
    """Есть сообщения за день, которых ещё нет в отчёте."""
    from assistant.stores import chat_digest as store

    for cid in chat_ids:
        msgs = store.messages_for_chat_date(int(cid), day)
        if not msgs:
            continue
        rep = store.get_report(day, int(cid))
        if not rep:
            return True
        if str(msgs[-1].get("ts_utc") or "") > str(rep.get("generated_at") or ""):
            return True
    return False


def refresh_user(uid: int) -> int:
    from assistant.services import chat_digest as svc
    from assistant.services import proactive_ai
    from assistant.stores import user_prefs

    chats = [int(c) for c in user_prefs.digest_chat_ids(uid)]
    if not chats or not svc.try_begin_refresh(uid):
        return 0
    updated = 0
    try:
        yesterday = svc.default_report_date()
        if _needs(chats, yesterday):
            updated += int(svc.run_digest_for_user(uid, report_date=yesterday).get("reports_updated") or 0)
        today = svc.digest_today()
        if time.time() - _LAST_TODAY.get(uid, 0) >= TODAY_EVERY_SEC and _needs(chats, today):
            _LAST_TODAY[uid] = time.time()
            updated += int(svc.run_digest_for_user(uid, report_date=today).get("reports_updated") or 0)
    finally:
        svc.end_refresh(uid)
    if updated:
        proactive_ai.run_pending(uid)
    return updated


async def digest_auto_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not auto_enabled():
        return
    from assistant.stores import user_prefs

    for uid in await asyncio.to_thread(user_prefs.iter_user_ids_with_digest):
        if not is_user_allowed(uid, None):
            continue
        try:
            n = await asyncio.to_thread(refresh_user, uid)
            if n:
                print(f"[digest_auto] uid={uid} reports={n}")
        except Exception as e:
            print(f"[digest_auto] uid={uid} err={e!r}")


def register_digest_auto_jobs(app) -> None:
    if not auto_enabled() or app.job_queue is None:
        return
    interval = poll_interval_sec()
    app.job_queue.run_repeating(digest_auto_tick, interval=interval, first=min(300.0, interval), name="digest_auto")
