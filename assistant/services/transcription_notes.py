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


def create_transcript_only(
    user_id: int | str,
    *,
    title: str,
    transcript: str,
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    note = notes_store.create_note(
        user_id,
        _title_from(title, fallback="Транскрипция"),
        _as_html(transcript),
        role=TRANSCRIPTION_ROLE,
        meta=meta or {},
        primary_sheet_title=TRANSCRIPT_SHEET_TITLE,
    )
    return enrich(note)


def _body_hash(body: str) -> str:
    import hashlib

    return hashlib.sha1((body or "").strip().encode("utf-8")).hexdigest()


def _with_body_hash(meta: Optional[dict[str, Any]], summary: str) -> dict[str, Any]:
    """Хэш тела саммари v2: по нему понимаем, правили ли саммари руками."""
    out = dict(meta or {})
    if out.get("summary_version") == 2:
        out["summary_body_hash"] = _body_hash(_as_html(summary))
    return out


def create_with_summary(
    user_id: int | str,
    *,
    title: str,
    transcript: str,
    summary: str,
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    note = notes_store.create_note(
        user_id,
        _title_from(title, fallback="Саммари"),
        _as_html(summary),
        role=TRANSCRIPTION_ROLE,
        meta=_with_body_hash(meta, summary),
        primary_sheet_title=SUMMARY_SHEET_TITLE,
    )
    sheets_store.create_sheet(
        note, TRANSCRIPT_SHEET_TITLE, body=_as_html(transcript)
    )
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
    meta = _with_body_hash(meta, summary_text)
    updated = notes_store.update_note(
        owner,
        int(note["id"]),
        body=_as_html(summary_text),
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
    transcript = _plain(transcript_html(note))
    if not transcript:
        fail_make_summary(uid, int(note_id), "Пустая транскрипция")
        return None
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    speakers = bool(meta.get("speakers_detected"))
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
            "speakers_detected": speakers,
            **v2_meta(summary_result),
        }
        if extra.get("summary_version") == 2:
            extra["summary_render"] = render_params
        return finish_make_summary(uid, int(note_id), display, extra_meta=extra)
    except Exception as e:
        fail_make_summary(uid, int(note_id), str(e))
        return None


def confirm_speaker(user_id: int | str, note_id: int, label: str, name: str) -> dict[str, Any]:
    """«Спикер 2 — это Саша»: обновить карту спикеров и исполнителей без нового STT/LLM.

    Тело саммари пересобирается, только если его не правили руками (хэш совпадает).
    """
    from assistant.lib import summary_normalize as sn
    from assistant.lib.meeting_context import context_for_note
    from assistant.lib.summary_render import render_summary_html
    from assistant.lib.telegram_markdown import prepare_summary_markdown

    uid = str(int(user_id))
    note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False)
    if not note or not is_transcription_note(note):
        raise TranscriptionNoteError("Это не файл транскрипции")
    owner = str(note.get("owner_user_id") or note.get("user_id") or uid)
    meta = dict(note.get("meta") or {}) if isinstance(note.get("meta"), dict) else {}
    data = sn.summary_data_from_meta(meta)
    if not data:
        raise TranscriptionNoteError("Для этого саммари нет карты спикеров")
    lab = sn.norm_speaker_label(label)
    nm = (name or "").strip()[:80]
    smap = dict(meta.get("speaker_map") or {})
    if lab not in smap:
        raise TranscriptionNoteError("Нет такого спикера")
    if nm:
        smap[lab] = {"name": nm, "confidence": 1.0, "evidence": "подтверждено пользователем", "confirmed": True}
    else:
        smap[lab] = {"name": "", "confidence": 0.0, "evidence": "", "confirmed": True}
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
    data["tasks"] = tasks
    data["speaker_map"] = smap
    meta["speaker_map"] = smap
    meta["tasks"] = tasks
    body_kwargs: dict[str, Any] = {}
    cur_body = str(note.get("body") or "")
    if meta.get("summary_body_hash") and _body_hash(cur_body) == meta.get("summary_body_hash"):
        params = meta.get("summary_render") if isinstance(meta.get("summary_render"), dict) else {}
        display = prepare_summary_markdown(render_summary_html(data), **params)
        body_kwargs = {"body": _as_html(display), "primary_sheet_title": SUMMARY_SHEET_TITLE}
        meta["summary_body_hash"] = _body_hash(_as_html(display))
    updated = notes_store.update_note(
        owner, int(note["id"]), meta=meta, actor_user_id=uid, **body_kwargs
    )
    if not updated:
        raise TranscriptionNoteError("Не удалось сохранить")
    out = enrich(updated)
    out["summary_rerendered"] = bool(body_kwargs)
    return out


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
