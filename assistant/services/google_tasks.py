"""Синхронизация Leo-задач с Google Tasks (best-effort)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from assistant.stores import calendar_tasks as calendar_tasks_store


TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"


def _creds(user_id: int):
    from google.oauth2.credentials import Credentials

    from assistant.integrations import google_calendar_oauth

    path = google_calendar_oauth.user_token_path(int(user_id))
    if not path.is_file():
        return None
    creds = Credentials.from_authorized_user_file(str(path))
    scopes = set(creds.scopes or [])
    if TASKS_SCOPE not in scopes and "https://www.googleapis.com/auth/tasks" not in scopes:
        return None
    return creds


def _service(user_id: int):
    creds = _creds(user_id)
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def _default_tasklist_id(svc: Any) -> str:
    res = svc.tasklists().list(maxResults=20).execute()
    items = res.get("items") or []
    if not items:
        created = svc.tasklists().insert(body={"title": "Leo"}).execute()
        return str(created.get("id") or "")
    for it in items:
        if str(it.get("title") or "").strip().lower() in {"my tasks", "задачи", "leo"}:
            return str(it.get("id") or "")
    return str(items[0].get("id") or "")


def _due_rfc3339(task: dict[str, Any]) -> str | None:
    raw = str(task.get("start_at") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _notes_from_task(task: dict[str, Any]) -> str:
    lines: list[str] = []
    desc = str(task.get("description") or "").strip()
    if desc:
        lines.append(desc)
    start = str(task.get("start_at") or "")
    end = str(task.get("end_at") or "")
    if start:
        lines.append(f"Время: {start} — {end}".strip(" —"))
    who = str(task.get("assignee_name") or task.get("assignee_email") or "").strip()
    if who:
        lines.append(f"Исполнитель: {who}")
    return "\n".join(lines)[:8000]


def _subtask_status(item: dict[str, Any]) -> str:
    return "completed" if item.get("done") else "needsAction"


def push_task(user_id: int, task: dict[str, Any]) -> dict[str, Any] | None:
    svc = _service(user_id)
    if svc is None or not task:
        return task
    list_id = str(task.get("google_tasklist_id") or "").strip() or _default_tasklist_id(svc)
    if not list_id:
        return task
    body = {
        "title": str(task.get("title") or "Задача"),
        "notes": _notes_from_task(task),
        "status": "completed" if task.get("done") else "needsAction",
    }
    due = _due_rfc3339(task)
    if due:
        body["due"] = due
    gid = str(task.get("google_task_id") or "").strip()
    if gid:
        remote = svc.tasks().patch(tasklist=list_id, task=gid, body=body).execute()
    else:
        remote = svc.tasks().insert(tasklist=list_id, body=body).execute()
        gid = str(remote.get("id") or "")
    _sync_subtasks(svc, list_id, gid, task.get("checklist") or [])
    if gid and (
        gid != str(task.get("google_task_id") or "")
        or list_id != str(task.get("google_tasklist_id") or "")
    ):
        updated = calendar_tasks_store.update_task(
            user_id,
            task["id"],
            google_task_id=gid,
            google_tasklist_id=list_id,
        )
        return updated or task
    return task


def _sync_subtasks(svc: Any, list_id: str, parent_id: str, checklist: list[dict[str, Any]]) -> None:
    if not parent_id:
        return
    existing = (
        svc.tasks()
        .list(tasklist=list_id, showCompleted=True, showHidden=True, maxResults=100)
        .execute()
        .get("items")
        or []
    )
    children = [it for it in existing if str(it.get("parent") or "") == parent_id]
    by_title = {str(it.get("title") or "").strip(): it for it in children}
    keep_ids: set[str] = set()
    for item in checklist:
        title = str(item.get("text") or "").strip()
        if not title:
            continue
        prev = by_title.get(title)
        payload = {"title": title, "status": _subtask_status(item)}
        if prev:
            svc.tasks().patch(
                tasklist=list_id, task=str(prev.get("id")), body=payload
            ).execute()
            keep_ids.add(str(prev.get("id") or ""))
        else:
            created = svc.tasks().insert(
                tasklist=list_id, body=payload, parent=parent_id
            ).execute()
            keep_ids.add(str(created.get("id") or ""))
    for child in children:
        cid = str(child.get("id") or "")
        if cid and cid not in keep_ids:
            try:
                svc.tasks().delete(tasklist=list_id, task=cid).execute()
            except Exception:
                pass


def delete_remote(user_id: int, task: dict[str, Any]) -> None:
    svc = _service(user_id)
    gid = str((task or {}).get("google_task_id") or "").strip()
    list_id = str((task or {}).get("google_tasklist_id") or "").strip()
    if svc is None or not gid:
        return
    if not list_id:
        list_id = _default_tasklist_id(svc)
    try:
        svc.tasks().delete(tasklist=list_id, task=gid).execute()
    except Exception as e:
        print(f"[google_tasks] delete_failed id={gid} err={e!r}")


def pull_into_leo(user_id: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Подтянуть задачи Google с due в окне; не затирать локальные интервалы."""
    svc = _service(user_id)
    if svc is None:
        return []
    list_id = _default_tasklist_id(svc)
    if not list_id:
        return []
    res = (
        svc.tasks()
        .list(
            tasklist=list_id,
            showCompleted=False,
            showHidden=False,
            maxResults=100,
        )
        .execute()
    )
    imported: list[dict[str, Any]] = []
    for it in res.get("items") or []:
        if it.get("parent"):
            continue
        gid = str(it.get("id") or "").strip()
        if not gid:
            continue
        existing = calendar_tasks_store.find_by_google_task(user_id, gid)
        if existing:
            continue
        due = str(it.get("due") or "").strip()
        if not due:
            continue
        try:
            due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
        except ValueError:
            continue
        if due_dt < start or due_dt >= end:
            continue
        notes = str(it.get("notes") or "")
        row = calendar_tasks_store.create_task(
            user_id,
            title=str(it.get("title") or "Задача"),
            description=notes,
            start_at=due_dt,
        )
        updated = calendar_tasks_store.update_task(
            user_id, row["id"], google_task_id=gid, google_tasklist_id=list_id
        )
        imported.append(updated or row)
    return imported
