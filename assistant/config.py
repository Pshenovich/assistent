"""Конфигурация из .env (корень репозитория)."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

DEFAULT_EVENT_TITLE = "Leo: Встреча без названия"
CALENDAR_TZ = os.getenv("CALENDAR_TZ", "Europe/Moscow").strip() or "Europe/Moscow"
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary"
OBUCHAT_TRANSCRIBE_BASE = os.getenv(
    "OBUCHAT_TRANSCRIBE_BASE", "https://assistant.obuchat.me/transcribe-api"
).strip()
WORK_HOURS_START = os.getenv("WORK_HOURS_START", "09:00").strip() or "09:00"
WORK_HOURS_END = os.getenv("WORK_HOURS_END", "18:00").strip() or "18:00"
SLOT_MIN_MINUTES = int(os.getenv("SLOT_MIN_MINUTES", "30") or "30")

def _mb_to_bytes(env_key: str, default_mb: int) -> int:
    return int(os.getenv(env_key, str(default_mb)) or default_mb) * 1024 * 1024


# Официальный лимит Bot API (облако api.telegram.org) — 20 МБ на скачивание.
TELEGRAM_CLOUD_BOT_FILE_LIMIT_BYTES = 20 * 1024 * 1024
# 20 МБ getFile + 50 МБ sendDocument — старые значения из .env, не для local Bot API.
_TELEGRAM_CLOUD_ERA_DOWNLOAD_BYTES = 50 * 1024 * 1024

TELEGRAM_BOT_MAX_DOWNLOAD_BYTES = _mb_to_bytes("TELEGRAM_BOT_MAX_DOWNLOAD_MB", 20)
TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_BYTES = _mb_to_bytes(
    "TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB", 500
)
URL_MAX_DOWNLOAD_BYTES = _mb_to_bytes("URL_MAX_DOWNLOAD_MB", 500)


def telegram_bot_api_base_url() -> str:
    return (os.getenv("TELEGRAM_BOT_API_BASE_URL", "") or "").strip().rstrip("/")


def telegram_local_bot_api_enabled() -> bool:
    """В .env задан локальный telegram-bot-api (снимает лимит 20 МБ)."""
    return bool(telegram_bot_api_base_url())


def telegram_bot_api_reachable(api_base: str | None = None, timeout: float = 0.6) -> bool:
    """TCP-проверка, что локальный Bot API слушает порт."""
    import socket
    from urllib.parse import urlparse

    raw = (api_base if api_base is not None else telegram_bot_api_base_url()).strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if (parsed.scheme or "").lower() == "https" else 80)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def effective_telegram_download_limit_bytes() -> int:
    """Фактический лимит скачивания медиа из Telegram для этого окружения."""
    url_cap = _mb_to_bytes("URL_MAX_DOWNLOAD_MB", 500)
    bot_cap = _mb_to_bytes("TELEGRAM_BOT_MAX_DOWNLOAD_MB", 20)
    if telegram_local_bot_api_enabled():
        local_cap = _mb_to_bytes("TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB", 500)
        # 20/50 МБ в .env — наследие облачного API, не режем local mode.
        if bot_cap <= _TELEGRAM_CLOUD_ERA_DOWNLOAD_BYTES:
            bot_cap = local_cap
        return min(bot_cap, local_cap, url_cap)
    return min(bot_cap, url_cap, TELEGRAM_CLOUD_BOT_FILE_LIMIT_BYTES)
ZOOM_MEETING_PROP_KEY = "leo_zoom_meeting_id"

ZOOM_AUTO_ATTACH_ON_CALENDAR_CREATE = os.getenv(
    "ZOOM_AUTO_ATTACH_ON_CALENDAR_CREATE", "1"
).strip().lower() not in ("0", "false", "no", "off")

TELEMOST_MEETING_PROP_KEY = "leo_telemost_conference_id"

TELEMOST_AUTO_ATTACH_ON_CALENDAR_CREATE = os.getenv(
    "TELEMOST_AUTO_ATTACH_ON_CALENDAR_CREATE", "1"
).strip().lower() not in ("0", "false", "no", "off")

TELEMOST_INSTANT_PHRASES: tuple[str, ...] = (
    "дай телемост",
    "создай телемост",
    "сделай телемост",
    "telemost ссылка",
    "дай ссылку на телемост",
    "ссылка на telemost",
    "создай telemost",
    "сделай telemost",
    "телемост",
    "telemost",
)

ZOOM_INSTANT_PHRASES: tuple[str, ...] = (
    "дай зум",
    "создай зум",
    "сделай зум",
    "zoom ссылка",
    "дай ссылку на зум",
    "ссылка на zoom",
    "создай zoom",
    "сделай zoom",
    "зум",
    "zoom",
)
NOTE_INTRO_PHRASES: tuple[str, ...] = (
    "добавь в заметки",
    "заметка",
)
NOTE_SEARCH_INTRO_RE = re.compile(
    r"^(?:найди|найти|ищи|покажи|поиск)\s+"
    r"(?:мне\s+)?(?:в\s+)?заметк\w*\s*"
    r"(?:про|о|об|по)?\s*(.+)$",
    re.IGNORECASE | re.UNICODE,
)
TRANSCRIBE_SEARCH_INTRO_RE = re.compile(
    r"^(?:найди|найти|ищи|покажи|поиск)\s+"
    r"(?:мне\s+)?(?:в\s+)?транскрип\w*\s*"
    r"(?:про|о|об|по)?\s*(.+)$",
    re.IGNORECASE | re.UNICODE,
)
TRANSCRIBE_INTRO_PHRASES: tuple[str, ...] = (
    "транскрипция",
    "транскрибируй",
    "транскриб",
    "расшифруй",
    "сделай транскрайб",
    "сделай транскрипт",
    "выведи текст",
)
SUMMARY_INTRO_PHRASES: tuple[str, ...] = (
    "саммари",
    "сделай саммари",
    "сделай summary",
    "summary",
    "протокол",
    "сделай протокол",
    "протокол встречи",
    "выжимка",
    "краткое содержание",
    "краткий пересказ",
    "пересказ",
    "сделай выжимку",
    "суммаризируй",
    "summarize",
)
SUMMARY_SEARCH_INTRO_RE = re.compile(
    r"^(?:найди|найти|ищи|покажи|поиск)\s+"
    r"(?:мне\s+)?(?:в\s+)?(?:саммари|саммари\s+встреч\w*|выжимк\w*|протокол\w*)\s*"
    r"(?:про|о|об|по)?\s*(.+)$"
    r"|^(?:что\s+решили|какие\s+решения)\s+(?:по|про|об|о)?\s*(.+)$"
    r"|^(?:какие\s+задачи)\s+(?:ставили|у|для|назначили)\s+(.+)$"
    r"|^(?:какие\s+задачи)\s+(?:по|про|об|о)\s+(.+)$"
    r"|^(?:покажи|найди)\s+(?:все\s+)?встреч\w*\s+про\s+(.+)$",
    re.IGNORECASE | re.UNICODE,
)
