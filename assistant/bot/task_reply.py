"""Ответ из Telegram в обсуждение задачи (как из карточки в Орбите): кнопка «Ответить» под
уведомлением о сообщении по задаче (следующий текст или голосовое) или свайп-ответ на само уведомление.

То же для обсуждений документов и чатов Орбиты (``dsc:reply:<id сообщения>``): ответ уходит в тот же
чат обсуждения ответом на это сообщение."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

CANCEL_CALLBACK = "tsk:rcancel"
_PENDING_KEY = "leo_task_reply"
_PENDING_TTL_SEC = 30 * 60
_CALLBACK_RE = re.compile(r"^tsk:reply:(\d+)$")
_DSC_CALLBACK_RE = re.compile(r"^dsc:reply:(\d+)$")
_ACCEPT_CALLBACK_RE = re.compile(r"^tsk:accept:(\d+)$")


def _get_pending(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict[str, Any] | None:
    st = context.user_data.get(_PENDING_KEY) if context.user_data is not None else None
    if not isinstance(st, dict) or int(st.get("user_id") or 0) != int(user_id):
        return None
    if time.time() - float(st.get("at") or 0) > _PENDING_TTL_SEC:
        context.user_data.pop(_PENDING_KEY, None)
        return None
    return st


_TASK_URL_RE = re.compile(r"[?&#]task=(\d+)")


def _task_id_from_replied(replied: Any, bot_id: int | None) -> int | None:
    """Свайп-ответ на уведомление «X по задаче …»: id из кнопки «Ответить», иначе из ссылки на задачу."""
    if replied is None:
        return None
    author = getattr(replied, "from_user", None)
    if not author or not getattr(author, "is_bot", False) or (bot_id and int(author.id) != int(bot_id)):
        return None
    markup = getattr(replied, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or ():
        for btn in row:
            m = _CALLBACK_RE.match(str(getattr(btn, "callback_data", "") or ""))
            if m:
                return int(m.group(1))
    if " по задаче " not in str(getattr(replied, "text", "") or ""):
        return None
    for ent in getattr(replied, "entities", None) or ():
        m = _TASK_URL_RE.search(str(getattr(ent, "url", "") or ""))
        if m:
            return int(m.group(1))
    return None


def _comment_id_from_replied(replied: Any, bot_id: int | None) -> int | None:
    """Свайп-ответ на уведомление из обсуждения: id сообщения из кнопки «Ответить»."""
    if replied is None:
        return None
    author = getattr(replied, "from_user", None)
    if not author or not getattr(author, "is_bot", False) or (bot_id and int(author.id) != int(bot_id)):
        return None
    markup = getattr(replied, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or ():
        for btn in row:
            m = _DSC_CALLBACK_RE.match(str(getattr(btn, "callback_data", "") or ""))
            if m:
                return int(m.group(1))
    return None


def discussion_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[int, bool] | None:
    """(id сообщения обсуждения, из режима «Ответить») — None: это не ответ в обсуждение."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat or chat.type != "private":
        return None
    try:
        bot_id = int(context.bot.id)
    except Exception:
        bot_id = None
    cid = _comment_id_from_replied(getattr(msg, "reply_to_message", None), bot_id)
    if cid:
        return cid, False
    st = _get_pending(context, int(user.id))
    return (int(st["comment_id"]), True) if st and st.get("comment_id") else None


def any_reply_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Это сообщение — ответ по задаче или в обсуждение (голосовое тогда диктовка, не команда)."""
    return reply_target(update, context) is not None or discussion_target(update, context) is not None


def reply_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[int, bool] | None:
    """(task_id, из режима «Ответить») — куда уйдёт это сообщение; None — это не ответ по задаче."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat or chat.type != "private":
        return None
    try:
        bot_id = int(context.bot.id)
    except Exception:  # бот ещё не инициализирован (тесты) — сверяем только is_bot
        bot_id = None
    replied = getattr(msg, "reply_to_message", None)
    tid = _task_id_from_replied(replied, bot_id)
    if tid:
        return tid, False
    if _comment_id_from_replied(replied, bot_id):
        return None  # свайп на уведомление из обсуждения — не к задаче, даже если ждали ответ по задаче
    st = _get_pending(context, int(user.id))
    return (int(st["task_id"]), True) if st and st.get("task_id") else None


