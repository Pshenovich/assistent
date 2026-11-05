"""Бот узнаёт человека по почте: Telegram — интерфейс к аккаунту Орбиты, а не сам аккаунт.

При обязательной почте (EMAIL_REQUIRED) в бота пускаем, если:
- у аккаунта есть подтверждённая почта, или
- человек — участник организации (в т. ч. только что узнан по @нику, который указал админ).

Иначе бот просит рабочую почту, шлёт на неё код и ждёт его в чате; после кода Telegram
привязывается к аккаунту с этой почтой (или аккаунт заводится), приглашения в
организации по этой почте превращаются в членство.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Optional

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

STATE_KEY = "email_link"

ASK_EMAIL = (
    "Чтобы начать, напишите рабочую почту — ту, что указана в вашей организации в Орбите. "
    "Пришлю на неё код подтверждения."
)


def email_required() -> bool:
    return (os.getenv("EMAIL_REQUIRED", "") or "").strip().lower() in ("1", "true", "yes")


def _sync_org_contacts(org_ids: list[int]) -> None:
    from assistant.stores import org_contacts

    for oid in org_ids:
        try:
            org_contacts.sync(int(oid))
        except Exception as e:
            print(f"[email_link] contacts sync org={oid} err={e!r}")


def recognize(uid: int, username: Optional[str]) -> tuple[bool, list[str]]:
    """(пускать ли без почты, названия организаций, куда только что вступил по нику)."""
    from assistant.lib import review_access
    from assistant.stores import accounts, orgs_store

    joined = orgs_store.claim_by_tg(uid, username)
    if joined:
        _sync_org_contacts(joined)
    names = [str((orgs_store.get_org(oid) or {}).get("name") or "") for oid in joined]
    if not email_required() or review_access.is_review_user(uid):
        return True, names
    if accounts.email_of(uid) or orgs_store.orgs_for_user(uid):
        return True, names
    return False, names


def link_after_code(tg_id: int, email: str) -> tuple[int, list[str]]:
    """Код подтверждён: привязать Telegram к аккаунту с этой почтой. (id аккаунта, новые организации)."""
    from assistant.lib import tg_account_ids
    from assistant.stores import accounts, orgs_store

    acc = accounts.get_by_email(email)
    if acc:
        if acc["tg_user_id"] and int(acc["tg_user_id"]) != int(tg_id):
            raise accounts.AccountError("Эта почта уже привязана к другому Telegram. Войдите с него или напишите в поддержку.")
        if not acc["tg_user_id"]:
            other = accounts.get_by_tg(tg_id)
            if other and int(other["user_id"]) != int(acc["user_id"]):
                raise accounts.AccountError("Этот Telegram уже привязан к другой почте.")
            accounts.link_tg(int(acc["user_id"]), int(tg_id))
        uid = int(acc["user_id"])
    else:
        # Аккаунта с этой почтой нет: аккаунт — этот Telegram (старый или новый), почта подтверждена.
        accounts.attach_email(int(tg_id), email)
        uid = int(tg_id)
    tg_account_ids.forget(tg_id, uid)
    joined = orgs_store.claim_pending(uid)
    _sync_org_contacts(joined)
    names = [str(o.get("name") or "") for o in orgs_store.orgs_for_user(uid)]
    return uid, names


async def prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[STATE_KEY] = {"step": "email"}  # type: ignore[index]
    msg = update.effective_message
    if msg:
        await msg.reply_text(ASK_EMAIL)


def _send_code(email: str, tg_id: int) -> dict:
    from assistant.services import transactional_mail
    from assistant.stores import accounts

    issued = accounts.issue_code(email, ip=f"tg:{tg_id}")
    transactional_mail.send_login_code(issued["email"], issued["code"], ttl_min=issued["ttl_sec"] // 60)
    return issued


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответы на «почта?» и «код?». Стоит раньше всех обработчиков; не наш шаг — пропускает."""
    st = (context.user_data or {}).get(STATE_KEY) if context.user_data is not None else None
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user
    if not st or not msg or not user or not chat or chat.type != "private" or not msg.text:
        return
    text = msg.text.strip()
    if text.startswith("/"):
        if text.split()[0].split("@")[0] == "/cancel":
            context.user_data.pop(STATE_KEY, None)  # type: ignore[union-attr]
            await msg.reply_text("Хорошо. Чтобы продолжить, нажмите /start.")
            raise ApplicationHandlerStop
        return  # /start и прочие команды — как обычно (/start спросит почту снова)

    from assistant.services.transactional_mail import TxMailError
    from assistant.stores.accounts import AccountError, normalize_email

    tg_id = int(user.id)
    if st.get("step") == "code":
        digits = re.sub(r"\D", "", text)
        if "@" in text:
            st = {"step": "email"}  # прислал другую почту — начинаем с неё
        elif len(digits) == 6:
            from assistant.stores import accounts

            try:
                email = await asyncio.to_thread(accounts.verify_code, st["email"], digits)
                uid, orgs = await asyncio.to_thread(link_after_code, tg_id, email)
            except AccountError as e:
                await msg.reply_text(str(e))
                raise ApplicationHandlerStop
            context.user_data.pop(STATE_KEY, None)  # type: ignore[union-attr]
            where = f" Вы в организации «{orgs[0]}»." if len(orgs) == 1 else (
                " Ваши организации: " + ", ".join(f"«{n}»" for n in orgs) + "." if orgs else "")
            await msg.reply_text(f"Готово, почта {email} подтверждена.{where}")
            await _continue_start(update, context, uid)
            raise ApplicationHandlerStop
        else:
            await msg.reply_text(f"Пришлите 6 цифр из письма на {st['email']}. Другая почта — просто напишите её.")
            raise ApplicationHandlerStop

    try:
        email = normalize_email(text)
    except AccountError:
        await msg.reply_text("Это не похоже на почту. Напишите в виде name@company.ru")
        raise ApplicationHandlerStop
    try:
        await asyncio.to_thread(_send_code, email, tg_id)
    except (AccountError, TxMailError) as e:
        await msg.reply_text(str(e))
        raise ApplicationHandlerStop
    context.user_data[STATE_KEY] = {"step": "code", "email": email}  # type: ignore[index]
    await msg.reply_text(f"Отправил код на {email}. Пришлите его сюда — 6 цифр. Письма нет — проверьте «Спам».")
    raise ApplicationHandlerStop


