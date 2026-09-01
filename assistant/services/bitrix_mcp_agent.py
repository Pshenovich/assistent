"""LLM agent loop для Bitrix24 MCP (tool calling)."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from assistant.integrations.bitrix_mcp_client import (
    call_tool_in_session,
    list_tools_in_session,
    mcp_session,
)
from assistant.integrations.bitrix_mcp_portal import portal_host_from_token
from assistant.integrations.bitrix_mcp_token import get_user_token
from assistant.integrations.openrouter_client import is_llm_configured, openrouter_chat_completion
from assistant.services.bitrix_mcp_search import find_task_candidates

BITRIX_AGENT_SYSTEM = """Ты ассистент Bitrix24 в Telegram-боте Leo.
Используй доступные инструменты MCP для выполнения запроса пользователя.

КРИТИЧЕСКИ ВАЖНО:
- Сначала ВСЕГДА вызывай инструменты. Не отвечай текстом, пока не попробовал поиск/получение данных.
- search_tasks принимает поля title и description (НЕ searchQuery). Для поиска по названию используй title.
- НЕ ищи по description широкими словам вроде «Кордекса» — это даёт ложные совпадения. Сначала title с ключевыми словами из запроса.
- Если в контексте есть «Предварительный поиск задач» с несколькими кандидатами — для запроса «найди задачи» верни ВСЕ подходящие, не только первую.
- «Описание задачи про X»: найди задачу → get_task_by_id → верни ОДНУ задачу с полным описанием.
- Для «найди задачи» / множественного поиска: перечисли все совпадения с названием, исполнителем, сроком, статусом и ссылкой.
- Формат списка задач (Rich HTML):
  - Заголовок: <h2>Нашёл N задач</h2>
  - Для каждой: <p>N. <a href="ссылка">Название</a></p>
  - Под названием: <details><summary>Подробнее</summary><b>Описание</b><br>…<br><br><b>Статус:</b> …<br><b>Исполнитель:</b> …</details>
- Не выдавай список из 10+ нерелевантных задач. Если кандидат один лучший — покажи только его (кроме явного запроса списка).
- НИКОГДА не выдумывай ссылки (example.com запрещён). Используй поле link из get_task_by_id с хостом портала.
- Учитывай предыдущие реплики («эту задачу», «её описание»).

СОЗДАНИЕ ЗАДАЧИ:
- Если указан исполнитель — ОБЯЗАТЕЛЬНО сначала search_users с полем searchQueries (массив имён, НЕ searchQuery).
- Затем create_task с responsibleId = ID из search_users. Без responsibleId исполнителем станет текущий пользователь — это ошибка.
- Не выдумывай срок/дедлайн, если пользователь его не просил.
- Для задач «[Бустра LLM] …» укажи groupId проекта клиентских задач, если он известен из контекста.

Полезные инструменты: search_tasks, get_task_by_id, update_task, create_task, search_users.

Правила ответа:
- Отвечай на русском, кратко и по делу.
- Используй Markdown: **жирный**, списки, [текст](url) для ссылок.
- Не выдумывай ID и поля — только из инструментов.
- Если инструмент вернул ERROR — объясни ошибку простым языком."""


def _bitrix_model() -> str:
    explicit = os.getenv("OPENROUTER_MODEL_BITRIX", "").strip()
    if explicit:
        return explicit
    return os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()


def _max_steps() -> int:
    try:
        return max(1, int(os.getenv("BITRIX_MCP_AGENT_MAX_STEPS", "8") or "8"))
    except ValueError:
        return 8


def _mcp_tool_to_openai(tool: Any) -> dict[str, Any]:
    schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
    if not schema:
        schema = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (tool.description or tool.title or tool.name or "").strip(),
            "parameters": schema,
        },
    }


def _message_content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p).strip()
    return str(content or "").strip()


async def _run_async(
    telegram_user_id: int,
    user_text: str,
    *,
    history: list[dict[str, str]] | None = None,
) -> str:
    if not is_llm_configured():
        raise RuntimeError("LLM не настроен (OPENROUTER_KEY или COMET_API_KEY)")
    token = get_user_token(telegram_user_id)
    if not token:
        raise RuntimeError("Bitrix24 не подключён")

    text = (user_text or "").strip()
    if not text:
        raise ValueError("Пустой запрос")

    async with mcp_session(token) as session:
        tools = await list_tools_in_session(session)
        if not tools:
            raise RuntimeError("Bitrix24 MCP не вернул доступные инструменты")

        openai_tools = [_mcp_tool_to_openai(t) for t in tools]
        portal = portal_host_from_token(token)
        system = (
            f"{BITRIX_AGENT_SYSTEM}\n\n"
            f"Портал Bitrix24: {portal}\n"
            f"Формат ссылки на задачу: {portal}/company/personal/user/{{userId}}/tasks/task/view/{{taskId}}/"
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

        candidates = await find_task_candidates(token, text, session=session)
        if candidates:
            hint = {
                "note": "Предварительный поиск (title + REST). Начни с get_task_by_id для лучшего совпадения.",
                "candidates": candidates[:8],
            }
            messages.append(
                {
                    "role": "system",
                    "content": json.dumps(hint, ensure_ascii=False),
                }
            )

        for item in history or []:
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": text})

        for step in range(_max_steps()):
            payload: dict[str, Any] = {
                "model": _bitrix_model(),
                "messages": messages,
                "tools": openai_tools,
                "temperature": 0.2,
                "tool_choice": "required" if step == 0 else "auto",
            }
            data = await asyncio.to_thread(
                openrouter_chat_completion,
                payload,
                operation="bitrix_mcp_agent",
                timeout=120,
            )
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") if isinstance(choice, dict) else {}
            if not isinstance(message, dict):
                raise RuntimeError("Некорректный ответ LLM")

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                answer = _message_content_text(message)
                if answer:
                    return answer
                raise RuntimeError("Пустой ответ LLM")

            messages.append(message)
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = str(fn.get("name") or "").strip()
                raw_args = fn.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                if not name:
                    result_text = "ERROR: пустое имя инструмента"
                else:
                    try:
                        result_text = await call_tool_in_session(session, name, arguments)
                    except Exception as e:
                        result_text = f"ERROR: {e}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(tc.get("id") or ""),
                        "content": result_text,
                    }
                )

        raise RuntimeError("Превышен лимит шагов агента Bitrix24")


def run(
    telegram_user_id: int,
    user_text: str,
    *,
    history: list[dict[str, str]] | None = None,
) -> str:
    return asyncio.run(_run_async(telegram_user_id, user_text, history=history))
