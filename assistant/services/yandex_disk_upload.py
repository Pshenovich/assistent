"""Выгрузка артефактов Zoom-записи на Яндекс Диск пользователя."""

from __future__ import annotations

from typing import Any

from assistant.integrations import yandex_disk_api, yandex_disk_oauth
from assistant.lib.journal_pdf import journal_pdf_for_user


def upload_meeting_for_user(
    *,
    user_id: int,
    media_bytes: bytes,
    media_filename: str,
    topic: str,
    transcript_event_id: int,
    summary_event_id: int,
) -> dict[str, str]:
    """Загружает медиа и PDF на диск пользователя. Бросает исключение при ошибке."""
    token = yandex_disk_oauth.get_valid_access_token(int(user_id))
    uid_str = str(int(user_id))
    tr_pdf = journal_pdf_for_user(uid_str, int(transcript_event_id))
    sm_pdf = journal_pdf_for_user(uid_str, int(summary_event_id))
    return yandex_disk_api.upload_meeting_artifacts(
        token,
        media_bytes=media_bytes,
        media_filename=media_filename,
        topic=topic,
        transcript_pdf=(tr_pdf[0], tr_pdf[1]),
        summary_pdf=(sm_pdf[0], sm_pdf[1]),
    )


def try_upload_meeting_for_user(
    *,
    user_id: int,
    media_bytes: bytes,
    media_filename: str,
    topic: str,
    transcript_event_id: int,
    summary_event_id: int,
) -> dict[str, Any]:
    """Безопасная обёртка: не бросает, возвращает {ok, paths?, error?}."""
    if not yandex_disk_oauth.has_connection(int(user_id)):
        return {"ok": False, "skipped": True}
    try:
        paths = upload_meeting_for_user(
            user_id=int(user_id),
            media_bytes=media_bytes,
            media_filename=media_filename,
            topic=topic,
            transcript_event_id=int(transcript_event_id),
            summary_event_id=int(summary_event_id),
        )
        return {"ok": True, "paths": paths}
    except Exception as e:
        print(f"[yandex_disk_upload] user={user_id} err={e!r}")
        return {"ok": False, "error": str(e)}
