"""Пассивный лог групповых сообщений для Дайджеста (без NLU / ответов)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

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
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return
    when = msg.date
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    digest_store.insert_message(
        chat_id=int(chat.id),
        message_id=int(msg.message_id),
        user_id=int(user.id) if user else None,
        username=user.username if user else None,
        display_name=_display_name(user) if user else None,
        text=text,
        ts_utc=when if isinstance(when, datetime) else None,
    )


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
    # Раньше handlers (group=-2): только лог, без влияния на gate/NLU.
    app.add_handler(TypeHandler(Update, ingest_message_update), group=-2)
    app.add_handler(
        ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER),
        group=-2,
    )
