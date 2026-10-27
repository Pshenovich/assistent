"""Чат Лео: «<ссылка> транскрибируй / сделай саммари» → транскрипция в фоне.

Запрос чата не ждёт скачивания и STT: сразу отвечаем, а результат (ссылку на заметку
или текст ошибки) Лео дописывает в тот же тред отдельным сообщением.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

_TRANSCRIBE_RE = re.compile(r"транскри\w*|транскрайб\w*|расшифр\w*|transcri\w*", re.IGNORECASE)
_SUMMARY_RE = re.compile(
    r"саммари|summary|summari[sz]\w*|суммариз\w*|выжимк\w*|протокол\w*|пересказ\w*|"
    r"кратк\w+\s+содержани\w*",
    re.IGNORECASE,
)
_MEDIA_EXT_RE = re.compile(
    r"\.(?:mp3|m4a|wav|ogg|oga|opus|flac|aac|wma|webm|mp4|m4v|mov|mkv|avi|wmv|3gp)$",
    re.IGNORECASE,
)
_URL_TRAILING = ".,;:!?)»\"'"

ACCEPTED_TEXT = (
    "Скачиваю и транскрибирую{summary} — это может занять несколько минут. "
    "Транскрипция появится в «Сохранённом» → «Транскрипции», а сюда я пришлю ссылку."
)
# Сообщение с результатом не должно обогнать ответ «Скачиваю…», который клиент сохраняет сам.
MIN_RESULT_DELAY_SEC = 4.0


@dataclass(frozen=True)
class UrlTranscribeRequest:
    url: str
    with_summary: bool


def _clean_url(raw: str) -> str:
    return (raw or "").strip().rstrip(_URL_TRAILING)


def is_media_file_url(url: str) -> bool:
    """Ссылка явно на файл: Google Диск, Яндекс.Диск или путь с расширением аудио/видео."""
    from assistant.lib.media_download import google_drive_file_id, is_yandex_disk_public_url

    if google_drive_file_id(url) or is_yandex_disk_public_url(url):
        return True
    return bool(_MEDIA_EXT_RE.search(urlparse(url).path or ""))


def detect_url_transcribe_request(text: str) -> Optional[UrlTranscribeRequest]:
    """Ссылка + «транскрибируй» (любая ссылка) или «саммари» (только ссылка на файл).

    «Сделай саммари статьи <url>» про веб-страницу остаётся модели.
    """
    from assistant.lib.urls import extract_urls
    from assistant.nlu.regex import parse_summary_intent, parse_transcribe_intent

    s = (text or "").strip()
    if not s:
        return None
    urls = [_clean_url(u) for u in extract_urls(s)]
    urls = [u for u in urls if u.lower().startswith(("http://", "https://"))]
    if not urls:
        return None
    rest = s
    for u in extract_urls(s):
        rest = rest.replace(u, " ")
    wants_transcribe = parse_transcribe_intent(rest) or bool(_TRANSCRIBE_RE.search(rest))
    wants_summary = parse_summary_intent(rest) or bool(_SUMMARY_RE.search(rest))
    if not wants_transcribe and not wants_summary:
        return None
    media = [u for u in urls if is_media_file_url(u)]
    if not wants_transcribe and not media:
        return None
    return UrlTranscribeRequest(url=(media or urls)[0], with_summary=wants_summary)


def accepted_text(req: UrlTranscribeRequest) -> str:
    return ACCEPTED_TEXT.format(summary=" и сделаю саммари" if req.with_summary else "")


def _md_label(title: str) -> str:
    label = re.sub(r"[\[\]()\n\r]+", " ", (title or "").strip())
    return " ".join(label.split()) or "Транскрипция"


def done_text(note: dict[str, Any], *, with_summary: bool) -> str:
    from assistant.stores.note_members import note_webapp_url

    link = f"[{_md_label(str(note.get('title') or ''))}]({note_webapp_url(int(note['id']))})"
    if with_summary:
        return (
            f"Готово: транскрипция — {link}. Саммари ещё пишется, оно появится в этой же "
            "заметке через минуту-другую."
        )
    return f"Готово: транскрипция — {link}. Она лежит в «Сохранённом» → «Транскрипции»."


def transcribe_url_to_note(
    uid: int,
    url: str,
    *,
    with_summary: bool,
    username: str | None = None,
    prepare_audio: Callable[[bytes, str, str | None], tuple[bytes, str]] | None = None,
) -> dict[str, Any]:
    """Скачать по ссылке → STT → заметка-транскрипция (+ саммари). ValueError — понятная ошибка."""
    from assistant.integrations import transcribe as obu
    from assistant.lib.media_download import download_url_bytes
    from assistant.lib.usage_store import insert_usage_event
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import voice_prefs

    data, source_filename = download_url_bytes(url)
    source_filename = str(source_filename or "media.mp4").strip() or "media.mp4"
    filename = source_filename
    if prepare_audio is not None:
        data, filename = prepare_audio(data, filename, None)
    result = voice_prefs.process_transcript_result(uid, obu.transcribe_bytes(data, filename))
    transcript = (result.formatted_text or result.plain_text or "").strip()
    if not transcript:
        raise ValueError("В файле не нашлось речи — транскрипция пустая.")
    insert_usage_event(
        operation="obuchat_transcribe",
        model=None,
        generation_id=result.job_id,
        usage={
            "text": transcript[:4000],
            "source": "miniapp_chat_url",
            "filename": filename,
            "with_summary": with_summary,
        },
        telegram_user_id=str(uid),
        telegram_username=username,
    )
    note = tnotes.create_transcript_only(
        uid,
        title=(Path(source_filename).stem.strip() or "Транскрипция")[:120],
        transcript=transcript,
        meta={
            "source": "miniapp_url",
            "url": url,
            "filename": source_filename,
            "job_id": result.job_id,
        },
        segments=getattr(result, "segments", None),
    )
    if with_summary:
        note = tnotes.begin_make_summary(uid, int(note["id"]))
    return note


def post_chat_reply(uid: int, thread_id: str, text: str, *, model: str | None = None) -> None:
    """Сообщение Лео в тред чата — как ответ ИИ, сохранённый клиентом."""
    from assistant.integrations.openrouter_client import gpt_display_label
    from assistant.stores import chat_threads, share_comments

    body = (text or "").strip()
    if not body:
        return
    share_comments.add_comment(
        uid,
        "chat",
        thread_id,
        author_user_id=uid,
        author_name=gpt_display_label(model) or share_comments.GPT_AUTHOR_NAME,
        author_username=share_comments.GPT_AUTHOR_USERNAME,
        body=body[: share_comments.MAX_GPT_BODY_LEN],
        prefix=share_comments.GPT_PREFIX,
    )
    chat_threads.after_comment(uid, thread_id, body, is_user=False)


def run_chat_url_transcription(
    uid: int,
    thread_id: str,
    req: UrlTranscribeRequest,
    *,
    model: str | None = None,
    username: str | None = None,
    prepare_audio: Callable[[bytes, str, str | None], tuple[bytes, str]] | None = None,
    min_delay_sec: float = MIN_RESULT_DELAY_SEC,
) -> None:
    """Фоновая задача: транскрипция по ссылке и итоговое сообщение в тред."""
    started = time.monotonic()
    note: dict[str, Any] | None = None
    try:
        note = transcribe_url_to_note(
            uid,
            req.url,
            with_summary=req.with_summary,
            username=username,
            prepare_audio=prepare_audio,
        )
        reply = done_text(note, with_summary=req.with_summary)
    except ValueError as e:
        reply = f"Не получилось: {e}"
    except Exception as e:
        print(f"[chat_url_transcribe] failed uid={uid} url={req.url!r} err={e!r}")
        reply = f"Не получилось транскрибировать файл по ссылке: {e}"
    wait = min_delay_sec - (time.monotonic() - started)
    if wait > 0:
        time.sleep(wait)
    try:
        post_chat_reply(uid, thread_id, reply, model=model)
    except Exception as e:
        print(f"[chat_url_transcribe] reply failed uid={uid} thread={thread_id} err={e!r}")
    if note is not None and req.with_summary:
        from assistant.services import transcription_notes as tnotes

        try:
            tnotes.generate_summary_for_note(uid, int(note["id"]))
        except Exception as e:
            print(f"[chat_url_transcribe] summary failed uid={uid} note={note.get('id')} err={e!r}")
