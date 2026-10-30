"""Агент Лео: системный промпт, календарные инструменты, история — общий для чата Orbita и Telegram."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from assistant.services import gpt_calendar_tools as gcal_tools

# Как в webapp/app.js: GPT_HISTORY_MAX_TURNS / GPT_HISTORY_MAX_CHARS, сообщение ≤ 6000.
HISTORY_MAX_TURNS = 16
HISTORY_MAX_CHARS = 18000
HISTORY_MSG_MAX = 6000
# Ответ ИИ, который действовал в календаре, хранит id действий: «[[leo-cal:id,id]]».
CAL_MARKER_RE = re.compile(r"\n*\[\[leo-cal:([a-z0-9,]+)\]\]\s*$", re.I)


def gpt_system_text(agent: dict[str, Any]) -> str:
    system_text = str(agent.get("system") or "")
    if agent.get("kind") == "gpt":
        from assistant.nlu.prompts import ASK_SYSTEM, LEO_IDENTITY

        # Сохранённый пользователем промпт GPT мог быть скопирован до появления LEO_IDENTITY.
        if not system_text.strip():
            system_text = ASK_SYSTEM
        elif "Orbita" not in system_text:
            system_text = LEO_IDENTITY + "\n\n" + system_text
    return system_text


def calendar_addon(uid: int) -> str:
    from assistant.services import mail_tools

    cal_addon = gcal_tools.now_prompt_line(uid) + "\n" + gcal_tools.SYSTEM_ADDON
    if not gcal_tools.has_google_calendar(uid):
        cal_addon += (
            "\nGoogle Календарь у пользователя не подключён — его календарь в Орбите: встречи Орбиты (id orb-…), "
            "задачи и напоминания Leo. Ставить (в том числе повторяющиеся), переносить и отменять встречи, "
            "искать свободное время можно как обычно."
        )
    if mail_tools.has_mail(uid):
        cal_addon += "\n" + mail_tools.MAIL_ADDON
    from assistant.onboarding import leo_help

    cal_addon += "\n\n" + leo_help.system_addon()
    return cal_addon


def append_system(system_text: str, block: str) -> str:
    if not block:
        return system_text
    return (system_text.rstrip() + "\n\n" + block).strip() if system_text else block


def tool_kwargs(gcal_ctx: gcal_tools.GptCalendarContext, question: str) -> dict[str, Any]:
    from assistant.services import mail_tools

    tools = gcal_tools.active_tools(gcal_ctx.uid)
    mail_q = mail_tools.looks_like_mail_question(question) and any(
        t["function"]["name"] in mail_tools.MAIL_TOOL_NAMES for t in tools
    )
    return {
        "tools": tools,
        "tool_runner": gcal_tools.make_runner(gcal_ctx),
        # Вопрос про календарь или почту — модель обязана сначала посмотреть данные.
        "tool_choice_first": "required" if gcal_tools.looks_like_calendar_question(question) or mail_q else "auto",
    }


def make_calendar_context(
    uid: int, *, telegram_username: str | None, display_name: str
) -> gcal_tools.GptCalendarContext:
    from assistant.lib.task_notify import notify_task_assignee
    from assistant.services.task_google_sync import sync_task_google

    return gcal_tools.GptCalendarContext(
        uid=uid,
        telegram_username=str(telegram_username or "").strip() or None,
        display_name=display_name,
        sync_task_google=sync_task_google,
        notify_task_assignee=notify_task_assignee,
    )


def attach_calendar_actions(
    gcal_ctx: gcal_tools.GptCalendarContext, out: dict[str, Any], uid: int
) -> None:
    try:
        gcal_tools.finalize_auto_cards(gcal_ctx, str(out.get("answer") or ""))
    except Exception as e:
        print(f"[gpt_calendar] auto_cards err={e!r}")
    if gcal_ctx.actions:
        out["calendar_actions"] = [
            gcal_tools.public_action(a, viewer_uid=uid)
            for a in gcal_tools.actions_by_ids(gcal_ctx.actions)
        ]


async def notify_invitees(
    gcal_ctx: gcal_tools.GptCalendarContext, *, uid: int, organizer_user: dict[str, Any] | None
) -> None:
    """Приглашения участникам встреч, которые ИИ создал/изменил (как при создании из календаря)."""
    if not (gcal_ctx.created_events or gcal_ctx.updated_events):
        return
    try:
        from assistant.services import meeting_invites as inv

        organizer = organizer_user if isinstance(organizer_user, dict) else {}
        for created in gcal_ctx.created_events:
            await inv.notify_invitees_for_miniapp(
                organizer_uid=uid, organizer_user=organizer, result=created
            )
        for ev in gcal_ctx.updated_events:
            added = inv.added_invitees(ev)
            if added:
                await inv.notify_invitees_for_miniapp(
                    organizer_uid=uid, organizer_user=organizer, result=ev, only_emails=added
                )
    except Exception as e:
        print(f"[gpt_calendar] invite_notify err={e!r}")


# ---------------------------------------------------------------- история


def cal_marker(action_ids: list[str]) -> str:
    ids = [str(i) for i in action_ids if str(i).strip()]
    return "\n\n[[leo-cal:" + ",".join(ids) + "]]" if ids else ""


def strip_cal_marker(text: str) -> str:
    return CAL_MARKER_RE.sub("", str(text or ""))


def cal_marker_for_history(text: str) -> str:
    """Для истории модели: вместо маркера — что уже сделано, чтобы ИИ не повторял действия."""
    m = CAL_MARKER_RE.search(str(text or ""))
    clean = strip_cal_marker(text)
    if not m:
        return clean
    done: list[str] = []
    shown: list[str] = []
    for a in gcal_tools.actions_by_ids([x for x in m.group(1).split(",") if x]):
        kind = str(a.get("kind") or "")
        if kind == "show_task":
            # карточки задач в чате: модели нужны их id («перенеси обе на пятницу»)
            tid = str((a.get("payload") or {}).get("target_id") or "")
            if tid:
                shown.append(f"«{a.get('title') or 'Задача'}» (task id {tid})")
            continue
        if kind == "email_thread":
            p = a.get("payload") or {}
            shown.append(f"письмо «{a.get('title') or 'без темы'}» от {p.get('from') or '?'} (mail key: {p.get('mail_key')})")
            continue
        if kind == "email_draft":
            p = a.get("payload") or {}
            st = {"sent": "отправлен", "sending": "отправляется", "failed": "не отправился"}.get(str(a.get("status")), "черновик у пользователя")
            done.append(f"ответ на письмо «{a.get('title') or ''}» (mail key: {p.get('mail_key')}) — {st}")
            continue
        if kind.startswith("show_") or kind == "focus_item":
            continue
        pub = gcal_tools.public_action(a)
        st = {"undone": " (отменено)", "proposed": " (предложено)"}.get(str(a.get("status") or ""), "")
        done.append((pub.get("title") or kind) + (f" — {pub['when']}" if pub.get("when") else "") + st)
    out = clean
    if shown:
        out += "\n[В чате показаны задачи: " + "; ".join(shown) + "]"
    if done or not shown:
        out += "\n[Уже выполнено, не повторять: " + ("; ".join(done) if done else "действия с календарём из этого ответа") + "]"
    return out


def _parse_ts(raw: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def history_from_comments(
    comments: list[dict[str, Any]],
    *,
    cut_id: int = 0,
    idle_reset: timedelta | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Порт gptHistoryFromComments для треда с Лео: GPT → assistant, остальное → user.

    idle_reset: если между репликами пауза дольше — старое считаем другой темой и не берём.
    """
    from assistant.stores import share_comments as sc

    rows = [
        c
        for c in comments or []
        if c
        and int(c.get("id") or 0) > int(cut_id or 0)
        and not sc.is_agent_turn(c)
        and not sc.is_research_turn(c)
    ]
    rows.sort(key=lambda c: (str(c.get("created_at") or ""), int(c.get("id") or 0)))
    if idle_reset is not None and rows:
        last = now or datetime.now(timezone.utc)
        start = len(rows)
        for i in range(len(rows) - 1, -1, -1):
            ts = _parse_ts(rows[i].get("created_at"))
            if ts is None or last - ts > idle_reset:
                break
            start = i
            last = ts
        rows = rows[start:]
    history: list[dict[str, str]] = []
    for c in rows:
        content = cal_marker_for_history(str(c.get("body") or "")).strip()
        if not content or content == "📎":
            continue
        if len(content) > HISTORY_MSG_MAX:
            content = content[: HISTORY_MSG_MAX - 1] + "…"
        role = "assistant" if sc.is_gpt_comment(c) else "user"
        history.append({"role": role, "content": content})
    history = history[-HISTORY_MAX_TURNS:]
    total = 0
    for i in range(len(history) - 1, -1, -1):
        total += len(history[i]["content"])
        if total > HISTORY_MAX_CHARS:
            history = history[i + 1 :]
            break
    return history


