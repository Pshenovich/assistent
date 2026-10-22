"""Лео в Telegram — тот же агент, что в чате Orbita: история, календарь, задачи, напоминания.

Переписка хранится в скрытом треде «Лео · Telegram» (Профиль → «Лео в Telegram»).
"""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from assistant.lib.telegram_status import dismiss_status, send_working_action, take_work_status

if TYPE_CHECKING:
    from assistant.nlu.dispatch import RouteExtras

# Пауза дольше — новая тема: старые реплики остаются в треде, но в контекст не идут.
IDLE_RESET = timedelta(hours=6)
CB_PREFIX = "leo:"
MAX_ACTION_BUTTONS = 8
MAX_PHOTO_BYTES = 5 * 1024 * 1024
# Что умеет gpt_calendar_tools.undo_action.
UNDOABLE_KINDS = frozenset({"event_create", "slot", "event_update", "task_create", "reminder_create"})
CHANNEL_HINT = (
    "Сейчас пользователь пишет тебе в Telegram. Отвечай коротко и по делу, обычным Markdown "
    "(без таблиц). Карточки встреч, задач и вариантов времени появятся кнопками под ответом — "
    "не дублируй их длинными списками."
)


async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, msg: Message) -> None:
    """Telegram гасит «печатает…» через ~5 с, а агент думает дольше — обновляем, пока ждём."""
    while True:
        await send_working_action(context, msg)
        await asyncio.sleep(4.5)


def _user_meta(update: Update) -> tuple[int, str, str | None]:
    u = update.effective_user
    if not u:
        return 0, "Пользователь", None
    name = (u.full_name or "").strip() or (f"@{u.username}" if u.username else "Пользователь")
    return int(u.id), name, (u.username or None)


def _reply_context(msg: Message) -> str:
    rep = msg.reply_to_message
    if not rep:
        return ""
    return (rep.text or rep.caption or "").strip()


async def _photo_images(msg: Message) -> list[dict[str, str]]:
    if not msg.photo:
        return []
    ph = msg.photo[-1]
    if (ph.file_size or 0) > MAX_PHOTO_BYTES:
        return []
    try:
        f = await ph.get_file()
        blob = bytes(await f.download_as_bytearray())
    except Exception as e:
        print(f"[leo_chat] photo download err={e!r}")
        return []
    if not blob or len(blob) > MAX_PHOTO_BYTES:
        return []
    return [{"mime": "image/jpeg", "b64": base64.b64encode(blob).decode("ascii")}]


def _load_history(uid: int, thread_id: int) -> list[dict[str, str]]:
    from assistant.services import leo_agent
    from assistant.stores import share_comments, user_prefs

    comments = share_comments.list_comments(uid, "chat", thread_id)
    return leo_agent.history_from_comments(
        comments, cut_id=user_prefs.get_tg_leo_cut_id(uid), idle_reset=IDLE_RESET
    )


def _save_turn(
    uid: int,
    thread_id: int,
    *,
    text: str,
    name: str,
    username: str | None,
    gpt: bool = False,
    model: str = "",
) -> None:
    from assistant.integrations.openrouter_client import gpt_display_label
    from assistant.stores import chat_threads, share_comments

    body = text.strip()
    if not body:
        return
    try:
        if gpt:
            share_comments.add_comment(
                uid,
                "chat",
                thread_id,
                author_user_id=uid,
                author_name=gpt_display_label(model) or share_comments.GPT_AUTHOR_NAME,
                author_username=share_comments.GPT_AUTHOR_USERNAME,
                body=body[: share_comments.MAX_GPT_BODY_LEN],
                prefix=share_comments.GPT_PREFIX,
            )
        else:
            share_comments.add_comment(
                uid,
                "chat",
                thread_id,
                author_user_id=uid,
                author_name=name,
                author_username=username,
                body=body[: share_comments.MAX_BODY_LEN],
            )
        chat_threads.after_comment(uid, thread_id, body, is_user=not gpt)
    except Exception as e:
        print(f"[leo_chat] save uid={uid} gpt={gpt} err={e!r}")


