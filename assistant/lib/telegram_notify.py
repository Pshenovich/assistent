"""Синхронная отправка сообщений через Bot API (мини-приложение, без PTB)."""

from __future__ import annotations

import os
from typing import Any, Optional

import requests


def telegram_api_base(*, bot_token: str | None = None) -> str:
    api_base = os.getenv("TELEGRAM_BOT_API_BASE_URL", "").strip().rstrip("/")
    token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token:
        return ""
    if api_base:
        return f"{api_base}/bot{token}"
    return f"https://api.telegram.org/bot{token}"


def send_message(
    chat_id: int | str,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_markup: dict[str, Any] | None = None,
    disable_web_page_preview: bool = True,
    bot_token: str | None = None,
) -> bool:
    api = telegram_api_base(bot_token=bot_token)
    if not api:
        return False
    payload: dict[str, Any] = {
        "chat_id": int(chat_id),
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    proxy = os.getenv("TELEGRAM_PROXY_URL", "").strip() or None
    try:
        r = requests.post(
            f"{api}/sendMessage",
            json=payload,
            timeout=15,
            proxies={"http": proxy, "https": proxy} if proxy else None,
        )
        return bool(r.ok)
    except Exception as e:
        print(f"[telegram_notify] send chat={chat_id} err={e!r}")
        return False


def get_user_profile_photo_bytes(
    user_id: int, *, bot_token: str | None = None
) -> Optional[bytes]:
    api = telegram_api_base(bot_token=bot_token)
    if not api:
        return None
    proxy = os.getenv("TELEGRAM_PROXY_URL", "").strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.post(
            f"{api}/getUserProfilePhotos",
            json={"user_id": int(user_id), "limit": 1},
            timeout=15,
            proxies=proxies,
        )
        data = r.json() if r.content else {}
        photos = ((data.get("result") or {}).get("photos") or [])
        if not photos:
            return None
        sizes = photos[0] or []
        if not sizes:
            return None
        file_id = str((sizes[-1] or {}).get("file_id") or "").strip()
        if not file_id:
            return None
        fr = requests.post(
            f"{api}/getFile",
            json={"file_id": file_id},
            timeout=15,
            proxies=proxies,
        )
        file_path = str(((fr.json() or {}).get("result") or {}).get("file_path") or "")
        if not file_path:
            return None
        if file_path.startswith("http://") or file_path.startswith("https://"):
            url = file_path
        else:
            url = f"{api.replace('/bot', '/file/bot', 1)}/{file_path}"
            if "/file/bot" not in url:
                token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
                url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        img = requests.get(url, timeout=20, proxies=proxies)
        if img.ok and img.content:
            return img.content
    except Exception as e:
        print(f"[telegram_notify] photo uid={user_id} err={e!r}")
    return None
