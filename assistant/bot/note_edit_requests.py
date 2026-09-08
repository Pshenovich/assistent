"""Заявки на редактирование заметки: кнопки Разрешить / Отклонить в Leo."""

from __future__ import annotations

from html import escape as html_escape

from telegram import Update
from telegram.ext import ContextTypes

from assistant.lib.telegram_notify import send_message
from assistant.stores import note_members
from assistant.stores import notes as notes_store


def _username_label(req: dict) -> str:
    return note_members.requester_label(
        req.get("requester_username"),
        req.get("requester_name"),
        str(req.get("requester_user_id") or ""),
    )


async def handle_note_edit_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    user = update.effective_user
    if not q or not user:
        return
    data = (q.data or "").strip()
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "ned":
        return
    action, token = parts[1], parts[2]
    req = note_members.get_edit_request(token)
    if not req:
        await q.answer("Заявка устарела или уже обработана.", show_alert=True)
        return
    if str(req.get("owner_user_id") or "") != str(int(user.id)):
        await q.answer("Это заявка не вам.", show_alert=True)
        return
    if req.get("status") != "pending":
        await q.answer("Заявка уже обработана.", show_alert=True)
        return
    await q.answer()
    note = notes_store.get_note(req["owner_user_id"], int(req["note_id"]))
    title = str((note or {}).get("title") or "Без названия")
    allow = action == "ok"
    updated = note_members.resolve_edit_request(token, allow=allow)
    if not updated:
        try:
            await q.edit_message_text("Заявка устарела или уже обработана.")
        except Exception:
            pass
        return
    who = _username_label(req)
    if allow:
        text = f"Редактирование разрешено: {title} — {who}."
        try:
            await q.edit_message_text(text)
        except Exception:
            pass
        link = note_members.note_title_link_html(title, int(req["note_id"]))
        send_message(
            int(req["requester_user_id"]),
            f"Вам открыто редактирование заметки {link}.",
        )
        return
    text = f"Запрос на редактирование отклонён: {title} — {who}."
    try:
        await q.edit_message_text(text)
    except Exception:
        pass
    send_message(
        int(req["requester_user_id"]),
        f"Автор отклонил запрос на редактирование заметки «{html_escape(title)}».",
    )
