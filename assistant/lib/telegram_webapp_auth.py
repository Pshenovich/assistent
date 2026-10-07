"""Проверка Telegram Web App initData (HMAC-SHA256 по документации Telegram)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import parse_qsl, unquote


def _auth_max_age_sec() -> int:
    raw = os.getenv("TELEGRAM_WEBAPP_AUTH_MAX_AGE_SEC", "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return 86400


def _bot_tokens(*, primary: str | None = None) -> list[str]:
    """Основные и запасные токены: initData мог быть подписан другим ботом того же продукта."""
    seen: set[str] = set()
    out: list[str] = []

    def add(tok: str) -> None:
        t = (tok or "").strip().strip("'").strip('"')
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    add(primary or "")
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TG_DONATELLO_BOT_TOKEN",
        "TELEGRAM_WEBAPP_AUTH_EXTRA_TOKENS",
    ):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        parts = raw.split(",") if key.endswith("EXTRA_TOKENS") else [raw]
        for p in parts:
            add(p)
    return out


def _hmac_hex(data_check_string: str, *, bot_token: str) -> str:
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _init_data_variants(init_data: str) -> list[tuple[str, str]]:
    """Варианты строки: as-is и один decode, если фронт/прокси обернули encodeURIComponent."""
    raw = (init_data or "").strip()
    variants: list[tuple[str, str]] = [("raw", raw)]
    if not raw:
        return variants
    # Весь initData один раз percent-encoded: user%3D%7B...%26auth_date%3D...
    if "%3D" in raw[:80] or "%26" in raw[:120]:
        try:
            decoded = unquote(raw)
        except Exception:
            decoded = raw
        if decoded != raw:
            variants.append(("unquote", decoded))
    return variants


def parse_and_validate_init_data(init_data: str, *, bot_token: str | None = None) -> dict[str, str]:
    """Разбирает query-string initData, проверяет hash и свежесть auth_date.

    Возвращает словарь полей (строки), без hash/signature. Поле user — JSON-строка.
    """
    tokens = _bot_tokens(primary=bot_token)
    if not (init_data or "").strip() or not tokens:
        raise ValueError("Пустой initData или токен бота.")

    last_keys: list[str] = []
    for variant_name, variant in _init_data_variants(init_data):
        data = dict(parse_qsl(variant, keep_blank_values=True, strict_parsing=False))
        received_hash = data.pop("hash", None)
        if not received_hash:
            continue
        # Bot API 8+: signature (Ed25519) обычно не входит в HMAC; пробуем оба режима.
        signature = data.pop("signature", None)
        last_keys = sorted(list(data.keys()) + (["signature"] if signature else []))
        field_sets: list[tuple[bool, dict[str, str]]] = [(False, dict(data))]
        if signature is not None:
            with_sig = dict(data)
            with_sig["signature"] = signature
            field_sets.append((True, with_sig))

        for tok in tokens:
            for include_sig, check_fields in field_sets:
                data_check_string = "\n".join(
                    f"{k}={v}" for k, v in sorted(check_fields.items())
                )
                calculated = _hmac_hex(data_check_string, bot_token=tok)
                if not hmac.compare_digest(calculated, received_hash):
                    continue
                auth_raw = data.get("auth_date", "").strip()
                try:
                    auth_ts = int(auth_raw)
                except ValueError as e:
                    raise ValueError("Некорректное поле auth_date.") from e
                now = int(time.time())
                if auth_ts <= 0 or now - auth_ts > _auth_max_age_sec():
                    raise ValueError("Устаревший auth_date.")
                if variant_name != "raw" or include_sig or tok != tokens[0]:
                    print(
                        "[miniapp_auth] initData ok via",
                        f"variant={variant_name}",
                        f"include_signature={include_sig}",
                        f"token_id={tok.split(':', 1)[0]}",
                    )
                return dict(data)

    print(
        f"[miniapp_auth] initData hash mismatch keys={last_keys!r} "
        f"tokens={len(tokens)} len={len(init_data or '')}"
    )
    raise ValueError("Неверная подпись initData.")


def user_payload_from_init_data(init_data: str, *, bot_token: str | None = None) -> dict[str, Any]:
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


def telegram_user_id_from_init_data(init_data: str, *, bot_token: str | None = None) -> int:
    """Идентификатор пользователя только из провалидированного payload."""
    user = user_payload_from_init_data(init_data, bot_token=bot_token)
    uid = user.get("id")
    try:
        return int(uid)
    except (TypeError, ValueError) as e:
        raise ValueError("В user нет корректного id.") from e
