"""Публичный URL мини-приложения (с cache-bust для Telegram WebView)."""

from __future__ import annotations

import os
from urllib.parse import quote, urlencode


# Меняйте при каждом релизе webapp — Telegram кэширует HTML по URL.
WEBAPP_BUILD_ID = "20261017-pwa-open1"


def public_base_url() -> str:
    return (
        os.getenv("WEBAPP_PUBLIC_URL")
        or os.getenv("APP_URL")
        or "http://127.0.0.1:8080"
    ).rstrip("/")


def webapp_entry_url() -> str:
    build = (os.getenv("WEBAPP_BUILD_ID") or WEBAPP_BUILD_ID).strip()
    return f"{public_base_url()}/webapp/?b={build}"


def webapp_event_url(
    event_id: str,
    *,
    calendar_id: str = "primary",
    day: str | None = None,
) -> str:
    """Deep link на карточку встречи в мини-приложении / PWA."""
    eid = str(event_id or "").strip()
    if not eid:
        return webapp_entry_url()
    base = webapp_entry_url()
    sep = "&" if "?" in base else "?"
    cal = str(calendar_id or "primary").strip() or "primary"
    q = f"event={quote(eid, safe='')}&cal={quote(cal, safe='')}"
    d = str(day or "").strip()[:10]
    if d:
        q += f"&day={quote(d, safe='')}"
    return f"{base}{sep}{q}"


def webapp_launch_url(
    *,
    event_id: str = "",
    calendar_id: str = "primary",
    day: str | None = None,
    target: str | None = None,
) -> str:
    """Промежуточная страница: из Telegram пытается открыть установленный PWA.

    Обычный https из TG почти всегда остаётся во встроенном браузере.
    launch.html пробует protocol handler (web+leo://) и на Android — Chrome intent.
    """
    base = f"{public_base_url()}/webapp/launch.html"
    q: dict[str, str] = {}
    tgt = str(target or "").strip()
    if tgt:
        q["u"] = tgt
    else:
        eid = str(event_id or "").strip()
        if eid:
            q["event"] = eid
            q["cal"] = str(calendar_id or "primary").strip() or "primary"
            d = str(day or "").strip()[:10]
            if d:
                q["day"] = d
    return f"{base}?{urlencode(q)}" if q else base


def public_share_url(token: str) -> str:
    return f"{public_base_url()}/share/{token}"


def public_booking_url(token: str) -> str:
    return f"{public_base_url()}/book/{token}"
