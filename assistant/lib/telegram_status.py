"""Статусные сообщения бота (в т.ч. в группах и топиках)."""

from __future__ import annotations

from telegram import Message
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

WORK_STATUS_KEY = "_work_status_message"


def delivery_kwargs(msg: Message | None) -> dict:
    if not msg:
        return {}
    thread_id = getattr(msg, "message_thread_id", None)
    if thread_id:
        return {"message_thread_id": int(thread_id)}
    return {}


async def send_working_action(
    context: ContextTypes.DEFAULT_TYPE,
    msg: Message | None,
) -> None:
    if not msg or not msg.chat:
        return
    try:
        await context.bot.send_chat_action(
            chat_id=msg.chat_id,
            action=ChatAction.TYPING,
            **delivery_kwargs(msg),
        )
    except Exception:
        pass


async def post_status(
    msg: Message | None,
    text: str,
    *,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> Message | None:
    body = (text or "").strip()
    if not msg or not body:
        return None
    kwargs = delivery_kwargs(msg)
    if context is not None:
        await send_working_action(context, msg)
    try:
        return await msg.reply_text(body, **kwargs)
    except Exception:
        try:
            bot = msg.get_bot()
            return await bot.send_message(chat_id=msg.chat_id, text=body, **kwargs)
        except Exception:
            return None


async def set_status(
    status_msg: Message | None,
    text: str,
    *,
    anchor: Message | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> Message | None:
    body = (text or "").strip()
    if not body:
        return status_msg
    if status_msg:
        try:
            await status_msg.edit_text(body)
            return status_msg
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return status_msg
        except Exception:
            pass
    if anchor:
        return await post_status(anchor, body, context=context)
    return status_msg


def reply_has_media(msg: Message | None) -> bool:
    if not msg:
        return False
    return bool(
        msg.voice
        or msg.audio
        or msg.video
        or msg.document
        or msg.video_note
    )


def take_work_status(context: ContextTypes.DEFAULT_TYPE) -> Message | None:
    raw = context.user_data.pop(WORK_STATUS_KEY, None)
    return raw if isinstance(raw, Message) else None


async def maybe_post_early_work_status(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    """В группе сразу подтверждаем приём задачи (до маршрутизации и скачивания)."""
    msg = update.message
    chat = update.effective_chat
    if not msg or not chat or chat.type not in ("group", "supergroup"):
        return
    rep = msg.reply_to_message
    if not rep or not reply_has_media(rep):
        return
    from assistant.nlu.regex import parse_summary_intent, parse_transcribe_intent

    is_summary = parse_summary_intent(text)
    is_transcribe = parse_transcribe_intent(text)
    if not is_summary and not is_transcribe:
        return
    label = "саммари" if is_summary else "транскрипцию"
    status = await post_status(
        msg,
        f"Принял запрос, готовлю {label}…",
        context=context,
    )
    if status is not None:
        context.user_data[WORK_STATUS_KEY] = status
