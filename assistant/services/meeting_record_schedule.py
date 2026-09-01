"""Планирование и отправка meeting bot на Zoom-встречи."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from assistant.integrations import meeting_bot
from assistant.lib import zoom_link
from assistant.lib.telegram_notify import send_user_message
from assistant.services import meeting_record_reconcile as reconcile
from assistant.stores import meeting_recordings_store as mrs
from assistant.stores import user_prefs


def service_available() -> bool:
    return meeting_bot.enabled()


def user_auto_record_enabled(user_id: int) -> bool:
    return user_prefs.zoom_auto_record_enabled(int(user_id))


def schedule_zoom_recording(
    user_id: int,
    *,
    meeting_url: str,
    topic: str,
    source: str,
    start_at: datetime | None = None,
    dispatch_immediately: bool = False,
) -> dict[str, Any] | None:
    """Создать задачу записи или подписать на уже идущую запись той же встречи."""
    if not service_available():
        return None
    link = zoom_link.parse_zoom_url(meeting_url)
    if link is None:
        raise RuntimeError("Не удалось разобрать Zoom-ссылку.")
    now = datetime.now(timezone.utc)
    start_utc = start_at.astimezone(timezone.utc) if start_at else None
    if dispatch_immediately or start_utc is None or start_utc <= now + timedelta(seconds=30):
        start_utc = start_utc or now

    job_id, joined_existing = mrs.create_or_join_job(
        telegram_user_id=int(user_id),
        source=source,
        topic=(topic or "Встреча Zoom").strip(),
        meeting_url=link.url,
        native_meeting_id=link.native_meeting_id,
        passcode=link.passcode,
        start_at_utc=start_utc,
    )
    job = mrs.get_job(job_id)
    if not job:
        return None

    if joined_existing:
        job = reconcile.reconcile_job(job)
        send_user_message(int(user_id), reconcile.existing_recording_message(job))
        return {**job, "_joined_existing": True}

    if dispatch_immediately or start_utc <= now + timedelta(seconds=30):
        dispatch_job(job_id)
        job = mrs.get_job(job_id)
    return job


def dispatch_job(job_id: int) -> None:
    job = mrs.get_job(job_id)
    if not job:
        return
    if str(job.get("status") or "") not in ("scheduled", "failed"):
        return
    native = str(job.get("native_meeting_id") or "").strip()
    other = mrs.find_active_job_by_native_meeting_id(native, exclude_job_id=job_id)
    if other and reconcile.dedupe_action(other) == "subscribe":
        uid = int(job["telegram_user_id"])
        mrs.add_subscriber(int(other["id"]), uid)
        mrs.update_job(job_id, status="merged", error_message="merged_into_existing")
        other = reconcile.reconcile_job(other)
        send_user_message(uid, reconcile.existing_recording_message(other))
        return

    uid = int(job["telegram_user_id"])
    link = zoom_link.ZoomMeetingLink(
        url=str(job.get("meeting_url") or ""),
        native_meeting_id=native,
        passcode=str(job.get("passcode") or "") or None,
    )
    try:
        meeting_bot.stop_zoom_bot(native)
        from assistant.services.meeting_record_reconcile import abandon_stale_joining_jobs_for_user

        abandon_stale_joining_jobs_for_user(uid, keep_job_id=job_id)
        mrs.update_job(job_id, status="joining")
        obf = None
        if uid:
            from assistant.integrations import zoom_oauth

            obf = zoom_oauth.mint_obf_token(uid, native)
            meta = dict(job.get("meta") or {})
            if obf:
                meta["obf_sent"] = True
                meta.pop("obf_missing", None)
            else:
                send_user_message(uid, zoom_oauth.obf_reauth_message(uid))
                meta["obf_missing"] = True
                meta.pop("obf_sent", None)
            mrs.update_job(job_id, meta=meta)
        created = meeting_bot.create_zoom_bot(
            link, telegram_user_id=uid, zoom_obf_token=obf
        )
        vexa_mid = created.get("id")
        mrs.update_job(
            job_id,
            status="joining",
            vexa_meeting_id=int(vexa_mid) if vexa_mid is not None else None,
        )
        topic = str(job.get("topic") or "Встреча Zoom")
        reconcile.update_job_status_messages(
            job_id,
            reconcile.dispatch_started_message(topic=topic),
            [uid],
        )
    except Exception as e:
        print(f"[meeting_record_schedule] dispatch job={job_id} err={e!r}")
        mrs.update_job(job_id, status="failed", error_message=str(e))
        send_user_message(uid, f"❌ Не удалось отправить бота на встречу: {e}")


def maybe_schedule_for_zoom_create(
    user_id: int,
    *,
    join_url: str,
    topic: str,
    start_at: datetime | None = None,
    source: str = "zoom_create",
) -> dict[str, Any] | None:
    if not service_available() or not user_auto_record_enabled(user_id):
        return None
    return schedule_zoom_recording(
        user_id,
        meeting_url=join_url,
        topic=topic,
        source=source,
        start_at=start_at,
        dispatch_immediately=start_at is None,
    )


def dispatch_due_jobs(*, now: datetime | None = None) -> int:
    if not service_available():
        return 0
    when = now or datetime.now(timezone.utc)
    early = when + timedelta(minutes=meeting_bot.join_early_minutes())
    due = mrs.list_due_scheduled(before_utc=early)
    n = 0
    for job in due:
        jid = int(job.get("id") or 0)
        if jid:
            dispatch_job(jid)
            n += 1
    return n
