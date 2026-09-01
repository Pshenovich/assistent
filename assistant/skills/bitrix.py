"""Bitrix24 MCP — свободные команды по задачам и CRM."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from assistant.integrations.bitrix_mcp_client import (
    call_tool_in_session,
    run_with_mcp_session,
)
from assistant.integrations.bitrix_mcp_portal import fix_bitrix_links, portal_host_from_token
from assistant.integrations.bitrix_mcp_token import get_user_token, is_connected, is_token_expired, remove_user_token
from assistant.lib.exception_format import format_exception_message
from assistant.lib.bitrix_task_format import (
    _parse_task_payload,
    format_candidates_markdown,
    format_task_from_tool_result,
    format_task_list_markdown,
)
from assistant.lib.telegram_html import uses_html_markup
from assistant.lib.telegram_markdown import prepare_bitrix_markdown
from assistant.lib.telegram_message import reply_formatted
from assistant.lib.telegram_status import post_status, take_work_status
from assistant.lib.webapp_public import webapp_entry_url
from assistant.nlu.regex import parse_bitrix_intent
from assistant.services import bitrix_mcp_agent
from assistant.services.bitrix_task_create import (
    create_task_direct,
    is_create_task_request,
    parse_create_task_request,
)
from assistant.services.bitrix_mcp_search import (
    find_task_candidates,
    find_tasks_for_list_query,
    has_clear_winner,
    is_task_list_request,
)

BITRIX_HISTORY_KEY = "bitrix_chat_history"
BITRIX_HISTORY_MAX = 6


def _truncate_telegram(text: str, limit: int = 4000) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _looks_like_task_description_request(text: str) -> bool:
    s = _norm(text)
    if "задач" not in s:
        return False
    return bool(
        re.search(
            r"\b(описани|детал|подробн|что\s+в|расскажи|выведи|получи|получить|дай|покажи)\b",
            s,
        )
    )


def _bitrix_auth_help_message() -> str:
    url = webapp_entry_url()
    return (
        "Токен Bitrix24 MCP недействителен или истёк.\n\n"
        "1. В Битрикс24: Приложения → MCP-подключения → «Получить токен подключения»\n"
        "2. В мини-приложении: Профиль → Интеграции → Битрикс24 → вставьте новый токен\n\n"
        f"Мини-приложение: {url}"
    )


def _is_bitrix_unauthorized_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "http 401" in text or "недействителен или истёк" in text


async def _reply_bitrix_auth_error(msg, *, telegram_user_id: int) -> None:
    remove_user_token(telegram_user_id)
    await msg.reply_text(_bitrix_auth_help_message(), disable_web_page_preview=True)


def bitrix_likely_followup(text: str) -> bool:
    s = _norm(text)
    if not s:
        return False
    patterns = (
        r"\b(эту|этой|этого|эта)\s+задач",
        r"\bполучи(ть)?\s+описани",
        r"\bпокажи\s+описани",
        r"\bчто\s+там\s+в\s+задач",
        r"\bописани[ея]\s+эт",
        r"\bстатус\s+эт",
        r"\bдедлайн\s+эт",
    )
    return any(re.search(p, s) for p in patterns)


def get_chat_history(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, str]]:
    raw = context.user_data.get(BITRIX_HISTORY_KEY)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out[-BITRIX_HISTORY_MAX:]


def append_chat_history(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_text: str,
    assistant_text: str,
) -> None:
    hist = get_chat_history(context)
    hist.append({"role": "user", "content": user_text.strip()})
    hist.append({"role": "assistant", "content": assistant_text.strip()})
    context.user_data[BITRIX_HISTORY_KEY] = hist[-BITRIX_HISTORY_MAX:]


async def _enrich_task(
    token: str,
    item: dict[str, Any],
    *,
    session: Any,
) -> dict[str, Any] | None:
    tid = int(item.get("taskId") or 0)
    if not tid:
        return None
    raw = await call_tool_in_session(session, "get_task_by_id", {"taskId": tid})
    task = _parse_task_payload(raw)
    if not task:
        return None
    gid = item.get("groupId")
    stage_id = item.get("stageId") or 0
    if gid and stage_id:
        from assistant.integrations.bitrix24_rest import stage_title

        task["stageTitle"] = stage_title(int(gid), int(stage_id))
    return task


async def _direct_task_list_body(session: Any, token: str, text: str) -> str | None:
    candidates = await find_tasks_for_list_query(token, text, session=session)
    if not candidates:
        return None
    portal = portal_host_from_token(token)
    tasks: list[dict[str, Any]] = []
    for item in candidates[:15]:
        enriched = await _enrich_task(token, item, session=session)
        if enriched:
            tasks.append(enriched)
    if not tasks:
        return None
    count_word = "задача" if len(tasks) == 1 else "задачи"
    heading = f"Нашёл {len(tasks)} {count_word}"
    return format_task_list_markdown(tasks, portal_host=portal, heading=heading)


async def _try_direct_task_list_answer(telegram_user_id: int, text: str) -> str | None:
    token = get_user_token(telegram_user_id)
    if not token:
        return None
    return await run_with_mcp_session(token, lambda session: _direct_task_list_body(session, token, text))


async def _direct_task_answer_body(session: Any, token: str, text: str) -> str | None:
    candidates = await find_task_candidates(token, text, session=session)
    if not candidates:
        return None
    portal = portal_host_from_token(token)
    if not has_clear_winner(candidates, text):
        if _looks_like_task_description_request(text) and len(candidates) > 1:
            return format_candidates_markdown(candidates, portal_host=portal)
        return None
    best = candidates[0]
    raw = await call_tool_in_session(
        session,
        "get_task_by_id",
        {"taskId": int(best["taskId"])},
    )
    return format_task_from_tool_result(raw, token=token)


async def _try_direct_task_answer(telegram_user_id: int, text: str) -> str | None:
    token = get_user_token(telegram_user_id)
    if not token:
        return None
    return await run_with_mcp_session(token, lambda session: _direct_task_answer_body(session, token, text))


async def try_continue_bitrix(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    msg = update.message
    if not msg:
        return False
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return False
    user = update.effective_user
    if not user or not is_connected(int(user.id)):
        return False
    if not get_chat_history(context):
        return False
    if parse_bitrix_intent(text) or bitrix_likely_followup(text):
        await handle(update, context, text)
        return True
    return False


async def _reply_bitrix(msg, body: str, *, token: str) -> str:
    portal = portal_host_from_token(token)
    fixed = fix_bitrix_links(body, portal_host=portal)
    truncated = _truncate_telegram(fixed)
    use_html = uses_html_markup(truncated)
    if use_html:
        from assistant.lib.telegram_html import prepare_rich_html

        rendered = prepare_rich_html(truncated)
        await reply_formatted(
            msg,
            rendered,
            rich_markdown=False,
            disable_web_page_preview=False,
        )
    else:
        rendered = prepare_bitrix_markdown(truncated)
        await reply_formatted(
            msg,
            rendered,
            rich_markdown=True,
            disable_web_page_preview=False,
        )
    return rendered


async def handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return

    uid = int(user.id)
    if not is_connected(uid):
        url = webapp_entry_url()
        await msg.reply_text(
            "Подключите Битрикс24 в мини-приложении:\n"
            "Профиль → Интеграции → Битрикс24.\n\n"
            f"Мини-приложение: {url}",
            disable_web_page_preview=True,
        )
        return

    question = (text or "").strip()
    if not question:
        await msg.reply_text(
            "Напишите, что сделать в Битрикс24: создать или найти задачу, "
            "изменить дедлайн, работать с чек-листом, сделкой, воронкой и т.д."
        )
        return

    token = get_user_token(uid) or ""
    if is_token_expired(token):
        await _reply_bitrix_auth_error(msg, telegram_user_id=uid)
        return

    history = get_chat_history(context)
    status = await post_status(msg, "Работаю с Битрикс24…")
    try:
        if is_create_task_request(question):
            req = parse_create_task_request(question)
            if req:
                direct_create = await create_task_direct(token, req)
                rendered = await _reply_bitrix(msg, direct_create, token=token)
                append_chat_history(context, user_text=question, assistant_text=rendered)
                return

        if is_task_list_request(question):
            direct_list = await _try_direct_task_list_answer(uid, question)
            if direct_list:
                rendered = await _reply_bitrix(msg, direct_list, token=token)
                append_chat_history(context, user_text=question, assistant_text=rendered)
                return

        if _looks_like_task_description_request(question):
            direct = await _try_direct_task_answer(uid, question)
            if direct:
                rendered = await _reply_bitrix(msg, direct, token=token)
                append_chat_history(context, user_text=question, assistant_text=rendered)
                return

        answer = await asyncio.to_thread(
            bitrix_mcp_agent.run,
            uid,
            question,
            history=history,
        )
        rendered = await _reply_bitrix(msg, answer, token=token)
        append_chat_history(context, user_text=question, assistant_text=rendered)
    except BaseException as e:
        if _is_bitrix_unauthorized_error(e):
            await _reply_bitrix_auth_error(msg, telegram_user_id=uid)
        else:
            await msg.reply_text(f"Битрикс24: {format_exception_message(e)}")
    finally:
        await take_work_status(status)
