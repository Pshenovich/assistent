"""Маршрутизация NLU → skills."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Literal

from telegram import Message, Update
from telegram.ext import ContextTypes

from assistant.integrations.openrouter_client import is_llm_configured
from assistant.lib.urls import extract_urls
from assistant.lib.calendar_intent_heuristics import calendar_likely_user_text
from assistant.lib.journal_retrieval import journal_qa_likely_user_text
from assistant.lib.knowledge_retrieval import knowledge_qa_likely_user_text
from assistant.nlu import regex as regex_route_mod
from assistant.nlu.intent_router import (
    IntentContext,
    Route,
    classify_intent,
    resolve_route,
    router_enabled,
)
from assistant.skills import bitrix as bitrix_skill
from assistant.skills import ask as ask_skill
from assistant.skills import calendar as calendar_skill
from assistant.skills import contacts as contacts_skill
from assistant.skills import journal_qa as journal_qa_skill
from assistant.skills import knowledge_qa as knowledge_qa_skill
from assistant.skills import notes as notes_skill
from assistant.skills import reminders as reminders_skill
from assistant.skills import summary as summary_skill
from assistant.skills import transcribe as transcribe_skill
from assistant.skills import telemost as telemost_skill
from assistant.skills import zoom as zoom_skill
from assistant.skills import zoom_record as zoom_record_skill

RouteSourceKind = Literal["private", "voice", "group"]


@dataclass
class RouteExtras:
    source: RouteSourceKind = "private"
    replied: Message | None = None
    urls: list[str] | None = None


async def build_resolved_route(
    text: str,
    *,
    chat_type: str,
    has_reply: bool,
    has_url: bool,
    has_attachment: bool,
) -> Route | None:
    regex = regex_route_mod.regex_route(text)
    llm = None
    # LLM только если regex не сработал (иначе +1–5 с на каждое сообщение).
    # INTENT_ROUTER_WITH_REGEX=1 — старое поведение: всегда сверять с LLM.
    with_regex_llm = os.getenv("INTENT_ROUTER_WITH_REGEX", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    use_llm = router_enabled() and is_llm_configured() and (regex is None or with_regex_llm)
    if not use_llm and regex is None and is_llm_configured() and calendar_likely_user_text(text):
        use_llm = True
    if use_llm:
        hint = None
        if regex is not None:
            hint = {"skill": regex.skill, "sub_intent": regex.sub_intent}
        ctx = IntentContext(
            text=text,
            chat_type=chat_type,
            has_reply=has_reply,
            has_url=has_url,
            has_attachment=has_attachment,
            regex_hint=hint,
        )
        llm = await asyncio.to_thread(
            classify_intent, ctx, force=not router_enabled()
        )
    return resolve_route(regex, llm)


async def dispatch_route(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    route: Route,
    extras: RouteExtras,
) -> bool:
    msg = update.message
    if msg is None:
        return False
    text = (route.body or "").strip()
    full = (msg.text or msg.caption or route.body or "").strip()
    uid = update.effective_user.id if update.effective_user else 0

    if route.skill == "reminder":
        await reminders_skill.handle(update, context, text or full)
        return True
    if route.skill == "note_search":
        await notes_skill.handle_search(update, context, text or full)
        return True
    if route.skill == "transcribe_search":
        await transcribe_skill.handle_search(update, context, text or full)
        return True
    if route.skill == "summary_search":
        await summary_skill.handle_search(update, context, text or full)
        return True
    if route.skill == "summary_latest":
        await summary_skill.handle_latest(update, context)
        return True
    if route.skill == "todoist_note":
        await notes_skill.handle(update, context, text or full)
        return True
    if route.skill == "zoom_record":
        await zoom_record_skill.handle(update, context, full)
        return True
    if route.skill == "calendar":
        if route.calendar_kind == "zoom" or route.sub_intent.startswith("zoom"):
            sub = route.sub_intent
            if sub in ("zoom_update", "update"):
                await zoom_skill.handle_update(update, context, full)
            elif sub in ("zoom_delete", "delete"):
                await zoom_skill.handle_delete(update, context, full)
            else:
                await zoom_skill.handle_instant(update, context, full)
            return True
        if route.calendar_kind == "telemost" or route.sub_intent.startswith("telemost"):
            sub = route.sub_intent
            if sub in ("telemost_update", "update"):
                await telemost_skill.handle_update(update, context, full)
            elif sub in ("telemost_delete", "delete"):
                await telemost_skill.handle_delete(update, context, full)
            else:
                await telemost_skill.handle_instant(update, context, full)
            return True
        if route.sub_intent == "contacts":
            await contacts_skill.handle_add_text(update, context, full)
            return True
        from assistant.lib.message_context import reply_author_info, reply_context_text

        await calendar_skill.handle(
            update,
            context,
            full,
            kind=route.calendar_kind or route.sub_intent,
            reply_context=reply_context_text(msg),
            reply_author=reply_author_info(msg),
        )
        return True
    if route.skill == "summary":
        from assistant.nlu.regex import parse_summary_intent

        urls = list(extras.urls or extract_urls(full))
        rep = extras.replied
        if rep and not urls:
            urls = extract_urls((rep.text or rep.caption or ""))

        has_media = bool(
            msg.document or msg.audio or msg.video or msg.voice
        )
        rep_has_media = bool(
            rep
            and (
                rep.voice
                or rep.audio
                or rep.video
                or rep.document
                or rep.video_note
            )
        )

        if urls:
            summary_skill.mark_summary_handled(context)
            await summary_skill.handle_url(update, context, urls[0], user_id=uid)
        elif has_media and parse_summary_intent(full):
            summary_skill.mark_summary_handled(context)
            await summary_skill.run_explicit_summary(
                update, context, source_message=msg
            )
        elif rep and rep_has_media:
            summary_skill.mark_summary_handled(context)
            await summary_skill.handle_reply(update, context)
        else:
            await msg.reply_text(
                "Пришлите файл, голосовое или ссылку для саммари."
            )
        return True
    if route.skill == "transcribe":
        from assistant.nlu.regex import parse_transcribe_intent

        urls = list(extras.urls or extract_urls(full))
        rep = extras.replied
        if rep and not urls:
            urls = extract_urls((rep.text or rep.caption or ""))

        has_media = bool(
            msg.document or msg.audio or msg.video or msg.voice
        )
        rep_has_media = bool(
            rep
            and (
                rep.voice
                or rep.audio
                or rep.video
                or rep.document
                or rep.video_note
            )
        )

        if urls:
            transcribe_skill.mark_transcribe_handled(context)
            await transcribe_skill.handle_url(update, context, urls[0], user_id=uid)
        elif has_media and parse_transcribe_intent(full):
            transcribe_skill.mark_transcribe_handled(context)
            await transcribe_skill.run_explicit_transcribe(
                update, context, source_message=msg
            )
        elif rep and rep_has_media:
            transcribe_skill.mark_transcribe_handled(context)
            await transcribe_skill.handle_reply(update, context)
        else:
            await msg.reply_text(
                "Пришлите файл, голосовое или ссылку для транскрибации."
            )
        return True
    if route.skill == "journal_qa":
        await journal_qa_skill.handle(update, context, text or full)
        return True
    if route.skill == "knowledge_qa":
        await knowledge_qa_skill.handle(update, context, text or full)
        return True
    if route.skill == "bitrix":
        await bitrix_skill.handle(update, context, text or full)
        return True
    if route.skill == "ask":
        await ask_skill.handle(update, context, text or full, replied=extras.replied)
        return True
    return False


async def route_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    extras: RouteExtras,
    *,
    chat_type: str = "private",
) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    msg = update.message
    replied = extras.replied
    urls = extras.urls or extract_urls(cleaned)
    has_attachment = bool(
        msg and (msg.document or msg.audio or msg.video or msg.voice)
    )
    route = await build_resolved_route(
        cleaned,
        chat_type=chat_type,
        has_reply=replied is not None,
        has_url=bool(urls),
        has_attachment=has_attachment,
    )
    uid = int(update.effective_user.id) if update.effective_user else 0
    if route is None:
        if replied and cleaned:
            await ask_skill.handle(update, context, cleaned, replied=replied)
            return True
        if knowledge_qa_likely_user_text(cleaned):
            from assistant.stores import knowledge_base_store as kb_store

            if uid and kb_store.user_has_accessible_kbs(uid):
                await knowledge_qa_skill.handle(update, context, cleaned)
                return True
        if journal_qa_likely_user_text(cleaned):
            await journal_qa_skill.handle(update, context, cleaned)
            return True
        return False
    return await dispatch_route(update, context, route, extras)
