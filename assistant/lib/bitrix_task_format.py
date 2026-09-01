"""Форматирование задачи Bitrix24 для Telegram Rich Message (HTML)."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from assistant.integrations.bitrix_mcp_portal import portal_host_from_token, task_url


def _parse_task_payload(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    brace = text.find("{")
    if brace < 0:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(text[brace:])
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return None


def _clean_description(value: str, *, limit: int = 700) -> str:
    s = (value or "").strip()
    if not s:
        return "Описание не указано"
    s = re.sub(r"\[/?[A-Z]+\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[/?LIST[^\]]*\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[\*\]", "•", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.strip() or "Описание не указано"
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def _task_link(task: dict[str, Any], *, portal_host: str) -> str:
    tid = task.get("taskId") or task.get("id") or "?"
    creator = task.get("creator") if isinstance(task.get("creator"), dict) else {}
    responsible = task.get("responsible") if isinstance(task.get("responsible"), dict) else {}
    return task_url(
        portal_host=portal_host,
        task_id=tid,
        link=str(task.get("link") or ""),
        user_id=creator.get("id") or responsible.get("id") or "",
    )


def _task_status_label(task: dict[str, Any]) -> str:
    parts: list[str] = []
    status = str(task.get("status") or "").strip()
    stage = str(task.get("stageTitle") or "").strip()
    if status:
        parts.append(status)
    if stage and stage.lower() not in status.lower():
        parts.append(stage)
    return " · ".join(parts) or "—"


def _task_responsible_name(task: dict[str, Any]) -> str:
    responsible = task.get("responsible") if isinstance(task.get("responsible"), dict) else {}
    return str(responsible.get("name") or "").strip() or "—"


def format_task_dropdown_markdown(
    task: dict[str, Any],
    *,
    portal_host: str,
    index: int | None = None,
) -> str:
    """Кликабельное название + раскрывающийся блок (HTML для sendRichMessage)."""
    title = str(task.get("title") or "Без названия").strip()
    link = _task_link(task, portal_host=portal_host)
    desc = html.escape(_clean_description(str(task.get("description") or ""))).replace(
        "\n", "<br>"
    )
    status = html.escape(_task_status_label(task))
    responsible = html.escape(_task_responsible_name(task))
    prefix = f"{index}. " if index is not None else ""
    return (
        f"<p>{prefix}<a href=\"{html.escape(link, quote=True)}\">"
        f"{html.escape(title)}</a></p>"
        f"<details>"
        f"<summary>Подробнее</summary>"
        f"<b>Описание</b><br>{desc}<br><br>"
        f"<b>Статус:</b> {status}<br>"
        f"<b>Исполнитель:</b> {responsible}"
        f"</details>"
    )


def format_task_markdown(task: dict[str, Any], *, portal_host: str) -> str:
    return format_task_dropdown_markdown(task, portal_host=portal_host)


def format_task_from_tool_result(raw: str, *, token: str) -> str | None:
    task = _parse_task_payload(raw)
    if not task:
        return None
    portal = portal_host_from_token(token)
    return format_task_markdown(task, portal_host=portal)


def format_candidates_markdown(
    candidates: list[dict[str, Any]],
    *,
    portal_host: str,
    user_id: int | str = "",
) -> str:
    if not candidates:
        return "Подходящих задач не нашёл. Уточните название или ID."
    lines = ["<h2>Нашёл несколько задач</h2>", "<p>Уточните, какая нужна:</p>"]
    for idx, item in enumerate(candidates[:5], start=1):
        tid = item.get("taskId")
        title = item.get("title") or "?"
        link = task_url(portal_host=portal_host, task_id=tid, user_id=user_id)
        lines.append(
            f"<p>{idx}. <a href=\"{html.escape(link, quote=True)}\">"
            f"{html.escape(str(title))}</a></p>"
        )
    return "\n".join(lines)


def format_task_list_markdown(
    tasks: list[dict[str, Any]],
    *,
    portal_host: str,
    heading: str = "",
) -> str:
    if not tasks:
        return "Подходящих задач не нашёл. Попробуйте уточнить название, статус или группу."
    lines: list[str] = []
    if heading:
        title = heading.lstrip("#").strip().rstrip(":")
        lines.append(f"<h2>{html.escape(title)}</h2>")
    for idx, task in enumerate(tasks, start=1):
        lines.append(format_task_dropdown_markdown(task, portal_host=portal_host, index=idx))
    return "\n".join(lines)
