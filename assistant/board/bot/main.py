"""Запуск Telegram-бота Executive Board (long polling)."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

from telegram.ext import Application

from assistant.board import store
from assistant.board.bot.followup_job import register_followup_jobs
from assistant.lib.telegram_bot_api import attach_local_bot_api
from assistant.board.bot.handlers import bind_service, register_handlers, setup_commands
from assistant.board.bot.publisher import TelegramPublisher
from assistant.board.meeting import MeetingService


def board_token() -> str:
    return (
        os.getenv("TG_DONATELLO_BOT_TOKEN", "").strip()
        or os.getenv("BOARD_BOT_TOKEN", "").strip()
    )


def _socks_supported() -> bool:
    try:
        import socks  # noqa: F401  # PySocks
        return True
    except ImportError:
        pass
    try:
        import httpx_socks  # noqa: F401
        return True
    except ImportError:
        return False


def _effective_proxy() -> str:
    raw = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    port = parsed.port
    if scheme.startswith("socks") and not _socks_supported():
        print(
            "[board] TELEGRAM_PROXY_URL=socks, но нет PySocks/httpx-socks — "
            "иду к Telegram напрямую. Поставьте: pip install 'python-telegram-bot[socks]'"
        )
        return ""
    if host in {"127.0.0.1", "localhost", "::1"} and port:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.4)
        try:
            sock.connect(("127.0.0.1", int(port)))
        except OSError:
            print(
                f"[board] TELEGRAM_PROXY_URL={host}:{port} недоступен — "
                "подключаюсь к Telegram напрямую"
            )
            return ""
        finally:
            sock.close()
    return raw


def build_application() -> Application:
    token = board_token()
    if not token:
        raise RuntimeError("TG_DONATELLO_BOT_TOKEN не задан в .env")
    store.init_db()
    proxy = _effective_proxy()
    builder = Application.builder().token(token)
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
        print(f"[board] telegram_proxy=configured")
    else:
        print("[board] telegram_proxy=none")
    builder = attach_local_bot_api(builder, log_prefix="board")
    builder = builder.concurrent_updates(8)

    async def _post_init(application: Application) -> None:
        try:
            me = await application.bot.get_me()
            print(f"[board] logged in as @{me.username} id={me.id}")
        except Exception as e:
            print(f"[board] getMe failed: {e!r}")
        try:
            await setup_commands(application)
        except Exception as e:
            print(f"[board] set_commands failed: {e}")

    builder = builder.post_init(_post_init)
    app = builder.build()
    publisher = TelegramPublisher(app)
    bind_service(MeetingService(publisher=publisher))
    register_handlers(app)
    register_followup_jobs(app)
    return app


def main() -> None:
    app = build_application()
    print("[board] polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
