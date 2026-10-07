"""Проверка Telegram WebApp initData (в т.ч. поле signature из Bot API 8+)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from assistant.lib.telegram_webapp_auth import parse_and_validate_init_data, user_payload_from_init_data


def _sign(fields: dict[str, str], bot_token: str) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def test_init_data_with_signature_field_validates():
    """Telegram кладёт signature рядом с hash; HMAC считается без signature."""
    bot_token = "123456:ABC-DEF"
    user = {"id": 42, "first_name": "Test", "username": "tester"}
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAE",
        "user": json.dumps(user, separators=(",", ":")),
        # Как в реальном initData — не должен ломать HMAC.
        "signature": "fake-ed25519-signature-value",
    }
    fields_for_hash = {k: v for k, v in fields.items() if k != "signature"}
    fields["hash"] = _sign(fields_for_hash, bot_token)
    init_data = urlencode(fields)

    parsed = parse_and_validate_init_data(init_data, bot_token=bot_token)
    assert "hash" not in parsed
    assert "signature" not in parsed
    out = user_payload_from_init_data(init_data, bot_token=bot_token)
    assert out["id"] == 42


def test_init_data_with_signature_included_in_hmac_also_validates():
    """Запасной режим: если клиент подписал вместе с signature."""
    bot_token = "123456:ABC-DEF"
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": 7, "first_name": "B"}),
        "signature": "sig-value",
    }
    fields["hash"] = _sign(fields, bot_token)
    out = user_payload_from_init_data(urlencode(fields), bot_token=bot_token)
    assert out["id"] == 7


def test_init_data_rejects_bad_hash():
    bot_token = "123456:ABC-DEF"
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": 1, "first_name": "A"}),
        "hash": "0" * 64,
    }
    with pytest.raises(ValueError, match="подпись"):
        parse_and_validate_init_data(urlencode(fields), bot_token=bot_token)


def test_init_data_cyrillic_user_name():
    bot_token = "123456:ABC-DEF"
    user = {"id": 99, "first_name": "Иван"}
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    fields["hash"] = _sign(fields, bot_token)
    out = user_payload_from_init_data(urlencode(fields), bot_token=bot_token)
    assert out["first_name"] == "Иван"