# ---------------------------------------------------------------- один ход


def run_turn(
    uid: int,
    text: str,
    *,
    history: list[dict[str, str]] | None,
    model: str,
    display_name: str,
    telegram_username: str | None = None,
    context: str = "",
    images: list[dict[str, str]] | None = None,
    channel_hint: str = "",
) -> tuple[dict[str, Any] | None, gcal_tools.GptCalendarContext]:
    """Один ход агента Лео (синхронно). Возвращает (ответ, календарный контекст для приглашений)."""
    from assistant.compat.miniapp_shims import gpt_openrouter_answer_with_context
    from assistant.integrations.openrouter_client import (
        model_emits_images,
        set_openrouter_usage_telegram_user,
    )
    from assistant.stores import user_agents

    from assistant.nlu import auto_model

    agent = user_agents.runtime_config(uid, "gpt")
    system_text = append_system(gpt_system_text(agent), channel_hint)
    # «Авто»: простой общий вопрос — дешёвая модель, остальное — сильная.
    model, auto_hint = auto_model.resolve(
        model, text, history=history, quote=context, has_attachments=bool(images)
    )
    system_text = append_system(system_text, auto_hint)
    gcal_ctx = make_calendar_context(
        uid, telegram_username=telegram_username, display_name=display_name
    )
    kwargs: dict[str, Any] = {}
    if not model_emits_images(model or ""):
        system_text = append_system(system_text, calendar_addon(uid))
        kwargs = tool_kwargs(gcal_ctx, text)
    prompt = text.strip() or "Опиши вложение и ответь по нему."
    try:
        set_openrouter_usage_telegram_user(telegram_user_id=uid, telegram_username=telegram_username)
        out = gpt_openrouter_answer_with_context(
            prompt,
            (context or "").strip(),
            history=history or None,
            model=model,
            images=images or None,
            system=system_text,
            temperature=agent.get("temperature"),
            web=bool(agent.get("web_search")),
            **kwargs,
        )
    finally:
        set_openrouter_usage_telegram_user(telegram_user_id=None)
    if out is not None:
        attach_calendar_actions(gcal_ctx, out, uid)
    return out, gcal_ctx


