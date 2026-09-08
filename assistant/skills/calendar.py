"""Календарь: создание, просмотр, удаление."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from assistant.lib.telegram_message import reply_formatted
from telegram.ext import ContextTypes

from assistant.lib import calendar_pending_store as cps
from assistant.lib.slot_time import (
    parse_time_from_user_text,
    resolve_pick_day_and_time,
    user_text_mentions_calendar_day,
)
from assistant.lib.calendar_attendees import enrich_parsed_attendees, names_match
from assistant.lib.calendar_intent_heuristics import calendar_free_slots_hide_meetings
from assistant.lib.calendar_intent_heuristics import calendar_detect_amend_kind
from assistant.lib.message_context import (
    add_reply_author_as_attendee,
    is_reply_to_bot,
    merge_calendar_text,
    parse_bot_calendar_event_message,
    reply_author_info,
    reply_context_text,
    strip_bot_mention,
)
from assistant.stores import user_prefs
from assistant.lib.calendar_datetime_parse import calendar_extract_match_query
from assistant.nlu import llm as nlu_llm
from assistant.services import calendar as cal_svc
from assistant.stores import contacts_store as contacts
from assistant.stores.contacts_store import EMAIL_RE, normalize_telegram_username

_RE_CONTACT_LINE = re.compile(
    r"^(.+?)\s+([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})(?:\s+@([a-zA-Z][a-zA-Z0-9_]{4,31}))?\s*$"
)
_RE_EMAIL_GAP_DOT = re.compile(
    r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]*)\s+\.([A-Za-z]{2,})"
)
_RE_EMAIL_GAP_AT = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
)


def _normalize_contact_input(text: str) -> str:
    """Склеивает переносы и пробелы внутри email (gmail\\n.com → gmail.com)."""
    s = " ".join((text or "").split())
    for _ in range(3):
        ns = _RE_EMAIL_GAP_DOT.sub(r"\1.\2", s)
        ns = _RE_EMAIL_GAP_AT.sub(r"\1@\2", ns)
        if ns == s:
            break
        s = ns
    return s

_PENDING_TTL = float(os.getenv("CALENDAR_PENDING_TTL_SEC", "3600") or "3600")


def _tz(user_id: int):
    return user_prefs.get_user_tz(user_id)


def _chat_id(update: Update) -> int:
    chat = update.effective_chat
    return int(chat.id) if chat else 0


def _register_event_message_ref(
    parent_msg,
    sent_msg,
    *,
    user_id: int,
    result: dict[str, Any],
) -> None:
    if not parent_msg or not sent_msg:
        return
    ev_id = str(result.get("event_id") or "").strip()
    if not ev_id:
        return
    st = result.get("start")
    start_iso = st.isoformat() if isinstance(st, datetime) else str(st or "")
    cps.put_event_message_ref(
        int(parent_msg.chat.id),
        int(sent_msg.message_id),
        user_id=int(user_id),
        event_id=ev_id,
        calendar_id=str(result.get("calendar_id") or ""),
        summary=str(result.get("summary") or ""),
        start_iso=start_iso,
    )


async def _send_event_result(
    msg,
    result: dict[str, Any],
    *,
    user_id: int,
    updated: bool = False,
) -> None:
    text = cal_svc.format_create_reply_html(result, user_id)
    if updated:
        text = text.replace("Создана встреча:", "Обновлена встреча:")
    kb = cal_svc.build_created_event_keyboard(result, user_id=user_id)
    sent = await reply_formatted(
        msg,
        text,
        reply_markup=kb,
        disable_web_page_preview=True,
        # Classic HTML returns Message with message_id so reply «удали» binds
        # to the correct Google event (Rich Message often omits message_id).
        prefer_classic=True,
    )
    _register_event_message_ref(msg, sent, user_id=user_id, result=result)


async def _send_created(
    msg,
    result: dict[str, Any],
    *,
    user_id: int,
) -> None:
    await _send_event_result(msg, result, user_id=user_id, updated=False)


async def _prompt_missing_attendees(
    msg,
    *,
    chat_id: int,
    user_id: int,
    parsed: dict[str, Any],
    missing: list[str],
    request_text: str = "",
    reply_context: str = "",
) -> None:
    name = str(missing[0]) if missing else "участника"
    cps.set_pending(
        chat_id,
        {
            "kind": "missing_attendees",
            "user_id": user_id,
            "parsed": parsed,
            "missing_names": missing,
            "request_text": request_text,
            "reply_context": reply_context,
        },
        user_id=user_id,
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Добавить контакт", callback_data="cal:add_contact"
                )
            ],
            [
                InlineKeyboardButton(
                    "Создать без участников", callback_data="cal:skip_attendees"
                )
            ],
        ]
    )
    await msg.reply_text(
        f"Не нашел в контактах {name}, добавить этого человека в ваши контакты?\n"
        "Можно ответить одной строкой: «Имя email@mail.ru» или «Имя email@mail.ru @username».",
        reply_markup=kb,
    )


def _parse_contact_quick_line(text: str) -> dict[str, str] | None:
    """Одна строка: «Имя email@x.ru @username»."""
    normalized = _normalize_contact_input(text)
    m = _RE_CONTACT_LINE.match(normalized)
    if not m:
        em = EMAIL_RE.search(normalized)
        if not em:
            return None
        email = em.group(0).lower()
        rest = normalized.replace(email, " ").strip()
        tg_m = re.search(r"@([a-zA-Z][a-zA-Z0-9_]{4,31})", rest)
        name = re.sub(r"@\w+", "", rest).strip(" ,;")
        if not name:
            name = email.split("@")[0]
        out: dict[str, str] = {"name": name, "email": email}
        if tg_m:
            out["telegram_username"] = normalize_telegram_username(tg_m.group(1))
        return out
    name = (m.group(1) or "").strip()
    email = (m.group(2) or "").strip().lower()
    out = {"name": name, "email": email}
    if m.group(3):
        out["telegram_username"] = normalize_telegram_username(m.group(3))
    return out


def _contact_step_message(step: str, *, hint_name: str = "") -> str:
    if step == "name":
        return (
            "Пришлите Имя и фамилию человека, его e-mail и юзернейм в телеграме.\n"
            "Или одной строкой: «Имя Фамилия email@mail.ru @username»."
        )
    if step == "email":
        return "Шаг 2/3 — email.\nВведите адрес почты контакта."
    return (
        "Шаг 3/3 — Telegram username.\n"
        "Введите @username (без @ можно) или «—», если не нужен."
    )


def _contact_aliases(draft: dict[str, str], missing_names: list[str]) -> list[str] | None:
    seen: set[str] = set()
    out: list[str] = []
    name = str(draft.get("name") or "").strip()
    if name:
        seen.add(name.lower())
        out.append(name)
    for raw in missing_names:
        hint = str(raw).strip()
        if not hint:
            continue
        key = hint.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(hint)
    return out or None


def _parsed_after_contact_saved(
    parsed: dict[str, Any],
    draft: dict[str, str],
    missing_names: list[str],
) -> dict[str, Any]:
    """Убрать из parsed неразрешённое имя и добавить email участника."""
    out = dict(parsed)
    email = str(draft.get("email") or "").strip().lower()
    name = str(draft.get("name") or "").strip()
    if email:
        emails = [
            str(e).strip().lower()
            for e in (out.get("attendees") or [])
            if "@" in str(e)
        ]
        if email not in emails:
            emails.append(email)
        out["attendees"] = emails
    hints = [str(x).strip() for x in missing_names if str(x).strip()]
    names: list[str] = []
    for raw in out.get("attendee_names") or []:
        n = str(raw).strip()
        if not n:
            continue
        if any(names_match(n, h) for h in hints):
            continue
        names.append(n)
    if name and not any(names_match(name, x) for x in names):
        names.append(name)
    out["attendee_names"] = names
    return out


async def _save_contact_and_continue(
    update: Update,
    msg,
    *,
    user_id: int,
    chat_id: int,
    draft: dict[str, str],
    parsed: dict[str, Any],
    missing_names: list[str],
    request_text: str = "",
    reply_context: str = "",
) -> None:
    user = update.effective_user
    name = str(draft.get("name") or "").strip()
    email = str(draft.get("email") or "").strip().lower()
    tg = str(draft.get("telegram_username") or "").strip()
    tg_norm = normalize_telegram_username(tg) if tg and tg not in {"—", "-", "нет", "skip"} else ""
    contacts.create_contact_for_user(
        telegram_user_id=user_id,
        telegram_username=user.username if user else None,
        name=name,
        email=email,
        telegram_username_contact=tg_norm or None,
        aliases=_contact_aliases(draft, missing_names),
    )
    parsed = _parsed_after_contact_saved(parsed, draft, missing_names)
    username = user.username if user else None
    still_missing = cal_svc.unresolved_attendee_names(
        user_id, parsed, telegram_username=username
    )
    if still_missing:
        cps.set_pending(
            chat_id,
            {
                "kind": "missing_attendees",
                "user_id": user_id,
                "parsed": parsed,
                "missing_names": still_missing,
                "request_text": request_text,
                "reply_context": reply_context,
            },
            user_id=user_id,
        )
        await _prompt_missing_attendees(
            msg,
            chat_id=chat_id,
            user_id=user_id,
            parsed=parsed,
            missing=still_missing,
            request_text=request_text,
            reply_context=reply_context,
        )
        return
    cps.clear_pending(chat_id, user_id=user_id)
    await _maybe_create_or_pick_time(
        update,
        msg,
        user_id,
        parsed,
        request_text,
        reply_context=reply_context,
        skip_attendee_names=None,
    )


def _build_slot_keyboard(
    user_id: int, starts: list[datetime]
) -> InlineKeyboardMarkup | None:
    if not starts:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for st in starts:
        label = st.strftime("%H:%M")
        token = cps.put_slot_pick_token(user_id, st.replace(tzinfo=None).isoformat(timespec="seconds"))
        row.append(InlineKeyboardButton(label, callback_data=f"cal:stp:{token}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else None


def _build_conflict_keyboard(
    user_id: int,
    starts: list[datetime],
    *,
    force_start: datetime,
) -> InlineKeyboardMarkup:
    """Свободные слоты + «Все равно поставить» на запрошенное время."""
    rows: list[list[InlineKeyboardButton]] = []
    slot_kb = _build_slot_keyboard(user_id, starts)
    if slot_kb and slot_kb.inline_keyboard:
        rows.extend(list(slot_kb.inline_keyboard))
    start_iso = force_start.replace(tzinfo=None).isoformat(timespec="seconds")
    fc_token = cps.put_force_create_token(user_id, start_iso)
    rows.append(
        [
            InlineKeyboardButton(
                "Все равно поставить",
                callback_data=f"cal:fc:{fc_token}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _pick_day_label(day_iso: str, *, today) -> str:
    from datetime import date as date_cls, timedelta

    try:
        d = date_cls.fromisoformat(day_iso[:10])
    except ValueError:
        return day_iso
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    return day_iso


async def _prompt_pick_time(
    msg,
    *,
    chat_id: int,
    user_id: int,
    parsed: dict[str, Any],
    user_text: str,
    skip_attendee_names: set[str] | None = None,
    request_text: str = "",
    reply_context: str = "",
) -> None:
    tz = _tz(user_id)
    today = datetime.now(tz).date()
    day = cal_svc.parsed_event_day(parsed)
    if not day:
        day = today.isoformat()
    elif not str(parsed.get("slot_day_strict") or "") and not user_text_mentions_calendar_day(
        request_text or user_text, today=today
    ):
        day = today.isoformat()
        parsed["slot_day"] = day
    if not day:
        await msg.reply_text("Уточните день встречи.")
        return
    dur = int(parsed.get("duration_min") or 60)
    try:
        starts = cal_svc.suggest_slot_starts(user_id, day, duration_min=dur)
    except Exception as e:
        await msg.reply_text(f"Ошибка: {e}")
        return
    if not starts:
        now_local = datetime.now(tz)
        work_start, work_end = cal_svc._work_window(user_id, day)
        hint = (
            f"На {day} нет свободных окон в рабочее время. "
            "Напишите время текстом (например: «завтра в 10:00»)."
        )
        if day == now_local.date().isoformat():
            if now_local >= work_end:
                hint = (
                    f"На сегодня рабочий день уже закончился "
                    f"(до {work_end.strftime('%H:%M')}). "
                    "Напишите, например: «завтра в 10:00»."
                )
            else:
                hint = (
                    "На сегодня подходящих слотов по кнопкам не осталось. "
                    "Напишите время текстом, например: «сегодня в 22:00»."
                )
        await msg.reply_text(hint)
        cps.set_pending(
            chat_id,
            {
                "kind": "await_pick_time",
                "user_id": user_id,
                "parsed": parsed,
                "day_iso": day,
                "skip_names": list(skip_attendee_names or []),
                "request_text": request_text or user_text,
                "reply_context": reply_context,
            },
            user_id=user_id,
        )
        return
    kb = _build_slot_keyboard(user_id, starts)
    cps.set_pending(
        chat_id,
        {
            "kind": "await_pick_time",
            "user_id": user_id,
            "parsed": parsed,
            "day_iso": day,
            "skip_names": list(skip_attendee_names or []),
            "request_text": request_text or user_text,
            "reply_context": reply_context,
        },
        user_id=user_id,
    )
    day_label = _pick_day_label(day, today=today)
    await msg.reply_text(
        f"На {day_label} выберите время встречи кнопкой или напишите "
        f"(например: 10 или «сегодня в 22:00»):",
        reply_markup=kb,
    )


async def _finalize_create_at_start(
    update: Update,
    msg,
    uid: int,
    parsed: dict[str, Any],
    start: datetime,
    *,
    skip_attendee_names: set[str] | None = None,
) -> None:
    """Создать встречу в указанное время (без повторной проверки занятости)."""
    cal_svc.apply_start_to_parsed(parsed, start)
    await _create_from_parsed(
        update, msg, uid, parsed, skip_attendee_names=skip_attendee_names
    )


async def _apply_slot_and_create(
    update: Update,
    msg,
    uid: int,
    parsed: dict[str, Any],
    start: datetime,
    *,
    skip_attendee_names: set[str] | None = None,
) -> None:
    username = update.effective_user.username if update.effective_user else None
    skip = {str(x).strip().lower() for x in (skip_attendee_names or set())}
    try:
        check = cal_svc.validate_meeting_slot(
            uid,
            parsed,
            start,
            telegram_username=username,
            skip_names=skip,
        )
    except Exception as e:
        await msg.reply_text(f"Ошибка: {e}")
        return
    day = cal_svc.parsed_event_day(parsed) or start.date().isoformat()
    if not check["ok"]:
        lines: list[str] = []
        if check.get("owner_conflict"):
            lines.append(
                f"В {start.strftime('%H:%M')} у вас уже есть встреча."
            )
        for name in check.get("attendee_busy") or []:
            lines.append(
                f"У {name} в это время занято (по календарю в боте)."
            )
        seen_warn: set[str] = set()
        for name in check.get("attendees_not_checked") or []:
            key = " ".join((name or "").strip().lower().split())
            if not key or key in seen_warn:
                continue
            seen_warn.add(key)
            lines.append(
                f"Занятость «{name}» не проверена: нет @username в контакте "
                "или календарь не подключён к боту (/calendar_auth)."
            )
        alts = list(check.get("alt_starts") or [])
        attendee_alts = list(check.get("attendee_alt_starts") or [])
        att_label = str(check.get("attendee_alt_label") or "").strip()
        if alts:
            lines.append(f"Свободные слоты на {day} (вам и участнику):")
        elif attendee_alts and att_label:
            dur_min = int(check.get("attendee_alt_duration_min") or parsed.get("duration_min") or 60)
            dur_note = ""
            if dur_min != int(parsed.get("duration_min") or 60):
                dur_note = f", {dur_min} мин"
            if str(check.get("attendee_alt_source") or "") == "primary":
                lines.append(
                    f"Общих окон нет. По личному календарю у {att_label} свободно "
                    f"на {day}{dur_note} (рабочий календарь может отличаться):"
                )
            else:
                lines.append(
                    f"Общих окон нет. У {att_label} свободно на {day}{dur_note} "
                    f"(у вас могут быть другие встречи):"
                )
            alts = attendee_alts
        else:
            lines.append(
                f"На {day} нет свободных окон в рабочее время. "
                "Выберите время кнопкой, напишите другое (например: 14:30) "
                "или нажмите «Все равно поставить» для {0}.".format(
                    start.strftime("%H:%M")
                )
            )
        text = "\n".join(lines) if lines else "Это время занято."
        kb = _build_conflict_keyboard(uid, alts, force_start=start)
        pending: dict[str, Any] = {
            "kind": "await_pick_time",
            "user_id": uid,
            "parsed": parsed,
            "day_iso": day,
            "skip_names": list(skip_attendee_names or []),
        }
        alt_dur = int(check.get("attendee_alt_duration_min") or 0)
        if attendee_alts and alts == attendee_alts and alt_dur > 0:
            pending["slot_duration_min"] = alt_dur
        cps.set_pending(
            _chat_id(update),
            pending,
            user_id=uid,
        )
        await msg.reply_text(text, reply_markup=kb)
        return
    not_checked = list(check.get("attendees_not_checked") or [])
    if not_checked:
        names = "», «".join(not_checked[:3])
        await msg.reply_text(
            f"Занятость «{names}» не проверена: добавьте в контакт @username "
            "или пусть участник выполнит /calendar_auth в этом боте."
        )
    await _finalize_create_at_start(
        update, msg, uid, parsed, start, skip_attendee_names=skip_attendee_names
    )


async def _maybe_create_or_pick_time(
    update: Update,
    msg,
    uid: int,
    parsed: dict[str, Any],
    user_text: str,
    *,
    reply_context: str = "",
    skip_attendee_names: set[str] | None = None,
) -> None:
    cal_svc.ensure_event_title_from_request(
        parsed, user_text, reply_context=reply_context
    )
    if cal_svc.needs_time_selection(
        parsed, user_text, reply_context=reply_context
    ):
        await _prompt_pick_time(
            msg,
            chat_id=_chat_id(update),
            user_id=uid,
            parsed=parsed,
            user_text=user_text,
            skip_attendee_names=skip_attendee_names,
            request_text=user_text,
            reply_context=reply_context,
        )
        return
    start_s = str(parsed.get("start") or "").strip()
    if start_s and "T" in start_s:
        try:
            start = cal_svc._parse_dt(start_s, uid)
            await _apply_slot_and_create(
                update,
                msg,
                uid,
                parsed,
                start,
                skip_attendee_names=skip_attendee_names,
            )
            return
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
            return
    await _create_from_parsed(
        update, msg, uid, parsed, skip_attendee_names=skip_attendee_names
    )


async def _create_from_parsed(
    update: Update,
    msg,
    uid: int,
    parsed: dict[str, Any],
    *,
    skip_attendee_names: set[str] | None = None,
) -> None:
    username = update.effective_user.username if update.effective_user else None
    try:
        result = cal_svc.create_event(
            uid,
            parsed,
            telegram_username=username,
            skip_attendee_names=skip_attendee_names,
        )
        from assistant.lib import zoom_gcal

        result = zoom_gcal.try_auto_attach_after_create(uid, result)
        from assistant.lib import telemost_gcal

        result = telemost_gcal.try_auto_attach_after_create(uid, result)
        cps.clear_pending(_chat_id(update), user_id=uid)
        await _send_created(msg, result, user_id=uid)
        try:
            from assistant.services import meeting_invites as inv

            bot = msg.get_bot() if msg else None
            if bot:
                await inv.notify_invitees(
                    bot,
                    organizer=update.effective_user,
                    organizer_uid=uid,
                    result=result,
                    organizer_username=username,
                )
        except Exception as e:
            print(f"[meeting_invite] notify err={e!r}")
    except Exception as e:
        await msg.reply_text(f"Ошибка календаря: {e}")


def _event_candidate_from_ref(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(ref.get("event_id") or ""),
        "calendar_id": str(ref.get("calendar_id") or "").strip() or None,
        "summary": str(ref.get("summary") or ""),
        "start": str(ref.get("start_iso") or "")[:16],
    }


def _resolve_replied_calendar_event(
    msg,
    *,
    user_id: int,
    bot_id: int,
) -> dict[str, Any] | None:
    rep = msg.reply_to_message if msg else None
    if not rep or not is_reply_to_bot(msg, bot_id=bot_id):
        return None
    ref = cps.get_event_message_ref(int(msg.chat.id), int(rep.message_id))
    if ref and int(ref.get("user_id") or 0) == int(user_id):
        return _event_candidate_from_ref(ref)
    raw = rep.text or rep.caption or ""
    parsed = parse_bot_calendar_event_message(raw)
    if not parsed:
        return None
    return {
        "id": "",
        "calendar_id": None,
        "summary": parsed.get("summary") or "",
        "start": "",
        "_match_query": parsed.get("match_query") or parsed.get("summary") or "",
        "_when_line": parsed.get("when_line") or "",
    }


def _match_date_from_when_line(when_line: str, *, now: datetime) -> str | None:
    """Достаёт YYYY-MM-DD из строки «21 июля 2026, 14:00–15:00»."""
    raw = (when_line or "").strip()
    if not raw:
        return None
    import re

    months = {
        "января": 1,
        "февраля": 2,
        "марта": 3,
        "апреля": 4,
        "мая": 5,
        "июня": 6,
        "июля": 7,
        "августа": 8,
        "сентября": 9,
        "октября": 10,
        "ноября": 11,
        "декабря": 12,
    }
    m = re.search(
        r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|"
        r"сентября|октября|ноября|декабря)(?:\s+(\d{4}))?",
        raw.lower(),
    )
    if not m:
        return None
    day = int(m.group(1))
    month = months[m.group(2)]
    year = int(m.group(3)) if m.group(3) else int(now.year)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


async def try_reply_amend_calendar_event(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Реплай на сообщение бота о созданной встрече: перенос / изменение / отмена."""
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return False
    text = (msg.text or msg.caption or "").strip()
    if not text or not msg.reply_to_message:
        return False
    bot_id = int(context.bot.id)
    action = calendar_detect_amend_kind(text)
    if not action:
        return False
    if not is_reply_to_bot(msg, bot_id=bot_id):
        return False
    uid = int(user.id)
    candidate = _resolve_replied_calendar_event(msg, user_id=uid, bot_id=bot_id)
    if not candidate:
        # Important: do NOT fall through to generic «удали встречу» (deletes today's event).
        await msg.reply_text(
            "Не смог определить встречу из этого сообщения. "
            "Напишите: «удали встречу <название>» или удалите её кнопкой под карточкой."
        )
        return True

    bot_user = (context.bot.username or os.getenv("TELEGRAM_BOT_USERNAME") or "")
    clean = strip_bot_mention(text, bot_user)
    rep_txt = reply_context_text(msg)
    merged = merge_calendar_text(clean, rep_txt)
    now = datetime.now(_tz(uid))
    req = "update_event" if action == "update" else "delete_event"
    parsed = nlu_llm.parse_calendar(
        merged,
        now=now,
        requested_intent=req,
        user_id=uid,
        reply_context=rep_txt,
    )
    if not parsed:
        await msg.reply_text("Не удалось разобрать запрос к календарю.")
        return True
    from assistant.lib.calendar_datetime_parse import calendar_normalize_parsed
    from assistant.lib.user_timezone import resolve_user_tz_name

    calendar_normalize_parsed(
        parsed,
        merged,
        today_iso=now.date().isoformat(),
        tz_name=resolve_user_tz_name(uid),
    )
    if parsed.get("need_more_info"):
        qs = parsed.get("questions") or []
        await msg.reply_text("\n".join(qs) if qs else "Уточните новое время или дату.")
        return True

    ev_id = str(candidate.get("id") or "").strip()
    if ev_id:
        found = [candidate]
    else:
        q = str(candidate.get("_match_query") or candidate.get("summary") or "").strip()
        if not q:
            q = _resolve_match_query(parsed, merged, clean)
        md = str(parsed.get("match_date") or "").strip() or None
        if not md:
            md = _match_date_from_when_line(
                str(candidate.get("_when_line") or ""), now=now
            )
        try:
            found = cal_svc.find_events_by_query(uid, q, md)
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
            return True
        if not found and md:
            # Title search without day if day-filtered search missed.
            try:
                found = cal_svc.find_events_by_query(uid, q, None)
            except Exception as e:
                await msg.reply_text(f"Ошибка: {e}")
                return True
        if len(found) == 1:
            pass
        elif len(found) > 1:
            await _clarify_or_run_events(
                msg, update, uid=uid, parsed=parsed, found=found, action=action
            )
            return True
        else:
            await msg.reply_text("Не нашёл встречу из этого сообщения.")
            return True

    await _clarify_or_run_events(
        msg, update, uid=uid, parsed=parsed, found=found, action=action
    )
    return True


