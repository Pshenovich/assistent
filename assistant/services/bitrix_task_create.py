"""Создание задач Bitrix24: парсинг запроса и назначение исполнителя."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from assistant.integrations.bitrix24_rest import default_client_group_id
from assistant.integrations.bitrix_mcp_client import call_tool
from assistant.lib.bitrix_task_format import _parse_task_payload, format_task_from_tool_result

_CREATE_RE = re.compile(
    r"\b(заведи|создай|поставь|добавь|оформи|сделай)\s+задач",
    re.IGNORECASE,
)
_TITLE_QUOTED_RE = re.compile(
    r"задач\w*\s+[«\"']([^»\"']+)[»\"']",
    re.IGNORECASE,
)
_DESC_RE = re.compile(
    r"(?:описани[ея]|description)\s*:\s*(.+?)(?=(?:исполнител\w*|ответственн\w*|срок|дедлайн|приоритет)\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)
_ASSIGNEE_RE = re.compile(
    r"(?:исполнител\w*|ответственн\w*)\s*:\s*([^,\n]+)",
    re.IGNORECASE,
)
_DEADLINE_RE = re.compile(
    r"(?:срок|дедлайн|deadline)\s*:\s*([^\n,]+)",
    re.IGNORECASE,
)


@dataclass
class CreateTaskRequest:
    title: str
    description: str = ""
    assignee_name: str = ""
    deadline: str = ""
    group_id: int | None = None


def is_create_task_request(text: str) -> bool:
    return bool(_CREATE_RE.search((text or "").strip()))


def _name_search_queries(name: str) -> list[str]:
    raw = (name or "").strip()
    if not raw:
        return []
    queries = [raw]
    if "ё" in raw.lower():
        queries.append(raw.replace("Ё", "Е").replace("ё", "е"))
    if "е" in raw.lower() and "ё" not in raw.lower():
        queries.append(raw.replace("е", "ё").replace("Е", "Ё"))
    parts = raw.split()
    if len(parts) >= 2:
        queries.append(parts[0])
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.strip().lower()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(q.strip())
    return out[:4]


def parse_create_task_request(text: str) -> CreateTaskRequest | None:
    raw = (text or "").strip()
    if not is_create_task_request(raw):
        return None

    title_m = _TITLE_QUOTED_RE.search(raw)
    if not title_m:
        return None
    title = title_m.group(1).strip()
    if not title:
        return None

    desc_m = _DESC_RE.search(raw)
    description = (desc_m.group(1).strip() if desc_m else "").strip(" ,.")

    assignee_m = _ASSIGNEE_RE.search(raw)
    assignee_name = (assignee_m.group(1).strip() if assignee_m else "").strip(" .")

    deadline_m = _DEADLINE_RE.search(raw)
    deadline = (deadline_m.group(1).strip() if deadline_m else "").strip()

    group_id = None
    if "бустра llm" in title.lower() or "[бустра llm]" in title.lower():
        group_id = default_client_group_id()

    return CreateTaskRequest(
        title=title,
        description=description,
        assignee_name=assignee_name,
        deadline=deadline,
        group_id=group_id,
    )


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    start = text.find("[")
    if start < 0:
        return []
    try:
        data, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _user_display_name(user: dict[str, Any]) -> str:
    parts = [
        str(user.get("LAST_NAME") or user.get("lastName") or "").strip(),
        str(user.get("NAME") or user.get("name") or "").strip(),
        str(user.get("SECOND_NAME") or user.get("secondName") or "").strip(),
    ]
    return " ".join(p for p in parts if p).strip()


def _pick_user(users: list[dict[str, Any]], assignee_name: str) -> dict[str, Any] | None:
    if not users:
        return None
    needle = (assignee_name or "").strip().lower()
    if not needle:
        return users[0]
    for user in users:
        display = _user_display_name(user).lower()
        first = str(user.get("NAME") or user.get("name") or "").strip().lower()
        if needle == first or needle in display or display.startswith(needle):
            return user
    return users[0]


async def resolve_responsible_id(token: str, assignee_name: str) -> tuple[int | None, str]:
    name = (assignee_name or "").strip()
    if not name:
        return None, ""
    queries = _name_search_queries(name)
    if not queries:
        return None, ""
    raw = await call_tool(token, "search_users", {"searchQueries": queries})
    if raw.strip().startswith("ERROR:"):
        raise RuntimeError(raw.strip())
    users = _parse_json_array(raw)
    if not users:
        return None, ""
    picked = _pick_user(users, name)
    if not picked:
        return None, ""
    uid = picked.get("ID") or picked.get("id")
    if uid is None:
        return None, ""
    return int(uid), _user_display_name(picked)


def _parse_created_task_id(raw: str) -> int | None:
    task = _parse_task_payload(raw)
    if task:
        tid = task.get("taskId") or task.get("id")
        if tid is not None:
            return int(tid)
    text = (raw or "").strip()
    for pattern in (
        r'"taskId"\s*:\s*(\d+)',
        r'"id"\s*:\s*"?(\d+)"?',
        r"task[_ ]?id[=:]?\s*(\d+)",
    ):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


async def create_task_direct(token: str, req: CreateTaskRequest) -> str:
    payload: dict[str, Any] = {"title": req.title}
    if req.description:
        payload["description"] = req.description
    if req.deadline:
        payload["deadlineDate"] = req.deadline
    if req.group_id:
        payload["groupId"] = int(req.group_id)

    if req.assignee_name:
        responsible_id, resolved_name = await resolve_responsible_id(token, req.assignee_name)
        if not responsible_id:
            raise RuntimeError(
                f"Не нашёл пользователя «{req.assignee_name}» в Битрикс24. "
                "Уточните имя или фамилию исполнителя."
            )
        payload["responsibleId"] = responsible_id

    raw = await call_tool(token, "create_task", payload)
    if raw.strip().startswith("ERROR:"):
        raise RuntimeError(raw.strip())

    task_id = _parse_created_task_id(raw)
    if task_id:
        detail_raw = await call_tool(token, "get_task_by_id", {"taskId": task_id})
        formatted = format_task_from_tool_result(detail_raw, token=token)
        if formatted:
            return f"Задача создана.\n\n{formatted}"

    return f"Задача **{req.title}** создана."
