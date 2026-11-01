"""Тестовый вход для проверяющих Google и Zoom.

Постоянный код из .env (REVIEW_LOGIN_CODE) в поле «Войти по коду» открывает отдельный
пустой аккаунт REVIEW_USER_ID — без Telegram. Кода нет — входа нет.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

_DEFAULT_USER_ID = 900_000_000_001  # вне диапазона id Telegram


def review_user_id() -> int:
    try:
        return int(os.getenv("REVIEW_USER_ID", "") or _DEFAULT_USER_ID)
    except ValueError:
        return _DEFAULT_USER_ID


def _norm(raw: str | None) -> str:
    return str(raw or "").strip().upper().replace(" ", "").replace("-", "")


def review_code() -> str:
    return _norm(os.getenv("REVIEW_LOGIN_CODE"))


def is_review_user(user_id: int) -> bool:
    return bool(review_code()) and int(user_id) == review_user_id()


def match_code(raw: str | None) -> dict[str, Any] | None:
    """Профиль тестового аккаунта, если код совпал; иначе None."""
    want = review_code()
    if len(want) < 10 or not hmac.compare_digest(_norm(raw).encode(), want.encode()):
        return None
    return {"id": review_user_id(), "first_name": "Reviewer", "username": "orbita_reviewer"}