async def _continue_start(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int) -> None:
    """После подтверждения — как после /start: шлюз доступа и знакомство.

    В этом апдейте ещё Telegram id; если Telegram привязали к аккаунту по почте (другой id),
    знакомство — сразу на аккаунт, следующие апдейты придут уже с ним (tg_account_ids).
    """
    from assistant.bot import handlers
    from assistant.bot.onboarding import send_start_onboarding

    user = update.effective_user
    try:
        if user and int(user.id) == int(uid):
            await handlers.cmd_start(update, context)
            return
        await handlers._set_chat_webapp_menu_button(update, context)
        await send_start_onboarding(update, user_id=int(uid), webapp_url=handlers._webapp_url())
    except ApplicationHandlerStop:
        raise
    except Exception as e:
        print(f"[email_link] continue start uid={uid} err={e!r}")


async def link_by_start(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str) -> None:
    """/start link_<token> из профиля Орбиты: привязать этот Telegram к аккаунту по почте (или сменить)."""
    from assistant.lib import tg_account_ids
    from assistant.stores import accounts

    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    uid_now = int(user.id)
    if accounts.is_synthetic(uid_now):
        # Апдейт уже пришёл с id аккаунта — этот Telegram к нему привязан.
        await msg.reply_text("Этот Telegram уже привязан к аккаунту Орбиты. Чтобы перенести его на другую почту, напишите в поддержку.")
        return
    try:
        acc, old = await asyncio.to_thread(accounts.link_tg_by_token, token, uid_now)
    except accounts.AccountError as e:
        await msg.reply_text(str(e))
        return
    tg_account_ids.forget(uid_now, int(acc["user_id"]), *([old] if old else []))
    context.user_data.pop(STATE_KEY, None)  # type: ignore[union-attr]
    await msg.reply_text(f"Готово: этот Telegram привязан к аккаунту {acc['email']}.")
    if old:
        try:
            await context.bot.send_message(chat_id=int(old), text=f"Аккаунт Орбиты {acc['email']} перенесён на другой Telegram.")
        except Exception:
            pass
    await _continue_start(update, context, int(acc["user_id"]))
