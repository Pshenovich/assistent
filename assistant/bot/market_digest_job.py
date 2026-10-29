"""Выпуск «Рынок» в Telegram: тем, кто включил «Присылать мне выпуск», — раз в день в время выпуска организации."""

from __future__ import annotations

import asyncio
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from assistant.bot.access_gate import is_user_allowed
from assistant.config import ROOT

TICK_SEC = 600


def _issue_html() -> str:
    path = ROOT / "news" / "index.html"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


async def market_digest_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.lib.webapp_public import webapp_entry_url
    from assistant.services import market_settings
    from assistant.stores import user_prefs

    from assistant.services import market_news

    try:  # свои выпуски организаций — по одной, без параллели (сервер маленький)
        for oid in await asyncio.to_thread(market_news.due_orgs):
            await asyncio.to_thread(market_news.build_for_org, oid)
    except Exception as e:
        print(f"[market_news] build err={e!r}")
    try:
        uids = await asyncio.to_thread(market_settings.telegram_user_ids)
    except Exception as e:
        print(f"[market_tg] users err={e!r}")
        return
    if not uids:
        return
    shared = await asyncio.to_thread(_issue_html)
    for uid in uids:
        if not is_user_allowed(uid, None):
            continue
        try:
            own = await asyncio.to_thread(market_news.page_for_user, uid)
            html = own["html"] if own else shared
            text = await asyncio.to_thread(market_settings.due_message, uid, issue_html=html)
        except Exception as e:
            print(f"[market_tg] uid={uid} err={e!r}")
            continue
        if not text:
            continue
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть «Рынок»", web_app=WebAppInfo(url=webapp_entry_url()))]])
        try:
            await context.bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML, reply_markup=kb,
                                           disable_web_page_preview=True)
            day = datetime.now(user_prefs.get_user_tz(uid)).date().isoformat()
            await asyncio.to_thread(market_settings.mark_sent, uid, day)
            print(f"[market_tg] sent uid={uid}")
        except Exception as e:
            print(f"[market_tg] send uid={uid} err={e!r}")


def register_market_digest_jobs(app) -> None:
    if app.job_queue is None:
        return
    app.job_queue.run_repeating(market_digest_tick, interval=TICK_SEC, first=120.0, name="market_digest_tg")
