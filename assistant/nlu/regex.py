"""Regex-маршрутизация без зависимости от монолита."""

from __future__ import annotations

import re

from assistant.config import (
    NOTE_INTRO_PHRASES,
    NOTE_SEARCH_INTRO_RE,
    SUMMARY_INTRO_PHRASES,
    SUMMARY_SEARCH_INTRO_RE,
    TELEMOST_INSTANT_PHRASES,
    TRANSCRIBE_INTRO_PHRASES,
    TRANSCRIBE_SEARCH_INTRO_RE,
    ZOOM_INSTANT_PHRASES,
)
from assistant.lib.calendar_intent_heuristics import calendar_detect_route_kind
from assistant.lib.journal_retrieval import is_journal_archive_query
from assistant.lib.knowledge_retrieval import is_knowledge_base_query
from assistant.nlu.intent_router import Route


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def parse_bitrix_intent(text: str) -> str | None:
    s = _norm(text)
    if not s:
        return None
    if re.search(r"\b(битрикс|bitrix24|bitrix)\b", s):
        return (text or "").strip()
    if re.search(r"\bсделк", s):
        return (text or "").strip()
    if re.search(r"\bворонк", s):
        return (text or "").strip()
    if re.search(r"\bстади[яиюей]\b", s) and re.search(r"\bворонк", s):
        return (text or "").strip()
    if re.search(r"отвяжи.*от группы", s):
        return (text or "").strip()
    if re.search(r"пользовательск(ое|ие|ого|ая)\s+пол", s):
        return (text or "").strip()
    if re.search(r"\bзадач", s) and re.search(
        r"\b(создай|создать|поставь|найди|найти|поиск|статус|дедлайн|крайн|чек-?лист|результат|описани|измени|убери|удали|добавь|перемест|переимен|дай|покажи|расскажи|выведи|получи|получить|что\s+в)\b",
        s,
    ):
        return (text or "").strip()
    if re.search(r"\bописани[ея]?\s+задач", s):
        return (text or "").strip()
    if re.search(r"\bзадач[аиеу]?\s+по\b", s):
        return (text or "").strip()
    if re.search(r"\bчек-?лист", s):
        return (text or "").strip()
    return None


def parse_remind_intent(text: str) -> str | None:
    s = _norm(text)
    if not s:
        return None
    if re.search(r"\b(напомни|напоминание|не забудь|уведоми)\b", s):
        return (text or "").strip()
    return None


