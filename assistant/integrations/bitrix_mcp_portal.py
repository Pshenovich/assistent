"""Портал Bitrix24 из MCP-токена (JWT aud)."""

from __future__ import annotations

import base64
import json
import os
import re
from urllib.parse import urlparse


def portal_host_from_token(token: str) -> str:
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    parts = raw.split(".")
    if len(parts) >= 2:
        try:
            pad = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(pad))
            aud = str(payload.get("aud") or "").strip()
            if aud and "." in aud:
                return f"https://{aud}".rstrip("/")
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    webhook = (os.getenv("BITRIX24_WEBHOOK_URL") or "").strip()
    if webhook:
        host = urlparse(webhook).netloc
        if host:
            return f"https://{host}".rstrip("/")
    return "https://bitrix24.ru"


def absolutize_task_link(link: str, *, portal_host: str) -> str:
    value = (link or "").strip()
    base = portal_host.rstrip("/")
    if not value:
        return base
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/"):
        return f"{base}{value}"
    return f"{base}/{value.lstrip('/')}"


def task_url(*, portal_host: str, task_id: int | str, link: str = "", user_id: int | str = "") -> str:
    if link:
        return absolutize_task_link(link, portal_host=portal_host)
    tid = str(task_id).strip()
    uid = str(user_id).strip() or "0"
    return f"{portal_host.rstrip('/')}/company/personal/user/{uid}/tasks/task/view/{tid}/"


def fix_bitrix_links(text: str, *, portal_host: str) -> str:
    body = (text or "").strip()
    if not body:
        return body
    base = portal_host.rstrip("/")
    body = re.sub(r"https?://example\.com", base, body, flags=re.IGNORECASE)
    body = re.sub(
        r"\]\((/company/personal/[^)]+)\)",
        lambda m: f"]({base}{m.group(1)})",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"(?<!\])\((/company/personal/[^)\s]+)\)",
        lambda m: f"({base}{m.group(1)})",
        body,
        flags=re.IGNORECASE,
    )
    return body
