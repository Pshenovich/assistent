"""Пересылка продуктовых событий из data/analytics.sqlite в PostHog.

Только с сервера (никакого скрипта в браузере): фоновый поток раз в
ANALYTICS_FORWARD_INTERVAL_SEC закрывает сессии, забирает неотправленные события
и шлёт пачкой в PostHog /batch/. Не получилось — события остаются у нас и уйдут
следующей попыткой.

Наружу уходит только хэш Telegram id (sha256 с солью), тип события, платформа и
свойства-числа/метки. Имён, юзернеймов и текстов нет.

Включение: ANALYTICS_FORWARD=posthog, POSTHOG_KEY (или POSTHOG_TOKEN), ANALYTICS_ID_SALT
(POSTHOG_HOST, по умолчанию https://eu.i.posthog.com).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

from assistant.stores import analytics_store

_thread: threading.Thread | None = None
_internal_cache: tuple[float, frozenset[str]] = (0.0, frozenset())


def api_key() -> str:
    """Ключ проекта PostHog (phc_…): POSTHOG_KEY или POSTHOG_TOKEN."""
    return (os.getenv("POSTHOG_KEY", "") or os.getenv("POSTHOG_TOKEN", "")).strip()


def enabled() -> bool:
    return (
        os.getenv("ANALYTICS_FORWARD", "off").strip().lower() == "posthog"
        and bool(api_key())
        and bool(os.getenv("ANALYTICS_ID_SALT", "").strip())
    )


def distinct_id(user_id: Any) -> str:
    salt = os.getenv("ANALYTICS_ID_SALT", "").strip()
    return hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:32]


def internal_user_ids() -> frozenset[str]:
    """Свои аккаунты (ANALYTICS_EXCLUDE_IDS) — в PostHog помечаем, в /dashboard исключаем."""
    global _internal_cache
    now = time.time()
    if now - _internal_cache[0] < 300:
        return _internal_cache[1]
    raw = os.getenv("ANALYTICS_EXCLUDE_IDS", "")
    ids = frozenset(x.strip() for x in raw.replace(";", ",").split(",") if x.strip())
    _internal_cache = (now, ids)
    return ids


def to_posthog(ev: dict[str, Any]) -> dict[str, Any]:
    uid = str(ev["user_id"])
    props: dict[str, Any] = dict(ev.get("props") or {})
    platform = str(ev.get("platform") or "")
    if platform:
        props["platform"] = platform
    if "session_id" in props:
        props["$session_id"] = props.pop("session_id")
    props["$set"] = {"internal": uid in internal_user_ids()}
    once: dict[str, Any] = {}
    if platform and platform != "server":
        once["first_platform"] = platform
    if ev["event"] == "bot.start" and props.get("ref"):
        once["first_ref"] = props["ref"]
    if once:
        props["$set_once"] = once
    props["$process_person_profile"] = True
    return {
        "event": ev["event"],
        "distinct_id": distinct_id(uid),
        "timestamp": datetime.fromtimestamp(float(ev["ts"]), tz=timezone.utc).isoformat(),
        "properties": props,
    }


def _post(batch: list[dict[str, Any]]) -> None:
    host = os.getenv("POSTHOG_HOST", "https://eu.i.posthog.com").strip().rstrip("/")
    payload = json.dumps({"api_key": api_key(), "batch": batch}).encode()
    req = urllib.request.Request(
        f"{host}/batch/", data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — адрес из конфигурации
        if resp.status >= 300:
            raise RuntimeError(f"posthog status={resp.status}")


def forward_once(post=_post) -> int:
    """Одна итерация: закрыть сессии, отправить накопленное. Возвращает число отправленных."""
    analytics_store.emit_closed_sessions()
    sent = 0
    while True:
        rows = analytics_store.pending_forward(500)
        if not rows:
            return sent
        post([to_posthog(r) for r in rows])
        analytics_store.mark_forwarded(r["id"] for r in rows)
        sent += len(rows)
        if len(rows) < 500:
            return sent


def _loop() -> None:
    interval = max(5.0, float(os.getenv("ANALYTICS_FORWARD_INTERVAL_SEC", "15") or 15))
    backoff = interval
    while True:
        time.sleep(backoff)
        try:
            if enabled():
                n = forward_once()
                if n:
                    print(f"[analytics] forwarded n={n}")
            else:
                analytics_store.emit_closed_sessions()
            backoff = interval
        except Exception as e:
            print(f"[analytics] forward_fail err={e!r}")
            backoff = min(600.0, backoff * 2)


def start() -> None:
    """Запустить фоновую пересылку (один процесс — веб-сервер)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="analytics-forward", daemon=True)
    _thread.start()
    print(f"[analytics] forward thread started enabled={enabled()}")
