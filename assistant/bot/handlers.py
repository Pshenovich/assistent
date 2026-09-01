"""Telegram handlers."""

from __future__ import annotations

import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user

from assistant.bot.access_gate import ensure_access, handle_access_callback
from assistant.bot.onboarding import handle_onboarding_callback, send_start_onboarding
from assistant.bot.operation_cancel import try_cancel_operation
from assistant.bot.group_gate import should_process_message
from assistant.bot.group_reaction import react_group_message_seen
from assistant.stores import telegram_registry
from assistant.integrations import google_calendar_oauth, telemost_oauth, todoist_oauth, yandex_disk_oauth, zoom_oauth
from assistant.lib.urls import extract_urls
from assistant.nlu.dispatch import RouteExtras, route_text
from assistant.skills import calendar as calendar_skill
from assistant.skills import reminders as reminders_skill
from assistant.skills import telemost as telemost_skill
from assistant.skills import zoom as zoom_skill
from assistant.lib.webapp_public import webapp_entry_url
from assistant.skills import journal_pdf as journal_pdf_skill
from assistant.skills import transcribe as transcribe_skill


def _webapp_url() -> str:
    return webapp_entry_url()


async def _set_chat_webapp_menu_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat = update.effective_chat
    if not chat:
        return
    try:
        await context.bot.set_chat_menu_button(
            chat_id=chat.id,
            menu_button=MenuButtonWebApp(
                text="Ассистент", web_app=WebAppInfo(url=_webapp_url())
            ),
        )
    except Exception:
        pass


