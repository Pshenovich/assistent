"""Per-user Bitrix24 MCP connection tokens (без OAuth)."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _tokens_dir() -> Path:
    raw = os.getenv("BITRIX_MCP_USER_TOKENS_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "bitrix_mcp_user_tokens"
    p.mkdir(parents=True, exist_ok=True)
    return p


def strip_bearer_prefix(token: str) -> str:
    value = (token or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def parse_token_payload(token: str) -> dict[str, object]:
    raw = strip_bearer_prefix(token)
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad))
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def is_token_expired(token: str, *, now: float | None = None) -> bool:
    payload = parse_token_payload(token)
    exp = payload.get("exp")
    if exp is None:
        return False
    try:
        return float(now if now is not None else time.time()) >= float(exp)
    except (TypeError, ValueError):
        return False


def user_token_path(telegram_user_id: int) -> Path:
    return _tokens_dir() / f"{int(telegram_user_id)}.json"


def is_connected(telegram_user_id: int) -> bool:
    return user_token_path(telegram_user_id).is_file()


def get_user_token(telegram_user_id: int) -> str | None:
    path = user_token_path(telegram_user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    token = strip_bearer_prefix(str(data.get("token") or ""))
    return token or None


def save_user_token(telegram_user_id: int, token: str) -> None:
    value = strip_bearer_prefix(token)
    if not value:
        raise ValueError("Пустой токен Bitrix24 MCP")
    path = user_token_path(telegram_user_id)
    path.write_text(
        json.dumps({"token": value, "saved_at": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def remove_user_token(telegram_user_id: int) -> bool:
    path = user_token_path(telegram_user_id)
    if not path.is_file():
        return False
    path.unlink()
    return True
