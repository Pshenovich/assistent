"""Слияние структурированных задач с HTML-саммари."""

from __future__ import annotations

import re
from typing import Any


def _task_text(task: dict[str, Any]) -> str:
    return str(task.get("task") or "").strip()


def _normalize_task_key(task: str) -> str:
    s = (task or "").lower().strip()
    s = re.sub(r"[^\w\s]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def merge_tasks(
    primary: list[Any] | None,
    secondary: list[Any] | None,
) -> list[dict[str, str]]:
    """Объединяет списки задач без дубликатов; сохраняет assignee/deadline если есть."""
    out: list[dict[str, str]] = []
    by_key: dict[str, dict[str, str]] = {}

    def _add(raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        task = _task_text(raw)
        if not task:
            return
        key = _normalize_task_key(task)
        if not key:
            return
        cur = by_key.get(key)
        item = {
            "assignee": str(raw.get("assignee") or "").strip(),
            "task": task,
            "deadline": str(raw.get("deadline") or "").strip(),
            "completed": bool(raw.get("completed")),
        }
        if cur is None:
            by_key[key] = item
            return
        if not cur.get("assignee") and item.get("assignee"):
            cur["assignee"] = item["assignee"]
        if not cur.get("deadline") and item.get("deadline"):
            cur["deadline"] = item["deadline"]
        if item.get("completed"):
            cur["completed"] = True
        if len(item["task"]) > len(cur["task"]):
            cur["task"] = item["task"]

    order: list[str] = []
    for src in (primary or [], secondary or []):
        for raw in src:
            if not isinstance(raw, dict):
                continue
            task = _task_text(raw)
            key = _normalize_task_key(task)
            if not key:
                continue
            if key not in by_key:
                order.append(key)
            _add(raw)

    for key in order:
        out.append(by_key[key])
    return out


def inject_tasks_into_summary(summary_html: str, tasks: list[dict[str, str]]) -> str:
    """Подставляет разделы задач чеклистами Rich Message."""
    from assistant.lib.telegram_rich import inject_tasks_checklists

    return inject_tasks_checklists(summary_html, tasks)
