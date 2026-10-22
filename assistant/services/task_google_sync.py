"""Синк задач Leo с Google (Tasks / события) — общий для миниаппа и бота."""

from __future__ import annotations

from typing import Any


def task_owner_uid(task: dict[str, Any] | None, fallback: int) -> int:
    try:
        return int(str((task or {}).get("owner_user_id") or fallback) or fallback)
    except (TypeError, ValueError):
        return int(fallback)


def clear_task_google_ids(uid: int, task: dict[str, Any]) -> dict[str, Any]:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    cleared = calendar_tasks_store.update_task(
        uid,
        task["id"],
        google_task_id="",
        google_tasklist_id="",
        google_event_id="",
        google_calendar_id="",
    )
    return cleared or task


def sync_task_google(uid: int, task: dict[str, Any] | None, action: str) -> dict[str, Any] | None:
    if not task:
        return task
    try:
        from assistant.services import calendar as calendar_svc
        from assistant.services import google_tasks
        from assistant.stores import calendar_tasks as calendar_tasks_store

        owner = task_owner_uid(task, uid)
        if action == "delete":
            calendar_svc.delete_task_event(owner, task)
            google_tasks.delete_remote(owner, task)
            return task
        if calendar_tasks_store.task_is_delegated(task):
            # Делегированные — только Leo; снять старый Google-синк если был.
            if str(task.get("google_task_id") or "").strip() or str(
                task.get("google_event_id") or ""
            ).strip():
                try:
                    calendar_svc.delete_task_event(owner, task)
                except Exception as e:
                    print(f"[calendar_tasks] delete_task_event id={task.get('id')} err={e!r}")
                try:
                    google_tasks.delete_remote(owner, task)
                except Exception as e:
                    print(f"[calendar_tasks] delete_remote id={task.get('id')} err={e!r}")
                return clear_task_google_ids(owner, task)
            return task
        if owner != int(uid):
            return task
        if str(task.get("google_event_id") or "").strip():
            calendar_svc.delete_task_event(uid, task)
            cleared = calendar_tasks_store.update_task(
                uid, task["id"], google_event_id="", google_calendar_id=""
            )
            task = cleared or task
        return google_tasks.push_task(uid, task) or task
    except Exception as e:
        print(f"[calendar_tasks] google_sync action={action} err={e!r}")
        return task