def clear_pending_for_user(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if _get_pending(context, user_id):
        context.user_data.pop(_PENDING_KEY, None)
        return True
    return False


def _actor_name(user_id: int, fallback: str) -> str:
    from assistant.lib.task_notify import owner_display_name

    name = owner_display_name(user_id)
    return fallback if name == "Коллега" and fallback else name


def post_reply(user_id: int, task_id: int, text: str, *, fallback_name: str = "") -> dict[str, Any] | None:
    """Сообщение в обсуждение задачи + пуш второй стороне — как POST /calendar/tasks/{id}/messages."""
    from assistant.lib.task_notify import notify_task_message
    from assistant.stores import calendar_tasks as calendar_tasks_store

    name = _actor_name(user_id, fallback_name)
    row = calendar_tasks_store.post_message(user_id, task_id, text, actor_name=name)
    if not row:
        return None
    notify_task_message(task=row, sender_id=user_id, sender_name=name, text=text)
    try:
        from assistant.services import semantic_memory

        for who in {str(row.get("owner_user_id") or ""), str(row.get("assignee_user_id") or "")} - {""}:
            semantic_memory.note_changed(who)
    except Exception as e:
        print(f"[task_reply] memory task={task_id} err={e!r}")
    return row


def _discussion_comment_for(user_id: int, comment_id: int) -> dict[str, Any] | None:
    """Сообщение обсуждения, если человек — участник (тот же круг, кому уходят пуши) и видит этот чат."""
    from assistant.services.discussion_notify import list_discussion_participants
    from assistant.stores import note_chats
    from assistant.stores import share_comments as share_comments_store

    row = share_comments_store.get_comment(int(comment_id))
    if not row:
        return None
    owner = str(row.get("owner_user_id") or "")
    kind = str(row.get("item_kind") or "")
    item_id = str(row.get("item_id") or "")
    ids = {str(m.get("user_id") or "") for m in list_discussion_participants(owner, kind, item_id)}
    if str(user_id) not in ids or not note_chats.comment_visible(row, str(user_id)):
        return None
    return row


def _author_identity(user_id: int, fallback: str) -> tuple[str, str | None]:
    """Имя и ник автора — как у сообщений из приложения (профиль Орбиты)."""
    prof: dict[str, Any] = {}
    try:
        from assistant.stores import note_members

        prof = note_members.get_profile(int(user_id)) or {}
    except Exception:
        prof = {}
    first = str(prof.get("first_name") or "").strip()
    last = str(prof.get("last_name") or "").strip() if first else ""
    name = " ".join(p for p in (first, last) if p).strip() or (fallback or "").strip()
    username = str(prof.get("username") or "").strip() or None
    if not name:
        name = f"@{username}" if username else "Пользователь"
    from assistant.stores import share_comments as share_comments_store

    if share_comments_store.looks_like_ai_author(name, None):
        name = f"«{name}»"
    return name, username


def discussion_title(row: dict[str, Any]) -> str:
    from assistant.services.discussion_notify import discussion_item_title

    try:
        return discussion_item_title(str(row.get("owner_user_id") or ""), str(row.get("item_kind") or ""), str(row.get("item_id") or "")) or "Обсуждение"
    except Exception:
        return "Обсуждение"


def post_discussion_reply(user_id: int, comment_id: int, text: str, *, fallback_name: str = "") -> dict[str, Any] | None:
    """Ответ в обсуждение + пуш остальным — как POST /notes/{kind}/{id}/comments с parent_id."""
    from assistant.services.discussion_notify import notify_discussion_comment_safe
    from assistant.stores import note_chats
    from assistant.stores import share_comments as share_comments_store

    parent = _discussion_comment_for(user_id, comment_id)
    if not parent:
        return None
    owner = str(parent.get("owner_user_id") or "")
    kind = str(parent.get("item_kind") or "")
    item_id = str(parent.get("item_id") or "")
    name, username = _author_identity(user_id, fallback_name)
    row = share_comments_store.add_comment(
        owner,
        kind,
        item_id,
        author_user_id=user_id,
        author_name=name,
        author_username=username,
        body=text,
        parent_id=int(comment_id),
        chat_id=parent.get("chat_id"),
    )
    if kind == "chat":
        try:
            from assistant.stores import chat_threads as chat_threads_store

            chat_threads_store.after_comment(owner, item_id, text, is_user=True)
        except Exception as e:
            print(f"[task_reply] chat_thread comment={comment_id} err={e!r}")
    if note_chats.comment_visible(row, None):
        notify_discussion_comment_safe(
            owner=owner,
            kind=kind,
            item_id=item_id,
            author_user_id=str(user_id),
            author_name=name,
            author_username=username,
            body=text,
            is_assistant=False,
            chat_id=row.get("chat_id"),
            comment_id=row.get("id"),
        )
    return row


async def _handle_discussion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, comment_id: int) -> None:
    q = update.callback_query
    uid = int(update.effective_user.id)
    row = await asyncio.to_thread(_discussion_comment_for, uid, comment_id)
    if not row:
        await q.answer("Обсуждение недоступно — возможно, сообщение удалили", show_alert=True)
        return
    context.user_data[_PENDING_KEY] = {"user_id": uid, "comment_id": int(comment_id), "at": time.time()}
    await q.answer()
    import html

    title = html.escape(await asyncio.to_thread(discussion_title, row))
    who = html.escape(str(row.get("author_name") or "").strip())
    chat_id = q.message.chat_id if q.message else uid
    await context.bot.send_message(
        chat_id,
        f"Напишите или надиктуйте голосовым ответ{(' для ' + who) if who else ''} в обсуждении «{title}» — добавлю его туда.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data=CANCEL_CALLBACK)]]),
    )


