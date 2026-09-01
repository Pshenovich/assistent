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

TELEGRAM_BOT_MAX_DOWNLOAD_BYTES = _mb_to_bytes("TELEGRAM_BOT_MAX_DOWNLOAD_MB", 20)
TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_BYTES = _mb_to_bytes(
    "TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB", 500
)
URL_MAX_DOWNLOAD_BYTES = _mb_to_bytes("URL_MAX_DOWNLOAD_MB", 500)


def telegram_local_bot_api_enabled() -> bool:
    """Локальный telegram-bot-api на VPS снимает лимит 20 МБ."""
    return bool(os.getenv("TELEGRAM_BOT_API_BASE_URL", "").strip())


def effective_telegram_download_limit_bytes() -> int:
    """Фактический лимит скачивания медиа из Telegram для этого окружения."""
    caps = [
        _mb_to_bytes("TELEGRAM_BOT_MAX_DOWNLOAD_MB", 20),
        _mb_to_bytes("URL_MAX_DOWNLOAD_MB", 500),
    ]
    if telegram_local_bot_api_enabled():
        caps.append(_mb_to_bytes("TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB", 500))
    else:
        caps.append(TELEGRAM_CLOUD_BOT_FILE_LIMIT_BYTES)
    return min(caps)
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
