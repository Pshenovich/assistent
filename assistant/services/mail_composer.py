"""Окно письма: быстрые действия по «+» (слоты) и ИИ-правки текста («Короче», «Вежливее», «Перевести», «Поправь: …»)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

SLOT_MIN = 60
MAX_SLOTS = 5

_WEEKDAYS = {
    "пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2, "среду": 2, "чт": 3, "четверг": 3,
    "пт": 4, "пятница": 4, "пятницу": 4, "сб": 5, "суббота": 5, "субботу": 5, "вс": 6, "воскресенье": 6,
}
_MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8, "сен": 9,
           "окт": 10, "ноя": 11, "дек": 12}
_STOP = {"слоты", "слот", "+слоты", "+", "я", "и", "с", "со", "мной", "на", "в", "во", "для", "встреча", "встречи",
         "свободные", "окна", "время", "следующий", "следующую", "следующей", "следующая", "неделе", "через",
         "сегодня", "завтра", "послезавтра", "мин", "минут", "минуты", "час", "часа", "полчаса", "по"}
_WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def parse_query(query: str, today: date) -> dict[str, Any]:
    """«слоты я и Мыздриков следующий вторник 30 минут» → {date, people, duration}. Дата не указана — None."""
    q = re.sub(r"^\s*\+?\s*", "", str(query or "")).strip()
    low = q.lower()
    day: Optional[date] = None
    if "послезавтра" in low:
        day = today + timedelta(days=2)
    elif "завтра" in low:
        day = today + timedelta(days=1)
    elif "сегодня" in low:
        day = today
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", low)
    if not day and m:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            day = date(y, int(m.group(2)), int(m.group(1)))
            if not m.group(3) and day < today:
                day = date(y + 1, day.month, day.day)
        except ValueError:
            day = None
    m = re.search(r"\b(\d{1,2})\s+(янв|фев|мар|апр|мая|май|июн|июл|авг|сен|окт|ноя|дек)[а-я]*", low)
    if not day and m:
        try:
            day = date(today.year, _MONTHS[m.group(2)], int(m.group(1)))
            if day < today:
                day = date(today.year + 1, day.month, day.day)
        except ValueError:
            day = None
    if not day:
        for w in re.findall(r"[а-яё]+", low):
            if w in _WEEKDAYS:
                ahead = (_WEEKDAYS[w] - today.weekday()) % 7 or 7  # ближайший такой день (не сегодня)
                day = today + timedelta(days=ahead)
                if re.search(r"следующ", low):
                    day += timedelta(days=7)  # «следующий вторник» — на 7 дней позже ближайшего (как у Лео)
                break
    duration = SLOT_MIN
    if "полчаса" in low:
        duration = 30
    else:
        mm = re.search(r"(\d+)\s*мин", low)
        hh = re.search(r"(\d+(?:[.,]5)?)\s*час", low)
        if mm:
            duration = max(15, min(240, int(mm.group(1))))
        elif hh:
            duration = max(15, min(240, int(float(hh.group(1).replace(",", ".")) * 60)))
    people = []
    for w in re.findall(r"@?[A-Za-zА-Яа-яЁё][\w.@-]*", q):
        lw = w.lower()
        if lw in _STOP or lw in _WEEKDAYS or lw[:3] in _MONTHS or re.fullmatch(r"(след|недел|минут|час).*", lw):
            continue
        people.append(w)
    return {"date": day, "people": people, "duration": duration}


def _visible(owner: int, email: str, ws: datetime, we: datetime) -> bool:
    """Видна ли нам занятость человека (свой пользователь Orbita или открыт календарь)."""
    from assistant.lib.calendar_user_lookup import lookup_user_id_by_calendar_email
    from assistant.services import calendar as cal

    uid = lookup_user_id_by_calendar_email(email)
    if uid and int(uid) != int(owner):
        return True
    try:
        return cal.busy_intervals_via_viewer_email(int(owner), email, ws, we) is not None
    except Exception:
        return False


def _non_overlapping(slots: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for s in slots:
        if out and s["start"] < out[-1]["end"]:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def slots(uid: int, query: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    from assistant.lib.calendar_attendees import resolve_attendee_names
    from assistant.services import calendar as cal
    from assistant.services.calendar import _tz_for
    from assistant.services.gpt_calendar_tools import has_google_calendar

    if not has_google_calendar(uid):
        return {"ok": False, "error": "Подключите Google Календарь в профиле — тогда Лео подберёт свободное время."}
    tz = _tz_for(uid)
    now = (now or datetime.now(tz)).astimezone(tz)
    parsed = parse_query(query, now.date())
    found, missing = resolve_attendee_names(uid, parsed["people"]) if parsed["people"] else ([], [])
    emails = [a["email"] for a in found if a.get("email")]
    names = {a["email"]: a.get("name") or a["email"] for a in found if a.get("email")}
    day = parsed["date"]
    auto = day is None
    picked: list[dict[str, str]] = []
    for i in range(0, 8 if auto else 1):
        d = (now.date() + timedelta(days=i)) if auto else day
        got = cal.joint_free_slots_day(uid, d.isoformat(), emails, duration_min=parsed["duration"])
        picked = _non_overlapping(got, MAX_SLOTS)
        if picked:
            day = d
            break
    if day is None:
        day = now.date()
    ws, we = cal._work_window(uid, day.isoformat())
    hidden = [names.get(e, e) for e in emails if not _visible(uid, e, ws, we)]
    out_slots = []
    for s in picked:
        st, en = datetime.fromisoformat(s["start"]).astimezone(tz), datetime.fromisoformat(s["end"]).astimezone(tz)
        out_slots.append({"start": st.isoformat(), "end": en.isoformat(),
                          "label": f"{_WD_SHORT[st.weekday()]} {st.strftime('%d.%m')}, {st.strftime('%H:%M')}–{en.strftime('%H:%M')}"})
    tz_label = "МСК" if getattr(tz, "key", "") in ("Europe/Moscow", "W-SU") else getattr(tz, "key", "")
    return {
        "ok": True, "date": day.isoformat(), "duration": parsed["duration"],
        "people": [names[e] for e in emails], "not_found": missing, "unavailable": hidden,
        "slots": out_slots, "tz_label": tz_label,
    }


def slots_text(labels: list[str], tz_label: str) -> str:
    """Строка для письма: «Предлагаю: вт 28.10, 11:00–12:00 или 15:00–16:00 (МСК)»."""
    if not labels:
        return ""
    day0 = labels[0].split(",")[0]
    parts = [labels[0]] + [lb.split(", ", 1)[1] if lb.split(",")[0] == day0 else lb for lb in labels[1:]]
    body = ", ".join(parts[:-1]) + (" или " + parts[-1] if len(parts) > 1 else parts[0])
    return f"Предлагаю: {body}" + (f" ({tz_label})" if tz_label else "")


# ---------------------------------------------------------------- ИИ-правки текста

_MODES = {
    "short": "Сократи письмо: оставь суть, убери воду. Тон тот же.",
    "polite": "Сделай письмо вежливее и мягче, не меняя смысл и не раздувая.",
    "translate": "Переведи письмо на {lang}. Если оно уже на этом языке — на русский.",
    "custom": "Исправь письмо по просьбе пользователя: {instruction}",
}


def rewrite(uid: int, body: str, mode: str, *, instruction: str = "", lang: str = "английский") -> str:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.nlu import llm

    body = str(body or "").strip()
    if not body:
        raise ValueError("Сначала напишите текст письма.")
    if mode not in _MODES:
        raise ValueError("Неизвестное действие")
    if mode == "custom" and not instruction.strip():
        raise ValueError("Напишите, что поправить.")
    task = _MODES[mode].format(instruction=instruction.strip()[:500], lang=lang)
    system = ("Ты редактор деловой переписки. " + task +
              " Верни только новый текст письма — без пояснений, без темы, без кавычек. Сохрани приветствие и абзацы.")
    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    try:
        out = llm._chat(system, body[:8000], operation="mail_rewrite", timeout=60, temperature=0.3, max_tokens=1500)
    finally:
        set_openrouter_usage_telegram_user(telegram_user_id=None)
    out = str(out or "").strip().strip("`").strip()
    if not out:
        raise ValueError("Не получилось — попробуйте ещё раз.")
    return out


# ---------------------------------------------------------------- Лео внутри ветки

def _thread_prompt(uid: int, thread_key: str) -> tuple[str, dict[str, Any]]:
    from assistant.services import mail_inbox

    t = mail_inbox.thread(uid, thread_key)
    lines = []
    for m in t["messages"][-8:]:
        who = "Я" if m["direction"] == "out" else (m["from"] or m["from_addr"])
        lines.append(f"[{str(m['date'])[:16].replace('T', ' ')}] {who}: {m['text'][:3000]}")
    return f"Тема: {t['subject']}\n\n" + "\n\n".join(lines), t


def _llm(uid: int, system: str, user: str, operation: str, max_tokens: int = 1200) -> str:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.nlu import llm

    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    try:
        out = llm._chat(system, user[:24000], operation=operation, timeout=90, temperature=0.3, max_tokens=max_tokens)
    finally:
        set_openrouter_usage_telegram_user(telegram_user_id=None)
    out = str(out or "").strip().strip("`").strip()
    if not out:
        raise ValueError("Не получилось — попробуйте ещё раз.")
    return out


def draft_reply(uid: int, thread_key: str, *, reply_to_key: str = "", instruction: str = "") -> str:
    """Текст ответа в стиле пользователя (образцы — его письма этому человеку). Ничего не отправляет."""
    from assistant.services import mail_reply

    convo, t = _thread_prompt(uid, thread_key)
    target = next((m for m in t["messages"] if m["mail_key"] == reply_to_key), None) or next(
        (m for m in reversed(t["messages"]) if m["direction"] == "in"), t["messages"][-1])
    samples = mail_reply.style_samples(uid, target["from_addr"]) if target["direction"] == "in" else []
    system = (
        "Ты пишешь ответ на письмо от имени пользователя. Пиши как он: тон, длина, обращение — по образцам его писем. "
        "Только текст письма: без темы, без подписи, без цитаты. Ничего не обещай за пользователя, чего нет в переписке; "
        "если нужно решение, которого нет, — оставь короткий вопрос или [уточнить]."
    )
    user = (f"Переписка:\n{convo}\n\nОтвечаем на письмо от {target['from'] or 'собеседника'} ({str(target['date'])[:10]}).\n"
            + (f"Пожелание пользователя: {instruction}\n" if instruction else "")
            + ("Образцы, как пользователь пишет этому человеку:\n" + "\n---\n".join(samples) if samples else ""))
    return _llm(uid, system, user, "mail_draft")


def summarize_thread(uid: int, thread_key: str) -> str:
    convo, _t = _thread_prompt(uid, thread_key)
    system = ("Кратко перескажи переписку для руководителя: 1) о чём речь, 2) что решили, 3) что ждут от пользователя "
              "(«Я» в переписке) и к какому сроку, 4) открытые вопросы. Списком, без воды, до 8 пунктов. Не выдумывай.")
    return _llm(uid, system, convo, "mail_summary", max_tokens=700)
