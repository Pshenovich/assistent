"""Связки с учётом доступа: каждый видит в связке только то, к чему у него и так есть доступ.

Правила:
• документ (в т.ч. саммари встречи, база знаний) — если человек может его читать;
• задача — если видит задачу (постановщик, исполнитель, участник);
• чат — владелец или участник;
• напоминание — только хозяин напоминания;
• человек и встреча из Google — личное: видит только тот, кто добавил (снимок подписи).
Связку видит тот, кому доступен хотя бы один её элемент. Недоступные элементы не раскрываются
даже названием. Сейфы связать нельзя — у них нет вида в KINDS.
"""

from __future__ import annotations

from typing import Any, Optional

from assistant.stores import bundles as bundles_store

KIND_LABEL = {
    "note": "Документ",
    "task": "Задача",
    "chat": "Чат",
    "reminder": "Напоминание",
    "person": "Человек",
    "event": "Встреча",
}


def _uid(user_id: Any) -> str:
    return str(int(user_id))


def _note(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.stores import notes as notes_store

    try:
        n = notes_store.get_readable_note(uid, int(rid), with_sharing=False)
    except Exception as e:
        print(f"[bundles] note access id={rid} err={e!r}")
        return None
    if not n:
        return None
    is_tr = str(n.get("role") or "") == notes_store.TRANSCRIPTION_ROLE
    return {
        "title": str(n.get("title") or "").strip() or "Без названия",
        "subtitle": "Саммари встречи" if is_tr else "Документ",
        "is_transcription": is_tr,
        "updated_at": str(n.get("updated_at") or ""),
    }


def _task(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.stores import calendar_tasks

    t = calendar_tasks.get_task_for_user(uid, rid)
    if not t:
        return None
    sub = ["Задача"]
    if t.get("assignee_name"):
        sub.append(str(t["assignee_name"]))
    return {
        "title": str(t.get("title") or "").strip() or "Задача",
        "subtitle": " · ".join(sub),
        "done": bool(t.get("done")),
        "start_at": str(t.get("start_at") or ""),
    }


def _chat(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.stores import chat_threads

    c = chat_threads.get_accessible_thread(uid, rid)
    if not c:
        return None
    return {"title": str(c.get("title") or "").strip() or "Чат", "subtitle": "Чат"}


def _reminder(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.stores import reminders_store

    r = reminders_store.get_reminder_by_id(rid)
    if not r or str(int(r.get("user_id") or 0)) != uid:
        return None
    return {"title": str(r.get("task") or r.get("text") or "").strip()[:200] or "Напоминание", "subtitle": "Напоминание"}


def resolve(user_id: Any, kind: str, item_id: str, *, label: str = "", added_by: str = "") -> Optional[dict[str, Any]]:
    """Как элемент выглядит для этого человека, или None — если ему недоступен."""
    uid = _uid(user_id)
    try:
        k, rid = bundles_store.norm_item(kind, item_id)
    except ValueError:
        return None
    info: Optional[dict[str, Any]]
    if k == "note":
        info = _note(uid, rid)
    elif k == "task":
        info = _task(uid, rid)
    elif k == "chat":
        info = _chat(uid, rid)
    elif k == "reminder":
        info = _reminder(uid, rid)
    else:  # person, event — личное: только тому, кто добавил
        if not added_by or str(added_by) != uid:
            return None
        info = {"title": str(label or "").strip() or KIND_LABEL[k], "subtitle": KIND_LABEL[k]}
    if not info:
        return None
    return {"kind": k, "id": rid, **info}


def can_add(user_id: Any, kind: str, item_id: str, label: str = "") -> dict[str, Any]:
    """Проверка перед добавлением: элемент доступен человеку. ValueError — если нет."""
    uid = _uid(user_id)
    k, rid = bundles_store.norm_item(kind, item_id)
    if k in ("person", "event"):
        if not str(label or "").strip():
            raise ValueError("Нет названия элемента")
        return {"kind": k, "id": rid, "title": str(label).strip()[:200]}
    info = resolve(uid, k, rid)
    if not info:
        raise ValueError("Нет доступа к элементу")
    return info


def view_bundle(user_id: Any, bundle: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Связка глазами человека: только доступные элементы; None — если не доступен ни один."""
    uid = _uid(user_id)
    items = []
    for it in bundle.get("items") or []:
        info = resolve(
            uid, it["item_kind"], it["item_id"], label=it.get("label") or "", added_by=it.get("added_by") or ""
        )
        if info:
            info["can_remove"] = str(it.get("added_by") or "") == uid or str(bundle.get("owner_user_id")) == uid
            items.append(info)
    if not items:
        return None
    is_owner = str(bundle.get("owner_user_id") or "") == uid
    return {
        "id": int(bundle["id"]),
        "title": str(bundle.get("title") or ""),
        "is_owner": is_owner,
        "can_rename": True,
        "can_delete": is_owner,
        "items": items,
        "hidden": len(bundle.get("items") or []) - len(items),
        "updated_at": str(bundle.get("updated_at") or ""),
    }


def bundles_for_item(user_id: Any, kind: str, item_id: str) -> list[dict[str, Any]]:
    """Связки элемента для человека, которому доступен сам элемент (проверяет вызывающий)."""
    out = []
    for b in bundles_store.bundles_for_item(kind, item_id):
        v = view_bundle(user_id, b)
        if v:
            out.append(v)
    return out


def visible_bundle(user_id: Any, bundle_id: Any) -> Optional[dict[str, Any]]:
    b = bundles_store.get(bundle_id)
    return view_bundle(user_id, b) if b else None


def visible_ids_for_item(user_id: Any, kind: str, item_id: str) -> set[int]:
    return {int(b["id"]) for b in bundles_for_item(user_id, kind, item_id)}


def member_user_ids(bundle: dict[str, Any]) -> list[str]:
    """Кому слать «связки изменились»: автор и все, кто добавлял элементы (без перебора доступа)."""
    ids = {str(bundle.get("owner_user_id") or "")}
    for it in bundle.get("items") or []:
        ids.add(str(it.get("added_by") or ""))
    return sorted(i for i in ids if i)


def context_items(user_id: Any, kind: str, item_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Связанные элементы для контекста Лео (без самого элемента, без повторов)."""
    try:
        self_key = bundles_store.norm_item(kind, item_id)
    except ValueError:
        return []
    seen = {self_key}
    out = []
    for b in bundles_for_item(user_id, *self_key):
        for it in b["items"]:
            key = (it["kind"], it["id"])
            if key in seen:
                continue
            seen.add(key)
            out.append({**it, "bundle_title": b["title"]})
            if len(out) >= limit:
                return out
    return out


def leo_context(user_id: Any, kind: str, item_id: str, *, max_notes: int = 3) -> tuple[list[str], str]:
    """Контекст для Лео: id связанных документов (тексты подложит pack_attached_notes) и
    короткий список всего связанного — названия, тип, статус задачи. Только доступное человеку."""
    items = context_items(user_id, kind, item_id, limit=20)
    if not items:
        return [], ""
    note_ids = [it["id"] for it in items if it["kind"] == "note"][:max_notes]
    lines = []
    for it in items:
        bits = [str(it.get("subtitle") or KIND_LABEL.get(it["kind"], "")), f"«{it['title']}»"]
        if it["kind"] == "task":
            bits.append("выполнена" if it.get("done") else "открыта")
        if it.get("bundle_title"):
            bits.append(f"связка «{it['bundle_title']}»")
        lines.append("• " + " · ".join(b for b in bits if b))
    text = (
        "Связанные материалы (пользователь сам связал их с этим документом; "
        "учитывай их, ссылайся по названию):\n" + "\n".join(lines)
    )
    return note_ids, text


def _accessible_note_ids(uid: str) -> list[int]:
    from assistant.stores import note_members
    from assistant.stores import notes as notes_store

    ids = set(notes_store.own_note_ids(uid))
    try:
        ids.update(int(nid) for _owner, nid in note_members.list_note_ids_for_member(uid))
    except Exception as e:
        print(f"[bundles] shared notes uid={uid} err={e!r}")
    return sorted(ids)


def list_for_user(user_id: Any, *, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Именованные связки человека (для «Проектов», поиска и Лео): только видимые ему.
    query — по названию связки и названиям её элементов."""
    uid = _uid(user_id)
    q = " ".join(str(query or "").lower().split())
    out = []
    for bid in bundles_store.candidate_ids_for_user(uid, note_ids=_accessible_note_ids(uid)):
        v = visible_bundle(uid, bid)
        if not v or not v["title"]:
            continue
        if q:
            hay = " ".join([v["title"]] + [it["title"] for it in v["items"]]).lower()
            if not all(w in hay for w in q.split()):
                continue
        out.append(v)
        if len(out) >= limit:
            break
    return out
