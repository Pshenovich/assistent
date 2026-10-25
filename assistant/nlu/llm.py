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
    ASK_ATTACHMENTS,
    ASK_SYSTEM,
    CALENDAR_PARSE_SYSTEM,
    CHAT_DIGEST_SYSTEM,
    JOURNAL_QA_ANSWER_SYSTEM,
    JOURNAL_QA_PARSE_SYSTEM,
    KB_QA_ANSWER_SYSTEM,
    KB_QA_PARSE_SYSTEM,
    NOTE_FORMAT_SYSTEM,
    REMINDER_PARSE_SYSTEM,
    SUMMARY_SEARCH_PARSE_SYSTEM,
    SUMMARY_SYSTEM,
    TELEMOST_PARSE_SYSTEM,
    VOICE_COMMAND_SYSTEM,
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
        # gpt-4o на eval (12 встреч) давал нестабильное число задач от запуска к запуску
        or "openai/gpt-5.5"
    )


def _model_chat_digest() -> str:
    return (
        os.getenv("OPENROUTER_MODEL_CHAT_DIGEST", "").strip()
        or os.getenv("OPENROUTER_MODEL_BOARD", "").strip()
        or "google/gemini-2.5-flash"
    )


def chat_digest_model_name() -> str:
    return _model_chat_digest()


def summary_model_name() -> str:
    return _model_summary()


def _user_content(
    text: str,
    images: list[dict[str, str]] | None,
    files: list[dict[str, str]] | None = None,
) -> Any:
    if not images and not files:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text or "Опиши вложение."}]
    for img in images or []:
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
    for item in files or []:
        mime = str((item or {}).get("mime") or "application/pdf").split(";")[0].strip()
        mime = mime or "application/pdf"
        b64 = str((item or {}).get("b64") or "").strip()
        name = str((item or {}).get("filename") or "document.pdf").strip() or "document.pdf"
        if not b64:
            continue
        parts.append(
            {
                "type": "file",
                "file": {
                    "filename": name,
                    "file_data": f"data:{mime};base64,{b64}",
                },
            }
        )
    return parts if len(parts) > 1 else text


def _system_with_attachments(system: str) -> str:
    base = (system or "").strip() or ASK_SYSTEM
    if ":::file" in base and ":::image" in base:
        return base
    return f"{base}\n\n{ASK_ATTACHMENTS}"


