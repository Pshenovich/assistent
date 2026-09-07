"""Проверка доступа новых пользователей (одобрение администратором)."""

from __future__ import annotations

import os

import requests
from telegram import Update
from telegram.ext import ContextTypes

from assistant.lib import telegram_access_allowlist as access


def access_scope_from_context(context: ContextTypes.DEFAULT_TYPE | None) -> str:
    token = ""
    if context is not None:
        bot = getattr(context, "bot", None)
        token = str(getattr(bot, "token", "") or "").strip()
    donatello = (
        os.getenv("TG_DONATELLO_BOT_TOKEN", "").strip()
        or os.getenv("BOARD_BOT_TOKEN", "").strip()
    )
    if donatello and token and token == donatello:
        return "donatello"
    return "leo"


def _product_name(scope: str | None) -> str:
    return "Donatello" if access.normalize_scope(scope) == "donatello" else "Leo"


def is_user_allowed(
    user_id: int, username: str | None = None, *, scope: str | None = None
) -> bool:
    if not access.gate_enabled():
        return True
    if user_id in access.approver_user_ids():
        return True
    if access.is_extra_allowed(username, user_id, scope=scope):
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
    if is_user_allowed(user_id, username, scope="leo"):
        return True, ""
    if access.is_denied(user_id, scope="leo"):
        return (
            False,
            "Доступ к боту не предоставлен. Обратитесь к администратору.",
        )
    if access.is_pending(user_id, scope="leo"):
        return (
            False,
            "Заявка на доступ уже отправлена. Ожидайте одобрения администратора.",
        )
    token = access.register_pending_request(
        user_id=int(user_id),
        username=username,
        first_name=first_name,
        last_name=last_name,
        scope="leo",
    )
    _notify_approvers_sync(
        token=token,
        user_id=int(user_id),
        username=username,
        first_name=first_name,
        last_name=last_name,
        scope="leo",
    )
    return (
        False,
        "Заявка на доступ отправлена администратору. "
        "Когда вас одобрят, откройте мини-приложение снова.",
    )


def _telegram_api_base(*, bot_token: str | None = None) -> str:
    api_base = os.getenv("TELEGRAM_BOT_API_BASE_URL", "").strip().rstrip("/")
    token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token:
        return ""
    if api_base:
        return f"{api_base}/bot{token}"
    return f"https://api.telegram.org/bot{token}"


def _bot_token_for_scope(scope: str | None, *, context_token: str | None = None) -> str:
    ctx = (context_token or "").strip()
    if ctx:
        return ctx
    if access.normalize_scope(scope) == "donatello":
        return (
            os.getenv("TG_DONATELLO_BOT_TOKEN", "").strip()
            or os.getenv("BOARD_BOT_TOKEN", "").strip()
        )
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _notify_approvers_sync(
    *,
    token: str,
    user_id: int,
    username: str | None,
    first_name: str,
    last_name: str,
    scope: str | None = None,
    bot_token: str | None = None,
) -> None:
    api = _telegram_api_base(
        bot_token=_bot_token_for_scope(scope, context_token=bot_token)
    )
    if not api:
        return
    product = _product_name(scope)
    text = (
        f"Новая заявка на доступ к {product}:\n\n"
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
    scope = access_scope_from_context(context)
    if is_user_allowed(uid, user.username, scope=scope):
        return True

    msg = update.effective_message

    if access.is_denied(uid, scope=scope):
        if msg:
            await msg.reply_text(
                "Доступ к боту не предоставлен. Обратитесь к администратору."
            )
        return False

    if access.is_pending(uid, scope=scope):
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
        scope=scope,
    )
    if msg:
        await msg.reply_text(
            "Заявка на доступ отправлена администратору. "
            "Когда вас одобрят, напишите /start снова."
        )
    bot_token = str(getattr(getattr(context, "bot", None), "token", "") or "")
    _notify_approvers_sync(
        token=token,
        user_id=int(user.id),
        username=user.username,
        first_name=str(user.first_name or ""),
        last_name=str(user.last_name or ""),
        scope=scope,
        bot_token=bot_token,
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
    scope = access_scope_from_context(context)
    product = _product_name(scope)
    uid = access.consume_action_token(token, scope=scope)
    if not uid:
        await q.edit_message_text("Заявка устарела или уже обработана.")
        return
    if action == "ok":
        pending = access.get_pending_user(int(uid), scope=scope) or {}
        access.add_approved_user(
            username=pending.get("username") or None,
            user_id=int(uid),
            scope=scope,
        )
        await q.edit_message_text(f"Доступ разрешён (ID {uid}).")
        try:
            await context.bot.send_message(
                int(uid),
                f"Вам открыт доступ к {product}. Напишите /start, чтобы начать.",
            )
        except Exception:
            pass
        return
    if action == "no":
        access.deny_user(int(uid), scope=scope)
        await q.edit_message_text(f"Доступ отклонён (ID {uid}).")
        try:
            await context.bot.send_message(
                int(uid),
                "Администратор отклонил заявку на доступ к боту.",
            )
        except Exception:
            pass
