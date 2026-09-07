"""Периодическая отправка follow-up по решениям с KPI."""

from __future__ import annotations

import asyncio
import os

from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from assistant.board import store
from assistant.board.followup import collect_due_followups
from assistant.board.renderer import format_followup


def followups_enabled() -> bool:
    raw = os.getenv("BOARD_FOLLOWUPS_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def poll_interval_sec() -> float:
    try:
        return max(30.0, float(os.getenv("BOARD_FOLLOWUP_POLL_SEC", "60") or "60"))
    except ValueError:
        return 60.0


async def followup_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not followups_enabled():
        return
    due = await asyncio.to_thread(collect_due_followups)
    for item in due:
        decision = store.get_decision(str(item.get("decision_id") or ""))
        if not decision:
            store.mark_followup_sent(str(item["id"]))
            continue
        chat_id = int(item.get("chat_id") or decision.get("chat_id") or 0)
        if not chat_id:
            continue
        kwargs = {}
        tid = item.get("thread_id")
        if tid:
            kwargs["message_thread_id"] = int(tid)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_followup(decision),
                parse_mode=ParseMode.HTML,
                **kwargs,
            )
            store.mark_followup_sent(str(item["id"]))
        except Exception as e:
            print(f"[board.followup] send_err id={item.get('id')} err={e!r}")


def register_followup_jobs(app: Application) -> None:
    jq = app.job_queue
    if jq is None:
        print("[board] JobQueue недоступен — follow-up не будут отправляться автоматически")
        return
    jq.run_repeating(followup_tick, interval=poll_interval_sec(), first=20)