def _materialize_generated_media(
    text: str,
    images: list[dict[str, str]] | None,
    *,
    question: str = "",
    history: list[dict[str, str]] | None = None,
    source_images: list[dict[str, str]] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    from assistant.integrations.openrouter_client import openrouter_generate_images
    from assistant.stores.comment_files import (
        claims_attachment_ready,
        extract_embedded_images,
        is_bare_confirm,
        looks_like_attachment_refusal,
        parse_generated_image_prompts,
        suggested_image_filename,
        visual_image_prompt,
        wants_image_delivery,
        wants_resend_attachment,
    )

    cleaned, prompts = parse_generated_image_prompts(text or "")
    cleaned, embedded = extract_embedded_images(cleaned)
    model_images = list(images or [])
    prompt_src = (question or "").strip()
    if is_bare_confirm(prompt_src) or wants_resend_attachment(prompt_src):
        for it in reversed(history or []):
            content = str((it or {}).get("content") or "").strip()
            role = str((it or {}).get("role") or "").strip().lower()
            if role == "user" and content and not is_bare_confirm(content):
                if wants_resend_attachment(content):
                    continue
                prompt_src = content
                break
    # Intent only from the user turn — model answer must not invent image delivery.
    user_wants = wants_image_delivery(
        prompt_src or question,
        has_source=bool(source_images),
        answer="",
    )
    if not user_wants:
        # Без явного запроса картинки — выкидываем и :::image/base64, и «нативные»
        # model images (иначе GPT снова прикрепляет png без просьбы).
        return cleaned, []

    out_images = model_images + embedded
    need = wants_image_delivery(
        prompt_src or question,
        has_source=bool(source_images),
        answer=text,
    ) or claims_attachment_ready(text) or claims_attachment_ready(cleaned)
    if need and not prompts and not out_images:
        prompts.append(
            visual_image_prompt(
                prompt_src or question,
                history=history,
                has_source=bool(source_images),
            )
        )
    for prompt in prompts[:2]:
        if out_images:
            break
        try:
            generated = openrouter_generate_images(
                prompt,
                source_images=source_images,
                timeout=180,
            )
        except Exception as e:
            print(f"[llm] image_generate_failed prompt={prompt[:80]!r} err={e!r}")
            generated = []
        if not generated:
            # Один повтор с явным visual prompt — модель иногда отвечает текстом.
            retry = visual_image_prompt(
                prompt,
                history=history,
                has_source=bool(source_images),
            )
            if retry != prompt:
                try:
                    generated = openrouter_generate_images(
                        retry,
                        source_images=source_images,
                        timeout=180,
                    )
                except Exception as e:
                    print(f"[llm] image_generate_retry_failed prompt={retry[:80]!r} err={e!r}")
                    generated = []
        out_images.extend(generated)
        if len(out_images) >= 4:
            break
    out_images = out_images[:4]
    fname = (
        suggested_image_filename(prompt_src or question or "")
        or suggested_image_filename(text or "")
        or suggested_image_filename(cleaned)
    )
    if fname and out_images and isinstance(out_images[0], dict) and not out_images[0].get("filename"):
        out_images[0] = {**out_images[0], "filename": fname}
    if out_images:
        if looks_like_attachment_refusal(cleaned) or claims_attachment_ready(cleaned) or not cleaned.strip():
            cleaned = "Готово — файл во вложении."
    elif need or claims_attachment_ready(text) or claims_attachment_ready(cleaned):
        cleaned = (
            "Не удалось сгенерировать файл. Напишите ещё раз «нарисуй…» "
            "или приложите фото и попросите апскейл."
        )
    return cleaned, out_images


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
    files: list[dict[str, str]] | None = None,
    web: bool = False,
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
        files=files,
        web=web,
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
    files: list[dict[str, str]] | None = None,
    web: bool = False,
    tools: list[dict[str, Any]] | None = None,
    tool_runner: Any = None,
    max_tool_steps: int = 6,
    tool_choice_first: str = "auto",
) -> tuple[str, list[dict[str, str]], str]:
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
    messages.append({"role": "user", "content": _user_content(user, images, files)})
    from assistant.integrations.openrouter_client import model_emits_images

    run_model = model or _model()
    if files or web or model_emits_images(run_model):
        timeout = max(float(timeout), 180.0)
    payload: dict[str, Any] = {
        "model": run_model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    from assistant.board.llm import reasoning_request_fields

    reason = reasoning_request_fields(run_model, payload.get("max_tokens"))
    if reason:
        payload.pop("temperature", None)
        payload.update(reason)
        timeout = max(float(timeout), 180.0)
    if model_emits_images(run_model):
        payload["modalities"] = ["image", "text"]
    plugins: list[dict[str, Any]] = []
    if files:
        plugin: dict[str, Any] = {"id": "file-parser"}
        engine = os.getenv("OPENROUTER_PDF_ENGINE", "").strip()
        if engine:
            plugin["pdf"] = {"engine": engine}
        plugins.append(plugin)
    if web:
        plugins.append({"id": "web"})
    if plugins:
        payload["plugins"] = plugins
    if tools and tool_runner is not None:
        data = _run_tool_loop(
            payload, tools, tool_runner, operation=operation, timeout=timeout,
            max_steps=max_tool_steps, tool_choice_first=tool_choice_first,
        )
    else:
        data = openrouter_chat_completion(payload, operation=operation, timeout=timeout)
    text, out_images = extract_chat_message_media(data)
    if not text and not out_images:
        text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    from assistant.integrations.openrouter_client import sanitize_openrouter_model_id

    used_model = (
        sanitize_openrouter_model_id(str((data or {}).get("model") or "")) or run_model
    )
    return text, out_images, used_model


def _run_tool_loop(
    payload: dict[str, Any],
    tools: list[dict[str, Any]],
    tool_runner: Any,
    *,
    operation: str,
    timeout: float,
    max_steps: int = 6,
    tool_choice_first: str = "auto",
) -> dict[str, Any]:
    """Цикл function calling: модель зовёт инструменты, мы исполняем и возвращаем результат,
    пока не придёт обычный ответ. Последний шаг — без инструментов, чтобы ответ был всегда."""
    import json as _json

    messages = list(payload.get("messages") or [])
    data: dict[str, Any] = {}
    for step in range(max(1, int(max_steps)) + 1):
        req = dict(payload)
        req["messages"] = messages
        if step < max_steps:
            req["tools"] = tools
            # Вопрос про календарь — первый шаг обязан посмотреть данные, а не отвечать наугад.
            req["tool_choice"] = tool_choice_first if step == 0 else "auto"
        try:
            data = openrouter_chat_completion(req, operation=operation, timeout=timeout)
        except Exception as e:
            # Модель без поддержки инструментов — отвечаем как раньше, без них.
            if step == 0:
                print(f"[tool_loop] tools rejected, retry without err={e!r}")
                return openrouter_chat_completion(payload, operation=operation, timeout=timeout)
            raise
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        calls = (message or {}).get("tool_calls") if isinstance(message, dict) else None
        if not calls:
            return data
        messages.append(
            {
                "role": "assistant",
                "content": (message or {}).get("content") or "",
                "tool_calls": calls,
            }
        )
        for tc in calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name") or "").strip()
            raw_args = fn.get("arguments") or "{}"
            try:
                args = _json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (ValueError, TypeError):
                args = {}
            try:
                result = tool_runner(name, args if isinstance(args, dict) else {})
            except Exception as e:  # noqa: BLE001
                result = _json.dumps({"error": str(e)}, ensure_ascii=False)
            messages.append(
                {"role": "tool", "tool_call_id": str(tc.get("id") or ""), "content": str(result)}
            )
    return data


def research_with_web(
    system: str,
    user: str,
    *,
    timeout: float = 180,
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    return _chat(
        system,
        user,
        operation="note_research",
        model=model,
        timeout=timeout,
        temperature=0.2 if temperature is None else float(temperature),
        web=True,
    )


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


def parse_voice_command(text: str, *, now: datetime) -> dict[str, Any] | None:
    """Голосовая команда с «Актуального»: задача / напоминание / встреча / вопрос."""
    from datetime import timedelta

    wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    # Готовый календарь на 2 недели: модели плохо считают «в пятницу» сами.
    days = [now.date() + timedelta(days=i) for i in range(14)]
    payload = {
        "now": now.strftime("%Y-%m-%d %H:%M") + " (" + wd[now.weekday()] + ")",
        "days": [d.isoformat() + " " + wd[d.weekday()] for d in days],
        "text": text,
    }
    raw = _chat(
        VOICE_COMMAND_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        operation="parse_voice_command",
        model=os.getenv("OPENROUTER_MODEL_ROUTER", "").strip() or "google/gemini-2.5-flash",
        timeout=40,
    )
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
    quotes: list[str] | None = None,
    knowledge_brief: str = "",
    attached_notes: str = "",
    images: list[dict[str, str]] | None = None,
    files: list[dict[str, str]] | None = None,
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
        quotes=quotes,
        knowledge_brief=knowledge_brief,
        attached_notes=attached_notes,
        images=images,
        files=files,
        file_notes=file_notes,
    )["answer"]


def model_identity_line(model_id: str) -> str:
    mid = str(model_id or "").strip()
    if not mid:
        return ""
    return (
        f"Ты отвечаешь моделью {mid} (её выбрал пользователь). "
        "Если спросят, какая модель отвечает, называй её, а не другую."
    )


def answer_with_context_result(
    question: str,
    context: str = "",
    model: str | None = None,
    history: list[dict[str, str]] | None = None,
    *,
    note_title: str = "",
    note_text: str = "",
    quote: str = "",
    quotes: list[str] | None = None,
    knowledge_brief: str = "",
    attached_notes: str = "",
    images: list[dict[str, str]] | None = None,
    files: list[dict[str, str]] | None = None,
    file_notes: str = "",
    system: str | None = None,
    temperature: float | None = None,
    web: bool = False,
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_runner: Any = None,
    tool_choice_first: str = "auto",
) -> dict[str, Any]:
    from assistant.nlu.ask_context import assemble_ask_messages

    extra, user = assemble_ask_messages(
        question=question,
        context=context,
        note_title=note_title,
        note_text=note_text,
        quote=quote,
        quotes=quotes,
        knowledge_brief=knowledge_brief,
        attached_notes=attached_notes,
    )
    notes = (file_notes or "").strip()
    if notes:
        user = (user or "").rstrip() + "\n\n" + notes
    if (images or files) and not (question or "").strip() and not (user or "").strip():
        user = "Опиши вложение и ответь по нему."
    sys = _system_with_attachments((system or "").strip() or ASK_SYSTEM)
    temp = 0.1 if temperature is None else float(temperature)
    run_model = model or _model_ask()
    # Модель не знает своего имени и угадывает («я GPT-4»), хотя пользователь выбрал другую — называем её.
    sys = sys.rstrip() + "\n\n" + model_identity_line(run_model)
    chat_out = _chat_result(
        sys,
        user,
        operation="ask",
        model=run_model,
        temperature=temp,
        history=history,
        context_prefix=extra or None,
        images=images,
        files=files,
        web=web,
        max_tokens=max_tokens,
        tools=tools,
        tool_runner=tool_runner,
        tool_choice_first=tool_choice_first,
    )
    text = chat_out[0]
    out_images = chat_out[1] if len(chat_out) > 1 else []
    used_model = chat_out[2] if len(chat_out) > 2 else run_model
    text, out_images = _materialize_generated_media(
        text,
        out_images,
        question=question or user,
        history=history,
        source_images=images,
    )
    return {"answer": text, "images": out_images, "model": used_model or run_model}


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


def summary_v2_enabled() -> bool:
    return (os.getenv("SUMMARY_PIPELINE", "v2") or "v2").strip().lower() != "v1"


def _json_obj(raw: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _summarize_recording_v2(
    transcript: str, *, speakers_detected: bool, context: Any = None
) -> dict[str, Any] | None:
    """Окна → кандидаты → сведение задач; факты одним вызовом или синтезом по окнам → рендер кодом."""
    from assistant.lib import summary_normalize as sn
    from assistant.lib.summary_render import render_summary_html
    from assistant.nlu import summary_v2

    ctx_payload = context.to_payload() if context is not None else {}
    model = _model_summary()
    raw = summary_v2.extract(
        transcript, speakers_detected=speakers_detected, ctx_payload=ctx_payload, model=model, chat=_chat
    )
    if not raw:
        return None
    obj = raw["obj"]
    text = raw["text"]
    if not (str(obj.get("brief") or "").strip() or raw["tasks"] or obj.get("discussion")):
        return None
    content_type = str(obj.get("content_type") or "meeting").strip().lower()
    if content_type not in {"meeting", "interview", "lecture", "brainstorm", "voice_note"}:
        content_type = "meeting"
    try:
        confidence = max(0.0, min(1.0, float(obj.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    speaker_map = raw["speaker_map"]
    people = list(getattr(context, "people", []) or [])
    meeting_at = str(getattr(context, "meeting_at", "") or "")
    tasks = sn.resolve_tasks(
        raw["tasks"],
        speaker_map=speaker_map,
        people=people,
        meeting_at=meeting_at,
        transcript=text,
    )
    data = {
        "content_type": content_type,
        "brief": str(obj.get("brief") or "").strip(),
        "discussion": obj.get("discussion") if isinstance(obj.get("discussion"), list) else [],
        "decisions": sn.decisions(obj.get("decisions"), text),
        "tasks": tasks,
        "open_questions": sn.text_items(obj.get("open_questions")),
        "risks": sn.text_items(obj.get("risks")),
        "next_steps": sn.text_items(obj.get("next_steps")),
        "sections": obj.get("sections") if isinstance(obj.get("sections"), list) else [],
        "speaker_map": speaker_map,
    }
    summary = render_summary_html(data)
    participants = sn.text_items(obj.get("participants"))
    return {
        "content_type": content_type,
        "confidence": confidence,
        "main_topic": str(obj.get("main_topic") or "").strip(),
        "summary": summary,
        "participants": participants,
        "tasks": tasks,
        "decisions": [d["text"] for d in data["decisions"]],
        "decisions_detail": data["decisions"],
        "deadlines": sn.text_items(obj.get("deadlines")),
        "topics": sn.text_items(obj.get("topics")),
        "open_questions": data["open_questions"],
        "risks": data["risks"],
        "speaker_map": speaker_map,
        "summary_data": data,
        "summary_version": 2,
        "summary_model": model,
        "meeting_at": meeting_at,
        "summary_stats": raw["stats"],
    }


def summarize_recording(
    transcript: str, *, speakers_detected: bool = False, context: Any = None
) -> dict[str, Any] | None:
    """Саммари записи. context — assistant.lib.meeting_context.MeetingContext (необязателен)."""
    text = (transcript or "").strip()
    if not text:
        return None
    if summary_v2_enabled():
        try:
            out = _summarize_recording_v2(text, speakers_detected=speakers_detected, context=context)
            if out:
                return out
            print("[summary_v2] empty/invalid result, falling back to v1")
        except Exception as e:
            print(f"[summary_v2] failed, falling back to v1: {e!r}")
    return _summarize_recording_v1(text, speakers_detected=speakers_detected)


def summary_v1_dedupe_enabled() -> bool:
    return (os.getenv("SUMMARY_V1_DEDUPE", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}


def _dedupe_v1_tasks(tasks: list[dict[str, Any]], transcript: str) -> list[dict[str, Any]]:
    """v1 + склейка: задачи v1 (полнота лучше v2 на эталоне) → вызов сведения только убирает повторы."""
    from assistant.lib import summary_normalize as sn
    from assistant.nlu import summary_v2

    raw = [t for t in (sn.raw_task(x) for x in tasks) if t]
    if len(raw) < 2:
        return tasks
    merged, stats = summary_v2.consolidate_tasks(
        _chat, raw, ctx_payload={}, model=_model_summary(), transcript_norm=sn._norm(transcript)
    )
    if stats.get("consolidate_failed") or not merged:
        return tasks
    out = []
    for t in merged:
        out.append({
            "assignee": t.get("assignee") or t.get("speaker") or "",
            "task": t["task"],
            "deadline": t.get("deadline") or "",
            "completed": bool(t.get("completed")),
        })
    return out


def _summarize_recording_v1(
    transcript: str, *, speakers_detected: bool = False, dedupe: bool | None = None
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
    if summary_v1_dedupe_enabled() if dedupe is None else dedupe:
        try:
            merged_tasks = _dedupe_v1_tasks(merged_tasks, text)
        except Exception as e:
            print(f"[summary_v1] dedupe failed: {e!r}")
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


_CHAT_DIGEST_TRANSCRIPT_MAX = 48000


def _normalize_digest_summary(obj: dict[str, Any]) -> dict[str, Any]:
    decisions_raw = obj.get("decisions") if isinstance(obj.get("decisions"), list) else []
    decisions: list[dict[str, str]] = []
    for item in decisions_raw:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("decision") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            decisions.append({"text": text})

    steps_raw = obj.get("next_steps") if isinstance(obj.get("next_steps"), list) else []
    next_steps: list[dict[str, str]] = []
    for item in steps_raw:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        next_steps.append(
            {
                "assignee": str(item.get("assignee") or "").strip(),
                "task": task,
                "deadline": str(item.get("deadline") or "").strip(),
            }
        )

    deadlines_raw = obj.get("deadlines") if isinstance(obj.get("deadlines"), list) else []
    deadlines: list[dict[str, str]] = []
    for item in deadlines_raw:
        if not isinstance(item, dict):
            continue
        what = str(item.get("item") or item.get("text") or "").strip()
        if not what:
            continue
        deadlines.append(
            {
                "item": what,
                "when": str(item.get("when") or item.get("deadline") or "").strip(),
                "assignee": str(item.get("assignee") or "").strip(),
            }
        )

    questions_raw = (
        obj.get("open_questions") if isinstance(obj.get("open_questions"), list) else []
    )
    open_questions: list[dict[str, str]] = []
    for item in questions_raw:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("question") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            open_questions.append({"text": text})

    topics_raw = (
        obj.get("context_topics") if isinstance(obj.get("context_topics"), list) else []
    )
    context_topics: list[str] = []
    for item in topics_raw:
        t = str(item or "").strip()
        if t:
            context_topics.append(t)
        if len(context_topics) >= 5:
            break

    return {
        "brief": str(obj.get("brief") or "").strip(),
        "decisions": decisions,
        "next_steps": next_steps,
        "deadlines": deadlines,
        "open_questions": open_questions,
        "context_topics": context_topics,
    }


def digest_chat_day(
    *,
    chat_title: str,
    report_date: str,
    transcript: str,
) -> dict[str, Any] | None:
    """Саммари дня по переписке чата. Usage: operation=chat_digest."""
    text = (transcript or "").strip()
    if not text:
        return None
    payload = {
        "chat_title": (chat_title or "").strip(),
        "report_date": (report_date or "").strip(),
        "messages": text[:_CHAT_DIGEST_TRANSCRIPT_MAX],
    }
    raw = _chat(
        CHAT_DIGEST_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        operation="chat_digest",
        model=_model_chat_digest(),
        timeout=180,
        temperature=0.1,
        max_tokens=4096,
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return _normalize_digest_summary(obj)