def accept_from_telegram(user_id: int, task_id: int, *, fallback_name: str = "") -> tuple[str, dict[str, Any] | None]:
    """«Принять задачу» из Telegram — как POST /calendar/tasks/{id}/accept.
    ("ok"|"already"|"forbidden", задача)."""
    from assistant.services.task_google_sync import sync_task_google
    from assistant.stores import calendar_tasks as calendar_tasks_store

    task = calendar_tasks_store.get_task_for_user(user_id, task_id)
    if not task or str(task.get("owner_user_id") or "") != str(user_id):
        return "forbidden", None
    if str(task.get("outcome") or "") == "accepted":
        return "already", task
    row = calendar_tasks_store.accept_task(user_id, task_id, actor_name=_actor_name(user_id, fallback_name))
    if not row:
        return "forbidden", None
    try:
        row = sync_task_google(int(user_id), row, "update") or row
    except Exception as e:
        print(f"[task_reply] accept google_sync task={task_id} err={e!r}")
    try:
        from assistant.services import semantic_memory

        for who in {str(row.get("owner_user_id") or ""), str(row.get("assignee_user_id") or "")} - {""}:
            semantic_memory.note_changed(who)
    except Exception as e:
        print(f"[task_reply] accept memory task={task_id} err={e!r}")
    return "ok", row


