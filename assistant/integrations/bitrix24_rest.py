"""Bitrix24 REST (webhook) — поиск задач с фильтрами по стадии и группе."""

from __future__ import annotations

import os
import re
from typing import Any

import requests

_STAGE_CACHE: dict[int, dict[str, dict[str, Any]]] = {}


def webhook_base() -> str | None:
    raw = (os.getenv("BITRIX24_WEBHOOK_URL") or "").strip().rstrip("/")
    return raw or None


def default_client_group_id() -> int | None:
    raw = (os.getenv("BITRIX24_PROJECT_ID") or os.getenv("BITRIX24_CLIENT_GROUP_ID") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _resolve_group_id_by_name("Obuchat")


def _resolve_group_id_by_name(name_contains: str) -> int | None:
    needle = (name_contains or "").strip().lower()
    if not needle:
        return None
    data = _post(
        "socialnetwork.api.workgroup.list",
        {"filter": {"NAME": f"%{name_contains}%"}},
    )
    if not data:
        return None
    result = data.get("result")
    groups = result if isinstance(result, list) else []
    for item in groups:
        if not isinstance(item, dict):
            continue
        title = str(item.get("NAME") or item.get("name") or "").lower()
        if needle in title:
            gid = item.get("ID") or item.get("id")
            if gid is not None:
                try:
                    return int(gid)
                except (TypeError, ValueError):
                    continue
    return None


def _post(method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    base = webhook_base()
    if not base:
        return None
    try:
        r = requests.post(f"{base}/{method}", json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    return data if isinstance(data, dict) else None


def get_group_stages(group_id: int) -> dict[str, dict[str, Any]]:
    cached = _STAGE_CACHE.get(group_id)
    if cached is not None:
        return cached
    data = _post("task.stages.get", {"entityId": int(group_id)})
    result = data.get("result") if isinstance(data, dict) else None
    stages: dict[str, dict[str, Any]] = {}
    if isinstance(result, dict):
        for sid, info in result.items():
            if isinstance(info, dict):
                stages[str(sid)] = info
    _STAGE_CACHE[group_id] = stages
    return stages


def resolve_stage_ids(group_id: int, *, status_hint: str = "", group_hint: str = "") -> list[int]:
    """Подбирает stageId по фразам вроде «оценка, клиентские задачи»."""
    hint = f"{status_hint} {group_hint}".strip().lower()
    if not hint:
        return []
    stages = get_group_stages(group_id)
    matched: list[tuple[int, int]] = []
    want_client = "клиент" in hint
    want_product = "продукт" in hint
    want_estimate = "оценк" in hint
    for sid, info in stages.items():
        title = str(info.get("TITLE") or "").lower()
        if not title:
            continue
        if want_estimate and "оценк" not in title:
            continue
        if want_client and "клиент" not in title:
            continue
        if want_product and "продукт" not in title:
            continue
        score = 0
        if want_estimate and "оценк" in title:
            score += 10
        if want_client and "клиент" in title:
            score += 10
        if want_product and "продукт" in title:
            score += 10
        if score <= 0:
            continue
        try:
            matched.append((score, int(sid)))
        except ValueError:
            continue
    matched.sort(key=lambda x: (-x[0], x[1]))
    return [sid for _score, sid in matched]


def search_tasks_by_title(
    title_contains: str,
    *,
    limit: int = 50,
    group_id: int | None = None,
    stage_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    base = webhook_base()
    needle = (title_contains or "").strip()
    if not base or not needle:
        return []

    filter_body: dict[str, Any] = {"%TITLE": needle}
    if group_id is not None:
        filter_body["GROUP_ID"] = int(group_id)

    data = _post(
        "tasks.task.list",
        {
            "filter": filter_body,
            "select": ["ID", "TITLE", "STATUS", "STAGE_ID", "GROUP_ID", "DEADLINE"],
            "order": {"ID": "DESC"},
        },
    )
    if not data:
        return []
    result = data.get("result")
    if not isinstance(result, dict):
        return []
    tasks = result.get("tasks")
    if not isinstance(tasks, list):
        return []

    stage_set = {int(x) for x in (stage_ids or []) if str(x).isdigit() or isinstance(x, int)}
    out: list[dict[str, Any]] = []
    for item in tasks[: limit * 3]:
        if not isinstance(item, dict):
            continue
        tid = item.get("id") or item.get("ID")
        title = item.get("title") or item.get("TITLE") or ""
        if tid is None:
            continue
        stage_raw = item.get("stageId") or item.get("stage_id") or item.get("STAGE_ID") or 0
        try:
            stage_id = int(stage_raw)
        except (TypeError, ValueError):
            stage_id = 0
        gid_raw = item.get("groupId") or item.get("group_id") or item.get("GROUP_ID")
        try:
            gid = int(gid_raw) if gid_raw is not None else None
        except (TypeError, ValueError):
            gid = None

        if stage_set and stage_id not in stage_set:
            if not (
                stage_id == 0
                and group_id is not None
                and gid == group_id
                and needle.lower() in title.lower()
            ):
                continue

        out.append(
            {
                "taskId": int(tid),
                "title": str(title),
                "source": "rest",
                "stageId": stage_id,
                "groupId": gid,
                "statusCode": item.get("status") or item.get("STATUS"),
            }
        )
        if len(out) >= limit:
            break
    return out


def get_task_rest(task_id: int) -> dict[str, Any] | None:
    data = _post("tasks.task.get", {"taskId": int(task_id)})
    if not data:
        return None
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    task = result.get("task")
    return task if isinstance(task, dict) else None


def stage_title(group_id: int, stage_id: int | str) -> str:
    if not stage_id or str(stage_id) == "0":
        return ""
    info = get_group_stages(group_id).get(str(stage_id)) or {}
    return str(info.get("TITLE") or "").strip()