def parse_transcribe_search_intent(text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    m = TRANSCRIBE_SEARCH_INTRO_RE.match(s)
    if m:
        q = (m.group(1) or "").strip(" :—-.,!?")
        return q or s
    return None


def parse_note_search_intent(text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    m = NOTE_SEARCH_INTRO_RE.match(s)
    if m:
        q = (m.group(1) or "").strip(" :—-.,!?")
        return q or s
    return None


def parse_note_intent(text: str) -> str | None:
    s = _norm(text)
    for phrase in NOTE_INTRO_PHRASES:
        if s.startswith(phrase):
            rest = (text or "").strip()[len(phrase) :].lstrip(" :,—-")
            return rest or (text or "").strip()
    return None


def parse_transcribe_intent(text: str) -> bool:
    s = _norm(text)
    if not s:
        return False
    for phrase in TRANSCRIBE_INTRO_PHRASES:
        if s == phrase:
            return True
        if s.startswith(phrase + " ") or s.startswith(phrase + ","):
            return True
        if len(phrase) >= 4 and re.search(
            rf"(?:^|\s){re.escape(phrase)}(?:\s|$|[,.:;!?])", s
        ):
            return True
    return False


_LATEST_SUMMARY_RE = re.compile(
    r"последн\w*\s+саммари|саммари\s+последн",
    re.IGNORECASE,
)


def parse_latest_summary_intent(text: str) -> bool:
    s = (text or "").strip()
    return bool(s and _LATEST_SUMMARY_RE.search(s))


def parse_summary_search_intent(text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    m = SUMMARY_SEARCH_INTRO_RE.match(s)
    if m:
        q = (m.group(1) or "").strip(" :—-.,!?")
        return q or s
    return None


def parse_summary_intent(text: str) -> bool:
    s = _norm(text)
    if not s:
        return False
    for phrase in SUMMARY_INTRO_PHRASES:
        if s == phrase:
            return True
        if s.startswith(phrase + " ") or s.startswith(phrase + ","):
            return True
        if len(phrase) >= 4 and re.search(
            rf"(?:^|\s){re.escape(phrase)}(?:\s|$|[,.:;!?])", s
        ):
            return True
    return False


_ZOOM_MARKERS = re.compile(r"\b(зум|zoom)\b", re.IGNORECASE)
_ZOOM_UPDATE = re.compile(
    r"\b(перенес\w*|сдвин\w*|измен\w*|передвин\w*|reschedule\w*|move|shift|change)\b.*\b(зум|zoom)\b"
    r"|\b(зум|zoom)\b.*\b(перенес\w*|сдвин\w*|измен\w*|передвин\w*|reschedule\w*|move|shift|change)\b",
    re.IGNORECASE,
)
_ZOOM_DELETE = re.compile(
    r"\b(удал\w*|отмен\w*|сотр\w*|delete|remove|cancel)\b.*\b(зум|zoom)\b"
    r"|\b(зум|zoom)\b.*\b(удал\w*|отмен\w*|сотр\w*|delete|remove|cancel)\b",
    re.IGNORECASE,
)
_ZOOM_AMEND = re.compile(
    r"\b(удал\w*|отмен\w*|сотр\w*|delete|remove|cancel|перенес\w*|сдвин\w*|измен\w*|"
    r"передвин\w*|reschedule\w*|move|shift|change)\b",
    re.IGNORECASE,
)
_ZOOM_RECORD = re.compile(
    r"\b(запиш\w*|запис\w*|запип\w*|запись|record|транскриб\w*|саммари)\b",
    re.IGNORECASE,
)
_ZOOM_URL_ONLY = re.compile(r"^https?://\S+$", re.IGNORECASE)
_MEETING_TOPIC = re.compile(r"\b(встреч\w*|созвон\w*|митинг\w*)\b", re.IGNORECASE)
_CALENDAR_AMEND = re.compile(
    r"\b(перенес\w*|передвин\w*|перестав\w*|измени\w*|поменя\w*|"
    r"сдвин\w*|обнови\w*|удали\w*|отмени\w*|сотри\w*)\b",
    re.IGNORECASE,
)


def parse_zoom_record_route(text: str) -> Route | None:
    from assistant.lib.urls import extract_urls
    from assistant.lib.zoom_link import find_zoom_url

    s = (text or "").strip()
    if not s:
        return None
    urls = extract_urls(s)
    link = find_zoom_url(urls)
    if link is None:
        return None
    norm = _norm(s)
    if _CALENDAR_AMEND.search(norm) and _MEETING_TOPIC.search(norm):
        return None
    if _ZOOM_RECORD.search(norm) or _ZOOM_URL_ONLY.match(s.strip()):
        return Route(
            skill="zoom_record",
            sub_intent="record",
            body=s,
            confidence=1.0,
            source="regex",
        )
    # Ссылка join/... — существующая встреча: записать, а не создать новую.
    return Route(
        skill="zoom_record",
        sub_intent="record",
        body=s,
        confidence=1.0,
        source="regex",
    )


def parse_zoom_route(text: str) -> Route | None:
    s = _norm(text)
    if not s or not _ZOOM_MARKERS.search(s):
        return None
    if _ZOOM_DELETE.search(s):
        return Route(
            skill="calendar",
            sub_intent="zoom_delete",
            body=(text or "").strip(),
            confidence=1.0,
            source="regex",
            calendar_kind="zoom",
        )
    if _ZOOM_UPDATE.search(s):
        return Route(
            skill="calendar",
            sub_intent="zoom_update",
            body=(text or "").strip(),
            confidence=1.0,
            source="regex",
            calendar_kind="zoom",
        )
    if any(s == p or s.startswith(p + " ") or s.startswith(p + ",") for p in ZOOM_INSTANT_PHRASES):
        return Route(
            skill="calendar",
            sub_intent="zoom",
            body=(text or "").strip(),
            confidence=1.0,
            source="regex",
            calendar_kind="zoom",
        )
    if len(s) <= 12 and _ZOOM_MARKERS.search(s) and not _ZOOM_AMEND.search(s) and not re.search(
        r"\b(встреч|календар|сегодня|завтра|\d{1,2}:\d{2})\b", s
    ):
        return Route(
            skill="calendar",
            sub_intent="zoom",
            body=(text or "").strip(),
            confidence=1.0,
            source="regex",
            calendar_kind="zoom",
        )
    return None


def parse_zoom_instant(text: str) -> bool:
    r = parse_zoom_route(text)
    return r is not None and r.sub_intent == "zoom"


_TELEMOST_MARKERS = re.compile(r"\b(телемост|telemost)\b", re.IGNORECASE)
_TELEMOST_UPDATE = re.compile(
    r"\b(перенес\w*|сдвин\w*|измен\w*|передвин\w*)\b.*\b(телемост|telemost)\b"
    r"|\b(телемост|telemost)\b.*\b(перенес\w*|сдвин\w*|измен\w*|передвин\w*)\b",
    re.IGNORECASE,
)
_TELEMOST_DELETE = re.compile(
    r"\b(удал\w*|отмен\w*|сотр\w*)\b.*\b(телемост|telemost)\b"
    r"|\b(телемост|telemost)\b.*\b(удал\w*|отмен\w*)\b",
    re.IGNORECASE,
)


def parse_telemost_route(text: str) -> Route | None:
    s = _norm(text)
    if not s or not _TELEMOST_MARKERS.search(s):
        return None
    if _TELEMOST_DELETE.search(s):
        return Route(
            skill="calendar",
            sub_intent="telemost_delete",
            body=(text or "").strip(),
            confidence=1.0,
            source="regex",
            calendar_kind="telemost",
        )
    if _TELEMOST_UPDATE.search(s):
        return Route(
            skill="calendar",
            sub_intent="telemost_update",
            body=(text or "").strip(),
            confidence=1.0,
            source="regex",
            calendar_kind="telemost",
        )
    if any(
        s == p or s.startswith(p + " ") or s.startswith(p + ",")
        for p in TELEMOST_INSTANT_PHRASES
    ):
        return Route(
            skill="calendar",
            sub_intent="telemost",
            body=(text or "").strip(),
            confidence=1.0,
            source="regex",
            calendar_kind="telemost",
        )
    if len(s) <= 16 and _TELEMOST_MARKERS.search(s) and not re.search(
        r"\b(встреч|календар|сегодня|завтра|\d{1,2}:\d{2})\b", s
    ):
        return Route(
            skill="calendar",
            sub_intent="telemost",
            body=(text or "").strip(),
            confidence=1.0,
            source="regex",
            calendar_kind="telemost",
        )
    return None


def parse_telemost_instant(text: str) -> bool:
    r = parse_telemost_route(text)
    return r is not None and r.sub_intent == "telemost"


def regex_route(text: str) -> Route | None:
    s = (text or "").strip()
    if not s:
        return None

    remind = parse_remind_intent(s)
    if remind is not None:
        return Route(
            skill="reminder",
            sub_intent="create",
            body=remind,
            confidence=1.0,
            source="regex",
        )

    transcribe_search = parse_transcribe_search_intent(s)
    if transcribe_search is not None:
        return Route(
            skill="transcribe_search",
            sub_intent="search",
            body=transcribe_search,
            confidence=1.0,
            source="regex",
        )

    summary_search = parse_summary_search_intent(s)
    if summary_search is not None:
        return Route(
            skill="summary_search",
            sub_intent="search",
            body=summary_search,
            confidence=1.0,
            source="regex",
        )

    if parse_latest_summary_intent(s):
        return Route(
            skill="summary_latest",
            sub_intent="latest",
            body=s,
            confidence=1.0,
            source="regex",
        )

    if parse_summary_intent(s):
        return Route(
            skill="summary",
            sub_intent="default",
            body=s,
            confidence=1.0,
            source="regex",
        )

    if parse_transcribe_intent(s):
        return Route(
            skill="transcribe",
            sub_intent="default",
            body=s,
            confidence=1.0,
            source="regex",
        )

    zoom_record_r = parse_zoom_record_route(s)
    if zoom_record_r is not None:
        return zoom_record_r

    zoom_r = parse_zoom_route(s)
    if zoom_r is not None:
        return zoom_r

    telemost_r = parse_telemost_route(s)
    if telemost_r is not None:
        return telemost_r

    note_search = parse_note_search_intent(s)
    if note_search is not None:
        return Route(
            skill="note_search",
            sub_intent="search",
            body=note_search,
            confidence=1.0,
            source="regex",
        )

    note = parse_note_intent(s)
    if note is not None:
        return Route(
            skill="todoist_note",
            sub_intent="create",
            body=note,
            confidence=1.0,
            source="regex",
        )

    bitrix = parse_bitrix_intent(s)
    if bitrix is not None:
        return Route(
            skill="bitrix",
            sub_intent="command",
            body=bitrix,
            confidence=1.0,
            source="regex",
        )

    if is_knowledge_base_query(s):
        return Route(
            skill="knowledge_qa",
            sub_intent="search",
            body=s,
            confidence=1.0,
            source="regex",
        )

    if is_journal_archive_query(s):
        return Route(
            skill="journal_qa",
            sub_intent="archive",
            body=s,
            confidence=1.0,
            source="regex",
        )

    cal_kind = calendar_detect_route_kind(s)
    if cal_kind:
        return Route(
            skill="calendar",
            sub_intent=cal_kind,
            body=s,
            confidence=1.0,
            source="regex",
            calendar_kind=cal_kind,
        )

    return None
