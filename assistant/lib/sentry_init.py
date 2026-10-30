"""Отчёты об ошибках в Sentry — только если в .env задан SENTRY_DSN и стоит sentry-sdk.

Без DSN или без пакета ничего не делает: ни импорта, ни сети.
"""

from __future__ import annotations

import os

_DONE = False


def init_sentry(service: str) -> bool:
    global _DONE
    if _DONE:
        return True
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        print("[sentry] SENTRY_DSN задан, но sentry-sdk не установлен — пропуск")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("SENTRY_ENVIRONMENT", "production").strip() or "production",
            server_name=service,
            # Тексты заметок и сообщений пользователей в Sentry не уходят.
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0") or 0),
        )
        sentry_sdk.set_tag("service", service)
    except Exception as e:
        print(f"[sentry] init failed err={e!r}")
        return False
    _DONE = True
    print(f"[sentry] on service={service}")
    return True


def capture_exception(err: BaseException | None) -> None:
    """Ошибку, которую код перехватил сам (например, обработчик ошибок бота), — в Sentry."""
    if not _DONE or err is None:
        return
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(err)
    except Exception as e:
        print(f"[sentry] capture failed err={e!r}")
