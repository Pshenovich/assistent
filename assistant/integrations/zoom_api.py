"""Zoom REST API (meetings) для user-level OAuth access_token."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

ZOOM_API_BASE = "https://api.zoom.us/v2"
REQUEST_TIMEOUT_SEC = 30


def _bearer_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {(access_token or '').strip()}",
        "Content-Type": "application/json",
    }


def _raise_for_zoom(resp: requests.Response, action: str) -> None:
    if resp.ok:
        return
    try:
        body: Any = resp.json()
    except Exception:
        body = (resp.text or "")[:1200]
    raise RuntimeError(f"Zoom API ({action}): HTTP {resp.status_code}: {body!r}")


def fetch_current_user(access_token: str) -> dict[str, Any]:
    """GET /users/me — id, email, first_name, last_name."""
    url = f"{ZOOM_API_BASE}/users/me"
    r = requests.get(
        url,
        headers=_bearer_headers(access_token),
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_zoom(r, "fetch_current_user")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Zoom: неожиданный ответ users/me: {data!r}")
    return data


def _meeting_settings(*, auto_record: bool = False) -> dict[str, Any]:
    """Настройки встречи, удобные для meeting bot (Leo заходит до хоста, без WR)."""
    del auto_record
    return {
        "waiting_room": False,
        "join_before_host": True,
    }


def _meeting_url(meeting_id: str) -> str:
    mid = quote(str(meeting_id or "").strip(), safe="")
    return f"{ZOOM_API_BASE}/meetings/{mid}"


def create_scheduled_meeting(
    access_token: str,
    *,
    topic: str,
    start_utc: datetime,
    end_utc: datetime,
    timezone_str: str,
    auto_record: bool = False,
) -> dict[str, Any]:
    """POST /users/me/meetings — type=2 (scheduled). Возвращает dict с id, join_url, start_url, uuid."""
    su = start_utc.astimezone(timezone.utc)
    eu = end_utc.astimezone(timezone.utc)
    duration_min = max(1, int(round((eu - su).total_seconds() / 60.0)))
    start_s = su.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "topic": (topic or "Встреча").strip() or "Встреча",
        "type": 2,
        "start_time": start_s,
        "duration": duration_min,
        "timezone": (timezone_str or "UTC").strip() or "UTC",
        "settings": _meeting_settings(auto_record=auto_record),
    }
    url = f"{ZOOM_API_BASE}/users/me/meetings"
    r = requests.post(
        url,
        headers=_bearer_headers(access_token),
        json=payload,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_zoom(r, "create_scheduled_meeting")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Zoom: неожиданный ответ create: {data!r}")
    return data


def update_scheduled_meeting(
    access_token: str,
    meeting_id: str,
    *,
    topic: str | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    timezone_str: str | None = None,
) -> None:
    """PATCH /meetings/{meetingId} для запланированной встречи."""
    mid = (meeting_id or "").strip()
    if not mid:
        raise RuntimeError("Zoom: пустой meeting_id.")
    body: dict[str, Any] = {}
    if topic is not None and str(topic).strip():
        body["topic"] = str(topic).strip()
    if start_utc is not None and end_utc is not None:
        su = start_utc.astimezone(timezone.utc)
        eu = end_utc.astimezone(timezone.utc)
        body["start_time"] = su.strftime("%Y-%m-%dT%H:%M:%SZ")
        body["duration"] = max(1, int(round((eu - su).total_seconds() / 60.0)))
    if timezone_str is not None and str(timezone_str).strip():
        body["timezone"] = str(timezone_str).strip()
    if not body:
        return
    url = _meeting_url(mid)
    r = requests.patch(
        url,
        headers=_bearer_headers(access_token),
        json=body,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_zoom(r, "update_scheduled_meeting")


def delete_meeting(access_token: str, meeting_id: str) -> None:
    """DELETE /meetings/{meetingId}."""
    mid = (meeting_id or "").strip()
    if not mid:
        return
    url = _meeting_url(mid)
    r = requests.delete(
        url,
        headers=_bearer_headers(access_token),
        timeout=REQUEST_TIMEOUT_SEC,
    )
    if r.status_code == 404:
        return
    _raise_for_zoom(r, "delete_meeting")


def list_upcoming_meetings(
    access_token: str,
    *,
    page_size: int = 30,
) -> list[dict[str, Any]]:
    """GET /users/me/meetings?type=upcoming."""
    url = f"{ZOOM_API_BASE}/users/me/meetings"
    r = requests.get(
        url,
        headers=_bearer_headers(access_token),
        params={"type": "upcoming", "page_size": max(1, min(page_size, 300))},
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_zoom(r, "list_upcoming_meetings")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Zoom: неожиданный ответ list: {data!r}")
    items = data.get("meetings") or []
    return [m for m in items if isinstance(m, dict)]


def create_instant_meeting(
    access_token: str,
    *,
    topic: str | None = None,
    auto_record: bool = False,
) -> dict[str, Any]:
    """POST /users/me/meetings — type=1 (instant)."""
    payload = {
        "topic": (topic or "Zoom").strip() or "Zoom",
        "type": 1,
        "settings": _meeting_settings(auto_record=auto_record),
    }
    url = f"{ZOOM_API_BASE}/users/me/meetings"
    r = requests.post(
        url,
        headers=_bearer_headers(access_token),
        json=payload,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_zoom(r, "create_instant_meeting")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Zoom: неожиданный ответ instant: {data!r}")
    return data
