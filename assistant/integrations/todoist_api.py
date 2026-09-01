"""Todoist REST для заметок."""

from __future__ import annotations

import os
from typing import Any

import requests

from assistant.integrations import todoist_oauth

TODOIST_API_BASE = "https://api.todoist.com/api/v1"
TODOIST_TASKS_URL = f"{TODOIST_API_BASE}/tasks"
DEFAULT_LABEL = os.getenv("TODOIST_LABEL", "разобрать").strip()
PROJECT_ID = os.getenv("TODOIST_PROJECT_ID", "").strip()


def _token(user_id: int) -> str:
    if todoist_oauth.user_token_path(user_id).exists():
        return todoist_oauth.get_access_token_for_user(user_id)
    legacy = os.getenv("TODOIST_TOKEN", "").strip()
    if legacy:
        return legacy
    raise RuntimeError("Todoist не подключён. Выполните /todoist_auth.")


def add_note(user_id: int, title: str, description: str = "") -> str:
    payload: dict[str, Any] = {
        "content": (title or "Заметка")[:500],
        "description": description or "",
    }
    if PROJECT_ID:
        payload["project_id"] = PROJECT_ID
    if DEFAULT_LABEL:
        payload["labels"] = [DEFAULT_LABEL]
    r = requests.post(
        TODOIST_TASKS_URL,
        headers={"Authorization": f"Bearer {_token(user_id)}"},
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    tid = data.get("id") if isinstance(data, dict) else None
    if tid is None:
        raise RuntimeError(f"Todoist: неожиданный ответ {data!r}")
    return str(tid)


def list_notes(
    user_id: int,
    limit: int = 100,
    *,
    label_filter: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if PROJECT_ID:
        params["project_id"] = PROJECT_ID
    r = requests.get(
        TODOIST_TASKS_URL,
        headers={"Authorization": f"Bearer {_token(user_id)}"},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    tasks = data.get("results") if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        return []
    needle = (label_filter or "").strip().lower()
    out: list[dict[str, Any]] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if needle:
            labels = t.get("labels") or []
            if not isinstance(labels, list):
                labels = []
            label_ok = any(
                str(lab).strip().lower() == needle for lab in labels if str(lab).strip()
            )
            if not label_ok:
                continue
        out.append(
            {
                "id": str(t.get("id")),
                "title": (t.get("content") or "").strip(),
                "description": (t.get("description") or "").strip(),
            }
        )
        if len(out) >= limit:
            break
    return out
