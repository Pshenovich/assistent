"""Битрикс24 как трекер: доски (проекты/группы + «Мои задачи»), стадии канбана, создание задачи.

Транспорт — REST по личному входящему вебхуку пользователя (права: task, sonet_group, user).
MCP-токен (чат с Лео) не подходит (проверено 2026-10-23, scripts/bitrix_mcp_probe.py): списка своих
проектов нет (только поиск по имени), стадии — без ID (только названия), а create_task/update_task
не умеют ставить задачу в стадию канбана.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

from assistant.trackers.base import (
    Board,
    CreatedTask,
    Stage,
    TaskDraft,
    TrackerError,
    TrackerNotConnected,
)
from assistant.trackers.markup import md_to_bbcode

PROVIDER_ID = "bitrix24"
PERSONAL_BOARD_ID = "personal"

# Адрес вебхука в любой вставке: вокруг может быть текст, невидимые символы, пример метода после токена.
_WEBHOOK_FIND_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})/rest/(\d+)/([A-Za-z0-9_-]+)", re.IGNORECASE)
_INVISIBLE_RE = re.compile(r"[\s\u00a0\u200b-\u200f\u2028\u2029\u2060\ufeff]+")
_STAGES_TTL_SEC = 600
_STAGES_CACHE: dict[tuple[int, str], tuple[float, list[Stage]]] = {}


def normalize_webhook(url: str) -> str:
    value = _INVISIBLE_RE.sub("", str(url or ""))
    m = _WEBHOOK_FIND_RE.search(value)
    if not m:
        # Сам адрес — секрет: в лог только его форма (буквы → a, цифры → 9).
        shape = re.sub(r"[0-9]", "9", re.sub(r"[A-Za-z]", "a", value))[:120]
        print(f"[trackers] bitrix webhook rejected shape={shape!r}")
        raise TrackerError(
            "Это не похоже на адрес вебхука. Нужен адрес вида https://ваш-портал.bitrix24.ru/rest/1/abc123/"
        )
    host, user_id, token = m.group(1).lower(), m.group(2), m.group(3)
    return f"https://{host}/rest/{user_id}/{token}/"


_SCOPE_BY_METHOD = {"user": "«Пользователи»", "task": "«Задачи»", "tasks": "«Задачи»", "sonet_group": "«Рабочие группы»"}
_AUTH_ERRORS = ("invalid_token", "NO_AUTH_FOUND", "expired_token", "INVALID_CREDENTIALS", "WRONG_AUTH_TYPE")


def call(base: str, method: str, params: dict[str, Any] | None = None) -> Any:
    try:
        r = requests.post(f"{base}{method}.json", json=params or {}, timeout=30)
    except requests.RequestException as e:
        raise TrackerError(f"Битрикс24 не отвечает: {e}") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    err = str(data.get("error") or "")
    desc = str(data.get("error_description") or "").strip()
    if err or r.status_code >= 400:
        # Адрес вебхука — секрет: в лог только метод и ответ.
        print(f"[trackers] bitrix {method} http={r.status_code} err={err!r} desc={desc[:200]!r}")
    # Нехватка прав приходит тоже с HTTP 401 — проверяем её раньше «не принят».
    if err == "insufficient_scope":
        scope = _SCOPE_BY_METHOD.get(method.split(".")[0], "")
        raise TrackerError(
            "У вебхука не хватает прав" + (f" {scope}" if scope else "")
            + ". Откройте вебхук в Битрикс24 и отметьте «Задачи», «Рабочие группы» и «Пользователи»."
        )
    if err in _AUTH_ERRORS or (r.status_code == 401 and not err):
        detail = f" (Битрикс24: {desc or err})" if (desc or err) else ""
        raise TrackerError("Вебхук Битрикс24 не принят — проверьте адрес или создайте вебхук заново" + detail)
    if err or r.status_code >= 400:
        raise TrackerError(f"Битрикс24 ({method}): {(desc or err or f'HTTP {r.status_code}')[:240]}")
    return data.get("result")


def _user_name(user: dict[str, Any]) -> str:
    parts = [str(user.get(k) or "").strip() for k in ("NAME", "LAST_NAME")]
    return " ".join(p for p in parts if p) or str(user.get("EMAIL") or "").strip()


def check_webhook(url: str) -> dict[str, str]:
    """Проверка вебхука: кто владелец. Возвращает base, portal, account_id, account_name."""
    base = normalize_webhook(url)
    me = call(base, "user.current")
    if not isinstance(me, dict) or not me.get("ID"):
        raise TrackerError("Битрикс24 не вернул пользователя вебхука")
    # Права проверяем сразу, чтобы не узнать о них при первой задаче.
    call(base, "task.stages.get", {"entityId": 0})
    call(base, "sonet_group.user.groups")
    host = urlparse(base).netloc
    return {"base": base, "portal": f"https://{host}", "account_id": str(me["ID"]), "account_name": _user_name(me)}


def _stage_list(result: Any) -> list[Stage]:
    items = result.values() if isinstance(result, dict) else (result if isinstance(result, list) else [])
    stages = []
    for it in items:
        if not isinstance(it, dict) or it.get("ID") is None:
            continue
        try:
            sort = int(it.get("SORT") or 0)
        except (TypeError, ValueError):
            sort = 0
        color = str(it.get("COLOR") or "").strip().lstrip("#")
        stages.append(Stage(id=str(it["ID"]), name=str(it.get("TITLE") or "").strip() or "Без названия",
                            sort=sort, color=f"#{color}" if color else ""))
    stages.sort(key=lambda s: (s.sort, int(s.id) if s.id.isdigit() else 0))
    return stages


class Bitrix24Tracker:
    id = PROVIDER_ID
    title = "Битрикс24"
    short_title = "Б24"

    def _creds(self, uid: int) -> dict[str, str]:
        from assistant.stores import trackers as store

        creds = store.get_credentials(uid, PROVIDER_ID)
        if not creds or creds.get("kind") != "webhook" or not creds.get("secret"):
            raise TrackerNotConnected("Подключите Битрикс24 для задач в профиле")
        return creds

    def is_connected(self, uid: int) -> bool:
        from assistant.stores import trackers as store

        creds = store.get_credentials(uid, PROVIDER_ID)
        return bool(creds and creds.get("secret"))

    def connection(self, uid: int) -> dict[str, Any]:
        from assistant.stores import trackers as store

        creds = store.get_credentials(uid, PROVIDER_ID) or {}
        return {
            "transport": "webhook" if creds.get("secret") else "",
            "portal": creds.get("portal") or "",
            "account_name": creds.get("account_name") or "",
        }

    def connect_webhook(self, uid: int, url: str) -> dict[str, Any]:
        from assistant.stores import trackers as store

        info = check_webhook(url)
        store.save_credentials(
            uid, PROVIDER_ID, kind="webhook", secret=info["base"], portal=info["portal"],
            account_id=info["account_id"], account_name=info["account_name"],
        )
        _drop_stage_cache(uid)
        return self.connection(uid)

    def disconnect(self, uid: int) -> bool:
        from assistant.stores import trackers as store

        _drop_stage_cache(uid)
        return store.delete_credentials(uid, PROVIDER_ID)

    def list_boards(self, uid: int) -> list[Board]:
        base = self._creds(uid)["secret"]
        result = call(base, "sonet_group.user.groups")
        groups = result if isinstance(result, list) else []
        boards = []
        seen: set[str] = set()
        for g in groups:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("GROUP_ID") or "").strip()
            if not gid or gid in seen:
                continue
            seen.add(gid)
            boards.append(Board(id=gid, name=str(g.get("GROUP_NAME") or f"Группа {gid}").strip()))
        boards.sort(key=lambda b: b.name.lower())
        return [Board(id=PERSONAL_BOARD_ID, name="Мои задачи", kind="personal")] + boards

    def list_stages(self, uid: int, board_id: str) -> list[Stage]:
        board_id = str(board_id or "").strip()
        if not board_id:
            return []
        key = (int(uid), board_id)
        hit = _STAGES_CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _STAGES_TTL_SEC:
            return list(hit[1])
        base = self._creds(uid)["secret"]
        entity = 0 if board_id == PERSONAL_BOARD_ID else int(board_id)
        stages = _stage_list(call(base, "task.stages.get", {"entityId": entity}))
        _STAGES_CACHE[key] = (time.monotonic(), stages)
        return list(stages)

    def _find_user(self, base: str, query: str) -> tuple[str, str]:
        from assistant.services.bitrix_task_create import _name_search_queries, _pick_user

        for q in _name_search_queries(query):
            try:
                found = call(base, "user.search", {"FIND": q})
            except TrackerError:
                found = None
            users = [u for u in (found or []) if isinstance(u, dict) and u.get("ID")]
            if users:
                picked = _pick_user(users, query) or users[0]
                return str(picked["ID"]), _user_name(picked)
        raise TrackerError(f"Не нашёл «{query}» среди пользователей Битрикс24 — уточните имя или фамилию")

    def create_task(self, uid: int, draft: TaskDraft) -> CreatedTask:
        creds = self._creds(uid)
        base = creds["secret"]
        title = str(draft.title or "").strip()
        if not title:
            raise TrackerError("Укажите название задачи")
        fields: dict[str, Any] = {"TITLE": title[:250]}
        description = md_to_bbcode(draft.description)
        if description:
            fields["DESCRIPTION"] = description
        responsible_id, responsible_name = creds.get("account_id") or "", creds.get("account_name") or ""
        if str(draft.responsible or "").strip():
            responsible_id, responsible_name = self._find_user(base, draft.responsible.strip())
        if responsible_id:
            fields["RESPONSIBLE_ID"] = int(responsible_id)
        board_id = str(draft.board_id or "").strip()
        group_id = board_id if board_id and board_id != PERSONAL_BOARD_ID else ""
        stage_id = str(draft.stage_id or "").strip()
        if group_id:
            fields["GROUP_ID"] = int(group_id)
            if stage_id:
                fields["STAGE_ID"] = int(stage_id)
        if draft.deadline:
            fields["DEADLINE"] = f"{str(draft.deadline)[:10]}T19:00:00"
        result = call(base, "tasks.task.add", {"fields": fields})
        task = (result or {}).get("task") if isinstance(result, dict) else None
        task_id = str((task or {}).get("id") or (task or {}).get("ID") or "").strip()
        if not task_id:
            raise TrackerError("Битрикс24 не вернул номер задачи")
        if stage_id and not group_id:
            # «Мой план»: стадия личная, ставится отдельным вызовом. Не вышло — задача всё равно создана.
            try:
                call(base, "task.stages.movetask", {"id": int(task_id), "stageId": int(stage_id)})
            except TrackerError as e:
                print(f"[trackers] bitrix movetask failed uid={uid} task={task_id} err={e}")
        portal = creds.get("portal") or ""
        if group_id:
            url = f"{portal}/workgroups/group/{group_id}/tasks/task/view/{task_id}/"
        else:
            url = f"{portal}/company/personal/user/{creds.get('account_id') or 0}/tasks/task/view/{task_id}/"
        return CreatedTask(external_id=task_id, url=url, title=title, extra={"responsible_name": responsible_name})


def _drop_stage_cache(uid: int) -> None:
    for key in [k for k in _STAGES_CACHE if k[0] == int(uid)]:
        _STAGES_CACHE.pop(key, None)
