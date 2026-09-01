"""Эвристики календаря: день из «завтра» и режим free_slots vs список встреч."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from assistant.lib.calendar_event_utils import (
    calendar_date_from_weekday_phrase,
    calendar_is_meeting_overview_query,
    calendar_try_resolve_clarify,
)
from assistant.lib.journal_retrieval import is_journal_archive_query

__all__ = [
    "calendar_apply_relative_day",
    "calendar_detect_route_kind",
    "calendar_free_slots_hide_meetings",
    "calendar_is_meeting_overview_query",
    "calendar_likely_user_text",
    "calendar_try_resolve_clarify",
    "calendar_user_text_blob",
]

# Роутинг: корни слов, не точные фразы («встрече» / «встречу» → встреч\w*).
_CALENDAR_TOPIC = r"(?:встреч\w*|созвон\w*|митинг\w*|календар\w*|расписан\w*|слот\w*|событ\w*|переговор\w*)"
_CREATE_VERBS = (
    r"(?:поставь|создай|заведи|запиши|запланируй|добавь|назначь|забронируй|"
    r"организуй|организ\w*|собери|проведи|провест\w*|сделай)"
)
_UPDATE_VERBS = r"(?:перенес\w*|передвин\w*|перестав\w*|измени\w*|поменя\w*|сдвинь\w*|обнови\w*)"
_DELETE_VERBS = r"(?:удали\w*|отмени\w*|сотри\w*)"
_OTHER_SKILL_PREFIX = re.compile(
    r"^(?:создай|поставь|запланируй|заведи|запиши|добавь|организуй|удали|отмени|перенеси|измени|поменяй)\s+"
    r"(?:задач\w*|таск\w*|заметк\w*|напоминан\w*)\b",
    re.IGNORECASE,
)
_HAS_CLOCK_TIME = re.compile(
    r"(?:\bв\s+)?\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s*(?:час(?:а|ов)?|ч)\b",
    re.IGNORECASE,
)
_HAS_ATTENDEES = re.compile(
    r"\bс\s+[A-Za-zА-ЯЁа-яё][\wА-ЯЁа-яё.-]*(?:\s+и\s+[A-Za-zА-ЯЁа-яё][\wА-ЯЁа-яё.-]*)*",
    re.IGNORECASE,
)
_HAS_THEME = re.compile(
    r"\bтем[аыуе]\b|«[^»]{3,}»|\"[^\"]{3,}\"",
    re.IGNORECASE,
)


def _looks_like_schedule_create(sl: str) -> bool:
    """Создание встречи: глагол или (тема/участники + конкретное время)."""
    if re.search(_CREATE_VERBS, sl) and not re.search(_UPDATE_VERBS, sl) and not re.search(
        _DELETE_VERBS, sl
    ):
        return True
    if not re.search(_CALENDAR_TOPIC, sl):
        return False
    if not _HAS_CLOCK_TIME.search(sl):
        return False
    if re.search(r"(?:^|\s)(?:покажи|дай|что|какие|расскаж|мои|моё|мое)\b", sl):
        return False
    return bool(_HAS_ATTENDEES.search(sl) or _HAS_THEME.search(sl))


def _calendar_sl(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def calendar_detect_amend_kind(text: str) -> str | None:
    """update/delete по глаголу без слова «встреча» — для реплая на сообщение бота."""
    sl = _calendar_sl(text)
    if not sl or len(sl) > 300:
        return None
    if re.search(rf"(?:^|\s){_DELETE_VERBS}\b", sl):
        return "delete"
    if re.search(rf"(?:^|\s){_UPDATE_VERBS}\b", sl):
        return "update"
    return None


def calendar_detect_route_kind(text: str) -> str | None:
    """Определяет подтип календаря: create | update | delete | free.

    Намеренно без списка жёстких фраз — только глаголы/корни и смысл.
    Детальный разбор текста делает gpt_openrouter_parse_calendar в обработчике.
    """
    sl = _calendar_sl(text)
    if not sl or len(sl) > 500:
        return None
    if is_journal_archive_query(text):
        return None
    if _OTHER_SKILL_PREFIX.match(sl):
        return None

    from assistant.lib.urls import extract_urls
    from assistant.lib.zoom_link import find_zoom_url

    if find_zoom_url(extract_urls(text or "")):
        if calendar_detect_amend_kind(text) in ("update", "delete"):
            pass
        else:
            return None

    has_topic = bool(re.search(_CALENDAR_TOPIC, sl))
    if not has_topic:
        if re.match(
            r"^(?:расписан\w*|календар\w*|график\w*)(?:\s+на\s+"
            r"(?:сегодня|завтра|послезавтра|день))?$",
            sl,
        ):
            return "free"
        if sl in {"расписание", "мое расписание", "моё расписание", "календарь", "график", "мой график"}:
            return "free"
        return None

    if re.search(rf"(?:^|\s){_DELETE_VERBS}(?:\s+.*)?{_CALENDAR_TOPIC}|{_DELETE_VERBS}.{{0,40}}{_CALENDAR_TOPIC}", sl):
        return "delete"
    if re.search(
        rf"(?:^|\s){_UPDATE_VERBS}(?:\s+.*)?{_CALENDAR_TOPIC}"
        rf"|{_UPDATE_VERBS}.{{0,40}}{_CALENDAR_TOPIC}"
        rf"|{_CALENDAR_TOPIC}.{{0,80}}{_UPDATE_VERBS}",
        sl,
    ):
        return "update"

    if _looks_like_schedule_create(sl):
        return "create"
    if re.search(rf"^{_CREATE_VERBS}\s+{_CALENDAR_TOPIC}", sl):
        return "create"

    if re.search(r"(?:свободн|когда\s+(?:я\s+)?свобод|найди\s+(?:слот|окн))", sl):
        return "free"
    if calendar_is_meeting_overview_query(sl):
        return "free"
    if re.search(r"(?:сегодня|завтра|послезавтра|после\s+завтра)", sl) and not re.search(
        _CREATE_VERBS, sl
    ):
        return "free"
    if re.search(r"(?:^|\s)(?:покажи|дай|что|какие|расскаж)\b", sl):
        return "free"

    return None


def calendar_likely_user_text(text: str) -> bool:
    """Текст похож на календарь — для вызова LLM-роутера при сомнениях."""
    if calendar_detect_route_kind(text) is not None:
        return True
    sl = _calendar_sl(text)
    if not sl or len(sl) > 500:
        return False
    if _OTHER_SKILL_PREFIX.match(sl):
        return False
    return bool(re.search(_CALENDAR_TOPIC, sl))


def calendar_user_text_blob(
    body_text: str,
    extra_context: str = "",
    *,
    message_text: str = "",
) -> str:
    return " ".join(
        x.strip()
        for x in (message_text, body_text, extra_context)
        if x and x.strip()
    )


def calendar_apply_relative_day(
    parsed: dict[str, Any],
    user_text: str,
    *,
    today_iso: str,
) -> None:
    try:
        today = date.fromisoformat(today_iso)
    except ValueError:
        return
    from assistant.lib.calendar_datetime_parse import calendar_relative_day

    target = calendar_relative_day(user_text, today, role="any")
    if target is not None:
        parsed["free_slots_date"] = target.isoformat()
        if str(parsed.get("intent") or "") == "create_event":
            parsed["slot_day"] = target.isoformat()
            parsed["slot_day_strict"] = True


def calendar_free_slots_hide_meetings(user_text: str) -> bool:
    sl = " ".join((user_text or "").lower().split())
    if not sl:
        return False
    meeting_overview = (
        "какие встреч",
        "что у меня",
        "мои встречи",
        "встречи у меня",
        "у меня встреч",
        "мой календарь",
        "покажи календарь",
        "покажи расписание",
        "покажи встречи",
        "расписание на",
        "расписан",
        "календарь на",
        "что в календаре",
        "что по встречам",
        "планы на",
        "график",
        "встречи на",
        "встречи завтра",
        "встречи сегодня",
    )
    if any(p in sl for p in meeting_overview):
        return False
    if calendar_is_meeting_overview_query(sl):
        return False
    free_only = (
        "свободн",
        "слот",
        "когда свобод",
        "когда я свобод",
        "найди слот",
        "найди окно",
    )
    if any(p in sl for p in free_only):
        return True
    return False
