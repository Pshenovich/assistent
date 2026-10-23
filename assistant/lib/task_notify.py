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


def notify_task_owner_done(
    *,
    task: dict[str, Any],
    completer_name: str,
) -> None:
    """Пуш автору, когда исполнитель отметил делегированную задачу выполненной."""
    from assistant.lib.telegram_notify import send_message
    from assistant.stores.calendar_tasks import task_is_delegated

    if not task_is_delegated(task):
        return
    try:
        owner = int(str(task.get("owner_user_id") or "").strip() or 0)
    except (TypeError, ValueError):
        owner = 0
    if owner <= 0:
        return
    try:
        completer_uid = int(str(task.get("assignee_user_id") or "").strip() or 0)
    except (TypeError, ValueError):
        completer_uid = 0
    # Не уведомляем, если автор сам себе закрыл.
    if completer_uid and completer_uid == owner:
        return
    link = task_title_link_html(str(task.get("title") or "Задача"), task.get("id") or 0)
    who = html.escape((completer_name or "").strip() or "Исполнитель")
    text = f"{who} выполнил(а) задачу: {link}."
    result = str(task.get("result") or "").strip()
    if result:
        if len(result) > 600:
            result = result[:600].rstrip() + "…"
        text += f"\nРезультат: {html.escape(result)}"
    try:
        send_message(owner, text)
    except Exception as e:
        print(f"[calendar_tasks] notify_owner_done uid={owner} err={e!r}")


def _int_uid(raw: Any) -> int:
    try:
        return int(str(raw or "").strip() or 0)
    except (TypeError, ValueError):
        return 0


def notify_task_nudge(*, task: dict[str, Any], assigner_name: str) -> bool:
    """«Напомнить» из «Поручено другим»: пуш исполнителю со ссылкой и сроком."""
    from assistant.lib.telegram_notify import send_message
    from assistant.stores.calendar_tasks import chip_label, task_is_delegated

    if not task_is_delegated(task) or task.get("done"):
        return False
    mid = _int_uid(task.get("assignee_user_id"))
    if mid <= 0:
        return False
    link = task_title_link_html(str(task.get("title") or "Задача"), task.get("id") or 0)
    who = html.escape((assigner_name or "").strip() or "Постановщик")
    lines = [f"{who} напоминает о задаче: {link}."]
    when = str(task.get("chip_label") or "").strip() or chip_label(task)
    if when:
        lines.append(f"Срок: {html.escape(when)}")
    try:
        return bool(send_message(mid, "\n".join(lines)))
    except Exception as e:
        print(f"[calendar_tasks] notify_nudge uid={mid} err={e!r}")
        return False


def notify_task_owner_question(*, task: dict[str, Any], asker_name: str, question: str) -> None:
    """Исполнитель нажал «Есть вопрос» — пуш автору с текстом вопроса."""
    from assistant.lib.telegram_notify import send_message
    from assistant.stores.calendar_tasks import task_is_delegated

    if not task_is_delegated(task):
        return
    owner = _int_uid(task.get("owner_user_id"))
    if owner <= 0:
        return
    link = task_title_link_html(str(task.get("title") or "Задача"), task.get("id") or 0)
    who = html.escape((asker_name or "").strip() or "Исполнитель")
    q = (question or "").strip()
    if len(q) > 600:
        q = q[:600].rstrip() + "…"
    text = f"{who} спрашивает по задаче {link}:\n{html.escape(q)}"
    try:
        send_message(owner, text)
    except Exception as e:
        print(f"[calendar_tasks] notify_owner_question uid={owner} err={e!r}")


def _send_task_push(to_uid: Any, text: str, tag: str) -> None:
    from assistant.lib.telegram_notify import send_message

    uid = _int_uid(to_uid)
    if uid <= 0:
        return
    try:
        send_message(uid, text)
    except Exception as e:
        print(f"[calendar_tasks] {tag} uid={uid} err={e!r}")


def notify_task_message(
    *, task: dict[str, Any], sender_id: Any, sender_name: str, text: str, mentions: list[dict[str, Any]] | None = None
) -> None:
    """Сообщение в обсуждении поручения — второй стороне (постановщику или исполнителю)."""
    from assistant.stores.calendar_tasks import task_is_delegated

    if not task_is_delegated(task):
        return
    owner = str(task.get("owner_user_id") or "")
    to = str(task.get("assignee_user_id") or "") if str(sender_id) == owner else owner
    link = task_title_link_html(str(task.get("title") or "Задача"), task.get("id") or 0)
    who = html.escape((sender_name or "").strip() or "Участник")
    body = (text or "").strip()
    if len(body) > 600:
        body = body[:600].rstrip() + "…"
    _send_task_push(to, f"{who} по задаче {link}:\n{html.escape(body)}", "notify_message")
    # Упомянутые через @ (кроме участников — им уже ушло): без ссылки, задача может быть им недоступна.
    title = html.escape(str(task.get("title") or "Задача"))
    for m in mentions or []:
        mid = str((m or {}).get("user_id") or "")
        if not mid or mid in (str(sender_id), owner, str(task.get("assignee_user_id") or "")):
            continue
        _send_task_push(mid, f"{who} упомянул(а) вас в задаче «{title}»:\n{html.escape(body)}", "notify_mention")


def notify_task_returned(*, task: dict[str, Any], owner_name: str, comment: str) -> None:
    link = task_title_link_html(str(task.get("title") or "Задача"), task.get("id") or 0)
    who = html.escape((owner_name or "").strip() or "Постановщик")
    text = f"{who} вернул(а) задачу в работу: {link}."
    if (comment or "").strip():
        text += f"\n{html.escape(comment.strip()[:600])}"
    _send_task_push(task.get("assignee_user_id"), text, "notify_returned")


def notify_task_cancelled(*, task: dict[str, Any], owner_name: str) -> None:
    who = html.escape((owner_name or "").strip() or "Постановщик")
    title = html.escape(str(task.get("title") or "Задача"))
    _send_task_push(task.get("assignee_user_id"), f"{who} отменил(а) задачу «{title}» — делать её больше не нужно.", "notify_cancelled")