async def _bind_usage_telegram_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Привязка LLM-расхода к Telegram user id для дашборда и mini-app."""
    user = update.effective_user
    if user:
        set_openrouter_usage_telegram_user(
            telegram_user_id=user.id,
            telegram_username=user.username,
        )
    else:
        set_openrouter_usage_telegram_user(telegram_user_id=None)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    user = update.effective_user
    if user:
        telegram_registry.register_user(
            telegram_user_id=int(user.id),
            telegram_username=user.username,
        )
    msg = update.message
    await _set_chat_webapp_menu_button(update, context)
    if msg and user:
        await send_start_onboarding(
            update,
            user_id=int(user.id),
            webapp_url=_webapp_url(),
        )


async def cmd_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    user = update.effective_user
    if user:
        telegram_registry.register_user(
            telegram_user_id=int(user.id),
            telegram_username=user.username,
        )
    await _set_chat_webapp_menu_button(update, context)
    if update.message and user:
        await send_start_onboarding(
            update,
            user_id=int(user.id),
            webapp_url=_webapp_url(),
            force=True,
        )


async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    url = _webapp_url()
    await _set_chat_webapp_menu_button(update, context)
    if update.message:
        await update.message.reply_text(f"Мини-приложение: {url}")


async def _reply_oauth_link(
    update: Update,
    *,
    title: str,
    register_state,
    build_url,
    button_label: str = "Подключить",
) -> None:
    user = update.effective_user
    msg = update.message
    if not user or not msg:
        return
    try:
        state = register_state(int(user.id))
        url = build_url(state)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(button_label, url=url)]]
        )
        await msg.reply_text(
            f"{title}\nНажмите кнопку ниже (не используйте старые ссылки из чата).",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as e:
        await msg.reply_text(f"{title}\nОшибка: {e}", disable_web_page_preview=True)


async def cmd_calendar_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    await _reply_oauth_link(
        update,
        title="Подключите Google Calendar:",
        register_state=google_calendar_oauth.register_oauth_state,
        build_url=google_calendar_oauth.build_authorization_url,
    )


async def cmd_zoom_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    await _reply_oauth_link(
        update,
        title="Подключите Zoom:",
        register_state=zoom_oauth.register_oauth_state,
        build_url=zoom_oauth.build_authorization_url,
    )


async def cmd_telemost_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    user = update.effective_user
    msg = update.message
    if not user or not msg:
        return
    try:
        state = telemost_oauth.register_oauth_state(int(user.id))
        url = telemost_oauth.build_authorization_url(state)
    except Exception as e:
        await msg.reply_text(f"Телемост: {e}")
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Подключить", url=url)]]
    )
    lines = [
        "Подключите Yandex Telemost (создание ссылок на видеовстречи):",
        "Нужен аккаунт Яндекс 360 для бизнеса на домене организации.",
        "Нажмите кнопку ниже (не используйте старые ссылки из чата).",
    ]
    if telemost_oauth.uses_verification_code_flow():
        lines.append(
            "Код подтверждения введите в мини-приложении: Профиль → Телемост."
        )
    await msg.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    await try_cancel_operation(update, context)


async def cmd_todoist_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    await _reply_oauth_link(
        update,
        title="Подключите Todoist:",
        register_state=todoist_oauth.register_oauth_state,
        build_url=todoist_oauth.build_authorization_url,
    )


async def cmd_yandex_disk_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    user = update.effective_user
    msg = update.message
    if not user or not msg:
        return
    try:
        state = yandex_disk_oauth.register_oauth_state(int(user.id))
        url = yandex_disk_oauth.build_authorization_url(state)
    except Exception as e:
        await msg.reply_text(f"Яндекс Диск: {e}")
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Подключить", url=url)]]
    )
    lines = [
        "Подключите Яндекс Диск (записи Zoom-встреч будут сохраняться автоматически):",
        "Нажмите кнопку ниже (не используйте старые ссылки из чата).",
    ]
    if yandex_disk_oauth.uses_verification_code_flow():
        lines.append(
            "Код подтверждения введите в мини-приложении: Профиль → Яндекс Диск."
        )
    await msg.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def cmd_bitrix_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    msg = update.message
    if not msg:
        return
    url = _webapp_url()
    await msg.reply_text(
        "Подключите Битрикс24 в мини-приложении:\n"
        "Профиль → Интеграции → Битрикс24.\n\n"
        "Получите токен: Настройки → MCP → Приложения → MCP-подключения.\n\n"
        f"Мини-приложение: {url}",
        disable_web_page_preview=True,
    )


async def cmd_yandex_disk_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    msg = update.message
    if not msg:
        return
    await msg.reply_text(
        "Код Яндекс Диска вводится в мини-приложении:\n"
        "Меню «Ассистент» → Профиль → Яндекс Диск → вставьте код со страницы Яндекса."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not should_process_message(update, context):
        return
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    is_group = chat and chat.type in ("group", "supergroup")
    if is_group:
        await react_group_message_seen(update, context)
    user = update.effective_user
    if user:
        telegram_registry.register_user(
            telegram_user_id=int(user.id),
            telegram_username=user.username,
        )
    text = (msg.text or msg.caption or "").strip()
    if is_group and text:
        from assistant.lib.message_context import strip_bot_mention

        text = strip_bot_mention(
            text, context.bot.username or os.getenv("TELEGRAM_BOT_USERNAME") or ""
        )
        if not text:
            return
    if text and await try_cancel_operation(update, context, text=text):
        return
    if await zoom_skill.try_continue_pending(update, context):
        return
    if await telemost_skill.try_continue_pending(update, context):
        return
    if await reminders_skill.try_continue_pending(update, context):
        return
    if await calendar_skill.try_continue_pending(update, context):
        return
    from assistant.skills import bitrix as bitrix_skill

    if await bitrix_skill.try_continue_bitrix(update, context):
        return
    if transcribe_skill.consume_transcribe_handled(context):
        return
    from assistant.skills import summary as summary_skill

    if summary_skill.consume_summary_handled(context):
        return
    if not text and not (msg.voice or msg.document):
        return
    extras = RouteExtras(
        source="group" if is_group else "private",
        replied=msg.reply_to_message,
        urls=extract_urls(text),
    )
    if msg.voice and not text:
        await transcribe_skill.handle_voice(update, context)
        return
    if text:
        from assistant.lib.telegram_status import maybe_post_early_work_status

        await maybe_post_early_work_status(update, context, text)
        handled = await route_text(
            update,
            context,
            text,
            extras,
            chat_type="group" if is_group else "private",
        )
        if not handled and not is_group:
            await msg.reply_text(
                "Не понял запрос. Примеры: «встреча завтра в 15:00», "
                "«напомни в 18:00 позвонить», «заметка: …»"
            )


def register_handlers(app: Application) -> None:
    app.add_handler(TypeHandler(Update, _bind_usage_telegram_user), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("onboarding", cmd_onboarding))
    app.add_handler(CommandHandler("app", cmd_app))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("calendar_auth", cmd_calendar_auth))
    app.add_handler(CommandHandler("zoom_auth", cmd_zoom_auth))
    app.add_handler(CommandHandler("telemost_auth", cmd_telemost_auth))
    app.add_handler(CommandHandler("todoist_auth", cmd_todoist_auth))
    app.add_handler(CommandHandler("yandex_disk_auth", cmd_yandex_disk_auth))
    app.add_handler(CommandHandler("bitrix_auth", cmd_bitrix_auth))
    app.add_handler(CommandHandler("yandex_disk_code", cmd_yandex_disk_code))
    app.add_handler(
        CallbackQueryHandler(
            handle_access_callback,
            pattern=r"^acc:",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_onboarding_callback,
            pattern=r"^ob:",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            calendar_skill.handle_callback,
            pattern=r"^(cal:|ced:|inv:)",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            reminders_skill.handle_callback,
            pattern=r"^rem:",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            journal_pdf_skill.handle_callback,
            pattern=r"^jmpdf:",
        )
    )
    app.add_handler(MessageHandler(filters.VOICE, transcribe_skill.handle_voice))
    app.add_handler(
        MessageHandler(
            filters.Document.ALL | filters.AUDIO | filters.VIDEO,
            transcribe_skill.handle_document,
        )
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_handler(
        MessageHandler(filters.CAPTION & ~filters.COMMAND, handle_message)
    )
