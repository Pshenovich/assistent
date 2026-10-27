"""Транскрипции и саммари как local notes с листами."""

from __future__ import annotations

import html
import re
from typing import Any, Optional

from assistant.stores import note_sheets as sheets_store
from assistant.stores import notes as notes_store

SUMMARY_SHEET_TITLE = notes_store.SUMMARY_SHEET_TITLE
TRANSCRIPT_SHEET_TITLE = notes_store.TRANSCRIPT_SHEET_TITLE
TRANSCRIPTION_ROLE = notes_store.TRANSCRIPTION_ROLE

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class TranscriptionNoteError(ValueError):
    pass


def is_transcription_note(note: dict[str, Any] | None) -> bool:
    if not note:
        return False
    return bool(
        note.get("is_transcription")
        or str(note.get("role") or "") == TRANSCRIPTION_ROLE
    )


def has_summary(note: dict[str, Any] | None) -> bool:
    if not is_transcription_note(note):
        return False
    title = str((note or {}).get("primary_sheet_title") or "").strip()
    return title == SUMMARY_SHEET_TITLE


def _as_html(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    if "<" in body and ">" in body:
        return body
    if body.startswith("#") or "\n#" in body or "**" in body or "- [" in body:
        return body
    return "<p>" + html.escape(body).replace("\n", "<br>\n") + "</p>"


def _plain(text: str) -> str:
    from assistant.lib.telegram_html import html_to_plain

    raw = (text or "").strip()
    if not raw:
        return ""
    if "<" in raw and ">" in raw:
        try:
            return html_to_plain(raw).strip()
        except Exception:
            return _HTML_TAG_RE.sub("", raw).replace("\xa0", " ").strip()
    return raw


def _title_from(*parts: str | None, fallback: str) -> str:
    for part in parts:
        t = str(part or "").strip()
        if t:
            return t[:200]
    return fallback


def _sheet_by_title(note: dict[str, Any], title: str) -> dict[str, Any] | None:
    want = (title or "").strip()
    for row in sheets_store.list_sheets(note):
        if row.get("is_primary"):
            continue
        if str(row.get("title") or "").strip() == want:
            return row
    return None


def transcript_html(note: dict[str, Any]) -> str:
    if has_summary(note):
        extra = _sheet_by_title(note, TRANSCRIPT_SHEET_TITLE)
        if extra:
            return str(extra.get("body") or extra.get("description") or "")
    return str(note.get("body") or note.get("description") or "")


def summary_html(note: dict[str, Any]) -> str:
    if has_summary(note):
        return str(note.get("body") or note.get("description") or "")
    return ""


def _segments(note: dict[str, Any]) -> list[dict[str, Any]] | None:
    from assistant.stores import transcript_segments

    try:
        return transcript_segments.get(int(note["id"]))
    except Exception as e:
        print(f"[transcription_notes] segments read failed note={note.get('id')} err={e!r}")
        return None


def _speaker_map(meta: Any) -> dict[str, Any]:
    smap = meta.get("speaker_map") if isinstance(meta, dict) else None
    return dict(smap) if isinstance(smap, dict) else {}


def _same_text(a: str, b: str) -> bool:
    return " ".join(_plain(a).split()) == " ".join(_plain(b).split())


def _render_transcript(segments: list[dict[str, Any]], speaker_map: dict[str, Any]) -> str:
    from assistant.lib.transcript_render import render_html

    return render_html(segments, speaker_map)


def _unedited_segments(note: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Сегменты, если транскрипт руками не правили (иначе правки пользователя важнее)."""
    segs = _segments(note)
    if not segs:
        return None
    if not _same_text(transcript_html(note), _render_transcript(segs, _speaker_map(note.get("meta")))):
        return None
    return segs


def transcript_for_llm(note: dict[str, Any]) -> str:
    """Текст для саммари: «Имя:\n…» по сегментам с подтверждёнными именами, иначе — как в заметке."""
    from assistant.integrations.transcribe import formatted_from_segments
    from assistant.lib.transcript_render import speaker_names

    segs = _unedited_segments(note)
    if not segs:
        return _plain(transcript_html(note))
    names = speaker_names(_speaker_map(note.get("meta")))
    return formatted_from_segments(
        [{**s, "speaker": names.get(str(s.get("speaker") or ""), s.get("speaker") or "")} for s in segs]
    )


def _multi_speaker(segments: Any) -> bool:
    return len({str(s.get("speaker") or "") for s in (segments or []) if isinstance(s, dict)} - {""}) > 1


def _save_segments(note_id: int, segments: Any) -> None:
    if not segments:
        return
    from assistant.stores import transcript_segments

    try:
        transcript_segments.save(int(note_id), list(segments))
    except Exception as e:
        print(f"[transcription_notes] segments save failed note={note_id} err={e!r}")


def guess_speakers(owner: int | str, note_id: int) -> dict[str, Any]:
    """Догадки «Спикер N — это …» в meta.speaker_map (не подтверждённые; подтверждённые не трогаем)."""
    from assistant.lib.meeting_context import context_for_note
    from assistant.nlu import llm as llm_mod
    from assistant.nlu import speaker_guess

    oid = str(int(owner))
    note = notes_store.get_note(oid, int(note_id))
    segs = _segments(note) if note else None
    if not note or not _multi_speaker(segs):
        return {}
    ctx = context_for_note(int(oid), note)
    guesses = speaker_guess.guess(
        segs or [],
        context_payload={k: v for k, v in ctx.to_payload().items() if k != "previous"},
        people=ctx.people,
        chat=llm_mod._chat,
        model=llm_mod._model_summary(),
    )
    fresh = notes_store.get_note(oid, int(note_id))
    if not fresh:
        return {}
    meta = dict(fresh.get("meta") or {}) if isinstance(fresh.get("meta"), dict) else {}
    smap = _speaker_map(meta)
    for label, g in guesses.items():
        cur = smap.get(label)
        if isinstance(cur, dict) and (cur.get("confirmed") or float(cur.get("confidence") or 0) > float(g.get("confidence") or 0)):
            continue
        smap[label] = g
    meta["speaker_map"] = smap
    meta["speaker_guess_done"] = True
    notes_store.update_note(oid, int(note_id), meta=meta)
    return guesses


def schedule_speaker_guess(owner: int | str, note_id: int, segments: Any) -> None:
    """Угадать спикеров в фоне — к открытию «Кто есть кто» догадки уже готовы."""
    import os
    import threading

    if not _multi_speaker(segments) or os.getenv("SPEAKER_GUESS_ENABLED", "1").strip() == "0":
        return

    def run() -> None:
        try:
            guess_speakers(owner, note_id)
        except Exception as e:
            print(f"[speaker_guess] failed note={note_id} err={e!r}")

    threading.Thread(target=run, name=f"speaker-guess-{note_id}", daemon=True).start()


def _set_transcript_html(owner: str, note: dict[str, Any], body: str, *, actor: str) -> None:
    if has_summary(note):
        extra = _sheet_by_title(note, TRANSCRIPT_SHEET_TITLE)
        if extra:
            sheets_store.update_sheet(int(note["id"]), int(extra["id"]), body=body)
        return
    notes_store.update_note(owner, int(note["id"]), body=body, actor_user_id=actor)


def enrich(note: dict[str, Any]) -> dict[str, Any]:
    out = dict(note)
    out["is_transcription"] = True
    out["role"] = TRANSCRIPTION_ROLE
    out["has_summary"] = has_summary(out)
    meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}
    out["summary_generating"] = bool(meta.get("summary_generating"))
    preview_src = summary_html(out) if out["has_summary"] else transcript_html(out)
    out["preview"] = _plain(preview_src)[:280]
    return out


def list_for_user(user_id: int | str, *, limit: int = 120) -> list[dict[str, Any]]:
    return [enrich(n) for n in notes_store.list_transcription_notes(user_id, limit=limit)]


def _focus_summary_ready(owner: int | str, note_id: int) -> None:
    """Саммари готово — Фокус разбирает его сразу (задачи и договорённости из встречи)."""
    try:
        from assistant.services import proactive_ai

        proactive_ai.note_changed(owner, note_id, ready=True)
    except Exception as e:
        print(f"[proactive] summary_ready failed note={note_id} err={e!r}")
    try:
        from assistant.services import semantic_memory

        semantic_memory.note_changed(owner, delay=5)
    except Exception as e:
        print(f"[semantic] summary_ready failed note={note_id} err={e!r}")
    from assistant.services import meeting_share

    meeting_share.schedule_auto_share(owner, note_id)  # встреча уже привязана — участникам сразу


def create_transcript_only(
    user_id: int | str,
    *,
    title: str,
    transcript: str,
    meta: Optional[dict[str, Any]] = None,
    segments: Any = None,
) -> dict[str, Any]:
    meta = dict(meta or {})
    if _multi_speaker(segments):
        meta["speakers_detected"] = True
    body = _render_transcript(list(segments), _speaker_map(meta)) if segments else _as_html(transcript)
    note = notes_store.create_note(
        user_id,
        _title_from(title, fallback="Транскрипция"),
        body,
        role=TRANSCRIPTION_ROLE,
        meta=meta,
        primary_sheet_title=TRANSCRIPT_SHEET_TITLE,
    )
    _save_segments(int(note["id"]), segments)
    schedule_speaker_guess(user_id, int(note["id"]), segments)
    return enrich(note)


def _summary_body(summary: str, meta: Optional[dict[str, Any]]) -> str:
    """Тело саммари для заметки — сразу HTML, а у каждой задачи ID на её строке.

    Markdown переводим так же, как приложение (note_markdown); ID задачи пишется и в meta.tasks."""
    from assistant.lib import note_items
    from assistant.lib.note_markdown import markdown_to_html

    body = (summary or "").strip()
    if not body:
        return ""
    if note_items.is_html_body(body):
        out = body
    elif "<" in body and ">" in body and "<table" not in body:
        out = body  # чужой HTML без блоков — как раньше
    elif body.startswith("#") or "\n#" in body or "**" in body or "- [" in body or "\n- " in body or "<table" in body:
        out = markdown_to_html(body)
    else:
        out = _as_html(body)
    tasks = (meta or {}).get("tasks")
    if isinstance(tasks, list) and tasks and note_items.is_html_body(out):
        from assistant.services import meeting_tasks

        # отброшенные проверкой — не теряем: в таблицу, свёрнутыми «Возможно, не задача»
        meeting_tasks.merge_doubtful(meta)
        tasks = meta["tasks"]
        # задачи — таблица «Задача | Статус | Ответственный | Дедлайн», ID пункта на его строке
        out = meeting_tasks.replace_tasks_section(out, tasks)
        out = note_items.stamp_summary_tasks(out, tasks)
    return out


def _body_hash(body: str) -> str:
    import hashlib

    return hashlib.sha1((body or "").strip().encode("utf-8")).hexdigest()


def _with_body_hash(meta: Optional[dict[str, Any]], body_html: str) -> dict[str, Any]:
    """Хэш тела саммари v2: по нему понимаем, правили ли саммари руками."""
    out = dict(meta or {})
    if out.get("summary_version") == 2:
        out["summary_body_hash"] = _body_hash(body_html)
    return out


def create_with_summary(
    user_id: int | str,
    *,
    title: str,
    transcript: str,
    summary: str,
    meta: Optional[dict[str, Any]] = None,
    segments: Any = None,
) -> dict[str, Any]:
    meta = dict(meta or {})
    body = _summary_body(summary, meta)
    note = notes_store.create_note(
        user_id,
        _title_from(title, fallback="Саммари"),
        body,
        role=TRANSCRIPTION_ROLE,
        meta=_with_body_hash(meta, body),
        primary_sheet_title=SUMMARY_SHEET_TITLE,
    )
    transcript_body = (
        _render_transcript(list(segments), _speaker_map(meta)) if segments else _as_html(transcript)
    )
    sheets_store.create_sheet(note, TRANSCRIPT_SHEET_TITLE, body=transcript_body)
    _save_segments(int(note["id"]), segments)
    schedule_speaker_guess(user_id, int(note["id"]), segments)
    fresh = notes_store.get_note(user_id, int(note["id"])) or note
    _focus_summary_ready(user_id, int(note["id"]))
    return enrich(fresh)


def begin_make_summary(user_id: int | str, note_id: int) -> dict[str, Any]:
    uid = str(int(user_id))
    note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False)
    if not note or not is_transcription_note(note):
        raise TranscriptionNoteError("Это не файл транскрипции")
    owner = str(note.get("owner_user_id") or note.get("user_id") or uid)
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    if has_summary(note):
        if _plain(str(note.get("body") or "")) and not meta.get("summary_error"):
            if not meta.get("summary_generating"):
                raise TranscriptionNoteError("У этого файла уже есть саммари")
        meta["summary_generating"] = True
        meta.pop("summary_error", None)
        updated = notes_store.update_note(
            owner, int(note["id"]), meta=meta, actor_user_id=uid
        )
        return enrich(updated or note)
    transcript_body = str(note.get("body") or "")
    if not _plain(transcript_body):
        raise TranscriptionNoteError("Пустая транскрипция")
    existing = _sheet_by_title(note, TRANSCRIPT_SHEET_TITLE)
    if existing:
        sheets_store.update_sheet(
            int(note["id"]), int(existing["id"]), body=transcript_body
        )
    else:
        sheets_store.create_sheet(
            note, TRANSCRIPT_SHEET_TITLE, body=transcript_body
        )
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    meta["summary_generating"] = True
    meta.pop("summary_error", None)
    updated = notes_store.update_note(
        owner,
        int(note["id"]),
        body="",
        primary_sheet_title=SUMMARY_SHEET_TITLE,
        meta=meta,
        allow_empty_body=True,
        actor_user_id=uid,
    )
    if not updated:
        raise TranscriptionNoteError("Не удалось начать генерацию саммари")
    return enrich(updated)


def finish_make_summary(
    user_id: int | str,
    note_id: int,
    summary_text: str,
    *,
    extra_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    uid = str(int(user_id))
    note = notes_store.get_note(uid, int(note_id))
    if not note:
        note = notes_store.get_note_by_id(int(note_id))
    if not note or not is_transcription_note(note):
        raise TranscriptionNoteError("Это не файл транскрипции")
    owner = str(note.get("owner_user_id") or note.get("user_id") or uid)
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    meta.pop("summary_generating", None)
    meta.pop("summary_error", None)
    if extra_meta:
        meta.update(extra_meta)
    body = _summary_body(summary_text, meta)
    meta = _with_body_hash(meta, body)
    updated = notes_store.update_note(
        owner,
        int(note["id"]),
        body=body,
        primary_sheet_title=SUMMARY_SHEET_TITLE,
        meta=meta,
        allow_empty_body=True,
        actor_user_id=uid,
    )
    if not updated:
        raise TranscriptionNoteError("Не удалось сохранить саммари")
    _focus_summary_ready(owner, int(note["id"]))
    return enrich(updated)


def fail_make_summary(user_id: int | str, note_id: int, error: str) -> None:
    uid = str(int(user_id))
    note = notes_store.get_note(uid, int(note_id)) or notes_store.get_note_by_id(
        int(note_id)
    )
    if not note:
        return
    owner = str(note.get("owner_user_id") or note.get("user_id") or uid)
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    meta["summary_generating"] = False
    meta["summary_error"] = (error or "").strip()[:500]
    notes_store.update_note(owner, int(note["id"]), meta=meta, actor_user_id=uid)


def generate_summary_for_note(user_id: int | str, note_id: int) -> dict[str, Any] | None:
    """Синхронная генерация саммари (для фонового таска)."""
    from assistant.lib.summary_normalize import tasks_for_inject, v2_meta
    from assistant.lib.telegram_markdown import prepare_summary_markdown
    from assistant.nlu import llm as llm_mod

    uid = str(int(user_id))
    note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False)
    if not note or not is_transcription_note(note):
        return None
    if not has_summary(note):
        try:
            note = begin_make_summary(uid, int(note_id))
        except TranscriptionNoteError:
            return None
    transcript = transcript_for_llm(note)
    if not transcript:
        fail_make_summary(uid, int(note_id), "Пустая транскрипция")
        return None
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    speakers = bool(meta.get("speakers_detected")) or _multi_speaker(_segments(note))
    try:
        from assistant.lib.meeting_context import context_for_note

        try:
            owner_uid = str(note.get("owner_user_id") or note.get("user_id") or uid)
            meeting_ctx = context_for_note(int(owner_uid), note)
        except Exception as e:
            print(f"[transcription_notes] context failed note={note_id} err={e!r}")
            meeting_ctx = None
        summary_result = llm_mod.summarize_recording(
            transcript, speakers_detected=speakers, context=meeting_ctx
        )
        if not summary_result:
            raise RuntimeError("Не удалось сделать саммари")
        summary_text = str(summary_result.get("summary") or "").strip()
        display = prepare_summary_markdown(
            summary_text,
            tasks=tasks_for_inject(summary_result),
            source_url=str(meta.get("source_url") or "") or None,
            telegram_link=str(meta.get("telegram_link") or "") or None,
            headline=str(note.get("title") or "Саммари"),
        )
        render_params = {
            "source_url": str(meta.get("source_url") or "") or None,
            "telegram_link": str(meta.get("telegram_link") or "") or None,
            "headline": str(note.get("title") or "Саммари"),
        }
        extra = {
            "content_type": summary_result.get("content_type") or meta.get("content_type"),
            "main_topic": summary_result.get("main_topic") or meta.get("main_topic") or "",
            "participants": summary_result.get("participants") or meta.get("participants") or [],
            "tasks": summary_result.get("tasks") or [],
            "decisions": summary_result.get("decisions") or [],
            "deadlines": summary_result.get("deadlines") or [],
            "topics": summary_result.get("topics") or [],
            "open_questions": summary_result.get("open_questions") or [],
            "risks": summary_result.get("risks") or [],
            "tasks_dropped": summary_result.get("tasks_dropped") or [],
            "speakers_detected": speakers,
            **v2_meta(summary_result),
        }
        if extra.get("summary_version") == 2:
            extra["summary_render"] = render_params
        confirmed = {k: v for k, v in _speaker_map(meta).items() if isinstance(v, dict) and v.get("confirmed")}
        if confirmed or _speaker_map(meta):
            # подтверждённое пользователем (и догадки до саммари) не затираем картой из v2
            merged = {**_speaker_map(meta), **_speaker_map(extra)}
            merged.update(confirmed)
            extra["speaker_map"] = merged
        return finish_make_summary(uid, int(note_id), display, extra_meta=extra)
    except Exception as e:
        fail_make_summary(uid, int(note_id), str(e))
        return None


def confirm_speaker(
    user_id: int | str, note_id: int, label: str, name: str, *, is_me: bool = False
) -> dict[str, Any]:
    """«Спикер 2 — это Саша»: имя в транскрипте, у исполнителей и в тексте саммари без нового STT/LLM.

    Работает и до саммари. Транскрипт перерисовывается, если его не правили руками;
    саммари v2 — целиком, если его не правили (хэш совпадает), иначе (и в v1) —
    заменой «Спикер N» / прежнего имени в тексте. is_me — это смотрящий (вид «Диалог»).
    """
    from assistant.lib import summary_normalize as sn

    uid = str(int(user_id))
    note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False)
    if not note or not is_transcription_note(note):
        raise TranscriptionNoteError("Это не файл транскрипции")
    owner = str(note.get("owner_user_id") or note.get("user_id") or uid)
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    lab = sn.norm_speaker_label(label)
    nm = (name or "").strip()[:80]
    smap = _speaker_map(meta)
    segs = _unedited_segments(note)
    known = set(smap) | {str(s.get("speaker") or "") for s in (_segments(note) or [])}
    if lab not in known:
        raise TranscriptionNoteError("Нет такого спикера")
    prev = smap.get(lab) if isinstance(smap.get(lab), dict) else {}
    old_name = str(prev.get("name") or "").strip() if prev.get("confirmed") else ""
    if nm:
        smap[lab] = {**prev, "name": nm, "confidence": 1.0, "evidence": "подтверждено пользователем", "confirmed": True}
    else:
        smap[lab] = {"name": "", "confidence": 0.0, "evidence": "", "confirmed": True}
    smap[lab].pop("user_id", None)
    if is_me:
        for v in smap.values():  # «я» в записи один
            if isinstance(v, dict) and str(v.get("user_id") or "") == uid:
                v.pop("user_id", None)
        smap[lab]["user_id"] = uid
    meta["speaker_map"] = smap
    if segs:
        _set_transcript_html(owner, note, _render_transcript(segs, smap), actor=uid)
        note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False) or note
    body_kwargs: dict[str, Any] = {}
    data = sn.summary_data_from_meta(meta)
    if data:
        body_kwargs = _reassign_tasks(owner, note, meta, data, smap)
    elif nm:
        _rename_tasks(meta, lab, old_name, nm)
    rerendered = bool(body_kwargs)
    renamed = False
    if has_summary(note) and nm and not body_kwargs:
        cur = str(note.get("body") or "")
        new_body = _rename_in_summary(cur, lab, old_name, nm, meta)
        if new_body != cur:
            if meta.get("summary_body_hash") and _body_hash(cur) == meta.get("summary_body_hash"):
                meta["summary_body_hash"] = _body_hash(new_body)
            body_kwargs = {"body": new_body, "primary_sheet_title": SUMMARY_SHEET_TITLE}
            renamed = True
    from assistant.services import meeting_tasks

    cur = str(body_kwargs.get("body", note.get("body") or ""))
    if meeting_tasks.has_table(cur):
        # ячейки «Ответственный / Срок» таблицы задач — из meta, даже если текст саммари правили
        fresh = meeting_tasks.refresh_body(cur, meeting_tasks.rows({**note, "meta": meta}))
        if fresh != cur:
            if meta.get("summary_body_hash") and _body_hash(cur) == meta.get("summary_body_hash"):
                meta["summary_body_hash"] = _body_hash(fresh)
            body_kwargs = {**body_kwargs, "body": fresh, "primary_sheet_title": SUMMARY_SHEET_TITLE}
    updated = notes_store.update_note(
        owner, int(note["id"]), meta=meta, actor_user_id=uid, **body_kwargs
    )
    if not updated:
        raise TranscriptionNoteError("Не удалось сохранить")
    out = enrich(updated)
    out["summary_rerendered"] = rerendered
    out["summary_renamed"] = renamed
    out["transcript_rerendered"] = bool(segs)
    return out


def _label_re(label: str) -> "re.Pattern[str]":
    """«Спикер 2» как отдельное слово (не «Спикер 20»); и «SPEAKER_2» из старых текстов."""
    m = re.match(r"^Спикер (\d+)$", label)
    if m:
        return re.compile(rf"(?:Спикер|SPEAKER)[ _]?{m.group(1)}(?!\d)", re.IGNORECASE)
    return re.compile(rf"(?<!\w){re.escape(label)}(?!\w)")


def _rename_tasks(meta: dict[str, Any], label: str, old_name: str, name: str) -> None:
    """Исполнитель «Спикер N» (или прежнее подтверждённое имя) → новое имя в meta.tasks (v1)."""
    lab_re = _label_re(label)
    for t in meta.get("tasks") or []:
        if not isinstance(t, dict):
            continue
        who = str(t.get("assignee") or "").strip()
        if (who and lab_re.fullmatch(who)) or (old_name and who == old_name):
            t["assignee"] = name
        elif not who and lab_re.fullmatch(str(t.get("speaker") or "").strip()):
            t["assignee"] = name


def _rename_in_summary(body: str, label: str, old_name: str, name: str, meta: dict[str, Any]) -> str:
    """Замена в тексте саммари. «Спикер N» — везде (в тексте, не в разметке);
    прежнее имя — только в строках задач этого исполнителя (имя могло встречаться и по другому поводу)."""
    lab_re = _label_re(label)
    out = re.sub(r">([^<]+)<", lambda m: ">" + lab_re.sub(name, m.group(1)) + "<", ">" + body + "<")[1:-1]
    if old_name and old_name != name:
        ids = {str(t.get("id")) for t in meta.get("tasks") or [] if isinstance(t, dict) and t.get("id") and str(t.get("assignee") or "") == name}
        old_re = re.compile(rf"(?<!\w){re.escape(old_name)}(?!\w)")

        def fix_li(m: "re.Match[str]") -> str:
            if m.group(2) not in ids:
                return m.group(1)
            return re.sub(r">([^<]+)<", lambda x: ">" + old_re.sub(name, x.group(1)) + "<", m.group(1))

        out = re.sub(r'(<li[^>]*data-leo-item="([^"]+)"[^>]*>.*?</li>)', fix_li, out, flags=re.S)
    return out


def _reassign_tasks(
    owner: str, note: dict[str, Any], meta: dict[str, Any], data: dict[str, Any], smap: dict[str, Any]
) -> dict[str, Any]:
    """Исполнители задач саммари v2 по новой карте спикеров. Меняет meta; возвращает правку тела."""
    from assistant.lib import summary_normalize as sn
    from assistant.lib.meeting_context import context_for_note
    from assistant.lib.summary_render import render_summary_html
    from assistant.lib.telegram_markdown import prepare_summary_markdown

    ctx = context_for_note(int(owner), note)
    raw = [t for t in (sn.raw_task(x) for x in data.get("tasks") or []) if t]
    # в summary_data лежат уже разрешённые задачи: для «self» исполнителя
    # заново берём из карты спикеров, срок — исходной формулировкой
    for t, src in zip(raw, [x for x in data.get("tasks") or [] if isinstance(x, dict) and str(x.get("task") or "").strip()]):
        if t["assignee_basis"] == "self":
            t["assignee"] = ""
        if "deadline_text" in src:
            t["deadline"] = str(src.get("deadline_text") or "")
    tasks = sn.resolve_tasks(
        raw,
        speaker_map=smap,
        people=ctx.people,
        meeting_at=str(meta.get("meeting_at") or ctx.meeting_at),
        transcript=_plain(transcript_html(note)),
    )
    old_tasks = [x for x in meta.get("tasks") or [] if isinstance(x, dict)]
    for t, prev in zip(tasks, old_tasks):
        if isinstance(t, dict) and prev.get("id"):
            t["id"] = prev["id"]  # ID задачи переживает пересборку — связь с Фокусом и чипом не рвётся
    data["tasks"] = tasks
    data["speaker_map"] = smap
    meta["tasks"] = tasks
    cur_body = str(note.get("body") or "")
    if not (meta.get("summary_body_hash") and _body_hash(cur_body) == meta.get("summary_body_hash")):
        return {}
    params = meta.get("summary_render") if isinstance(meta.get("summary_render"), dict) else {}
    display = prepare_summary_markdown(render_summary_html(data), **params)
    new_body = _summary_body(display, meta)
    meta["summary_body_hash"] = _body_hash(new_body)
    return {"body": new_body, "primary_sheet_title": SUMMARY_SHEET_TITLE}


def speakers_view(user_id: int | str, note_id: int) -> dict[str, Any]:
    """Для «Кто есть кто»: спикеры с репликами и догадками, кандидаты в имена."""
    from assistant.lib import transcript_render as tr
    from assistant.lib.meeting_context import context_for_note

    uid = str(int(user_id))
    note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False)
    if not note or not is_transcription_note(note):
        raise TranscriptionNoteError("Это не файл транскрипции")
    owner = str(note.get("owner_user_id") or note.get("user_id") or uid)
    smap = _speaker_map(note.get("meta"))
    segs = _segments(note) or []
    labels: list[str] = []
    talk: dict[str, float] = {}
    for sg in segs:
        lab = str(sg.get("speaker") or "")
        if not lab:
            continue
        if lab not in labels:
            labels.append(lab)
        if sg.get("start") is not None and sg.get("end") is not None:
            talk[lab] = talk.get(lab, 0.0) + max(0.0, float(sg["end"]) - float(sg["start"]))
    if not labels:  # старые заметки: спикеры только из карты v2
        labels = sorted((k for k in smap if k.startswith("Спикер ")), key=lambda k: int(k[7:]) if k[7:].isdigit() else 0)
    quotes = tr.samples(segs, per_speaker=3, max_chars=160)
    speakers = []
    for lab in labels:
        info = smap.get(lab) if isinstance(smap.get(lab), dict) else {}
        speakers.append({
            "label": lab,
            "name": str(info.get("name") or ""),
            "confidence": float(info.get("confidence") or 0),
            "evidence": str(info.get("evidence") or ""),
            "confirmed": bool(info.get("confirmed")),
            "talk_sec": round(talk.get(lab, 0.0)),
            "is_me": _is_me(info, uid),
            "samples": [{"t": tr.fmt_time(q.get("start")), "text": q["text"]} for q in quotes.get(lab, [])],
        })
    # кандидаты: участники встречи, контакты, уже названные в этой записи
    candidates: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    try:
        ctx = context_for_note(int(owner), note)
        att_low = {a.lower() for a in ctx.attendees}
        for p in ctx.people:
            if p.get("owner"):
                continue
            nm = str(p.get("name") or "")
            em = str(p.get("email") or "")
            rows.append({"name": nm, "email": em, "attendee": nm.lower() in att_low or em.lower() in att_low})
        known = {r["name"].lower() for r in rows} | {r["email"].lower() for r in rows if r["email"]}
        for a in ctx.attendees:  # приглашённые без контакта
            if a.lower() not in known:
                rows.append({"name": a.split("@")[0] if "@" in a else a, "email": a if "@" in a else "", "attendee": True})
    except Exception as e:
        print(f"[transcription_notes] speakers ctx failed note={note_id} err={e!r}")
    rows += [{"name": str(v.get("name") or ""), "email": "", "attendee": False} for v in smap.values() if isinstance(v, dict)]
    seen: set[str] = set()
    for r in sorted(rows, key=lambda r: not r["attendee"]):
        n = r["name"].strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            candidates.append({**r, "name": n})
    return {
        "speakers": speakers,
        "candidates": candidates[:60],
        "can_merge": bool(segs),
        "guessing": bool(segs) and _multi_speaker(segs) and not (note.get("meta") or {}).get("speaker_guess_done"),
    }


def _is_me(info: Any, uid: str) -> bool:
    return isinstance(info, dict) and str(info.get("user_id") or "") == uid


def transcript_dialog(user_id: int | str, note_id: int) -> dict[str, Any]:
    """Реплики для вида «Диалог»: свои (спикер с пометкой «это я») — справа."""
    from assistant.lib import transcript_render as tr

    uid = str(int(user_id))
    note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False)
    if not note or not is_transcription_note(note):
        raise TranscriptionNoteError("Это не файл транскрипции")
    segs = _segments(note)
    if not segs:
        return {"turns": [], "has_me": False}
    smap = _speaker_map(note.get("meta"))
    names = tr.speaker_names(smap)
    turns = []
    for t in tr.turns(segs):
        lab = t["speaker"]
        turns.append({
            "label": lab,
            "name": names.get(lab, lab),
            "t": tr.fmt_time(t["start"]),
            "paras": t["paras"],
            "me": _is_me(smap.get(lab), uid),
        })
    return {"turns": turns, "has_me": any(t["me"] for t in turns), "edited": _unedited_segments(note) is None}


def merge_speakers(user_id: int | str, note_id: int, src: str, dst: str) -> dict[str, Any]:
    """Диаризация разделила одного человека на двоих: реплики «src» становятся репликами «dst»."""
    from assistant.lib import summary_normalize as sn

    uid = str(int(user_id))
    note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False)
    if not note or not is_transcription_note(note):
        raise TranscriptionNoteError("Это не файл транскрипции")
    owner = str(note.get("owner_user_id") or note.get("user_id") or uid)
    a, b = sn.norm_speaker_label(src), sn.norm_speaker_label(dst)
    segs = _segments(note) or []
    if a == b or not any(s.get("speaker") == a for s in segs) or not any(s.get("speaker") == b for s in segs):
        raise TranscriptionNoteError("Нет такого спикера")
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    smap = _speaker_map(meta)
    unedited = _unedited_segments(note) is not None
    segs = [{**s, "speaker": b} if s.get("speaker") == a else s for s in segs]
    _save_segments(int(note["id"]), segs)
    if a in smap:
        if not (isinstance(smap.get(b), dict) and smap[b].get("confirmed")) and isinstance(smap[a], dict) and smap[a].get("confirmed"):
            smap[b] = smap[a]
        smap.pop(a, None)
    meta["speaker_map"] = smap
    if unedited:
        _set_transcript_html(owner, note, _render_transcript(segs, smap), actor=uid)
    updated = notes_store.update_note(owner, int(note["id"]), meta=meta, actor_user_id=uid)
    if not updated:
        raise TranscriptionNoteError("Не удалось сохранить")
    return enrich(updated)


def _score_text(hay: str, words: list[str]) -> int:
    low = (hay or "").lower()
    if not low:
        return 0
    return sum(1 for w in words if w in low)


def _summary_field_text(meta: dict[str, Any], field: str | None) -> str:
    f = (field or "").strip().lower()
    if f == "decisions":
        return "\n".join(str(x) for x in (meta.get("decisions") or []) if str(x).strip())
    if f == "topics":
        return "\n".join(str(x) for x in (meta.get("topics") or []) if str(x).strip())
    if f == "participants":
        items = meta.get("participants") or []
        lines: list[str] = []
        for item in items:
            if isinstance(item, dict):
                lines.append(str(item.get("name") or "").strip())
            else:
                lines.append(str(item or "").strip())
        return "\n".join(x for x in lines if x)
    if f == "tasks":
        lines: list[str] = []
        for t in meta.get("tasks") or []:
            if not isinstance(t, dict):
                continue
            parts = [
                str(t.get("assignee") or "").strip(),
                str(t.get("task") or "").strip(),
                str(t.get("deadline") or "").strip(),
            ]
            line = " ".join(p for p in parts if p)
            if line:
                lines.append(line)
        return "\n".join(lines)
    if f == "main_topic":
        return str(meta.get("main_topic") or "").strip()
    return ""


def _note_as_search_item(note: dict[str, Any], *, as_summary: bool) -> dict[str, Any]:
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    text = summary_html(note) if as_summary else transcript_html(note)
    plain = _plain(text)
    headline = (
        str(meta.get("main_topic") or "").strip()
        or str(note.get("title") or "").strip()
        or ("Саммари" if as_summary else "Транскрипция")
    )
    return {
        "id": note.get("id"),
        "note_id": note.get("id"),
        "kind": "local",
        "ts_utc": str(note.get("updated_at") or note.get("created_at") or ""),
        "date_utc": str(note.get("updated_at") or note.get("created_at") or "")[:10],
        "headline": headline[:120],
        "text": text or plain,
        "meta": meta,
        "transcript": _plain(transcript_html(note)) or None,
        "source_url": str(meta.get("source_url") or "").strip() or None,
        "telegram_link": str(meta.get("telegram_link") or "").strip() or None,
        "is_transcription_note": True,
    }


def search_transcripts(
    user_id: int | str, query: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    from assistant.lib.usage_store import search_transcriptions as journal_search

    uid = str(int(user_id))
    q = (query or "").strip()
    if not uid or not q:
        return []
    words = [w for w in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if len(w) >= 2]
    if not words:
        words = [q.lower()]
    lim = max(1, min(int(limit), 20))
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for note in list_for_user(uid, limit=500):
        hay = f"{note.get('title') or ''}\n{_plain(transcript_html(note))}"
        score = _score_text(hay, words)
        if score <= 0:
            continue
        item = _note_as_search_item(note, as_summary=False)
        scored.append((score, str(item.get("ts_utc") or ""), item))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = [item for _, _, item in scored[:lim]]
    if len(out) < lim:
        for item in journal_search(uid, q, limit=lim):
            if any(str(x.get("id")) == str(item.get("id")) and not x.get("note_id") for x in out):
                continue
            out.append(item)
            if len(out) >= lim:
                break
    return out[:lim]


def search_summaries(
    user_id: int | str,
    query: str,
    *,
    field: str | None = None,
    assignee: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    from assistant.lib.usage_store import search_summaries as journal_search

    uid = str(int(user_id))
    q = (query or "").strip()
    if not uid or not q:
        return []
    words = [w for w in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if len(w) >= 2]
    if not words:
        words = [q.lower()]
    assignee_q = (assignee or "").strip().lower()
    lim = max(1, min(int(limit), 20))
    field_name = (field or "").strip().lower() or None
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for note in list_for_user(uid, limit=500):
        if not has_summary(note):
            continue
        meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
        summary_text = _plain(summary_html(note))
        hay = ""
        if field_name == "tasks":
            hay = _summary_field_text(meta, "tasks")
            if assignee_q:
                lines = [
                    line
                    for line in hay.split("\n")
                    if assignee_q in line.lower()
                ]
                hay = "\n".join(lines)
                if not hay:
                    continue
        elif field_name:
            hay = _summary_field_text(meta, field_name)
            if not hay and field_name != "summary":
                continue
        if not hay:
            hay = summary_text
            if meta.get("main_topic"):
                hay = f"{meta.get('main_topic')}\n{hay}"
        score = _score_text(hay, words)
        transcript_text = _plain(transcript_html(note))
        if transcript_text:
            score = max(score, _score_text(transcript_text, words))
        if score <= 0:
            continue
        item = _note_as_search_item(note, as_summary=True)
        item["field"] = field_name
        scored.append((score, str(item.get("ts_utc") or ""), item))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = [item for _, _, item in scored[:lim]]
    if len(out) < lim:
        for item in journal_search(
            uid, q, field=field, assignee=assignee, limit=lim
        ):
            out.append(item)
            if len(out) >= lim:
                break
    return out[:lim]


def get_latest_summary(user_id: int | str) -> dict[str, Any] | None:
    from assistant.lib.usage_store import get_latest_summary as journal_latest

    uid = str(int(user_id))
    for note in list_for_user(uid, limit=50):
        if has_summary(note):
            return _note_as_search_item(note, as_summary=True)
    return journal_latest(uid)
