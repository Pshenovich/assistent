"""Постановка Leo-задачи контакту из чата («поставь задачу Ульяне…»)."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from assistant.config import CALENDAR_TZ
from assistant.lib.calendar_attendees import find_contact_by_name
from assistant.lib.slot_time import parse_datetime_from_user_text
from assistant.lib.task_notify import notify_task_assignee, task_title_link_html
from assistant.stores import calendar_tasks as calendar_tasks_store
from assistant.stores import note_members
from assistant.stores.contacts_store import contact_display_name, normalize_telegram_username

_BITRIX_RE = re.compile(r"(?i)\b(битрикс|bitrix24?)\b")
_ASR_PREFIX_RE = re.compile(
    r"(?is)^\s*(?:"
    r"лев[,!\s]+|leo[,!\s]+|бот[,!\s]+|"
    r"слушай[,!\s]+|слушай|э+[,!\s]*|ну[,!\s]+|"
    r"пожалуйста[,!\s]+"
    r")+"
)
_ASSIGN_TRIGGER_RE = re.compile(
    r"(?is)(?:"
    r"поставь(?:те)?(?:\s*,?\s*пожалуйста)?\s+задачу|"
    r"поставь(?:те)?(?:\s*,?\s*пожалуйста)?|"
    r"закинь(?:те)?(?:\s*,?\s*пожалуйста)?|"
    r"передай(?:те)?(?:\s*,?\s*пожалуйста)?|"
    r"назначь(?:те)?(?:\s*,?\s*пожалуйста)?\s+задачу|"
    r"назначь(?:те)?(?:\s*,?\s*пожалуйста)?|"
    r"создай(?:те)?(?:\s*,?\s*пожалуйста)?\s+задачу"
    r")\s+(?P<body>.+)$"
)
_NAME_LEAD_RE = re.compile(
    r"(?is)^(?P<name>@[A-Za-z][A-Za-z0-9_]{2,31}|[А-ЯЁA-Zа-яё][а-яёa-zA-ZёЁ-]{1,40})"
    r"(?:\s+(?:на\s+(?:сегодня|завтра|послезавтра)|"
    r"сегодня|завтра|послезавтра))?"
    r"(?:\s+(?:в\s+задаче|задачу|надо|нужно))?\s*"
    r"(?P<title>.+)$"
)
_STRIP_FILLER_RE = re.compile(
    r"(?i)^\s*(?:в\s+задаче|задачу|надо|нужно|чтобы|"
    r"на\s+(?:сегодня|завтра|послезавтра)|сегодня|завтра|послезавтра)\s+"
)
_PERSON_TOKEN_RE = re.compile(
    r"(?i)(?:@[A-Za-z][A-Za-z0-9_]{2,31}|[А-ЯЁA-Zа-яё][а-яёa-zA-ZёЁ-]{1,40})"
)

URGENCY_EMOJI = {
    "hot": "🚨",
    "imp": "❗️",
    "chill": "😴",
}
_URGENCY_PREFIX_RE = re.compile(
    r"^(?:🚨|❗️|❗|😴)\s*"
)
_LONG_TITLE_WORDS = 5


def _prep_assign_text(text: str) -> str:
    raw = (text or "").strip()
    raw = _ASR_PREFIX_RE.sub("", raw).strip()
    raw = re.sub(r"\s+", " ", raw)
    return raw


def _has_person_token(text: str) -> bool:
    return bool(_PERSON_TOKEN_RE.search(text or ""))


def is_assign_task_request(text: str) -> bool:
    """Leo-постановка другому человеку — не Битрикс."""
    raw = _prep_assign_text(text)
    if not raw or _BITRIX_RE.search(raw):
        return False
    low = raw.lower()
    # Явно не задача: встреча/созвон/слоты.
    if re.search(r"(?i)\b(встреч|созвон|слот|zoom|телемост)\w*\b", raw):
        return False
    m = _ASSIGN_TRIGGER_RE.search(raw)
    if not m:
        return False
    body = str(m.group("body") or "")
    if "задач" in low or re.search(r"(?i)\bв\s+задач", raw):
        # «поставь/создай задачу …» без слова «битрикс» — всегда Leo,
        # даже если имя контакта ASR исказил (иначе уходит в Bitrix).
        return True
    # «поставь Ульяне на сегодня сделать отчёт» без слова «задачу»
    return _has_person_token(body)


def parse_assign_task_request(text: str) -> dict[str, str] | None:
    raw = _prep_assign_text(text)
    m = _ASSIGN_TRIGGER_RE.search(raw)
    if not m:
        return None
    body = str(m.group("body") or "").strip()
    body = re.sub(r"(?i)^задачу\s+", "", body).strip()
    nm = _NAME_LEAD_RE.match(body)
    if not nm:
        return None
    name = str(nm.group("name") or "").strip()
    title = str(nm.group("title") or "").strip()
    # «на сегодня задачу сделать…» / «задачу сделать…»
    title = _STRIP_FILLER_RE.sub("", title).strip()
    title = re.sub(r"(?i)^\s*задачу\s+", "", title).strip()
    title = _STRIP_FILLER_RE.sub("", title).strip()
    if not name:
        return None
    if not title or title.lower() in {"задачу", "задача", "на сегодня", "сегодня"}:
        title = "Задача"
    return {"assignee_name": name, "title": title}


def word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", (text or "").strip()) if w])


def fallback_short_title(text: str, *, max_words: int = 5) -> str:
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    if not words:
        return "Задача"
    short = " ".join(words[:max_words]).rstrip(".,;:!")
    return capitalize_title(short) or "Задача"


def resolve_title_and_description(raw_title: str) -> tuple[str, str]:
    """Если текст длиннее 5 слов — в description целиком, title кратко."""
    body = (raw_title or "").strip()
    if not body:
        return "Задача", ""
    if word_count(body) <= _LONG_TITLE_WORDS:
        return capitalize_title(body), ""
    title = fallback_short_title(body)
    try:
        from assistant.nlu import llm as nlu_llm

        formatted = nlu_llm.format_note(body)
        if formatted and (formatted.get("title") or "").strip():
            title = capitalize_title(str(formatted["title"]).strip())
    except Exception as e:
        print(f"[assign_task] title_gen_failed err={e!r}")
    return title or "Задача", body


def capitalize_title(title: str) -> str:
    s = (title or "").strip()
    if not s:
        return s
    return s[0].upper() + s[1:]


def strip_urgency_prefix(title: str) -> str:
    return _URGENCY_PREFIX_RE.sub("", (title or "").strip()).strip()


def apply_urgency_emoji(title: str, emoji: str) -> str:
    base = strip_urgency_prefix(title) or "Задача"
    mark = (emoji or "").strip()
    if not mark:
        return base
    return f"{mark} {base}"


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(CALENDAR_TZ)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _owner_username(update: Update) -> str | None:
    user = update.effective_user
    if not user:
        return None
    return normalize_telegram_username(user.username or "") or None


def _strip_when_from_title(title: str) -> str:
    s = (title or "").strip()
    s = re.sub(
        r"(?i)\s*(?:завтра|сегодня|послезавтра|"
        r"в\s+\d{1,2}(?::\d{2})?|"
        r"к\s+\d{1,2}(?::\d{2})?|"
        r"\d{1,2}[:.]\d{2})\s*",
        " ",
        s,
    )
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.-")
    return s or title


def _when_phrase(row: dict, *, tz: ZoneInfo, now: datetime) -> str:
    if row.get("all_day"):
        raw = str(row.get("start_at") or "").strip()
        start = None
        if raw:
            try:
                start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                start = None
        if start is not None:
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            day = start.astimezone(tz).date()
            today = now.astimezone(tz).date()
            if day == today:
                return "на сегодня"
            if day == today + timedelta(days=1):
                return "на завтра"
            if day == today + timedelta(days=2):
                return "на послезавтра"
        return calendar_tasks_store.chip_label(row, tz=tz)
    return calendar_tasks_store.chip_label(row, tz=tz)


def urgency_keyboard(task_id: int | str) -> InlineKeyboardMarkup:
    tid = int(task_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🚨", callback_data=f"tsk:urg:{tid}:hot"),
                InlineKeyboardButton("❗️", callback_data=f"tsk:urg:{tid}:imp"),
                InlineKeyboardButton("😴", callback_data=f"tsk:urg:{tid}:chill"),
            ]
        ]
    )


def _confirm_html(row: dict, *, tz: ZoneInfo, now: datetime) -> str:
    who = html.escape(str(row.get("assignee_name") or "исполнителя"))
    link = task_title_link_html(str(row.get("title") or "Задача"), row["id"])
    when = html.escape(_when_phrase(row, tz=tz, now=now))
    return f"Поставил задачу на {who} — {link}\nКогда: {when}."


async def handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return
    uid = int(user.id)
    raw = (text or msg.text or msg.caption or "").strip()
    parsed = parse_assign_task_request(raw)
    if not parsed:
        await msg.reply_text(
            "Не разобрал задачу. Пример: «поставь задачу Ульяне сделать презентацию завтра к 10:00»."
        )
        return

    owner_uname = _owner_username(update)
    contact = find_contact_by_name(
        uid, parsed["assignee_name"], telegram_username=owner_uname
    )
    if not contact:
        await msg.reply_text(
            f"Контакт «{parsed['assignee_name']}» не найден. "
            "Добавьте его в Профиль → Контакты."
        )
        return

    email = str(contact.get("email") or "").strip()
    try:
        assignee_uid, contact = note_members.resolve_contact_telegram_id(
            owner_user_id=uid,
            email=email or None,
            telegram_user_id=contact.get("telegram_user_id"),
            telegram_username=contact.get("telegram_username")
            or contact.get("tg_username"),
            owner_username=owner_uname,
        )
    except ValueError:
        await msg.reply_text(
            f"У контакта «{contact_display_name(contact)}» нет Telegram в Leo. "
            "Попросите открыть мини-приложение хотя бы раз."
        )
        return

    if int(assignee_uid) == uid:
        await msg.reply_text("Это была бы задача себе — укажите другого исполнителя.")
        return

    tz = _tz()
    now = datetime.now(tz)
    when = parse_datetime_from_user_text(raw, now=now)
    all_day = when is None
    start = (
        when
        if when is not None
        else now.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    title = parsed["title"]
    if when is not None:
        title = _strip_when_from_title(title)
    title, description = resolve_title_and_description(title)

    row = calendar_tasks_store.create_task(
        uid,
        title=title,
        description=description,
        start_at=start,
        end_at=None,
        assignee_user_id=assignee_uid,
        assignee_email=email,
        assignee_name=contact_display_name(contact),
        all_day=all_day,
        tz=tz,
    )

    assigner = (user.full_name or user.first_name or "Участник").strip()
    notify_task_assignee(
        assignee_user_id=assignee_uid,
        task=row,
        assigner_name=assigner,
    )

    await msg.reply_text(
        _confirm_html(row, tz=tz, now=now),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=urgency_keyboard(row["id"]),
    )


async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    m = re.match(r"^tsk:urg:(\d+):(hot|imp|chill)$", q.data)
    if not m:
        await q.answer()
        return
    user = update.effective_user
    if not user:
        await q.answer()
        return
    tid = int(m.group(1))
    code = m.group(2)
    emoji = URGENCY_EMOJI.get(code)
    if not emoji:
        await q.answer()
        return
    uid = int(user.id)
    existing = calendar_tasks_store.get_task_for_user(uid, tid)
    if not existing:
        await q.answer("Задача не найдена", show_alert=True)
        return
    if str(existing.get("owner_user_id") or "") != str(uid):
        await q.answer("Срочность может менять только автор", show_alert=True)
        return
    new_title = apply_urgency_emoji(str(existing.get("title") or ""), emoji)
    row = calendar_tasks_store.update_task(uid, tid, title=new_title)
    if not row:
        await q.answer("Не удалось обновить", show_alert=True)
        return
    await q.answer(f"Срочность: {emoji}")
    tz = _tz()
    now = datetime.now(tz)
    try:
        await q.edit_message_text(
            _confirm_html(row, tz=tz, now=now),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=urgency_keyboard(tid),
        )
    except Exception:
        pass
