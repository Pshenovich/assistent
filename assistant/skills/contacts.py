"""Контакты пользователя."""

from __future__ import annotations

import re

from telegram import Update
from telegram.ext import ContextTypes

from assistant.stores import contacts_store

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


async def handle_add_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    emails = EMAIL_RE.findall(text)
    if not emails:
        await msg.reply_text("Укажите email контакта, например: Иван ivan@mail.ru")
        return
    email = emails[0].lower()
    name_part = EMAIL_RE.sub("", text).strip(" ,—-")
    name = name_part or email.split("@")[0]
    items = contacts_store.load_contacts(telegram_user_id=int(user.id))
    status = contacts_store.add_or_update_contact(items, name, email)
    contacts_store.save_contacts(
        items, telegram_user_id=int(user.id), telegram_username=user.username
    )
    await msg.reply_text(f"Контакт {name} ({email}) — {status}.")