async def try_continue_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Ответ email для нового контакта или реплай на уточнение."""
    if await try_reply_amend_calendar_event(update, context):
        return True
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return False
    chat_id = _chat_id(update)
    st = cps.get_pending(chat_id, ttl_sec=_PENDING_TTL, user_id=int(user.id))
    if not st or int(st.get("user_id") or 0) != int(user.id):
        return False
    kind = str(st.get("kind") or "")
    if kind == "missing_attendees":
        text = (msg.text or "").strip()
        quick = _parse_contact_quick_line(text)
        if quick:
            parsed_evt = dict(st.get("parsed") or {})
            missing = [str(x) for x in (st.get("missing_names") or [])]
            await _save_contact_and_continue(
                update,
                msg,
                user_id=int(user.id),
                chat_id=chat_id,
                draft=quick,
                parsed=parsed_evt,
                missing_names=missing,
                request_text=str(st.get("request_text") or ""),
                reply_context=str(st.get("reply_context") or ""),
            )
            return True
    if kind == "await_contact_details":
        text = (msg.text or "").strip()
        quick = _parse_contact_quick_line(text)
        parsed_evt = dict(st.get("parsed") or {})
        missing = [str(x) for x in (st.get("missing_names") or [])]
        uid = int(user.id)
        if quick:
            await _save_contact_and_continue(
                update,
                msg,
                user_id=uid,
                chat_id=chat_id,
                draft=quick,
                parsed=parsed_evt,
                missing_names=missing,
                request_text=str(st.get("request_text") or ""),
                reply_context=str(st.get("reply_context") or ""),
            )
            return True
        step = str(st.get("step") or "name")
        draft = dict(st.get("draft") or {})
        if step == "name":
            if len(text) < 2:
                await msg.reply_text("Имя слишком короткое.")
                return True
            draft["name"] = text
            cps.set_pending(
                chat_id,
                {**st, "step": "email", "draft": draft},
                user_id=int(user.id),
            )
            await msg.reply_text(_contact_step_message("email"))
            return True
        if step == "email":
            m = EMAIL_RE.search(text)
            if not m:
                await msg.reply_text("Нужен корректный email, например: ivan@mail.ru")
                return True
            draft["email"] = m.group(0).lower()
            cps.set_pending(
                chat_id,
                {**st, "step": "telegram_username", "draft": draft},
                user_id=int(user.id),
            )
            await msg.reply_text(_contact_step_message("telegram_username"))
            return True
        if step == "telegram_username":
            low = text.lower().strip()
            if low not in {"—", "-", "нет", "skip", "пропустить"}:
                tg = text.lstrip("@").strip()
                if tg and not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{4,31}", tg):
                    await msg.reply_text(
                        "Некорректный username. Пример: ivanov или @ivanov"
                    )
                    return True
                if tg:
                    draft["telegram_username"] = tg
            await _save_contact_and_continue(
                update,
                msg,
                user_id=uid,
                chat_id=chat_id,
                draft=draft,
                parsed=parsed_evt,
                missing_names=missing,
                request_text=str(st.get("request_text") or ""),
                reply_context=str(st.get("reply_context") or ""),
            )
            return True
    if kind == "await_pick_time":
        text = (msg.text or "").strip()
        # Не перехватываем явные Zoom-команды (delete/reschedule) — иначе
        # «reschedule zoom to 4pm» отвечает «Напишите время…» и PATCH не уходит.
        from assistant.nlu.regex import parse_zoom_route

        zr = parse_zoom_route(text)
        if zr and zr.sub_intent in ("zoom_delete", "zoom_update", "zoom"):
            cps.clear_pending(chat_id, user_id=int(user.id))
            return False
        parsed_evt = dict(st.get("parsed") or {})
        request_text = str(st.get("request_text") or "")
        reply_context = str(st.get("reply_context") or "")
        cal_svc.ensure_event_title_from_request(
            parsed_evt, request_text, reply_context=reply_context
        )
        fallback_day = str(st.get("day_iso") or cal_svc.parsed_event_day(parsed_evt) or "")
        skip_set = {str(x).strip().lower() for x in (st.get("skip_names") or [])}
        slot_dur = int(st.get("slot_duration_min") or 0)
        if slot_dur > 0:
            parsed_evt["duration_min"] = slot_dur
        uid = int(user.id)
        tz = _tz(uid)
        today = datetime.now(tz).date()
        day, th = resolve_pick_day_and_time(
            text, today=today, fallback_day_iso=fallback_day
        )
        if not th:
            await msg.reply_text(
                "Напишите время, например: 10, 14:30 или «сегодня в 22:00»."
            )
            return True
        if day != fallback_day[:10]:
            parsed_evt["slot_day"] = day
            parsed_evt["slot_day_strict"] = True
        try:
            from datetime import date as date_cls, time as time_cls
            from assistant.lib.calendar_datetime_parse import calendar_ensure_future_datetime

            d = date_cls.fromisoformat(day[:10])
            start_local = datetime.combine(d, time_cls(th[0], th[1]))
            now = datetime.now(tz)
            fixed = calendar_ensure_future_datetime(
                start_local,
                now.replace(tzinfo=None),
                today,
                intent="create_event",
            )
            start = fixed.replace(tzinfo=tz)
        except ValueError:
            await msg.reply_text("Не удалось разобрать дату. Создайте встречу заново.")
            return True
        await _apply_slot_and_create(
            update,
            msg,
            uid,
            parsed_evt,
            start,
            skip_attendee_names=skip_set,
        )
        return True
    if kind == "await_clarify":
        text = (msg.text or "").strip()
        candidates = list(st.get("candidates") or [])
        parsed_evt = dict(st.get("parsed") or {})
        action = str(st.get("action") or "update")
        try:
            idx = int(text) - 1
        except ValueError:
            await msg.reply_text("Ответьте номером встречи из списка.")
            return True
        if idx < 0 or idx >= len(candidates):
            await msg.reply_text("Некорректный номер.")
            return True
        picked = candidates[idx]
        ev_id = str(picked.get("id") or "")
        cal_id = str(picked.get("calendar_id") or "").strip() or None
        cps.clear_pending(chat_id, user_id=int(user.id))
        try:
            from assistant.lib import zoom_gcal

            if action == "delete":
                zoom_gcal.delete_zoom_for_event(
                    int(user.id), ev_id, calendar_id=cal_id
                )
                cal_svc.delete_event(int(user.id), ev_id, calendar_id=cal_id)
                title = str(picked.get("summary") or "Встреча")
                await msg.reply_text(f"Встреча «{title}» удалена.")
            else:
                result = cal_svc.update_event(
                    int(user.id), ev_id, parsed_evt, calendar_id=cal_id
                )
                zoom_gcal.sync_zoom_after_calendar_update(
                    int(user.id), ev_id, parsed_evt, calendar_id=cal_id
                )
                await _send_event_result(
                    msg, result, user_id=int(user.id), updated=True
                )
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return True
    return False


def _resolve_match_query(parsed: dict[str, Any], merged: str, clean: str) -> str:
    q = str(parsed.get("match_query") or "").strip()
    if not q:
        q = calendar_extract_match_query(merged) or calendar_extract_match_query(clean)
    return q


async def _clarify_or_run_events(
    msg,
    update: Update,
    *,
    uid: int,
    parsed: dict[str, Any],
    found: list[dict[str, Any]],
    action: str,
) -> None:
    if not found:
        verb = "удалить" if action == "delete" else "изменить"
        await msg.reply_text(f"Не нашёл встречу для того, чтобы {verb}.")
        return
    if len(found) > 1:
        verb = "удалить" if action == "delete" else "изменить"
        lines = [f"Какую встречу {verb}? Ответьте номером:"]
        for i, ev in enumerate(found[:8], 1):
            lines.append(f"{i}. {ev.get('summary')} ({str(ev.get('start') or '')[:16]})")
        cps.set_pending(
            _chat_id(update),
            {
                "kind": "await_clarify",
                "action": action,
                "user_id": uid,
                "parsed": parsed,
                "candidates": found[:8],
            },
            user_id=uid,
        )
        await msg.reply_text("\n".join(lines))
        return
    ev_id = str(found[0].get("id") or "")
    cal_id = str(found[0].get("calendar_id") or "").strip() or None
    title = str(found[0].get("summary") or "Встреча")
    try:
        from assistant.lib import zoom_gcal

        if action == "delete":
            zoom_gcal.delete_zoom_for_event(uid, ev_id, calendar_id=cal_id)
            cal_svc.delete_event(uid, ev_id, calendar_id=cal_id)
            await msg.reply_text(f"Встреча «{title}» удалена.")
        else:
            result = cal_svc.update_event(uid, ev_id, parsed, calendar_id=cal_id)
            zoom_gcal.sync_zoom_after_calendar_update(
                uid, ev_id, parsed, calendar_id=cal_id
            )
            await _send_event_result(msg, result, user_id=uid, updated=True)
    except Exception as e:
        await msg.reply_text(f"Ошибка: {e}")


async def handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    kind: str | None,
    reply_context: str = "",
    reply_author: dict[str, Any] | None = None,
) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    uid = int(user.id)
    bot_user = (context.bot.username or os.getenv("TELEGRAM_BOT_USERNAME") or "")
    clean = strip_bot_mention(text, bot_user)
    rep_txt = reply_context or reply_context_text(msg)
    author = reply_author or reply_author_info(msg)
    merged = merge_calendar_text(clean, rep_txt)
    now = datetime.now(_tz(uid))
    intent_map = {
        "create": "create_event",
        "update": "update_event",
        "delete": "delete_event",
        "free_slots": "free_slots",
        "free": "free_slots",
        "list": "list_events",
    }
    req = intent_map.get((kind or "").strip().lower())
    parsed = nlu_llm.parse_calendar(
        merged,
        now=now,
        requested_intent=req,
        user_id=uid,
        reply_context=rep_txt,
        reply_author=author,
    )
    if not parsed:
        await msg.reply_text("Не удалось разобрать запрос к календарю.")
        return
    from assistant.lib.calendar_datetime_parse import calendar_normalize_parsed
    from assistant.lib.user_timezone import resolve_user_tz_name

    calendar_normalize_parsed(
        parsed,
        merged,
        today_iso=now.date().isoformat(),
        tz_name=resolve_user_tz_name(uid),
    )
    enrich_parsed_attendees(parsed, merged)
    add_reply_author_as_attendee(
        parsed, author, owner_user_id=uid, owner_username=user.username
    )
    intent = str(parsed.get("intent") or "none")
    if intent == "free_slots" and not calendar_free_slots_hide_meetings(merged):
        intent = "list_events"
    if parsed.get("need_more_info"):
        qs = parsed.get("questions") or []
        await msg.reply_text("\n".join(qs) if qs else "Уточните дату и время встречи.")
        return
    day = str(parsed.get("free_slots_date") or parsed.get("match_date") or now.date().isoformat())
    if intent == "create_event":
        cal_svc.normalize_event_title(parsed)
        missing = cal_svc.unresolved_attendee_names(
            uid, parsed, telegram_username=user.username
        )
        if missing:
            await _prompt_missing_attendees(
                msg,
                chat_id=_chat_id(update),
                user_id=uid,
                parsed=parsed,
                missing=missing,
                request_text=clean,
                reply_context=rep_txt,
            )
            return
        await _maybe_create_or_pick_time(
            update,
            msg,
            uid,
            parsed,
            clean,
            reply_context=rep_txt,
            skip_attendee_names=None,
        )
        return
    if intent == "list_events":
        try:
            events = cal_svc.list_events_day(uid, day)
            body = cal_svc.format_events_list(events, f"Встречи на {day}")
            await reply_formatted(
                msg,
                body,
                rich_markdown=True,
                disable_web_page_preview=True,
            )
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return
    if intent == "free_slots":
        try:
            body = cal_svc.free_slots_message(
                uid, parsed, day, telegram_username=user.username
            )
            await msg.reply_text(body)
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return
    if intent == "search_events":
        q = str(parsed.get("match_query") or clean or merged).strip()
        try:
            events = cal_svc.search_events(uid, q)
            body = cal_svc.format_events_list(events, f"Поиск «{q}»")
            await reply_formatted(
                msg,
                body,
                rich_markdown=True,
                disable_web_page_preview=True,
            )
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
        return
    if intent in {"update_event", "delete_event"}:
        q = _resolve_match_query(parsed, merged, clean)
        md = str(parsed.get("match_date") or "").strip() or None
        # Do not default delete to "today" when query is empty — that deletes the
        # wrong meeting (e.g. recurring «Планирование») instead of the replied one.
        if intent == "delete_event" and not md and q:
            md = now.date().isoformat()
        action = "delete" if intent == "delete_event" else "update"
        try:
            found = cal_svc.find_events_by_query(uid, q, md)
        except Exception as e:
            await msg.reply_text(f"Ошибка: {e}")
            return
        if intent == "delete_event" and not q and not found:
            await msg.reply_text(
                "Какую встречу удалить? Напишите название или ответьте реплаем "
                "на сообщение бота о встрече."
            )
            return
        await _clarify_or_run_events(
            msg, update, uid=uid, parsed=parsed, found=found, action=action
        )
        return
    await msg.reply_text("Не понял, что сделать с календарём. Переформулируйте запрос.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    user = update.effective_user
    msg = q.message if q else None
    if not q or not user or not msg:
        return
    data = (q.data or "").strip()
    chat_id = int(msg.chat.id)
    uid = int(user.id)

    if data.startswith("inv:"):
        from assistant.services import meeting_invites as inv

        if await inv.handle_invite_rsvp_callback(q, invitee_uid=uid):
            return
        await q.answer()
        return

    await q.answer()

    if data == "cal:skip_attendees":
        st = cps.get_pending(chat_id, ttl_sec=_PENDING_TTL, user_id=uid)
        if not st or st.get("kind") != "missing_attendees" or int(st.get("user_id") or 0) != uid:
            await q.edit_message_text("Сессия устарела. Создайте встречу заново.")
            return
        parsed = dict(st.get("parsed") or {})
        missing = {str(n).strip().lower() for n in (st.get("missing_names") or [])}
        request_text = str(st.get("request_text") or "")
        reply_context = str(st.get("reply_context") or "")
        cps.clear_pending(chat_id, user_id=uid)
        try:
            await _maybe_create_or_pick_time(
                update,
                msg,
                uid,
                parsed,
                request_text,
                reply_context=reply_context,
                skip_attendee_names=missing,
            )
        except Exception as e:
            await q.edit_message_text(f"Ошибка календаря: {e}")
        return

    if data.startswith("cal:stp:"):
        token = data[8:]
        start_iso = cps.pop_slot_pick_token(token, user_id=uid)
        st = cps.get_pending(chat_id, ttl_sec=_PENDING_TTL, user_id=uid)
        if not start_iso or not st or st.get("kind") != "await_pick_time":
            await q.answer("Сессия устарела.", show_alert=True)
            return
        parsed = dict(st.get("parsed") or {})
        skip_set = {str(x).strip().lower() for x in (st.get("skip_names") or [])}
        slot_dur = int(st.get("slot_duration_min") or 0)
        if slot_dur > 0:
            parsed["duration_min"] = slot_dur
        try:
            start = datetime.fromisoformat(start_iso)
            if start.tzinfo is None:
                start = start.replace(tzinfo=_tz(uid))
        except ValueError:
            await q.edit_message_text("Некорректное время.")
            return
        await q.edit_message_text(f"Выбрано {start.strftime('%H:%M')}…")
        await _apply_slot_and_create(
            update, msg, uid, parsed, start, skip_attendee_names=skip_set
        )
        return

    if data.startswith("cal:fc:"):
        token = data[7:]
        start_iso = cps.pop_force_create_token(token, user_id=uid)
        st = cps.get_pending(chat_id, ttl_sec=_PENDING_TTL, user_id=uid)
        if not start_iso or not st or st.get("kind") != "await_pick_time":
            await q.answer("Сессия устарела.", show_alert=True)
            return
        parsed = dict(st.get("parsed") or {})
        skip_set = {str(x).strip().lower() for x in (st.get("skip_names") or [])}
        try:
            start = datetime.fromisoformat(start_iso)
            if start.tzinfo is None:
                start = start.replace(tzinfo=_tz(uid))
        except ValueError:
            await q.edit_message_text("Некорректное время.")
            return
        cps.clear_pending(chat_id, user_id=uid)
        await q.edit_message_text(
            f"Создаю встречу на {start.strftime('%H:%M')} (без проверки занятости)…"
        )
        await _finalize_create_at_start(
            update, msg, uid, parsed, start, skip_attendee_names=skip_set
        )
        return

    if data == "cal:add_contact":
        st = cps.get_pending(chat_id, ttl_sec=_PENDING_TTL, user_id=uid)
        if not st or st.get("kind") != "missing_attendees" or int(st.get("user_id") or 0) != uid:
            await q.edit_message_text("Сессия устарела. Создайте встречу заново.")
            return
        missing = list(st.get("missing_names") or [])
        if not missing:
            await q.edit_message_text("Нет участников для добавления.")
            return
        hint = str(missing[0])
        draft: dict[str, str] = {}
        rep_author = dict((st.get("parsed") or {}).get("_reply_author") or {})
        if rep_author:
            fn = str(rep_author.get("first_name") or "").strip()
            ln = str(rep_author.get("last_name") or "").strip()
            if fn:
                draft["name"] = " ".join(p for p in (fn, ln) if p).strip()
            un = str(rep_author.get("username") or "").strip()
            if un:
                draft["telegram_username"] = un
        cps.set_pending(
            chat_id,
            {
                "kind": "await_contact_details",
                "step": "name" if not draft.get("name") else "email",
                "user_id": uid,
                "draft": draft,
                "parsed": dict(st.get("parsed") or {}),
                "missing_names": missing,
                "hint_name": hint,
                "request_text": str(st.get("request_text") or ""),
                "reply_context": str(st.get("reply_context") or ""),
            },
            user_id=uid,
        )
        step = "email" if draft.get("name") else "name"
        await q.edit_message_text(_contact_step_message(step, hint_name=hint))
        return

    if data.startswith("ced:"):
        token = data[4:]
        consumed = cal_svc.consume_delete_token(token, user_id=uid)
        if not consumed:
            await q.answer("Не удалось удалить (сессия устарела).", show_alert=True)
            return
        ev_id, cal_id = consumed
        try:
            from assistant.lib import zoom_gcal

            zoom_gcal.delete_zoom_for_event(uid, ev_id, calendar_id=cal_id)
            cal_svc.delete_event(uid, ev_id, calendar_id=cal_id)
            await q.edit_message_text("Встреча удалена.")
        except Exception as e:
            await q.edit_message_text(f"Не удалось удалить: {e}")
        return
