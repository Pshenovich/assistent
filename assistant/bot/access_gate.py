"""Проверка доступа новых пользователей (одобрение администратором)."""

from __future__ import annotations

import os

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from assistant.lib import telegram_access_allowlist as access


def is_user_allowed(user_id: int, username: str | None = None) -> bool:
    if not access.gate_enabled():
        return True
    if user_id in access.approver_user_ids():
        return True
    if access.is_extra_allowed(username, user_id):
        return True
    return False


def miniapp_access_message(
    *,
    user_id: int,
    username: str | None,
    first_name: str = "",
    last_name: str = "",
) -> tuple[bool, str]:
    """Проверка доступа для мини-приложения; при новой заявке уведомляет администраторов."""
    if is_user_allowed(user_id, username):
        return True, ""
    if access.is_denied(user_id):
        return (
            False,
            "Доступ к боту не предоставлен. Обратитесь к администратору.",
        )
    if access.is_pending(user_id):
        return (
            False,
            "Заявка на доступ уже отправлена. Ожидайте одобрения администратора.",
        )
    token = access.register_pending_request(
        user_id=int(user_id),
        username=username,
        first_name=first_name,
        last_name=last_name,
    )
    _notify_approvers_sync(
        token=token,
        user_id=int(user_id),
        username=username,
        first_name=first_name,
        last_name=last_name,
    )
    return (
        False,
        "Заявка на доступ отправлена администратору. "
        "Когда вас одобрят, откройте мини-приложение снова.",
    )


def _telegram_api_base() -> str:
    api_base = os.getenv("TELEGRAM_BOT_API_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return ""
    if api_base:
        return f"{api_base}/bot{token}"
    return f"https://api.telegram.org/bot{token}"


def _notify_approvers_sync(
    *,
    token: str,
    user_id: int,
    username: str | None,
    first_name: str,
    last_name: str,
) -> None:
    api = _telegram_api_base()
    if not api:
        return
    text = (
        "Новая заявка на доступ к Leo:\n\n"
        + access.format_user_brief(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
    )
    kb = {
        "inline_keyboard": [
            [
                {"text": "Разрешить", "callback_data": f"acc:ok:{token}"},
                {"text": "Отклонить", "callback_data": f"acc:no:{token}"},
            ]
        ]
    }
    proxy = os.getenv("TELEGRAM_PROXY_URL", "").strip() or None
    for approver_id in access.approver_user_ids():
        try:
            requests.post(
                f"{api}/sendMessage",
                json={
                    "chat_id": int(approver_id),
                    "text": text,
                    "reply_markup": kb,
                },
                timeout=15,
                proxies={"http": proxy, "https": proxy} if proxy else None,
            )
        except Exception as e:
            print(f"[access_gate] notify approver={approver_id} err={e!r}")


async def ensure_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True — пользователь может пользоваться ботом."""
    user = update.effective_user
    if not user or user.is_bot:
        return False
    uid = int(user.id)
    if is_user_allowed(uid, user.username):
        return True

    msg = update.message or update.callback_query.message if update.callback_query else None

    if access.is_denied(uid):
        if msg:
            await msg.reply_text(
                "Доступ к боту не предоставлен. Обратитесь к администратору."
            )
        return False

    if access.is_pending(uid):
        if msg:
            await msg.reply_text(
                "Заявка на доступ уже отправлена. Ожидайте одобрения администратора."
            )
        return False

    token = access.register_pending_request(
        user_id=uid,
        username=user.username,
        first_name=str(user.first_name or ""),
        last_name=str(user.last_name or ""),
    )
    if msg:
        await msg.reply_text(
            "Заявка на доступ отправлена администратору. "
            "Когда вас одобрят, напишите /start снова."
        )
    _notify_approvers_sync(
        token=token,
        user_id=int(user.id),
        username=user.username,
        first_name=str(user.first_name or ""),
        last_name=str(user.last_name or ""),
    )
    return False


async def handle_access_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    approver = update.effective_user
    if not q or not approver:
        return
    if int(approver.id) not in access.approver_user_ids():
        await q.answer("Недостаточно прав.", show_alert=True)
        return
    data = (q.data or "").strip()
    await q.answer()
    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    action, token = parts[1], parts[2]
    uid = access.consume_action_token(token)
    if not uid:
        await q.edit_message_text("Заявка устарела или уже обработана.")
        return
    if action == "ok":
        pending = access.get_pending_user(int(uid)) or {}
        access.add_approved_user(
            username=pending.get("username") or None,
            user_id=int(uid),
        )
        await q.edit_message_text(f"Доступ разрешён (ID {uid}).")
        try:
            await context.bot.send_message(
                int(uid),
                "Вам открыт доступ к Leo. Напишите /start, чтобы начать.",
            )
        except Exception:
            pass
        return
    if action == "no":
        access.deny_user(int(uid))
        await q.edit_message_text(f"Доступ отклонён (ID {uid}).")
        try:
            await context.bot.send_message(
                int(uid),
                "Администратор отклонил заявку на доступ к боту.",
            )
        except Exception:
            pass
