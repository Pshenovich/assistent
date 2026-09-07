#!/usr/bin/env python3
"""Точка входа AI Executive Board (отдельный Telegram-бот)."""

from assistant.config import ROOT  # noqa: F401  — загружает .env
from assistant.board.bot.main import main

if __name__ == "__main__":
    main()
