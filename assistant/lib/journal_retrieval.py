"""Единый поиск по заметкам, транскрипциям и саммари для Q&A."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from assistant.lib.telegram_html import html_to_plain
from assistant.lib.usage_store import (
    get_latest_summary,
    search_summaries,
    search_transcriptions,
)
from assistant.stores import notes as notes_store

_CONTEXT_MAX_CHARS = 12000
_FRAGMENT_TEXT_MAX = 3500

_SOURCE_LABELS = {
    "note": "Заметка",
    "transcript": "Транскрипция",
    "summary": "Саммари",
}

_FOCUS_TO_FIELD = {
    "decisions": "decisions",
    "tasks": "tasks",
    "participants": "participants",
    "summary": "summary",
    "main_topic": "main_topic",
    "complaints": "summary",
    "general": None,
}

_JOURNAL_QA_MARKERS = re.compile(
    r"(?:встреч|договор|задач|жалоб|итог|саммари|транскрип|архив|"
    r"обсуждал|решил|поруч|клиент|интеграц|работ)",
    re.IGNORECASE,
)
_QUESTION_HINT = re.compile(
    r"(?:[?]|^(?:что|как|какие|кто|где|когда|перечисли|найди|расскаж))",
    re.IGNORECASE,
)
_ARCHIVE_PAST = re.compile(
    r"\b(?:были|был|была|было|договорил\w*|обсуждал\w*|решил\w*|"
    r"поручил\w*|озвучил\w*|поставил\w*|принял\w*|сделал\w*)\b",
    re.IGNORECASE,
)
_ARCHIVE_LAST_MEETING = re.compile(
    r"последн\w*\s+(?:встреч\w*|созвон\w*|митинг\w*)",
    re.IGNORECASE,
)
_ARCHIVE_ON_MEETING = re.compile(
    r"на\s+(?:последн\w*\s+)?(?:встреч\w*|созвон\w*|митинг\w*)",
    re.IGNORECASE,
)
_ARCHIVE_FOCUS = re.compile(
    r"\b(?:задач\w*|жалоб\w*|договор\w*|итог\w*|решен\w*|поручен\w*|"
    r"саммари|транскрип\w*|клиент\w*|интеграц\w*)\b",
    re.IGNORECASE,
)
_MEETING_WORD = re.compile(r"(?:встреч\w*|созвон\w*|митинг\w*)", re.IGNORECASE)
_CALENDAR_SCHEDULE = re.compile(
    r"(?:\b(?:сегодня|завтра|послезавтра|на\s+(?:этой|следующей)\s+недел)\b"
    r"|\b(?:у\s+меня|мои)\s+(?:встреч\w*|созвон\w*)"
    r"|\b(?:покажи|дай|список)\s+(?:встреч\w*|расписан\w*|календар\w*))",
    re.IGNORECASE,
)


def _is_calendar_schedule_query(text: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return False
    if not _MEETING_WORD.search(s) and not re.search(r"(?:расписан|календар)", s):
        return False
    if _ARCHIVE_PAST.search(s) or _ARCHIVE_LAST_MEETING.search(s):
        return False
    return bool(_CALENDAR_SCHEDULE.search(s))


def is_journal_archive_query(text: str) -> bool:
    """Вопрос о прошлых встречах/архиве — не про расписание календаря."""
    s = (text or "").strip().lower()
    if len(s) < 5:
        return False
    if _is_calendar_schedule_query(text):
        return False
    if _ARCHIVE_LAST_MEETING.search(s):
        return True
    if _ARCHIVE_ON_MEETING.search(s) and _ARCHIVE_FOCUS.search(s):
        return True
    if _ARCHIVE_PAST.search(s) and (
        _MEETING_WORD.search(s)
        or re.search(r"(?:саммари|транскрип\w*|архив)", s, re.IGNORECASE)
    ):
        return True
    if re.search(r"\bвчера\b", s, re.IGNORECASE) and (
        _ARCHIVE_FOCUS.search(s) or _MEETING_WORD.search(s)
    ):
        return True
    return journal_qa_likely_user_text(text)


@dataclass
class JournalFragment:
    source_type: str
    item_id: str | int
    ts_utc: str
    date_utc: str | None
    headline: str
    text: str
    score: float
    transcript_event_id: int | None = None


def journal_qa_likely_user_text(text: str) -> bool:
    """Эвристика: похоже ли сообщение на вопрос по архиву."""
    s = (text or "").strip()
    if len(s) < 5:
        return False
    if _is_calendar_schedule_query(text):
        return False
    return bool(_QUESTION_HINT.search(s) and _JOURNAL_QA_MARKERS.search(s))


def _item_date_iso(item: dict[str, Any], *, source_type: str) -> str | None:
    if source_type == "note":
        raw = str(item.get("updated_at") or item.get("created_at") or "")
        return raw[:10] if len(raw) >= 10 else None
    raw = str(item.get("date_utc") or item.get("ts_utc") or "")
    return raw[:10] if len(raw) >= 10 else None


def _in_date_range(
    item_date: str | None,
    *,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    if not date_from and not date_to:
        return True
    if not item_date:
        return not (date_from or date_to)
    if date_from and item_date < date_from[:10]:
        return False
    if date_to and item_date > date_to[:10]:
        return False
    return True


def _plain_text(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    if "<" in body and ">" in body:
        return html_to_plain(body).strip()
    return body


def _note_fragment(note: dict[str, Any], *, score: float) -> JournalFragment:
    title = str(note.get("title") or "Заметка").strip()
    body = _plain_text(str(note.get("body") or ""))
    text = f"{title}\n{body}".strip()
    ts = str(note.get("updated_at") or note.get("created_at") or "")
    return JournalFragment(
        source_type="note",
        item_id=int(note.get("id") or 0),
        ts_utc=ts,
        date_utc=_item_date_iso(note, source_type="note"),
        headline=title[:120],
        text=text[:_FRAGMENT_TEXT_MAX],
        score=score,
    )


def _transcript_fragment(item: dict[str, Any], *, score: float) -> JournalFragment:
    text = _plain_text(str(item.get("text") or ""))
    headline = str(item.get("headline") or "Транскрипция").strip()[:120]
    return JournalFragment(
        source_type="transcript",
        item_id=int(item.get("id") or 0),
        ts_utc=str(item.get("ts_utc") or ""),
        date_utc=str(item.get("date_utc") or "")[:10] or None,
        headline=headline,
        text=text[:_FRAGMENT_TEXT_MAX],
        score=score,
    )


def _summary_fragment(item: dict[str, Any], *, score: float) -> JournalFragment:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    summary_text = _plain_text(str(item.get("text") or ""))
    raw_json_transcript = str(item.get("transcript") or "").strip()
    transcript_event_id = meta.get("transcript_event_id")
    tid: int | None = None
    try:
        if transcript_event_id is not None:
            tid = int(transcript_event_id)
    except (TypeError, ValueError):
        tid = None
    if raw_json_transcript:
        transcript_excerpt = raw_json_transcript[:1500]
    else:
        transcript_excerpt = ""
    parts = [summary_text]
    if transcript_excerpt and transcript_excerpt not in summary_text:
        parts.append(f"[Фрагмент транскрипта]\n{transcript_excerpt}")
    text = "\n\n".join(p for p in parts if p).strip()
    headline = str(item.get("headline") or "Саммари").strip()[:120]
    return JournalFragment(
        source_type="summary",
        item_id=int(item.get("id") or 0),
        ts_utc=str(item.get("ts_utc") or ""),
        date_utc=str(item.get("date_utc") or "")[:10] or None,
        headline=headline,
        text=text[:_FRAGMENT_TEXT_MAX],
        score=score,
        transcript_event_id=tid,
    )


def _fetch_latest_meeting_fragments(user_id: str) -> list[JournalFragment]:
    latest = get_latest_summary(user_id)
    if not latest:
        return []
    meta = latest.get("meta") if isinstance(latest.get("meta"), dict) else {}
    transcript_event_id = meta.get("transcript_event_id")
    return [
        _summary_fragment(latest, score=10.0),
    ]


def _normalize_parsed(parsed: dict[str, Any] | None, *, fallback_query: str) -> dict[str, Any]:
    p = dict(parsed or {})
    search_query = str(p.get("search_query") or p.get("query") or "").strip()
    if not search_query:
        search_query = fallback_query.strip()
    focus = str(p.get("focus") or "general").strip().lower()
    if focus not in _FOCUS_TO_FIELD:
        focus = "general"
    sources = p.get("sources")
    if not isinstance(sources, list) or not sources:
        sources = ["notes", "transcripts", "summaries"]
    sources = [str(s).strip().lower() for s in sources]
    relative = str(p.get("relative") or "").strip().lower() or None
    if relative in ("null", "none", ""):
        relative = None
    return {
        "search_query": search_query,
        "focus": focus,
        "assignee": str(p.get("assignee") or "").strip(),
        "date_from": str(p.get("date_from") or "").strip()[:10] or None,
        "date_to": str(p.get("date_to") or "").strip()[:10] or None,
        "relative": relative,
        "sources": sources,
    }


def retrieve_journal_context(
    user_id: str | int,
    parsed_query: dict[str, Any] | None,
    *,
    original_question: str = "",
    limit: int = 8,
) -> tuple[list[JournalFragment], str]:
    """Ищет релевантные фрагменты и собирает текстовый контекст для LLM."""
    uid = str(int(user_id))
    parsed = _normalize_parsed(parsed_query, fallback_query=original_question)
    search_q = parsed["search_query"]
    focus = parsed["focus"]
    field = _FOCUS_TO_FIELD.get(focus)
    assignee = parsed["assignee"] or None
    date_from = parsed["date_from"]
    date_to = parsed["date_to"]
    relative = parsed["relative"]
    sources = parsed["sources"]
    lim = max(1, min(int(limit), 12))

    fragments: list[JournalFragment] = []

    if relative == "latest":
        fragments.extend(_fetch_latest_meeting_fragments(uid))

    per_source_limit = max(lim, 5)
    if "notes" in sources and search_q:
        for i, note in enumerate(notes_store.search_notes(uid, search_q, limit=per_source_limit)):
            score = float(per_source_limit - i)
            fragments.append(_note_fragment(note, score=score))

    if "transcripts" in sources and search_q:
        for i, item in enumerate(
            search_transcriptions(uid, search_q, limit=per_source_limit)
        ):
            score = float(per_source_limit - i)
            fragments.append(_transcript_fragment(item, score=score))

    if "summaries" in sources and search_q:
        for i, item in enumerate(
            search_summaries(
                uid,
                search_q,
                field=field,
                assignee=assignee,
                limit=per_source_limit,
            )
        ):
            score = float(per_source_limit - i + (1 if focus in ("decisions", "tasks") else 0))
            fragments.append(_summary_fragment(item, score=score))

    if relative == "latest" and fragments:
        latest_date = max(
            (f.date_utc or f.ts_utc[:10] for f in fragments if f.date_utc or f.ts_utc),
            default=None,
        )
        if latest_date:
            fragments = [
                f
                for f in fragments
                if (f.date_utc or f.ts_utc[:10] or "") == latest_date
            ]

    if date_from or date_to:
        fragments = [
            f
            for f in fragments
            if _in_date_range(f.date_utc or f.ts_utc[:10], date_from=date_from, date_to=date_to)
        ]

    linked_transcript_ids = {
        f.transcript_event_id
        for f in fragments
        if f.source_type == "summary" and f.transcript_event_id
    }
    deduped: list[JournalFragment] = []
    seen: set[tuple[str, str | int]] = set()
    for f in fragments:
        if f.source_type == "transcript" and f.item_id in linked_transcript_ids:
            continue
        key = (f.source_type, f.item_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)

    deduped.sort(key=lambda f: (f.score, f.ts_utc), reverse=True)
    top = deduped[:lim]
    context = build_journal_context_text(top)
    return top, context


def build_journal_context_text(fragments: list[JournalFragment]) -> str:
    """Собирает контекст для LLM с ограничением по длине."""
    if not fragments:
        return ""
    parts: list[str] = []
    total = 0
    for frag in fragments:
        label = _SOURCE_LABELS.get(frag.source_type, frag.source_type)
        date_part = frag.date_utc or frag.ts_utc[:10] or "—"
        header = f"[{label} | {date_part} | {frag.headline}]"
        body = frag.text.strip()
        block = f"{header}\n{body}"
        if total + len(block) + 6 > _CONTEXT_MAX_CHARS:
            remaining = _CONTEXT_MAX_CHARS - total - len(header) - 10
            if remaining > 200:
                block = f"{header}\n{body[:remaining]}…"
                parts.append(block)
            break
        parts.append(block)
        total += len(block) + 6
    return "\n\n---\n\n".join(parts)