# ---------------------------------------------------------------- кнопки карточек действий


class ActionError(Exception):
    pass


class ActionNeedsConfirm(ActionError):
    """Отправка письма на адрес не из переписки — нужен явный повтор с подтверждением."""

    def __init__(self, message: str, addrs: list[str]) -> None:
        super().__init__(message)
        self.addrs = addrs


def apply_action_op(
    uid: int, action_id: str, op: str, *, telegram_username: str | None = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """undo / apply / dismiss для карточки действия ИИ. Возвращает (свежее действие, созданная встреча)."""
    a = gcal_tools.get_action(action_id)
    if not a or int(a.get("owner_uid") or 0) != int(uid):
        raise LookupError("Действие не найдено")
    status = str(a.get("status") or "")
    created: dict[str, Any] | None = None
    if op == "undo":
        if status != "done":
            return a, None
        res = gcal_tools.undo_action(a)
        gcal_tools.set_action_status(action_id, "undone", result=res)
    elif op == "apply":
        if status != "proposed":
            return a, None
        if a.get("kind") == "slot":
            # Выбран вариант времени → встреча; остальные варианты этой подборки скрываем.
            res = gcal_tools.apply_slot(a, telegram_username=telegram_username)
            created = res.pop("_created", None)
            gcal_tools.set_action_status(action_id, "done", result=res)
            gcal_tools.dismiss_slot_siblings(a)
        else:
            res = gcal_tools.apply_delete(a)
            gcal_tools.set_action_status(action_id, "applied", result=res)
    elif op == "dismiss":
        if status == "proposed":
            gcal_tools.set_action_status(action_id, "dismissed")
    elif op in ("send", "send_confirmed", "cancel_send") and a.get("kind") == "email_draft":
        from assistant.services import mail_reply

        try:
            if op == "cancel_send":
                mail_reply.cancel_send(uid, action_id)
            else:
                mail_reply.request_send(uid, action_id, confirm_new=op == "send_confirmed")
        except mail_reply.NeedsConfirm as e:
            raise ActionNeedsConfirm(str(e), e.addrs) from e
        except mail_reply.ReplyError as e:
            raise ActionError(str(e)) from e
    else:
        raise ActionError("Неизвестная операция")
    return gcal_tools.get_action(action_id) or a, created
