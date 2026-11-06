"""Подключение python-telegram-bot к локальному telegram-bot-api."""

from __future__ import annotations

from telegram import Bot
from telegram.ext import ApplicationBuilder

from assistant.config import (
    telegram_bot_api_base_url,
    telegram_bot_api_is_loopback,
    telegram_bot_api_reachable,
)


def attach_local_bot_api(builder: ApplicationBuilder, *, log_prefix: str) -> ApplicationBuilder:
    """
    Если TELEGRAM_BOT_API_BASE_URL задан и порт жив — local_mode для файлов >20 МБ.
    Мёртвый локальный API не должен валить polling: тогда остаёмся на api.telegram.org.
    """
    api_base = telegram_bot_api_base_url()
    if not api_base:
        return builder
    if not telegram_bot_api_is_loopback(api_base):
        # Зеркало api.telegram.org вне РФ (из российского ДЦ Telegram режут): тот же API, облачные лимиты.
        from assistant.lib.tg_api_failover import api_bases

        spare = ", ".join(api_bases()[1:]) or "нет"
        print(f"[{log_prefix}] Bot API через зеркало: {api_base} (запасное: {spare})")
        return builder.base_url(f"{api_base}/bot").base_file_url(f"{api_base}/file/bot")
    if not telegram_bot_api_reachable(api_base):
        print(
            f"[{log_prefix}] local Bot API {api_base} недоступен — "
            "работаю через api.telegram.org (лимит скачивания 20 МБ)"
        )
        return builder
    print(f"[{log_prefix}] local Bot API: {api_base}")
    return (
        builder.base_url(f"{api_base}/bot")
        .base_file_url(f"{api_base}/file/bot")
        .local_mode(True)
    )


def make_bot(token: str) -> Bot:
    """Bot для разовых отправок вне Application — через тот же Bot API, что и бот
    (зеркало или локальный сервер). Голый Bot(token) идёт на api.telegram.org, который
    из российского ДЦ режут: каждая отправка висела на таймаутах соединения."""
    from assistant.lib.tg_account_ids import AccountMappingRequest

    req = AccountMappingRequest()
    api_base = telegram_bot_api_base_url()
    if not api_base:
        return Bot(token, request=req)
    if not telegram_bot_api_is_loopback(api_base):
        return Bot(token, base_url=f"{api_base}/bot", base_file_url=f"{api_base}/file/bot", request=req)
    if not telegram_bot_api_reachable(api_base):
        return Bot(token, request=req)
    return Bot(token, base_url=f"{api_base}/bot", base_file_url=f"{api_base}/file/bot", local_mode=True, request=req)
