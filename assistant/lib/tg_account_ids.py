"""Telegram id ↔ id аккаунта Орбиты на уровне HTTP к Bot API.

Код бота везде берёт ``update.effective_user.id`` как ключ данных пользователя и шлёт
сообщения на ``chat_id=user_id``. У старых аккаунтов id аккаунта = Telegram id, и здесь
ничего не меняется. У аккаунтов, заведённых по почте (синтетический id, см.
``stores/accounts.py``) и потом привязанных к Telegram, id разные — переводим в одном месте:

- входящие апдейты (getUpdates): ``from.id`` такого человека → id его аккаунта;
  ``chat.id`` не трогаем — ответы в чат идут как раньше;
- исходящие вызовы: ``chat_id`` / ``user_id`` ≥ SYNTHETIC_BASE → Telegram id аккаунта.
  Синтетический id не бывает настоящим чатом, так что подмена безопасна; Telegram
  не привязан — ответ «chat not found», как для заблокировавшего бота.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from telegram.request import HTTPXRequest, RequestData
from telegram.request._requestparameter import RequestParameter

_CACHE_TTL = 30.0
_LOCK = threading.Lock()
_IN: dict[int, tuple[int, float]] = {}
_OUT: dict[int, tuple[Optional[int], float]] = {}

_ID_PARAMS = ("chat_id", "user_id")
_NO_CHAT = (400, b'{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}')


def _synthetic_base() -> int:
    from assistant.stores.accounts import SYNTHETIC_BASE

    return SYNTHETIC_BASE


def account_for_tg(tg_id: int) -> int:
    now = time.monotonic()
    with _LOCK:
        hit = _IN.get(tg_id)
        if hit and now - hit[1] < _CACHE_TTL:
            return hit[0]
    try:
        from assistant.stores import accounts

        acc = accounts.account_for_tg(tg_id)
    except Exception as e:
        print(f"[tg_ids] account lookup failed tg={tg_id} err={e!r}")
        return tg_id
    with _LOCK:
        if len(_IN) > 10000:
            _IN.clear()
        _IN[tg_id] = (acc, now)
    return acc


def tg_chat_for(chat_id: int) -> Optional[int]:
    """Синтетический id → Telegram id (None — не привязан). Остальные id как есть."""
    if chat_id < _synthetic_base():
        return chat_id
    now = time.monotonic()
    with _LOCK:
        hit = _OUT.get(chat_id)
        if hit and now - hit[1] < _CACHE_TTL:
            return hit[0]
    try:
        from assistant.stores import accounts

        tg = accounts.tg_chat_for(chat_id)
    except Exception as e:
        print(f"[tg_ids] chat lookup failed account={chat_id} err={e!r}")
        tg = None
    with _LOCK:
        if len(_OUT) > 10000:
            _OUT.clear()
        _OUT[chat_id] = (tg, now)
    return tg


def forget(*ids: int) -> None:
    """Сбросить кэш после привязки Telegram к аккаунту."""
    with _LOCK:
        for i in ids:
            _IN.pop(int(i), None)
            _OUT.pop(int(i), None)


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.lstrip("-").isdigit():
        return int(v)
    return None


def outgoing_params(params: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Параметры вызова с переведёнными id; None — адресат без Telegram (не отправлять)."""
    out = params
    for key in _ID_PARAMS:
        n = _as_int(params.get(key))
        if n is None or n < _synthetic_base():
            continue
        tg = tg_chat_for(n)
        if tg is None:
            return None
        if out is params:
            out = dict(params)
        out[key] = tg
    return out


def _rewrite_users(node: Any) -> bool:
    """Во всех объектах ``from`` подменить Telegram id на id аккаунта. True — что-то поменяли."""
    changed = False
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "from" and isinstance(v, dict) and not v.get("is_bot"):
                tg = _as_int(v.get("id"))
                if tg and tg > 0:
                    acc = account_for_tg(tg)
                    if acc != tg:
                        v["id"] = acc
                        changed = True
            elif isinstance(v, (dict, list)):
                changed = _rewrite_users(v) or changed
    elif isinstance(node, list):
        for v in node:
            changed = _rewrite_users(v) or changed
    return changed


def incoming_updates(body: bytes) -> bytes:
    try:
        data = json.loads(body)
    except Exception:
        return body
    if not isinstance(data, dict) or not data.get("ok") or not isinstance(data.get("result"), list):
        return body
    return json.dumps(data).encode() if _rewrite_users(data["result"]) else body


class AccountMappingRequest(HTTPXRequest):
    """HTTPXRequest с переводом id (см. модуль)."""

    async def do_request(self, url: str, method: str, request_data: Optional[RequestData] = None, **kw: Any):
        if request_data is not None:
            params = request_data.parameters
            if any(k in params for k in _ID_PARAMS):
                mapped = outgoing_params(params)
                if mapped is None:
                    return _NO_CHAT
                if mapped is not params:
                    request_data = RequestData(
                        [p if p.name not in _ID_PARAMS else RequestParameter.from_input(p.name, mapped[p.name])
                         for p in request_data._parameters]  # type: ignore[attr-defined]
                    )
        status, body = await super().do_request(url, method, request_data, **kw)
        if status == 200 and url.endswith("/getUpdates"):
            body = incoming_updates(body)
        return status, body


def post(url: str, **kwargs: Any):
    """``requests.post`` к Bot API с тем же переводом id (уведомления мимо python-telegram-bot)."""
    import requests

    for field in ("json", "data"):
        params = kwargs.get(field)
        if isinstance(params, dict) and any(k in params for k in _ID_PARAMS):
            mapped = outgoing_params(params)
            if mapped is None:
                resp = requests.Response()
                resp.status_code, resp._content = _NO_CHAT  # type: ignore[attr-defined]
                resp.url = url
                return resp
            if field == "data" and mapped is not params:
                mapped = {k: (str(v) if k in _ID_PARAMS else v) for k, v in mapped.items()}
            kwargs[field] = mapped
    return requests.post(url, **kwargs)
