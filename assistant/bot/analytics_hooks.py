"""Аналитика бота: какое действие человек сделал в чате с ботом.

После регистрации всех обработчиков оборачиваем их колбэки: команда → `bot.command`
(name), кнопка → `bot.callback` (group — префикс callback_data), голос/файл/текст →
`bot.voice` / `bot.document` / `bot.message`. Навык, в который ушёл текст, пишет
nlu.dispatch (`bot.skill`).
"""

from __future__ import annotations

import functools
import re
from typing import Any, Callable

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler

from assistant.stores import analytics_store

_MESSAGE_EVENTS = {
    "handle_voice": "bot.voice",
    "handle_document": "bot.document",
    "handle_message": "bot.message",
}


def _callback_group(handler: CallbackQueryHandler) -> str:
    pattern = getattr(handler, "pattern", None)
    raw = getattr(pattern, "pattern", pattern)
    m = re.match(r"\^?\(?([a-z_]+)", str(raw or ""))
    return m.group(1) if m else "other"


def _describe(handler: Any) -> tuple[str, dict[str, Any]] | None:
    if isinstance(handler, CommandHandler):
        names = sorted(getattr(handler, "commands", ()) or ())
        return "bot.command", {"name": names[0] if names else "other"}
    if isinstance(handler, CallbackQueryHandler):
        return "bot.callback", {"group": _callback_group(handler)}
    if isinstance(handler, MessageHandler):
        name = getattr(handler.callback, "__name__", "")
        ev = _MESSAGE_EVENTS.get(name)
        return (ev, {}) if ev else None
    return None


def is_user_action(update: Update) -> bool:
    """Личный чат с ботом или нажатие кнопки — не сообщения людей в группах."""
    if update.callback_query is not None:
        return True
    chat = update.effective_chat
    return chat is not None and chat.type == "private"


def _wrap(callback: Callable, event: str, props: dict[str, Any]) -> Callable:
    @functools.wraps(callback)
    async def tracked(update: Any, context: Any) -> Any:
        try:
            user = getattr(update, "effective_user", None)
            if user is not None and isinstance(update, Update) and is_user_action(update):
                analytics_store.track(user.id, event, platform="tg_bot", **props)
        except Exception:
            pass
        return await callback(update, context)

    return tracked


def instrument(app: Application) -> int:
    """Оборачивает обработчики всех групп; возвращает число обёрнутых."""
    n = 0
    for handlers in app.handlers.values():
        for h in handlers:
            desc = _describe(h)
            if desc is None or getattr(h.callback, "_leo_tracked", False):
                continue
            h.callback = _wrap(h.callback, *desc)
            h.callback._leo_tracked = True  # type: ignore[attr-defined]
            n += 1
    return n
