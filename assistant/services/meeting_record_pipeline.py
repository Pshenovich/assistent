"""Постобработка записи встречи: Obuchat + саммари + журнал + уведомление."""

from __future__ import annotations

import html
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

from assistant.integrations import meeting_bot, transcribe as obu
from assistant.integrations.transcribe import TranscribeResult
from assistant.lib.telegram_rich import append_footnotes, zoom_meeting_footnote
from assistant.lib.usage_store import insert_usage_event
from assistant.lib.vexa_transcript import formatted_transcript_from_vexa
from assistant.nlu import llm as llm_mod
from assistant.stores import meeting_recordings_store as mrs

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MEETING_SPEAKERS_MODE = (os.getenv("MEETING_BOT_OBUCHAT_SPEAKERS", "auto") or "auto").strip() or "auto"


def _display_participant_name(name: str) -> str:
    raw = (name or "").strip()
    m = re.match(r"SPEAKER_(\d+)$", raw, flags=re.IGNORECASE)
    if m:
        return f"Спикер {m.group(1)}"
    if raw.lower().startswith("speaker "):
        return "Спикер " + raw.split(None, 1)[-1]
    return raw


def _participant_names_from_transcript(formatted: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for block in (formatted or "").split("\n\n"):
        if ":\n" not in block:
            continue
        name = _display_participant_name(block.split(":\n", 1)[0].strip())
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _speakers_detected(result: TranscribeResult) -> bool:
    formatted = (result.formatted_text or "").strip()
    plain = (result.plain_text or "").strip()
    if formatted != plain and ("Спикер" in formatted or ":\n" in formatted):
        return True
    return "Спикер" in formatted


def _build_meta(
    result: dict,
    *,
    speakers_detected: bool,
    participant_names: list[str] | None = None,
) -> dict:
    participants = result.get("participants") or []
    if participant_names:
        participants = [{"name": n} for n in participant_names]
    return {
        "content_type": result.get("content_type") or "meeting",
        "confidence": result.get("confidence") or 0.0,
        "main_topic": result.get("main_topic") or "",
        "participants_count": len(participants) if isinstance(participants, list) else 0,
        "speakers_detected": speakers_detected,
        "participants": participants,
        "tasks": result.get("tasks") or [],
        "decisions": result.get("decisions") or [],
        "deadlines": result.get("deadlines") or [],
        "topics": result.get("topics") or [],
        "open_questions": result.get("open_questions") or [],
        "risks": result.get("risks") or [],
    }


def _append_source_footer_html(summary_text: str, *, source_url: str | None) -> str:
    body = (summary_text or "").strip()
    if not source_url:
        return body
    ref, foot = zoom_meeting_footnote(source_url)
    if not ref:
        return body
    return append_footnotes(f"{body}\n<p>{ref}</p>", [foot] if foot else [])


def _format_transcript_display(transcript: str) -> str:
    body = (transcript or "").strip()
    return re.sub(
        r"(?m)^(SPEAKER_(\d+)):",
        lambda m: f"Спикер {m.group(2)}:",
        body,
    )


def _save_transcription_journal(
    *,
    user_id: int,
    username: str | None,
    transcript: str,
    result: TranscribeResult | None,
    filename: str | None,
    source_url: str | None,
    topic: str,
    participant_names: list[str] | None,
) -> int:
    text = _format_transcript_display(transcript)
    usage: dict[str, Any] = {
        "text": text,
        "source": "zoom_meeting_bot",
    }
    if filename:
        usage["filename"] = filename
    if source_url:
        usage["source_url"] = source_url
    meta: dict[str, Any] = {"meeting_topic": (topic or "").strip()}
    if participant_names:
        meta["participants"] = [
            _display_participant_name(n) for n in participant_names if str(n).strip()
        ]
    usage["meta"] = meta
    return insert_usage_event(
        operation="obuchat_transcribe",
        model=None,
        generation_id=result.job_id if result else None,
        usage=usage,
        telegram_user_id=str(user_id) if user_id else None,
        telegram_username=username,
    )


def _save_summary_journal(
    *,
    user_id: int,
    username: str | None,
    summary_text: str,
    meta: dict,
    result: TranscribeResult | None = None,
    filename: str | None = None,
    source_url: str | None = None,
    transcript_event_id: int | None = None,
) -> int:
    usage_meta = dict(meta)
    if transcript_event_id is not None:
        usage_meta["transcript_event_id"] = int(transcript_event_id)
    usage: dict[str, Any] = {
        "text": summary_text,
        "source": "zoom_meeting_bot",
        "meta": usage_meta,
    }
    if filename:
        usage["filename"] = filename
    if source_url:
        usage["source_url"] = source_url
    return insert_usage_event(
        operation="summarize",
        model=llm_mod.summary_model_name(),
        generation_id=result.job_id if result else None,
        usage=usage,
        telegram_user_id=str(user_id) if user_id else None,
        telegram_username=username,
    )


def _transcribe_recording(
    *,
    audio: bytes,
    filename: str,
    native_meeting_id: str,
) -> tuple[TranscribeResult, list[str]]:
    """Vexa (имена из Zoom) → fallback Obuchat."""
    participant_names: list[str] = []
    vexa_body = meeting_bot.fetch_zoom_transcript(native_meeting_id)
    if vexa_body:
        parsed = formatted_transcript_from_vexa(vexa_body)
        if parsed:
            plain, formatted, participant_names = parsed
            return TranscribeResult(
                plain_text=plain,
                formatted_text=formatted,
                job_id=None,
            ), participant_names
    obu_result = obu.transcribe_bytes(
        audio,
        filename or "meeting.wav",
        speakers=_MEETING_SPEAKERS_MODE,
    )
    names = _participant_names_from_transcript(obu_result.formatted_text or "")
    return obu_result, names


def process_recording_bytes(
    *,
    user_id: int,
    username: str | None,
    data: bytes,
    filename: str,
    topic: str,
    source_url: str | None = None,
    native_meeting_id: str | None = None,
) -> dict[str, Any]:
    result, participant_names = _transcribe_recording(
        audio=data,
        filename=filename,
        native_meeting_id=(native_meeting_id or "").strip(),
    )
    transcript = (result.formatted_text or result.plain_text or "").strip()
    if not transcript:
        raise RuntimeError("Пустая транскрипция записи встречи.")
    speakers = _speakers_detected(result) or bool(participant_names)
    summary_result = llm_mod.summarize_recording(transcript, speakers_detected=speakers)
    if not summary_result:
        raise RuntimeError("Не удалось сделать саммари.")
    summary_text = str(summary_result.get("summary") or "").strip()
    from assistant.lib.telegram_markdown import prepare_summary_markdown

    ts = datetime.now(timezone.utc).isoformat()
    headline = f"📝 Саммари встречи «{topic}»"
    display_text = prepare_summary_markdown(
        summary_text,
        tasks=summary_result.get("tasks"),
        source_url=source_url,
        headline=headline,
        ts=ts,
    )
    meta = _build_meta(
        summary_result,
        speakers_detected=speakers,
        participant_names=participant_names or None,
    )
    llm_names: list[str] = []
    for item in summary_result.get("participants") or []:
        if isinstance(item, dict):
            n = str(item.get("name") or "").strip()
        else:
            n = str(item or "").strip()
        if n and n not in llm_names:
            llm_names.append(n)
    for n in participant_names:
        if n not in llm_names:
            llm_names.append(n)
    participant_names = llm_names or participant_names
    return {
        "summary_text": display_text,
        "transcript": transcript,
        "participant_names": participant_names,
        "meta": meta,
        "result": result,
    }


def persist_meeting_artifacts_for_user(
    *,
    user_id: int,
    username: str | None,
    processed: dict[str, Any],
    topic: str,
    source_url: str | None,
    filename: str | None,
) -> tuple[int, int]:
    """Сохранить транскрипцию и саммари в журнал мини-приложения. → (transcript_id, summary_id)."""
    transcript = str(processed.get("transcript") or "")
    result = processed.get("result")
    tr_result = result if isinstance(result, TranscribeResult) else None
    participant_names = list(processed.get("participant_names") or [])
    transcript_id = _save_transcription_journal(
        user_id=int(user_id),
        username=username,
        transcript=transcript,
        result=tr_result,
        filename=filename,
        source_url=source_url,
        topic=topic,
        participant_names=participant_names,
    )
    summary_id = _save_summary_journal(
        user_id=int(user_id),
        username=username,
        summary_text=str(processed.get("summary_text") or ""),
        meta=dict(processed.get("meta") or {}),
        result=tr_result,
        filename=filename,
        source_url=source_url,
        transcript_event_id=transcript_id,
    )
    return transcript_id, summary_id


def notify_summary_ready(
    *,
    user_id: int,
    topic: str,
    summary_text: str,
    summary_event_id: int | None = None,
    transcript_event_id: int | None = None,
    yandex_disk_url: str | None = None,
    yandex_disk_saved: bool = False,
    meeting_ts: str | None = None,
) -> None:
    from assistant.skills.journal_pdf import pdf_download_inline_keyboard

    from assistant.lib import telegram_notify
    from assistant.lib.telegram_markdown import (
        append_yandex_disk_footer_markdown,
        format_meeting_date,
    )

    summary_markup = (
        pdf_download_inline_keyboard(int(summary_event_id))
        if summary_event_id
        else None
    )
    body = append_yandex_disk_footer_markdown(
        summary_text,
        yandex_disk_url,
        yandex_disk_saved=yandex_disk_saved,
    )
    body = (body or "").strip()
    if not body.lstrip().startswith("#"):
        date_line = format_meeting_date(
            meeting_ts or datetime.now(timezone.utc).isoformat()
        )
        title = f"## 📝 Саммари встречи «{topic}»"
        if date_line:
            title += f"\n*{date_line}*"
        body = f"{title}\n\n{body}"
    body += "\n\nСохранено в мини-приложении → вкладка «Саммари»."
    if not telegram_notify.send_rich_message(
        int(user_id),
        body,
        markdown=True,
        reply_markup=summary_markup,
    ):
        telegram_notify.send_user_message(
            int(user_id),
            "Не удалось отправить саммари в чат. Откройте мини-приложение → вкладка «Саммари».",
            reply_markup=summary_markup,
        )
    if transcript_event_id:
        transcript_markup = pdf_download_inline_keyboard(int(transcript_event_id))
        telegram_notify.send_user_message(
            int(user_id),
            (
                f"🗣 Транскрипция встречи «{topic}» сохранена в мини-приложении "
                "→ вкладка «Транскрипции»."
            ),
            reply_markup=transcript_markup,
        )


def process_job_recording_async(job_id: int, recording: dict[str, Any]) -> None:
    """Фоновая обработка: скачать аудио → транскрипция → саммари → всем подписчикам."""

    def _run() -> None:
        job = mrs.get_job(job_id)
        if not job:
            return
        if str(job.get("status") or "") in ("processing", "done"):
            return
        topic = str(job.get("topic") or "Встреча Zoom")
        source_url = str(job.get("meeting_url") or "") or None
        native_mid = str(job.get("native_meeting_id") or "").strip()
        recipients = mrs.list_recipient_user_ids(job_id)
        try:
            mrs.update_job(job_id, status="processing")
            from assistant.services.meeting_record_reconcile import (
                processing_status_message,
                update_job_status_messages,
            )

            update_job_status_messages(
                job_id,
                processing_status_message(topic=topic),
                recipients,
            )
            media = meeting_bot.pick_audio_media_file(recording)
            if not media:
                raise RuntimeError("Vexa не вернул media_files для записи.")
            rec_id = recording.get("id") or job.get("vexa_recording_id")
            media_id = media.get("id")
            if rec_id is None or media_id is None:
                raise RuntimeError("Не удалось определить id записи Vexa.")
            data, fname = meeting_bot.download_recording_media(rec_id, media_id)
            first_uid = recipients[0] if recipients else int(job["telegram_user_id"])
            out = process_recording_bytes(
                user_id=first_uid,
                username=None,
                data=data,
                filename=fname,
                topic=topic,
                source_url=source_url,
                native_meeting_id=native_mid,
            )
            display_text = str(out.get("summary_text") or "")
            journal_ids: dict[int, int] = {}
            transcript_ids: dict[int, int] = {}
            yandex_uploads: dict[int, dict] = {}
            yandex_errors: dict[int, str] = {}
            for uid in recipients:
                tr_id, sm_id = persist_meeting_artifacts_for_user(
                    user_id=uid,
                    username=None,
                    processed=out,
                    topic=topic,
                    source_url=source_url,
                    filename=fname,
                )
                transcript_ids[uid] = tr_id
                journal_ids[uid] = sm_id
                from assistant.services import yandex_disk_upload

                upload_result = yandex_disk_upload.try_upload_meeting_for_user(
                    user_id=uid,
                    media_bytes=data,
                    media_filename=fname,
                    topic=topic,
                    transcript_event_id=tr_id,
                    summary_event_id=sm_id,
                )
                if upload_result.get("ok"):
                    yandex_uploads[uid] = dict(upload_result.get("paths") or {})
                elif not upload_result.get("skipped"):
                    err = str(upload_result.get("error") or "").strip()
                    if err:
                        yandex_errors[uid] = err
            mrs.update_job(
                job_id,
                status="done",
                vexa_recording_id=int(rec_id) if rec_id is not None else None,
                journal_event_id=journal_ids.get(first_uid),
            )
            for uid in recipients:
                yandex_paths = yandex_uploads.get(uid)
                yandex_public_url = str(
                    (yandex_paths or {}).get("folder_public_url") or ""
                ).strip() or None
                yandex_saved = bool(yandex_paths) and not yandex_public_url
                notify_summary_ready(
                    user_id=uid,
                    topic=topic,
                    summary_text=display_text,
                    summary_event_id=journal_ids.get(uid),
                    transcript_event_id=transcript_ids.get(uid),
                    yandex_disk_url=yandex_public_url,
                    yandex_disk_saved=yandex_saved,
                )
                if uid in yandex_errors:
                    from assistant.lib import telegram_notify

                    telegram_notify.send_user_message(
                        int(uid),
                        (
                            f"⚠️ Не удалось сохранить запись «{topic}» на Яндекс Диск: "
                            f"{yandex_errors[uid]}"
                        ),
                    )
        except Exception as e:
            print(f"[meeting_record_pipeline] job={job_id} err={e!r}")
            mrs.update_job(job_id, status="failed", error_message=str(e))
            from assistant.services.meeting_record_reconcile import update_job_status_messages

            update_job_status_messages(
                job_id,
                f"❌ Не удалось обработать запись «{topic}»: {e}",
                recipients,
            )

    threading.Thread(target=_run, name=f"meeting-record-{job_id}", daemon=True).start()
