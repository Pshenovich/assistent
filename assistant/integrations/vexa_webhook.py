"""Обработка Vexa webhooks (meeting.status_change, recording.completed)."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from assistant.integrations import meeting_bot
from assistant.services import meeting_record_pipeline as pipeline
from assistant.services.meeting_record_reconcile import (
    notify_job_failure,
    notify_job_human_help,
    should_notify_recording_failure,
)
from assistant.stores import meeting_recordings_store as mrs

_RECOVERABLE_REASONS = frozenset({"evicted", "removed_by_admin", "host_ended", "stopped"})


def webhook_secret() -> str:
    return (os.getenv("VEXA_WEBHOOK_SECRET", "") or "").strip()


def _auth_ok(authorization: str | None) -> bool:
    sec = webhook_secret()
    if not sec:
        return True
    auth = (authorization or "").strip()
    expected = f"Bearer {sec}"
    return auth == expected


def _nested_dict(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    for root in (payload, payload.get("data"), payload.get("payload")):
        if not isinstance(root, dict):
            continue
        val = root.get(key)
        if isinstance(val, dict):
            return val
    return None


def _extract_recording(payload: dict[str, Any]) -> dict[str, Any] | None:
    return _nested_dict(payload, "recording")


def _extract_meeting(payload: dict[str, Any]) -> dict[str, Any] | None:
    return _nested_dict(payload, "meeting")


def _find_job(payload: dict[str, Any]) -> dict[str, Any] | None:
    meeting = _extract_meeting(payload)
    native = ""
    if meeting:
        mid = meeting.get("id")
        if mid is not None:
            job = mrs.find_job_by_vexa_meeting_id(int(mid))
            if job:
                return job
        native = str(meeting.get("native_meeting_id") or "").strip()
    recording = _extract_recording(payload)
    if recording:
        meeting_id = recording.get("meeting_id")
        if meeting_id is not None:
            job = mrs.find_job_by_vexa_meeting_id(int(meeting_id))
            if job:
                return job
    if native:
        job = mrs.find_active_job_by_native_meeting_id(native)
        if job:
            return job
        return mrs.find_job_by_native_meeting_id(native)
    return None


def _completed_recording_for_job(job: dict[str, Any]) -> dict[str, Any] | None:
    vexa_mid = job.get("vexa_meeting_id")
    if vexa_mid is None:
        return None
    rec_id = job.get("vexa_recording_id")
    if rec_id is not None:
        try:
            rec = meeting_bot.fetch_recording(int(rec_id))
            if (
                isinstance(rec, dict)
                and str(rec.get("status") or "") == "completed"
                and meeting_bot.pick_audio_media_file(rec)
            ):
                return rec
        except Exception as e:
            print(f"[vexa_webhook] fetch_recording id={rec_id} err={e!r}")
    for rec in meeting_bot.list_recordings(meeting_id=int(vexa_mid)):
        if (
            str(rec.get("status") or "") == "completed"
            and meeting_bot.pick_audio_media_file(rec)
        ):
            return rec
    return None


def start_recording_pipeline_if_ready(job: dict[str, Any], recording: dict[str, Any] | None = None) -> bool:
    """Запустить Obuchat+саммари, если запись в Vexa готова."""
    job_id = int(job["id"])
    status = str(job.get("status") or "")
    if status in ("processing", "done"):
        return False
    rec = recording if isinstance(recording, dict) else _completed_recording_for_job(job)
    if not rec:
        return False
    rec_id = rec.get("id")
    if rec_id is not None:
        mrs.update_job(job_id, vexa_recording_id=int(rec_id))
    pipeline.process_job_recording_async(job_id, rec)
    return True


def _completion_reason(meeting: dict[str, Any] | None) -> str:
    if not meeting:
        return ""
    data = meeting.get("data")
    if isinstance(data, dict):
        return str(data.get("completion_reason") or "").strip().lower()
    return ""


def _handle_recording_completed(payload: dict[str, Any]) -> None:
    recording = _extract_recording(payload)
    if not isinstance(recording, dict):
        print(
            f"[vexa_webhook] recording.completed: no recording dict keys={list(payload.keys())!r}"
        )
        job = _find_job(payload)
        if job:
            start_recording_pipeline_if_ready(job)
        return
    job = _find_job(payload)
    if not job:
        print("[vexa_webhook] recording.completed: job not found")
        return
    start_recording_pipeline_if_ready(job, recording)


def _handle_meeting_completed(payload: dict[str, Any]) -> None:
    job = _find_job(payload)
    if not job:
        return
    if start_recording_pipeline_if_ready(job):
        return
    meeting = _extract_meeting(payload)
    reason = _completion_reason(meeting)
    if reason in _RECOVERABLE_REASONS:
        return
    job_id = int(job["id"])
    if str(job.get("status") or "") in ("processing", "done"):
        return
    mrs.update_job(job_id, status="failed", error_message=f"vexa:meeting.completed:{reason or 'unknown'}")
    notify_job_failure(job_id)


def _handle_meeting_status(payload: dict[str, Any]) -> None:
    meeting = _extract_meeting(payload)
    if not isinstance(meeting, dict):
        return
    status = str(meeting.get("status") or "").strip().lower()
    job = _find_job(payload)
    if not job:
        return
    job_id = int(job["id"])
    job_status = str(job.get("status") or "")
    topic = str(job.get("topic") or "Встреча Zoom")
    reason = _completion_reason(meeting)

    if status == "failed":
        if start_recording_pipeline_if_ready(job):
            return
        if reason in _RECOVERABLE_REASONS:
            mrs.update_job(
                job_id,
                status="failed",
                error_message=f"встреча завершилась ({reason})",
            )
            return
        mrs.update_job(
            job_id,
            status="failed",
            error_message=str((meeting.get("data") or {}).get("error") or reason or "meeting failed"),
        )
        notify_job_failure(job_id)
    elif status in ("completed", "ended"):
        _handle_meeting_completed(payload)
    elif status == "active":
        if job_status in ("joining", "scheduled"):
            mrs.update_job(job_id, status="recording")
    elif status in ("joining", "awaiting_admission"):
        if job_status == "scheduled":
            mrs.update_job(job_id, status="joining")
    elif status == "needs_human_help":
        if job_status in ("processing", "done"):
            return
        if job_status == "recording":
            meeting_full = meeting_bot.fetch_vexa_meeting(int(job.get("vexa_meeting_id") or 0))
            if meeting_full and meeting_full.get("start_time"):
                return
            mrs.update_job(job_id, status="joining")
        meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
        if meta.get("human_help_notified"):
            return
        meeting_full = meeting_bot.fetch_vexa_meeting(int(job.get("vexa_meeting_id") or 0))
        if not meeting_full or str(meeting_full.get("status") or "") != "needs_human_help":
            return
        esc = (meeting_full.get("data") or {}).get("escalation")
        esc_reason = ""
        if isinstance(esc, dict):
            esc_reason = str(esc.get("reason") or "").lower()
        if esc_reason.startswith("audio_join_failed") and meeting_full.get("start_time"):
            return
        if not should_notify_recording_failure(job):
            meta = dict(meta)
            meta["human_help_notified"] = True
            mrs.update_job(job_id, meta=meta)
            return
        notify_job_human_help(job_id, topic=topic, esc_reason=esc_reason)


def handle_webhook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event = str(payload.get("event_type") or payload.get("event") or "").strip()
    print(f"[vexa_webhook] event={event!r}")
    if event == "recording.completed":
        threading.Thread(
            target=_handle_recording_completed,
            args=(payload,),
            name="vexa-recording-completed",
            daemon=True,
        ).start()
        return {"ok": True}
    if event in ("meeting.status_change", "meeting.completed"):
        threading.Thread(
            target=_handle_meeting_status if event == "meeting.status_change" else _handle_meeting_completed,
            args=(payload,),
            name="vexa-meeting-status",
            daemon=True,
        ).start()
        return {"ok": True}
    return {"ok": True, "ignored": event or "unknown"}


def handle_webhook_request(
    body: bytes,
    *,
    authorization: str | None,
) -> tuple[int, dict[str, Any]]:
    if not _auth_ok(authorization):
        print("[vexa_webhook] invalid_auth")
        return 401, {"ok": False, "error": "invalid authorization"}
    try:
        payload = json.loads(body.decode("utf-8") if body else "{}")
    except json.JSONDecodeError:
        return 400, {"ok": False, "error": "invalid json"}
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "invalid payload"}
    out = handle_webhook_payload(payload)
    return 200, out
