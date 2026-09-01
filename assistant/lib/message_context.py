"""Контекст сообщения для NLU (reply, группы, @mention)."""

from __future__ import annotations

import os
import re
from typing import Any

from telegram import Message, Update
from telegram.constants import MessageEntityType

from assistant.stores.contacts_store import contact_display_name, normalize_telegram_username


def strip_bot_mention(text: str, bot_username: str) -> str:
    uname = (bot_username or "").lstrip("@").lower()
    if not uname:
        return (text or "").strip()
    out = re.sub(rf"@{re.escape(uname)}\b", "", text or "", flags=re.IGNORECASE)
    # Telegram / users often type "@Name_bot" as "@Name bot"
    if uname.endswith("_bot"):
        head = uname[: -len("_bot")]
        if head:
            out = re.sub(
                rf"@{re.escape(head)}\s+bot\b",
                "",
                out,
                flags=re.IGNORECASE,
            )
            out = re.sub(rf"@{re.escape(head)}\b", "", out, flags=re.IGNORECASE)
            out = re.sub(r"(?i)^\s*bot\b[\s:,-]*", "", out)
    return " ".join(out.split()).strip()


def telegram_message_link(
    msg: Message | None,
    *,
    bot_username: str | None = None,
    bot_id: int | None = None,
) -> str | None:
    """Ссылка на сообщение в Telegram.

    В личке с ботом chat.id — это id пользователя, не бота; для private используем
    t.me/{bot_username}/{message_id} или tg://openmessage с id бота.
    """
    if not msg or not msg.chat or not msg.message_id:
        return None
    chat = msg.chat
    mid = int(msg.message_id)
    if chat.type == "private":
        uname = (bot_username or os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
        if uname:
            return f"https://t.me/{uname}/{mid}"
        if bot_id:
            return f"tg://openmessage?chat_id={int(bot_id)}&message_id={mid}"
        return None
    if chat.username:
        return f"https://t.me/{chat.username}/{mid}"
    if chat.type in ("group", "supergroup", "channel"):
        cid = str(chat.id)
        if cid.startswith("-100"):
            cid = cid[4:]
        elif cid.startswith("-"):
            cid = cid[1:]
        if cid.isdigit():
            return f"https://t.me/c/{cid}/{mid}"
    return None


def reply_context_text(msg: Message | None) -> str:
    if not msg or not msg.reply_to_message:
        return ""
    rep = msg.reply_to_message
    return (rep.text or rep.caption or "").strip()


def reply_author_info(msg: Message | None) -> dict[str, Any] | None:
    if not msg or not msg.reply_to_message:
        return None
    u = msg.reply_to_message.from_user
    if not u or u.is_bot:
        return None
    return {
        "telegram_user_id": int(u.id),
        "first_name": str(u.first_name or "").strip(),
        "last_name": str(u.last_name or "").strip(),
        "username": str(u.username or "").strip(),
    }


_RE_BOT_EVENT_CREATED = re.compile(
    r"(?:Создана|Обновлена)\s+встреча:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)


def is_reply_to_bot(msg: Message | None, *, bot_id: int) -> bool:
    if not msg or not msg.reply_to_message or not msg.reply_to_message.from_user:
        return False
    return int(msg.reply_to_message.from_user.id) == int(bot_id)


def parse_bot_calendar_event_message(text: str) -> dict[str, str] | None:
    """Разбор текста «Создана встреча: …» из сообщения бота (fallback без store)."""
    raw = re.sub(r"<[^>]+>", "", text or "").strip()
    if "встреча:" not in raw.lower():
        return None
    m = _RE_BOT_EVENT_CREATED.search(raw)
    if not m:
        return None
    block = (m.group(1) or "").strip()
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    title = (lines[0] if lines else block).strip()
    if not title:
        return None
    out: dict[str, str] = {"summary": title, "match_query": title}
    if len(lines) > 1:
        out["when_line"] = lines[1]
    return out


def merge_calendar_text(user_text: str, reply_text: str) -> str:
    u = (user_text or "").strip()
    r = (reply_text or "").strip()
    if u and r:
        return f"{u}\n\nКонтекст реплая: {r}"
    return u or r


def add_reply_author_as_attendee(
    parsed: dict[str, Any],
    author: dict[str, Any] | None,
    *,
    owner_user_id: int,
    owner_username: str | None,
) -> None:
    if not author:
        return
    author_id = int(author.get("telegram_user_id") or 0)
    if author_id and author_id == int(owner_user_id):
        return
    from assistant.lib.calendar_attendees import find_contact_by_telegram_user

    hit = find_contact_by_telegram_user(
        owner_user_id,
        telegram_user_id=int(author.get("telegram_user_id") or 0),
        telegram_username=author.get("username"),
        owner_telegram_username=owner_username,
    )
    names = list(parsed.get("attendee_names") or [])
    if hit:
        label = contact_display_name(hit) or hit.get("name") or ""
        tg = normalize_telegram_username(
            str(hit.get("telegram_username") or hit.get("tg_username") or "")
        )
        if tg:
            at = f"@{tg}"
            if at not in names:
                names.append(at)
        if label and label not in names:
            names.append(label)
    else:
        fn = str(author.get("first_name") or "").strip()
        ln = str(author.get("last_name") or "").strip()
        label = " ".join(p for p in (fn, ln) if p).strip()
        un = str(author.get("username") or "").strip()
        if un:
            label = label or f"@{normalize_telegram_username(un)}"
        if label and label not in names:
            names.append(label)
    if names:
        parsed["attendee_names"] = names
    parsed["_reply_author"] = author
