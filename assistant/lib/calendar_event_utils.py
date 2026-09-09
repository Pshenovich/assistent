"""Классификация записей Google Calendar и расчёт занятости (бот + Mini App)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from assistant.lib.journal_retrieval import is_journal_archive_query


def calendar_event_is_cancelled(event: dict[str, Any]) -> bool:
    return str(event.get("status") or "").strip().lower() == "cancelled"


def calendar_entry_kind_label(event: dict[str, Any]) -> str:
    """Единая подпись типа записи для списков календаря."""
    et = str(event.get("eventType") or "default").strip().lower()
    if et in {"outofoffice", "out_of_office"}:
        return "Занятость"
    if et == "focustime":
        return "Фокус"
    if et == "workinglocation":
        return "Рабочее место"
    if et == "birthday":
        return "День рождения"
    summary = str(event.get("summary") or "").strip().lower()
    if summary.startswith("задача:") or "✓" in summary[:4]:
        return "Задача"
    desc = str(event.get("description") or "").strip().lower()
    if "reminder" in desc[:40] or "напоминание" in desc[:40]:
        return "Напоминание"
    guests = event.get("attendees") or []
    if isinstance(guests, list) and len(guests) > 0:
        return "Встреча"
    if et == "fromgmail":
        return "Событие"
    return "Встреча"


def calendar_event_counts_as_busy(event: dict[str, Any]) -> bool:
    """Запись занимает время в рабочем окне (слоты и обзор дня)."""
    if calendar_event_is_cancelled(event):
        return False
    et = str(event.get("eventType") or "default").strip().lower()
    if et == "birthday":
        return False
    return True


def calendar_event_busy_window_local(
    event: dict[str, Any],
    *,
    tz: Any,
    work_start: datetime,
    work_end: datetime,
) -> tuple[datetime, datetime] | None:
    """Интервал занятости события в локальной tz, обрезанный рабочим окном дня."""
    if not calendar_event_counts_as_busy(event):
        return None
    st = event.get("start") or {}
    en = event.get("end") or {}
    if not isinstance(st, dict) or not isinstance(en, dict):
        return None

    ds = st.get("dateTime")
    de = en.get("dateTime")
    if ds and de:
        try:
            s = datetime.fromisoformat(str(ds).replace("Z", "+00:00")).astimezone(tz)
            e = datetime.fromisoformat(str(de).replace("Z", "+00:00")).astimezone(tz)
        except ValueError:
            return None
        s = max(s, work_start)
        e = min(e, work_end)
        if s < e:
            return (s, e)
        return None

    day_s = st.get("date")
    if day_s:
        try:
            d = date.fromisoformat(str(day_s)[:10])
        except ValueError:
            return None
        if d != work_start.date():
            return None
        return (work_start, work_end)

    return None


def calendar_merge_busy_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged: list[tuple[datetime, datetime]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def calendar_busy_from_events(
    events: list[dict[str, Any]],
    *,
    tz: Any,
    work_start: datetime,
    work_end: datetime,
) -> list[tuple[datetime, datetime]]:
    extra: list[tuple[datetime, datetime]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        win = calendar_event_busy_window_local(
            ev, tz=tz, work_start=work_start, work_end=work_end
        )
        if win:
            extra.append(win)
    return calendar_merge_busy_intervals(extra)


_CALENDAR_UPDATE_OR_DELETE_IN_TEXT = re.compile(
    r"(?:перенес\w*|передвин\w*|перестав\w*|измени\w*|поменя\w*|сдвинь\w*|обнови\w*|"
    r"удали\w*|отмени\w*|сотри\w*)",
    re.IGNORECASE,
)


def calendar_is_meeting_overview_query(sl: str) -> bool:
    """Естественные вопросы про расписание без жёсткого шаблона фразы."""
    if not sl or len(sl) > 240:
        return False
    if is_journal_archive_query(sl):
        return False
    if _CALENDAR_UPDATE_OR_DELETE_IN_TEXT.search(sl):
        return False
    if re.match(
        r"^(создай|поставь|запланируй|заведи|запиши|добавь|организуй|организ\w*|"
        r"собери|проведи|сделай|удали|отмени|"
        r"перенеси|передвинь|измени|поменяй)\b",
        sl,
    ):
        return False
    if not re.search(
        r"(встреч|созвон|митинг|календар|расписан|график|событ|переговор)",
        sl,
    ):
        return False
    if re.search(r"(какие|что|покажи|дай|список|расскаж|напомни)", sl):
        return True
    if re.search(r"(у меня|мои)\s+.*(встреч|созвон|митинг)", sl):
        return True
    if re.search(r"(встреч|созвон|митинг).*(у меня|мои)", sl):
        return True
    return False


_WEEKDAY_OFFSET: tuple[tuple[str, ...], int] = (
    (("понедельник", "пн"), 0),
    (("вторник", "вт"), 1),
    (("среда", "среду", "ср"), 2),
    (("четверг", "четверга", "чт"), 3),
    (("пятница", "пятницу", "пт"), 4),
    (("суббота", "субботу", "сб"), 5),
    (("воскресенье", "воскресенье", "вс"), 6),
)


def calendar_date_from_weekday_phrase(sl: str, today: date) -> date | None:
    """«в понедельник», «следующий вторник» → ближайший подходящий день вперёд."""
    sl_n = " ".join((sl or "").lower().split())
    if not sl_n:
        return None
    force_next = bool(
        re.search(r"(?:^|\s)(?:следующ\w*|след\.?)\s*", sl_n)
        or "на следующей неделе" in sl_n
    )
    for names, wd in _WEEKDAY_OFFSET:
        for nm in names:
            if re.search(rf"\b{re.escape(nm)}\b", sl_n):
                delta = (wd - today.weekday()) % 7
                if force_next and delta == 0:
                    delta = 7
                return today + timedelta(days=delta)
    return None


_CALENDAR_PLACEHOLDER_TITLES = frozenset(
    {
        "встреча",
        "созвон",
        "митинг",
        "встреча с гостем",
        "новая встреча",
        "meeting",
    }
)


def calendar_title_is_placeholder(title: str) -> bool:
    t = (title or "").strip().lower().replace("ё", "е")
    if not t:
        return True
    if t in _CALENDAR_PLACEHOLDER_TITLES:
        return True
    if t.startswith("встреча с ") and len(t.split()) <= 4:
        return True
    return False


def calendar_clarify_expects_title(pending: dict[str, Any]) -> bool:
    field = str(pending.get("clarify_field") or "").strip().lower()
    if field == "title":
        return True
    questions = (pending.get("parsed") or {}).get("questions") or pending.get("questions") or []
    q0 = str(questions[0] if questions else "").lower().replace("ё", "е")
    return any(
        x in q0
        for x in (
            "назван",
            "тема",
            "назов",
            "как назв",
            "какова",
            "какое",
            "о чем",
            "о чём",
            "какую встреч",
            "как назвать",
        )
    )


def calendar_answer_looks_like_time(answer: str) -> bool:
    low = (answer or "").strip().lower()
    if re.search(r"\d{1,2}[:\.]\d{2}", low):
        return True
    return any(
        w in low
        for w in (
            "сегодня",
            "завтра",
            "послезавтра",
            "понедельник",
            "вторник",
            "среду",
            "четверг",
            "пятниц",
            "суббот",
            "воскресен",
        )
    )


def calendar_try_resolve_clarify(
    pending: dict[str, Any], answer: str
) -> dict[str, Any] | None:
    """Ответ на уточняющий вопрос бота (название и т.п.) без повторного GPT.

    Возвращает None, если это новая команда календаря (вызывающий сбросит pending).
    """
    if str(pending.get("mode") or "") != "await_clarify":
        return None
    parsed = pending.get("parsed")
    if not isinstance(parsed, dict):
        return None
    ans = (answer or "").strip()
    if not ans:
        return None
    low = ans.lower().replace("ё", "е")
    if low in {"стоп", "отмена", "cancel", "/stop", "/cancel"}:
        return None

    sl_ans = " ".join(low.split())
    if re.search(
        r"(?:^|\s)(?:поставь|создай|заведи|запиши|запланируй)\s+(?:встреч\w*|созвон\w*|митинг\w*)",
        sl_ans,
    ):
        return None
    if re.search(
        r"(?:^|\s)(?:перенес\w*|передвин\w*|измени\w*|сдвинь\w*|удали\w*|отмени\w*)",
        sl_ans,
    ):
        return None

    out = dict(parsed)
    intent = str(out.get("intent") or "").strip().lower()
    if intent in {"", "none"} and (out.get("start") or str(pending.get("body") or "").strip()):
        out["intent"] = "create_event"
        intent = "create_event"

    clarify_field = str(pending.get("clarify_field") or "").strip().lower()
    expects_title = clarify_field == "title" or calendar_clarify_expects_title(pending)

    if intent == "create_event" and expects_title:
        if len(ans) <= 200 and not calendar_answer_looks_like_time(ans):
            out["title"] = ans
            out["need_more_info"] = False
            out["questions"] = []
            return out

    # Режим уточнения без явного поля — короткий ответ считаем темой для create.
    if intent == "create_event" and len(ans) <= 200 and not calendar_answer_looks_like_time(ans):
        out["title"] = ans
        out["need_more_info"] = False
        out["questions"] = []
        return out

    if intent == "update_event":
        field = str(pending.get("clarify_field") or "").strip().lower()
        if field == "match_query" or field in {"", "title"}:
            if len(ans) <= 200 and not calendar_answer_looks_like_time(ans):
                out["match_query"] = ans
                out["intent"] = "update_event"
                out["need_more_info"] = False
                out["questions"] = []
                return out

    return None


_GCAL_HTML_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_GCAL_BLOCK_TAGS = {
    "p",
    "div",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "ul",
    "ol",
    "table",
    "pre",
}


def calendar_description_plain(raw: str | None) -> str:
    """Google Calendar часто кладёт HTML в description — для карточки нужен текст."""
    from html.parser import HTMLParser

    s = str(raw or "")
    if not s.strip():
        return ""
    if not _GCAL_HTML_RE.search(s):
        return re.sub(r"[ \t]+\n", "\n", s).strip()

    class _Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: list) -> None:
            t = (tag or "").lower()
            if t == "br":
                self.parts.append("\n")
            elif t == "li":
                self.parts.append("\n• ")
            elif t in _GCAL_BLOCK_TAGS:
                self.parts.append("\n")

        def handle_endtag(self, tag: str) -> None:
            t = (tag or "").lower()
            if t in _GCAL_BLOCK_TAGS or t == "li":
                self.parts.append("\n")

        def handle_data(self, data: str) -> None:
            self.parts.append(data or "")

    parser = _Text()
    try:
        parser.feed(s)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = _GCAL_HTML_RE.sub(" ", s)
    text = text.replace("\xa0", " ")
    text = re.sub(r"•\s+", "• ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