async def _handle_accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    q = update.callback_query
    user = update.effective_user
    try:
        status, _row = await asyncio.to_thread(
            accept_from_telegram, int(user.id), task_id, fallback_name=str(user.full_name or "").strip()
        )
    except Exception as e:
        print(f"[task_reply] accept uid={user.id} task={task_id} err={e!r}")
        await q.answer("Не получилось принять задачу. Попробуйте ещё раз.", show_alert=True)
        return
    if status == "forbidden":
        await q.answer("Принять может только постановщик — или задачу удалили", show_alert=True)
        return
    await q.answer("Задача уже принята" if status == "already" else "Задача принята")
    # Под уведомлением — отметка вместо кнопок, чтобы не нажать второй раз.
    msg = q.message
    if msg is not None:
        try:
            html_text = getattr(msg, "text_html", None) or str(getattr(msg, "text", "") or "")
            await msg.edit_text(html_text + "\n\n✅ Принято", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass


async def handle_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.stores import calendar_tasks as calendar_tasks_store
    from assistant.stores.calendar_tasks import task_is_delegated

    q = update.callback_query
    user = update.effective_user
    if not q or not q.data or not user:
        return
    uid = int(user.id)
    if q.data == CANCEL_CALLBACK:
        clear_pending_for_user(context, uid)
        await q.answer("Отменено")
        try:
            await q.edit_message_text("Ответ отменён.")
        except Exception:
            pass
        return
    am = _ACCEPT_CALLBACK_RE.match(q.data)
    if am:
        await _handle_accept_callback(update, context, int(am.group(1)))
        return
    dm = _DSC_CALLBACK_RE.match(q.data)
    if dm:
        await _handle_discussion_callback(update, context, int(dm.group(1)))
        return
    m = _CALLBACK_RE.match(q.data)
    if not m:
        await q.answer()
        return
    task_id = int(m.group(1))
    task = await asyncio.to_thread(calendar_tasks_store.get_task_for_user, uid, task_id)
    if not task or not task_is_delegated(task):
        await q.answer("Задача недоступна — возможно, её удалили", show_alert=True)
        return
    context.user_data[_PENDING_KEY] = {"user_id": uid, "task_id": task_id, "at": time.time()}
    await q.answer()
    from assistant.lib.task_notify import task_title_link_html

    link = task_title_link_html(str(task.get("title") or "Задача"), task_id)
    chat_id = q.message.chat_id if q.message else uid
    await context.bot.send_message(
        chat_id,
        f"Напишите или надиктуйте голосовым ответ по задаче {link} — добавлю его в обсуждение.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data=CANCEL_CALLBACK)]]),
    )


async def try_consume_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Ждём ответ по задаче — отправляем текст в её обсуждение. True — сообщение обработано."""
    from assistant.lib.task_notify import task_title_link_html

    body = (text or "").strip()
    if not body:
        return False
    dtarget = discussion_target(update, context)
    if dtarget and reply_target(update, context) is None:
        return await _consume_discussion_reply(update, context, body, *dtarget)
    target = reply_target(update, context)
    if not target:
        return False
    msg = update.message
    user = update.effective_user
    uid = int(user.id)
    task_id, from_pending = target
    if from_pending:
        context.user_data.pop(_PENDING_KEY, None)
    try:
        row = await asyncio.to_thread(
            post_reply, uid, task_id, body, fallback_name=str(user.full_name or "").strip()
        )
    except ValueError as e:
        await msg.reply_text(str(e))
        return True
    except Exception as e:
        print(f"[task_reply] post uid={uid} task={task_id} err={e!r}")
        await msg.reply_text("Не получилось добавить ответ в задачу. Попробуйте ещё раз.")
        return True
    if not row:
        await msg.reply_text("Задача недоступна — возможно, её удалили.")
        return True
    link = task_title_link_html(str(row.get("title") or "Задача"), task_id)
    await msg.reply_text(
        f"✅ Ответ добавлен в задачу {link}.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return True



async def _consume_discussion_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, body: str, comment_id: int, from_pending: bool
) -> bool:
    import html

    msg = update.message
    user = update.effective_user
    uid = int(user.id)
    if from_pending:
        context.user_data.pop(_PENDING_KEY, None)
    try:
        row = await asyncio.to_thread(
            post_discussion_reply, uid, comment_id, body, fallback_name=str(user.full_name or "").strip()
        )
    except ValueError as e:
        await msg.reply_text(str(e))
        return True
    except Exception as e:
        print(f"[task_reply] discussion post uid={uid} comment={comment_id} err={e!r}")
        await msg.reply_text("Не получилось отправить ответ в обсуждение. Попробуйте ещё раз.")
        return True
    if not row:
        await msg.reply_text("Обсуждение недоступно — возможно, сообщение удалили.")
        return True
    title = html.escape(await asyncio.to_thread(discussion_title, row))
    await msg.reply_text(f"✅ Ответ добавлен в обсуждение «{title}».", parse_mode=ParseMode.HTML)
    return True
