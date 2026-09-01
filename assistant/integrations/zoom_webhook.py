"""Обработка Zoom Event Subscriptions (recording.completed, meeting.ended и др.)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from assistant.integrations import (
    zoom_local_notify,
    zoom_local_pending,
    zoom_oauth,
    zoom_recording_processor,
)


def webhook_secret() -> str:
    return (
        os.getenv("ZOOM_WEBHOOK_SECRET", "").strip()
        or os.getenv("ZOOM_WEBHOOK_SECRET_TOKEN", "").strip()
    )


def verify_signature(
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    *,
    secret: str | None = None,
) -> bool:
    sec = (secret or webhook_secret()).strip()
    if not sec or not signature or not timestamp:
        return False
    try:
        message = f"v0:{timestamp}:{body.decode('utf-8')}"
        digest = hmac.new(sec.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        expected = f"v0={digest}"
        return hmac.compare_digest(expected, signature.strip())
    except Exception:
        return False


def _encrypted_validation_token(plain_token: str, *, secret: str | None = None) -> str:
    sec = (secret or webhook_secret()).strip()
    return hmac.new(sec.encode("utf-8"), plain_token.encode("utf-8"), hashlib.sha256).hexdigest()


def _host_id_from_meeting_object(obj: dict[str, Any], inner: dict[str, Any]) -> str:
    host_id = str(obj.get("host_id") or "").strip()
    if not host_id:
        host_id = str(inner.get("account_id") or "").strip()
    return host_id


def _handle_meeting_ended(payload: dict[str, Any]) -> None:
    inner = payload.get("payload")
    if not isinstance(inner, dict):
        print("[zoom_webhook] meeting_ended: no payload dict")
        return
    obj = inner.get("object")
    if not isinstance(obj, dict):
        print("[zoom_webhook] meeting_ended: no object")
        return

    host_id = _host_id_from_meeting_object(obj, inner)
    tg_uid = zoom_oauth.find_telegram_user_by_zoom_host(host_id)
    if tg_uid is None:
        print(f"[zoom_webhook] meeting_ended no_telegram_user host_id={host_id!r}")
        return

    topic = str(obj.get("topic") or "Встреча Zoom").strip()
    meeting_id = str(obj.get("id") or obj.get("uuid") or "").strip()

    zoom_local_pending.set_pending(
        tg_uid,
        topic=topic,
        meeting_id=meeting_id,
        host_id=host_id,
    )
    print(
        f"[zoom_webhook] meeting_ended uid={tg_uid} host={host_id!r} "
        f"meeting={meeting_id!r} topic={topic!r}"
    )
    zoom_local_notify.notify_local_recording_expected(
        tg_uid, topic=topic, meeting_id=meeting_id
    )


def _handle_app_deauthorized(payload: dict[str, Any]) -> None:
    inner = payload.get("payload")
    if not isinstance(inner, dict):
        print("[zoom_webhook] app_deauthorized: no payload dict")
        return
    zoom_user_id = str(inner.get("user_id") or "").strip()
    client_id = str(inner.get("client_id") or "").strip()
    if not zoom_user_id:
        print("[zoom_webhook] app_deauthorized: missing user_id")
        return
    try:
        expected_cid = zoom_oauth.client_id()
    except Exception:
        expected_cid = ""
    if expected_cid and client_id and client_id != expected_cid:
        print(
            f"[zoom_webhook] app_deauthorized: client_id mismatch "
            f"got={client_id!r} expected={expected_cid!r}"
        )
        return
    purged = zoom_oauth.deauthorize_by_zoom_user_id(zoom_user_id)
    print(
        f"[zoom_webhook] app_deauthorized zoom_user_id={zoom_user_id!r} purged={purged!r}"
    )


def handle_webhook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event = str(payload.get("event") or "").strip()
    print(f"[zoom_webhook] event={event!r}")

    if event == "endpoint.url_validation":
        plain = str((payload.get("payload") or {}).get("plainToken") or "").strip()
        if not plain:
            return {"ok": False, "error": "missing plainToken"}
        return {
            "plainToken": plain,
            "encryptedToken": _encrypted_validation_token(plain),
        }

    if event in ("recording.completed", "recording.complete"):
        _handle_recording_completed(payload)
        return {"ok": True}

    if event == "meeting.ended":
        _handle_meeting_ended(payload)
        return {"ok": True}

    if event == "app_deauthorized":
        _handle_app_deauthorized(payload)
        return {"ok": True}

    return {"ok": True, "ignored": event or "unknown"}


def _handle_recording_completed(payload: dict[str, Any]) -> None:
    inner = payload.get("payload")
    if not isinstance(inner, dict):
        print("[zoom_webhook] recording_completed: no payload dict")
        return
    obj = inner.get("object")
    if not isinstance(obj, dict):
        print("[zoom_webhook] recording_completed: no object")
        return

    host_id = _host_id_from_meeting_object(obj, inner)
    tg_uid = zoom_oauth.find_telegram_user_by_zoom_host(host_id)
    if tg_uid is None:
        print(f"[zoom_webhook] no_telegram_user host_id={host_id!r}")
        return

    topic = str(obj.get("topic") or "Встреча Zoom").strip()
    meeting_id = str(obj.get("id") or obj.get("uuid") or "").strip()
    start_time = str(obj.get("start_time") or "").strip()
    download_token = str(
        inner.get("download_token") or obj.get("download_token") or ""
    ).strip()

    files_n = len(zoom_recording_processor._iter_recording_file_dicts(obj))
    print(
        f"[zoom_webhook] recording uid={tg_uid} host={host_id!r} "
        f"meeting={meeting_id!r} files={files_n} transcribe="
        f"{zoom_recording_processor.transcribe_enabled()!r}"
    )

    zoom_recording_processor.process_recording_async(
        telegram_user_id=tg_uid,
        topic=topic,
        meeting_id=meeting_id,
        host_id=host_id,
        start_time=start_time,
        download_token=download_token,
        recording_object=obj,
    )


def handle_webhook_request(
    body: bytes,
    *,
    signature: str | None,
    timestamp: str | None,
) -> tuple[int, dict[str, Any]]:
    try:
        payload = json.loads(body.decode("utf-8") if body else "{}")
    except json.JSONDecodeError:
        return 400, {"ok": False, "error": "invalid json"}
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "invalid payload"}

    event = str(payload.get("event") or "").strip()
    if event == "endpoint.url_validation":
        out = handle_webhook_payload(payload)
        return 200, out

    if not verify_signature(body, signature, timestamp):
        print("[zoom_webhook] invalid_signature")
        return 401, {"ok": False, "error": "invalid signature"}

    out = handle_webhook_payload(payload)
    return 200, out
