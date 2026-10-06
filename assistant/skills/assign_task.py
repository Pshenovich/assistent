"""Постановка Leo-задачи контакту из чата («поставь задачу Ульяне…»)."""

from __future__ import annotations

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
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
_ASSIGN_TRIGGER_RE = re.compile(
    r"(?is)^\s*(?:"
    r"поставь(?:\s+пожалуйста)?\s+задачу|"
    r"поставь(?:\s+пожалуйста)?|"
    r"закинь(?:\s+пожалуйста)?|"
    r"передай(?:\s+пожалуйста)?|"
    r"назначь(?:\s+пожалуйста)?\s+задачу|"
    r"назначь(?:\s+пожалуйста)?"
    r")\s+(?P<body>.+)$"
)
_NAME_LEAD_RE = re.compile(
    r"(?is)^(?P<name>@[A-Za-z][A-Za-z0-9_]{2,31}|[А-ЯЁA-Z][а-яёa-zA-ZёЁ-]{1,40})"
    r"(?:\s+(?:в\s+задаче|задачу|надо|нужно))?\s*"
    r"(?P<title>.+)$"
)
_STRIP_FILLER_RE = re.compile(
    r"(?i)^\s*(?:в\s+задаче|задачу|надо|нужно|чтобы)\s+"
)


def is_assign_task_request(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or _BITRIX_RE.search(raw):
        return False
    if not _ASSIGN_TRIGGER_RE.match(raw):
        return False
    low = raw.lower()
    if "задач" in low:
        return True
    return bool(re.search(r"(?i)\bв\s+задач", raw))


def parse_assign_task_request(text: str) -> dict[str, str] | None:
    m = _ASSIGN_TRIGGER_RE.match((text or "").strip())
    if not m:
        return None
    body = str(m.group("body") or "").strip()
    body = re.sub(r"(?i)^задачу\s+", "", body).strip()
    nm = _NAME_LEAD_RE.match(body)
    if not nm:
        return None
    name = str(nm.group("name") or "").strip()
    title = str(nm.group("title") or "").strip()
    title = _STRIP_FILLER_RE.sub("", title).strip()
    title = re.sub(r"(?i)^\s*задачу\s+", "", title).strip()
    if not name or not title:
        return None
    return {"assignee_name": name, "title": title}


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

    row = calendar_tasks_store.create_task(
        uid,
        title=title,
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

    who = html.escape(contact_display_name(contact))
    link = task_title_link_html(str(row.get("title") or "Задача"), row["id"])
    when_label = calendar_tasks_store.chip_label(row, tz=tz)
    when_html = (
        "на сегодня (вверху календаря)"
        if all_day
        else html.escape(when_label)
    )
    await msg.reply_text(
        f"Поставил(а) {who} задачу {link}.\nКогда: {when_html}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
