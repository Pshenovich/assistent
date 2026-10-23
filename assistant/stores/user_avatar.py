"""Своя аватарка пользователя Orbita (вместо фото из Telegram).

Файл ``<uid>.avatar.jpg`` лежит рядом с настройками пользователя (``user_prefs``).
Картинку приводим к квадрату 512×512 JPEG, чтобы она весила немного и одинаково
выглядела в кружках по всему приложению.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path

from assistant.stores import user_prefs

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SIZE_PX = 512

_lock = threading.RLock()


class AvatarError(ValueError):
    """Картинку не удалось прочитать — текст ошибки показываем пользователю."""


def _path(user_id: int) -> Path:
    return user_prefs._dir() / f"{int(user_id)}.avatar.jpg"


def normalize_image(raw: bytes) -> bytes:
    if not raw:
        raise AvatarError("Файл пустой")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise AvatarError("Файл больше 10 МБ — выберите фото поменьше")
    try:
        from PIL import Image, ImageOps

        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise AvatarError("Не получилось открыть картинку — нужен JPEG, PNG или WebP") from e
    # Центральный квадрат: лицо обычно в середине кадра.
    side = min(im.size)
    left = (im.width - side) // 2
    top = (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side))
    if side > SIZE_PX:
        im = im.resize((SIZE_PX, SIZE_PX), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86, optimize=True)
    return buf.getvalue()


def save_avatar(user_id: int, raw: bytes) -> bytes:
    data = normalize_image(raw)
    path = _path(user_id)
    tmp = path.with_suffix(".jpg.tmp")
    with _lock:
        tmp.write_bytes(data)
        tmp.replace(path)
    return data


def get_avatar(user_id: int) -> bytes | None:
    path = _path(user_id)
    with _lock:
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None


def has_avatar(user_id: int) -> bool:
    return _path(user_id).is_file()


def delete_avatar(user_id: int) -> bool:
    path = _path(user_id)
    with _lock:
        if not path.is_file():
            return False
        path.unlink(missing_ok=True)
        return True
