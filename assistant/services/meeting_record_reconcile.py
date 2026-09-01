"""Сверка задач записи со статусом бота в Vexa."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

from assistant.integrations import meeting_bot
from assistant.stores import meeting_recordings_store as mrs

DedupeAction = Literal["subscribe", "create_new"]

_VEXA_LIVE = frozenset({"active", "joining", "awaiting_admission"})
_VEXA_STUCK = frozenset({"needs_human_help"})
_VEXA_TERMINAL = frozenset({"failed", "completed", "ended"})

_STUCK_RETRY_MINUTES = 15
_ORPHAN_JOINING_MINUTES = 15
_STATUS_POLL_SEC = 25


def _parse_iso(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _vexa_meeting_for_job(job: dict[str, Any]) -> dict[str, Any] | None:
    vexa_id = job.get("vexa_meeting_id")
    if vexa_id is None:
        return None
    return meeting_bot.fetch_vexa_meeting(int(vexa_id))


def _participants_count(meeting: dict[str, Any] | None) -> int:
    if not meeting:
        return 0
    data = meeting.get("data")
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("participants_count") or 0)
    except (TypeError, ValueError):
        return 0


def _escalation_reason(meeting: dict[str, Any] | None) -> str:
    if not meeting:
        return ""
    data = meeting.get("data")
    if not isinstance(data, dict):
        return ""
    esc = data.get("escalation")
    if isinstance(esc, dict):
        return str(esc.get("reason") or "").strip().lower()
    return ""


def is_join_blocked(meeting: dict[str, Any] | None) -> bool:
    """Leo не дошёл до Zoom: нет в waiting room и нет среди участников."""
    if not meeting:
        return False
    vs = str(meeting.get("status") or "").strip().lower()
    if vs == "needs_human_help":
        if meeting.get("start_time"):
            return False
        if _escalation_reason(meeting) == "unknown_blocking_state":
            return True
        if is_waiting_room(meeting):
            return False
    if vs in _VEXA_STUCK:
        return True
    if vs in _VEXA_LIVE and _participants_count(meeting) == 0:
        data = meeting.get("data") if isinstance(meeting.get("data"), dict) else {}
        transitions = data.get("status_transition") if isinstance(data.get("status_transition"), list) else []
        for tr in transitions:
            if isinstance(tr, dict) and str(tr.get("to") or "") == "needs_human_help":
                return True
    return False


def is_waiting_room(meeting: dict[str, Any] | None) -> bool:
    if not meeting:
        return False
    vs = str(meeting.get("status") or "").strip().lower()
    if vs == "awaiting_admission":
        return True
    if vs == "needs_human_help":
        reason = _escalation_reason(meeting)
        if reason in ("waiting_room", "host_admission_required") and not meeting.get(
            "start_time"
        ):
            return True
    return False


def _job_meta(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {}
    raw = job.get("meta_json") or job.get("meta")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            import json

            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def join_blocked_message(
    *, topic: str = "Встреча Zoom", job: dict[str, Any] | None = None
) -> str:
    meta = _job_meta(job)
    obf_missing = bool(meta.get("obf_missing"))
    obf_sent = bool(meta.get("obf_sent"))
    zoom_configured = obf_sent or (job is not None and not obf_missing)
    lines = [
        f"❌ Leo не смог войти на встречу «{topic}».",
        "В Zoom его нет — ни среди участников, ни в waiting room.",
        "",
    ]
    if obf_missing:
        lines.extend(
            [
                "По логам бот застрял на входе: без OBF Zoom пускает гостя **Leo** "
                "только через веб-форму (reCAPTCHA с IP сервера, типично для us04web).",
                "",
                "Zoom API не подключён или нет scope **user:read:token** — выполните /zoom_auth.",
                "После успешного OAuth снова отправьте ссылку, пока вы в зале как хост.",
            ]
        )
    elif zoom_configured:
        lines.extend(
            [
                "Zoom подключён (/zoom_auth), OBF-токен отправлен — Leo входил через "
                "Meeting SDK, не как анонимный гость. Zoom всё равно отклонил подключение.",
                "",
                "Чаще всего помогает:",
                "• начать встречу как хост и снова отправить ссылку;",
                "• убедиться, что встреча в том же Zoom-аккаунте, что подключён в боте;",
                "• подождать, пока хост уже в зале (для us04web иногда критично).",
                "",
                "Или пришлите аудио/файл записи Zoom после встречи — расшифрую.",
            ]
        )
    else:
        lines.extend(
            [
                "По логам бот застрял на входе: Zoom показывает reCAPTCHA и не пускает "
                "гостя **Leo** с IP сервера (типично для us04web).",
                "",
                "Подключите Zoom через /zoom_auth (нужен scope user:read:token) "
                "и отправьте ссылку снова, когда вы в зале как хост.",
                "Или пришлите аудио/файл записи Zoom после встречи — расшифрую.",
            ]
        )
    return "\n".join(lines)


def waiting_room_instructions(*, topic: str = "Встреча Zoom") -> str:
    return (
        f"⏳ Leo ждёт допуска на встречу «{topic}» (waiting room).\n\n"
        "Откройте Zoom → Participants → Admit для участника **Leo**.\n"
        "Если Leo нет в списке ожидания — подождите ещё минуту или отправьте ссылку снова."
    )


def status_message_for_job(job: dict[str, Any], meeting: dict[str, Any] | None = None) -> str | None:
    """Сообщение о текущем статусе подключения Leo."""
    topic = str(job.get("topic") or "Встреча Zoom")
    meeting = meeting or _vexa_meeting_for_job(job)
    if not meeting:
        return None
    vs = str(meeting.get("status") or "").strip().lower()
    if vs == "active":
        return f"✅ Leo на встрече «{topic}» и записывает."
    if is_join_blocked(meeting):
        return join_blocked_message(topic=topic, job=job)
    if is_waiting_room(meeting) or vs == "awaiting_admission":
        return waiting_room_instructions(topic=topic)
    return None


def should_notify_recording_failure(job: dict[str, Any]) -> bool:
    """Не слать fail по старой попытке, если у пользователя уже идёт новая запись."""
    return _should_notify_failed(job)


def abandon_stale_joining_jobs_for_user(
    telegram_user_id: int,
    *,
    keep_job_id: int,
) -> None:
    """Закрыть зависшие joining-задачи при новой отправке бота (без уведомлений)."""
    uid = int(telegram_user_id)
    for other in mrs.list_jobs_for_user(uid, limit=30):
        oid = int(other["id"])
        if oid == keep_job_id:
            continue
        if str(other.get("status") or "") != "joining":
            continue
        _fail_job(
            oid,
            message="заменено новой попыткой записи",
            skip_failed_notify=True,
        )
        meeting_bot.stop_zoom_bot(str(other.get("native_meeting_id") or ""))


def dispatch_started_message(*, topic: str) -> str:
    return (
        f"🎙 Отправил Leo на встречу «{topic}».\n"
        "Через полминуты напишу, удалось ли подключиться."
    )


def processing_status_message(*, topic: str) -> str:
    return f"⏳ Обрабатываю запись встречи «{topic}»…"


def status_message_ids(job: dict[str, Any]) -> dict[int, int]:
    meta = job.get("meta") or {}
    raw = meta.get("status_message_ids") if isinstance(meta, dict) else None
    out: dict[int, int] = {}
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        try:
            out[int(key)] = int(val)
        except (TypeError, ValueError):
            continue
    return out


def update_job_status_messages(
    job_id: int,
    text: str,
    user_ids: Iterable[int],
) -> int:
    """Обновить одно статусное сообщение у каждого получателя (edit или send)."""
    from assistant.lib.telegram_notify import upsert_user_status_message

    job = mrs.get_job(job_id)
    if not job:
        return 0
    ids = status_message_ids(job)
    new_ids = dict(ids)
    updated = 0
    for uid in user_ids:
        uid = int(uid)
        mid = upsert_user_status_message(uid, text, message_id=ids.get(uid))
        if mid:
            new_ids[uid] = mid
            updated += 1
    if new_ids != ids:
        meta = dict(job.get("meta") or {})
        meta["status_message_ids"] = {str(k): v for k, v in new_ids.items()}
        mrs.update_job(job_id, meta=meta)
    return updated


def _fail_job(
    job_id: int,
    *,
    message: str,
    skip_failed_notify: bool = False,
) -> dict[str, Any] | None:
    fields: dict[str, Any] = {"status": "failed", "error_message": message}
    if skip_failed_notify:
        job = mrs.get_job(job_id)
        meta = dict((job or {}).get("meta") or {})
        meta["skip_failed_notify"] = True
        fields["meta"] = meta
    mrs.update_job(job_id, **fields)
    job = mrs.get_job(job_id)
    if job and not skip_failed_notify:
        notify_job_failure(job_id)
    return job


def _user_has_newer_recording_job(job: dict[str, Any]) -> bool:
    """У пользователя уже идёт или завершилась более новая запись — не спамим старым fail."""
    uid = int(job["telegram_user_id"])
    jid = int(job["id"])
    for other in mrs.list_jobs_for_user(uid, limit=30):
        if int(other["id"]) <= jid:
            continue
        if str(other.get("status") or "") in ("recording", "processing", "done"):
            return True
    return False


def _should_notify_failed(job: dict[str, Any]) -> bool:
    if _meta_flag(job, "skip_failed_notify"):
        return False
    if _user_has_newer_recording_job(job):
        return False
    return True


def _job_needs_failure_notification(job: dict[str, Any]) -> bool:
    if str(job.get("status") or "") != "failed":
        return False
    if _meta_flag(job, "failed_notified"):
        return False
    if not _should_notify_failed(job):
        return False
    if status_message_ids(job):
        return True
    updated = _parse_iso(str(job.get("updated_at_utc") or ""))
    if updated and datetime.now(timezone.utc) - updated < timedelta(hours=6):
        return True
    return False


def failure_notification_text(job: dict[str, Any]) -> str:
    topic = str(job.get("topic") or "Встреча Zoom")
    err = str(job.get("error_message") or "").strip()
    err_l = err.lower()
    join_markers = (
        "join_failure",
        "join_blocked",
        "блокировка zoom",
        "не смог войти",
        "unknown_blocking_state",
    )
    if any(marker in err_l for marker in join_markers):
        return join_blocked_message(topic=topic, job=job)
    if "выгнан" in err_l or "evicted" in err_l:
        return (
            f"⚠️ Встреча «{topic}» завершилась, но запись не удалось обработать.\n"
            "Отправьте ссылку ещё раз на следующую встречу."
        )
    if err:
        return f"❌ Leo не смог записать «{topic}»: {err}\nОтправьте ссылку ещё раз."
    return f"❌ Leo не смог записать встречу «{topic}».\nОтправьте ссылку ещё раз."


def notify_job_failure(job_id: int) -> bool:
    """Обновить статусное сообщение (над саммари) о неудачной записи."""
    job = mrs.get_job(job_id)
    if not job or str(job.get("status") or "") != "failed":
        return False
    if _meta_flag(job, "failed_notified"):
        return False
    if not _should_notify_failed(job):
        _set_meta_flag(job_id, job, "failed_notified")
        return False
    if not status_message_ids(job):
        updated = _parse_iso(str(job.get("updated_at_utc") or ""))
        if not updated or datetime.now(timezone.utc) - updated >= timedelta(hours=6):
            return False
    recipients = mrs.list_recipient_user_ids(job_id)
    if not recipients:
        recipients = [int(job["telegram_user_id"])]
    text = failure_notification_text(job)
    if not update_job_status_messages(job_id, text, recipients):
        return False
    job = mrs.get_job(job_id) or job
    _set_meta_flag(job_id, job, "failed_notified")
    return True


def notify_job_human_help(job_id: int, *, topic: str, esc_reason: str) -> bool:
    """Уведомить о waiting room / блокировке входа через статусное сообщение."""
    job = mrs.get_job(job_id)
    if not job or not should_notify_recording_failure(job):
        return False
    if esc_reason == "unknown_blocking_state":
        if _meta_flag(job, "join_blocked_notified"):
            return False
        text = join_blocked_message(topic=topic, job=job)
        flag = "join_blocked_notified"
    else:
        if _meta_flag(job, "waiting_room_notified"):
            return False
        text = waiting_room_instructions(topic=topic)
        flag = "waiting_room_notified"
    recipients = mrs.list_recipient_user_ids(job_id)
    if not recipients:
        recipients = [int(job["telegram_user_id"])]
    if not update_job_status_messages(job_id, text, recipients):
        return False
    job = mrs.get_job(job_id) or job
    _set_meta_flag(job_id, job, flag)
    _set_meta_flag(job_id, job, "human_help_notified")
    return True


def _stop_stale_bot(job: dict[str, Any]) -> None:
    native = str(job.get("native_meeting_id") or "").strip()
    if native:
        meeting_bot.stop_zoom_bot(native)


def reconcile_job(job: dict[str, Any]) -> dict[str, Any]:
    """Привести статус job в соответствие с Vexa."""
    if not job:
        return job
    job_id = int(job["id"])
    status = str(job.get("status") or "")
    if status not in mrs._ACTIVE_STATUSES:
        return job

    vexa_id = job.get("vexa_meeting_id")
    if vexa_id is None:
        if status != "joining":
            return job
        updated = _parse_iso(str(job.get("updated_at_utc") or ""))
        if updated and datetime.now(timezone.utc) - updated < timedelta(
            minutes=_ORPHAN_JOINING_MINUTES
        ):
            return job
        return _fail_job(job_id, message="бот не был отправлен на встречу") or job

    meeting = meeting_bot.fetch_vexa_meeting(int(vexa_id))
    if not meeting:
        return job

    vs = str(meeting.get("status") or "").strip().lower()
    updated = _parse_iso(str(meeting.get("updated_at") or job.get("updated_at_utc") or ""))
    now = datetime.now(timezone.utc)

    if vs == "active":
        if status == "joining":
            mrs.update_job(job_id, status="recording")
            return mrs.get_job(job_id) or job
        return job

    if vs in _VEXA_LIVE:
        if status == "recording":
            mrs.update_job(job_id, status="joining")
            return mrs.get_job(job_id) or job
        return job

    if vs in _VEXA_STUCK:
        if meeting.get("start_time"):
            if status == "joining":
                mrs.update_job(job_id, status="recording")
                return mrs.get_job(job_id) or job
            return job
        if updated and now - updated >= timedelta(minutes=_STUCK_RETRY_MINUTES):
            _stop_stale_bot(job)
            skip_notify = _user_has_newer_recording_job(job)
            return _fail_job(
                job_id,
                message="Leo не смог войти на встречу (блокировка Zoom)",
                skip_failed_notify=skip_notify,
            ) or job
        if status == "recording" and not meeting.get("start_time"):
            mrs.update_job(job_id, status="joining")
            refreshed = mrs.get_job(job_id)
            return refreshed or job
        return job

    if vs in _VEXA_TERMINAL:
        _stop_stale_bot(job)
        reason = ""
        if vs == "failed":
            reason = str((meeting.get("data") or {}).get("completion_reason") or vs)
        if vs in ("completed", "ended"):
            from assistant.integrations.vexa_webhook import start_recording_pipeline_if_ready

            if start_recording_pipeline_if_ready(job):
                return mrs.get_job(job_id) or job
        if vs == "failed" and reason in ("evicted", "removed_by_admin", "host_ended", "stopped"):
            from assistant.integrations.vexa_webhook import start_recording_pipeline_if_ready

            if start_recording_pipeline_if_ready(job):
                return job
            return _fail_job(
                job_id,
                message=f"встреча завершилась ({reason})",
            ) or job
        msg = f"встреча завершена в Vexa ({vs})"
        if vs == "failed":
            msg = f"бот не смог записать встречу ({reason or vs})"
        skip = _user_has_newer_recording_job(job)
        return _fail_job(job_id, message=msg, skip_failed_notify=skip) or job

    return job


def dedupe_action(job: dict[str, Any] | None) -> DedupeAction:
    """Можно ли подписать пользователя на существующую задачу."""
    if not job:
        return "create_new"
    job = reconcile_job(job)
    status = str(job.get("status") or "")
    if status in ("failed", "done", "merged"):
        return "create_new"
    if status == "scheduled":
        return "subscribe"
    if status in ("joining", "recording", "processing"):
        return "subscribe"
    return "create_new"


def existing_recording_message(job: dict[str, Any]) -> str:
    """Текст для пользователя, подписавшегося на уже идущую запись."""
    status = str(job.get("status") or "")
    meeting = _vexa_meeting_for_job(job)
    topic = str(job.get("topic") or "Встреча Zoom")

    if status == "scheduled":
        return (
            "Запись этой встречи уже запланирована. "
            "После окончания пришлю транскрипцию и саммари."
        )
    msg = status_message_for_job(job, meeting)
    if msg:
        return msg
    if str(meeting.get("status") if meeting else "") == "active" or status in (
        "recording",
        "processing",
    ):
        return (
            "Leo уже записывает эту встречу. "
            "После окончания пришлю вам транскрипцию и саммари."
        )
    return (
        "Leo уже обрабатывает эту встречу. "
        "После окончания пришлю транскрипцию и саммари."
    )


def _meta_flag(job: dict[str, Any], key: str) -> bool:
    meta = job.get("meta")
    return bool(isinstance(meta, dict) and meta.get(key))


def _set_meta_flag(job_id: int, job: dict[str, Any], key: str) -> None:
    meta = dict(job.get("meta") or {})
    meta[key] = True
    mrs.update_job(job_id, meta=meta)


def poll_joining_jobs() -> int:
    """Опрос Vexa: уведомить о блокировке / waiting room / входе."""

    jobs = mrs.list_jobs_by_statuses(("joining", "recording"))
    sent = 0
    now = datetime.now(timezone.utc)
    for job in jobs:
        job = reconcile_job(job)
        job_id = int(job["id"])
        status = str(job.get("status") or "")
        recipients = mrs.list_recipient_user_ids(job_id)
        meeting = _vexa_meeting_for_job(job)

        if status == "failed":
            err = str(job.get("error_message") or "")
            if "evicted" in err.lower():
                from assistant.integrations.vexa_webhook import start_recording_pipeline_if_ready

                if start_recording_pipeline_if_ready(job):
                    continue
            if notify_job_failure(job_id):
                sent += 1
            continue

        if status not in ("joining", "recording"):
            continue

        updated = _parse_iso(str(job.get("updated_at_utc") or ""))
        if not updated or (now - updated).total_seconds() < _STATUS_POLL_SEC:
            continue

        vs = str(meeting.get("status") or "").strip().lower() if meeting else ""
        if vs == "active" and status == "recording":
            if not _meta_flag(job, "joined_notified"):
                text = status_message_for_job(job, meeting)
                if text:
                    update_job_status_messages(job_id, text, recipients)
                    _set_meta_flag(job_id, job, "joined_notified")
                    sent += 1
            continue

        if is_join_blocked(meeting):
            if not _meta_flag(job, "join_blocked_notified"):
                text = status_message_for_job(job, meeting)
                if text:
                    update_job_status_messages(job_id, text, recipients)
                    _set_meta_flag(job_id, job, "join_blocked_notified")
                    sent += 1
            continue

        if is_waiting_room(meeting) and not _meta_flag(job, "waiting_room_notified"):
            text = status_message_for_job(job, meeting)
            if text:
                update_job_status_messages(job_id, text, recipients)
                _set_meta_flag(job_id, job, "waiting_room_notified")
                sent += 1
    for job in mrs.list_jobs_by_statuses(("failed",), limit=30):
        if _job_needs_failure_notification(job) and notify_job_failure(int(job["id"])):
            sent += 1
    return sent
