"""Публичный URL мини-приложения (с cache-bust для Telegram WebView)."""

from __future__ import annotations

import os

# Меняйте при каждом релизе webapp — Telegram кэширует HTML по URL.
WEBAPP_BUILD_ID = "20260909-composer-pad"


def public_base_url() -> str:
    return (
        os.getenv("WEBAPP_PUBLIC_URL")
        or os.getenv("APP_URL")
        or "http://127.0.0.1:8080"
    ).rstrip("/")


def webapp_entry_url() -> str:
    build = (os.getenv("WEBAPP_BUILD_ID") or WEBAPP_BUILD_ID).strip()
    return f"{public_base_url()}/webapp/?b={build}"


def public_share_url(token: str) -> str:
    return f"{public_base_url()}/share/{token}"
