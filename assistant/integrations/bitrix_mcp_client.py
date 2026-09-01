"""Bitrix24 MCP client (Streamable HTTP + токен в Authorization)."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, TypeVar

import httpx

from assistant.lib.exception_format import format_exception_message, unwrap_exception

DEFAULT_MCP_URL = "https://mcp.bitrix24.tech/mcp/"
T = TypeVar("T")


def normalize_auth_token(token: str) -> str:
    """Authorization для Bitrix MCP: Bearer <jwt> (с префиксом или без)."""
    value = (token or "").strip()
    if not value:
        raise ValueError("Не задан токен Bitrix24 MCP")
    if value.lower().startswith("bearer "):
        return value
    return f"Bearer {value}"


def mcp_url() -> str:
    return (os.getenv("BITRIX_MCP_URL", "") or DEFAULT_MCP_URL).strip().rstrip("/") + "/"


def _http_timeout() -> httpx.Timeout:
    try:
        connect = float(os.getenv("BITRIX_MCP_CONNECT_TIMEOUT_SEC", "30") or "30")
    except ValueError:
        connect = 30.0
    try:
        read = float(os.getenv("BITRIX_MCP_READ_TIMEOUT_SEC", "120") or "120")
    except ValueError:
        read = 120.0
    return httpx.Timeout(connect, read=read)


def _response_detail(response: httpx.Response | None) -> str:
    """Текст ответа для сообщения об ошибке (без падения на stream-ответах)."""
    if response is None:
        return ""
    try:
        text = response.text
        if text:
            return text.strip()[:240]
    except httpx.ResponseNotRead:
        pass
    except Exception:
        pass
    reason = getattr(response, "reason_phrase", None) or ""
    return str(reason).strip()[:240]


def _raise_mcp_error(exc: BaseException) -> None:
    """Преобразует TaskGroup/HTTP-ошибки MCP SDK в понятный RuntimeError."""
    root = unwrap_exception(exc)
    if isinstance(root, httpx.ResponseNotRead):
        raise RuntimeError(
            "Ошибка Bitrix24 MCP: не удалось прочитать ответ сервера"
        ) from root
    if isinstance(root, httpx.HTTPStatusError):
        status = root.response.status_code if root.response is not None else "?"
        detail = _response_detail(root.response)
        if status == 401:
            raise RuntimeError(
                "Токен Bitrix24 MCP недействителен или истёк (HTTP 401). "
                "Получите новый токен в Битрикс24: Приложения → MCP-подключения → "
                "«Получить токен подключения», затем подключите снова в мини-приложении."
            ) from root
        hint = f" HTTP {status}"
        if detail:
            hint += f": {detail}"
        raise RuntimeError(f"Ошибка Bitrix24 MCP{hint}") from root
    if isinstance(root, httpx.HTTPError):
        raise RuntimeError(f"Сеть Bitrix24 MCP: {root}") from root
    message = format_exception_message(exc)
    raise RuntimeError(f"Bitrix24 MCP: {message}") from root


def _run_mcp_work_sync(work: Callable[[], Awaitable[T]]) -> T:
    """MCP SDK (anyio) стабильнее в отдельном asyncio.run, не в loop бота/FastAPI."""
    return asyncio.run(work())


async def run_mcp_isolated(work: Callable[[], Awaitable[T]]) -> T:
    return await asyncio.to_thread(_run_mcp_work_sync, work)


async def run_with_mcp_session(
    token: str,
    body: Callable[[Any], Awaitable[T]],
) -> T:
    async def _work() -> T:
        async with mcp_session(token) as session:
            return await body(session)

    return await run_mcp_isolated(_work)


@asynccontextmanager
async def mcp_session(token: str) -> AsyncIterator[Any]:
    """Одна MCP-сессия для нескольких вызовов инструментов подряд."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    value = normalize_auth_token(token)
    url = mcp_url()
    timeout = _http_timeout()
    try:
        async with httpx.AsyncClient(
            headers={"Authorization": value},
            timeout=timeout,
        ) as http:
            async with streamable_http_client(url, http_client=http) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
    except BaseException as exc:
        _raise_mcp_error(exc)
        raise  # pragma: no cover


@asynccontextmanager
async def _session(token: str) -> AsyncIterator[Any]:
    async with mcp_session(token) as session:
        yield session


def _serialize_tool_result(result: Any) -> str:
    if result is None:
        return ""
    parts: list[str] = []
    content = getattr(result, "content", None)
    if content:
        for block in content:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                parts.append(str(block))
    if getattr(result, "isError", False):
        body = "\n".join(parts).strip() or "Ошибка Bitrix24 MCP"
        return f"ERROR: {body}"
    if parts:
        return "\n".join(parts)
    try:
        if hasattr(result, "model_dump"):
            return json.dumps(result.model_dump(), ensure_ascii=False, default=str)
    except Exception:
        pass
    return str(result)


async def validate_token(token: str) -> bool:
    try:
        await validate_token_detail(token)
        return True
    except Exception:
        return False


async def validate_token_detail(token: str) -> None:
    """Проверка токена; при ошибке — исключение с текстом для UI."""
    try:
        await run_mcp_isolated(
            lambda: _validate_token_detail_async(token),
        )
    except RuntimeError:
        raise
    except Exception as e:
        msg = format_exception_message(e)
        raise RuntimeError(
            "Не удалось подключиться к Bitrix24 MCP. "
            "Проверьте токен из «Приложения → MCP-подключения» "
            "(можно вставлять с «Bearer » или без). "
            f"Технически: {msg}"
        ) from e


async def _validate_token_detail_async(token: str) -> None:
    async with mcp_session(token):
        return


async def list_tools(token: str) -> list[Any]:
    return await run_mcp_isolated(lambda: _list_tools_async(token))


async def _list_tools_async(token: str) -> list[Any]:
    async with mcp_session(token) as session:
        return await list_tools_in_session(session)


async def list_tools_in_session(session: Any) -> list[Any]:
    try:
        response = await session.list_tools()
        return list(response.tools or [])
    except BaseException as exc:
        _raise_mcp_error(exc)
        raise  # pragma: no cover


async def call_tool(token: str, name: str, arguments: dict[str, Any] | None = None) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    return await run_mcp_isolated(lambda: _call_tool_async(token, name, args))


async def _call_tool_async(token: str, name: str, args: dict[str, Any]) -> str:
    async with mcp_session(token) as session:
        return await call_tool_in_session(session, name, args)


async def call_tool_in_session(
    session: Any,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    try:
        result = await session.call_tool(name, arguments=args)
        return _serialize_tool_result(result)
    except BaseException as exc:
        _raise_mcp_error(exc)
        raise  # pragma: no cover