def _gpt_body(answer: str, actions: list[dict[str, Any]]) -> str:
    """Тело ответа в треде — как сохраняет клиент Orbita (с маркером действий)."""
    from assistant.services import leo_agent
    from assistant.stores import share_comments

    marker = leo_agent.cal_marker([str(a.get("id") or "") for a in actions])
    limit = share_comments.MAX_GPT_BODY_LEN - len(marker) - 2
    return (answer or "—")[:limit].rstrip() + marker


def _btn_label(prefix: str, a: dict[str, Any]) -> str:
    title = str(a.get("title") or "").strip() or "действие"
    when = str(a.get("when") or "").strip()
    label = f"{prefix} {title}" + (f" · {when}" if when else "")
    return label if len(label) <= 60 else label[:59] + "…"


def action_keyboard(actions: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    """Карточки действий ИИ → кнопки: выбрать слот, подтвердить удаление, отменить сделанное, открыть."""
    rows: list[list[InlineKeyboardButton]] = []
    for a in actions:
        if len(rows) >= MAX_ACTION_BUTTONS:
            break
        aid = str(a.get("id") or "")
        kind = str(a.get("kind") or "")
        status = str(a.get("status") or "")
        if not aid:
            continue
        if kind == "slot" and status == "proposed":
            rows.append(
                [InlineKeyboardButton(_btn_label("🕒", a), callback_data=f"{CB_PREFIX}apply:{aid}")]
            )
        elif kind.endswith("_delete") and status == "proposed":
            rows.append(
                [
                    InlineKeyboardButton(
                        _btn_label("🗑 Удалить", a), callback_data=f"{CB_PREFIX}apply:{aid}"
                    ),
                    InlineKeyboardButton("Оставить", callback_data=f"{CB_PREFIX}dismiss:{aid}"),
                ]
            )
        elif status == "done" and kind in UNDOABLE_KINDS:
            row = [InlineKeyboardButton(_btn_label("↩️ Отменить:", a), callback_data=f"{CB_PREFIX}undo:{aid}")]
            if a.get("link"):
                row.append(InlineKeyboardButton("Открыть", url=str(a["link"])))
            rows.append(row)
        elif a.get("link"):
            rows.append([InlineKeyboardButton(_btn_label("📅", a), url=str(a["link"]))])
    return InlineKeyboardMarkup(rows) if rows else None


async def handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    extras: "RouteExtras | None" = None,
) -> None:
    from assistant.lib.telegram_markdown import prepare_journal_qa_markdown
    from assistant.lib.telegram_message import reply_formatted
    from assistant.services import leo_agent
    from assistant.stores import chat_threads, user_prefs

    msg = update.message
    uid, name, username = _user_meta(update)
    if not msg or not uid:
        return
    question = (text or "").strip()
    status = take_work_status(context)
    typing = asyncio.create_task(_keep_typing(context, msg))
    try:
        thread = await asyncio.to_thread(chat_threads.telegram_thread, uid)
        tid = int(thread["id"])
        history = await asyncio.to_thread(_load_history, uid, tid)
        images = await _photo_images(msg)
        await asyncio.to_thread(
            _save_turn, uid, tid, text=question or "📎", name=name, username=username
        )
        model = user_prefs.get_tg_leo_model(uid)
        out, gcal_ctx = await asyncio.to_thread(
            leo_agent.run_turn,
            uid,
            question,
            history=history,
            model=model,
            display_name=name,
            telegram_username=username,
            context=_reply_context(msg),
            images=images,
            channel_hint=CHANNEL_HINT,
        )
        typing.cancel()
        if not out:
            await msg.reply_text("Не удалось ответить — попробуйте ещё раз.")
            return
        answer = str(out.get("answer") or "").strip() or "—"
        actions = out.get("calendar_actions") if isinstance(out.get("calendar_actions"), list) else []
        await asyncio.to_thread(
            _save_turn,
            uid,
            tid,
            text=_gpt_body(answer, actions),
            name=name,
            username=username,
            gpt=True,
            model=str(out.get("model") or model),
        )
        await dismiss_status(status)
        status = None
        await reply_formatted(
            msg,
            prepare_journal_qa_markdown(answer) or answer,
            reply_markup=action_keyboard(actions),
            rich_markdown=True,
        )
        organizer = update.effective_user.to_dict() if update.effective_user else None
        await leo_agent.notify_invitees(gcal_ctx, uid=uid, organizer_user=organizer)
    except Exception as e:
        print(f"[leo_chat] uid={uid} err={e!r}")
        await msg.reply_text(f"Ошибка: {e}")
    finally:
        typing.cancel()
        await dismiss_status(status)


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/new — начать разговор с Лео заново (старые сообщения остаются в треде)."""
    from assistant.stores import chat_threads, share_comments, user_prefs

    msg = update.message
    uid, _, _ = _user_meta(update)
    if not msg or not uid:
        return

    def _cut() -> None:
        thread = chat_threads.telegram_thread(uid)
        rows = share_comments.list_comments(uid, "chat", int(thread["id"]))
        user_prefs.set_tg_leo_cut_id(uid, max((int(r.get("id") or 0) for r in rows), default=0))

    await asyncio.to_thread(_cut)
    await msg.reply_text("Начинаем с чистого листа — прошлый разговор я больше не учитываю.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """leo:<op>:<action_id> — кнопки под ответом Лео (как карточки в Orbita)."""
    from assistant.services import gpt_calendar_tools as gcal_tools
    from assistant.services import leo_agent

    q = update.callback_query
    if not q or not q.data:
        return
    uid, _, username = _user_meta(update)
    try:
        _, op, action_id = q.data.split(":", 2)
    except ValueError:
        await q.answer()
        return
    try:
        fresh, created = await asyncio.to_thread(
            leo_agent.apply_action_op, uid, action_id, op, telegram_username=username
        )
    except LookupError:
        await q.answer("Действие не найдено", show_alert=True)
        return
    except Exception as e:
        print(f"[leo_chat] op={op} id={action_id} err={e!r}")
        await q.answer(f"Не получилось: {e}"[:190], show_alert=True)
        return
    if created and created.get("attendee_emails"):
        try:
            from assistant.services import meeting_invites as inv

            await inv.notify_invitees_for_miniapp(
                organizer_uid=uid,
                organizer_user=update.effective_user.to_dict() if update.effective_user else {},
                result=created,
            )
        except Exception as e:
            print(f"[leo_chat] slot invite err={e!r}")
    pub = gcal_tools.public_action(fresh, viewer_uid=uid)
    status = str(pub.get("status") or "")
    note = {
        "undone": "Отменено",
        "applied": "Удалено",
        "dismissed": "Оставил как есть",
        "done": "Готово",
    }.get(status, "")
    await q.answer(note or None)
    if q.message is None:
        return
    # Обновляем кнопки: у выбранного слота соседи исчезают, сделанное получает «Отменить».
    ids = _keyboard_action_ids(q.message)
    acts = [gcal_tools.public_action(a, viewer_uid=uid) for a in gcal_tools.actions_by_ids(ids)]
    fresh_kb = action_keyboard(acts)
    rows = [list(r) for r in (fresh_kb.inline_keyboard if fresh_kb else [])]
    # Кнопки-ссылки без callback (карточки «посмотреть») оставляем как были.
    old_markup = getattr(q.message, "reply_markup", None)
    for row in getattr(old_markup, "inline_keyboard", None) or []:
        if not any(str(getattr(b, "callback_data", "") or "").startswith(CB_PREFIX) for b in row):
            rows.append(list(row))
    try:
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows) if rows else None)
    except Exception:
        pass
    if note:
        title = str(pub.get("title") or "").strip()
        when = str(pub.get("when") or "").strip()
        await q.message.reply_text(
            f"{note}: {title}" + (f" — {when}" if when else "") if title else note
        )


def _keyboard_action_ids(message: Any) -> list[str]:
    markup = getattr(message, "reply_markup", None)
    ids: list[str] = []
    for row in getattr(markup, "inline_keyboard", None) or []:
        for btn in row:
            data = str(getattr(btn, "callback_data", "") or "")
            if data.startswith(CB_PREFIX):
                aid = data.split(":", 2)[-1]
                if aid not in ids:
                    ids.append(aid)
    return ids
