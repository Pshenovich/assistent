"""Проверка Telegram Web App initData (HMAC-SHA256 по документации Telegram)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import parse_qsl


def _auth_max_age_sec() -> int:
    raw = os.getenv("TELEGRAM_WEBAPP_AUTH_MAX_AGE_SEC", "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return 86400


def parse_and_validate_init_data(init_data: str, *, bot_token: str) -> dict[str, str]:
    """Разбирает query-string initData, проверяет hash и свежесть auth_date.

    Возвращает словарь полей (строки), без hash. Поле user — JSON-строка как в initData.
    """
    init_data = (init_data or "").strip()
    token = (bot_token or "").strip()
    if not init_data or not token:
        raise ValueError("Пустой initData или токен бота.")

    # parse_qsl: пары key=value из query string; повторяющиеся ключи — берём последнее.
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    data: dict[str, str] = dict(pairs)

    received_hash = data.pop("hash", None)
    if not received_hash:
        raise ValueError("В initData нет поля hash.")

    # Цепочка проверки: все пары кроме hash, по алфавиту ключей, разделитель \n.
    check_pairs = sorted(data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in check_pairs)

    secret_key = hmac.new(
        b"WebAppData",
        token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("Неверная подпись initData.")

    auth_raw = data.get("auth_date", "").strip()
    try:
        auth_ts = int(auth_raw)
    except ValueError as e:
        raise ValueError("Некорректное поле auth_date.") from e
    now = int(time.time())
    if auth_ts <= 0 or now - auth_ts > _auth_max_age_sec():
        raise ValueError("Устаревший auth_date.")

    return data


def user_payload_from_init_data(init_data: str, *, bot_token: str) -> dict[str, Any]:
    """После проверки подписи возвращает объект user из поля user (JSON)."""
    fields = parse_and_validate_init_data(init_data, bot_token=bot_token)
    raw_user = fields.get("user")
    if not raw_user:
        raise ValueError("В initData нет поля user.")
    try:
        user = json.loads(raw_user)
    except json.JSONDecodeError as e:
        raise ValueError("Поле user не является JSON.") from e
    if not isinstance(user, dict):
        raise ValueError("Поле user должно быть объектом.")
    return user


def telegram_user_id_from_init_data(init_data: str, *, bot_token: str) -> int:
    """Идентификатор пользователя только из провалидированного payload."""
    user = user_payload_from_init_data(init_data, bot_token=bot_token)
    uid = user.get("id")
    try:
        return int(uid)
    except (TypeError, ValueError) as e:
        raise ValueError("В user нет корректного id.") from e
