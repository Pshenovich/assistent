"""Zoom: мгновенные и запланированные ссылки, перенос и удаление."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from assistant.integrations import zoom_api, zoom_oauth
from assistant.lib import zoom_gcal
from assistant.lib.user_timezone import resolve_user_tz_name
from assistant.nlu import llm as nlu_llm
from assistant.services import calendar as cal_svc
from assistant.services import meeting_record_schedule as meeting_sched
from assistant.stores import user_prefs
from assistant.stores import zoom_recent_meetings as zrm


def _uid(update: Update) -> int:
    user = update.effective_user
    return int(user.id) if user else 0


async def _reply_no_auth(msg) -> None:
    await msg.reply_text("Zoom не подключён. Выполните /zoom_auth.")


async def handle_instant(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    if not zoom_gcal.user_has_zoom(int(user.id)):
        await _reply_no_auth(msg)
        return
    try:
        token = zoom_oauth.get_access_token_for_user(int(user.id))
        auto_record = user_prefs.zoom_auto_record_enabled(int(user.id)) and meeting_sched.service_available()
        zm = zoom_api.create_instant_meeting(token, topic="Встреча", auto_record=auto_record)
        zrm.register_meeting(int(user.id), zm, source="zoom_instant")
        join = str(zm.get("join_url") or "")
        lines = [f"Zoom:\n{join}" if join else "Ссылка создана."]
        if auto_record and join:
            meeting_sched.maybe_schedule_for_zoom_create(
                int(user.id),
                join_url=join,
                topic="Встреча",
                start_at=None,
                source="zoom_instant",
            )
            lines.append(
                "\nLeo подключится к встрече и запишет её. "
                "После окончания пришлю транскрипцию и саммари."
            )
        await msg.reply_text("\n".join(lines))
    except Exception as e:
        await msg.reply_text(f"Zoom: {e}")


async def handle_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    msg = update.message
    if not msg:
        return
    uid = _uid(update)
    if not zoom_gcal.user_has_zoom(uid):
        await _reply_no_auth(msg)
        return
    now = datetime.now(user_prefs.get_user_tz(uid))
    parsed = nlu_llm.parse_zoom(text, now=now, user_id=uid, requested_action="update")
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
    if not zoom_gcal.user_has_zoom(uid):
        await _reply_no_auth(msg)
        return
    now = datetime.now(user_prefs.get_user_tz(uid))
    parsed = nlu_llm.parse_zoom(text, now=now, user_id=uid, requested_action="delete")
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
        await msg.reply_text("\n".join(qs) if qs else "Уточните, какую Zoom-встречу имеете в виду.")
        return
    q = str(parsed.get("match_query") or "").strip()
    md = str(parsed.get("match_date") or "").strip() or None
    # Для reschedule LLM часто ставит match_date = день НОВОГО времени («tomorrow»),
    # а не день существующей встречи — из-за этого поиск пустой. Ищем без даты.
    if action == "update":
        new_start = str(parsed.get("start") or "").strip()
        if md and new_start and md[:10] == new_start[:10]:
            md = None
        lookup_date = None
    else:
        lookup_date = md

    # Без темы («delete zoom» / «reschedule zoom to …») — только встречи, созданные ботом.
    # Иначе GET /users/me/meetings возвращает весь аккаунт ревьюера (Bookings и т.п.),
    # бот просит выбрать номер и DELETE/PATCH так и не уходят в Call logs.
    bot_hits = zoom_gcal.find_bot_tracked_meetings(uid, q, lookup_date)
    if q:
        zoom_hits = zoom_gcal.find_zoom_meetings(uid, q, lookup_date)
        if not zoom_hits:
            zoom_hits = bot_hits
    else:
        zoom_hits = bot_hits
        if not zoom_hits:
            # Фоллбек: API list, но только если локально пусто
            zoom_hits = zoom_gcal.find_zoom_meetings(uid, q, lookup_date)

    gcal_hits = zoom_gcal.find_gcal_events_with_zoom(uid, q, lookup_date)
    gcal_actionable = [
        row
        for row in gcal_hits
        if zoom_gcal.read_zoom_meeting_id(row.get("_raw") or {})
    ]

    def _pick_one(meetings: list[dict[str, Any]], *, scheduled_only: bool) -> dict[str, Any] | None:
        pool = meetings
        if scheduled_only:
            sched = [m for m in meetings if int(m.get("type") or 0) == 2]
            pool = sched or [m for m in meetings if int(m.get("type") or 0) != 1]
        if not pool:
            return None
        # list_recent уже newest-first; для API без created_at — первый
        return pool[0]

    if action == "update":
        if not parsed.get("start"):
            await msg.reply_text(
                "Укажите новое время, например: reschedule zoom to 15:00 tomorrow "
                "(или перенеси зум на 15:00). Для переноса нужна запланированная встреча — "
                "сначала создайте: meeting tomorrow at 3pm with zoom."
            )
            return
        meeting = _pick_one(zoom_hits, scheduled_only=True)
        if meeting is not None:
            if int(meeting.get("type") or 0) == 1:
                await msg.reply_text(
                    "Instant Zoom meetings cannot be rescheduled. "
                    "Create a scheduled meeting (e.g. meeting tomorrow at 3pm with zoom), "
                    "then reschedule it."
                )
                return
            if q and len([m for m in zoom_hits if int(m.get("type") or 0) == 2]) > 1:
                await _ask_pick_zoom(
                    msg,
                    update,
                    uid,
                    [m for m in zoom_hits if int(m.get("type") or 0) == 2][:8],
                    parsed,
                    action="update",
                )
                return
            await _update_zoom_only(msg, uid, meeting, parsed)
            return
        if len(gcal_actionable) > 1 and q:
            await _ask_pick(msg, update, uid, gcal_actionable, parsed, action="update")
            return
        if gcal_actionable:
            await _update_gcal_zoom(msg, uid, gcal_actionable[0], parsed)
            return
        await msg.reply_text("Не нашёл Zoom-встречу для переноса.")
        return

    # delete
    meeting = _pick_one(zoom_hits, scheduled_only=False)
    if meeting is not None:
        if q and len(zoom_hits) > 1:
            await _ask_pick_zoom(msg, update, uid, zoom_hits[:8], parsed, action="delete")
            return
        await _delete_zoom_only(msg, uid, meeting)
        return
    if len(gcal_actionable) > 1 and q:
        await _ask_pick(msg, update, uid, gcal_actionable, parsed, action="delete")
        return
    if gcal_actionable:
        row = gcal_actionable[0]
        eid = str(row.get("id") or "")
        cal_id = str(row.get("calendar_id") or "").strip() or None
        ok = zoom_gcal.delete_zoom_for_event(uid, eid, calendar_id=cal_id)
        title = str(row.get("summary") or "Встреча")
        if ok:
            await msg.reply_text(f"Zoom для «{title}» удалён (событие в календаре сохранено).")
        else:
            await msg.reply_text(
                f"Не удалось удалить Zoom для «{title}» через Zoom API. "
                "Попробуйте снова или удалите встречу в Zoom."
            )
        return
    await msg.reply_text("Не нашёл Zoom-встречу для удаления.")


async def _ask_pick(msg, update, uid, hits, parsed, *, action: str) -> None:
    from assistant.lib import calendar_pending_store as cps

    verb = "удалить" if action == "delete" else "перенести"
    lines = [f"Какую Zoom-встречу {verb}? Ответьте номером:"]
    for i, ev in enumerate(hits[:8], 1):
        lines.append(f"{i}. {ev.get('summary')} ({str(ev.get('start') or '')[:16]})")
    chat_id = int(update.effective_chat.id) if update.effective_chat else 0
    cps.set_pending(
        chat_id,
        {
            "kind": "await_zoom_pick",
            "action": action,
            "user_id": uid,
            "parsed": parsed,
            "candidates": hits[:8],
        },
        user_id=uid,
    )
    await msg.reply_text("\n".join(lines))


async def _ask_pick_zoom(msg, update, uid, hits, parsed, *, action: str) -> None:
    from assistant.lib import calendar_pending_store as cps

    verb = "удалить" if action == "delete" else "перенести"
    lines = [f"Какую Zoom-встречу {verb}? Ответьте номером:"]
    for i, m in enumerate(hits[:8], 1):
        lines.append(f"{i}. {m.get('topic')} ({m.get('start_time', '')[:16]})")
    chat_id = int(update.effective_chat.id) if update.effective_chat else 0
    cps.set_pending(
        chat_id,
        {
            "kind": "await_zoom_api_pick",
            "action": action,
            "user_id": uid,
            "parsed": parsed,
            "candidates": hits[:8],
        },
        user_id=uid,
    )
    await msg.reply_text("\n".join(lines))


async def _update_gcal_zoom(msg, uid: int, row: dict[str, Any], parsed: dict[str, Any]) -> None:
    eid = str(row.get("id") or "")
    cal_id = str(row.get("calendar_id") or "").strip() or None
    try:
        result = cal_svc.update_event(uid, eid, parsed, calendar_id=cal_id)
        zoom_gcal.sync_zoom_after_calendar_update(uid, eid, parsed, calendar_id=cal_id)
        await msg.reply_text(
            cal_svc.format_create_reply_html(result, uid).replace(
                "Создана встреча:", "Zoom-встреча обновлена:"
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        await msg.reply_text(f"Zoom: {e}")


async def _update_zoom_only(msg, uid: int, meeting: dict[str, Any], parsed: dict[str, Any]) -> None:
    mid = str(meeting.get("id") or "")
    if not mid:
        await msg.reply_text("Не удалось определить ID встречи Zoom.")
        return
    start = cal_svc._parse_dt(str(parsed["start"]), uid)
    dur = int(parsed.get("duration_min") or 60)
    end_s = str(parsed.get("end") or "").strip()
    end = (
        cal_svc._parse_dt(end_s, uid)
        if end_s
        else start + timedelta(minutes=max(15, dur))
    )
    try:
        token = zoom_oauth.get_access_token_for_user(uid)
        tz_name = resolve_user_tz_name(uid)
        zoom_api.update_scheduled_meeting(
            token,
            mid,
            start_utc=start,
            end_utc=end,
            timezone_str=tz_name,
        )
        print(f"[zoom] PATCH meeting_id={mid} user={uid}")
        try:
            from datetime import timezone as tz_utc

            zrm.register_meeting(
                uid,
                {
                    "id": mid,
                    "topic": meeting.get("topic") or "Встреча",
                    "start_time": start.astimezone(tz_utc.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "type": 2,
                    "join_url": meeting.get("join_url") or "",
                },
                source=str(meeting.get("source") or "zoom_update"),
            )
        except Exception:
            pass
        when = cal_svc.format_event_when(start, end, uid)
        await msg.reply_text(f"Zoom-встреча перенесена: {when}")
    except Exception as e:
        await msg.reply_text(f"Zoom: {e}")


async def _delete_zoom_only(msg, uid: int, meeting: dict[str, Any]) -> None:
    mid = str(meeting.get("id") or "")
    topic = str(meeting.get("topic") or "Встреча")
    try:
        token = zoom_oauth.get_access_token_for_user(uid)
        zoom_api.delete_meeting(token, mid)
        print(f"[zoom] DELETE meeting_id={mid} user={uid}")
        zrm.remove_meeting(uid, mid)
        await msg.reply_text(f"Zoom-встреча «{topic}» удалена.")
    except Exception as e:
        await msg.reply_text(f"Zoom: {e}")


async def try_continue_pending(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Выбор номера при нескольких Zoom-встречах."""
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
    kind = str(st.get("kind") or "")
    if kind not in ("await_zoom_pick", "await_zoom_api_pick"):
        return False
    idx = int(text) - 1
    candidates = list(st.get("candidates") or [])
    if idx < 0 or idx >= len(candidates):
        await msg.reply_text("Неверный номер.")
        return True
    parsed = dict(st.get("parsed") or {})
    action = str(st.get("action") or "")
    cps.clear_pending(chat_id, user_id=uid)
    if kind == "await_zoom_pick":
        row = candidates[idx]
        if action == "delete":
            eid = str(row.get("id") or "")
            cal_id = str(row.get("calendar_id") or "").strip() or None
            ok = zoom_gcal.delete_zoom_for_event(uid, eid, calendar_id=cal_id)
            if ok:
                await msg.reply_text(f"Zoom для «{row.get('summary')}» удалён.")
            else:
                await msg.reply_text(
                    f"Не удалось удалить Zoom для «{row.get('summary')}» через Zoom API."
                )
        else:
            await _update_gcal_zoom(msg, uid, row, parsed)
        return True
    meeting = candidates[idx]
    if action == "delete":
        await _delete_zoom_only(msg, uid, meeting)
    else:
        if int(meeting.get("type") or 0) == 1:
            await msg.reply_text(
                "Instant Zoom meetings cannot be rescheduled. "
                "Create a scheduled meeting first, then reschedule it."
            )
        else:
            await _update_zoom_only(msg, uid, meeting, parsed)
    return True
