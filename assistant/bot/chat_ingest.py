"""Пассивный лог групповых сообщений для Дайджеста (без NLU / ответов)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from telegram import ChatMemberUpdated, Update
from telegram.ext import ChatMemberHandler, ContextTypes, TypeHandler

from assistant.bot.group_gate import is_group_chat_type
from assistant.stores import chat_digest as digest_store


def ingest_enabled() -> bool:
    raw = os.getenv("CHAT_DIGEST_INGEST_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _display_name(user) -> str:
    if not user:
        return ""
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    if name:
        return name
    if user.username:
        return f"@{user.username}"
    return str(user.id)


async def ingest_message_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not ingest_enabled():
        return
    msg = update.message or update.edited_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if not is_group_chat_type(chat.type):
        # Личный чат с ботом — тоже в реестре «моих чатов», без полного лога.
        if chat.type == "private" and update.effective_user:
            digest_store.upsert_chat(
                int(chat.id),
                title="Личный чат с Leo",
                chat_type="private",
                touch_message=True,
            )
            digest_store.upsert_member(
                int(chat.id),
                int(update.effective_user.id),
                username=update.effective_user.username,
            )
        return

    title = (chat.title or "").strip() or None
    digest_store.upsert_chat(
        int(chat.id),
        title=title,
        chat_type=str(chat.type or ""),
        touch_message=True,
    )
    user = msg.from_user
    if user and not user.is_bot:
        digest_store.upsert_member(
            int(chat.id),
            int(user.id),
            username=user.username,
        )
    thread_id = message_thread(msg)
    remember_topic_name(int(chat.id), msg, thread_id)
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return
    when = msg.date
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    reply = getattr(msg, "reply_to_message", None)
    if reply is not None and getattr(reply, "forum_topic_created", None):
        # В форуме каждое сообщение топика «отвечает» на его создание — это не реплай.
        reply = None
    reply_user = getattr(reply, "from_user", None) if reply else None
    digest_store.insert_message(
        chat_id=int(chat.id),
        message_id=int(msg.message_id),
        user_id=int(user.id) if user else None,
        username=user.username if user else None,
        display_name=_display_name(user) if user else None,
        text=text,
        ts_utc=when if isinstance(when, datetime) else None,
        reply_to_message_id=int(reply.message_id) if reply else None,
        reply_to_user_id=int(reply_user.id) if reply_user else None,
        mentions=message_mentions(msg),
        thread_id=thread_id,
        is_auto=is_automated(msg),
    )


def is_automated(msg: Any) -> bool:
    """Не живой человек: бот (в т.ч. Channel_Bot / GroupAnonymousBot), пост от имени канала или чата,
    сообщение через инлайн-бота, автопересылка из канала или пересылка."""
    user = getattr(msg, "from_user", None)
    if user is not None and getattr(user, "is_bot", False):
        return True
    for attr in ("sender_chat", "via_bot", "forward_origin", "forward_date"):
        if getattr(msg, attr, None):
            return True
    return bool(getattr(msg, "is_automatic_forward", False))


def message_thread(msg: Any) -> int | None:
    """Топик форума; None — обычная группа или «General»."""
    if not getattr(msg, "is_topic_message", False):
        return None
    tid = getattr(msg, "message_thread_id", None)
    return int(tid) if tid else None


def remember_topic_name(chat_id: int, msg: Any, thread_id: int | None) -> None:
    """Bot API не отдаёт список топиков — имя ловим из сервисных сообщений."""
    if not thread_id:
        return
    for src in (msg, getattr(msg, "reply_to_message", None)):
        if src is None:
            continue
        created = getattr(src, "forum_topic_created", None)
        edited = getattr(src, "forum_topic_edited", None)
        name = getattr(created, "name", None) or getattr(edited, "name", None)
        if name:
            digest_store.upsert_topic(chat_id, thread_id, str(name))
            return


def message_mentions(msg: Any) -> list[str]:
    """@ники и id упомянутых пользователей (mention / text_mention) из текста или подписи."""
    out: list[str] = []
    text = msg.text or msg.caption or ""
    entities = list(getattr(msg, "entities", None) or []) + list(getattr(msg, "caption_entities", None) or [])
    for e in entities:
        kind = str(getattr(e, "type", "") or "")
        if kind.endswith("mention") and not kind.endswith("text_mention"):
            try:
                out.append(text[e.offset : e.offset + e.length].lower())
            except Exception:
                continue
        elif kind.endswith("text_mention") and getattr(e, "user", None):
            out.append(f"id:{int(e.user.id)}")
    return out


def _member_became_in_chat(update: ChatMemberUpdated) -> bool:
    try:
        old = update.old_chat_member.status
        new = update.new_chat_member.status
    except Exception:
        return False
    leftish = {"left", "kicked"}
    inish = {"member", "administrator", "restricted", "owner"}
    return old in leftish and new in inish


async def on_my_chat_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not ingest_enabled():
        return
    ev = update.my_chat_member
    chat = update.effective_chat
    if not ev or not chat:
        return
    if not is_group_chat_type(chat.type) and chat.type != "private":
        return
    if not _member_became_in_chat(ev):
        # всё равно обновим title, если бот уже в чате
        pass
    title = (chat.title or "").strip()
    if chat.type == "private":
        title = title or "Личный чат с Leo"
    digest_store.upsert_chat(
        int(chat.id),
        title=title or None,
        chat_type=str(chat.type or ""),
    )
    actor = ev.from_user
    if actor and not actor.is_bot:
        digest_store.upsert_member(
            int(chat.id),
            int(actor.id),
            username=actor.username,
        )


def register_chat_ingest(app) -> None:
    # Раньше всех (group=-4), до отсечки «старых» апдейтов (-3): после простоя бота
    # сообщения групп всё равно попадают в лог; insert идемпотентен по (chat_id, message_id).
    app.add_handler(TypeHandler(Update, ingest_message_update), group=-4)
    app.add_handler(
        ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER),
        group=-4,
    )
