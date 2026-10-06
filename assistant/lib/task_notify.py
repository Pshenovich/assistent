"""Уведомление исполнителя о Leo-задаче."""

from __future__ import annotations

import html
from typing import Any


def task_webapp_url(task_id: int | str) -> str:
    from assistant.lib.webapp_public import webapp_entry_url

    base = webapp_entry_url()
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}task={int(task_id)}"


def task_title_link_html(title: str, task_id: int | str) -> str:
    label = html.escape((title or "").strip() or "Задача")
    url = html.escape(task_webapp_url(task_id), quote=True)
    return f'<a href="{url}">{label}</a>'


def notify_task_assignee(
    *,
    assignee_user_id: int | str,
    task: dict[str, Any],
    assigner_name: str,
) -> None:
    from assistant.lib.telegram_notify import send_message
    from assistant.stores.calendar_tasks import chip_label, task_is_delegated

    if not task_is_delegated(task):
        return
    try:
        mid = int(str(assignee_user_id or "").strip() or 0)
    except (TypeError, ValueError):
        mid = 0
    if mid <= 0:
        return
    link = task_title_link_html(str(task.get("title") or "Задача"), task.get("id") or 0)
    who = html.escape(assigner_name or "Участник")
    lines = [f"{who} поставил(а) вам задачу: {link}."]
    if not task.get("all_day"):
        when = str(task.get("chip_label") or "").strip() or chip_label(task)
        if when:
            lines.append(f"Когда: {html.escape(when)}")
    try:
        send_message(mid, "\n".join(lines))
    except Exception as e:
        print(f"[calendar_tasks] notify_assignee uid={mid} err={e!r}")
