"""Разбор дат/времени для календаря (RU) и нормализация ответа LLM."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from assistant.lib.calendar_event_utils import calendar_date_from_weekday_phrase

_MONTHS_GENITIVE: tuple[tuple[str, ...], int] = (
    (("январ",), 1),
    (("феврал",), 2),
    (("март", "марта"), 3),
    (("апрел",), 4),
    (("ма", "мая"), 5),
    (("июн",), 6),
    (("июл",), 7),
    (("август",), 8),
    (("сентябр",), 9),
    (("октябр",), 10),
    (("ноябр",), 11),
    (("декабр",), 12),
)

_WEEKDAY_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

# «на 19 мая», «в 15:30» — цель переноса/создания (после «на» / «в HH:MM»).
_MONTH_PREFIXES = "|".join(p for prefixes, _ in _MONTHS_GENITIVE for p in prefixes)

_RE_MOVE_ON_DATE = re.compile(
    r"(?:^|\s)на\s+"
    r"(?:(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?|"
    rf"(\d{{1,2}})\s+({_MONTH_PREFIXES}))",
    re.IGNORECASE,
)
_RE_AT_TIME = re.compile(
    r"(?:^|\s)(?:в|к)\s*(\d{1,2})(?:[:\.](\d{2}))?\s*(?:час|ч\.?)?(?=\s|$|[,.])",
    re.IGNORECASE,
)
_RE_TIME_COLON = re.compile(r"\b(\d{1,2})[:\.](\d{2})\b")
_RE_IN_N_MIN = re.compile(r"через\s+(\d+)\s*(?:мин|минут)", re.IGNORECASE)
_RE_IN_N_HOURS = re.compile(r"через\s+(\d+(?:[.,]\d+)?)\s*(?:час|часа|часов|ч\.)", re.IGNORECASE)
_RE_NEXT_WEEK = re.compile(r"(?:на\s+)?следующ\w*\s+недел", re.IGNORECASE)
_RE_UPDATE_SPLIT = re.compile(
    r"\s+на\s+(?=\d|завтра|послезавтра|следующ|в\s+\d|"
    r"понедельник|вторник|сред|четверг|пятниц|суббот|воскресен)",
    re.IGNORECASE,
)
_RE_ACTION_VERB = re.compile(
    r"^(?:удали\w*|отмени\w*|сотри\w*|перенес\w*|передвин\w*|"
    r"перестав\w*|измени\w*|поменя\w*|сдвинь\w*|обнови\w*)\s+",
    re.IGNORECASE,
)
_RE_MEETING_NOUN = re.compile(
    r"\b(?:встреч\w*|созвон\w*|митинг\w*|событ\w*|переговор\w*)\b",
    re.IGNORECASE,
)
_REL_DAY_WORDS = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|сегодняшн\w*|завтрашн\w*)\b",
    re.IGNORECASE,
)


def _sl(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _month_from_token(tok: str) -> int | None:
    t = (tok or "").lower()
    for prefixes, num in _MONTHS_GENITIVE:
        if any(t.startswith(p) for p in prefixes):
            return num
    return None


def calendar_parse_reference(today: date, tz: ZoneInfo) -> dict[str, Any]:
    """Справочник дат для LLM и постобработки."""
    now = datetime.now(tz)
    tomorrow = today + timedelta(days=1)
    day_after = today + timedelta(days=2)
    iso_weekday = today.weekday()
    week_start = today - timedelta(days=iso_weekday)
    week_end = week_start + timedelta(days=6)
    next_week_start = week_start + timedelta(days=7)
    next_week_end = next_week_start + timedelta(days=6)

    named: dict[str, str] = {
        "today": today.isoformat(),
        "tomorrow": tomorrow.isoformat(),
        "day_after_tomorrow": day_after.isoformat(),
    }
    for i in range(14):
        d = today + timedelta(days=i)
        named[f"day_plus_{i}"] = d.isoformat()

    weekdays: list[dict[str, str]] = []
    for offset in range(7):
        d = today + timedelta(days=offset)
        weekdays.append(
            {
                "date": d.isoformat(),
                "weekday_ru": _WEEKDAY_RU[d.weekday()],
                "label": "сегодня" if offset == 0 else ("завтра" if offset == 1 else ""),
            }
        )

    return {
        "today": today.isoformat(),
        "tomorrow": tomorrow.isoformat(),
        "day_after_tomorrow": day_after.isoformat(),
        "now_local": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "weekday_today_ru": _WEEKDAY_RU[iso_weekday],
        "iso_weekday_today": iso_weekday,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "next_week_start": next_week_start.isoformat(),
        "next_week_end": next_week_end.isoformat(),
        "named_offsets": named,
        "next_7_days": weekdays,
    }


def calendar_build_llm_user_payload(
    *,
    user_text: str,
    today_iso: str,
    timezone: str,
    requested_intent: str | None = None,
    previous: dict[str, Any] | None = None,
    reply_context: str = "",
    reply_author: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        today = date.fromisoformat(today_iso)
    except ValueError:
        today = date.today()
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("Europe/Moscow")
    payload: dict[str, Any] = {
        "today": today_iso,
        "timezone": timezone,
        "user_text": (user_text or "").strip(),
        "reference": calendar_parse_reference(today, tz),
        "rules_short": (
            "Все даты/время — локально в timezone. "
            "create_event/update_event: start не в прошлом — ближайшее будущее. "
            "«следующий понедельник» = не раньше чем через 7 дней от сегодня, если иначе попал на прошлое. "
            "«на следующей неделе» = даты из next_week_start…next_week_end. "
            "update: match_date — когда встреча СЕЙЧАС; start — куда переносим (после «на …»). "
            "Используй reference.next_7_days для «завтра», дней недели."
        ),
    }
    if requested_intent:
        payload["requested_intent"] = requested_intent
    if previous:
        payload["previous_parsed"] = previous
    if (reply_context or "").strip():
        payload["reply_context"] = (reply_context or "").strip()
    if reply_author:
        payload["reply_author"] = reply_author
    return payload


def calendar_relative_day(
    text: str,
    today: date,
    *,
    role: Literal["any", "search", "move"] = "any",
) -> date | None:
    """День из «сегодня/завтра/в пятницу/следующий вторник»."""
    sl = _sl(text)
    if not sl:
        return None

    if role == "move":
        parts = _RE_UPDATE_SPLIT.split(text, maxsplit=1)
        sl = _sl(parts[-1] if parts else text)
    elif role == "search" and _RE_UPDATE_SPLIT.search(text):
        parts = _RE_UPDATE_SPLIT.split(text, maxsplit=1)
        sl = _sl(parts[0])

    if "послезавтра" in sl or "после завтра" in sl:
        return today + timedelta(days=2)
    if re.search(r"\bзавтра\b", sl):
        return today + timedelta(days=1)
    if re.search(r"\bсегодня\b", sl):
        return today

    if _RE_NEXT_WEEK.search(sl):
        iso = today.weekday()
        week_start = today - timedelta(days=iso) + timedelta(days=7)
        wd = calendar_date_from_weekday_phrase(sl, today)
        if wd is not None:
            target_wd = wd.weekday()
            return week_start + timedelta(days=target_wd)
        return week_start

    return calendar_date_from_weekday_phrase(sl, today)


def calendar_parse_explicit_date(text: str, today: date) -> date | None:
    """«19 мая», «19.05», «19.05.2026»."""
    sl = _sl(text)
    if not sl:
        return None

    m = re.search(
        r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b",
        sl,
    )
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year_s = m.group(3)
        year = today.year
        if year_s:
            y = int(year_s)
            year = y if y > 99 else 2000 + y
        try:
            return date(year, month, day)
        except ValueError:
            pass

    for pat in (
        rf"\b(\d{{1,2}})\s+({_MONTH_PREFIXES})\w*",
        rf"\b(\d{{1,2}})\s+({_MONTH_PREFIXES})",
    ):
        m2 = re.search(pat, sl, re.IGNORECASE)
        if m2:
            day = int(m2.group(1))
            mon = _month_from_token(m2.group(2))
            if mon:
                try:
                    return date(today.year, mon, day)
                except ValueError:
                    return None
    return None


def calendar_extract_move_date(text: str, today: date) -> date | None:
    """Дата переноса/создания: фрагмент после «на …»."""
    parts = _RE_UPDATE_SPLIT.split(text, maxsplit=1)
    tail = parts[-1] if len(parts) > 1 else text
    sl = _sl(tail)

    if re.search(r"\bзавтра\b", sl):
        return today + timedelta(days=1)
    if "послезавтра" in sl or "после завтра" in sl:
        return today + timedelta(days=2)

    m = _RE_MOVE_ON_DATE.search(tail)
    if m:
        if m.group(4) and m.group(5):
            day = int(m.group(4))
            mon = _month_from_token(m.group(5))
            if mon:
                try:
                    return date(today.year, mon, day)
                except ValueError:
                    pass
        if m.group(1) and m.group(2):
            d, mo = int(m.group(1)), int(m.group(2))
            year = today.year
            if m.group(3):
                y = int(m.group(3))
                year = y if y > 99 else 2000 + y
            try:
                return date(year, mo, d)
            except ValueError:
                pass

    return calendar_relative_day(tail, today, role="move") or calendar_parse_explicit_date(
        tail, today
    )


def calendar_extract_time(text: str) -> tuple[int, int] | None:
    sl = _sl(text)
    if not sl:
        return None
    m = _RE_AT_TIME.search(sl)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi
    for m in _RE_TIME_COLON.finditer(sl):
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi
    return None


def calendar_roll_date_forward(d: date, today: date) -> date:
    """Если дата в прошлом — ближайшее будущее вхождение (год+1 для дня-месяца)."""
    while d < today:
        try:
            d = d.replace(year=d.year + 1)
        except ValueError:
            d = d + timedelta(days=365)
    return d


def calendar_ensure_future_datetime(
    dt: datetime,
    now: datetime,
    today: date,
    *,
    intent: str,
) -> datetime:
    """Сдвигает start в будущее относительно today/now (логический «сегодня» из контекста)."""
    grace = timedelta(minutes=5)
    if dt.date() < today:
        rolled = calendar_roll_date_forward(dt.date(), today)
        return datetime.combine(rolled, dt.time())
    if dt.date() > today:
        return dt
    ref = datetime.combine(today, now.time())
    if dt >= ref - grace:
        return dt
    if intent in {"create_event", "update_event"}:
        return dt + timedelta(days=1)
    return dt


def _parse_iso_field(s: str) -> datetime | None:
    raw = (s or "").strip()
    if not raw:
        return None
    raw = raw.replace(" ", "T", 1) if " " in raw and "T" not in raw else raw
    raw = re.sub(r"[+-]\d{2}:?\d{2}$", "", raw.rstrip("Z"))
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _merge_date_time(
    d: date,
    existing: datetime | None,
    time_hint: tuple[int, int] | None,
) -> datetime:
    if time_hint:
        return datetime.combine(d, time(time_hint[0], time_hint[1]))
    if existing is not None:
        return datetime.combine(d, existing.time())
    return datetime.combine(d, time(0, 0))


def calendar_extract_match_query(user_text: str) -> str:
    """Название встречи из «удали встречу сегодня Бустра Ложечкин»."""
    sl = (user_text or "").strip()
    if not sl:
        return ""
    sl = _RE_ACTION_VERB.sub("", sl)
    sl = _RE_MEETING_NOUN.sub(" ", sl)
    sl = _REL_DAY_WORDS.sub(" ", sl)
    for wd in _WEEKDAY_RU:
        sl = re.sub(rf"\b{re.escape(wd)}\w*\b", " ", sl, flags=re.IGNORECASE)
    sl = re.sub(r"\b(?:на|в|с|со|к)\b", " ", sl, flags=re.IGNORECASE)
    return " ".join(sl.split()).strip()


def calendar_normalize_parsed(
    parsed: dict[str, Any],
    user_text: str,
    *,
    today_iso: str,
    tz_name: str,
) -> None:
    """Правит даты/время по тексту пользователя и убирает прошлое для create/update."""
    try:
        today = date.fromisoformat(today_iso)
    except ValueError:
        today = date.today()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    intent = str(parsed.get("intent") or "").strip()

    # free_slots / slot_day
    rel = calendar_relative_day(user_text, today, role="any")
    if rel is not None:
        if intent == "free_slots" or not str(parsed.get("free_slots_date") or "").strip():
            parsed["free_slots_date"] = rel.isoformat()
        if str(parsed.get("slot_mode") or "") == "nearest" or not str(
            parsed.get("slot_day") or ""
        ).strip():
            parsed["slot_day"] = rel.isoformat()
            if calendar_relative_day(user_text, today, role="any") is not None:
                parsed["slot_day_strict"] = True

    if intent in {"update_event", "delete_event"}:
        mq = str(parsed.get("match_query") or "").strip()
        if not mq:
            mq = calendar_extract_match_query(user_text)
            if mq:
                parsed["match_query"] = mq
        md_search = calendar_relative_day(user_text, today, role="search")
        if md_search and not str(parsed.get("match_date") or "").strip():
            parsed["match_date"] = md_search.isoformat()
        if not str(parsed.get("match_date") or "").strip():
            rel = calendar_relative_day(user_text, today, role="any")
            if rel is not None:
                parsed["match_date"] = rel.isoformat()

    if intent == "update_event":
        move_d = calendar_extract_move_date(user_text, today)
        if move_d is not None:
            move_d = calendar_roll_date_forward(move_d, today)
            st = _parse_iso_field(str(parsed.get("start") or ""))
            th = calendar_extract_time(
                _RE_UPDATE_SPLIT.split(user_text, maxsplit=1)[-1]
                if _RE_UPDATE_SPLIT.search(user_text)
                else user_text
            )
            merged = _merge_date_time(move_d, st, th)
            parsed["start"] = merged.replace(tzinfo=None).isoformat(timespec="seconds")

    if intent == "create_event":
        day = calendar_extract_move_date(user_text, today) or calendar_relative_day(
            user_text, today, role="any"
        )
        if day is not None:
            day = calendar_roll_date_forward(day, today)
            st = _parse_iso_field(str(parsed.get("start") or ""))
            th = calendar_extract_time(user_text)
            if th or st is not None or not str(parsed.get("start") or "").strip():
                merged = _merge_date_time(day, st, th)
                parsed["start"] = merged.replace(tzinfo=None).isoformat(timespec="seconds")
                parsed["slot_day"] = day.isoformat()

        if not str(parsed.get("start") or "").strip():
            m_h = _RE_IN_N_HOURS.search(user_text)
            m_m = _RE_IN_N_MIN.search(user_text)
            if m_h:
                hrs = float(m_h.group(1).replace(",", "."))
                start = now + timedelta(hours=hrs)
                parsed["start"] = start.replace(tzinfo=None).isoformat(timespec="seconds")
            elif m_m:
                start = now + timedelta(minutes=int(m_m.group(1)))
                parsed["start"] = start.replace(tzinfo=None).isoformat(timespec="seconds")

    for field in ("start",):
        st = _parse_iso_field(str(parsed.get(field) or ""))
        if st is not None and intent in {"create_event", "update_event"}:
            st_aw = st.replace(tzinfo=tz) if st.tzinfo is None else st.astimezone(tz)
            fixed = calendar_ensure_future_datetime(
                st_aw.replace(tzinfo=None),
                now.replace(tzinfo=None),
                today,
                intent=intent,
            )
            if fixed != st:
                parsed[field] = fixed.isoformat(timespec="seconds")

    for field in ("free_slots_date", "match_date", "slot_day"):
        raw = str(parsed.get(field) or "").strip()
        if not raw:
            continue
        d = _parse_iso_field(raw)
        if d is None:
            continue
        rolled = calendar_roll_date_forward(d.date(), today)
        if rolled != d.date():
            parsed[field] = rolled.isoformat()

    mth = str(parsed.get("match_time_hhmm") or "").strip()
    if not mth and intent in {"update_event", "delete_event"}:
        head = (
            _RE_UPDATE_SPLIT.split(user_text, maxsplit=1)[0]
            if _RE_UPDATE_SPLIT.search(user_text)
            else user_text
        )
        th = calendar_extract_time(head)
        if th:
            parsed["match_time_hhmm"] = f"{th[0]:02d}:{th[1]:02d}"
