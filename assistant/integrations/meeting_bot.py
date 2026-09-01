"""Клиент self-hosted Vexa meeting bot API."""

from __future__ import annotations

import os
from typing import Any

import requests

from assistant.lib.zoom_link import ZoomMeetingLink

REQUEST_TIMEOUT_SEC = 45


def enabled() -> bool:
    raw = os.getenv("MEETING_BOT_ENABLED", "0").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return False
    return bool(api_key())


def api_base() -> str:
    return (os.getenv("VEXA_API_BASE", "http://127.0.0.1:8056") or "").strip().rstrip("/")


def api_key() -> str:
    return (os.getenv("VEXA_API_KEY", "") or "").strip()


def bot_name() -> str:
    return (os.getenv("MEETING_BOT_NAME", "Leo") or "Leo").strip() or "Leo"


def join_early_minutes() -> int:
    try:
        return max(0, int(os.getenv("MEETING_BOT_JOIN_EARLY_MIN", "2") or "2"))
    except ValueError:
        return 2


def _headers() -> dict[str, str]:
    return {
        "X-API-Key": api_key(),
        "Content-Type": "application/json",
    }


def _raise_for_vexa(resp: requests.Response, action: str) -> None:
    if resp.ok:
        return
    try:
        body: Any = resp.json()
    except Exception:
        body = (resp.text or "")[:1200]
    raise RuntimeError(f"Vexa ({action}): HTTP {resp.status_code}: {body!r}")


def fetch_vexa_meeting(vexa_meeting_id: int) -> dict[str, Any] | None:
    """GET /bots/id/{meeting_id} — актуальный статус бота на встрече."""
    if not enabled():
        return None
    try:
        vid = int(vexa_meeting_id)
    except (TypeError, ValueError):
        return None
    url = f"{api_base()}/bots/id/{vid}"
    try:
        r = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT_SEC)
    except requests.RequestException as e:
        print(f"[meeting_bot] fetch_vexa_meeting err={e!r}")
        return None
    if r.status_code == 404:
        return None
    if not r.ok:
        print(f"[meeting_bot] fetch_vexa_meeting HTTP {r.status_code}")
        return None
    data = r.json()
    return data if isinstance(data, dict) else None


def stop_zoom_bot(native_meeting_id: str) -> None:
    """DELETE /bots/zoom/{native_meeting_id} — остановить зависшего бота."""
    if not enabled():
        return
    mid = (native_meeting_id or "").strip()
    if not mid:
        return
    url = f"{api_base()}/bots/zoom/{mid}"
    try:
        r = requests.delete(url, headers=_headers(), timeout=REQUEST_TIMEOUT_SEC)
    except requests.RequestException as e:
        print(f"[meeting_bot] stop_zoom_bot err={e!r}")
        return
    if r.status_code not in (200, 204, 404):
        print(f"[meeting_bot] stop_zoom_bot HTTP {r.status_code}")


def create_zoom_bot(
    link: ZoomMeetingLink,
    *,
    bot_name_override: str | None = None,
    telegram_user_id: int | None = None,
    zoom_obf_token: str | None = None,
) -> dict[str, Any]:
    """POST /bots — отправить бота на Zoom-встречу."""
    if not enabled():
        raise RuntimeError("Meeting bot отключён (MEETING_BOT_ENABLED=0).")
    payload: dict[str, Any] = {
        "platform": "zoom",
        "native_meeting_id": link.native_meeting_id,
        "recording_enabled": True,
        "transcribe_enabled": True,
        "transcription_tier": "deferred",
        "bot_name": (bot_name_override or bot_name()).strip() or "Leo",
    }
    if link.url:
        payload["meeting_url"] = link.url
    if link.passcode:
        payload["passcode"] = link.passcode
    if telegram_user_id is not None:
        from assistant.integrations import zoom_oauth

        obf = (zoom_obf_token or "").strip() or zoom_oauth.mint_obf_token(
            int(telegram_user_id), link.native_meeting_id
        )
        if obf:
            payload["zoom_obf_token"] = obf
    url = f"{api_base()}/bots"
    r = requests.post(
        url,
        headers=_headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    _raise_for_vexa(r, "create_zoom_bot")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Vexa: неожиданный ответ create_zoom_bot: {data!r}")
    return data if isinstance(data, dict) else None


def list_recordings(*, meeting_id: int | None = None) -> list[dict[str, Any]]:
    """GET /recordings — список записей (опционально по meeting_id)."""
    if not enabled():
        return []
    params: dict[str, Any] = {}
    if meeting_id is not None:
        params["meeting_id"] = int(meeting_id)
    url = f"{api_base()}/recordings"
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=REQUEST_TIMEOUT_SEC)
    except requests.RequestException as e:
        print(f"[meeting_bot] list_recordings err={e!r}")
        return []
    if not r.ok:
        print(f"[meeting_bot] list_recordings HTTP {r.status_code}")
        return []
    data = r.json()
    if isinstance(data, dict):
        recs = data.get("recordings")
        if isinstance(recs, list):
            return [x for x in recs if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def fetch_recording(recording_id: int | str) -> dict[str, Any]:
    rid = str(recording_id or "").strip()
    if not rid:
        raise RuntimeError("Vexa: пустой recording_id.")
    url = f"{api_base()}/recordings/{rid}"
    r = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT_SEC)
    _raise_for_vexa(r, "fetch_recording")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Vexa: неожиданный ответ fetch_recording: {data!r}")
    return data


def download_recording_media(recording_id: int | str, media_file_id: int | str) -> tuple[bytes, str]:
    """Скачать аудио/видео через /recordings/{id}/media/{media_id}/raw."""
    rid = str(recording_id or "").strip()
    mid = str(media_file_id or "").strip()
    if not rid or not mid:
        raise RuntimeError("Vexa: пустой recording_id или media_file_id.")
    url = f"{api_base()}/recordings/{rid}/media/{mid}/raw"
    r = requests.get(url, headers=_headers(), timeout=600)
    _raise_for_vexa(r, "download_recording_media")
    fmt = "wav"
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "mpeg" in ctype or "mp3" in ctype:
        fmt = "mp3"
    elif "mp4" in ctype:
        fmt = "mp4"
    return r.content, f"meeting.{fmt}"


def fetch_zoom_transcript(native_meeting_id: str) -> dict[str, Any] | None:
    """GET /transcripts/zoom/{native_meeting_id} — сегменты с именами участников."""
    if not enabled():
        return None
    mid = (native_meeting_id or "").strip()
    if not mid:
        return None
    url = f"{api_base()}/transcripts/zoom/{mid}"
    try:
        r = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT_SEC)
    except requests.RequestException as e:
        print(f"[meeting_bot] fetch_zoom_transcript err={e!r}")
        return None
    if r.status_code == 404:
        return None
    if not r.ok:
        print(f"[meeting_bot] fetch_zoom_transcript HTTP {r.status_code}")
        return None
    data = r.json()
    return data if isinstance(data, dict) else None


def pick_audio_media_file(recording: dict[str, Any]) -> dict[str, Any] | None:
    files = recording.get("media_files")
    if not isinstance(files, list):
        return None
    audio: list[dict[str, Any]] = []
    video: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        if kind == "audio":
            audio.append(item)
        elif kind == "video":
            video.append(item)
    if audio:
        return audio[0]
    if video:
        return video[0]
    return files[0] if files and isinstance(files[0], dict) else None
