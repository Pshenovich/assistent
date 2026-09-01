"""Yandex Telemost: мгновенные ссылки, перенос и удаление через календарь."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from assistant.integrations import telemost_api, telemost_oauth
from assistant.lib import telemost_gcal
from assistant.nlu import llm as nlu_llm
from assistant.services import calendar as cal_svc
from assistant.stores import user_prefs


def _uid(update: Update) -> int:
    user = update.effective_user
    return int(user.id) if user else 0


def _format_err(exc: Exception) -> str:
    if telemost_api.is_org_restricted_error(exc):
        return telemost_api.org_restricted_user_message()
    return f"Телемост: {exc}"


async def _reply_no_auth(msg) -> None:
    await msg.reply_text("Телемост не подключён. Выполните /telemost_auth.")


async def handle_instant(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    uid = int(user.id)
    if not telemost_gcal.user_has_telemost(uid):
        await _reply_no_auth(msg)
        return
    try:
        token = telemost_oauth.get_access_token_for_user(uid)
        auto_summ = user_prefs.telemost_auto_record_enabled(uid)
        conf = telemost_api.create_conference(token, auto_summarization=auto_summ)
        join = str(conf.get("join_url") or "")
        lines = [f"Телемост:\n{join}" if join else "Ссылка создана."]
        if auto_summ:
            lines.append(
                "\nВключено автоконспектирование Телемоста. "
                "После встречи конспект придёт на почту; Leo доставит саммари в Telegram, "
                "если подключён доступ к почте (mail:imap_ro)."
            )
        await msg.reply_text("\n".join(lines))
    except Exception as e:
        await msg.reply_text(_format_err(e))


async def handle_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    msg = update.message
    if not msg:
        return
    uid = _uid(update)
    if not telemost_gcal.user_has_telemost(uid):
        await _reply_no_auth(msg)
        return
    now = datetime.now(user_prefs.get_user_tz(uid))
    parsed = nlu_llm.parse_telemost(text, now=now, user_id=uid, requested_action="update")
    if not parsed:
        await msg.reply_text("Не удалось разобрать запрос.")
        return
    await _run_update_or_delete(update, msg, uid, parsed, action="update", now=now)


async def handle_delete(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    msg = update.message
    if not msg:
        return
    uid = _uid(update)
    if not telemost_gcal.user_has_telemost(uid):
        await _reply_no_auth(msg)
        return
    now = datetime.now(user_prefs.get_user_tz(uid))
    parsed = nlu_llm.parse_telemost(text, now=now, user_id=uid, requested_action="delete")
    if not parsed:
        await msg.reply_text("Не удалось разобрать запрос.")
        return
    await _run_update_or_delete(update, msg, uid, parsed, action="delete", now=now)


async def _run_update_or_delete(
    update: Update,
    msg,
    uid: int,
    parsed: dict[str, Any],
    *,
    action: str,
    now: datetime,
) -> None:
    if parsed.get("need_more_info"):
        qs = parsed.get("questions") or []
        await msg.reply_text(
            "\n".join(qs) if qs else "Уточните, какую встречу в Телемосте имеете в виду."
        )
        return
    q = str(parsed.get("match_query") or "").strip()
    md = str(parsed.get("match_date") or "").strip() or None
    if action == "delete" and not md:
        md = now.date().isoformat()

    gcal_hits = telemost_gcal.find_gcal_events_with_telemost(uid, q, md)

    if action == "update":
        if not parsed.get("start"):
            await msg.reply_text(
                "Укажите новое время, например: перенеси телемост на 15:00."
            )
            return
        if len(gcal_hits) > 1:
            await _ask_pick(msg, update, uid, gcal_hits, parsed, action="update")
            return
        if len(gcal_hits) == 1:
            await _update_gcal_telemost(msg, uid, gcal_hits[0], parsed)
            return
        await msg.reply_text("Не нашёл встречу с Телемостом для переноса.")
        return

    if len(gcal_hits) > 1:
        await _ask_pick(msg, update, uid, gcal_hits, parsed, action="delete")
        return
    if len(gcal_hits) == 1:
        row = gcal_hits[0]
        eid = str(row.get("id") or "")
        cal_id = str(row.get("calendar_id") or "").strip() or None
        telemost_gcal.delete_telemost_for_event(uid, eid, calendar_id=cal_id)
        title = str(row.get("summary") or "Встреча")
        await msg.reply_text(
            f"Телемост для «{title}» удалён (событие в календаре сохранено)."
        )
        return
    await msg.reply_text("Не нашёл встречу с Телемостом для удаления.")


async def _ask_pick(msg, update, uid, hits, parsed, *, action: str) -> None:
    from assistant.lib import calendar_pending_store as cps

    verb = "удалить" if action == "delete" else "перенести"
    lines = [f"Какую встречу в Телемосте {verb}? Ответьте номером:"]
    for i, ev in enumerate(hits[:8], 1):
        lines.append(f"{i}. {ev.get('summary')} ({str(ev.get('start') or '')[:16]})")
    chat_id = int(update.effective_chat.id) if update.effective_chat else 0
    cps.set_pending(
        chat_id,
        {
            "kind": "await_telemost_pick",
            "action": action,
            "user_id": uid,
            "parsed": parsed,
            "candidates": hits[:8],
        },
        user_id=uid,
    )
    await msg.reply_text("\n".join(lines))


async def _update_gcal_telemost(msg, uid: int, row: dict[str, Any], parsed: dict[str, Any]) -> None:
    eid = str(row.get("id") or "")
    cal_id = str(row.get("calendar_id") or "").strip() or None
    try:
        result = cal_svc.update_event(uid, eid, parsed, calendar_id=cal_id)
        await msg.reply_text(
            cal_svc.format_create_reply_html(result, uid).replace(
                "Создана встреча:", "Встреча с Телемостом обновлена:"
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        await msg.reply_text(_format_err(e))


async def try_continue_pending(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return False
    text = (msg.text or "").strip()
    if not text.isdigit():
        return False
    from assistant.lib import calendar_pending_store as cps

    chat_id = int(update.effective_chat.id) if update.effective_chat else 0
    uid = int(user.id)
    st = cps.get_pending(chat_id, ttl_sec=900, user_id=uid)
    if not st or int(st.get("user_id") or 0) != uid:
        return False
    if str(st.get("kind") or "") != "await_telemost_pick":
        return False
    idx = int(text) - 1
    candidates = list(st.get("candidates") or [])
    if idx < 0 or idx >= len(candidates):
        await msg.reply_text("Неверный номер.")
        return True
    parsed = dict(st.get("parsed") or {})
    action = str(st.get("action") or "")
    cps.clear_pending(chat_id, user_id=uid)
    row = candidates[idx]
    if action == "delete":
        eid = str(row.get("id") or "")
        cal_id = str(row.get("calendar_id") or "").strip() or None
        telemost_gcal.delete_telemost_for_event(uid, eid, calendar_id=cal_id)
        await msg.reply_text(f"Телемост для «{row.get('summary')}» удалён.")
    else:
        await _update_gcal_telemost(msg, uid, row, parsed)
    return True
