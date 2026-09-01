#!/usr/bin/env python3
"""Одноразовый вход в Telegram-аккаунт ассистента (создаёт session-файл)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Корень проекта в PYTHONPATH
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=True)

from assistant_telegram import api_hash, api_id, is_assistant_configured, session_path
from telethon import TelegramClient


async def main() -> None:
    if not is_assistant_configured():
        print(
            "Задайте в .env TELEGRAM_ASSISTANT_API_ID и TELEGRAM_ASSISTANT_API_HASH "
            "(https://my.telegram.org)."
        )
        raise SystemExit(1)
    aid = api_id()
    assert aid is not None
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(path), aid, api_hash())
    await client.start()
    me = await client.get_me()
    print(f"Вход выполнен: {me.first_name} (@{me.username})")
    print(f"Session: {path}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
