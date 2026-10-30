"""Предупреждение операторам о всплеске расхода ИИ у пользователя.

Раз в час: если кто-то за сегодня (UTC) потратил больше порога и заметно больше своего
обычного дня — одно сообщение операторам (TELEGRAM_ACCESS_APPROVER_IDS). Ничего не
ограничивает, только сообщает. Не чаще одного раза в день на пользователя.
"""

from __future__ import annotations

import asyncio
import html
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram.ext import ContextTypes

INTERVAL_SEC = 3600
_ALERTED: dict[str, str] = {}  # uid → дата UTC последнего предупреждения


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def find_spikes(today: str | None = None) -> list[dict[str, Any]]:
    """Пользователи со всплеском сегодня: [{uid, username, today_usd, avg_usd}]."""
    from assistant.lib import usage_store

    min_usd = _env_float("SPEND_ALERT_MIN_USD", 2.0)
    ratio = _env_float("SPEND_ALERT_RATIO", 4.0)
    day = today or datetime.now(timezone.utc).date().isoformat()
    d = datetime.fromisoformat(day).date()
    base_from = (d - timedelta(days=14)).isoformat()
    base_to = (d - timedelta(days=1)).isoformat()
    base = {r["telegram_user_id"]: float(r.get("cost_usd") or 0) for r in usage_store.users_summary(base_from, base_to)}
    out = []
    for r in usage_store.users_summary(day, day):
        uid = str(r.get("telegram_user_id") or "")
        spent = float(r.get("cost_usd") or 0)
        if not uid or uid == "__unknown__" or spent < min_usd:
            continue
        avg = base.get(uid, 0.0) / 14.0
        if avg > 0 and spent < avg * ratio:
            continue
        out.append({"uid": uid, "username": r.get("telegram_username") or "", "today_usd": spent, "avg_usd": avg})
    return out


def _message(s: dict[str, Any]) -> str:
    who = html.escape(("@" + s["username"]) if s["username"] else s["uid"])
    return (
        f"⚠️ Всплеск расхода ИИ: {who} — ${s['today_usd']:.2f} за сегодня "
        f"(обычно ~${s['avg_usd']:.2f} в день). Подробности — в админке, раздел «Расход»."
    )


def _tick_sync() -> int:
    from assistant.lib.telegram_access_allowlist import approver_user_ids
    from assistant.lib.telegram_notify import send_message

    day = datetime.now(timezone.utc).date().isoformat()
    sent = 0
    for s in find_spikes(day):
        if _ALERTED.get(s["uid"]) == day:
            continue
        _ALERTED[s["uid"]] = day
        for op in approver_user_ids():
            if send_message(op, _message(s)):
                sent += 1
        print(f"[spend_alert] uid={s['uid']} today=${s['today_usd']:.2f} avg=${s['avg_usd']:.2f}")
    return sent


async def spend_alert_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await asyncio.to_thread(_tick_sync)
    except Exception as e:
        print(f"[spend_alert] tick failed err={e!r}")


def register_spend_alert_jobs(app) -> None:
    if app.job_queue is None or os.getenv("SPEND_ALERT_ENABLED", "1").strip() in ("0", "false", "no", "off"):
        return
    app.job_queue.run_repeating(spend_alert_tick, interval=INTERVAL_SEC, first=300, name="spend_alert")
