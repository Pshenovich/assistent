"""Скачивание медиа из Telegram с учётом лимитов Bot API и локального сервера."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from urllib.parse import unquote

from telegram import Bot, File as TgFile
from telegram.error import BadRequest

from assistant.config import (
    URL_MAX_DOWNLOAD_BYTES,
    effective_telegram_download_limit_bytes,
    telegram_local_bot_api_enabled,
)

_DEFAULT_BOT_API_DATA_DIR = "/opt/assistant/data/telegram-bot-api"
_CONTAINER_BOT_API_DIR = "/var/lib/telegram-bot-api"
_GET_FILE_RETRIES = 4
_GET_FILE_RETRY_BASE_SEC = 0.2
_HOST_FILE_RETRIES = 5
_HOST_FILE_RETRY_BASE_SEC = 0.15


class MediaTooLargeError(ValueError):
    """Файл больше допустимого лимита скачивания."""

    def __init__(
        self,
        *,
        file_size: int | None,
        limit_bytes: int,
        message: str | None = None,
    ) -> None:
        self.file_size = file_size
        self.limit_bytes = limit_bytes
        super().__init__(message or format_media_too_large_message(file_size, limit_bytes))


def format_media_too_large_message(
    file_size: int | None,
    limit_bytes: int | None = None,
) -> str:
    limit = limit_bytes if limit_bytes is not None else effective_telegram_download_limit_bytes()
    lines: list[str] = []
    if file_size is not None and file_size > 0:
        lines.append(
            f"Файл ~{max(1, file_size // (1024 * 1024))} МБ — больше лимита "
            f"скачивания через бота ({max(1, limit // (1024 * 1024))} МБ)."
        )
    else:
        lines.append(
            f"Файл не скачивается: лимит Telegram для ботов — "
            f"{max(1, limit // (1024 * 1024))} МБ."
        )
    url_mb = max(1, URL_MAX_DOWNLOAD_BYTES // (1024 * 1024))
    lines.append(
        f"Пришлите прямую http(s)-ссылку на аудио/видео (до {url_mb} МБ) "
        "вместе с командой «транскрипция»."
    )
    if not telegram_local_bot_api_enabled():
        lines.append(
            "Лимит 20 МБ — ограничение облачного Bot API. "
            "На сервере можно подключить локальный telegram-bot-api (см. docs/local-bot-api.md)."
        )
    return "\n".join(lines)


def _bot_api_data_dir() -> Path:
    raw = (
        os.getenv("TELEGRAM_BOT_API_DATA_DIR", _DEFAULT_BOT_API_DATA_DIR) or _DEFAULT_BOT_API_DATA_DIR
    ).strip()
    return Path(raw)


def _remap_container_path(file_path: str) -> str:
    """Путь из getFile в контейнере → путь на хосте (bind mount)."""
    s = (file_path or "").strip().replace("\\", "/")
    if not s or "://" in s:
        return file_path or ""
    prefix = _CONTAINER_BOT_API_DIR
    if s.startswith(prefix + "/") or s == prefix:
        return str(_bot_api_data_dir()) + s[len(prefix) :]
    return s


def _relative_media_path(file_path: str, bot_token: str) -> str:
    """
    /var/lib/telegram-bot-api/<token>/videos/file_0.mp4 → videos/file_0.mp4
    Также разбирает URL локального Bot API и пути от PTB.
    """
    s = unquote(_remap_container_path(file_path)).replace("\\", "/")
    if not s:
        return ""
    if "/file/bot" in s:
        tail = s.split("/file/bot", 1)[1].lstrip("/")
        token_marker = f"/{bot_token}/"
        if token_marker in tail:
            return tail.split(token_marker, 1)[1].lstrip("/")
        if tail.startswith(bot_token + "/"):
            return tail[len(bot_token) + 1 :]
        if tail.startswith(bot_token):
            rest = tail[len(bot_token) :].lstrip("/")
            if rest.startswith(_CONTAINER_BOT_API_DIR.lstrip("/")) or rest.startswith(
                _CONTAINER_BOT_API_DIR
            ):
                return _relative_media_path(rest, bot_token)
            return rest
    token_marker = f"/{bot_token}/"
    if token_marker in s:
        return s.split(token_marker, 1)[1].lstrip("/")
    marker = "/telegram-bot-api/"
    if marker in s:
        tail = s.split(marker, 1)[1]
        if tail.startswith(bot_token + "/"):
            return tail[len(bot_token) + 1 :]
        if "/" in tail:
            return tail.split("/", 1)[1]
    if s.startswith(bot_token + "/"):
        return s[len(bot_token) + 1 :]
    for prefix in ("videos/", "documents/", "voice/", "audio/", "video_notes/"):
        idx = s.find(prefix)
        if idx >= 0:
            return s[idx:]
    if not s.startswith("/") and "://" not in s:
        return s.lstrip("/")
    return ""


def _host_path_candidates(file_path: str, bot_token: str) -> list[Path]:
    remapped = _remap_container_path(file_path)
    out: list[Path] = []
    if remapped and remapped != file_path:
        out.append(Path(remapped))
    rel = _relative_media_path(file_path, bot_token)
    if not rel:
        return out
    root = _bot_api_data_dir()
    out.extend(
        [
            root / bot_token / rel,
            root / bot_token / "temp" / Path(rel).name,
            root / "api-tmp" / bot_token / rel,
        ]
    )
    return out


def _host_path_for_local_api(file_path: str, bot_token: str) -> Path | None:
    for candidate in _host_path_candidates(file_path, bot_token):
        if candidate.is_file():
            return candidate
    return None


def _check_size_before_download(file_size: int | None) -> None:
    limit = effective_telegram_download_limit_bytes()
    if file_size is not None and file_size > limit:
        raise MediaTooLargeError(file_size=file_size, limit_bytes=limit)


def _read_path_sync(path: Path, *, limit: int) -> bytes:
    size = path.stat().st_size
    if size > limit:
        raise MediaTooLargeError(file_size=size, limit_bytes=limit)
    return path.read_bytes()


def _is_transient_file_error(exc: BaseException) -> bool:
    err = str(exc).lower()
    return (
        "wrong file_id" in err
        or "temporarily unavailable" in err
        or "file is not found" in err
    )


def _friendly_file_error(exc: BaseException) -> RuntimeError:
    err = str(exc).lower()
    if _is_transient_file_error(exc) or err == "not found" or "404" in err:
        return RuntimeError(
            "Голосовое ещё не готово на сервере Telegram. "
            "Подождите 2–3 секунды и отправьте снова, "
            "или ответьте «транскрипция» на уже отправленное голосовое."
        )
    return RuntimeError(str(exc))


async def _wait_host_path(file_path: str, bot_token: str) -> Path | None:
    for attempt in range(_HOST_FILE_RETRIES):
        host_path = _host_path_for_local_api(file_path, bot_token)
        if host_path is not None:
            return host_path
        if attempt + 1 < _HOST_FILE_RETRIES:
            await asyncio.sleep(_HOST_FILE_RETRY_BASE_SEC * (2**attempt))
    return None


async def _get_tg_file_with_retry(bot: Bot, file_id: str) -> TgFile:
    last: BaseException | None = None
    for attempt in range(_GET_FILE_RETRIES):
        try:
            return await bot.get_file(file_id)
        except BadRequest as e:
            last = e
            err = str(e).lower()
            if "too big" in err or "too large" in err or "file is too big" in err:
                raise
            if _is_transient_file_error(e) and attempt + 1 < _GET_FILE_RETRIES:
                await asyncio.sleep(_GET_FILE_RETRY_BASE_SEC * (2**attempt))
                continue
            raise _friendly_file_error(e) from e
        except Exception as e:
            last = e
            if attempt + 1 < _GET_FILE_RETRIES:
                await asyncio.sleep(_GET_FILE_RETRY_BASE_SEC * (2**attempt))
                continue
            raise
    if last:
        raise _friendly_file_error(last)
    raise RuntimeError("Не удалось получить файл из Telegram.")


async def _download_via_ptb_file(
    tg_file: TgFile,
    *,
    limit: int,
    bot_token: str = "",
) -> bytes:
    if bot_token and tg_file.file_path:
        host_path = _host_path_for_local_api(tg_file.file_path, bot_token)
        if host_path is not None:
            return await asyncio.to_thread(_read_path_sync, host_path, limit=limit)

    fd, tmp_path = tempfile.mkstemp(prefix="leo-tg-", suffix=".bin")
    os.close(fd)
    path = Path(tmp_path)
    try:
        out = await tg_file.download_to_drive(custom_path=str(path))
        read_from = Path(out) if out else path
        return await asyncio.to_thread(_read_path_sync, read_from, limit=limit)
    finally:
        path.unlink(missing_ok=True)


async def download_telegram_file(
    bot: Bot,
    file_id: str,
    *,
    file_size: int | None = None,
    filename_hint: str = "media.bin",
) -> tuple[bytes, str]:
    """
    Скачивает файл по file_id.
    Локальный Bot API: только чтение с диска (bind mount); HTTP в local mode не работает.
    """
    del filename_hint
    _check_size_before_download(file_size)
    limit = effective_telegram_download_limit_bytes()
    token = bot.token or ""

    tg_file = await _get_tg_file_with_retry(bot, file_id)
    raw_path = tg_file.file_path or ""
    default_name = _relative_media_path(raw_path, token).rsplit("/", 1)[-1] or "download.bin"

    if telegram_local_bot_api_enabled() and token:
        if not raw_path:
            raise RuntimeError("Telegram не вернул путь к файлу (getFile). Попробуйте ещё раз.")
        host_path = await _wait_host_path(raw_path, token)
        if host_path is not None:
            data = await asyncio.to_thread(_read_path_sync, host_path, limit=limit)
            return data, host_path.name
        raise RuntimeError(
            "Файл не найден на диске локального Bot API. "
            "Подождите 2–3 секунды и отправьте голосовое снова."
        )

    try:
        data = await _download_via_ptb_file(tg_file, limit=limit, bot_token=token)
    except Exception as e:
        try:
            data = bytes(await tg_file.download_as_bytearray())
        except Exception:
            raise _friendly_file_error(e) from e

    if len(data) > limit:
        raise MediaTooLargeError(file_size=len(data), limit_bytes=limit)
    return data, default_name
