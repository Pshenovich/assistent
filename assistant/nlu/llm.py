"""Вызовы OpenRouter для парсинга и ответов."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from assistant.config import CALENDAR_TZ
from assistant.integrations.openrouter_client import openrouter_chat_completion
from assistant.lib.calendar_datetime_parse import (
    calendar_build_llm_user_payload,
    calendar_normalize_parsed,
)
from assistant.lib.llm_json import strip_json_from_markdown
from assistant.lib.summary_enrich import inject_tasks_into_summary, merge_tasks
from assistant.nlu.prompts import (
    ACTION_ITEMS_SYSTEM,
    ASK_SYSTEM,
    CALENDAR_PARSE_SYSTEM,
    JOURNAL_QA_ANSWER_SYSTEM,
    JOURNAL_QA_PARSE_SYSTEM,
    KB_QA_ANSWER_SYSTEM,
    KB_QA_PARSE_SYSTEM,
    NOTE_FORMAT_SYSTEM,
    REMINDER_PARSE_SYSTEM,
    SUMMARY_SEARCH_PARSE_SYSTEM,
    SUMMARY_SYSTEM,
    TELEMOST_PARSE_SYSTEM,
    ZOOM_PARSE_SYSTEM,
)


def _model() -> str:
    return os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()


def _model_ask() -> str:
    return (
        os.getenv("OPENROUTER_MODEL_ASK", "").strip()
        or os.getenv("OPENROUTER_MODEL_CHAT", "").strip()
        or _model()
    )


def _model_summary() -> str:
    return (
        os.getenv("OPENROUTER_MODEL_SUMMARY", "").strip()
        or os.getenv("OPENROUTER_MODEL_ASK", "").strip()
        or os.getenv("OPENROUTER_MODEL_CHAT", "").strip()
        or "openai/gpt-4o"
    )


def summary_model_name() -> str:
    return _model_summary()


def _user_content(text: str, images: list[dict[str, str]] | None) -> Any:
    if not images:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text or "Опиши вложение."}]
    for img in images:
        mime = str((img or {}).get("mime") or "image/jpeg").split(";")[0].strip() or "image/jpeg"
        b64 = str((img or {}).get("b64") or "").strip()
        if not b64:
            continue
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    return parts if len(parts) > 1 else text


def _chat(
    system: str,
    user: str,
    *,
    operation: str,
    model: str | None = None,
    timeout: float = 120,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    history: list[dict[str, str]] | None = None,
    context_prefix: str | None = None,
    images: list[dict[str, str]] | None = None,
) -> str:
    return _chat_result(
        system,
        user,
        operation=operation,
        model=model,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
        history=history,
        context_prefix=context_prefix,
        images=images,
    )[0]


def _chat_result(
    system: str,
    user: str,
    *,
    operation: str,
    model: str | None = None,
    timeout: float = 120,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    history: list[dict[str, str]] | None = None,
    context_prefix: str | None = None,
    images: list[dict[str, str]] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    from assistant.integrations.openrouter_client import extract_chat_message_media

    system_text = system
    extra = (context_prefix or "").strip()
    if extra:
        system_text = f"{system}\n\n{extra}"
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
    for it in history or []:
        role = str((it or {}).get("role") or "").strip().lower()
        content = str((it or {}).get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        messages.append({"role": role, "content": content[:24000]})
    messages.append({"role": "user", "content": _user_content(user, images)})
    payload: dict[str, Any] = {
        "model": model or _model(),
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    data = openrouter_chat_completion(payload, operation=operation, timeout=timeout)
    text, out_images = extract_chat_message_media(data)
    if not text and not out_images:
        text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    return text, out_images


def parse_calendar(
    text: str,
    *,
    now: datetime | None = None,
    requested_intent: str | None = None,
    user_id: int | None = None,
    reply_context: str = "",
    reply_author: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from assistant.lib.user_timezone import resolve_user_tz_name
    from assistant.stores import user_prefs

    if user_id:
        tz_name = resolve_user_tz_name(int(user_id))
        now = now or datetime.now(user_prefs.get_user_tz(int(user_id)))
    else:
        from zoneinfo import ZoneInfo

        tz_name = CALENDAR_TZ
        now = now or datetime.now(ZoneInfo(CALENDAR_TZ))
    user_obj = calendar_build_llm_user_payload(
        user_text=text,
        today_iso=now.date().isoformat(),
        timezone=tz_name,
        requested_intent=requested_intent,
        reply_context=reply_context,
        reply_author=reply_author,
    )
    raw = _chat(
        CALENDAR_PARSE_SYSTEM,
        json.dumps(user_obj, ensure_ascii=False),
        operation="parse_calendar",
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    calendar_normalize_parsed(obj, text, today_iso=now.date().isoformat(), tz_name=tz_name)
    if requested_intent and obj.get("intent") not in ("none", ""):
        obj["intent"] = requested_intent
    return obj


def parse_zoom(
    text: str,
    *,
    now: datetime | None = None,
    user_id: int | None = None,
    requested_action: str | None = None,
) -> dict[str, Any] | None:
    from assistant.lib.user_timezone import resolve_user_tz_name
    from assistant.stores import user_prefs

    if user_id:
        tz_name = resolve_user_tz_name(int(user_id))
        now = now or datetime.now(user_prefs.get_user_tz(int(user_id)))
    else:
        from zoneinfo import ZoneInfo

        tz_name = CALENDAR_TZ
        now = now or datetime.now(ZoneInfo(CALENDAR_TZ))
    user_obj = {
        "text": text,
        "today_iso": now.date().isoformat(),
        "timezone": tz_name,
        "requested_action": requested_action,
    }
    raw = _chat(
        ZOOM_PARSE_SYSTEM,
        json.dumps(user_obj, ensure_ascii=False),
        operation="parse_zoom",
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    from assistant.lib.calendar_datetime_parse import calendar_normalize_parsed

    raw_action = str(obj.get("action") or requested_action or "instant").strip().lower()
    intent_map = {
        "instant": "instant",
        "update": "update_event",
        "delete": "delete_event",
    }
    norm = {
        "intent": intent_map.get(raw_action, raw_action),
        "start": obj.get("start"),
        "end": obj.get("end"),
        "duration_min": obj.get("duration_min") or 60,
        "match_query": obj.get("match_query"),
        "match_date": obj.get("match_date"),
        "need_more_info": obj.get("need_more_info"),
        "questions": obj.get("questions"),
    }
    calendar_normalize_parsed(
        norm,
        text,
        today_iso=now.date().isoformat(),
        tz_name=tz_name,
    )
    if requested_action:
        norm["intent"] = intent_map.get(requested_action, requested_action)
    action_out = {
        "update_event": "update",
        "delete_event": "delete",
    }.get(str(norm.get("intent") or ""), str(norm.get("intent") or "instant"))
    norm["action"] = action_out
    return norm


def parse_telemost(
    text: str,
    *,
    now: datetime | None = None,
    user_id: int | None = None,
    requested_action: str | None = None,
) -> dict[str, Any] | None:
    from assistant.lib.user_timezone import resolve_user_tz_name
    from assistant.stores import user_prefs

    if user_id:
        tz_name = resolve_user_tz_name(int(user_id))
        now = now or datetime.now(user_prefs.get_user_tz(int(user_id)))
    else:
        from zoneinfo import ZoneInfo

        tz_name = CALENDAR_TZ
        now = now or datetime.now(ZoneInfo(CALENDAR_TZ))
    user_obj = {
        "text": text,
        "today_iso": now.date().isoformat(),
        "timezone": tz_name,
        "requested_action": requested_action,
    }
    raw = _chat(
        TELEMOST_PARSE_SYSTEM,
        json.dumps(user_obj, ensure_ascii=False),
        operation="parse_telemost",
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    from assistant.lib.calendar_datetime_parse import calendar_normalize_parsed

    raw_action = str(obj.get("action") or requested_action or "instant").strip().lower()
    intent_map = {
        "instant": "instant",
        "update": "update_event",
        "delete": "delete_event",
    }
    norm = {
        "intent": intent_map.get(raw_action, raw_action),
        "start": obj.get("start"),
        "end": obj.get("end"),
        "duration_min": obj.get("duration_min") or 60,
        "match_query": obj.get("match_query"),
        "match_date": obj.get("match_date"),
        "need_more_info": obj.get("need_more_info"),
        "questions": obj.get("questions"),
    }
    calendar_normalize_parsed(
        norm,
        text,
        today_iso=now.date().isoformat(),
        tz_name=tz_name,
    )
    if requested_action:
        norm["intent"] = intent_map.get(requested_action, requested_action)
    action_out = {
        "update_event": "update",
        "delete_event": "delete",
    }.get(str(norm.get("intent") or ""), str(norm.get("intent") or "instant"))
    norm["action"] = action_out
    return norm


def parse_reminder(
    text: str, *, now: datetime, reply_context: str = ""
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "now_iso": now.isoformat(),
        "tz": CALENDAR_TZ,
        "text": text,
    }
    rc = (reply_context or "").strip()
    if rc:
        payload["reply_context"] = rc
    user = json.dumps(payload, ensure_ascii=False)
    raw = _chat(REMINDER_PARSE_SYSTEM, user, operation="parse_reminder")
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def format_note(text: str) -> dict[str, str] | None:
    """Только title; тело заметки вызывающий код берёт из исходного текста автора."""
    raw = _chat(NOTE_FORMAT_SYSTEM, text, operation="format_note")
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    title = str(obj.get("title") or "").strip()
    if not title:
        return None
    return {"title": title}


def answer_with_context(
    question: str,
    context: str = "",
    model: str | None = None,
    history: list[dict[str, str]] | None = None,
    *,
    note_title: str = "",
    note_text: str = "",
    quote: str = "",
    knowledge_brief: str = "",
    images: list[dict[str, str]] | None = None,
    file_notes: str = "",
) -> str:
    return answer_with_context_result(
        question,
        context,
        model=model,
        history=history,
        note_title=note_title,
        note_text=note_text,
        quote=quote,
        knowledge_brief=knowledge_brief,
        images=images,
        file_notes=file_notes,
    )["answer"]


def answer_with_context_result(
    question: str,
    context: str = "",
    model: str | None = None,
    history: list[dict[str, str]] | None = None,
    *,
    note_title: str = "",
    note_text: str = "",
    quote: str = "",
    knowledge_brief: str = "",
    images: list[dict[str, str]] | None = None,
    file_notes: str = "",
) -> dict[str, Any]:
    from assistant.nlu.ask_context import assemble_ask_messages

    extra, user = assemble_ask_messages(
        question=question,
        context=context,
        note_title=note_title,
        note_text=note_text,
        quote=quote,
        knowledge_brief=knowledge_brief,
    )
    notes = (file_notes or "").strip()
    if notes:
        user = (user or "").rstrip() + "\n\n" + notes
    if images and not (question or "").strip() and not (user or "").strip():
        user = "Опиши вложение и ответь по нему."
    if images:
        text, out_images = _chat_result(
            ASK_SYSTEM,
            user,
            operation="ask",
            model=model or _model_ask(),
            history=history,
            context_prefix=extra or None,
            images=images,
        )
    else:
        text = _chat(
            ASK_SYSTEM,
            user,
            operation="ask",
            model=model or _model_ask(),
            history=history,
            context_prefix=extra or None,
        )
        out_images = []
    return {"answer": text, "images": out_images}


def parse_journal_qa_query(
    text: str,
    *,
    today_iso: str,
    tz_name: str,
) -> dict[str, Any] | None:
    q = (text or "").strip()
    if not q:
        return None
    payload = {
        "text": q,
        "today_iso": today_iso,
        "timezone": tz_name,
    }
    raw = _chat(
        JOURNAL_QA_PARSE_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        operation="parse_journal_qa",
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    focus = str(obj.get("focus") or "general").strip().lower()
    valid_focus = {
        "decisions",
        "tasks",
        "complaints",
        "participants",
        "summary",
        "general",
    }
    if focus not in valid_focus:
        focus = "general"
    relative = str(obj.get("relative") or "").strip().lower()
    if relative in ("null", "none", ""):
        relative = None
    sources = obj.get("sources")
    if not isinstance(sources, list) or not sources:
        sources = ["notes", "transcripts", "summaries"]
    search_query = str(obj.get("search_query") or obj.get("query") or "").strip()
    date_from = str(obj.get("date_from") or "").strip()[:10] or None
    date_to = str(obj.get("date_to") or "").strip()[:10] or None
    if relative == "yesterday" and not date_from:
        from datetime import date, timedelta

        try:
            today = date.fromisoformat(today_iso[:10])
            yesterday = today - timedelta(days=1)
            date_from = yesterday.isoformat()
            date_to = yesterday.isoformat()
            relative = None
        except ValueError:
            pass
    return {
        "search_query": search_query,
        "focus": focus,
        "assignee": str(obj.get("assignee") or "").strip(),
        "date_from": date_from,
        "date_to": date_to,
        "relative": relative,
        "sources": [str(s).strip().lower() for s in sources],
    }


def answer_from_journal_context(question: str, context: str) -> str:
    user = f"Контекст из архива:\n{context[:12000]}\n\nВопрос пользователя:\n{question}"
    return _chat(
        JOURNAL_QA_ANSWER_SYSTEM,
        user,
        operation="journal_qa",
        model=_model_ask(),
    )


def parse_kb_qa_query(
    text: str,
    *,
    knowledge_bases: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    q = (text or "").strip()
    if not q:
        return None
    payload = {
        "text": q,
        "knowledge_bases": knowledge_bases or [],
    }
    raw = _chat(
        KB_QA_PARSE_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        operation="parse_kb_qa",
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return {
        "search_query": str(obj.get("search_query") or obj.get("query") or "").strip(),
        "kb_id": str(obj.get("kb_id") or "").strip(),
        "kb_name": str(obj.get("kb_name") or "").strip(),
    }


def answer_from_kb_context(question: str, context: str) -> str:
    user = (
        f"Контекст из базы знаний:\n{context[:12000]}\n\n"
        f"Вопрос пользователя:\n{question}"
    )
    return _chat(
        KB_QA_ANSWER_SYSTEM,
        user,
        operation="kb_qa",
        model=_model_ask(),
    )


_SUMMARY_TRANSCRIPT_MAX = 60000


def _parse_tasks_json(raw: str) -> list[dict[str, str]]:
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    tasks = obj.get("tasks")
    if not isinstance(tasks, list):
        return []
    out: list[dict[str, str]] = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        out.append(
            {
                "assignee": str(item.get("assignee") or "").strip(),
                "task": task,
                "deadline": str(item.get("deadline") or "").strip(),
                "completed": bool(item.get("completed")),
            }
        )
    return out


def extract_action_items(transcript: str) -> list[dict[str, str]]:
    text = (transcript or "").strip()
    if not text:
        return []
    payload = {"transcript": text[:_SUMMARY_TRANSCRIPT_MAX]}
    raw = _chat(
        ACTION_ITEMS_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        operation="summarize_tasks_llm",
        model=_model_summary(),
        timeout=300,
        temperature=0.0,
        max_tokens=4096,
    )
    return _parse_tasks_json(raw)


def summarize_recording(
    transcript: str, *, speakers_detected: bool = False
) -> dict[str, Any] | None:
    from concurrent.futures import ThreadPoolExecutor

    text = (transcript or "").strip()
    if not text:
        return None
    payload = {
        "transcript": text[:_SUMMARY_TRANSCRIPT_MAX],
        "speakers_detected": bool(speakers_detected),
    }
    user_payload = json.dumps(payload, ensure_ascii=False)
    model = _model_summary()
    with ThreadPoolExecutor(max_workers=2) as pool:
        summary_future = pool.submit(
            _chat,
            SUMMARY_SYSTEM,
            user_payload,
            operation="summarize_llm",
            model=model,
            timeout=300,
            max_tokens=8192,
        )
        tasks_future = pool.submit(extract_action_items, text)
        raw = summary_future.result()
        extracted_tasks = tasks_future.result()
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    summary = str(obj.get("summary") or "").strip()
    if not summary:
        return None
    content_type = str(obj.get("content_type") or "meeting").strip().lower()
    valid_types = {"meeting", "interview", "lecture", "brainstorm", "voice_note"}
    if content_type not in valid_types:
        content_type = "meeting"
    try:
        confidence = float(obj.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    summary_tasks = obj.get("tasks") if isinstance(obj.get("tasks"), list) else []
    merged_tasks = merge_tasks(summary_tasks, extracted_tasks)
    summary = inject_tasks_into_summary(summary, merged_tasks)
    return {
        "content_type": content_type,
        "confidence": confidence,
        "main_topic": str(obj.get("main_topic") or "").strip(),
        "summary": summary,
        "participants": obj.get("participants") if isinstance(obj.get("participants"), list) else [],
        "tasks": merged_tasks,
        "decisions": obj.get("decisions") if isinstance(obj.get("decisions"), list) else [],
        "deadlines": obj.get("deadlines") if isinstance(obj.get("deadlines"), list) else [],
        "topics": obj.get("topics") if isinstance(obj.get("topics"), list) else [],
        "open_questions": obj.get("open_questions") if isinstance(obj.get("open_questions"), list) else [],
        "risks": obj.get("risks") if isinstance(obj.get("risks"), list) else [],
    }


def parse_summary_search_query(text: str) -> dict[str, Any] | None:
    q = (text or "").strip()
    if not q:
        return None
    raw = _chat(
        SUMMARY_SEARCH_PARSE_SYSTEM,
        q,
        operation="parse_summary_search",
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    field = str(obj.get("field") or "summary").strip().lower()
    valid_fields = {"decisions", "tasks", "topics", "participants", "summary", "main_topic"}
    if field not in valid_fields:
        field = "summary"
    query = str(obj.get("query") or q).strip() or q
    assignee = str(obj.get("assignee") or "").strip()
    return {"field": field, "query": query, "assignee": assignee}
