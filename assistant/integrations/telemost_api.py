"""Yandex Telemost REST API (conferences) для user-level OAuth access_token."""

from __future__ import annotations

from typing import Any

import requests

TELEMOST_API_BASE = "https://cloud-api.yandex.net/v1/telemost-api"
REQUEST_TIMEOUT_SEC = 30

_ORG_RESTRICTED = "ApiRestrictedToOrganizations"


def _oauth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"OAuth {(access_token or '').strip()}",
        "Content-Type": "application/json",
    }


class TelemostApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


def is_org_restricted_error(exc: BaseException) -> bool:
    if isinstance(exc, TelemostApiError):
        return exc.error_code == _ORG_RESTRICTED or (
            exc.status_code == 403 and _ORG_RESTRICTED in str(exc)
        )
    return _ORG_RESTRICTED in str(exc)


def org_restricted_user_message() -> str:
    return (
        "Создать комнату через Leo можно только с корпоративным аккаунтом "
        "Яндекс 360 на домене организации (например, ivan@company.ru).\n\n"
        "Подписка Яндекс 360 на личном @yandex.ru даёт Телемост в браузере, "
        "но API для интеграций Яндекс не открывает — это их ограничение, не Leo.\n\n"
        "Что можно сделать:\n"
        "• Создайте встречу на https://telemost.yandex.ru и вставьте ссылку "
        "в календарь или пришлите Leo\n"
        "• Подключите корпоративную почту: /telemost_auth (аккаунт на домене компании)"
    )


def _parse_error_body(body: Any) -> str | None:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, str) and err.strip():
            return err.strip()
    return None


def _raise_for_telemost(resp: requests.Response, action: str) -> None:
    if resp.ok:
        return
    try:
        body: Any = resp.json()
    except Exception:
        body = (resp.text or "")[:1200]
    error_code = _parse_error_body(body) if isinstance(body, dict) else None
    msg = f"Telemost API ({action}): HTTP {resp.status_code}: {body!r}"
    if error_code == _ORG_RESTRICTED:
        msg = org_restricted_user_message()
    raise TelemostApiError(msg, status_code=resp.status_code, error_code=error_code)


def _conference_payload(*, auto_summarization: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"waiting_room_level": "PUBLIC"}
    if auto_summarization:
        payload["is_auto_summarization_enabled"] = True
    return payload


def create_conference(
    access_token: str,
    *,
    auto_summarization: bool = False,
) -> dict[str, Any]:
    """POST /conferences — мгновенная комната. Возвращает id, join_url."""
    url = f"{TELEMOST_API_BASE}/conferences"
    r = requests.post(
        url,
        headers=_oauth_headers(access_token),
        json=_conference_payload(auto_summarization=auto_summarization),
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_telemost(r, "create_conference")
    data = r.json()
    if not isinstance(data, dict):
        raise TelemostApiError(f"Telemost: неожиданный ответ create: {data!r}")
    return data


def get_conference(access_token: str, conference_id: str) -> dict[str, Any]:
    """GET /conferences/{id}."""
    cid = (conference_id or "").strip()
    if not cid:
        raise TelemostApiError("Telemost: пустой conference_id.")
    url = f"{TELEMOST_API_BASE}/conferences/{cid}"
    r = requests.get(
        url,
        headers=_oauth_headers(access_token),
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_telemost(r, "get_conference")
    data = r.json()
    if not isinstance(data, dict):
        raise TelemostApiError(f"Telemost: неожиданный ответ get: {data!r}")
    return data


def update_conference(
    access_token: str,
    conference_id: str,
    *,
    auto_summarization: bool | None = None,
    waiting_room_level: str | None = None,
) -> dict[str, Any]:
    """PATCH /conferences/{id}."""
    cid = (conference_id or "").strip()
    if not cid:
        raise TelemostApiError("Telemost: пустой conference_id.")
    body: dict[str, Any] = {}
    if auto_summarization is not None:
        body["is_auto_summarization_enabled"] = bool(auto_summarization)
    if waiting_room_level is not None and str(waiting_room_level).strip():
        body["waiting_room_level"] = str(waiting_room_level).strip()
    if not body:
        return get_conference(access_token, cid)
    url = f"{TELEMOST_API_BASE}/conferences/{cid}"
    r = requests.patch(
        url,
        headers=_oauth_headers(access_token),
        json=body,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_telemost(r, "update_conference")
    data = r.json()
    if not isinstance(data, dict):
        raise TelemostApiError(f"Telemost: неожиданный ответ update: {data!r}")
    return data


def delete_conference(access_token: str, conference_id: str) -> None:
    """DELETE /conferences/{id}."""
    cid = (conference_id or "").strip()
    if not cid:
        return
    url = f"{TELEMOST_API_BASE}/conferences/{cid}"
    r = requests.delete(
        url,
        headers=_oauth_headers(access_token),
        timeout=REQUEST_TIMEOUT_SEC,
    )
    if r.status_code == 404:
        return
    _raise_for_telemost(r, "delete_conference")
