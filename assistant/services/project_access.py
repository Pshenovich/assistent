"""Доступ через проект: участник проекта читает его документы и задачи.

Правило (решение владельца 2026-11-13): участие в проекте открывает документы и задачи проекта
на чтение. Почта и внешние чаты так не открываются — их видит только тот, у кого они и так есть.

Чтобы через проект нельзя было «раздать» чужое, элемент открывает доступ, только если его добавил
тот, кто сам может его править: у документа — автор или участник с правкой (не читатель базы
знаний), у задачи — постановщик или исполнитель (и те же роли выше по цепочке подзадач).

Доступ только на чтение: пути, которые меняют документ или задачу, по-прежнему проверяют
get_accessible_note / get_task_for_user и сюда не смотрят.
"""

from __future__ import annotations

from typing import Any, Optional

# Какие элементы проекта открываются участникам.
GRANTING_KINDS = ("note", "task")


def _uid(user_id: Any) -> str:
    return str(int(user_id))


def _adder_can_share(kind: str, item_id: str, added_by: str) -> bool:
    if not str(added_by or "").strip().isdigit():
        return False
    if kind == "note":
        from assistant.stores import notes as notes_store

        return notes_store.get_accessible_note(added_by, int(item_id), with_sharing=False) is not None
    if kind == "task":
        from assistant.stores import calendar_tasks

        return calendar_tasks.get_task_for_user(added_by, item_id) is not None
    return False


def project_role_for_item(user_id: Any, kind: str, item_id: Any) -> str:
    """Лучшая роль человека среди проектов, которые открывают ему этот элемент ('' — ни одного)."""
    from assistant.stores import bundles as bundles_store
    from assistant.stores import projects as projects_store

    if kind not in GRANTING_KINDS:
        return ""
    try:
        k, rid = bundles_store.norm_item(kind, item_id)
    except ValueError:
        return ""
    uid = _uid(user_id)
    best = ""
    for pid in projects_store.ids_with_item(k, rid):
        p = projects_store.get(pid)
        if not p or p.get("archived_at"):
            continue
        role = projects_store.role_of(uid, p)
        if not role or projects_store.ROLE_RANK.get(role, 0) <= projects_store.ROLE_RANK.get(best, 0):
            continue
        it = next((i for i in p["items"] if (i["item_kind"], i["item_id"]) == (k, rid)), None)
        if it and _adder_can_share(k, rid, str(it.get("added_by") or "")):
            best = role
    return best


def readable_note(user_id: Any, note_id: int) -> Optional[dict[str, Any]]:
    """Документ проекта для участника: только чтение; редактор проекта может комментировать."""
    role = project_role_for_item(user_id, "note", note_id)
    if not role:
        return None
    from assistant.stores import notes as notes_store

    note = notes_store.get_note_by_id(int(note_id))
    if not note:
        return None
    note = dict(note)
    note["project_role"] = role
    return note


def viewable_task(user_id: Any, task_id: Any) -> Optional[dict[str, Any]]:
    """Задача для просмотра: своя (постановщик, исполнитель) или задача проекта, где человек участник.
    Во втором случае в ответе project_view=True — менять задачу через это нельзя."""
    from assistant.stores import calendar_tasks

    t = calendar_tasks.get_task_for_user(user_id, task_id)
    if t:
        return t
    if not project_role_for_item(user_id, "task", task_id):
        return None
    try:
        row = calendar_tasks._get_task_row(int(task_id))  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        return None
    if not row:
        return None
    row = dict(row)
    row["project_view"] = True
    return row
